"""4.10 编排（契约 B §4.10/§12.1/§12.4/§5 S2）：拆解触发三入口归一 `POST /channels/{id}/decompose`
+ `GET /proposals/{id}` + **J9 confirm CAS / reject**。

裁量（J8 回报登记）：decompose 成功 → **202 + ProposalPublic**（对齐 J0 mock/OpenAPI 形状源）；
上下文注入 = S1 直投（daemon 离线 → 503 DAEMON_OFFLINE，事务回滚故 drafting 提案不落库）。

J9 裁量：confirm/reject 仅人类主体（Agent 403 rule=O9——确认/拒绝是人类面：Agent 结构变更唯一
通道 = `<control>` 提案本身，Agent 自确认会架空确认门；直落是唯一合法自动路径）；kind=delta 两
端点均 422（J10 接，防半实现语义泄漏）；STALE_CONFIRM 409 响应 = `{error, latest}` 双顶层键
（B §5 ① 原文形状——ApiError 只产 error 单键，故此处直接构造 JSONResponse）。
"""

from __future__ import annotations

from typing import Any

from coagentia_contracts import entities, rest
from coagentia_contracts.enums import (
    ChannelKind,
    MemberKind,
    MessageKind,
    ProposalKind,
    ProposalStatus,
)
from coagentia_contracts.kernel.decomposition import Env, proposal_fingerprint, validate_proposal
from coagentia_contracts.ws import EventType
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import insert, select

from coagentia_server.api import ApiError
from coagentia_server.canvas import service as canvas_service
from coagentia_server.db import models
from coagentia_server.deps import Tx, acting_member, get_tx, require_workspace
from coagentia_server.ledger import service
from coagentia_server.orchestration import draft as draft_domain
from coagentia_server.orchestration import proposal as proposal_domain
from coagentia_server.routes.serialize import message_public, proposal_public
from coagentia_server.tasks import service as tasks_service

router = APIRouter(prefix="/api", tags=["proposals"])

_CHANNEL = models.tbl(models.Channel)
_MSG = models.tbl(models.Message)
_TASK = models.tbl(models.Task)


def _require_channel(tx: Tx, channel_id: str) -> dict[str, Any]:
    row = tx.conn.execute(select(_CHANNEL).where(_CHANNEL.c.id == channel_id)).mappings().first()
    if row is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "频道不存在")
    return dict(row)


def _resolve_source_task(
    tx: Tx, *, workspace_id: str, channel: dict[str, Any], body: rest.DecomposeRequest,
    requester_id: str,
) -> dict[str, Any]:
    """归一化 source 任务（拆解设计 §4）：task_id → 直取并校验属本频道；text → 系统代发需求消息
    + 转任务（复用消息转任务链 create_task）。"""
    if body.task_id is not None:
        task = tx.conn.execute(
            select(_TASK).where(_TASK.c.id == body.task_id)
        ).mappings().first()
        if task is None or task["channel_id"] != channel["id"]:
            raise ApiError(404, rest.ErrorCode.NOT_FOUND, "source 任务不存在或不在本频道")
        return dict(task)

    # text：代发需求消息（author=请求者、顶级 user 消息）→ 广播 message.created → create_task。
    # 直接落库不复用 persist_message，避免 @mention 解析误唤醒他人与 T1 递归自触发。
    assert body.text is not None
    ts = service.now_iso()
    msg_id = service.new_ulid()
    tx.conn.execute(
        insert(_MSG).values(
            id=msg_id,
            workspace_id=workspace_id,
            channel_id=channel["id"],
            thread_root_id=None,
            author_member_id=requester_id,
            kind=MessageKind.USER,
            card_kind=None,
            card_ref=None,
            body=body.text,
            created_at=ts,
        )
    )
    msg = models.row_dict(
        tx.conn.execute(select(_MSG).where(_MSG.c.id == msg_id)).mappings().first()
    )
    tx.emit(EventType.MESSAGE_CREATED, channel["id"], {"message": message_public(msg, [])})
    source_task = tasks_service.create_task(
        tx,
        workspace_id=workspace_id,
        channel_id=channel["id"],
        root_message_id=msg_id,
        created_by=requester_id,
        source_body=body.text,
    )
    tasks_service.emit_task_created(tx, source_task)
    return source_task


