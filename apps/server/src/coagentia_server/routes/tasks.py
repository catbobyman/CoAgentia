"""任务域 REST 端点（契约 B §9 / §4.7）：convert、claim/unclaim/assign、status、列表、详情、补丁。

范式照抄 routes/messages.py（router 前缀 /api、acting_member 身份、ApiError 报错、tx.emit 广播）；
状态机、建号、留痕、建任务集中在 tasks/service.py（纪律 7 单一事实源）。
"""

from __future__ import annotations

from typing import Any

from coagentia_contracts import entities, rest
from coagentia_contracts.enums import ChannelKind, TaskEventKind, TaskStatus
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from coagentia_server.api import ApiError
from coagentia_server.db import models
from coagentia_server.deps import Tx, acting_member, get_tx
from coagentia_server.routes.serialize import task_public
from coagentia_server.tasks import service as tasks_service

router = APIRouter(prefix="/api", tags=["tasks"])

_TASK = models.Task.__table__
_EVT = models.TaskEvent.__table__
_CHANNEL = models.Channel.__table__
_MSG = models.Message.__table__
_MEMBER = models.Member.__table__
_TUE = models.TokenUsageEvent.__table__


def _require_task(tx: Tx, task_id: str) -> dict[str, Any]:
    row = tx.conn.execute(select(_TASK).where(_TASK.c.id == task_id)).mappings().first()
    if row is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "任务不存在")
    return dict(row)


# ---------------------------------------------------------------- Convert to Task


@router.post(
    "/messages/{message_id}/task", response_model=entities.TaskPublic, status_code=201
)
def convert_message_to_task(
    message_id: str,
    body: rest.ConvertToTask,
    request: Request,
    response: Response,
    tx: Tx = Depends(get_tx),
) -> Any:
    msg = tx.conn.execute(select(_MSG).where(_MSG.c.id == message_id)).mappings().first()
    if msg is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "消息不存在")

    # 幂等最强不变量（root_message_id UNIQUE）：已转过 → 直接返回既有任务 200（裁决 2）。
    prior = (
        tx.conn.execute(select(_TASK).where(_TASK.c.root_message_id == message_id))
        .mappings()
        .first()
    )
    if prior is not None:
        response.status_code = 200
        return task_public(dict(prior))

    channel = tx.conn.execute(
        select(_CHANNEL).where(_CHANNEL.c.id == msg["channel_id"])
    ).mappings().first()
    if channel is not None and channel["archived_at"] is not None:
        raise ApiError(409, rest.ErrorCode.CHANNEL_ARCHIVED, "归档频道不可转任务", rule="FR-1.3")
    if channel is not None and channel["kind"] == ChannelKind.DM:
        raise ApiError(422, rest.ErrorCode.TASK_IN_DM, "DM 不承载任务", rule="FR-5.1")
    if msg["thread_root_id"] is not None:
        raise ApiError(422, rest.ErrorCode.NOT_TOP_LEVEL_MESSAGE, "仅顶级消息可转任务", rule="T3")

    me = acting_member(request, tx.conn)
    try:
        # SAVEPOINT 包裹建任务（范式同 ledger.record）：上方 prior 预查有 TOCTOU 窗口，
        # 并发 convert 抢先时 UNIQUE(root_message_id) 触发 IntegrityError——只回退本段
        # （含 allocate_number 的编号自增，不漏号），退化为幂等命中返回既有任务 200。
        with tx.conn.begin_nested():
            task_row = tasks_service.create_task(
                tx,
                workspace_id=msg["workspace_id"],
                channel_id=msg["channel_id"],
                root_message_id=message_id,
                created_by=me["id"],
                title=body.title,
                source_body=msg["body"],
            )
    except IntegrityError:
        prior = (
            tx.conn.execute(select(_TASK).where(_TASK.c.root_message_id == message_id))
            .mappings()
            .first()
        )
        if prior is None:  # 非 root_message_id 冲突（防御：其它完整性错误不吞）
            raise
        response.status_code = 200
        return task_public(dict(prior))
    tasks_service.emit_task_created(tx, task_row)
    return task_public(task_row)


# ---------------------------------------------------------------- claim / unclaim / assign


