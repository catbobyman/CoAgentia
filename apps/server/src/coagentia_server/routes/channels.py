"""4.5 频道与 DM（契约 B §4.5）：列表/建/改/归档/删/成员/DM。"""

from __future__ import annotations

from typing import Any

from coagentia_contracts import entities, rest
from coagentia_contracts.enums import ChannelKind, MemberKind, MessageKind, NotificationMode
from coagentia_contracts.ws import EventType
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import delete, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

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
_MEMBER = models.tbl(models.Member)
_NOTIF = models.tbl(models.ChannelNotificationSetting)
# DEDAG：canvases 表冻结；此句柄仅供频道硬删清理存量画布行（FK 完整性），无其余读写。
_CANVAS = models.tbl(models.Canvas)


def _notif_setting_public(row: dict[str, Any]) -> dict[str, Any]:
    """通知设置行 → ChannelNotificationSettingPublic（形状校验；无行由调用点合成默认 all）。"""
    return entities.ChannelNotificationSettingPublic.model_validate(row).model_dump()


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
    # §11.4 #5：扩 notification_settings = 本人全部**非默认**行（mode≠all），前端一次拉齐渲染
    # 徽标；全默认/冷态 → []。PUT 后前端本地更新，零新增 WS 事件（同 §11.2 #4 裁决）。
    settings = tx.conn.execute(
        select(_NOTIF).where(
            _NOTIF.c.member_id == me["id"], _NOTIF.c.mode != NotificationMode.ALL
        )
    ).mappings()
    return {
        "items": [channel_public(dict(c)) for c in channels],
        "read_positions": [read_position_public(dict(p)) for p in positions],
        "notification_settings": [_notif_setting_public(dict(s)) for s in settings],
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
    # DEDAG：画布退役，建频道不再建 canvases 行（表冻结仅存量）。
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
    # 清理非不可变依赖行后删频道（canvases 为 DEDAG 前存量行，删以保 FK 完整性）。
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


# ---------------------------------------------------------------- 每频道通知设置（M5 §4.5/§11.4）


def _notif_subject(
    request: Request, tx: Tx, channel_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """GET/PUT notification-setting 共用门（契约 B §4.5/§11.4）：解析主体 + 频道约束。

    通知是**人类面**：用 acting_member 解析主体（浏览器=Owner 人类，成员自治**无 admin 门**）；
    Agent 主体 → 403 PERMISSION_DENIED（Agent 无人类通知面）。kind=dm → 422 NOTIF_IN_DM
    （DM 必达，无设置面——dm activity 恒生成不受任何 mode 影响）。返回 (channel, me)。
    """
    channel = _fetch_channel(tx, channel_id)
    me = acting_member(request, tx.conn)
    if me["kind"] != MemberKind.HUMAN:
        raise ApiError(
            403,
            rest.ErrorCode.PERMISSION_DENIED,
            "通知设置是人类面，Agent 无设置权",
            rule="B§11.4",
        )
    if channel["kind"] == ChannelKind.DM:
        raise ApiError(422, rest.ErrorCode.NOTIF_IN_DM, "DM 必达，无通知设置面", rule="B§11.4")
    return channel, me


@router.get(
    "/channels/{channel_id}/notification-setting",
    response_model=entities.ChannelNotificationSettingPublic,
)
def get_notification_setting(channel_id: str, request: Request, tx: Tx = Depends(get_tx)) -> Any:
    _, me = _notif_subject(request, tx, channel_id)
    row = (
        tx.conn.execute(
            select(_NOTIF).where(
                _NOTIF.c.channel_id == channel_id, _NOTIF.c.member_id == me["id"]
            )
        )
        .mappings()
        .first()
    )
    if row is None:  # 懒建：无行回默认 all（仅 PUT 落行，GET 不写库）
        return _notif_setting_public(
            {"channel_id": channel_id, "member_id": me["id"], "mode": NotificationMode.ALL}
        )
    return _notif_setting_public(dict(row))


@router.put(
    "/channels/{channel_id}/notification-setting",
    response_model=entities.ChannelNotificationSettingPublic,
)
def put_notification_setting(
    channel_id: str,
    body: rest.NotificationSettingPut,
    request: Request,
    tx: Tx = Depends(get_tx),
) -> Any:
    _, me = _notif_subject(request, tx, channel_id)
    # 原子 upsert（复合 PK channel_id+member_id）——消除"先查后插"并发双 PUT 的 TOCTOU
    # （两请求都读到无行 → 双 insert → 复合 PK 冲突 IntegrityError 500）。零新增 WS 事件。
    stmt = sqlite_insert(_NOTIF).values(channel_id=channel_id, member_id=me["id"], mode=body.mode)
    tx.conn.execute(
        stmt.on_conflict_do_update(
            index_elements=[_NOTIF.c.channel_id, _NOTIF.c.member_id],
            set_={"mode": body.mode},
        )
    )
    row = models.row_dict(
        tx.conn.execute(
            select(_NOTIF).where(
                _NOTIF.c.channel_id == channel_id, _NOTIF.c.member_id == me["id"]
            )
        )
        .mappings()
        .first()
    )
    return _notif_setting_public(row)
