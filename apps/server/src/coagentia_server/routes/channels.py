"""4.5 频道与 DM（契约 B §4.5）：列表/建/改/归档/删/成员/DM。"""

from __future__ import annotations

from typing import Any

from coagentia_contracts import entities, rest
from coagentia_contracts.enums import ChannelKind, MessageKind
from coagentia_contracts.kernel.fingerprint import fingerprint
from coagentia_contracts.ws import EventType
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import delete, insert, select, update

from coagentia_server.api import ApiError
from coagentia_server.db import models
from coagentia_server.deps import (
    Tx,
    acting_member,
    get_tx,
    owner_member,
    require_admin,
    require_workspace,
)
from coagentia_server.ledger.service import new_ulid, now_iso
from coagentia_server.routes.serialize import (
    channel_public,
    message_public,
    read_position_public,
)

router = APIRouter(prefix="/api", tags=["channels"])

_CHANNEL = models.tbl(models.Channel)
_CHANNEL_MEMBER = models.tbl(models.ChannelMember)
_READ = models.tbl(models.ReadPosition)
_MSG = models.tbl(models.Message)
_CANVAS = models.tbl(models.Canvas)
_MEMBER = models.tbl(models.Member)

EMPTY_CANVAS_HASH = fingerprint({"edges": [], "nodes": []})


def _fetch_channel(tx: Tx, channel_id: str) -> dict[str, Any]:
    row = tx.conn.execute(select(_CHANNEL).where(_CHANNEL.c.id == channel_id)).mappings().first()
    if row is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "频道不存在")
    return dict(row)


def _post_system_message(tx: Tx, channel: dict[str, Any], body: str) -> dict[str, Any]:
    msg_id = new_ulid()
    tx.conn.execute(
        insert(_MSG).values(
            id=msg_id,
            workspace_id=channel["workspace_id"],
            channel_id=channel["id"],
            thread_root_id=None,
            author_member_id=None,
            kind=MessageKind.SYSTEM,
            card_kind=None,
            card_ref=None,
            body=body,
            created_at=now_iso(),
        )
    )
    return models.row_dict(
        tx.conn.execute(select(_MSG).where(_MSG.c.id == msg_id)).mappings().first()
    )


@router.get("/channels", response_model=rest.ChannelsSnapshot)
def list_channels(tx: Tx = Depends(get_tx)) -> Any:
    ws = require_workspace(tx.conn)
    me = owner_member(tx.conn)
    channels = tx.conn.execute(
        select(_CHANNEL).where(_CHANNEL.c.workspace_id == ws["id"]).order_by(_CHANNEL.c.created_at)
    ).mappings()
    positions = tx.conn.execute(
        select(_READ).where(_READ.c.member_id == me["id"])
    ).mappings()
    return {
        "items": [channel_public(dict(c)) for c in channels],
        "read_positions": [read_position_public(dict(p)) for p in positions],
    }


@router.post("/channels", response_model=entities.ChannelPublic, status_code=201)
def create_channel(body: rest.ChannelCreate, request: Request, tx: Tx = Depends(get_tx)) -> Any:
    ws = require_workspace(tx.conn)
    require_admin(acting_member(request, tx.conn))
    exists = tx.conn.execute(
        select(_CHANNEL.c.id).where(
            _CHANNEL.c.workspace_id == ws["id"],
            _CHANNEL.c.kind == ChannelKind.CHANNEL,
            _CHANNEL.c.name == body.name,
        )
    ).first()
    if exists is not None:
        raise ApiError(409, rest.ErrorCode.NAME_TAKEN, f"频道名 {body.name} 已存在")

    ts = now_iso()
    channel_id = new_ulid()
    tx.conn.execute(
        insert(_CHANNEL).values(
            id=channel_id,
            workspace_id=ws["id"],
            kind=ChannelKind.CHANNEL,
            name=body.name,
            description=body.description,
            is_private=body.is_private,
            created_at=ts,
        )
    )
    for mid in dict.fromkeys(body.member_ids):
        tx.conn.execute(
            insert(_CHANNEL_MEMBER).values(channel_id=channel_id, member_id=mid, joined_at=ts)
        )
    # 每频道恰一空画布（契约 A §6；与 bootstrap #all 同构）。
    tx.conn.execute(
        insert(_CANVAS).values(
            id=new_ulid(),
            workspace_id=ws["id"],
            channel_id=channel_id,
            baseline_version=0,
            baseline_hash=EMPTY_CANVAS_HASH,
            updated_at=ts,
        )
    )
    pub = channel_public(_fetch_channel(tx, channel_id))
    tx.emit(EventType.CHANNEL_CREATED, channel_id, {"channel": pub})
    return pub


