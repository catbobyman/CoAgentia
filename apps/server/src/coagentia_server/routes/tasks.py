"""任务域 REST 端点（契约 B §9 / §4.7）：convert、claim/unclaim/assign、status、列表、详情、补丁。

范式照抄 routes/messages.py（router 前缀 /api、acting_member 身份、ApiError 报错、tx.emit 广播）；
状态机、建号、留痕、建任务集中在 tasks/service.py（纪律 7 单一事实源）。
"""

from __future__ import annotations

from typing import Any

from coagentia_contracts import constants, daemon, entities, rest
from coagentia_contracts.enums import (
    ChannelKind,
    MemberKind,
    MessageKind,
    TaskEventKind,
    TaskLevel,
    TaskStatus,
)
from fastapi import APIRouter, Depends, Request, Response
from pydantic import ValidationError
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError

from coagentia_server.api import ApiError
from coagentia_server.contracts import service as contracts_service
from coagentia_server.db import models
from coagentia_server.deps import Tx, acting_member, get_tx
from coagentia_server.messages import service as messages_service
from coagentia_server.routes._pagination import keyset_page
from coagentia_server.routes.serialize import (
    message_public,
    preview_session_public,
    task_contract_public,
    task_public,
    worktree_public,
)
from coagentia_server.tasks import merge as merge_domain
from coagentia_server.tasks import service as tasks_service

router = APIRouter(prefix="/api", tags=["tasks"])

_TASK = models.tbl(models.Task)
_EVT = models.tbl(models.TaskEvent)
_CHANNEL = models.tbl(models.Channel)
_MSG = models.tbl(models.Message)
_MEMBER = models.tbl(models.Member)
_TUE = models.tbl(models.TokenUsageEvent)
_PROJECT = models.tbl(models.Project)
_WORKTREE = models.tbl(models.Worktree)
_PREVIEW = models.tbl(models.PreviewSession)
_PREVIEW_ACTIVE = models.PREVIEW_ACTIVE_STATUSES


def _require_task(tx: Tx, task_id: str) -> dict[str, Any]:
    row = tx.conn.execute(select(_TASK).where(_TASK.c.id == task_id)).mappings().first()
    if row is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "任务不存在")
    return dict(row)


# ---------------------------------------------------------------- Convert to Task


@router.post("/messages/{message_id}/task", response_model=entities.TaskPublic, status_code=201)
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

    channel = (
        tx.conn.execute(select(_CHANNEL).where(_CHANNEL.c.id == msg["channel_id"]))
        .mappings()
        .first()
    )
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
        tx,
        final,
        kind=TaskEventKind.CLAIM,
        actor=me["id"],
        from_status=change_from,
        to_status=change_to,
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
        tx,
        final,
        kind=TaskEventKind.UNCLAIM,
        actor=me["id"],
        from_status=change_from,
        to_status=change_to,
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


def _notify_creator_in_review(tx: Tx, task: dict[str, Any], *, actor_id: str) -> None:
    """置 in_review 即通知创建者验收（DEDAG 挂账 ⑥，铺开 R2 实测教训机制化）。

    交付唤醒不再依赖交付话术遵从：任务线程落一条 durable 系统消息 + @创建者 mention 行，
    经 bus MESSAGE_CREATED 驱动投递引擎——创建者是 Agent 则 system+mention 视同唤醒触发
    （契约 D §8.2，hub._compute_trigger），是人类则走既有 mention 可见面（沉默提醒同款）。
    自己交付自己创建的任务不通知；创建者已移除则静默跳过。SYSTEM 消息不计入沉默链
    last_activity（B §10.5.2），不与 D5 提醒自激；同态幂等短路在调用点之前，重交付
    （in_review→in_progress→in_review）每次成功转换各通知一次。
    """
    creator = task.get("created_by_member_id")
    if creator is None or creator == actor_id:
        return
    row = tx.conn.execute(
        select(_MEMBER.c.name).where(_MEMBER.c.id == creator, _MEMBER.c.removed_at.is_(None))
    ).first()
    if row is None:
        return
    messages_service.post_system_message(
        tx,
        workspace_id=task["workspace_id"],
        channel_id=task["channel_id"],
        body=f"📬 任务 #{task['number']} 已交付进入待验收：@{row[0]} 请验收",
        thread_root_id=task["root_message_id"],
        mention_member_ids=(creator,),
    )


