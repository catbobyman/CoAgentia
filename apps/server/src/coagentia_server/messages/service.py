"""跨领域系统消息底座：频道人类受众查询与 durable 系统消息写入。"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from coagentia_contracts.enums import MemberKind, MessageKind
from coagentia_contracts.ws import EventType
from sqlalchemy import insert, select
from sqlalchemy.engine import Connection

from coagentia_server.db import models
from coagentia_server.ledger.service import new_ulid, now_iso
from coagentia_server.routes.serialize import message_public

_CHANNEL_MEMBER = models.tbl(models.ChannelMember)
_MEMBER = models.tbl(models.Member)
_MESSAGE = models.tbl(models.Message)
_MENTION = models.tbl(models.MessageMention)


def channel_human_members(conn: Connection, channel_id: str) -> list[dict[str, Any]]:
    """返回频道内未移除的人类成员；稳定按 member id 排序。"""
    rows = conn.execute(
        select(_MEMBER.c.id, _MEMBER.c.name, _MEMBER.c.kind)
        .select_from(
            _CHANNEL_MEMBER.join(_MEMBER, _CHANNEL_MEMBER.c.member_id == _MEMBER.c.id)
        )
        .where(
            _CHANNEL_MEMBER.c.channel_id == channel_id,
            _MEMBER.c.kind == MemberKind.HUMAN,
            _MEMBER.c.removed_at.is_(None),
        )
        .order_by(_MEMBER.c.id)
    ).mappings()
    return [dict(row) for row in rows]


def post_system_message(
    tx: Any,
    *,
    workspace_id: str,
    channel_id: str,
    body: str,
    thread_root_id: str | None,
    mention_member_ids: Iterable[str] = (),
    created_at: str | None = None,
) -> str:
    """写系统消息与 mention 派生行，并登记提交后的 message.created 广播。"""
    message_id = new_ulid()
    timestamp = created_at or now_iso()
    tx.conn.execute(
        insert(_MESSAGE).values(
            id=message_id,
            workspace_id=workspace_id,
            channel_id=channel_id,
            thread_root_id=thread_root_id,
            author_member_id=None,
            kind=MessageKind.SYSTEM,
            card_kind=None,
            card_ref=None,
            body=body,
            created_at=timestamp,
        )
    )
    for member_id in dict.fromkeys(mention_member_ids):
        tx.conn.execute(insert(_MENTION).values(message_id=message_id, member_id=member_id))
    row = models.row_dict(
        tx.conn.execute(select(_MESSAGE).where(_MESSAGE.c.id == message_id)).mappings().first()
    )
    tx.emit(EventType.MESSAGE_CREATED, channel_id, {"message": message_public(row)})
    return message_id


__all__ = ["channel_human_members", "post_system_message"]