@router.patch("/channels/{channel_id}", response_model=entities.ChannelPublic)
def patch_channel(
    channel_id: str, body: rest.ChannelPatch, request: Request, tx: Tx = Depends(get_tx)
) -> Any:
    require_admin(acting_member(request, tx.conn))
    _fetch_channel(tx, channel_id)
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    if changes:
        tx.conn.execute(update(_CHANNEL).where(_CHANNEL.c.id == channel_id).values(**changes))
    pub = channel_public(_fetch_channel(tx, channel_id))
    tx.emit(EventType.CHANNEL_UPDATED, channel_id, {"channel": pub})
    return pub


@router.post("/channels/{channel_id}/archive", response_model=entities.ChannelPublic)
def archive_channel(channel_id: str, request: Request, tx: Tx = Depends(get_tx)) -> Any:
    require_admin(acting_member(request, tx.conn))
    channel = _fetch_channel(tx, channel_id)
    tx.conn.execute(
        update(_CHANNEL).where(_CHANNEL.c.id == channel_id).values(archived_at=now_iso())
    )
    sys_msg = _post_system_message(tx, channel, "频道已归档。")
    pub = channel_public(_fetch_channel(tx, channel_id))
    tx.emit(EventType.MESSAGE_CREATED, channel_id, {"message": message_public(sys_msg)})
    tx.emit(EventType.CHANNEL_UPDATED, channel_id, {"channel": pub})
    return pub


@router.post("/channels/{channel_id}/unarchive", response_model=entities.ChannelPublic)
def unarchive_channel(channel_id: str, request: Request, tx: Tx = Depends(get_tx)) -> Any:
    require_admin(acting_member(request, tx.conn))
    channel = _fetch_channel(tx, channel_id)
    tx.conn.execute(update(_CHANNEL).where(_CHANNEL.c.id == channel_id).values(archived_at=None))
    sys_msg = _post_system_message(tx, channel, "频道已取消归档。")
    pub = channel_public(_fetch_channel(tx, channel_id))
    tx.emit(EventType.MESSAGE_CREATED, channel_id, {"message": message_public(sys_msg)})
    tx.emit(EventType.CHANNEL_UPDATED, channel_id, {"channel": pub})
    return pub


@router.delete("/channels/{channel_id}", status_code=204)
def delete_channel(channel_id: str, request: Request, tx: Tx = Depends(get_tx)) -> Response:
    require_admin(acting_member(request, tx.conn))
    channel = _fetch_channel(tx, channel_id)
    # 消息不可变、无法级联删——含任何消息的频道不可硬删；干净回 409 而非 FK 500（改用归档）。
    has_message = tx.conn.execute(
        select(_MSG.c.id).where(_MSG.c.channel_id == channel_id).limit(1)
    ).first()
    if has_message is not None:
        raise ApiError(
            409,
            rest.ErrorCode.CHANNEL_NOT_EMPTY,
            "频道含消息，无法删除（消息不可变）——请改用归档",
        )
    # 清理非不可变依赖行后删频道。
    tx.conn.execute(delete(_CANVAS).where(_CANVAS.c.channel_id == channel_id))
    tx.conn.execute(delete(_CHANNEL_MEMBER).where(_CHANNEL_MEMBER.c.channel_id == channel_id))
    tx.conn.execute(delete(_READ).where(_READ.c.channel_id == channel_id))
    tx.conn.execute(delete(_CHANNEL).where(_CHANNEL.c.id == channel_id))
    tx.emit(EventType.CHANNEL_DELETED, channel_id, {"channel": channel_public(channel)})
    return Response(status_code=204)