@router.post("/tasks/{task_id}/status", response_model=entities.TaskPublic)
def set_task_status(
    task_id: str, body: rest.TaskStatusChange, request: Request, tx: Tx = Depends(get_tx)
) -> Any:
    task = _require_task(tx, task_id)
    me = acting_member(request, tx.conn)
    to = body.to
    # CAS 状态写（纪律：状态机边写必条件 UPDATE；⑥ 收口时把可见副作用挂上本写点故一并
    # 条件化）：WHERE status=起态，竞败 rowcount=0 → 锁内重读最新态重走同一套校验——
    # 线性化语义（与串行到达等价），并发同转换收敛为幂等 200，双事件/双通知窗口关闭。
    for _attempt in range(3):
        cur = TaskStatus(task["status"])
        if to == cur:  # 同态幂等（含竞败后对方已达目标态）：不写事件、不广播（裁决 2）
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
        # T7 流转门（裁决 5）：l2 任务置 in_review 前，活动 TaskHandoff 的 deliverables/
        # evidence 必须非空；无活动 handoff 视同两者皆缺。l1 任务（M2 存量全 l1）不进本分支。
        if TaskLevel(task["level"]) == TaskLevel.L2 and to == TaskStatus.IN_REVIEW:
            missing = contracts_service.active_handoff_missing(tx.conn, task_id)
            if missing:
                raise ApiError(
                    422,
                    rest.ErrorCode.HANDOFF_INCOMPLETE,
                    f"缺少交接材料：{', '.join(missing)}",
                    rule="T7",
                    details={
                        "missing": missing,
                        "hint": "用 submit_task_contract 工具提交 kind=task_handoff，"
                        f"补齐 {', '.join(missing)}（deliverables 须≥1）后再置 in_review。",
                    },
                )
        ts = tasks_service.service.now_iso()
        res = tx.conn.execute(
            update(_TASK)
            .where(_TASK.c.id == task_id, _TASK.c.status == cur.value)
            .values(status=to, status_changed_at=ts)
        )
        if res.rowcount:
            break
        task = _require_task(tx, task_id)  # 竞败：锁内重读复核（M6 纪律）
    else:
        # 实际不可达（竞败后已持写锁，次轮必中或 422/幂等返回）；保守回最新态非法边。
        latest = TaskStatus(_require_task(tx, task_id)["status"])
        raise ApiError(
            422,
            rest.ErrorCode.TASK_TRANSITION_INVALID,
            f"任务状态并发变更，当前为 {latest.value}",
            rule="T4",
            details={
                "from": latest.value,
                "to": to.value,
                "allowed": sorted(s.value for s in tasks_service.TASK_TRANSITIONS[latest]),
            },
        )
    tasks_service.write_event(
        tx.conn, task_id, TaskEventKind.STATUS_CHANGE, actor=me["id"], from_status=cur, to_status=to
    )
    final = tasks_service.fetch_task(tx.conn, task_id)
    tasks_service.emit_task_updated(
        tx, final, kind=TaskEventKind.STATUS_CHANGE, actor=me["id"], from_status=cur, to_status=to
    )
    if to == TaskStatus.IN_REVIEW:
        _notify_creator_in_review(tx, final, actor_id=me["id"])
    return task_public(final)


# ------------------------------------------------- 任务级 merge（DEDAG）/ force-start（裁决 3）


@router.post("/tasks/{task_id}/merge", status_code=202)
def merge_task(task_id: str, request: Request, tx: Tx = Depends(get_tx)) -> Any:
    """任务级合并（DEDAG，契约 B v1.6 §14）：done 的 writes_code 任务合入主干。

    人类按钮与 Agent trigger_merge 工具同端点同权（合并是交付动作非画布结构，无 403 面）。
    同 Project 串行（409 沿 deploy 先例）；已 merged → 幂等 202 status=merged 不再下发；
    conflicted worktree 重触发 = 冲突解决后的 retry。下发经 tx.after_commit（铁律 4）。"""
    acting_member(request, tx.conn)  # 身份合法性校验（人类/Agent 同权）
    plan = merge_domain.prepare_merge(tx, task_id=task_id)
    if plan.already_merged:
        return {"task_id": task_id, "status": "merged"}
    hub = request.app.state.daemon_hub
    if hub.merge_running_for_project(plan.project_id):
        raise ApiError(
            409,
            rest.ErrorCode.DEPLOY_IN_PROGRESS,
            "该 Project 已有进行中的合并（同 Project 串行）",
            rule="W5",
        )
    if not hub.preview_daemon_online(plan.computer_id):
        raise ApiError(503, rest.ErrorCode.DAEMON_OFFLINE, "Project 宿主 daemon 离线")
    merge_domain.note_merge_started(tx, plan)
    tx.after_commit(lambda: hub.request_task_merge(plan))
    return {"task_id": task_id, "status": "accepted"}


