"""成本核算域 REST 端点（契约 B §13.4；M7b K6）。

`GET /usage?level=task|agent|canvas&ref=&rollup=<bool>` → rest.UsageReport。三层聚合维度：

- level=task：单任务 token 聚合（WHERE task_id=ref）；tasks_reporting 恒 {events>0?1:0, 1}。
- level=agent：usage = 该 agent 全部 token_usage_events 之和（WHERE agent_member_id=ref）；
  total = 该 agent owner 的所有任务数（tasks.owner_member_id=ref，含零上报任务→诚实覆盖率
  W7）；reporting = 其中有 ≥1 条 usage 事件的任务数。
- level=canvas：ref=channel_id；任务集 = tasks.channel_id=ref；usage = 这些任务的事件和
  （WHERE task_id IN 任务集）；total = 任务数，reporting = 有 usage 的任务数。（DEDAG：画布
  退役，维度名 canvas 沿契约 B 保留，语义 = 频道级任务集聚合，不读任何画布表。）

rollup=True → 附 breakdown 逐任务明细（ref=task_id，label=title，usage=该任务事件和；零上报任务
usage 全 0 仍列入）。永不折算货币（W7，无货币字段）。

聚合 SQL 活 server 单点（纪律 7）——本模块是块 b 唯一新账/成本聚合 SQL 归属处，前端只消费形状。
usage 四列 sum + 事件 count 体例照 routes/tasks.py TaskDetail.usage /
deployments.compute_token_summary（同源同形，永不重复实现于别处）。
"""

from __future__ import annotations

from typing import Any

from coagentia_contracts import rest
from coagentia_contracts.entities import TasksReporting, UsageBucket
from coagentia_contracts.enums import UsageLevel
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.engine import Connection
from sqlalchemy.sql import ColumnElement

from coagentia_server.db import models
from coagentia_server.deps import Tx, get_tx

router = APIRouter(prefix="/api", tags=["usage"])

_TASK = models.tbl(models.Task)
_TUE = models.tbl(models.TokenUsageEvent)


# ---------------------------------------------------------------- 聚合原语（纪律 7 单点）


def _bucket(conn: Connection, *conds: ColumnElement[bool]) -> UsageBucket:
    """token_usage_events 四列 sum + 事件 count（照 tasks.py TaskDetail.usage 体例）。

    conds 为空集（如 task_id IN []）时聚合恒返回一行零值——one() 免 Optional。
    """
    agg = conn.execute(
        select(
            func.coalesce(func.sum(_TUE.c.input_tokens), 0),
            func.coalesce(func.sum(_TUE.c.output_tokens), 0),
            func.coalesce(func.sum(_TUE.c.cache_read_tokens), 0),
            func.coalesce(func.sum(_TUE.c.cache_write_tokens), 0),
            func.count(_TUE.c.id),
        ).where(*conds)
    ).one()
    return UsageBucket(
        input_tokens=agg[0],
        output_tokens=agg[1],
        cache_read_tokens=agg[2],
        cache_write_tokens=agg[3],
        events=agg[4],
    )


def _task_set_report(
    conn: Connection,
    task_rows: list[Any],
    rollup: bool,
) -> tuple[UsageBucket, TasksReporting, list[rest.UsageBreakdownItem] | None]:
    """任务集口径（agent/canvas 共用）：usage / reporting / breakdown **全部按同一任务集
    (task_id IN 集) 聚合**——usage = Σ breakdown、覆盖率 reporting/total 与明细同源（复审
    CONFIRMED：旧版 agent 层 usage 按 agent_member_id 聚合、覆盖率/明细却按 owner 任务集，
    二者互不一致——agent 在非自有任务/未归属事件上的花费会让 usage > Σ breakdown）。
    total = 任务集大小（未上报任务计入分母，W7 诚实覆盖率）；reporting = 任务集中有 ≥1 条 usage
    事件的任务数（按 task_id 去重）。rollup 时附逐任务明细。
    """
    task_ids = [r[0] for r in task_rows]
    usage = _bucket(conn, _TUE.c.task_id.in_(task_ids)) if task_ids else UsageBucket()
    reporting = 0
    by_task: dict[str, UsageBucket] = {}
    if task_ids:
        reporting = conn.execute(
            select(func.count(func.distinct(_TUE.c.task_id))).where(
                _TUE.c.task_id.in_(task_ids)
            )
        ).scalar_one()
        if rollup:
            for r in conn.execute(
                select(
                    _TUE.c.task_id,
                    func.coalesce(func.sum(_TUE.c.input_tokens), 0),
                    func.coalesce(func.sum(_TUE.c.output_tokens), 0),
                    func.coalesce(func.sum(_TUE.c.cache_read_tokens), 0),
                    func.coalesce(func.sum(_TUE.c.cache_write_tokens), 0),
                    func.count(_TUE.c.id),
                )
                .where(_TUE.c.task_id.in_(task_ids))
                .group_by(_TUE.c.task_id)
            ):
                by_task[r[0]] = UsageBucket(
                    input_tokens=r[1],
                    output_tokens=r[2],
                    cache_read_tokens=r[3],
                    cache_write_tokens=r[4],
                    events=r[5],
                )
    reporting_meta = TasksReporting(reporting=reporting, total=len(task_rows))
    breakdown: list[rest.UsageBreakdownItem] | None = None
    if rollup:
        breakdown = [
            rest.UsageBreakdownItem(
                ref=r[0], label=r[1], usage=by_task.get(r[0], UsageBucket())
            )
            for r in task_rows
        ]
    return usage, reporting_meta, breakdown


# ---------------------------------------------------------------- 端点


@router.get("/usage", response_model=rest.UsageReport)
def get_usage(
    level: UsageLevel,
    ref: str,
    tx: Tx = Depends(get_tx),
    rollup: bool = False,
) -> Any:
    """三层成本核算（纯读；无副作用）。level/ref 无效或空集 → usage 全 0（不 404）。

    level 值域校验、ref 必填由 FastAPI 依 UsageLevel 枚举 / 必填参数处理（缺失→422）。
    """
    conn = tx.conn
    if level == UsageLevel.TASK:
        usage = _bucket(conn, _TUE.c.task_id == ref)
        # 单任务恒 {events>0?1:0, 1}：分母恒 1（该任务本身），有事件才算上报。
        reporting = TasksReporting(
            reporting=1 if usage.events > 0 else 0, total=1
        )
        return rest.UsageReport(
            level=level, ref=ref, usage=usage, tasks_reporting=reporting, breakdown=None
        )

    if level == UsageLevel.AGENT:
        task_rows = conn.execute(
            select(_TASK.c.id, _TASK.c.title)
            .where(_TASK.c.owner_member_id == ref)
            .order_by(_TASK.c.id)
        ).all()
        usage, reporting, breakdown = _task_set_report(conn, list(task_rows), rollup)
        return rest.UsageReport(
            level=level,
            ref=ref,
            usage=usage,
            tasks_reporting=reporting,
            breakdown=breakdown,
        )

    # level == UsageLevel.CANVAS：ref=channel_id，任务集 = 该频道全部任务（DEDAG：画布退役，
    # 维度名保留、语义即频道级聚合）。
    task_rows = conn.execute(
        select(_TASK.c.id, _TASK.c.title)
        .where(_TASK.c.channel_id == ref)
        .order_by(_TASK.c.id)
    ).all()
    usage, reporting, breakdown = _task_set_report(conn, list(task_rows), rollup)
    return rest.UsageReport(
        level=level,
        ref=ref,
        usage=usage,
        tasks_reporting=reporting,
        breakdown=breakdown,
    )
