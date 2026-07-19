"""worktree 生命周期的 server 侧事实推导（契约 B §12.6 / D §4.4 #5）。

daemon 只执行 git；本模块从任务、Project 与 worktrees DB 事实推导 ensure/cleanup 计划，
并消费 daemon 上报的状态。异步帧发送与 WS 广播仍由 computers.hub 负责。

DEDAG 批（2026-07-18）：画布退役后 worktree 派生改纯任务驱动——writes_code=true + project
绑定 + 非终态即派生；gating（blocked）判定随图退役。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from coagentia_contracts.daemon import WorktreeStatusData
from coagentia_contracts.enums import (
    TaskStatus,
    WorktreeStatus,
)
from sqlalchemy import insert, or_, select, update
from sqlalchemy.engine import Connection

from coagentia_server.db import models
from coagentia_server.ledger.service import new_ulid, now_iso

_TASK = models.tbl(models.Task)
_PROJECT = models.tbl(models.Project)
_WORKTREE = models.tbl(models.Worktree)
_MENTION = models.tbl(models.MessageMention)

TERMINAL_TASK_STATUSES = {TaskStatus.DONE.value, TaskStatus.CLOSED.value}
_TERMINAL_TASK_STATUSES = TERMINAL_TASK_STATUSES  # 模块内旧名沿用
_DIRECTORY_STATUSES = {WorktreeStatus.ACTIVE.value, WorktreeStatus.CONFLICTED.value}


@dataclass(frozen=True)
class EnsurePlan:
    task_id: str
    project_id: str
    computer_id: str
    repo_path: str
    branch: str


@dataclass(frozen=True)
class CleanupPlan:
    task_id: str
    computer_id: str


@dataclass(frozen=True)
class DirectoryContext:
    task_id: str
    channel_id: str
    root_message_id: str
    task_number: int
    task_title: str
    path: str


@dataclass(frozen=True)
class StatusResult:
    row: dict[str, Any]
    alias_rows: tuple[dict[str, Any], ...]
    became_active: bool
    workspace_id: str
    channel_id: str
    root_message_id: str
    owner_member_id: str | None
    task_status: str


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def ensure_plans(
    conn: Connection,
    *,
    computer_id: str | None = None,
    channel_id: str | None = None,
    task_id: str | None = None,
) -> list[EnsurePlan]:
    """推导缺树的激活 writes_code 任务；只读/终态不派生（DEDAG：纯任务驱动，无画布门）。

    cleaned 行视同无树（M6 review F3）：closed→todo reopen 的任务若树已按 keep_days 清理，
    只按 `_WORKTREE.task_id IS NULL` 推导会永远跳过它——revalidation 只扫 active、投递门又把
    cleaned 计入缺目录，该任务的投递被永久扣住且无恢复面。cleaned 行任务重新进 ensure 派生，
    daemon 幂等重建后 apply_status 把行 upsert 回 active。"""
    source = _TASK.join(_PROJECT, _PROJECT.c.id == _TASK.c.project_id).outerjoin(
        _WORKTREE, _WORKTREE.c.task_id == _TASK.c.id
    )
    stmt = (
        select(
            _TASK.c.id.label("task_id"),
            _TASK.c.project_id,
            _PROJECT.c.computer_id,
            _PROJECT.c.repo_path,
        )
        .select_from(source)
        .where(
            _TASK.c.writes_code.is_(True),
            _TASK.c.project_id.is_not(None),
            _TASK.c.status.notin_(_TERMINAL_TASK_STATUSES),
            or_(
                _WORKTREE.c.task_id.is_(None),
                _WORKTREE.c.status == WorktreeStatus.CLEANED.value,
            ),
        )
        .order_by(_TASK.c.id)
    )
    if computer_id is not None:
        stmt = stmt.where(_PROJECT.c.computer_id == computer_id)
    if channel_id is not None:
        stmt = stmt.where(_TASK.c.channel_id == channel_id)
    if task_id is not None:
        stmt = stmt.where(_TASK.c.id == task_id)

    return [
        EnsurePlan(
            task_id=row["task_id"],
            project_id=row["project_id"],
            computer_id=row["computer_id"],
            repo_path=row["repo_path"],
            branch=f"coagentia/task-{row['task_id']}",
        )
        for row in conn.execute(stmt).mappings()
    ]


def revalidation_plans(
    conn: Connection,
    *,
    computer_id: str | None = None,
    task_id: str | None = None,
) -> list[EnsurePlan]:
    """reconnect 复验计划（#3）：既有 active worktree 行且任务未终态 → 重下发 ensure（幂等；daemon
    树在则 noop 上报 active，树没了则 prune 重建）。ensure_plans 因 `_WORKTREE.task_id.is_(None)`
    排除已有行的 task，故需专用构造器。**仅 active，绝不含 conflicted**：re-ensure 会让 daemon 上报
    active，apply_status 会把冲突态覆盖回 active（回归）。"""
    stmt = (
        select(
            _WORKTREE.c.task_id,
            _WORKTREE.c.project_id,
            _PROJECT.c.computer_id,
            _PROJECT.c.repo_path,
            _WORKTREE.c.branch,
        )
        .select_from(
            _WORKTREE.join(_TASK, _TASK.c.id == _WORKTREE.c.task_id).join(
                _PROJECT, _PROJECT.c.id == _WORKTREE.c.project_id
            )
        )
        .where(
            _WORKTREE.c.status == WorktreeStatus.ACTIVE.value,
            _TASK.c.status.notin_(_TERMINAL_TASK_STATUSES),
        )
        .order_by(_WORKTREE.c.task_id)
    )
    if computer_id is not None:
        stmt = stmt.where(_PROJECT.c.computer_id == computer_id)
    if task_id is not None:
        stmt = stmt.where(_WORKTREE.c.task_id == task_id)
    return [
        EnsurePlan(
            task_id=row["task_id"],
            project_id=row["project_id"],
            computer_id=row["computer_id"],
            repo_path=row["repo_path"],
            branch=row["branch"],
        )
        for row in conn.execute(stmt).mappings()
    ]


def cleanup_plans(
    conn: Connection, *, computer_id: str, now: str | None = None, task_id: str | None = None
) -> list[CleanupPlan]:
    """任务 done/closed 起经过 Project.keep_days 后推导 cleanup；cleaned 行不会再下发。

    task_id 过滤（M6 review 效率）：hub._cleanup_worktree 锁内复核单任务是否 due，无须重推
    全 computer 的候选集与全局 merge-retained BFS（reconcile 已算过全量，K 个 due 任务原本要
    K+1 次全局推导）。"""
    at = _parse_timestamp(now or now_iso())
    stmt = (
        select(
            _WORKTREE.c.task_id,
            _WORKTREE.c.project_id,
            _WORKTREE.c.branch,
            _WORKTREE.c.path,
            _PROJECT.c.computer_id,
            _PROJECT.c.worktree_keep_days,
            _WORKTREE.c.status.label("worktree_status"),
            _WORKTREE.c.merged_at,
            _TASK.c.status.label("task_status"),
            _TASK.c.status_changed_at,
        )
        .select_from(
            _WORKTREE.join(_TASK, _TASK.c.id == _WORKTREE.c.task_id).join(
                _PROJECT, _PROJECT.c.id == _WORKTREE.c.project_id
            )
        )
        .where(
            _PROJECT.c.computer_id == computer_id,
            (
                _TASK.c.status.in_(_TERMINAL_TASK_STATUSES)
                | (_WORKTREE.c.status == WorktreeStatus.MERGED.value)
            ),
            _WORKTREE.c.status != WorktreeStatus.CLEANED.value,
        )
        .order_by(_WORKTREE.c.task_id)
    )
    if task_id is not None:
        stmt = stmt.where(_WORKTREE.c.task_id == task_id)
    rows = conn.execute(stmt).mappings()
    occupied_by_tree: dict[tuple[str, str, str], set[str]] = {}
    for active in conn.execute(
        select(
            _WORKTREE.c.task_id,
            _WORKTREE.c.project_id,
            _WORKTREE.c.path,
            _WORKTREE.c.branch,
        )
        .select_from(_WORKTREE.join(_TASK, _TASK.c.id == _WORKTREE.c.task_id))
        .where(
            _WORKTREE.c.status.in_(_DIRECTORY_STATUSES),
            _TASK.c.status.notin_(_TERMINAL_TASK_STATUSES),
        )
    ).mappings():
        key = (active["project_id"], active["path"], active["branch"])
        occupied_by_tree.setdefault(key, set()).add(active["task_id"])
    plans: list[CleanupPlan] = []
    for row in rows:
        # 冲突任务的逻辑 Worktree 行复用原任务 path/branch；物理树只由原分支所属任务清理。
        if row["branch"] != f"coagentia/task-{row['task_id']}":
            continue
        tree_key = (row["project_id"], row["path"], row["branch"])
        if occupied_by_tree.get(tree_key, set()) - {row["task_id"]}:
            continue  # 同物理树仍有 active/conflicted alias，retention 不得提前拆树。
        if (
            row["worktree_status"] == WorktreeStatus.ACTIVE.value
            and row["task_status"] == TaskStatus.DONE.value
        ):
            continue  # DEDAG：done 而未合并的树是待 merge 输入，retention 不得抢先拆树。
        if row["worktree_status"] == WorktreeStatus.MERGED.value:
            if row["merged_at"] is None and row["task_status"] not in _TERMINAL_TASK_STATUSES:
                continue
            anchor = row["merged_at"] or row["status_changed_at"]
        else:
            anchor = row["status_changed_at"]
        due_at = _parse_timestamp(anchor) + timedelta(
            days=int(row["worktree_keep_days"])
        )
        if due_at <= at:
            plans.append(CleanupPlan(task_id=row["task_id"], computer_id=row["computer_id"]))
    return plans


def apply_status(
    conn: Connection,
    *,
    computer_id: str,
    data: WorktreeStatusData,
    trusted_running_merge: bool = False,
) -> StatusResult | None:
    """校验上报归属并按 task_id upsert；重复 active 不重复触发目录消息。

    DEDAG：画布节点归属校验随图退役——非信任路径要求上报 daemon 与 Project.computer_id
    匹配（越界上报不污染事实源）；trusted_running_merge = 任务级 merge 执行中的步上报放行。"""
    task_stmt = (
        select(
            _TASK.c.workspace_id,
            _TASK.c.channel_id,
            _TASK.c.root_message_id,
            _TASK.c.owner_member_id,
            _TASK.c.project_id,
            _TASK.c.status.label("task_status"),
        )
        .select_from(_TASK.join(_PROJECT, _PROJECT.c.id == _TASK.c.project_id))
        .where(_TASK.c.id == data.task_id)
    )
    if not trusted_running_merge:
        task_stmt = task_stmt.where(_PROJECT.c.computer_id == computer_id)
    task = conn.execute(task_stmt).mappings().first()
    if task is None or task["project_id"] is None:
        return None

    existing_row = (
        conn.execute(select(_WORKTREE).where(_WORKTREE.c.task_id == data.task_id))
        .mappings()
        .first()
    )
    existing = dict(existing_row) if existing_row is not None else None
    ts = now_iso()
    status = WorktreeStatus(data.status)
    values: dict[str, Any] = {
        "branch": data.branch,
        "path": data.path,
        "status": status,
    }
    if data.merge_commit is not None or existing is None:
        values["merge_commit"] = data.merge_commit
    if status == WorktreeStatus.MERGED:
        values["merged_at"] = existing.get("merged_at") if existing else ts
        values["merged_at"] = values["merged_at"] or ts
    if status == WorktreeStatus.CLEANED:
        values["cleaned_at"] = existing.get("cleaned_at") if existing else ts
        values["cleaned_at"] = values["cleaned_at"] or ts

    if existing is None:
        worktree_id = new_ulid()
        conn.execute(
            insert(_WORKTREE).values(
                id=worktree_id,
                workspace_id=task["workspace_id"],
                project_id=task["project_id"],
                task_id=data.task_id,
                created_at=ts,
                merged_at=values.pop("merged_at", None),
                cleaned_at=values.pop("cleaned_at", None),
                **values,
            )
        )
    else:
        worktree_id = existing["id"]
        conn.execute(update(_WORKTREE).where(_WORKTREE.c.id == worktree_id).values(**values))

    row = conn.execute(select(_WORKTREE).where(_WORKTREE.c.id == worktree_id)).mappings().one()
    alias_rows: tuple[dict[str, Any], ...] = ()
    if status == WorktreeStatus.CLEANED:
        # 同物理 path/branch 的冲突任务逻辑行随原树一起收敛 cleaned，不另发 cleanup 指令。
        alias_ids = list(
            conn.execute(
                select(_WORKTREE.c.id)
                .where(
                    _WORKTREE.c.id != worktree_id,
                    _WORKTREE.c.project_id == task["project_id"],
                    _WORKTREE.c.path == row["path"],
                    _WORKTREE.c.branch == row["branch"],
                    _WORKTREE.c.status != WorktreeStatus.CLEANED.value,
                )
                .order_by(_WORKTREE.c.id)
            ).scalars()
        )
        if alias_ids:
            conn.execute(
                update(_WORKTREE)
                .where(_WORKTREE.c.id.in_(alias_ids))
                .values(status=WorktreeStatus.CLEANED, cleaned_at=row["cleaned_at"] or ts)
            )
            alias_rows = tuple(
                dict(alias)
                for alias in conn.execute(
                    select(_WORKTREE)
                    .where(_WORKTREE.c.id.in_(alias_ids))
                    .order_by(_WORKTREE.c.id)
                ).mappings()
            )
    became_active = status == WorktreeStatus.ACTIVE and (
        existing is None
        or existing["status"] != WorktreeStatus.ACTIVE.value
        or existing["path"] != data.path
    )
    return StatusResult(
        row=dict(row),
        alias_rows=alias_rows,
        became_active=became_active,
        workspace_id=task["workspace_id"],
        channel_id=task["channel_id"],
        root_message_id=task["root_message_id"],
        owner_member_id=task["owner_member_id"],
        task_status=task["task_status"],
    )


def directory_contexts(
    conn: Connection, *, agent_member_id: str, channel_id: str
) -> list[DirectoryContext]:
    """该 Agent 在频道内当前可工作的树，供投递副本与 briefing 注入。"""
    rows = conn.execute(
        select(
            _TASK.c.id.label("task_id"),
            _TASK.c.channel_id,
            _TASK.c.root_message_id,
            _TASK.c.number,
            _TASK.c.title,
            _WORKTREE.c.path,
        )
        .select_from(_TASK.join(_WORKTREE, _WORKTREE.c.task_id == _TASK.c.id))
        .where(
            _TASK.c.owner_member_id == agent_member_id,
            _TASK.c.channel_id == channel_id,
            _TASK.c.status.notin_(_TERMINAL_TASK_STATUSES),
            _WORKTREE.c.status.in_(_DIRECTORY_STATUSES),
        )
        .order_by(_TASK.c.number, _TASK.c.id)
    ).mappings()
    return [
        DirectoryContext(
            task_id=row["task_id"],
            channel_id=row["channel_id"],
            root_message_id=row["root_message_id"],
            task_number=row["number"],
            task_title=row["title"],
            path=row["path"],
        )
        for row in rows
    ]


def contexts_for_message(
    conn: Connection,
    *,
    agent_member_id: str,
    message: dict[str, Any],
    contexts: list[DirectoryContext] | None = None,
) -> list[DirectoryContext]:
    """任务线程只注入对应树；频道 briefing（系统消息且 @该 Agent）注入其全部活动树。"""
    available = (
        contexts
        if contexts is not None
        else directory_contexts(
            conn, agent_member_id=agent_member_id, channel_id=message["channel_id"]
        )
    )
    root = message.get("thread_root_id") or message["id"]
    scoped = [item for item in available if item.root_message_id == root]
    if scoped:
        return scoped
    if message.get("kind") != "system":
        return []
    mentioned = conn.execute(
        select(_MENTION.c.member_id).where(
            _MENTION.c.message_id == message["id"],
            _MENTION.c.member_id == agent_member_id,
        )
    ).first()
    return available if mentioned is not None else []


def directory_message(path: str) -> str:
    return f"[系统工作目录] 在 `{path}` 中工作，勿改动主工作区。"


def inject_directory_context(
    conn: Connection, *, agent_member_id: str, messages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """只改发往单 Agent 的 MessagePublic 副本 body；DB 行/id/游标身份不变。"""
    if not messages:
        return []
    by_channel: dict[str, list[DirectoryContext]] = {}
    result: list[dict[str, Any]] = []
    for message in messages:
        channel_id = message["channel_id"]
        contexts = by_channel.get(channel_id)
        if contexts is None:
            contexts = directory_contexts(
                conn, agent_member_id=agent_member_id, channel_id=channel_id
            )
            by_channel[channel_id] = contexts
        relevant = contexts_for_message(
            conn,
            agent_member_id=agent_member_id,
            message=message,
            contexts=contexts,
        )
        body = message["body"]
        additions = [directory_message(item.path) for item in relevant if item.path not in body]
        suffix = "\n\n" + "\n".join(additions) if additions else ""
        result.append({**message, "body": body + suffix})
    return result


def activation_context(
    conn: Connection, *, agent_member_id: str, message: dict[str, Any]
) -> DirectoryContext | None:
    """判定一条系统消息是否携带可执行目录（wake 附带目录上下文用；WakeReason.CANVAS_ACTIVATION
    帧名系契约 D 冻结沿用，画布域已随 DEDAG 退役，语义 = 任务开工上下文）。"""
    if message.get("kind") != "system":
        return None
    contexts = contexts_for_message(
        conn, agent_member_id=agent_member_id, message=message
    )
    if not contexts:
        return None
    return contexts[0]


def delivery_waits_for_directory(
    conn: Connection, *, agent_member_id: str, message: dict[str, Any]
) -> bool:
    """激活 writes_code 任务在绝对 path 未就绪时 fail-closed：相关 wake/deliver 留积压。"""
    rows = conn.execute(
        select(
            _TASK.c.id.label("task_id"),
            _TASK.c.root_message_id,
            _WORKTREE.c.status.label("worktree_status"),
            _WORKTREE.c.path,
        )
        .select_from(
            _TASK.outerjoin(_WORKTREE, _WORKTREE.c.task_id == _TASK.c.id)
        )
        .where(
            _TASK.c.owner_member_id == agent_member_id,
            _TASK.c.channel_id == message["channel_id"],
            _TASK.c.writes_code.is_(True),
            _TASK.c.status.notin_(_TERMINAL_TASK_STATUSES),
        )
        .order_by(_TASK.c.id)
    ).mappings()
    missing: list[dict[str, Any]] = []
    for row in rows:
        if row["worktree_status"] not in _DIRECTORY_STATUSES or not row["path"]:
            missing.append(dict(row))
    if not missing:
        return False
    root = message.get("thread_root_id") or message["id"]
    if any(item["root_message_id"] == root for item in missing):
        return True
    if message.get("kind") != "system":
        return False
    return conn.execute(
        select(_MENTION.c.member_id).where(
            _MENTION.c.message_id == message["id"],
            _MENTION.c.member_id == agent_member_id,
        )
    ).first() is not None


__all__ = [
    "CleanupPlan",
    "DirectoryContext",
    "EnsurePlan",
    "StatusResult",
    "activation_context",
    "apply_status",
    "cleanup_plans",
    "contexts_for_message",
    "directory_contexts",
    "directory_message",
    "delivery_waits_for_directory",
    "ensure_plans",
    "inject_directory_context",
    "revalidation_plans",
]