@router.post("/tasks/{task_id}/force-start", response_model=entities.TaskPublic)
def force_start_task(task_id: str, request: Request, tx: Tx = Depends(get_tx)) -> Any:
    """人类强制启动任务（裁决 3；DEDAG 后语义收敛为「踢一脚」）：主动唤醒 owner agent 开工。

    效果 = 双留痕 + 直投一次唤醒；**不改 status、不删边**。画布 gating 已随 DEDAG 退役，
    本端点保留为人类对拖延/沉默任务的显式催动通道（与群聊 @ 互补，带 task_events 留痕）。
    Agent 不得 force-start（403 C3）。留痕 = task_events(force_start) 行 + 任务线程锚点系统消息。
    提交后经 daemon_hub.force_start_wake 桥：owner 是 agent 且 daemon 在线则直投一次
    wake+deliver；owner 人类/空 或 daemon 离线则仅留痕（best-effort）。
    """
    task = _require_task(tx, task_id)
    me = acting_member(request, tx.conn)
    if me["kind"] == MemberKind.AGENT:
        raise ApiError(403, rest.ErrorCode.PERMISSION_DENIED, "仅人类可强制启动任务", rule="C3")
    # 留痕 1：task_events(force_start)（from/to_status 留空——不改状态）。
    tasks_service.write_event(tx.conn, task_id, TaskEventKind.FORCE_START, actor=me["id"])
    force_event_seq = tx.conn.execute(
        select(_EVT.c.seq)
        .where(
            _EVT.c.task_id == task_id,
            _EVT.c.kind == TaskEventKind.FORCE_START.value,
            _EVT.c.actor_member_id == me["id"],
        )
        .order_by(_EVT.c.seq.desc())
        .limit(1)
    ).scalar_one()
    # 留痕 2：任务线程锚点系统消息（author=None、kind=system、thread=任务根消息）。
    anchor_id = tasks_service.service.new_ulid()
    tx.conn.execute(
        insert(_MSG).values(
            id=anchor_id,
            workspace_id=task["workspace_id"],
            channel_id=task["channel_id"],
            thread_root_id=task["root_message_id"],
            author_member_id=None,
            kind=MessageKind.SYSTEM,
            card_kind=None,
            card_ref=None,
            body=f"{me['name']} 强制启动了此任务（已直投唤醒负责人，已留痕）",
            created_at=tasks_service.service.now_iso(),
        )
    )
    msg_row = models.row_dict(
        tx.conn.execute(select(_MSG).where(_MSG.c.id == anchor_id)).mappings().first()
    )
    tx.emit(
        tasks_service.EventType.MESSAGE_CREATED,
        task["channel_id"],
        {"message": message_public(msg_row, [])},
    )
    # hub 桥「本次放行」（best-effort；owner 人类/空 或 daemon 离线 → 仅留痕，不报错）。
    # 不改状态：直接回既有 task 行（本请求未 UPDATE tasks）。
    request.app.state.daemon_hub.force_start_wake(
        task["owner_member_id"],
        task["channel_id"],
        task_id=task_id,
        force_event_seq=force_event_seq,
    )
    return task_public(task)


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
    # keyset：after 行即使因 status/owner 过滤离开结果集，游标仍按 (created_at,id) 锚点
    # 继续往后翻——不再静默从头重发首页（M2 挂账 3 收口）。
    return keyset_page(tx.conn, _TASK, stmt, after=after, limit=limit, serialize=task_public)


