"""4.10 编排（契约 B §4.10/§12.1）：拆解触发三入口归一 `POST /channels/{id}/decompose` +
`GET /proposals/{id}`。confirm/reject 端点与落地执行归 J9，本模块不实现。

裁量（J8 回报登记）：decompose 成功 → **202 + ProposalPublic**（对齐 J0 mock/OpenAPI 形状源）；
上下文注入 = S1 直投（daemon 离线 → 503 DAEMON_OFFLINE，事务回滚故 drafting 提案不落库）。
"""

from __future__ import annotations

from typing import Any

from coagentia_contracts import entities, rest
from coagentia_contracts.enums import ChannelKind, MessageKind
from coagentia_contracts.ws import EventType
from fastapi import APIRouter, Depends, Request
from sqlalchemy import insert, select

from coagentia_server.api import ApiError
from coagentia_server.db import models
from coagentia_server.deps import Tx, acting_member, get_tx, require_workspace
from coagentia_server.ledger import service
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