@router.post("/channels/{channel_id}/members", status_code=201)
def add_channel_member(
    channel_id: str, body: rest.ChannelMemberAdd, request: Request, tx: Tx = Depends(get_tx)
) -> Any:
    require_admin(acting_member(request, tx.conn))
    _fetch_channel(tx, channel_id)
    existing = tx.conn.execute(
        select(_CHANNEL_MEMBER).where(
            _CHANNEL_MEMBER.c.channel_id == channel_id,
            _CHANNEL_MEMBER.c.member_id == body.member_id,
        )
    ).first()
    if existing is None:
        tx.conn.execute(
            insert(_CHANNEL_MEMBER).values(
                channel_id=channel_id, member_id=body.member_id, joined_at=now_iso()
            )
        )
    tx.emit(
        EventType.CHANNEL_MEMBER_ADDED,
        channel_id,
        {"channel_id": channel_id, "member_id": body.member_id},
    )
    return {"channel_id": channel_id, "member_id": body.member_id}


@router.delete("/channels/{channel_id}/members/{member_id}", status_code=204)
def remove_channel_member(
    channel_id: str, member_id: str, request: Request, tx: Tx = Depends(get_tx)
) -> Response:
    require_admin(acting_member(request, tx.conn))
    _fetch_channel(tx, channel_id)
    tx.conn.execute(
        delete(_CHANNEL_MEMBER).where(
            _CHANNEL_MEMBER.c.channel_id == channel_id,
            _CHANNEL_MEMBER.c.member_id == member_id,
        )
    )
    tx.emit(
        EventType.CHANNEL_MEMBER_REMOVED,
        channel_id,
        {"channel_id": channel_id, "member_id": member_id},
    )
    return Response(status_code=204)


@router.post("/dms", response_model=entities.ChannelPublic)
def open_dm(body: rest.DmCreate, tx: Tx = Depends(get_tx)) -> Any:
    ws = require_workspace(tx.conn)
    me = owner_member(tx.conn)
    if body.member_id == me["id"]:
        raise ApiError(422, rest.ErrorCode.VALIDATION_FAILED, "不能与自己建立 DM")
    target = tx.conn.execute(
        select(_MEMBER.c.id).where(
            _MEMBER.c.id == body.member_id, _MEMBER.c.removed_at.is_(None)
        )
    ).first()
    if target is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "成员不存在")
    key = ":".join(sorted([me["id"], body.member_id]))
    existing = tx.conn.execute(select(_CHANNEL).where(_CHANNEL.c.dm_key == key)).mappings().first()
    if existing is not None:
        return channel_public(dict(existing))  # dm_key 去重，幂等返回既有

    ts = now_iso()
    channel_id = new_ulid()
    tx.conn.execute(
        insert(_CHANNEL).values(
            id=channel_id,
            workspace_id=ws["id"],
            kind=ChannelKind.DM,
            name=None,
            is_private=True,
            dm_key=key,
            created_at=ts,
        )
    )
    for mid in dict.fromkeys((me["id"], body.member_id)):  # 去重 (channel_id, member_id)
        tx.conn.execute(
            insert(_CHANNEL_MEMBER).values(channel_id=channel_id, member_id=mid, joined_at=ts)
        )
    pub = channel_public(_fetch_channel(tx, channel_id))
    tx.emit(EventType.CHANNEL_CREATED, channel_id, {"channel": pub})
    return pub