@router.get("/tasks/{task_id}", response_model=rest.TaskDetail)
def get_task_detail(task_id: str, tx: Tx = Depends(get_tx)) -> Any:
    task = _require_task(tx, task_id)
    contracts = contracts_service.active_contracts(tx.conn, task_id)
    worktree = (
        tx.conn.execute(select(_WORKTREE).where(_WORKTREE.c.task_id == task_id)).mappings().first()
    )
    agg = tx.conn.execute(
        select(
            func.coalesce(func.sum(_TUE.c.input_tokens), 0),
            func.coalesce(func.sum(_TUE.c.output_tokens), 0),
            func.coalesce(func.sum(_TUE.c.cache_read_tokens), 0),
            func.coalesce(func.sum(_TUE.c.cache_write_tokens), 0),
            func.count(_TUE.c.id),
        ).where(_TUE.c.task_id == task_id)
    ).one()  # 聚合恒返回一行——one() 免 Optional（pyright 债批）
    return {
        "task": task_public(task),
        "contracts": [task_contract_public(c) for c in contracts],
        "usage": {
            "input_tokens": agg[0],
            "output_tokens": agg[1],
            "cache_read_tokens": agg[2],
            "cache_write_tokens": agg[3],
            "events": agg[4],
        },
        "worktree": worktree_public(dict(worktree)) if worktree is not None else None,
    }