@router.post(
    "/channels/{channel_id}/decompose",
    response_model=entities.ProposalPublic,
    status_code=202,
)
def decompose(
    channel_id: str, body: rest.DecomposeRequest, request: Request, tx: Tx = Depends(get_tx)
) -> Any:
    """拆解触发三入口归一（B §12.1；T1/T2/T3 均锚定一个 source 任务）。

    无 Orchestrator（频道成员中无 role_template_key='orchestrator' 未软删 Agent）→ 409
    NO_ORCHESTRATOR；Orchestrator 所在 daemon 离线 → 503 DAEMON_OFFLINE。成功 → 建 drafting
    提案 + 唤醒注入上下文 → 202 ProposalPublic。
    """
    ws = require_workspace(tx.conn)
    channel = _require_channel(tx, channel_id)
    if channel.get("archived_at"):
        raise ApiError(409, rest.ErrorCode.CHANNEL_ARCHIVED, "归档频道不可拆解", rule="FR-1.3")
    if channel["kind"] == ChannelKind.DM.value:
        raise ApiError(422, rest.ErrorCode.TASK_IN_DM, "DM 不承载任务，无法拆解", rule="FR-5.1")
    me = acting_member(request, tx.conn)

    orchestrator = proposal_domain.find_orchestrator(tx.conn, channel_id)
    if orchestrator is None:
        raise ApiError(
            409,
            rest.ErrorCode.NO_ORCHESTRATOR,
            "本频道无可用 Orchestrator（先创建 Orchestrator 角色 Agent 并加入频道）",
        )

    source_task = _resolve_source_task(
        tx, workspace_id=ws["id"], channel=channel, body=body, requester_id=me["id"]
    )
    proposal, inject = proposal_domain.initiate_proposal(
        tx,
        workspace_id=ws["id"],
        channel=channel,
        source_task=source_task,
        orchestrator=orchestrator,
        requester_id=me["id"],
    )
    # 上下文注入（strict）：daemon 离线 → 503，异常冒泡回滚（drafting 提案/需求消息均不落库）。
    from coagentia_server.computers import DaemonOffline

    hub = request.app.state.daemon_hub
    try:
        hub.inject_orchestrator(
            inject.agent_member_id, inject.body, kind=inject.kind, ref=inject.ref
        )
    except DaemonOffline as exc:
        raise ApiError(
            503, rest.ErrorCode.DAEMON_OFFLINE, "Orchestrator 所在 daemon 离线，无法注入拆解上下文"
        ) from exc
    return proposal_public(proposal)


@router.get("/proposals/{proposal_id}", response_model=entities.ProposalPublic)
def get_proposal(proposal_id: str, tx: Tx = Depends(get_tx)) -> Any:
    """提案与生命周期状态（草稿层渲染源，B §4.10）。"""
    proposal = proposal_domain.fetch_proposal(tx.conn, proposal_id)
    if proposal is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "提案不存在")
    return proposal_public(proposal)


# ---------------------------------------------------------------- J9：confirm CAS / reject


def _confirmable_proposal(
    tx: Tx, proposal_id: str, request: Request
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """confirm/reject 共用前置门：404 → Agent 403（O9）→ delta 422 → (proposal, canvas, me)。"""
    proposal = proposal_domain.fetch_proposal(tx.conn, proposal_id)
    if proposal is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "提案不存在")
    me = acting_member(request, tx.conn)
    if me["kind"] == MemberKind.AGENT.value:
        raise ApiError(
            403,
            rest.ErrorCode.PERMISSION_DENIED,
            "草稿确认/拒绝是人类操作（Agent 结构变更通道 = <control> 提案；直落归频道策略）",
            rule="O9",
        )
    if proposal["kind"] == ProposalKind.DELTA.value:
        raise ApiError(
            422,
            rest.ErrorCode.VALIDATION_FAILED,
            "delta 提案的确认/拒绝随增量变更批次提供（J10）",
            rule="B§12.4",
        )
    canvas = canvas_service.fetch_canvas_by_channel(tx.conn, proposal["channel_id"])
    assert canvas is not None  # 非 DM 频道建频即有画布；提案只在频道内产生
    return proposal, canvas, me


def _stale_response(tx: Tx, proposal: dict[str, Any], canvas: dict[str, Any]) -> JSONResponse:
    """409 STALE_CONFIRM `{error, latest}` 双顶层键（B §5 ①/02 §1.3a）——客户端刷新草稿重审。"""
    body = rest.ErrorBody(
        code=rest.ErrorCode.STALE_CONFIRM,
        message="提案或画布基线已变化，请刷新最新态后重审",
        rule="S2",
        details=None,
    )
    return JSONResponse(
        status_code=409,
        content={
            "error": body.model_dump(),
            "latest": draft_domain.stale_latest(tx.conn, proposal, canvas),
        },
    )