@router.post("/tasks/{task_id}/claim", response_model=entities.TaskPublic)
def claim_task(task_id: str, request: Request, tx: Tx = Depends(get_tx)) -> Any:
    task = _require_task(tx, task_id)
    me = acting_member(request, tx.conn)
    cur = TaskStatus(task["status"])
    if cur in tasks_service.UNCLAIMABLE_STATUSES:  # 终态门（review 裁决）：done/closed 不可认领
        raise ApiError(
            422,
            rest.ErrorCode.TASK_TRANSITION_INVALID,
            f"{cur.value} 任务不可认领（closed 需先 reopen 回 todo）",
            rule="T2",
            details={"status": cur.value},
        )
    # 条件更新 = 并发闸：同刻仅一事务能把 NULL→非空（T2 恰一成功）。
    res = tx.conn.execute(
        update(_TASK)
        .where(_TASK.c.id == task_id, _TASK.c.owner_member_id.is_(None))
        .values(owner_member_id=me["id"])
    )
    if res.rowcount == 0:
        cur = tx.conn.execute(
            select(_TASK.c.owner_member_id).where(_TASK.c.id == task_id)
        ).scalar_one()
        raise ApiError(
            409,
            rest.ErrorCode.CLAIM_RACE,
            "任务已被他人认领",
            rule="T2",
            details={"current_owner": cur},
        )
    ts = tasks_service.service.now_iso()
    tasks_service.write_event(tx.conn, task_id, TaskEventKind.CLAIM, actor=me["id"], owner=me["id"])
    fresh = tx.conn.execute(select(_TASK.c.status).where(_TASK.c.id == task_id)).scalar_one()
    change_from = change_to = None
    if TaskStatus(fresh) == TaskStatus.TODO:  # 联动 todo→in_progress（裁决 1）
        tx.conn.execute(
            update(_TASK)
            .where(_TASK.c.id == task_id)
            .values(status=TaskStatus.IN_PROGRESS, status_changed_at=ts)
        )
        tasks_service.write_event(
            tx.conn,
            task_id,
            TaskEventKind.STATUS_CHANGE,
            actor=me["id"],
            from_status=TaskStatus.TODO,
            to_status=TaskStatus.IN_PROGRESS,
        )
        change_from, change_to = TaskStatus.TODO, TaskStatus.IN_PROGRESS
    final = tasks_service.fetch_task(tx.conn, task_id)
    tasks_service.emit_task_updated(
        tx, final, kind=TaskEventKind.CLAIM, actor=me["id"],
        from_status=change_from, to_status=change_to,
    )
    return task_public(final)


@router.post("/tasks/{task_id}/unclaim", response_model=entities.TaskPublic)
def unclaim_task(task_id: str, request: Request, tx: Tx = Depends(get_tx)) -> Any:
    task = _require_task(tx, task_id)
    me = acting_member(request, tx.conn)
    if task["owner_member_id"] != me["id"]:  # 仅本人可释放（B §9.2；改派用 assign）
        raise ApiError(
            403,
            rest.ErrorCode.PERMISSION_DENIED,
            "只能释放自己认领的任务（改派请用 assign）",
            rule="T2",
        )
    ts = tasks_service.service.now_iso()
    tx.conn.execute(update(_TASK).where(_TASK.c.id == task_id).values(owner_member_id=None))
    tasks_service.write_event(tx.conn, task_id, TaskEventKind.UNCLAIM, actor=me["id"], owner=None)
    change_from = change_to = None
    if TaskStatus(task["status"]) == TaskStatus.IN_PROGRESS:  # 联动回 todo
        tx.conn.execute(
            update(_TASK)
            .where(_TASK.c.id == task_id)
            .values(status=TaskStatus.TODO, status_changed_at=ts)
        )
        tasks_service.write_event(
            tx.conn,
            task_id,
            TaskEventKind.STATUS_CHANGE,
            actor=me["id"],
            from_status=TaskStatus.IN_PROGRESS,
            to_status=TaskStatus.TODO,
        )
        change_from, change_to = TaskStatus.IN_PROGRESS, TaskStatus.TODO
    final = tasks_service.fetch_task(tx.conn, task_id)
    tasks_service.emit_task_updated(
        tx, final, kind=TaskEventKind.UNCLAIM, actor=me["id"],
        from_status=change_from, to_status=change_to,
    )
    return task_public(final)


@router.post("/tasks/{task_id}/assign", response_model=entities.TaskPublic)
def assign_task(
    task_id: str, body: rest.AssignRequest, request: Request, tx: Tx = Depends(get_tx)
) -> Any:
    _require_task(tx, task_id)
    me = acting_member(request, tx.conn)
    if body.member_id is not None:  # 非空须存在且未软删
        exists = tx.conn.execute(
            select(_MEMBER.c.id).where(
                _MEMBER.c.id == body.member_id, _MEMBER.c.removed_at.is_(None)
            )
        ).first()
        if exists is None:
            raise ApiError(404, rest.ErrorCode.NOT_FOUND, "指派目标成员不存在")
    # 后写胜出，不动 status / status_changed_at（裁决 1）。
    tx.conn.execute(
        update(_TASK).where(_TASK.c.id == task_id).values(owner_member_id=body.member_id)
    )
    tasks_service.write_event(
        tx.conn, task_id, TaskEventKind.ASSIGN, actor=me["id"], owner=body.member_id
    )
    final = tasks_service.fetch_task(tx.conn, task_id)
    tasks_service.emit_task_updated(tx, final, kind=TaskEventKind.ASSIGN, actor=me["id"])
    return task_public(final)


