"""部署域 REST 端点（契约 B §13.2/§13.3；M7b K4）。

判定归 server（排队 409 / 新账快照 / 主干 HEAD 解析），执行归 daemon（deploy.run 收指令即跑，
只上报 deploy.log/deploy.finished 事实）。范式照 routes/tasks.py 的 ensure_preview（acting_member
身份、ApiError 报错、SAVEPOINT+IntegrityError 兜底部分唯一索引、tx.after_commit 提交后下发、
tx.emit 广播）。日志由 server 收 deploy.log 时落盘（DaemonHub._report_deploy_log），本模块 GET log
端点直读该落盘文件——不依赖 daemon 在线（B §13.3）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from coagentia_contracts import daemon, entities, rest
from coagentia_contracts.constants import BUFFER_DEPLOY_LOG_MAX_BYTES, OPID_REST_IDEMPOTENCY
from coagentia_contracts.entities import TasksReporting, TokenSummary, UsageBucket
from coagentia_contracts.kernel.fingerprint import fingerprint
from coagentia_contracts.ws import EventType
from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy import func, insert, select
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from coagentia_server.api import ApiError
from coagentia_server.db import models
from coagentia_server.deps import Tx, acting_member, get_tx
from coagentia_server.ledger import service as ledger_service
from coagentia_server.ledger.service import new_ulid, now_iso
from coagentia_server.routes.serialize import deployment_public

router = APIRouter(prefix="/api", tags=["deployments"])

_DEPLOYMENT = models.tbl(models.Deployment)
_PROJECT = models.tbl(models.Project)
_WORKTREE = models.tbl(models.Worktree)
_TUE = models.tbl(models.TokenUsageEvent)

# 新账 task_ids 有界（details 有界先例 §12.12 #4；契约 TokenSummary.task_ids ≤50）。
_TOKEN_SUMMARY_TASK_CAP = 50


# ---------------------------------------------------------------- 新账 token_summary（纯 SQL 快照）


def compute_token_summary(
    conn: Connection, project_id: str, until_iso: str
) -> TokenSummary:
    """新账口径（契约 B §13.4）：上次 success 部署以来 merged 任务集的 token 聚合快照。

    - 下界 = 该 project 上一条 status='success' deployment 的 finished_at（无则无下界=首次全算）；
      **失败部署不推进区间**（下界只认 success）。
    - 上界 = 本次 deployment 的 created_at（until_iso，含端点）。
    - 任务集 = 该 project 的 worktrees 中 merged_at ∈ (下界, 上界] 的 task_id 集（去重）。
    - usage = 这些 task 的 token_usage_events 四列和 + 事件计数（照 tasks.py TaskDetail 聚合体例）。
    - tasks_reporting.total = 任务集大小；reporting = 其中有 ≥1 条 usage 事件的任务数（未上报任务
      计入分母，W7 诚实覆盖率）。
    - task_ids = 任务集按 id 稳定排序、截 50（total 仍为全量计数）。

    POST 建行时算一次落列；查询时不重算（快照，失败部署不推进下界故区间稳定）。
    """
    lower = conn.execute(
        select(_DEPLOYMENT.c.finished_at)
        .where(
            _DEPLOYMENT.c.project_id == project_id,
            _DEPLOYMENT.c.status == "success",
            _DEPLOYMENT.c.finished_at.isnot(None),
        )
        .order_by(_DEPLOYMENT.c.finished_at.desc())
        .limit(1)
    ).scalar()

    conds = [
        _WORKTREE.c.project_id == project_id,
        _WORKTREE.c.merged_at.isnot(None),
        _WORKTREE.c.merged_at <= until_iso,
    ]
    if lower is not None:
        conds.append(_WORKTREE.c.merged_at > lower)
    task_ids = sorted(
        set(conn.execute(select(_WORKTREE.c.task_id).where(*conds)).scalars().all())
    )
    if not task_ids:
        return TokenSummary(
            usage=UsageBucket(),
            tasks_reporting=TasksReporting(reporting=0, total=0),
            task_ids=[],
        )
    agg = conn.execute(
        select(
            func.coalesce(func.sum(_TUE.c.input_tokens), 0),
            func.coalesce(func.sum(_TUE.c.output_tokens), 0),
            func.coalesce(func.sum(_TUE.c.cache_read_tokens), 0),
            func.coalesce(func.sum(_TUE.c.cache_write_tokens), 0),
            func.count(_TUE.c.id),
        ).where(_TUE.c.task_id.in_(task_ids))
    ).one()
    reporting = conn.execute(
        select(func.count(func.distinct(_TUE.c.task_id))).where(
            _TUE.c.task_id.in_(task_ids)
        )
    ).scalar_one()
    return TokenSummary(
        usage=UsageBucket(
            input_tokens=agg[0],
            output_tokens=agg[1],
            cache_read_tokens=agg[2],
            cache_write_tokens=agg[3],
            events=agg[4],
        ),
        tasks_reporting=TasksReporting(reporting=reporting, total=len(task_ids)),
        task_ids=task_ids[:_TOKEN_SUMMARY_TASK_CAP],
    )


# ---------------------------------------------------------------- 内部


def _fetch_project(tx: Tx, project_id: str) -> dict[str, Any]:
    row = (
        tx.conn.execute(select(_PROJECT).where(_PROJECT.c.id == project_id))
        .mappings()
        .first()
    )
    if row is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "Project 不存在")
    return dict(row)


def _read_deployment(tx: Tx, deployment_id: str) -> dict[str, Any] | None:
    row = (
        tx.conn.execute(select(_DEPLOYMENT).where(_DEPLOYMENT.c.id == deployment_id))
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def _head_resolve_error(repo_path: str) -> ApiError:
    return ApiError(
        422,
        rest.ErrorCode.VALIDATION_FAILED,
        "无法解析 repo_path 主干 HEAD（分支/commit）",
        rule="B§13.2",
        details={"field": "repo_path", "hint": "确认 repo_path 指向有提交的 git 仓库"},
    )


def _resolve_head(repo_path: str) -> tuple[str, str | None]:
    """server 触发时直查主干 HEAD（MVP 单机同机直查文件系统，§12.12 #1 先例）：返回
    (branch, commit_hash)。查失败（非仓库/空仓/git 不可用）→ 422（照 diff 坏 base 处理）。"""
    path = Path(repo_path)
    if not path.is_dir():
        raise _head_resolve_error(repo_path)
    try:
        head = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        br = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--abbrev-ref", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _head_resolve_error(repo_path) from exc
    if head.returncode != 0:
        raise _head_resolve_error(repo_path)
    commit_hash = head.stdout.strip() or None
    branch = br.stdout.strip() if br.returncode == 0 else "HEAD"
    if not branch or branch == "HEAD":
        branch = "HEAD"  # detached HEAD：分支名未知，留 'HEAD'（commit_hash 仍留痕）
    return branch, commit_hash


# ---------------------------------------------------------------- 端点


@router.post(
    "/projects/{project_id}/deployments", response_model=entities.DeploymentPublic
)
def create_deployment(
    project_id: str,
    request: Request,
    response: Response,
    tx: Tx = Depends(get_tx),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Any:
    """触发一次部署（R8 全员含 Agent，无角色门；请求体空）。

    422 无 deploy_command / 503 daemon 离线（不建行）/ 409 已有非终态部署（部分唯一索引兜底）/
    Idempotency-Key 命中返旧。建行 queued + 新账快照落列 + 广播 deployment.created + 提交后
    （tx.after_commit）下发 deploy.run（铁律 4，杜绝 running 帧先于建行提交丢帧）。
    """
    me = acting_member(request, tx.conn)  # R8 无角色校验，Agent 亦放行
    project = _fetch_project(tx, project_id)
    deploy_command = project["deploy_command"]
    if not deploy_command or not deploy_command.strip():
        raise ApiError(
            422,
            rest.ErrorCode.VALIDATION_FAILED,
            "Project 未配置 deploy_command，无法触发部署",
            rule="B§13.2",
            details={
                "project_id": project_id,
                "hint": "先在 Project 设置里配置 deploy_command 再触发部署",
            },
        )
    hub = request.app.state.daemon_hub
    computer_id = project["computer_id"]
    # 503 早探（不建行）：daemon 离线直接拒，避免建 queued 孤行（判定归 server）。
    if not hub.preview_daemon_online(computer_id):
        raise ApiError(503, rest.ErrorCode.DAEMON_OFFLINE, "daemon 离线，无法触发部署")

    # 主干 HEAD 解析前置（可失败 422，须早于幂等 reserve 与建行副作用；照 templates 幂等纪律）。
    branch, commit_hash = _resolve_head(project["repo_path"])
    ts = now_iso()

    # Idempotency-Key 复用既有账本（B §1）：命中同键同体 → 返回既有 deployment；异体 → 409。
    deployment_id = new_ulid()
    if idempotency_key is not None:
        op_id = OPID_REST_IDEMPOTENCY.format(key=idempotency_key)
        req_hash = fingerprint({"project_id": project_id})
        res = ledger_service.record(
            tx.conn,
            op_id,
            "rest_deployment",
            {"deployment_id": deployment_id},
            request_hash=req_hash,
        )
        if res["status"] == "hit":
            existing = _read_deployment(tx, res["entry"].payload["deployment_id"])
            if existing is not None:
                response.status_code = 200
                return deployment_public(existing)
            # reserve 后建行曾被 409 拒（并发已有部署在跑）→ 同键重放仍视为进行中拒绝。
            raise ApiError(
                409, rest.ErrorCode.DEPLOY_IN_PROGRESS, "该 Project 已有部署进行中"
            )
        if res["status"] == "mismatch":
            raise ApiError(
                409, rest.ErrorCode.IDEMPOTENCY_MISMATCH, "同 Idempotency-Key 不同请求体"
            )
        deployment_id = res["entry"].payload["deployment_id"]

    # 新账快照（触发时纯 SQL 推导，落列；查询不重算）。
    token_summary = compute_token_summary(tx.conn, project_id, ts)

    try:
        # SAVEPOINT 包裹建行：同 project 已有非终态部署时部分唯一索引触发 IntegrityError——这里
        # 冲突=拒绝（非退化），抛 409 DEPLOY_IN_PROGRESS。
        with tx.conn.begin_nested():
            tx.conn.execute(
                insert(_DEPLOYMENT).values(
                    id=deployment_id,
                    workspace_id=project["workspace_id"],
                    project_id=project_id,
                    triggered_by_member_id=me["id"],
                    branch=branch,
                    commit_hash=commit_hash,
                    command=deploy_command,
                    status="queued",
                    exit_code=None,
                    url=None,
                    log_path=None,
                    token_summary=token_summary.model_dump(mode="json"),
                    started_at=None,
                    finished_at=None,
                    created_at=ts,
                )
            )
    except IntegrityError as exc:
        raise ApiError(
            409, rest.ErrorCode.DEPLOY_IN_PROGRESS, "该 Project 已有部署进行中"
        ) from exc

    row = _read_deployment(tx, deployment_id)
    assert row is not None  # 刚插入行必存在（PK 回读不变量）
    # 全量广播 deployment.created（channel_id=None，照 presence/全量事件体例）。
    tx.emit(EventType.DEPLOYMENT_CREATED, None, {"deployment": deployment_public(row)})
    # 提交后下发 deploy.run（铁律 4）：queued 行提交后 daemon 才收指令，running 帧的 CAS
    # （WHERE status='queued'）必命中已提交行。
    run_data = daemon.DeployRunData(
        deployment_id=deployment_id,
        repo_path=project["repo_path"],
        command=deploy_command,
        branch=branch,
        commit_hash=commit_hash,
    )
    tx.after_commit(
        lambda: hub.request_deploy_run(computer_id=computer_id, data=run_data)
    )
    response.status_code = 201
    return deployment_public(row)


@router.get("/deployments/{deployment_id}", response_model=entities.DeploymentPublic)
def get_deployment(deployment_id: str, tx: Tx = Depends(get_tx)) -> Any:
    """纯读部署现状（无写副作用；404 不存在）。"""
    row = _read_deployment(tx, deployment_id)
    if row is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "部署不存在")
    return deployment_public(row)


@router.get(
    "/deployments/{deployment_id}/log", response_model=rest.DeploymentLogPage
)
def get_deployment_log(
    deployment_id: str, tx: Tx = Depends(get_tx), after: int | None = None
) -> Any:
    """部署日志翻页（B §13.3）：server 直读落盘日志文件（不依赖 daemon 在线）。

    after = 行号游标（0-based：已消费行数，返回该行之后的行）；缺省从头。next_after = 新游标
    （追上=None）；文件超 5MB 上限 → truncated=True。log_path 空/文件不存在 → 空页（不 404，
    部署刚建/无日志）。
    """
    row = _read_deployment(tx, deployment_id)
    if row is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "部署不存在")
    log_path = row["log_path"]
    if not log_path:
        return rest.DeploymentLogPage(lines=[], next_after=None, truncated=False).model_dump()
    path = Path(log_path)
    if not path.is_file():
        return rest.DeploymentLogPage(lines=[], next_after=None, truncated=False).model_dump()
    truncated = path.stat().st_size >= BUFFER_DEPLOY_LOG_MAX_BYTES
    all_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start = after if after is not None and after > 0 else 0
    page = all_lines[start:]
    # 无分页上限：page 已含 start→EOF 全部行 → 恒已追平 → next_after=None（复审 CONFIRMED：旧
    # `total if total > start` 对任何非空日志都返 total，前端误显"加载更多历史"按钮且点击拉空页；
    # 实时新增行由 deployment.log 订阅流投递，前端不据 next_after 轮询）。
    return rest.DeploymentLogPage(
        lines=page, next_after=None, truncated=truncated
    ).model_dump()
