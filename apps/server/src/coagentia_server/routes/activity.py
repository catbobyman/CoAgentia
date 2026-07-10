"""Activity 聚合面（契约 B §9.7 / §4.8，FR-4.6）：GET /activity 读 + POST /activity/{id}/done。

MVP 单人：只查 Owner 人类成员（deps.owner_member）的 activity_items。activity_items 行由
messages.py 的 mention/dm 生成逻辑落库（并行工作）；本模块只读该表 + 更新 done_at，天然解耦。
"""

from __future__ import annotations

from typing import Any

from coagentia_contracts import entities, rest
from coagentia_contracts.enums import ActivityFilter, ActivityKind
from coagentia_contracts.ws import EventType
from fastapi import APIRouter, Depends
from sqlalchemy import select, update

from coagentia_server.api import ApiError
from coagentia_server.db import models
from coagentia_server.deps import Tx, get_tx, owner_member
from coagentia_server.ledger import service
from coagentia_server.routes._pagination import cursor_page
from coagentia_server.routes.serialize import activity_item_public

router = APIRouter(prefix="/api", tags=["activity"])

_ACT = models.ActivityItem.__table__


@router.get("/activity", response_model=rest.Page[entities.ActivityItemPublic])
def list_activity(
    tx: Tx = Depends(get_tx),
    filter: ActivityFilter = ActivityFilter.ALL,
    after: str | None = None,
    limit: int = rest.PAGE_DEFAULT_LIMIT,
) -> Any:
    me = owner_member(tx.conn)
    stmt = select(_ACT).where(_ACT.c.member_id == me["id"])
    if filter is ActivityFilter.UNREAD:  # 未处理 = done_at IS NULL
        stmt = stmt.where(_ACT.c.done_at.is_(None))
    elif filter is ActivityFilter.MENTIONS:
        stmt = stmt.where(_ACT.c.kind == ActivityKind.MENTION)
    # 倒序（created_at desc, id desc）——最新在前；游标 after=id 往后翻。
    rows = [
        dict(r)
        for r in tx.conn.execute(
            stmt.order_by(_ACT.c.created_at.desc(), _ACT.c.id.desc())
        ).mappings()
    ]
    return cursor_page(rows, after, limit, activity_item_public)


@router.post("/activity/{activity_id}/done", response_model=entities.ActivityItemPublic)
def mark_activity_done(activity_id: str, tx: Tx = Depends(get_tx)) -> Any:
    row = tx.conn.execute(select(_ACT).where(_ACT.c.id == activity_id)).mappings().first()
    if row is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "Activity 不存在")
    row = dict(row)
    if row["done_at"] is not None:  # 幂等：已 done → 原样返回、不重复广播
        return activity_item_public(row)
    ts = service.now_iso()
    tx.conn.execute(update(_ACT).where(_ACT.c.id == activity_id).values(done_at=ts))
    final = dict(
        tx.conn.execute(select(_ACT).where(_ACT.c.id == activity_id)).mappings().first()
    )
    # activity.done 是个人面事件（无频道归属）→ 全局广播（channel_id=None）。
    tx.emit(EventType.ACTIVITY_DONE, None, {"item_id": activity_id})
    return activity_item_public(final)
