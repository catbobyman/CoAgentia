"""M6 系统节点的触发、执行计划、终态与冲突派回。

daemon 仅执行命令；本模块在 server 侧决定 gating、DAG 合并顺序、重试与冲突任务结构。
运行身份复用不可变 ``diagnostic_events(type='agent.command')``，不新增状态列。
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Any

from coagentia_contracts import rest
from coagentia_contracts.daemon import (
    CheckFinishedData,
    CheckRunData,
    WorktreeMergeData,
    WorktreeStatusData,
)
from coagentia_contracts.enums import (
    CanvasNodeKind,
    CardKind,
    LandingBatchStatus,
    SystemAction,
    SystemNodeStatus,
    TaskEventKind,
    TaskLevel,
    TaskStatus,
    WorktreeStatus,
)
from coagentia_contracts.ws import EventType
from sqlalchemy import func, insert, select, update

from coagentia_server.api import ApiError
from coagentia_server.canvas import service as canvas_service
from coagentia_server.db import models
from coagentia_server.ledger.service import new_ulid, now_iso
from coagentia_server.messages.service import post_system_message
from coagentia_server.routes.serialize import (
    canvas_edge_public,
    canvas_node_public,
    worktree_public,
)
from coagentia_server.tasks import service as tasks_service

_NODE = models.tbl(models.CanvasNode)
_EDGE = models.tbl(models.CanvasEdge)
_CANVAS = models.tbl(models.Canvas)
_TASK = models.tbl(models.Task)
_PROJECT = models.tbl(models.Project)
_CHANNEL_PROJECT = models.tbl(models.ChannelProject)
_WORKTREE = models.tbl(models.Worktree)
_DIAG = models.tbl(models.DiagnosticEvent)
_MEMBER = models.tbl(models.Member)

_DIAGNOSTIC_TYPE = "agent.command"


@dataclass(frozen=True, slots=True)
class CheckDispatch:
    node_id: str
    channel_id: str
    computer_id: str
    data: CheckRunData


@dataclass(frozen=True, slots=True)
class MergeDispatch:
    node_id: str
    channel_id: str
    computer_id: str
    data: WorktreeMergeData


Dispatch = CheckDispatch | MergeDispatch


@dataclass(frozen=True, slots=True)
class _MergeStep:
    node_id: str
    task_id: str
    task_number: int
    task_title: str
    project_id: str
    computer_id: str
    repo_path: str
    branch: str
    path: str
    worktree_status: str
    merge_commit: str | None


class _ExecutionError(RuntimeError):
    pass


def candidate_node_ids(
    conn: Any, *, channel_id: str | None = None, workspace_id: str | None = None
) -> list[str]:
    stmt = (
        select(_NODE.c.id)
        .select_from(_NODE.join(_CANVAS, _CANVAS.c.id == _NODE.c.canvas_id))
        .where(
            _NODE.c.kind == CanvasNodeKind.SYSTEM.value,
            _NODE.c.system_status.in_(
                [SystemNodeStatus.IDLE.value, SystemNodeStatus.RUNNING.value]
            ),
        )
        .order_by(_NODE.c.id)
    )
    if channel_id is not None:
        stmt = stmt.where(_CANVAS.c.channel_id == channel_id)
    if workspace_id is not None:
        stmt = stmt.where(_CANVAS.c.workspace_id == workspace_id)
    return list(conn.execute(stmt).scalars())


def _channel_landing_in_progress(conn: Any, channel_id: str) -> bool:
    """本频道是否有 running 落地批（decomp/delta）——落地期系统节点认领抑制的判定源。

    并行审计 blocking 修复（阶段 4）：delta 步序先删后加、每步提交即广播,remove 步可把某既有
    idle merge/check 节点的上游删空;若此刻扫描器认领,空 steps 会 `_succeed_merge_node` 落
    **不可 retry 的 success 终态**,而后续 add 步才把替换边落上——J9 封死的「裸系统节点空成功」
    窗口经删除路径重开。判定读最新已提交态:任一 remove 步已提交 ⇒ 建批事务(更早提交)必可见,
    故对该窗口无过期读活口;批 :done 后抑制自然解除(hub 仅在 LANDING_COMPLETED 补扫描)。
    批 fail_closed 后抑制同样解除(无 settle 流,不能永久冻结频道),但截断前缀上的空 merge
    由 `_channel_fail_closed_unsettled` 守卫兜住(空成功转 retryable failed,M6 review F1)。
    decomp 批纯增本无此窗口,一并抑制无害且语义更简单。
    """
    _BATCH = models.tbl(models.LandingBatch)
    row = conn.execute(
        select(_BATCH.c.id).where(
            _BATCH.c.channel_id == channel_id,
            _BATCH.c.status == "running",
            _BATCH.c.kind.in_(["decomp", "delta"]),
        ).limit(1)
    ).first()
    return row is not None


# hub 扫描早退用公开别名（落地期每 node/edge 步事务各触发一次频道扫描，扫描级一查即返，
# 免逐节点锁+事务开销；语义与 prepare_dispatch 的认领抑制同源）。
channel_landing_in_progress = _channel_landing_in_progress


def _channel_fail_closed_unsettled(conn: Any, channel_id: str) -> bool:
    """本频道最近一个 decomp/delta 落地批是否停在 fail_closed（M6 review F1）。

    fail_closed 批可能只提交了 remove 前缀（步进事务），画布是截断前缀——此时被删空上游的
    idle merge 会被 reconcile/画布事件重扫认领并以空 steps 落**不可 retry 的 success**，
    绕过 hub「仅 LANDING_COMPLETED 补扫描」的事件面修复。以「最近批 == fail_closed」为
    未 settle 判定：其后任一批 :done 即自然解除；期间空 merge 转 retryable failed。"""
    _BATCH = models.tbl(models.LandingBatch)
    latest = conn.execute(
        select(_BATCH.c.status).where(
            _BATCH.c.channel_id == channel_id,
            _BATCH.c.kind.in_(["decomp", "delta"]),
        ).order_by(_BATCH.c.id.desc()).limit(1)
    ).scalar()
    return latest == LandingBatchStatus.FAIL_CLOSED.value


def prepare_dispatch(tx: Any, node_id: str) -> Dispatch | None:
    """锁内重读节点：idle 非 blocked 原子认领；running 恢复同一执行身份。

    落地期抑制：本频道有 running 落地批时不认领 idle 节点（见 _channel_landing_in_progress
    注记）；running 节点的恢复路径不受抑制（已在执行中的不半途而废）。"""
    context = _node_context(tx.conn, node_id)
    if context is None or context["kind"] != CanvasNodeKind.SYSTEM.value:
        return None
    status = context["system_status"]
    if status == SystemNodeStatus.IDLE.value:
        if _channel_landing_in_progress(tx.conn, context["channel_id"]):
            return None
        if node_id in canvas_service.blocked_node_ids(tx.conn, context["canvas_id"]):
            return None
        claimed = tx.conn.execute(
            update(_NODE)
            .where(
                _NODE.c.id == node_id,
                _NODE.c.system_status == SystemNodeStatus.IDLE.value,
            )
            .values(system_status=SystemNodeStatus.RUNNING.value)
        )
        if claimed.rowcount == 0:
            return None
        context["system_status"] = SystemNodeStatus.RUNNING.value
        try:
            _begin_attempt(tx, context)
        except _ExecutionError as exc:
            _fail_node(tx, context, action="system.trigger", reason=str(exc))
            return None
        _emit_node_updated(tx, context)
    elif status != SystemNodeStatus.RUNNING.value:
        return None

    try:
        if context["system_action"] == SystemAction.CHECK.value:
            return _check_dispatch(tx, context)
        if context["system_action"] == SystemAction.MERGE.value:
            steps = _attempt_merge_steps(tx.conn, context)
            pending = next(
                (step for step in steps if step.worktree_status != WorktreeStatus.MERGED.value),
                None,
            )
            if pending is None:
                if not steps and _channel_fail_closed_unsettled(
                    tx.conn, context["channel_id"]
                ):
                    # 截断前缀守卫（M6 review F1）：最近落地批 fail_closed 时，上游被删空的
                    # merge 空成功会落不可 retry 终态——转 failed（可 retry），交人类 settle。
                    _fail_node(
                        tx, context, action="system.merge",
                        reason="频道最近落地批 fail_closed，画布可能为截断前缀；"
                               "空 merge 不落成功，修复画布/重出增量后可 retry",
                    )
                    return None
                _succeed_merge_node(tx, context, empty=not steps)
                return None
            return MergeDispatch(
                node_id=node_id,
                channel_id=context["channel_id"],
                computer_id=pending.computer_id,
                data=WorktreeMergeData(
                    task_id=pending.task_id,
                    project_id=pending.project_id,
                    repo_path=pending.repo_path,
                    branch=pending.branch,
                    message=(
                        f"CoAgentia merge task #{pending.task_number} {pending.task_title} "
                        f"(task_id={pending.task_id}, node_id={node_id})"
                    ),
                ),
            )
        raise _ExecutionError(f"未知 system_action：{context['system_action']}")
    except _ExecutionError as exc:
        _fail_node(tx, context, action="system.dispatch", reason=str(exc))
        return None


def retry_failed_node(tx: Any, node_id: str) -> dict[str, Any]:
    context = _node_context(tx.conn, node_id)
    if context is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "画布节点不存在")
    if (
        context["kind"] != CanvasNodeKind.SYSTEM.value
        or context["system_status"] != SystemNodeStatus.FAILED.value
    ):
        raise ApiError(
            409,
            rest.ErrorCode.SYSTEM_NODE_NOT_RETRYABLE,
            "仅 failed 系统节点可重试",
            rule="W8",
            details={"status": context["system_status"]},
        )
    if node_id in canvas_service.blocked_node_ids(tx.conn, context["canvas_id"]):
        raise ApiError(
            409,
            rest.ErrorCode.SYSTEM_NODE_NOT_RETRYABLE,
            "系统节点上游尚未完成",
            rule="W8",
            details={"status": context["system_status"], "blocked": True},
        )
    claimed = tx.conn.execute(
        update(_NODE)
        .where(
            _NODE.c.id == node_id,
            _NODE.c.system_status == SystemNodeStatus.FAILED.value,
        )
        .values(system_status=SystemNodeStatus.RUNNING.value)
    )
    if claimed.rowcount == 0:
        current = tx.conn.execute(
            select(_NODE.c.system_status).where(_NODE.c.id == node_id)
        ).scalar_one()
        raise ApiError(
            409,
            rest.ErrorCode.SYSTEM_NODE_NOT_RETRYABLE,
            "系统节点已被并发重试",
            rule="W8",
            details={"status": current},
        )
    context["system_status"] = SystemNodeStatus.RUNNING.value
    try:
        _begin_attempt(tx, context)
    except _ExecutionError as exc:
        _fail_node(tx, context, action="system.retry", reason=str(exc))
        raise ApiError(
            409,
            rest.ErrorCode.SYSTEM_NODE_NOT_RETRYABLE,
            "系统节点当前无法重试",
            rule="W8",
            details={"status": SystemNodeStatus.FAILED.value, "reason": str(exc)},
        ) from exc
    _emit_node_updated(tx, context)
    return _fresh_node(tx.conn, node_id)


def complete_check(
    tx: Any, *, computer_id: str, data: CheckFinishedData
) -> tuple[bool, str | None]:
    """消费匹配当前 run_id 的 check.finished；终态/旧 run 重发不重复留痕。"""
    context = _node_context(tx.conn, data.node_id)
    if (
        context is None
        or context["kind"] != CanvasNodeKind.SYSTEM.value
        or context["system_action"] != SystemAction.CHECK.value
        or context["system_status"] != SystemNodeStatus.RUNNING.value
    ):
        return False, None
    active = _active_check_attempt(tx.conn, context)
    if (
        active is None
        or active.get("run_id") != data.run_id
        or active.get("computer_id") != computer_id
    ):
        return False, None

    terminal = SystemNodeStatus(data.status)
    tx.conn.execute(
        update(_NODE)
        .where(
            _NODE.c.id == data.node_id,
            _NODE.c.system_status == SystemNodeStatus.RUNNING.value,
        )
        .values(system_status=terminal.value)
    )
    context["system_status"] = terminal.value
    output = data.output_tail or "(无输出)"
    post_system_message(
        tx,
        workspace_id=context["workspace_id"],
        channel_id=context["channel_id"],
        thread_root_id=None,
        body=(
            f"check 节点完成\nnode_id: {data.node_id}\n"
            f"status={data.status} exit_code={data.exit_code}\n"
            f"输出尾：\n{output}"
        ),
    )
    _write_diagnostic(
        tx,
        context,
        action="check.finished",
        status=data.status,
        payload={
            "run_id": data.run_id,
            "exit_code": data.exit_code,
            "output_tail": data.output_tail,
        },
    )
    _emit_node_updated(tx, context)
    return (
        True,
        context["channel_id"] if terminal == SystemNodeStatus.SUCCESS else None,
    )


def pending_merge_node_ids(
    conn: Any,
    task_id: str,
    *,
    computer_id: str | None = None,
    branch: str | None = None,
    path: str | None = None,
) -> list[str]:
    """在 worktree.status 落库前定位当前正等待该 task 的 merge 节点。"""
    node_ids = list(
        conn.execute(
            select(_NODE.c.id)
            .where(
                _NODE.c.kind == CanvasNodeKind.SYSTEM.value,
                _NODE.c.system_action == SystemAction.MERGE.value,
                _NODE.c.system_status == SystemNodeStatus.RUNNING.value,
            )
            .order_by(_NODE.c.id)
        ).scalars()
    )
    matches: list[str] = []
    for node_id in node_ids:
        context = _node_context(conn, node_id)
        if context is None:
            continue
        try:
            pending = next(
                (
                    step
                    for step in _attempt_merge_steps(conn, context)
                    if step.worktree_status != WorktreeStatus.MERGED.value
                ),
                None,
            )
        except _ExecutionError:
            continue
        if (
            pending is not None
            and pending.task_id == task_id
            and (computer_id is None or pending.computer_id == computer_id)
            and (branch is None or pending.branch == branch)
            and (path is None or pending.path == path)
        ):
            matches.append(node_id)
    return matches


def apply_merge_result(
    tx: Any,
    *,
    node_ids: list[str],
    data: WorktreeStatusData,
    worktree_row: dict[str, Any],
) -> set[str]:
    """worktree.status 已持久后推进 merge；返回需继续 drive/解锁的频道集合。"""
    channels: set[str] = set()
    reconciled: set[str] = set()  # #9：同一 worktree_row 的 alias 广播整轮只做一次（菱形拓扑去重）
    for node_id in node_ids:
        context = _node_context(tx.conn, node_id)
        if context is None or context["system_status"] != SystemNodeStatus.RUNNING.value:
            continue
        if data.status == WorktreeStatus.MERGED.value:
            if not data.merge_commit:
                _fail_node(
                    tx,
                    context,
                    action="worktree.merge",
                    reason="merged 上报缺 merge_commit",
                    task_id=data.task_id,
                )
                continue
            _merge_step_succeeded(tx, context, data, worktree_row, reconciled)
            try:
                all_done = all(
                    step.worktree_status == WorktreeStatus.MERGED.value
                    for step in _attempt_merge_steps(tx.conn, context)
                )
            except _ExecutionError as exc:
                _fail_node(
                    tx,
                    context,
                    action="worktree.merge",
                    reason=str(exc),
                    task_id=data.task_id,
                )
                continue
            if all_done:
                _succeed_merge_node(tx, context)
            channels.add(context["channel_id"])
        elif data.status == WorktreeStatus.CONFLICTED.value:
            _merge_conflicted(tx, context, data, worktree_row)
    return channels


def fail_dispatch(
    tx: Any, *, node_id: str, action: str, reason: str, task_id: str | None = None
) -> None:
    context = _node_context(tx.conn, node_id)
    if context is None or context["system_status"] != SystemNodeStatus.RUNNING.value:
        return
    _fail_node(tx, context, action=action, reason=reason, task_id=task_id)


def _begin_attempt(tx: Any, context: dict[str, Any]) -> None:
    action = context["system_action"]
    payload: dict[str, Any] = {"attempt_id": new_ulid()}
    if action == SystemAction.CHECK.value:
        payload.update(_new_check_attempt(tx.conn, context))
        diag_action = "check.run"
    elif action == SystemAction.MERGE.value:
        payload["steps"] = [
            _merge_step_identity(step) for step in _merge_steps(tx.conn, context)
        ]
        diag_action = "worktree.merge"
    else:
        raise _ExecutionError(f"未知 system_action：{action}")
    _write_diagnostic(
        tx,
        context,
        action=diag_action,
        status=SystemNodeStatus.RUNNING.value,
        payload=payload,
    )


def _check_dispatch(tx: Any, context: dict[str, Any]) -> CheckDispatch:
    attempt = _active_check_attempt(tx.conn, context)
    if attempt is None:
        attempt = {"attempt_id": new_ulid(), **_new_check_attempt(tx.conn, context)}
        _write_diagnostic(
            tx,
            context,
            action="check.run",
            status=SystemNodeStatus.RUNNING.value,
            payload=attempt,
        )
    required = ("run_id", "project_id", "computer_id", "repo_path", "command")
    if not all(attempt.get(field) for field in required):
        raise _ExecutionError("check.run 运行身份不完整")
    return CheckDispatch(
        node_id=context["id"],
        channel_id=context["channel_id"],
        computer_id=str(attempt["computer_id"]),
        data=CheckRunData(
            run_id=str(attempt["run_id"]),
            node_id=context["id"],
            project_id=str(attempt["project_id"]),
            repo_path=str(attempt["repo_path"]),
            command=str(attempt["command"]),
        ),
    )


def _new_check_attempt(conn: Any, context: dict[str, Any]) -> dict[str, str]:
    project = _resolve_check_project(conn, context)
    command = (context.get("command") or "").strip()
    if not command:
        raise _ExecutionError("check 系统节点缺 command")
    return {
        "run_id": new_ulid(),
        "project_id": str(project["id"]),
        "computer_id": str(project["computer_id"]),
        "repo_path": str(project["repo_path"]),
        "command": command,
    }


def _resolve_check_project(conn: Any, context: dict[str, Any]) -> dict[str, Any]:
    ancestor_ids = _ancestor_order(conn, context["canvas_id"], context["id"])
    project_ids = set(
        conn.execute(
            select(_TASK.c.project_id)
            .select_from(_NODE.join(_TASK, _TASK.c.id == _NODE.c.task_id))
            .where(
                _NODE.c.id.in_(ancestor_ids),
                _TASK.c.writes_code.is_(True),
                _TASK.c.project_id.is_not(None),
            )
        ).scalars()
    ) if ancestor_ids else set()
    if not project_ids:
        project_ids = set(
            conn.execute(
                select(_CHANNEL_PROJECT.c.project_id).where(
                    _CHANNEL_PROJECT.c.channel_id == context["channel_id"]
                )
            ).scalars()
        )
    if len(project_ids) != 1:
        raise _ExecutionError("check 节点无法唯一确定 Project")
    project = conn.execute(
        select(_PROJECT).where(_PROJECT.c.id == next(iter(project_ids)))
    ).mappings().first()
    if project is None:
        raise _ExecutionError("check 节点 Project 不存在")
    return dict(project)


def _merge_steps(conn: Any, context: dict[str, Any]) -> list[_MergeStep]:
    order = _ancestor_order(conn, context["canvas_id"], context["id"])
    if not order:
        return []
    rows = conn.execute(
        select(
            _NODE.c.id.label("node_id"),
            _TASK.c.id.label("task_id"),
            _TASK.c.number.label("task_number"),
            _TASK.c.title.label("task_title"),
            _TASK.c.project_id,
            _TASK.c.writes_code,
            _PROJECT.c.computer_id,
            _PROJECT.c.repo_path,
            _WORKTREE.c.branch,
            _WORKTREE.c.path,
            _WORKTREE.c.status.label("worktree_status"),
            _WORKTREE.c.merge_commit,
        )
        .select_from(
            _NODE.join(_TASK, _TASK.c.id == _NODE.c.task_id)
            .outerjoin(_PROJECT, _PROJECT.c.id == _TASK.c.project_id)
            .outerjoin(_WORKTREE, _WORKTREE.c.task_id == _TASK.c.id)
        )
        .where(_NODE.c.id.in_(order), _NODE.c.kind == CanvasNodeKind.AGENT.value)
    ).mappings()
    by_node = {row["node_id"]: dict(row) for row in rows}
    steps: list[_MergeStep] = []
    seen_trees: set[tuple[str, str, str]] = set()
    for node_id in order:
        row = by_node.get(node_id)
        if row is None or not row["writes_code"]:
            continue
        if not all(
            row.get(field)
            for field in ("project_id", "computer_id", "repo_path", "branch", "path")
        ):
            raise _ExecutionError(f"代码任务 {row['task_id']} 缺可合并 worktree")
        tree_key = (row["project_id"], row["path"], row["branch"])
        if tree_key in seen_trees:
            continue  # 冲突任务复用原物理树，只合并同一分支一次。
        seen_trees.add(tree_key)
        if (
            row["worktree_status"] == WorktreeStatus.MERGED.value
            and not row["merge_commit"]
        ):
            raise _ExecutionError(f"代码任务 {row['task_id']} merged 状态缺 merge_commit")
        steps.append(
            _MergeStep(
                node_id=node_id,
                task_id=row["task_id"],
                task_number=int(row["task_number"]),
                task_title=row["task_title"],
                project_id=row["project_id"],
                computer_id=row["computer_id"],
                repo_path=row["repo_path"],
                branch=row["branch"],
                path=row["path"],
                worktree_status=row["worktree_status"],
                merge_commit=row["merge_commit"],
            )
        )
    return steps


def _merge_step_identity(step: _MergeStep) -> dict[str, Any]:
    return {
        "node_id": step.node_id,
        "task_id": step.task_id,
        "task_number": step.task_number,
        "task_title": step.task_title,
        "project_id": step.project_id,
        "computer_id": step.computer_id,
        "repo_path": step.repo_path,
        "branch": step.branch,
        "path": step.path,
    }


def _attempt_merge_steps(conn: Any, context: dict[str, Any]) -> list[_MergeStep]:
    """从 running attempt 恢复不可变步骤，仅从 worktrees 刷新执行终态。"""
    attempt = _active_merge_attempt(conn, context)
    raw_steps = attempt.get("steps") if attempt is not None else None
    if not isinstance(raw_steps, list):
        raise _ExecutionError("worktree.merge 运行身份不完整")
    task_ids = [
        step.get("task_id") for step in raw_steps if isinstance(step, dict)
    ]
    current = {
        row["task_id"]: dict(row)
        for row in conn.execute(
            select(
                _WORKTREE.c.task_id,
                _WORKTREE.c.status.label("worktree_status"),
                _WORKTREE.c.merge_commit,
            ).where(_WORKTREE.c.task_id.in_(task_ids))
        ).mappings()
    }
    required = (
        "node_id",
        "task_id",
        "task_number",
        "task_title",
        "project_id",
        "computer_id",
        "repo_path",
        "branch",
        "path",
    )
    steps: list[_MergeStep] = []
    for raw in raw_steps:
        if not isinstance(raw, dict) or not all(raw.get(field) for field in required):
            raise _ExecutionError("worktree.merge 步骤身份不完整")
        worktree = current.get(raw["task_id"])
        if worktree is None:
            raise _ExecutionError(f"代码任务 {raw['task_id']} 缺可合并 worktree")
        if (
            worktree["worktree_status"] == WorktreeStatus.MERGED.value
            and not worktree["merge_commit"]
        ):
            raise _ExecutionError(
                f"代码任务 {raw['task_id']} merged 状态缺 merge_commit"
            )
        steps.append(
            _MergeStep(
                node_id=str(raw["node_id"]),
                task_id=str(raw["task_id"]),
                task_number=int(raw["task_number"]),
                task_title=str(raw["task_title"]),
                project_id=str(raw["project_id"]),
                computer_id=str(raw["computer_id"]),
                repo_path=str(raw["repo_path"]),
                branch=str(raw["branch"]),
                path=str(raw["path"]),
                worktree_status=str(worktree["worktree_status"]),
                merge_commit=worktree["merge_commit"],
            )
        )
    return steps


def _ancestor_order(conn: Any, canvas_id: str, target_id: str) -> list[str]:
    edges = canvas_service.fetch_edges(conn, canvas_id)
    predecessors: dict[str, set[str]] = {}
    for edge in edges:
        predecessors.setdefault(edge["to_node_id"], set()).add(edge["from_node_id"])
    ancestors: set[str] = set()
    stack = list(predecessors.get(target_id, set()))
    while stack:
        current = stack.pop()
        if current in ancestors:
            continue
        ancestors.add(current)
        stack.extend(predecessors.get(current, set()))
    if not ancestors:
        return []

    indegree = {node_id: 0 for node_id in ancestors}
    followers: dict[str, list[str]] = {node_id: [] for node_id in ancestors}
    for edge in edges:
        source, target = edge["from_node_id"], edge["to_node_id"]
        if source in ancestors and target in ancestors:
            followers[source].append(target)
            indegree[target] += 1
    ready = [node_id for node_id, degree in indegree.items() if degree == 0]
    heapq.heapify(ready)
    order: list[str] = []
    while ready:
        node_id = heapq.heappop(ready)
        order.append(node_id)
        for target in sorted(followers[node_id]):
            indegree[target] -= 1
            if indegree[target] == 0:
                heapq.heappush(ready, target)
    if len(order) != len(ancestors):
        raise _ExecutionError("merge 上游画布不是 DAG")
    return order


def _active_check_attempt(conn: Any, context: dict[str, Any]) -> dict[str, Any] | None:
    row = conn.execute(
        select(_DIAG.c.payload)
        .where(
            _DIAG.c.workspace_id == context["workspace_id"],
            _DIAG.c.type == _DIAGNOSTIC_TYPE,
            func.json_extract(_DIAG.c.payload, "$.node_id") == context["id"],
            func.json_extract(_DIAG.c.payload, "$.action") == "check.run",
        )
        .order_by(_DIAG.c.seq.desc())
        .limit(1)
    ).scalar_one_or_none()
    return dict(row) if isinstance(row, dict) else None


def _active_merge_attempt(conn: Any, context: dict[str, Any]) -> dict[str, Any] | None:
    row = conn.execute(
        select(_DIAG.c.payload)
        .where(
            _DIAG.c.workspace_id == context["workspace_id"],
            _DIAG.c.type == _DIAGNOSTIC_TYPE,
            func.json_extract(_DIAG.c.payload, "$.node_id") == context["id"],
            func.json_extract(_DIAG.c.payload, "$.action") == "worktree.merge",
            func.json_extract(_DIAG.c.payload, "$.status")
            == SystemNodeStatus.RUNNING.value,
        )
        .order_by(_DIAG.c.seq.desc())
        .limit(1)
    ).scalar_one_or_none()
    return dict(row) if isinstance(row, dict) else None


def _merge_step_succeeded(
    tx: Any,
    context: dict[str, Any],
    data: WorktreeStatusData,
    worktree_row: dict[str, Any],
    reconciled: set[str] | None = None,
) -> None:
    # #9：alias 更新 + WORKTREE_UPDATED 广播按 worktree_row.id 整轮去重（菱形拓扑下 N 个 merge 节点
    # 对同一 worktree_row 只广播一次）；进展消息/诊断按 node_id 区分，属设计 per-node，不去重。
    if reconciled is None or worktree_row["id"] not in reconciled:
        if reconciled is not None:
            reconciled.add(worktree_row["id"])
        timestamp = worktree_row.get("merged_at") or now_iso()
        aliases = list(
            tx.conn.execute(
                select(_WORKTREE).where(
                    _WORKTREE.c.id != worktree_row["id"],
                    _WORKTREE.c.project_id == worktree_row["project_id"],
                    _WORKTREE.c.path == worktree_row["path"],
                    _WORKTREE.c.branch == worktree_row["branch"],
                )
            ).mappings()
        )
        for alias in aliases:
            tx.conn.execute(
                update(_WORKTREE)
                .where(_WORKTREE.c.id == alias["id"])
                .values(
                    status=WorktreeStatus.MERGED.value,
                    merge_commit=data.merge_commit,
                    merged_at=alias["merged_at"] or timestamp,
                )
            )
            fresh = tx.conn.execute(
                select(_WORKTREE).where(_WORKTREE.c.id == alias["id"])
            ).mappings().one()
            tx.emit(
                EventType.WORKTREE_UPDATED,
                context["channel_id"],
                {"worktree": worktree_public(dict(fresh))},
            )
    post_system_message(
        tx,
        workspace_id=context["workspace_id"],
        channel_id=context["channel_id"],
        thread_root_id=None,
        body=(
            f"merge 节点进展\nnode_id: {context['id']}\ntask_id={data.task_id}\n"
            f"merge_commit={data.merge_commit}"
        ),
    )
    _write_diagnostic(
        tx,
        context,
        action="worktree.merge",
        status="merged",
        task_id=data.task_id,
        payload={"merge_commit": data.merge_commit},
    )


def _succeed_merge_node(tx: Any, context: dict[str, Any], *, empty: bool = False) -> None:
    changed = tx.conn.execute(
        update(_NODE)
        .where(
            _NODE.c.id == context["id"],
            _NODE.c.system_status == SystemNodeStatus.RUNNING.value,
        )
        .values(system_status=SystemNodeStatus.SUCCESS.value)
    )
    if changed.rowcount == 0:
        return
    context["system_status"] = SystemNodeStatus.SUCCESS.value
    suffix = "（无待合并 worktree）" if empty else ""
    post_system_message(
        tx,
        workspace_id=context["workspace_id"],
        channel_id=context["channel_id"],
        thread_root_id=None,
        body=f"merge 节点成功{suffix}\nnode_id: {context['id']}",
    )
    _write_diagnostic(
        tx,
        context,
        action="worktree.merge",
        status=SystemNodeStatus.SUCCESS.value,
    )
    _emit_node_updated(tx, context)


def _merge_conflicted(
    tx: Any,
    context: dict[str, Any],
    data: WorktreeStatusData,
    worktree_row: dict[str, Any],
) -> None:
    tx.conn.execute(
        update(_NODE)
        .where(
            _NODE.c.id == context["id"],
            _NODE.c.system_status == SystemNodeStatus.RUNNING.value,
        )
        .values(system_status=SystemNodeStatus.FAILED.value)
    )
    context["system_status"] = SystemNodeStatus.FAILED.value
    files = sorted(dict.fromkeys(data.conflict_files or []))
    _write_diagnostic(
        tx,
        context,
        action="worktree.merge",
        status="conflicted",
        task_id=data.task_id,
        payload={"conflict_files": files},
    )
    post_system_message(
        tx,
        workspace_id=context["workspace_id"],
        channel_id=context["channel_id"],
        thread_root_id=None,
        body=(
            f"merge 节点冲突\nnode_id: {context['id']}\ntask_id={data.task_id}\n"
            + _conflict_file_block(files)
        ),
    )
    _create_conflict_task(tx, context, data, worktree_row, files)
    _emit_node_updated(tx, context)


def _create_conflict_task(
    tx: Any,
    context: dict[str, Any],
    data: WorktreeStatusData,
    worktree_row: dict[str, Any],
    files: list[str],
) -> None:
    # #4 幂等：若本 merge 节点已有未终态的同树「解决冲突」派回任务，复用不重复建（防同一冲突报告
    # 重复处理累积重复任务/节点/worktree 行）；二次真冲突（前次已 done）无此行 → 正常建新一轮。
    existing = tx.conn.execute(
        select(_TASK.c.id)
        .select_from(
            _EDGE.join(_NODE, _NODE.c.id == _EDGE.c.from_node_id)
            .join(_TASK, _TASK.c.id == _NODE.c.task_id)
            .join(_WORKTREE, _WORKTREE.c.task_id == _TASK.c.id)
        )
        .where(
            _EDGE.c.canvas_id == context["canvas_id"],
            _EDGE.c.to_node_id == context["id"],
            _NODE.c.kind == CanvasNodeKind.AGENT.value,
            _TASK.c.id != data.task_id,
            _TASK.c.writes_code.is_(True),
            _TASK.c.status.notin_([TaskStatus.DONE.value, TaskStatus.CLOSED.value]),
            _WORKTREE.c.project_id == worktree_row["project_id"],
            _WORKTREE.c.path == worktree_row["path"],
            _WORKTREE.c.branch == worktree_row["branch"],
            _WORKTREE.c.status == WorktreeStatus.ACTIVE.value,
        )
        .limit(1)
    ).first()
    if existing is not None:
        return
    original = tx.conn.execute(
        select(_TASK).where(_TASK.c.id == data.task_id)
    ).mappings().one()
    original = dict(original)
    owner_id = original["owner_member_id"]
    owner_prefix = ""
    if owner_id is not None:
        owner_name = tx.conn.execute(
            select(_MEMBER.c.name).where(_MEMBER.c.id == owner_id)
        ).scalar_one_or_none()
        if owner_name:
            owner_prefix = f"@{owner_name} "
    body = (
        f"{owner_prefix}解决冲突：merge 节点\nnode_id: {context['id']}\n"
        f"原任务：#{original['number']} {original['title']} (task_id={data.task_id})\n"
        f"{_conflict_file_block(files)}\n"
        "双方 Diff 引用：\n"
        f"- 任务分支 `{worktree_row['branch']}`\n"
        "- 主干 `HEAD`\n"
        f"- 对比 `GET /api/tasks/{data.task_id}/diff`"
    )
    anchor_id = post_system_message(
        tx,
        workspace_id=original["workspace_id"],
        channel_id=original["channel_id"],
        thread_root_id=None,
        mention_member_ids=[owner_id] if owner_id is not None else [],
        card_kind=CardKind.MERGE_CONFLICT,
        body=body,
    )
    task = tasks_service.create_task(
        tx,
        workspace_id=original["workspace_id"],
        channel_id=original["channel_id"],
        root_message_id=anchor_id,
        created_by=original["created_by_member_id"],
        title="解决冲突",
        source_body=body,
        level=TaskLevel.L2,
        project_id=original["project_id"],
        writes_code=True,
    )
    if owner_id is not None:
        tx.conn.execute(
            update(_TASK).where(_TASK.c.id == task["id"]).values(owner_member_id=owner_id)
        )
        tasks_service.write_event(
            tx.conn,
            task["id"],
            TaskEventKind.ASSIGN,
            actor=None,
            owner=owner_id,
        )
        task = tasks_service.fetch_task(tx.conn, task["id"])

    tx.conn.execute(
        insert(_WORKTREE).values(
            id=new_ulid(),
            workspace_id=original["workspace_id"],
            project_id=worktree_row["project_id"],
            task_id=task["id"],
            branch=worktree_row["branch"],
            path=worktree_row["path"],
            status=WorktreeStatus.ACTIVE.value,
            merge_commit=None,
            created_at=now_iso(),
            merged_at=None,
            cleaned_at=None,
        )
    )
    conflict_node = canvas_service.insert_node(
        tx.conn,
        canvas_id=context["canvas_id"],
        kind=CanvasNodeKind.AGENT,
        task_id=task["id"],
        is_summary=False,
        system_action=None,
        command=None,
        system_status=None,
        pos_x=float(context["pos_x"]) - 180,
        pos_y=float(context["pos_y"]) + 120,
        created_at=now_iso(),
    )
    edge_id = new_ulid()
    tx.conn.execute(
        insert(_EDGE).values(
            id=edge_id,
            canvas_id=context["canvas_id"],
            from_node_id=conflict_node["id"],
            to_node_id=context["id"],
        )
    )
    edge = tx.conn.execute(select(_EDGE).where(_EDGE.c.id == edge_id)).mappings().one()

    tasks_service.emit_task_created(tx, task)
    tx.emit(
        EventType.CANVAS_NODE_ADDED,
        context["channel_id"],
        {"node": canvas_node_public(conflict_node)},
    )
    tx.emit(
        EventType.CANVAS_EDGE_ADDED,
        context["channel_id"],
        {"edge": canvas_edge_public(dict(edge))},
    )
    version, hash_, changed = canvas_service.advance_baseline(tx, context["canvas_id"])
    if changed:
        tx.emit(
            EventType.CANVAS_BASELINE_ADVANCED,
            context["channel_id"],
            {
                "canvas_id": context["canvas_id"],
                "baseline_version": version,
                "baseline_hash": hash_,
            },
        )


def _fail_node(
    tx: Any,
    context: dict[str, Any],
    *,
    action: str,
    reason: str,
    task_id: str | None = None,
) -> None:
    tx.conn.execute(
        update(_NODE)
        .where(_NODE.c.id == context["id"])
        .values(system_status=SystemNodeStatus.FAILED.value)
    )
    context["system_status"] = SystemNodeStatus.FAILED.value
    post_system_message(
        tx,
        workspace_id=context["workspace_id"],
        channel_id=context["channel_id"],
        thread_root_id=None,
        body=f"系统节点失败\nnode_id: {context['id']}\n{reason}",
    )
    _write_diagnostic(
        tx,
        context,
        action=action,
        status=SystemNodeStatus.FAILED.value,
        task_id=task_id,
        payload={"reason": reason},
    )
    _emit_node_updated(tx, context)


def _write_diagnostic(
    tx: Any,
    context: dict[str, Any],
    *,
    action: str,
    status: str,
    task_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    tx.conn.execute(
        insert(_DIAG).values(
            workspace_id=context["workspace_id"],
            agent_member_id=None,
            type=_DIAGNOSTIC_TYPE,
            channel_id=context["channel_id"],
            task_id=task_id,
            batch_id=None,
            payload={
                "action": action,
                "node_id": context["id"],
                "status": status,
                **(payload or {}),
            },
            created_at=now_iso(),
        )
    )


def _emit_node_updated(tx: Any, context: dict[str, Any]) -> None:
    tx.emit(
        EventType.CANVAS_NODE_UPDATED,
        context["channel_id"],
        {"node": canvas_node_public(_fresh_node(tx.conn, context["id"]))},
    )


def _fresh_node(conn: Any, node_id: str) -> dict[str, Any]:
    return dict(conn.execute(select(_NODE).where(_NODE.c.id == node_id)).mappings().one())


def _node_context(conn: Any, node_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        select(
            _NODE,
            _CANVAS.c.workspace_id.label("workspace_id"),
            _CANVAS.c.channel_id.label("channel_id"),
        )
        .select_from(_NODE.join(_CANVAS, _CANVAS.c.id == _NODE.c.canvas_id))
        .where(_NODE.c.id == node_id)
    ).mappings().first()
    return dict(row) if row is not None else None


def _conflict_file_block(files: list[str]) -> str:
    listed = files or ["(daemon 未返回明细)"]
    return "冲突文件:\n" + "\n".join(f"- {path}" for path in listed)


__all__ = [
    "CheckDispatch",
    "Dispatch",
    "MergeDispatch",
    "apply_merge_result",
    "candidate_node_ids",
    "complete_check",
    "fail_dispatch",
    "pending_merge_node_ids",
    "prepare_dispatch",
    "retry_failed_node",
]