@router.post(
    "/proposals/{proposal_id}/confirm",
    response_model=rest.ProposalConfirmResult,
    status_code=202,
)
def confirm_proposal(
    proposal_id: str, body: rest.ProposalConfirm, request: Request, tx: Tx = Depends(get_tx)
) -> Any:
    """草稿确认 CAS（B §5 逐条）：① expected 三字段比对（任一不符/非 awaiting → 409 STALE_CONFIRM
    携最新态）→ ② 调整应用 + 结果图权威全量重验（env 现时重取——成员/绑定可能已变；失败 422 携
    V 系错误清单）→ ③ landed_hash 落账 + 转 landing + 建 decomp 批 → 202 {batch, proposal}。

    **不建任何画布节点**：落地 = 异步增量执行（orchestration/landing.py 执行器领批），
    `landing.completed` 事件收尾（B §5）。
    """
    proposal, canvas, me = _confirmable_proposal(tx, proposal_id, request)
    if proposal["status"] != ProposalStatus.AWAITING_CONFIRM.value:
        return _stale_response(tx, proposal, canvas)

    expected = body.expected
    if (
        expected.proposal_hash != proposal["proposal_hash"]
        or expected.baseline_version != canvas["baseline_version"]
        or expected.baseline_hash != canvas["baseline_hash"]
    ):
        return _stale_response(tx, proposal, canvas)

    if body.removed_ops:
        raise ApiError(
            422,
            rest.ErrorCode.VALIDATION_FAILED,
            "removed_ops 仅用于 delta 部分接受（J10）；full 提案确认不接受该字段",
            rule="B§12.4",
            details={"field": "removed_ops"},
        )

    # ② 调整应用（op 形状违例 422，draft.apply_adjustments 抛）→ 权威全量重验。
    adjustments = list(body.adjustments)
    adjusted = draft_domain.apply_adjustments(proposal["body"], adjustments)
    channel = _require_channel(tx, proposal["channel_id"])
    env = Env(
        node_limit=int(channel.get("decomp_node_limit") or 12),
        member_ids=proposal_domain.channel_member_ids(tx.conn, channel["id"]),
        bound_project_ids=proposal_domain.bound_project_ids(tx.conn, channel["id"]),
    )
    errors = validate_proposal(adjusted, env)
    if errors:
        raise ApiError(
            422,
            rest.ErrorCode.VALIDATION_FAILED,
            "调整后提案未通过校验",
            rule="B§12.4",
            details={"errors": [dict(e) for e in errors]},
        )

    # ③ 落账 + 转 landing + 建批（202；节点创建归异步执行器）。条件转移竞败（并发对手已推进
    # 状态——pysqlite 读自动提交，Python 侧状态检查可被过期读骗过）→ 409 STALE 携最新态。
    landed_hash = proposal_fingerprint(adjusted)
    try:
        batch, refreshed = draft_domain.confirm_apply(
            tx,
            proposal=proposal,
            adjustments=adjustments,
            landed_hash=landed_hash,
            confirmed_by=me["id"],
        )
    except draft_domain.StaleTransition:
        fresh = proposal_domain.fetch_proposal(tx.conn, proposal_id)
        assert fresh is not None
        return _stale_response(tx, fresh, canvas)
    return {"batch": batch.model_dump(mode="json"), "proposal": proposal_public(refreshed)}


@router.post("/proposals/{proposal_id}/reject", response_model=entities.ProposalPublic)
def reject_proposal(
    proposal_id: str, body: rest.ProposalReject, request: Request, tx: Tx = Depends(get_tx)
) -> Any:
    """拒绝草稿（B §12.4 #2 / 拆解设计 §8.2）：仅 awaiting_confirm（其余状态 409 STALE_CONFIRM 携
    最新态——客户端由 latest 看见结局）；理由发 source 线程（无理由也留痕）；提案转 rejected 终态。
    """
    proposal, canvas, _me = _confirmable_proposal(tx, proposal_id, request)
    if proposal["status"] != ProposalStatus.AWAITING_CONFIRM.value:
        return _stale_response(tx, proposal, canvas)
    try:
        rejected = draft_domain.reject_proposal(tx, proposal=proposal, reason=body.reason)
    except draft_domain.StaleTransition:
        fresh = proposal_domain.fetch_proposal(tx.conn, proposal_id)
        assert fresh is not None
        return _stale_response(tx, fresh, canvas)
    return proposal_public(rejected)