@router.get("/tasks/{task_id}/diff", response_model=daemon.DiffPayload)
def get_task_diff(
    task_id: str,
    request: Request,
    tx: Tx = Depends(get_tx),
    base: str | None = None,
) -> Any:
    """同一 worktree 的 Git diff 只读代理（契约 B §12.7 / D §6）。"""
    task = _require_task(tx, task_id)
    row = (
        tx.conn.execute(
            select(_WORKTREE, _PROJECT.c.repo_path, _PROJECT.c.computer_id)
            .select_from(_WORKTREE.join(_PROJECT, _WORKTREE.c.project_id == _PROJECT.c.id))
            .where(
                _WORKTREE.c.task_id == task_id,
                _WORKTREE.c.project_id == task["project_id"],
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "任务尚无可用 worktree")

    from coagentia_server.computers import DaemonOffline, GitQueryError

    query = daemon.GitDiffQuery(
        project_id=row["project_id"],
        repo_path=row["repo_path"],
        task_id=task_id,
        base=base,
    )
    try:
        payload = request.app.state.daemon_hub.query_git_diff(
            computer_id=row["computer_id"], query=query
        )
    except GitQueryError as exc:
        # daemon 在线但 git 查询失败（坏 base ref 等）→ 4xx 而非 503；文案透传 git prose（#5）。
        raise ApiError(
            422, rest.ErrorCode.VALIDATION_FAILED, str(exc), details={"base": base}
        ) from exc
    except DaemonOffline as exc:
        raise ApiError(
            503, rest.ErrorCode.DAEMON_OFFLINE, "daemon 离线或查询超时，无法读取 Diff"
        ) from exc
    return daemon.DiffPayload.model_validate(payload)


# ---------------------------------------------------------------- 预览域（M7 K3；契约 B §13.1）


def _preview_resource(tx: Tx, task_id: str, task: dict[str, Any]) -> dict[str, Any]:
    """预览宿主（挂 diff 同款资源）：任务 worktree 行 + 其 Project 的 dev_command/computer_id。
    无 worktree → 404（同 diff 拒绝路径）。"""
    row = (
        tx.conn.execute(
            select(_WORKTREE, _PROJECT.c.computer_id, _PROJECT.c.dev_command)
            .select_from(_WORKTREE.join(_PROJECT, _WORKTREE.c.project_id == _PROJECT.c.id))
            .where(
                _WORKTREE.c.task_id == task_id,
                _WORKTREE.c.project_id == task["project_id"],
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "任务尚无可用 worktree")
    return dict(row)


def _active_preview(tx: Tx, task_id: str) -> dict[str, Any] | None:
    """该任务的活跃预览行（starting/running；单活跃不变量下至多一行）。"""
    row = (
        tx.conn.execute(
            select(_PREVIEW)
            .where(_PREVIEW.c.task_id == task_id, _PREVIEW.c.status.in_(_PREVIEW_ACTIVE))
            .order_by(_PREVIEW.c.started_at.desc())
            .limit(1)
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def _read_preview(tx: Tx, preview_id: str) -> dict[str, Any]:
    return dict(
        tx.conn.execute(select(_PREVIEW).where(_PREVIEW.c.id == preview_id)).mappings().one()
    )


def _touch_preview(tx: Tx, preview_id: str, ts: str) -> None:
    """心跳/touch（裁决 8）：仅条件 UPDATE 推进 last_active_at（CAS 起态门——竞败/已终态
    rowcount=0 不覆盖终态）。last_active_at 是「面板还开着」的唯一诚实信号。"""
    tx.conn.execute(
        update(_PREVIEW)
        .where(_PREVIEW.c.id == preview_id, _PREVIEW.c.status.in_(_PREVIEW_ACTIVE))
        .values(last_active_at=ts)
    )


@router.post("/tasks/{task_id}/preview", response_model=entities.PreviewSessionPublic)
def ensure_preview(
    task_id: str, request: Request, response: Response, tx: Tx = Depends(get_tx)
) -> Any:
    """ensure+touch 幂等（裁决 8 / B §13.1）：无活跃预览 → 建 starting 行 + 下发 preview.start；
    已活跃 → 仅 touch last_active_at 返回现状（不重下发）。无 worktree 404 / daemon 离线 503 /
    Project 无 dev_command 422（details+hint）。"""
    task = _require_task(tx, task_id)
    res = _preview_resource(tx, task_id, task)  # 404 无 worktree
    dev_command = res["dev_command"]
    if not dev_command or not dev_command.strip():
        raise ApiError(
            422,
            rest.ErrorCode.VALIDATION_FAILED,
            "任务所属 Project 未配置 dev_command，无法启动预览",
            rule="B§13.1",
            details={
                "project_id": res["project_id"],
                "hint": "先在 Project 设置里配置 dev_command 再打开预览",
            },
        )
    computer_id = res["computer_id"]
    hub = request.app.state.daemon_hub
    # 503 早探（不建行）：daemon 离线直接拒，避免建 starting 孤行（判定归 server）。
    if not hub.preview_daemon_online(computer_id):
        raise ApiError(503, rest.ErrorCode.DAEMON_OFFLINE, "daemon 离线，无法启动预览")

    ts = tasks_service.service.now_iso()
    active = _active_preview(tx, task_id)
    if active is not None:
        _touch_preview(tx, active["id"], ts)  # touch 不重下发 preview.start
        response.status_code = 200
        return preview_session_public(_read_preview(tx, active["id"]))

    # 无活跃 → 建 starting 行（部分唯一索引兜底并发双 POST）+ 下发 preview.start（裁决 8）。
    session_id = tasks_service.service.new_ulid()
    try:
        # SAVEPOINT 包裹建行：并发双 POST 抢先时单活跃部分唯一索引触发 IntegrityError——只回退
        # 本段，退化为回读现有活跃行 touch 返回（恰一行）。
        with tx.conn.begin_nested():
            tx.conn.execute(
                insert(_PREVIEW).values(
                    id=session_id,
                    workspace_id=res["workspace_id"],
                    task_id=task_id,
                    worktree_id=res["id"],
                    port=None,
                    status="starting",
                    fail_log_tail=None,
                    started_at=ts,
                    last_active_at=ts,
                    recycled_at=None,
                )
            )
    except IntegrityError:
        existing = _active_preview(tx, task_id)
        if existing is None:  # 非单活跃索引冲突（防御：其它完整性错误不吞）
            raise
        _touch_preview(tx, existing["id"], ts)
        response.status_code = 200
        return preview_session_public(_read_preview(tx, existing["id"]))

    new_row = _read_preview(tx, session_id)
    tx.emit(
        tasks_service.EventType.PREVIEW_UPDATED,
        task["channel_id"],
        {"preview": preview_session_public(new_row)},
    )
    # 下发 preview.start：**提交后**（tx.after_commit）才发——running 帧的 CAS（WHERE status=
    # 'starting'）须命中已提交的 starting 行，否则「下发先于建行提交」窗口下会丢 running 帧
    # （DEV-PLAN §2 CAS 纪律；Fable 亲修）。start_data 闭包捕获，daemon 起进程健康检查后的 running
    # 帧必晚于本行提交到达。
    start_data = daemon.PreviewStartData(
        preview_session_id=session_id,
        task_id=task_id,
        worktree_path=res["path"],
        dev_command=dev_command,
    )
    tx.after_commit(
        lambda: hub.request_preview_start(computer_id=computer_id, task_id=task_id, data=start_data)
    )
    response.status_code = 201
    return preview_session_public(new_row)


@router.get("/tasks/{task_id}/preview", response_model=entities.PreviewSessionPublic)
def get_preview(task_id: str, tx: Tx = Depends(get_tx)) -> Any:
    """纯读现状（无写副作用；不推进 last_active_at）：活跃行优先，否则最近一条会话，皆无 → 404。"""
    _require_task(tx, task_id)
    active = _active_preview(tx, task_id)
    if active is not None:
        return preview_session_public(active)
    recent = (
        tx.conn.execute(
            select(_PREVIEW)
            .where(_PREVIEW.c.task_id == task_id)
            .order_by(_PREVIEW.c.started_at.desc())
            .limit(1)
        )
        .mappings()
        .first()
    )
    if recent is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "任务尚无预览会话")
    return preview_session_public(dict(recent))


@router.delete("/tasks/{task_id}/preview", response_model=entities.PreviewSessionPublic)
def stop_preview(task_id: str, request: Request, tx: Tx = Depends(get_tx)) -> Any:
    """下发 preview.stop（回收）：判定归 server（此处判「有活跃预览」），执行归 daemon（recycled 经
    preview.status 上报确认，行留存供诊断——不在此改态）。无活跃会话 → 404。"""
    _require_task(tx, task_id)
    active = _active_preview(tx, task_id)
    if active is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "无活跃预览会话可回收")
    # 目标 Computer 取自预览自持 worktree（避免任务 Project 改绑造成的错投）。
    computer_id = tx.conn.execute(
        select(_PROJECT.c.computer_id)
        .select_from(_WORKTREE.join(_PROJECT, _WORKTREE.c.project_id == _PROJECT.c.id))
        .where(_WORKTREE.c.id == active["worktree_id"])
    ).scalar_one_or_none()
    if computer_id is not None:
        request.app.state.daemon_hub.request_preview_stop(
            computer_id=computer_id, task_id=task_id, preview_session_id=active["id"]
        )
    return preview_session_public(active)


@router.patch("/tasks/{task_id}", response_model=entities.TaskPublic)
def patch_task(
    task_id: str, body: rest.TaskPatch, request: Request, tx: Tx = Depends(get_tx)
) -> Any:
    task = _require_task(tx, task_id)
    acting_member(request, tx.conn)  # 身份校验（R4 无角色门）
    # exclude_unset 区分「未提供」与「显式 null」：显式 null 仅对可清列落库清除（D5 任务级
    # 覆盖 silence_override_h 须能重置回 NULL）；其余列的 null 保持旧行为忽略（title 不被清空）。
    provided = body.model_dump(exclude_unset=True)
    _NULLABLE_CLEARABLE = {"silence_override_h"}
    changes: dict[str, Any] = {}
    for k, v in provided.items():
        if v is None and k not in _NULLABLE_CLEARABLE:
            continue
        changes[k] = v
    if "level" in changes:  # P-2 升格：仅 l1→l2 单向放行（拍板）
        cur_level = TaskLevel(task["level"])
        new_level = TaskLevel(changes["level"])
        if new_level == cur_level:
            changes.pop("level")  # l1→l1 / l2→l2 幂等无变更
        elif not (cur_level == TaskLevel.L1 and new_level == TaskLevel.L2):
            raise ApiError(
                422,
                rest.ErrorCode.TASK_TRANSITION_INVALID,
                f"任务升格不支持 {cur_level.value} → {new_level.value}",
                rule="D1",
                details={"from": cur_level.value, "to": new_level.value},
            )
        # T7 不变量守护（review 修复）：升格 l1→l2 若任务已在 in_review，须补齐 handoff——
        # 否则可借"先置 in_review（l1 无 T7）再升 l2"绕过 T7 门，造出 l2+in_review 无交接的态。
        elif TaskStatus(task["status"]) == TaskStatus.IN_REVIEW:
            missing = contracts_service.active_handoff_missing(tx.conn, task_id)
            if missing:
                raise ApiError(
                    422,
                    rest.ErrorCode.HANDOFF_INCOMPLETE,
                    f"升格为 L2 前须补齐交接材料（任务已在 In Review）：{', '.join(missing)}",
                    rule="T7",
                    details={
                        "missing": missing,
                        "hint": "用 submit_task_contract 工具提交 kind=task_handoff 补齐 "
                        f"{', '.join(missing)}（deliverables 须≥1）。",
                    },
                )
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


# ---------------------------------------------------------------- 契约域（M3a E2）


@router.get("/tasks/{task_id}/contracts", response_model=list[entities.TaskContractPublic])
def list_task_contracts(task_id: str, tx: Tx = Depends(get_tx)) -> Any:
    """全部契约行（含历史）；前端按 superseded_at 分活动/历史（B §4.3）。"""
    _require_task(tx, task_id)
    rows = contracts_service.active_contracts(tx.conn, task_id)
    return [task_contract_public(r) for r in rows]


@router.post(
    "/tasks/{task_id}/contracts", response_model=entities.TaskContractPublic, status_code=201
)
def submit_task_contract(
    task_id: str, body: rest.ContractCreate, request: Request, tx: Tx = Depends(get_tx)
) -> Any:
    """提交/修订契约（B §4.3）：kind 对应 body 模型二次校验 + 同 (task_id, kind) 修订链。

    kind≠schema 或字段不符 → 422 VALIDATION_FAILED；有活动同 kind 行则 supersede + revision+1
    （task_contract.updated），否则新建 revision=1（task_contract.created）。
    """
    task = _require_task(tx, task_id)
    me = acting_member(request, tx.conn)
    if body.kind not in constants.TASK_CONTRACT_KINDS:  # loop_contract 属 Reminder 域（M4）
        raise ApiError(
            422,
            rest.ErrorCode.VALIDATION_FAILED,
            f"任务契约仅支持 TaskPlan/TaskHandoff（{body.kind.value} 属 Reminder 域）",
            details={"kind": body.kind.value},
        )
    model = rest.CONTRACT_BODY_MODELS.get(body.kind)
    if model is None:  # 理论不可达（ContractKind 枚举全集已在映射表登记），防御兜底
        raise ApiError(
            422,
            rest.ErrorCode.VALIDATION_FAILED,
            f"未知契约 kind：{body.kind}",
            details={"kind": str(body.kind)},
        )
    try:
        validated = model.model_validate(body.body)
    except ValidationError as exc:
        raise ApiError(
            422,
            rest.ErrorCode.VALIDATION_FAILED,
            "契约内容与 kind 对应 schema 不符",
            details={
                "kind": body.kind.value,
                "errors": [
                    {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]}
                    for e in exc.errors()
                ],
            },
        ) from exc
    row, is_revision = contracts_service.submit_contract(
        tx,
        task_id=task_id,
        workspace_id=task["workspace_id"],
        kind=body.kind,
        body_dict=validated.model_dump(mode="json"),
        created_by=me["id"],
    )
    pub = task_contract_public(row)
    event_type = (
        tasks_service.EventType.TASK_CONTRACT_UPDATED
        if is_revision
        else tasks_service.EventType.TASK_CONTRACT_CREATED
    )
    tx.emit(event_type, task["channel_id"], {"contract": pub})
    if contracts_service.should_notify_needs_human(tx.conn, row):
        humans = messages_service.channel_human_members(tx.conn, task["channel_id"])
        mentions = " ".join(f"@{human['name']}" for human in humans)
        suffix = f"：{mentions}" if mentions else "。"
        messages_service.post_system_message(
            tx,
            workspace_id=task["workspace_id"],
            channel_id=task["channel_id"],
            thread_root_id=task["root_message_id"],
            body=(
                f"评审需人裁决：task #{task['number']}「{task['title']}」的 TaskHandoff "
                f"已标记 review_verdict=needs_human，请频道人类成员裁决{suffix}"
            ),
            mention_member_ids=[human["id"] for human in humans],
        )
    return pub


@router.post("/tasks/{task_id}/contracts/request-draft", status_code=202)
def request_contract_draft(
    task_id: str, body: rest.ContractDraftRequest, request: Request, tx: Tx = Depends(get_tx)
) -> Any:
    """让 @Agent 起草契约（B §4.3；P-3）：S1 定向直投；daemon 离线 → 503 DAEMON_OFFLINE。"""
    _require_task(tx, task_id)
    agent = (
        tx.conn.execute(
            select(_MEMBER).where(
                _MEMBER.c.id == body.agent_member_id,
                _MEMBER.c.kind == MemberKind.AGENT,
                _MEMBER.c.removed_at.is_(None),
            )
        )
        .mappings()
        .first()
    )
    if agent is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "起草请求目标 Agent 不存在")

    from coagentia_server.computers import DaemonOffline

    hub = request.app.state.daemon_hub
    try:
        hub.inject_contract_draft_request(
            agent_member_id=body.agent_member_id, task_id=task_id, kind=body.kind
        )
    except DaemonOffline as exc:
        raise ApiError(503, rest.ErrorCode.DAEMON_OFFLINE, "daemon 离线，无法投递起草请求") from exc
    return {"status": "accepted"}
