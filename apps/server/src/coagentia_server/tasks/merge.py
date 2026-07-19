"""任务级 merge 域（DEDAG 批，契约 B v1.6 §14）：校验、下发计划、完成/冲突处置。

前身 = system_nodes/service.py 的 merge 执行机制（M6a）；DAG 序/节点认领随画布退役，
单任务 `git merge --no-ff`、merge_commit 持久、冲突自动建任务派回、同树 alias 收敛
四语义原样保留。执行身份 = hub._merge_pending（单进程内存）+ diagnostic 留痕；daemon
帧（WorktreeMergeData / worktree.status 上报）契约 D 零修订。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from coagentia_contracts import rest
from coagentia_contracts.daemon import WorktreeMergeData, WorktreeStatusData
from coagentia_contracts.enums import (
    CardKind,
    TaskEventKind,
    TaskLevel,
    TaskStatus,
    WorktreeStatus,
)
from coagentia_contracts.ws import EventType
from sqlalchemy import insert, select, update

from coagentia_server.api import ApiError
from coagentia_server.db import models
from coagentia_server.ledger.service import new_ulid, now_iso
from coagentia_server.messages.service import post_system_message
from coagentia_server.routes.serialize import worktree_public
from coagentia_server.tasks import service as tasks_service

_TASK = models.tbl(models.Task)
_PROJECT = models.tbl(models.Project)
_WORKTREE = models.tbl(models.Worktree)
_DIAG = models.tbl(models.DiagnosticEvent)
_MEMBER = models.tbl(models.Member)

# 运行身份留痕沿 system_nodes 先例（diagnostic_events type='agent.command'，不加状态列）。
_DIAGNOSTIC_TYPE = "agent.command"
_MERGE_ACTION = "task.merge"


@dataclass(frozen=True, slots=True)
class TaskMergePlan:
    task_id: str
    task_number: int
    task_title: str
    workspace_id: str
    channel_id: str
    project_id: str
    computer_id: str
    repo_path: str
    branch: str
    path: str
    already_merged: bool
    data: WorktreeMergeData


def prepare_merge(tx: Any, *, task_id: str) -> TaskMergePlan:
    """校验并产出合并计划（B v1.6 §14 前置）。

    - 非 writes_code → 422 VALIDATION_FAILED；status != done → 422 TASK_TRANSITION_INVALID。
    - worktree 行 ∈ {active, conflicted} 可合并（conflicted 重触发 = 冲突解决后的 retry 路径，
      沿 M6a「仅 failed 可 retry」语义的任务级对应）；merged → already_merged=True（幂等
      202，不再下发）；缺行/cleaned → 409 VALIDATION 语义走 422。
    """
    row = (
        tx.conn.execute(
            select(
                _TASK.c.id,
                _TASK.c.number,
                _TASK.c.title,
                _TASK.c.workspace_id,
                _TASK.c.channel_id,
                _TASK.c.status,
                _TASK.c.writes_code,
                _TASK.c.project_id,
            ).where(_TASK.c.id == task_id)
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "任务不存在")
    if not row["writes_code"] or row["project_id"] is None:
        raise ApiError(
            422, rest.ErrorCode.VALIDATION_FAILED, "仅 writes_code 且绑定 Project 的任务可合并"
        )
    if row["status"] != TaskStatus.DONE.value:
        raise ApiError(
            422,
            rest.ErrorCode.TASK_TRANSITION_INVALID,
            "任务未 done，不可合并",
            details={"from": row["status"], "to": "merge", "allowed": ["done"]},
        )
    worktree = (
        tx.conn.execute(select(_WORKTREE).where(_WORKTREE.c.task_id == task_id))
        .mappings()
        .first()
    )
    if worktree is None or worktree["status"] == WorktreeStatus.CLEANED.value:
        raise ApiError(
            422, rest.ErrorCode.VALIDATION_FAILED, "任务无可合并 worktree（缺行或已清理）"
        )
    already = worktree["status"] == WorktreeStatus.MERGED.value
    if already and not worktree["merge_commit"]:
        raise ApiError(
            422, rest.ErrorCode.VALIDATION_FAILED, "worktree merged 状态缺 merge_commit"
        )
    if not worktree["branch"] or not worktree["path"]:
        raise ApiError(422, rest.ErrorCode.VALIDATION_FAILED, "worktree 行缺 branch/path")
    project = (
        tx.conn.execute(select(_PROJECT).where(_PROJECT.c.id == row["project_id"]))
        .mappings()
        .first()
    )
    if project is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "Project 不存在")
    return TaskMergePlan(
        task_id=task_id,
        task_number=int(row["number"]),
        task_title=row["title"],
        workspace_id=row["workspace_id"],
        channel_id=row["channel_id"],
        project_id=row["project_id"],
        computer_id=project["computer_id"],
        repo_path=project["repo_path"],
        branch=worktree["branch"],
        path=worktree["path"],
        already_merged=already,
        data=WorktreeMergeData(
            task_id=task_id,
            project_id=row["project_id"],
            repo_path=project["repo_path"],
            branch=worktree["branch"],
            message=(
                f"CoAgentia merge task #{row['number']} {row['title']} (task_id={task_id})"
            ),
        ),
    )


def note_merge_started(tx: Any, plan: TaskMergePlan) -> None:
    """202 受理时留痕（同事务）：diagnostic running 行。下发本体在 tx.after_commit（铁律 4）。"""
    _write_diagnostic(
        tx,
        workspace_id=plan.workspace_id,
        channel_id=plan.channel_id,
        task_id=plan.task_id,
        status="running",
        payload={"branch": plan.branch, "project_id": plan.project_id},
    )


def fail_merge(tx: Any, *, task_id: str, reason: str) -> None:
    """合并失败留痕：diagnostic failed + 频道系统消息（人工重触发即 retry）。"""
    row = (
        tx.conn.execute(
            select(_TASK.c.workspace_id, _TASK.c.channel_id, _TASK.c.number).where(
                _TASK.c.id == task_id
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return
    _write_diagnostic(
        tx,
        workspace_id=row["workspace_id"],
        channel_id=row["channel_id"],
        task_id=task_id,
        status="failed",
        payload={"reason": reason},
    )
    post_system_message(
        tx,
        workspace_id=row["workspace_id"],
        channel_id=row["channel_id"],
        thread_root_id=None,
        body=f"任务 #{row['number']} 合并失败\ntask_id: {task_id}\n{reason}\n可修复后重新触发合并",
    )


def apply_merge_report(
    tx: Any,
    *,
    data: WorktreeStatusData,
    worktree_row: dict[str, Any],
    workspace_id: str,
    channel_id: str,
) -> None:
    """daemon worktree.status（merged/conflicted）持久后的任务级完成处置（hub writer 线程调用）。"""
    if data.status == WorktreeStatus.MERGED.value:
        _sync_alias_rows(tx, worktree_row=worktree_row, channel_id=channel_id)
        task_no = _task_number(tx, data.task_id)
        post_system_message(
            tx,
            workspace_id=workspace_id,
            channel_id=channel_id,
            thread_root_id=None,
            body=(
                f"✅ 任务 #{task_no} 已合并主干\ntask_id: {data.task_id}\n"
                f"merge_commit: {data.merge_commit}"
            ),
        )
        _write_diagnostic(
            tx,
            workspace_id=workspace_id,
            channel_id=channel_id,
            task_id=data.task_id,
            status="merged",
            payload={"merge_commit": data.merge_commit},
        )
        return
    if data.status == WorktreeStatus.CONFLICTED.value:
        files = sorted(dict.fromkeys(data.conflict_files or []))
        _write_diagnostic(
            tx,
            workspace_id=workspace_id,
            channel_id=channel_id,
            task_id=data.task_id,
            status="conflicted",
            payload={"conflict_files": files},
        )
        post_system_message(
            tx,
            workspace_id=workspace_id,
            channel_id=channel_id,
            thread_root_id=None,
            body=(
                f"❌ 任务合并冲突\ntask_id: {data.task_id}\n" + _conflict_file_block(files)
            ),
        )
        _create_conflict_task(tx, data=data, worktree_row=worktree_row)


def _sync_alias_rows(tx: Any, *, worktree_row: dict[str, Any], channel_id: str) -> None:
    """同物理树（project/path/branch）的冲突任务逻辑行随 merged 收敛（M6a alias 语义）。"""
    timestamp = worktree_row.get("merged_at") or now_iso()
    aliases = list(
        tx.conn.execute(
            select(_WORKTREE).where(
                _WORKTREE.c.id != worktree_row["id"],
                _WORKTREE.c.project_id == worktree_row["project_id"],
                _WORKTREE.c.path == worktree_row["path"],
                _WORKTREE.c.branch == worktree_row["branch"],
                _WORKTREE.c.status != WorktreeStatus.CLEANED.value,
            )
        ).mappings()
    )
    for alias in aliases:
        tx.conn.execute(
            update(_WORKTREE)
            .where(_WORKTREE.c.id == alias["id"])
            .values(
                status=WorktreeStatus.MERGED.value,
                merge_commit=worktree_row["merge_commit"],
                merged_at=alias["merged_at"] or timestamp,
            )
        )
        fresh = (
            tx.conn.execute(select(_WORKTREE).where(_WORKTREE.c.id == alias["id"]))
            .mappings()
            .one()
        )
        tx.emit(
            EventType.WORKTREE_UPDATED,
            channel_id,
            {"worktree": worktree_public(dict(fresh))},
        )


def _create_conflict_task(
    tx: Any, *, data: WorktreeStatusData, worktree_row: dict[str, Any]
) -> None:
    """冲突自动建任务派回（M6a 语义，画布面退役）。

    幂等：同物理树已有未终态「解决冲突」writes_code 任务（active worktree alias）→ 复用不重建；
    二次真冲突（前次已 done）无此行 → 正常建新一轮。"""
    files = sorted(dict.fromkeys(data.conflict_files or []))
    existing = tx.conn.execute(
        select(_TASK.c.id)
        .select_from(_TASK.join(_WORKTREE, _WORKTREE.c.task_id == _TASK.c.id))
        .where(
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
    original = (
        tx.conn.execute(select(_TASK).where(_TASK.c.id == data.task_id)).mappings().one()
    )
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
        f"{owner_prefix}解决冲突：任务合并\n"
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
    tasks_service.emit_task_created(tx, task)


def _task_number(tx: Any, task_id: str) -> int:
    number = tx.conn.execute(
        select(_TASK.c.number).where(_TASK.c.id == task_id)
    ).scalar_one_or_none()
    return int(number) if number is not None else 0


def _write_diagnostic(
    tx: Any,
    *,
    workspace_id: str,
    channel_id: str,
    task_id: str,
    status: str,
    payload: dict[str, Any] | None = None,
) -> None:
    tx.conn.execute(
        insert(_DIAG).values(
            workspace_id=workspace_id,
            agent_member_id=None,
            type=_DIAGNOSTIC_TYPE,
            channel_id=channel_id,
            task_id=task_id,
            batch_id=None,
            payload={
                "action": _MERGE_ACTION,
                "task_id": task_id,
                "status": status,
                **(payload or {}),
            },
            created_at=now_iso(),
        )
    )


def _conflict_file_block(files: list[str]) -> str:
    listed = files or ["(daemon 未返回明细)"]
    return "冲突文件:\n" + "\n".join(f"- {path}" for path in listed)


__all__ = [
    "TaskMergePlan",
    "apply_merge_report",
    "fail_merge",
    "note_merge_started",
    "prepare_merge",
]
