"""Activity 聚合面（契约 B §9.7 / §4.8，FR-4.6）：GET /activity 读 + POST /activity/{id}/done。

MVP 单人：只查 Owner 人类成员（deps.owner_member）的 activity_items。activity_items 行由
messages.py 的 mention/dm 生成逻辑落库（并行工作）；本模块只读该表 + 更新 done_at，天然解耦。
"""

from __future__ import annotations

from typing import Any

from coagentia_contracts import entities, rest
from coagentia_contracts.enums import ActivityFilter, ActivityKind
from coagentia_contracts.ws import EventType
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select, update

from coagentia_server.api import ApiError
from coagentia_server.db import models
from coagentia_server.deps import Tx, acting_member, get_tx, owner_member
from coagentia_server.ledger import service
from coagentia_server.routes._pagination import keyset_page
from coagentia_server.routes.serialize import activity_item_public

router = APIRouter(prefix="/api", tags=["activity"])

_ACT = models.tbl(models.ActivityItem)
_MSG = models.tbl(models.Message)


@router.get("/activity", response_model=rest.Page[entities.ActivityItemPublic])
def list_activity(
    tx: Tx = Depends(get_tx),
    filter: ActivityFilter = ActivityFilter.ALL,
    after: str | None = None,
    limit: int = rest.PAGE_DEFAULT_LIMIT,
) -> Any:
    me = owner_member(tx.conn)
    # actor_member_id = 触发消息的作者（Public 派生字段，联查 messages，不落库）。
    stmt = (
        select(_ACT, _MSG.c.author_member_id.label("actor_member_id"))
        .select_from(_ACT.outerjoin(_MSG, _ACT.c.message_id == _MSG.c.id))
        .where(_ACT.c.member_id == me["id"])
    )
    if filter is ActivityFilter.UNREAD:  # 未处理 = done_at IS NULL
        stmt = stmt.where(_ACT.c.done_at.is_(None))
    elif filter is ActivityFilter.MENTIONS:
        stmt = stmt.where(_ACT.c.kind == ActivityKind.MENTION)
    # 倒序（created_at desc, id desc）——最新在前；keyset 游标 + LIMIT 下推（不再全量材料化）。
    return keyset_page(
        tx.conn,
        _ACT,
        stmt,
        after=after,
        limit=limit,
        desc=True,
        serialize=activity_item_public,
    )


@router.post("/activity/{activity_id}/done", response_model=entities.ActivityItemPublic)
def mark_activity_done(activity_id: str, request: Request, tx: Tx = Depends(get_tx)) -> Any:
    # 写面与读面同一归属门：只有条目接收者可置 done。主体走 acting_member（同 messages
    # 写路径）——Agent 经 Bearer 代理会解析成 agent 成员，与人类条目归属不匹配 → 404，
    # 挡住"任何主体可清 Owner 未读"（M2 二轮 review）；按不存在处理不泄露存在性。
    me = acting_member(request, tx.conn)
    row = (
        tx.conn.execute(
            select(_ACT).where(_ACT.c.id == activity_id, _ACT.c.member_id == me["id"])
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "Activity 不存在")
    row = dict(row)
    if row["message_id"] is not None:  # 响应形状与 list 一致：补 actor 派生字段
        row["actor_member_id"] = tx.conn.execute(
            select(_MSG.c.author_member_id).where(_MSG.c.id == row["message_id"])
        ).scalar_one_or_none()
    if row["done_at"] is not None:  # 幂等：已 done → 原样返回、不重复广播
        return activity_item_public(row)
    ts = service.now_iso()
    tx.conn.execute(update(_ACT).where(_ACT.c.id == activity_id).values(done_at=ts))
    row["done_at"] = ts
    # activity.done 是个人面事件（无频道归属）→ 全局广播（channel_id=None）。
    tx.emit(EventType.ACTIVITY_DONE, None, {"item_id": activity_id})
    return activity_item_public(row)
