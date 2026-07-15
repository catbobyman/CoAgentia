"""PS-WT REST 端点（契约 B §4.11 扩，设计 §3.2/§5）：目录浏览 fs 代理 + 工作树管理台读面 +
登记清理 + 孤儿清理。全部人类-only（Agent → 403 O9 同门），不注册 MCP 工具。

清理三段式锁纪律（CR-M8 教训「跨进程同步等待不得跨持锁事务」）：① 只读短事务做门校验；
② hub 下发 WORKTREE_CLEANUP 并等 daemon 结果**期间不持写事务**；③ 成功后另开事务条件 UPDATE
（CAS）+ 广播。下发失败/超时 → DB 未动 → 503，无幽灵态。
"""

from __future__ import annotations

from typing import Any

from coagentia_contracts import daemon, entities, rest
from coagentia_contracts.enums import MemberKind, WorktreeStatus
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from coagentia_server.api import ApiError
from coagentia_server.db import models
from coagentia_server.deps import (
    Tx,
    acting_member,
    get_tx,
    is_admin,
    require_workspace,
)
from coagentia_server.routes.serialize import worktree_public
from coagentia_server.worktrees import console as console_service

router = APIRouter(prefix="/api", tags=["worktrees"])

_WORKTREE = models.tbl(models.Worktree)
_PROJECT = models.tbl(models.Project)
_TASK = models.tbl(models.Task)
_COMPUTER = models.tbl(models.Computer)
_PREVIEW = models.tbl(models.PreviewSession)
_PREVIEW_ACTIVE = models.PREVIEW_ACTIVE_STATUSES

_TERMINAL_WORKTREE_STATUSES = (WorktreeStatus.MERGED.value, WorktreeStatus.CONFLICTED.value)


def _require_human(request: Request, tx: Tx) -> dict[str, Any]:
    """管理台读面 = 人类面：Agent 主体 → 403（O9 同门，设计 §3.2）。返回人类主体行。"""
    me = acting_member(request, tx.conn)
    if me["kind"] == MemberKind.AGENT.value:
        raise ApiError(
            403,
            rest.ErrorCode.PERMISSION_DENIED,
            "Agent 不可访问工作树管理台",
            rule="O9",
        )
    return me


def _require_human_admin(request: Request, tx: Tx) -> dict[str, Any]:
    """浏览/清理运维门：Agent → 403（O9 同门）；非 admin → 403（admin 门，设计 §3.2）。"""
    me = _require_human(request, tx)
    if not is_admin(me):
        raise ApiError(403, rest.ErrorCode.PERMISSION_DENIED, "需要管理员权限", rule="admin")
    return me


def _require_computer(tx: Tx, computer_id: str, workspace_id: str) -> None:
    row = tx.conn.execute(
        select(_COMPUTER.c.id).where(
            _COMPUTER.c.id == computer_id,
            _COMPUTER.c.workspace_id == workspace_id,
        )
    ).first()
    if row is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "机器不存在")


# ---------------------------------------------------------------- fs 代理（设计 §5.1）


@router.get("/computers/{computer_id}/fs", response_model=daemon.FsTreeReply)
def browse_fs(
    computer_id: str,
    request: Request,
    tx: Tx = Depends(get_tx),
    path: str | None = None,
) -> Any:
    """computer 级目录浏览只读代理（仿 get_task_diff）：admin 门 → hub 代理 FS_TREE；path 缺省
    None = 根视图（win32 盘符列表 / posix 单条 "/"）。daemon 离线/超时 → 503 DAEMON_OFFLINE。"""
    ws = require_workspace(tx.conn)
    _require_human_admin(request, tx)
    _require_computer(tx, computer_id, ws["id"])

    from coagentia_server.computers import DaemonOffline

    query = daemon.FsTreeQuery(path=path)
    try:
        reply = request.app.state.daemon_hub.query_fs_tree(
            computer_id=computer_id, query=query
        )
    except DaemonOffline as exc:
        raise ApiError(
            503, rest.ErrorCode.DAEMON_OFFLINE, "daemon 离线或查询超时，无法浏览目录"
        ) from exc
    return daemon.FsTreeReply.model_validate(reply)


# ---------------------------------------------------------------- 管理台读面（设计 §5.2）


