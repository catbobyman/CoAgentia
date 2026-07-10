"""任务域服务层（契约 B §9）：建号、建任务、状态机校验、留痕、WS 发射。

convert / as_task / claim / unclaim / assign / status 多处复用本层；状态机唯一事实源 =
contracts 的 TASK_TRANSITIONS 常量（纪律 7），本层只消费不复制字面量。
"""

from __future__ import annotations

import re
from typing import Any

from coagentia_contracts.constants import TASK_TRANSITIONS, UNCLAIMABLE_STATUSES
from coagentia_contracts.enums import TaskEventKind, TaskLevel, TaskStatus
from coagentia_contracts.ws import EventType
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection

from coagentia_server.db import models
from coagentia_server.ledger import service
from coagentia_server.routes.serialize import task_public

_TASK = models.Task.__table__
_EVT = models.TaskEvent.__table__
_CHANNEL = models.Channel.__table__

# 首非空行剥 Markdown 前缀（标题/列表/引用/有序号）。列表/引用/有序号标记须后跟空白才算前缀，
# 否则 `3.14 is pi` 会被 `\d+\.` 误剥成 `14 is pi`、`*bold*` 被 `[-*>]` 误剥成 `bold*`。
_MD_PREFIX = re.compile(r"^\s*(?:#{1,6}|[-*>]\s|\d+\.\s)\s*")

# 终态不可认领（review 裁决 2026-07-09）：done 无出边；closed 须先 reopen→todo 再认领。
# 这不是第二份边表（纪律 7）——claim 语义门；唯一事实源 = contracts UNCLAIMABLE_STATUSES
# （server 校验 + 前端认领钮防呆同源，同 TASK_TRANSITIONS 机读化到 contracts-ts）。
_TITLE_MAX = 80
_TITLE_FALLBACK = "未命名任务"


# ---------------------------------------------------------------- 标题与建号


def default_title(source_body: str) -> str:
    """契约 B §9.3.4：body 首非空行 → 剥 MD 前缀 → strip → >80 截断加省略号。"""
    for line in source_body.splitlines():
        stripped = _MD_PREFIX.sub("", line).strip()
        if stripped:
            if len(stripped) > _TITLE_MAX:
                return stripped[:_TITLE_MAX] + "…"
            return stripped
    return _TITLE_FALLBACK


def allocate_number(conn: Connection, channel_id: str) -> int:
    """channels.next_task_number 同事务原子读-增（契约 B §9.3.1）。

    单条 UPDATE…RETURNING 在语句内取写锁，无"先 SELECT 后 UPDATE"锁升级间隙；
    并发建号严格串行、不重号（UNIQUE(channel_id, number) 兜底）。
    """
    new_next = conn.execute(
        update(_CHANNEL)
        .where(_CHANNEL.c.id == channel_id)
        .values(next_task_number=_CHANNEL.c.next_task_number + 1)
        .returning(_CHANNEL.c.next_task_number)
    ).scalar_one()
    return new_next - 1  # 分配的编号 = 自增前值


# ---------------------------------------------------------------- 留痕


def write_event(
    conn: Connection,
    task_id: str,
    kind: TaskEventKind,
    *,
    actor: str | None = None,
    owner: str | None = None,
    from_status: TaskStatus | None = None,
    to_status: TaskStatus | None = None,
) -> None:
    """向不可变表 task_events 追加一行（永不 UPDATE/DELETE；触发器兜底）。"""
    conn.execute(
        insert(_EVT).values(
            task_id=task_id,
            kind=kind,
            from_status=from_status,
            to_status=to_status,
            owner_member_id=owner,
            actor_member_id=actor,
            created_at=service.now_iso(),
        )
    )


# ---------------------------------------------------------------- 建任务


def fetch_task(conn: Connection, task_id: str) -> dict[str, Any]:
    row = conn.execute(select(_TASK).where(_TASK.c.id == task_id)).mappings().first()
    assert row is not None
    return dict(row)


def create_task(
    tx: Any,
    *,
    workspace_id: str,
    channel_id: str,
    root_message_id: str,
    created_by: str,
    title: str | None = None,
    source_body: str = "",
) -> dict[str, Any]:
    """convert 与 as_task 共用的建任务（B §9.3）；status 起始 todo，无 created 事件。"""
    number = allocate_number(tx.conn, channel_id)
    resolved = (title or "").strip() or default_title(source_body)
    ts = service.now_iso()
    tid = service.new_ulid()
    tx.conn.execute(
        insert(_TASK).values(
            id=tid,
            workspace_id=workspace_id,
            channel_id=channel_id,
            number=number,
            root_message_id=root_message_id,
            title=resolved,
            status=TaskStatus.TODO,
            owner_member_id=None,
            level=TaskLevel.L1,
            created_by_member_id=created_by,
            silence_override_h=None,
            status_changed_at=ts,
            created_at=ts,
        )
    )
    return fetch_task(tx.conn, tid)


# ---------------------------------------------------------------- WS 发射


def emit_task_created(tx: Any, task_row: dict[str, Any]) -> None:
    tx.emit(EventType.TASK_CREATED, task_row["channel_id"], {"task": task_public(task_row)})


def emit_task_updated(
    tx: Any,
    task_row: dict[str, Any],
    *,
    kind: TaskEventKind,
    actor: str | None,
    from_status: TaskStatus | None = None,
    to_status: TaskStatus | None = None,
) -> None:
    """一动作一 task.updated 帧（设计裁决 D-WS）；change.kind = 端点主动作。"""
    tx.emit(
        EventType.TASK_UPDATED,
        task_row["channel_id"],
        {
            "task": task_public(task_row),
            "change": {
                "kind": kind,
                "from_status": from_status,
                "to_status": to_status,
                "actor_member_id": actor,
            },
        },
    )


__all__ = [
    "TASK_TRANSITIONS",
    "UNCLAIMABLE_STATUSES",
    "allocate_number",
    "create_task",
    "default_title",
    "emit_task_created",
    "emit_task_updated",
    "fetch_task",
    "write_event",
]