# ---------------------------------------------------------------- status（状态机）


@router.post("/tasks/{task_id}/status", response_model=entities.TaskPublic)
def set_task_status(
    task_id: str, body: rest.TaskStatusChange, request: Request, tx: Tx = Depends(get_tx)
) -> Any:
    task = _require_task(tx, task_id)
    me = acting_member(request, tx.conn)
    cur = TaskStatus(task["status"])
    to = body.to
    if to == cur:  # 同态幂等：不写事件、不广播（裁决 2）
        return task_public(task)
    if to not in tasks_service.TASK_TRANSITIONS[cur]:  # 非法边
        raise ApiError(
            422,
            rest.ErrorCode.TASK_TRANSITION_INVALID,
            f"任务不能从 {cur.value} 流转到 {to.value}",
            rule="T4",
            details={
                "from": cur.value,
                "to": to.value,
                "allowed": sorted(s.value for s in tasks_service.TASK_TRANSITIONS[cur]),
            },
        )
    ts = tasks_service.service.now_iso()
    tx.conn.execute(
        update(_TASK).where(_TASK.c.id == task_id).values(status=to, status_changed_at=ts)
    )
    tasks_service.write_event(
        tx.conn, task_id, TaskEventKind.STATUS_CHANGE, actor=me["id"], from_status=cur, to_status=to
    )
    final = tasks_service.fetch_task(tx.conn, task_id)
    tasks_service.emit_task_updated(
        tx, final, kind=TaskEventKind.STATUS_CHANGE, actor=me["id"], from_status=cur, to_status=to
    )
    return task_public(final)


# ---------------------------------------------------------------- 列表 / 详情 / 补丁


@router.get("/tasks", response_model=rest.Page[entities.TaskPublic])
def list_tasks(
    tx: Tx = Depends(get_tx),
    channel_id: str | None = None,
    status: str | None = None,
    owner: str | None = None,
    creator: str | None = None,
    after: str | None = None,
    limit: int = rest.PAGE_DEFAULT_LIMIT,
) -> Any:
    stmt = select(_TASK)
    if channel_id is not None:
        stmt = stmt.where(_TASK.c.channel_id == channel_id)
    if status is not None:  # 无效值 → 空结果集，不报错（过滤器宽容）
        stmt = stmt.where(_TASK.c.status == status)
    if owner is not None:
        stmt = stmt.where(_TASK.c.owner_member_id == owner)
    if creator is not None:
        stmt = stmt.where(_TASK.c.created_by_member_id == creator)
    rows = [
        dict(r)
        for r in tx.conn.execute(stmt.order_by(_TASK.c.created_at, _TASK.c.id)).mappings()
    ]
    ids = [t["id"] for t in rows]
    if after and after in ids:
        rows = rows[ids.index(after) + 1 :]
    limit = min(max(1, limit), rest.PAGE_MAX_LIMIT)
    page, tail = rows[:limit], rows[limit:]
    next_cursor = page[-1]["id"] if tail and page else None
    return {"items": [task_public(t) for t in page], "next_cursor": next_cursor}


@router.get("/tasks/{task_id}", response_model=rest.TaskDetail)
def get_task_detail(task_id: str, tx: Tx = Depends(get_tx)) -> Any:
    task = _require_task(tx, task_id)
    # contracts 恒空：task_contracts 是 M3 表（M2 不查）。
    agg = tx.conn.execute(
        select(
            func.coalesce(func.sum(_TUE.c.input_tokens), 0),
            func.coalesce(func.sum(_TUE.c.output_tokens), 0),
            func.coalesce(func.sum(_TUE.c.cache_read_tokens), 0),
            func.coalesce(func.sum(_TUE.c.cache_write_tokens), 0),
            func.count(_TUE.c.id),
        ).where(_TUE.c.task_id == task_id)
    ).first()
    return {
        "task": task_public(task),
        "contracts": [],
        "usage": {
            "input_tokens": agg[0],
            "output_tokens": agg[1],
            "cache_read_tokens": agg[2],
            "cache_write_tokens": agg[3],
            "events": agg[4],
        },
    }


@router.patch("/tasks/{task_id}", response_model=entities.TaskPublic)
def patch_task(
    task_id: str, body: rest.TaskPatch, request: Request, tx: Tx = Depends(get_tx)
) -> Any:
    _require_task(tx, task_id)
    acting_member(request, tx.conn)  # 身份校验（R4 无角色门）
    changes = {
        k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None
    }
    if changes:
        tx.conn.execute(update(_TASK).where(_TASK.c.id == task_id).values(**changes))
    final = tasks_service.fetch_task(tx.conn, task_id)
    # PATCH 不写 task_events；广播 task.updated 且 change=None（契约 C §6.4 放宽）。
    tx.emit(
        tasks_service.EventType.TASK_UPDATED,
        final["channel_id"],
        {"task": task_public(final), "change": None},
    )
    return task_public(final)