@router.get("/worktrees", response_model=rest.WorktreeConsoleReply)
def list_worktrees(
    request: Request,
    tx: Tx = Depends(get_tx),
    live: int = 0,
) -> Any:
    """工作树管理台列表（读面 = workspace 成员，非 admin）：live=0 纯 DB 骨架秒出；live=1 对该
    workspace 涉及的每个 computer 并发 WORKTREE_SCAN，server 侧合账（§5.2）附 derived/live/scans。
    """
    ws = require_workspace(tx.conn)
    _require_human(request, tx)

    db_rows = [
        dict(r)
        for r in tx.conn.execute(
            select(
                _WORKTREE,
                _PROJECT.c.name.label("project_name"),
                _PROJECT.c.computer_id,
                _TASK.c.title.label("task_title"),
                _TASK.c.channel_id.label("channel_id"),
            )
            .select_from(
                _WORKTREE.join(_PROJECT, _PROJECT.c.id == _WORKTREE.c.project_id).join(
                    _TASK, _TASK.c.id == _WORKTREE.c.task_id
                )
            )
            .where(_WORKTREE.c.workspace_id == ws["id"])
        ).mappings()
    ]

    projects = [
        dict(p)
        for p in tx.conn.execute(
            select(_PROJECT.c.id, _PROJECT.c.name, _PROJECT.c.computer_id).where(
                _PROJECT.c.workspace_id == ws["id"]
            )
        ).mappings()
    ]
    project_names = {p["id"]: p["name"] for p in projects}

    scans: dict[str, console_service.ScanOutcome] = {}
    task_info: dict[str, tuple[str | None, str | None]] = {}
    if live:
        computer_ids = sorted({p["computer_id"] for p in projects})
        scans = request.app.state.daemon_hub.scan_worktrees(computer_ids)
        disk_task_ids = {
            entry["task_id"]
            for status, entries in scans.values()
            if status == "ok" and entries
            for entry in entries
        }
        if disk_task_ids:
            task_info = {
                t["id"]: (t["title"], t["channel_id"])
                for t in tx.conn.execute(
                    select(_TASK.c.id, _TASK.c.title, _TASK.c.channel_id).where(
                        _TASK.c.id.in_(disk_task_ids)
                    )
                ).mappings()
            }

    items, scan_statuses = console_service.reconcile_console(
        db_rows=db_rows,
        scans=scans,
        project_names=project_names,
        task_info=task_info,
        live=bool(live),
    )
    return rest.WorktreeConsoleReply(items=items, scans=scan_statuses)


# ---------------------------------------------------------------- 清理端点（设计 §5.3）


@router.post("/worktrees/{worktree_id}/cleanup", response_model=entities.WorktreePublic)
def cleanup_worktree(
    worktree_id: str,
    request: Request,
    tx: Tx = Depends(get_tx),
) -> Any:
    """登记工作树清理（admin + Agent 403）：仅 merged/conflicted 可清理，任务无活跃预览，daemon 在。

    三段式：① 只读门校验（此前不写库）；② hub 下发并等 ack（不持写事务）；③ 成功后 CAS 收敛
    cleaned + 广播 worktree.updated。下发失败/超时/daemon 主动 failed → 503，登记未变更。"""
    ws = require_workspace(tx.conn)
    _require_human_admin(request, tx)

    # ① 只读门校验（只 SELECT，不写库 → 后续等 daemon ack 期间不持写锁，杜绝 CR-M8 自死锁）。
    row = (
        tx.conn.execute(
            select(_WORKTREE, _PROJECT.c.computer_id)
            .select_from(_WORKTREE.join(_PROJECT, _PROJECT.c.id == _WORKTREE.c.project_id))
            .where(_WORKTREE.c.id == worktree_id, _WORKTREE.c.workspace_id == ws["id"])
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "worktree 不存在")
    if row["status"] not in _TERMINAL_WORKTREE_STATUSES:
        raise ApiError(
            409,
            rest.ErrorCode.WORKTREE_NOT_TERMINAL,
            "仅 merged/conflicted 工作树可清理（active 走任务流程，裁决 #10）",
            rule="worktree_not_terminal",
            details={"status": row["status"]},
        )
    active_preview = tx.conn.execute(
        select(_PREVIEW.c.id)
        .where(_PREVIEW.c.task_id == row["task_id"], _PREVIEW.c.status.in_(_PREVIEW_ACTIVE))
        .limit(1)
    ).first()
    if active_preview is not None:
        raise ApiError(
            409,
            rest.ErrorCode.WORKTREE_PREVIEW_ACTIVE,
            "任务预览活跃占用工作树目录，请先停止预览再清理",
            rule="worktree_preview_active",
        )

    hub = request.app.state.daemon_hub
    if not hub.preview_daemon_online(row["computer_id"]):
        raise ApiError(503, rest.ErrorCode.DAEMON_OFFLINE, "daemon 离线，无法清理工作树")

    # ② 下发并等 daemon 结果（不持写事务）。
    from coagentia_server.computers import DaemonOffline

    try:
        result = hub.dispatch_worktree_cleanup(
            computer_id=row["computer_id"], task_id=row["task_id"]
        )
    except DaemonOffline as exc:
        raise ApiError(
            503, rest.ErrorCode.DAEMON_OFFLINE, "daemon 离线或清理超时，工作树未清理"
        ) from exc
    if result == "failed":
        # daemon 在线但删除失败（win32 文件锁等）：fail-closed 不推进登记，漂移下次扫描浮出。
        raise ApiError(
            503,
            rest.ErrorCode.DAEMON_OFFLINE,
            "daemon 清理工作树失败（目录可能被占用），登记未变更",
        )

    # ③ 成功后 CAS 收敛 + 广播（另开事务）。
    updated = hub.finalize_console_cleanup(task_id=row["task_id"], computer_id=row["computer_id"])
    if updated is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "worktree 已不存在")
    return worktree_public(updated)


@router.post(
    "/computers/{computer_id}/worktrees/cleanup-orphan",
    response_model=rest.OrphanCleanupResult,
)
def cleanup_orphan(
    computer_id: str,
    body: rest.OrphanCleanup,
    request: Request,
    tx: Tx = Depends(get_tx),
) -> Any:
    """孤儿工作树清理（admin + Agent 403）：ids-only 定位（永不传裸路径）。存在**非 cleaned**登记
    行 → 409 WORKTREE_NOT_ORPHAN（防把登记树当孤儿删）；daemon 在线 → 下发 WORKTREE_CLEANUP
    （带 project_id 供 daemon 自拼 worktrees_dir 内路径）。无 DB 行可写、不广播，响应即终态。"""
    ws = require_workspace(tx.conn)
    _require_human_admin(request, tx)
    _require_computer(tx, computer_id, ws["id"])

    existing = (
        tx.conn.execute(
            select(_WORKTREE.c.status).where(
                _WORKTREE.c.project_id == body.project_id,
                _WORKTREE.c.task_id == body.task_id,
            )
        )
        .mappings()
        .first()
    )
    if existing is not None and existing["status"] != WorktreeStatus.CLEANED.value:
        raise ApiError(
            409,
            rest.ErrorCode.WORKTREE_NOT_ORPHAN,
            "存在非 cleaned 登记行，非孤儿，不可作孤儿清理",
            rule="worktree_not_orphan",
            details={"status": existing["status"]},
        )

    hub = request.app.state.daemon_hub
    if not hub.preview_daemon_online(computer_id):
        raise ApiError(503, rest.ErrorCode.DAEMON_OFFLINE, "daemon 离线，无法清理孤儿工作树")

    from coagentia_server.computers import DaemonOffline

    try:
        result = hub.dispatch_worktree_cleanup(
            computer_id=computer_id, task_id=body.task_id, project_id=body.project_id
        )
    except DaemonOffline as exc:
        raise ApiError(
            503, rest.ErrorCode.DAEMON_OFFLINE, "daemon 离线或清理超时，孤儿工作树未清理"
        ) from exc
    if result == "failed":
        raise ApiError(
            503,
            rest.ErrorCode.DAEMON_OFFLINE,
            "daemon 清理孤儿工作树失败（目录可能被占用）",
        )

    return rest.OrphanCleanupResult(
        project_id=body.project_id, task_id=body.task_id, removed=True
    )
