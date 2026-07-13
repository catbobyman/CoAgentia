"""提案域（拆解设计 §3–§9 / 契约 B §12.1–§12.3）——J8 生命周期状态机 + 触发归一 + 上下文注入
+ 提案消息解析挂接 + 修复循环 + Superseded 管理。

**J8 边界**（M6-HANDOFF §4）：实现到 awaiting_confirm/failed/superseded + 直落分支「校验通过→
status='landing'」为止；awaiting_confirm→landing/landed/rejected（confirm/reject 端点）与落地执行器
归 J9，本模块不实现。

判断归模型、控制归代码：Orchestrator（Agent）只发消息（含 <control> 块），本模块做确定性校验、
状态机、幂等、留痕。校验内核单源 = contracts.kernel.decomposition（py 权威 + ts 镜像 + golden 双跑，
纪律 8）；本模块只消费 parse_control/validate_proposal/proposal_fingerprint，不重写判定。

daemon I/O 分层：本模块纯 DB/逻辑，产出 `PendingInject` 清单由调用方（persist_message / hub）经
daemon_hub.inject_orchestrator 投递（S1 直投，契约 D §5.2）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from coagentia_contracts.constants import (
    SCHEMA_DECOMPOSITION_DELTA_V1,
    SCHEMA_DECOMPOSITION_ERRORS_V1,
)
from coagentia_contracts.enums import (
    CardKind,
    ChannelKind,
    DecompMode,
    InjectKind,
    MemberKind,
    ProposalKind,
    ProposalStatus,
)
from coagentia_contracts.kernel.decomposition import (
    Env,
    parse_control,
    proposal_fingerprint,
    validate_proposal,
)
from coagentia_contracts.ws import EventType
from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from coagentia_server.canvas import service as canvas_service
from coagentia_server.db import models
from coagentia_server.ledger.service import new_ulid, now_iso
from coagentia_server.orchestration import delta as delta_domain
from coagentia_server.routes.serialize import proposal_public

_PROPOSAL = models.tbl(models.Proposal)
_TASK = models.tbl(models.Task)
_CHANNEL = models.tbl(models.Channel)
_MSG = models.tbl(models.Message)
_MEMBER = models.tbl(models.Member)
_AGENT = models.tbl(models.Agent)
_CHANNEL_MEMBER = models.tbl(models.ChannelMember)
_ROLE_TEMPLATE = models.tbl(models.AgentRoleTemplate)
_CHANNEL_PROJECT = models.tbl(models.ChannelProject)
_PROJECT = models.tbl(models.Project)
_CANVAS = models.tbl(models.Canvas)
_NODE = models.tbl(models.CanvasNode)
_DIAG = models.tbl(models.DiagnosticEvent)

ORCHESTRATOR_ROLE_KEY = "orchestrator"

# ---------------------------------------------------------------- 状态机（TASK_TRANSITIONS 纪律）

# 拆解设计 §3 生命周期。J8 只驱动到 awaiting_confirm/failed/superseded + validating→landing（直落）
# awaiting_confirm→landing/landed/rejected 与 landing→landed 的落地执行归 J9（表含边、本模块不走）
PROPOSAL_TRANSITIONS: dict[ProposalStatus, frozenset[ProposalStatus]] = {
    ProposalStatus.DRAFTING: frozenset({ProposalStatus.VALIDATING, ProposalStatus.SUPERSEDED}),
    ProposalStatus.VALIDATING: frozenset({
        ProposalStatus.REPAIRING, ProposalStatus.AWAITING_CONFIRM,
        ProposalStatus.LANDING, ProposalStatus.FAILED, ProposalStatus.SUPERSEDED,
    }),
    ProposalStatus.REPAIRING: frozenset({
        ProposalStatus.VALIDATING, ProposalStatus.FAILED, ProposalStatus.SUPERSEDED,
    }),
    ProposalStatus.AWAITING_CONFIRM: frozenset({
        ProposalStatus.LANDING, ProposalStatus.LANDED,
        ProposalStatus.REJECTED, ProposalStatus.SUPERSEDED,
    }),
    ProposalStatus.LANDING: frozenset({ProposalStatus.LANDED, ProposalStatus.FAILED}),
    ProposalStatus.LANDED: frozenset({ProposalStatus.AWAITING_CONFIRM}),  # delta（J10）
    ProposalStatus.SUPERSEDED: frozenset(),
    ProposalStatus.REJECTED: frozenset(),
    ProposalStatus.FAILED: frozenset(),
}

TERMINAL_STATUSES: frozenset[ProposalStatus] = frozenset({
    ProposalStatus.LANDED, ProposalStatus.SUPERSEDED,
    ProposalStatus.REJECTED, ProposalStatus.FAILED,
})
# 契约 A §4.8 部分唯一索引的 sqlite_where 值（同 source 单一非终态提案）。
_TERMINAL_VALUES: tuple[str, ...] = tuple(s.value for s in TERMINAL_STATUSES)

MAX_REPAIRS = 2  # O7：每 revision 至多 2 轮修复（第 3 次校验失败 → failed）
AWAITING_CONFIRM_REMIND_HOURS = 24.0  # F5：草稿超 24h 无人确认 → @提案请求者（阈值常量）
THREAD_SUMMARY_RECENT_N = 5  # §4：线程摘要 = 首条 + 最近 N 条（有界注入）

# 诊断类型（DIAGNOSTIC_TYPES 已登记；J8 新增 duplicate_ignored/awaiting_reminder_sent）。
DIAG_REQUESTED = "decomp.requested"
DIAG_CONTEXT_INJECTED = "decomp.context_injected"
DIAG_DRAFTED = "proposal.drafted"
DIAG_VALIDATION_FAILED = "proposal.validation_failed"
DIAG_REPAIR_ATTEMPT = "proposal.repair_attempt"
DIAG_FAILED_ESCALATED = "proposal.failed_escalated"
DIAG_DRAFT_PRESENTED = "draft.presented"
DIAG_DRAFT_SUPERSEDED = "draft.superseded"
DIAG_DUPLICATE_IGNORED = "proposal.duplicate_ignored"
DIAG_AWAITING_REMINDER = "proposal.awaiting_reminder_sent"


class NoOrchestrator(Exception):
    """频道无 role_template_key='orchestrator' 的未软删 Agent（→ 路由 409 NO_ORCHESTRATOR）。"""


@dataclass
class PendingInject:
    """待投递的 S1 直投（由调用方经 daemon_hub.inject_orchestrator 投递；best_effort=离线容忍）。"""

    agent_member_id: str
    body: str
    kind: InjectKind
    ref: str | None
    best_effort: bool = True


# ---------------------------------------------------------------- 查询助手


def find_orchestrator(conn: Connection, channel_id: str) -> dict[str, Any] | None:
    """频道成员中 role_template_key='orchestrator' 的未软删 Agent（B §4.10 判定）。稳定按 id。"""
    row = conn.execute(
        select(_AGENT.c.member_id, _MEMBER.c.name)
        .select_from(
            _AGENT.join(_MEMBER, _AGENT.c.member_id == _MEMBER.c.id).join(
                _CHANNEL_MEMBER, _CHANNEL_MEMBER.c.member_id == _AGENT.c.member_id
            )
        )
        .where(
            _CHANNEL_MEMBER.c.channel_id == channel_id,
            _MEMBER.c.kind == MemberKind.AGENT,
            _MEMBER.c.removed_at.is_(None),
            _AGENT.c.role_template_key == ORCHESTRATOR_ROLE_KEY,
        )
        .order_by(_MEMBER.c.id)
        .limit(1)
    ).mappings().first()
    return dict(row) if row is not None else None


def channel_member_ids(conn: Connection, channel_id: str) -> list[str]:
    """本频道未软删成员 id 集（kernel env.member_ids / V11 suggested_owner 校验用）。"""
    rows = conn.execute(
        select(_MEMBER.c.id)
        .select_from(_CHANNEL_MEMBER.join(_MEMBER, _CHANNEL_MEMBER.c.member_id == _MEMBER.c.id))
        .where(_CHANNEL_MEMBER.c.channel_id == channel_id, _MEMBER.c.removed_at.is_(None))
        .order_by(_MEMBER.c.id)
    ).scalars()
    return list(rows)


def bound_project_ids(conn: Connection, channel_id: str) -> list[str]:
    """已绑定本频道的 Project id 集（kernel env.bound_project_ids / V12 校验用）。"""
    rows = conn.execute(
        select(_CHANNEL_PROJECT.c.project_id)
        .where(_CHANNEL_PROJECT.c.channel_id == channel_id)
        .order_by(_CHANNEL_PROJECT.c.project_id)
    ).scalars()
    return list(rows)


def orchestrator_prompt_sections(conn: Connection) -> list[dict[str, Any]]:
    """从 agent_role_templates 行读 prompt_sections（**数据不是代码**：升级模板行即升级全部
    Orchestrator）。无行 → 空列表（降级为不注入角色段，仍注入任务上下文）。"""
    sections = conn.execute(
        select(_ROLE_TEMPLATE.c.prompt_sections).where(
            _ROLE_TEMPLATE.c.key == ORCHESTRATOR_ROLE_KEY
        )
    ).scalar()
    if isinstance(sections, list):
        return [s for s in sections if isinstance(s, dict)]
    return []


def fetch_proposal(conn: Connection, proposal_id: str) -> dict[str, Any] | None:
    row = conn.execute(select(_PROPOSAL).where(_PROPOSAL.c.id == proposal_id)).mappings().first()
    return models.row_dict(row) if row is not None else None


def _fetch_task(conn: Connection, task_id: str) -> dict[str, Any] | None:
    row = conn.execute(select(_TASK).where(_TASK.c.id == task_id)).mappings().first()
    return dict(row) if row is not None else None


def active_proposal_for_source(conn: Connection, source_task_id: str) -> dict[str, Any] | None:
    """source 任务当前唯一非终态提案（部分唯一索引保证至多一行）。"""
    row = conn.execute(
        select(_PROPOSAL)
        .where(
            _PROPOSAL.c.source_task_id == source_task_id,
            _PROPOSAL.c.status.notin_(_TERMINAL_VALUES),
        )
    ).mappings().first()
    return models.row_dict(row) if row is not None else None


# ---------------------------------------------------------------- 诊断 / 事件


def write_diagnostic(
    tx: Any,
    diag_type: str,
    *,
    workspace_id: str,
    channel_id: str | None,
    task_id: str | None = None,
    agent_member_id: str | None = None,
    payload: dict[str, Any],
    created_at: str | None = None,
) -> None:
    """写一条 decomp.*/proposal.*/draft.* 诊断（命名空间守契约 A §4.6；拆解设计 §15）。"""
    tx.conn.execute(
        insert(_DIAG).values(
            workspace_id=workspace_id,
            agent_member_id=agent_member_id,
            type=diag_type,
            channel_id=channel_id,
            task_id=task_id,
            batch_id=None,
            payload=payload,
            created_at=created_at or now_iso(),
        )
    )


def _emit_proposal_updated(tx: Any, proposal: dict[str, Any]) -> None:
    tx.emit(
        EventType.PROPOSAL_UPDATED, proposal["channel_id"], {"proposal": proposal_public(proposal)}
    )


def _emit_draft_presented(tx: Any, proposal: dict[str, Any]) -> None:
    tx.emit(
        EventType.DRAFT_PRESENTED, proposal["channel_id"], {"proposal": proposal_public(proposal)}
    )


def _emit_draft_superseded(tx: Any, proposal: dict[str, Any]) -> None:
    tx.emit(
        EventType.DRAFT_SUPERSEDED,
        proposal["channel_id"],
        {"proposal_id": proposal["id"], "revision": proposal["revision"]},
    )


# ---------------------------------------------------------------- 状态迁移（单点执法）


class StaleTransition(Exception):
    """提案状态转移竞败信号（并行审计修复 SM-F1，语义同 draft.StaleTransition——现单源于此，
    draft 模块 re-export 保持既有引用面）：条件 UPDATE rowcount≠1 = 并发对手已推进状态。

    为什么 _transition 也必须条件 UPDATE（J9 教训的普遍化）：pysqlite 方言下 SELECT 在首个 DML 前
    跑在自动提交（无快照），classify phase1 的读与 phase2 的写之间、以及跨请求的读-写窗口里，
    内存 `proposal["status"]` 可以是过期值——无条件 UPDATE 会把终态行写回非终态（终态复活）或把
    landing 行踩成 superseded（落地被夺/二次落地）。WHERE status=<起态> 把合法边执法原子化到
    写锁获取时刻，竞败方 rowcount=0。"""


def _transition(tx: Any, proposal: dict[str, Any], to_status: ProposalStatus) -> dict[str, Any]:
    """合法边校验 + **条件 UPDATE**（WHERE status=起态）status/updated_at；返回刷新行。
    非法边 → ValueError（编码缺陷即抛）；竞败（起态已被并发推进）→ StaleTransition。"""
    frm = ProposalStatus(proposal["status"])
    if to_status not in PROPOSAL_TRANSITIONS[frm]:
        raise ValueError(f"非法提案状态迁移 {frm.value} → {to_status.value}")
    now = now_iso()
    res = tx.conn.execute(
        update(_PROPOSAL)
        .where(_PROPOSAL.c.id == proposal["id"], _PROPOSAL.c.status == frm.value)
        .values(status=to_status.value, updated_at=now)
    )
    if res.rowcount != 1:
        raise StaleTransition(proposal["id"])
    refreshed = fetch_proposal(tx.conn, proposal["id"])
    assert refreshed is not None
    return refreshed


# ---------------------------------------------------------------- 建 / supersede 提案


def _fingerprint_lenient(body: dict[str, Any]) -> str:
    """未通过校验的原始体也要能出稳定哈希（修复/revbump/failed 留痕用，非 A §2 契约指纹）。

    proposal_fingerprint 的前置（无 float、数组内无 null、temp_id 皆 str）只对零错误体成立；
    修复路径的输入恰是违反前置的体，裸调会 ValueError/TypeError → 未捕获 500 → 消息回滚、
    修复循环打不响。退化为规范 JSON 的 SHA-256：同体同哈希即可（此哈希只做审计/对账留痕，
    不参与 landed_hash / 基线比对）。"""
    try:
        return proposal_fingerprint(body)
    except (TypeError, ValueError):
        canon = json.dumps(
            body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        )
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _drafting_placeholder() -> tuple[dict[str, Any], str]:
    """drafting 占位（裁量）：body={}（空提案占位），proposal_hash=fingerprint({})（真 64-hex，
    满足 Sha256Hex 列约束 → ProposalPublic 可序列化；Orchestrator 提交真提案时整体替换）。"""
    body: dict[str, Any] = {}
    return body, proposal_fingerprint(body)


def create_drafting_proposal(
    tx: Any,
    *,
    workspace_id: str,
    channel_id: str,
    source_task_id: str,
    proposed_by: str,
    revision: int = 1,
) -> dict[str, Any]:
    body, phash = _drafting_placeholder()
    pid = new_ulid()
    ts = now_iso()
    tx.conn.execute(
        insert(_PROPOSAL).values(
            id=pid,
            workspace_id=workspace_id,
            channel_id=channel_id,
            source_task_id=source_task_id,
            kind=ProposalKind.FULL.value,
            revision=revision,
            status=ProposalStatus.DRAFTING.value,
            body=body,
            proposal_hash=phash,
            base_hash=None,
            landed_hash=None,
            adjustments=[],
            repair_count=0,
            proposed_by_member_id=proposed_by,
            created_at=ts,
            updated_at=ts,
        )
    )
    proposal = fetch_proposal(tx.conn, pid)
    assert proposal is not None
    return proposal


def _supersede(tx: Any, proposal: dict[str, Any]) -> dict[str, Any]:
    """单行 supersede（终态 + draft.superseded + proposal.updated + 诊断）——三处共用单点：
    supersede_active_proposals / 对话修正 rev+1（有效与无效版）。"""
    superseded = _transition(tx, proposal, ProposalStatus.SUPERSEDED)
    _emit_draft_superseded(tx, superseded)
    _emit_proposal_updated(tx, superseded)
    write_diagnostic(
        tx,
        DIAG_DRAFT_SUPERSEDED,
        workspace_id=superseded["workspace_id"],
        channel_id=superseded["channel_id"],
        task_id=superseded["source_task_id"],
        payload={"proposal_id": superseded["id"], "revision": superseded["revision"]},
    )
    return superseded


def supersede_active_proposals(
    tx: Any, source_task_id: str, *, exclude_id: str | None = None
) -> bool:
    """把 source 的非终态提案置 superseded（终态）+ draft.superseded + 诊断（§8.2 重触发）。

    supersede 先于新提案落库——旧行转终态即移出部分唯一索引，新活动行方可插入。
    返回是否「遇到 landing 行」（True = 落地执行中不可替换——调用方据此走「复用现行提案」而非
    建新行；拆解设计 §8.2 landing 不可被 supersede，PROPOSAL_TRANSITIONS[LANDING] 无此边）。

    CAS 语义（并行审计修复 SM-F1）：逐行条件转移，竞败（StaleTransition）→ 重取现势重试
    （首次 UPDATE 执行后本事务已持写意向锁，第二轮读即权威，循环必收敛）；重取见终态 → 跳过，
    见 landing → 计入返回值。
    """
    rows = tx.conn.execute(
        select(_PROPOSAL).where(
            _PROPOSAL.c.source_task_id == source_task_id,
            _PROPOSAL.c.status.notin_(_TERMINAL_VALUES),
        )
    ).mappings().all()
    saw_landing = False
    for row in rows:
        proposal = models.row_dict(row)
        if exclude_id is not None and proposal["id"] == exclude_id:
            continue
        for _attempt in range(3):
            if proposal is None or proposal["status"] in _TERMINAL_VALUES:
                break  # 并发对手已终态化——目标已达
            if proposal["status"] == ProposalStatus.LANDING.value:
                saw_landing = True
                break  # landing 不可 supersede（落地执行中）
            try:
                _supersede(tx, proposal)
                break
            except StaleTransition:
                proposal = fetch_proposal(tx.conn, proposal["id"])  # 重取现势再试
        else:  # pragma: no cover - 写锁下第二轮即权威，理论不可达
            raise AssertionError(f"supersede 竞态未收敛：{proposal}")
    return saw_landing


# ---------------------------------------------------------------- 上下文注入（§4/§13.2）


def _member_roster(conn: Connection, channel_id: str) -> list[str]:
    rows = conn.execute(
        select(
            _MEMBER.c.id, _MEMBER.c.name, _MEMBER.c.kind,
            _AGENT.c.runtime, _AGENT.c.description,
        )
        .select_from(
            _CHANNEL_MEMBER.join(_MEMBER, _CHANNEL_MEMBER.c.member_id == _MEMBER.c.id)
            .outerjoin(_AGENT, _AGENT.c.member_id == _MEMBER.c.id)
        )
        .where(_CHANNEL_MEMBER.c.channel_id == channel_id, _MEMBER.c.removed_at.is_(None))
        .order_by(_MEMBER.c.id)
    ).mappings()
    lines: list[str] = []
    for r in rows:
        if r["kind"] == MemberKind.AGENT.value:
            desc = (r["description"] or "").strip().replace("\n", " ")
            if len(desc) > 60:
                desc = desc[:60] + "…"
            lines.append(
                f"- member_id={r['id']} · {r['name']} · agent · runtime={r['runtime']}"
                + (f" · {desc}" if desc else "")
            )
        else:
            lines.append(f"- member_id={r['id']} · {r['name']} · human")
    return lines


def _project_roster(conn: Connection, channel_id: str) -> list[str]:
    rows = conn.execute(
        select(_PROJECT.c.id, _PROJECT.c.name, _PROJECT.c.dev_command, _PROJECT.c.deploy_command)
        .select_from(
            _CHANNEL_PROJECT.join(_PROJECT, _CHANNEL_PROJECT.c.project_id == _PROJECT.c.id)
        )
        .where(_CHANNEL_PROJECT.c.channel_id == channel_id)
        .order_by(_PROJECT.c.id)
    ).mappings()
    lines: list[str] = []
    for r in rows:
        dev = "有" if (r["dev_command"] or "").strip() else "无"
        dep = "有" if (r["deploy_command"] or "").strip() else "无"
        lines.append(f"- project_id={r['id']} · {r['name']} · dev={dev} · deploy={dep}")
    return lines


def _thread_summary(conn: Connection, source_task: dict[str, Any]) -> list[str]:
    """线程摘要（有界）：首条（source 任务锚点消息）+ 最近 N 条回复。"""
    root_id = source_task["root_message_id"]
    root = conn.execute(
        select(_MSG.c.body).where(_MSG.c.id == root_id)
    ).scalar()
    lines: list[str] = []
    if root is not None:
        lines.append(f"[首条] {root}")
    replies = conn.execute(
        select(_MSG.c.body)
        .where(_MSG.c.thread_root_id == root_id)
        .order_by(_MSG.c.created_at.desc(), _MSG.c.id.desc())
        .limit(THREAD_SUMMARY_RECENT_N)
    ).scalars().all()
    for body in reversed(replies):  # 时序升序展示
        lines.append(f"[讨论] {body}")
    return lines


def _canvas_summary(conn: Connection, channel_id: str) -> str:
    canvas_id = conn.execute(
        select(_CANVAS.c.id).where(_CANVAS.c.channel_id == channel_id)
    ).scalar()
    node_count = 0
    if canvas_id is not None:
        node_count = conn.execute(
            select(func.count()).select_from(_NODE).where(_NODE.c.canvas_id == canvas_id)
        ).scalar() or 0
    in_progress = conn.execute(
        select(func.count())
        .select_from(_TASK)
        .where(_TASK.c.channel_id == channel_id, _TASK.c.status == "in_progress")
    ).scalar() or 0
    return f"画布节点数={node_count} · 进行中任务数={in_progress}"


def build_injection_body(
    conn: Connection,
    *,
    proposal: dict[str, Any],
    source_task: dict[str, Any],
    channel: dict[str, Any],
) -> str:
    """拼接注入消息（§4/§13.2）：角色 prompt_sections（数据不是代码）+ source 任务原文 + 线程讨论
    + 成员清单（含 member_id）+ Project 清单（含 project_id）+ 画布摘要 + 频道 decomp 配置。

    kernel ref 语义 = ULID id 精确匹配：故成员/项目清单显式给出 member_id/project_id 供 Orchestrator
    在 suggested_owner/project 里引用。source 亦给 source_task_id 供 body.source 引用。
    """
    channel_id = channel["id"]
    parts: list[str] = [
        "[system → 仅你可见] 任务拆解请求：请在**本线程内**回复一条含唯一 <control> 块的提案消息。"
    ]

    sections = orchestrator_prompt_sections(conn)
    if sections:
        parts.append("── 角色说明 ──")
        for s in sections:
            text = s.get("text")
            if isinstance(text, str) and text:
                parts.append(text)

    parts.append("── source 任务 ──")
    parts.append(
        f"source_task_id={source_task['id']} · #{source_task['number']} · {source_task['title']}"
    )
    parts.append("（在提案 <control> 的 source 字段填上述 source_task_id）")

    thread = _thread_summary(conn, source_task)
    if thread:
        parts.append("── 线程讨论（有界摘要）──")
        parts.extend(thread)

    members = _member_roster(conn, channel_id)
    parts.append("── 频道成员（suggested_owner 候选，须用 member_id）──")
    parts.extend(members or ["（无成员）"])

    projects = _project_roster(conn, channel_id)
    parts.append("── 绑定 Project（writes_code 节点的 project，须用 project_id）──")
    parts.extend(projects or ["（无绑定 Project）"])

    parts.append("── 画布现状 ──")
    parts.append(_canvas_summary(conn, channel_id))

    parts.append("── 频道拆解配置 ──")
    limit = channel.get("decomp_node_limit")
    parts.append(
        f"decomp_mode={channel.get('decomp_mode')} · decomp_node_limit={limit}"
        f"（decompose 模式节点数上限 = {limit}）"
    )
    return "\n".join(parts)


# ---------------------------------------------------------------- 触发归一（decompose 三入口）


def initiate_proposal(
    tx: Any,
    *,
    workspace_id: str,
    channel: dict[str, Any],
    source_task: dict[str, Any],
    orchestrator: dict[str, Any],
    requester_id: str | None,
) -> tuple[dict[str, Any], PendingInject | None]:
    """归一化后的建提案（supersede 旧活动提案 → 建 drafting → 诊断 → emit proposal.updated），
    返回 (proposal_row, PendingInject | None)。上下文注入由调用方投递。

    并行审计修复（SM-F1/F2）两条退化路径均**复用现行提案、不注入**（inject=None，零新错误码）：
    ① 现行提案在 landing（落地执行中不可替换，§8.2）→ 回该行——请求方 202 看到 landing 态即知
      「已在落地」；② 建行撞部分唯一索引（并发 decompose 竞败,对手已建活动行）→ SAVEPOINT 回滚
      后回对手行——两请求语义同为「对本 source 拆解」，赢家提案对双方等效。
    """
    saw_landing = supersede_active_proposals(tx, source_task["id"])
    if saw_landing:
        active = active_proposal_for_source(tx.conn, source_task["id"])
        if active is not None:
            return active, None
    try:
        with tx.conn.begin_nested():  # SAVEPOINT：部分唯一索引竞败兜底（并发同 source 建案）
            proposal = create_drafting_proposal(
                tx,
                workspace_id=workspace_id,
                channel_id=channel["id"],
                source_task_id=source_task["id"],
                proposed_by=orchestrator["member_id"],
            )
    except IntegrityError:
        active = active_proposal_for_source(tx.conn, source_task["id"])
        if active is None:  # 非本索引冲突（防御：其它完整性错误不吞）
            raise
        write_diagnostic(
            tx, DIAG_DUPLICATE_IGNORED,
            workspace_id=workspace_id, channel_id=channel["id"],
            task_id=source_task["id"],
            payload={
                "active_proposal_id": active["id"], "reason": "concurrent_initiate",
                "requester": requester_id,
            },
        )
        return active, None
    inj_body = build_injection_body(
        tx.conn, proposal=proposal, source_task=source_task, channel=channel
    )
    write_diagnostic(
        tx,
        DIAG_REQUESTED,
        workspace_id=workspace_id,
        channel_id=channel["id"],
        task_id=source_task["id"],
        agent_member_id=orchestrator["member_id"],
        payload={"proposal_id": proposal["id"], "requester": requester_id},
    )
    write_diagnostic(
        tx,
        DIAG_CONTEXT_INJECTED,
        workspace_id=workspace_id,
        channel_id=channel["id"],
        task_id=source_task["id"],
        agent_member_id=orchestrator["member_id"],
        payload={"proposal_id": proposal["id"], "body_len": len(inj_body)},
    )
    _emit_proposal_updated(tx, proposal)
    inject = PendingInject(
        agent_member_id=orchestrator["member_id"],
        body=inj_body,
        kind=InjectKind.SYSTEM,
        ref=proposal["id"],
        best_effort=True,
    )
    return proposal, inject


# ---------------------------------------------------------------- T1 入口（@Orchestrator 顶级消息）


def maybe_trigger_t1(
    tx: Any,
    *,
    channel: dict[str, Any],
    author_member_id: str,
    message_id: str,
    body: str,
    thread_root_id: str | None,
    mentioned: list[dict[str, Any]],
) -> list[PendingInject]:
    """T1（§4）：频道**顶级**消息 @Orchestrator → 同 decompose(text) 语义自动归一（消息已存在：
    转任务 + 建提案 + 注入唤醒）。线程内/DM 的 @Orchestrator 不触发——任务转换前置约束（顶级
    非 DM）天然挡住。挂接点 = persist_message mention 处理后。返回待投递 inject（best-effort）。"""
    if thread_root_id is not None:
        return []  # 线程内消息不触发（source 须为顶级消息转任务）
    if not mentioned:
        return []  # 短路（code-review 效率修复）：无 @mention 不可能 @Orchestrator，免跑三表联查
    if channel["kind"] == ChannelKind.DM.value or channel.get("archived_at"):
        return []  # DM 不承载任务（TASK_IN_DM）/ 归档频道不建
    orchestrator = find_orchestrator(tx.conn, channel["id"])
    if orchestrator is None:
        return []
    orch_id = orchestrator["member_id"]
    if author_member_id == orch_id:
        return []  # Orchestrator 自己的顶级消息不自触发
    if orch_id not in {m["id"] for m in mentioned}:
        return []  # 必须 @Orchestrator

    from coagentia_server.tasks import service as tasks_service

    existing = tx.conn.execute(
        select(_TASK.c.id).where(_TASK.c.root_message_id == message_id)
    ).scalar()
    if existing is not None:
        source_task = _fetch_task(tx.conn, existing)
        assert source_task is not None
    else:
        source_task = tasks_service.create_task(
            tx,
            workspace_id=channel["workspace_id"],
            channel_id=channel["id"],
            root_message_id=message_id,
            created_by=author_member_id,
            source_body=body,
        )
        tasks_service.emit_task_created(tx, source_task)
    _proposal, inject = initiate_proposal(
        tx,
        workspace_id=channel["workspace_id"],
        channel=channel,
        source_task=source_task,
        orchestrator=orchestrator,
        requester_id=author_member_id,
    )
    return [inject] if inject is not None else []  # None=复用现行提案（landing/竞败），不重注入


# ---------------------------------------------------------------- 提案消息解析挂接（§5.3/§6/§7）


def _build_env(conn: Connection, channel: dict[str, Any]) -> Env:
    return Env(
        node_limit=int(channel.get("decomp_node_limit", 12)),
        member_ids=channel_member_ids(conn, channel["id"]),
        bound_project_ids=bound_project_ids(conn, channel["id"]),
    )


@dataclass
class SubmissionDecision:
    """persist_message 两相接口：phase1 决定消息插入 card_kind/card_ref；phase2 apply 落状态。"""

    card_kind: str | None
    card_ref: str | None
    _apply: Any = field(repr=False, default=None)

    def apply(self, tx: Any) -> list[PendingInject]:
        return self._apply(tx) if self._apply is not None else []


def classify_submission(
    tx: Any,
    *,
    channel: dict[str, Any],
    author_member_id: str,
    body: str,
    thread_root_id: str | None,
) -> SubmissionDecision | None:
    """phase1：判定这是否是某非终态提案作者在其 source 线程内的 <control> 提案提交（裁决 #12
    freshness 门在前天然生效——held 草稿不落库不到此）。三前置全满足才解析，否则 None（普通消息）：
      ① 作者 = 本频道某非终态 proposal 的 proposed_by
      ② 消息在该 proposal 的 source 任务线程内（thread_root_id == source.root_message_id）
      ③ 正文含 <control>
    非以上情形的 <control> 消息 = 普通消息照常（写一条诊断留痕，不解析）。
    """
    if thread_root_id is None:
        return None  # 提案在 source 任务线程内提交（非顶级）
    if "<control>" not in body:
        return None  # 短路（code-review 效率修复）：所有决策分支都要 <control>，无控制块的线程
        # 回复不必先跑提案 join + delta 入口的 _is_agent_member/_task_by_root 查询。
    # ① + ②：作者是某非终态提案的 proposed_by，且该提案 source 任务锚点 == 本消息线程根。
    row = tx.conn.execute(
        select(_PROPOSAL)
        .select_from(_PROPOSAL.join(_TASK, _TASK.c.id == _PROPOSAL.c.source_task_id))
        .where(
            _PROPOSAL.c.channel_id == channel["id"],
            _PROPOSAL.c.proposed_by_member_id == author_member_id,
            _PROPOSAL.c.status.notin_(_TERMINAL_VALUES),
            _TASK.c.root_message_id == thread_root_id,
        )
    ).mappings().first()
    if row is None:
        # 无本作者非终态提案锚于本线程 → J10 delta 入口判定（Agent 在任务线程发 delta control）。
        return _classify_delta_entry(tx, channel, author_member_id, body, thread_root_id)
    if "<control>" not in body:
        return None  # 作者在 source 线程内但非提案消息（普通讨论）
    proposal = models.row_dict(row)
    status = ProposalStatus(proposal["status"])

    # landing：落地执行中不可被替换（J9 执行器在跑）——任何 <control> 提交（有效/无效）一律忽略
    # + 诊断留痕（reason=landing_in_progress）；结构变更走 landed 后 delta（J10）。不 supersede。
    if status is ProposalStatus.LANDING:
        return SubmissionDecision(
            card_kind=None, card_ref=None,
            _apply=lambda tx: _apply_duplicate_ignored(
                tx, proposal, reason="landing_in_progress"
            ),
        )

    # 校验按 proposal.kind 路由（delta 走 validate_delta，full 走 validate_proposal）——既有非终态
    # delta 提案的作者在同线程再发 <control> 时，校验器必须对齐提案形态（J10）。
    parsed, parse_err = parse_control(body)
    if parse_err is not None:
        parsed = None
        errors: list[dict[str, Any]] = [dict(parse_err)]
    else:
        assert parsed is not None
        errors = _validate_by_kind(tx.conn, channel, proposal["kind"], parsed)

    if status in (ProposalStatus.DRAFTING, ProposalStatus.VALIDATING, ProposalStatus.REPAIRING):
        # 同 revision：失败进修复循环（配额本 revision），通过同行更新。
        if errors:
            return SubmissionDecision(
                card_kind=None, card_ref=None,
                _apply=lambda tx: _apply_repair(tx, proposal, channel, body, parsed, errors),
            )
        assert parsed is not None
        new_hash = proposal_fingerprint(parsed)
        return SubmissionDecision(
            card_kind=CardKind.PROPOSAL.value, card_ref=proposal["id"],
            _apply=lambda tx: _apply_success_same(tx, proposal, channel, parsed, new_hash),
        )

    # awaiting_confirm：新 control = 对话修正（§8.2 新提案一律 revision+1、旧行 Superseded；
    # §3 新提案一律经 Validating，失败进 Repairing——无效版也是新 revision，配额全新）。
    if errors:
        new_id = new_ulid()
        return SubmissionDecision(
            card_kind=None, card_ref=None,  # 校验未过不落提案卡
            _apply=lambda tx: _apply_revbump_invalid(
                tx, proposal, channel, body, parsed, errors, new_id
            ),
        )
    assert parsed is not None
    new_hash = proposal_fingerprint(parsed)
    if new_hash == proposal["proposal_hash"]:
        # 同指纹 = 忽略新提案不动 revision（登记诊断）。
        return SubmissionDecision(
            card_kind=None, card_ref=None,
            _apply=lambda tx: _apply_duplicate_ignored(tx, proposal),
        )
    new_id = new_ulid()
    return SubmissionDecision(
        card_kind=CardKind.PROPOSAL.value, card_ref=new_id,
        _apply=lambda tx: _apply_success_revbump(tx, proposal, channel, parsed, new_hash, new_id),
    )


# ---------------------------------------------------------------- 校验路由（full / delta）


def _validate_by_kind(
    conn: Connection, channel: dict[str, Any], proposal_kind: str, parsed: dict[str, Any]
) -> list[dict[str, Any]]:
    """按提案形态路由校验：delta → validate_delta（结果图/base/NODE_ACTIVE），full →
    validate_proposal。J10 既有非终态 delta 再提交、修复循环续传共用。"""
    if proposal_kind == ProposalKind.DELTA.value:
        canvas = canvas_service.fetch_canvas_by_channel(conn, channel["id"])
        return [dict(e) for e in delta_domain.validate_delta(conn, channel, canvas, parsed)]
    env = _build_env(conn, channel)
    return [dict(e) for e in validate_proposal(parsed, env)]


# ---------------------------------------------------------------- J10 delta 入口


def _is_agent_member(conn: Connection, member_id: str) -> bool:
    kind = conn.execute(
        select(_MEMBER.c.kind).where(_MEMBER.c.id == member_id, _MEMBER.c.removed_at.is_(None))
    ).scalar()
    return kind == MemberKind.AGENT.value


def _task_by_root(
    conn: Connection, channel_id: str, root_message_id: str
) -> dict[str, Any] | None:
    row = conn.execute(
        select(_TASK).where(
            _TASK.c.channel_id == channel_id, _TASK.c.root_message_id == root_message_id
        )
    ).mappings().first()
    return dict(row) if row is not None else None


def _delta_base_of(parsed: dict[str, Any] | None) -> str | None:
    b = parsed.get("base") if isinstance(parsed, dict) else None
    return b if isinstance(b, str) else None


def create_delta_proposal(
    tx: Any,
    *,
    proposal_id: str,
    workspace_id: str,
    channel_id: str,
    source_task_id: str,
    proposed_by: str,
    body: dict[str, Any],
    proposal_hash: str,
    base_hash: str | None,
    status: ProposalStatus,
) -> dict[str, Any]:
    """建 kind=delta 提案行（revision=1）；status=drafting（valid→转 validating→present；
    invalid→走修复循环）。base_hash = body.base（提案时基线，F9 依据）。"""
    ts = now_iso()
    tx.conn.execute(
        insert(_PROPOSAL).values(
            id=proposal_id,
            workspace_id=workspace_id,
            channel_id=channel_id,
            source_task_id=source_task_id,
            kind=ProposalKind.DELTA.value,
            revision=1,
            status=status.value,
            body=body,
            proposal_hash=proposal_hash,
            base_hash=base_hash,
            landed_hash=None,
            adjustments=[],
            repair_count=0,
            proposed_by_member_id=proposed_by,
            created_at=ts,
            updated_at=ts,
        )
    )
    proposal = fetch_proposal(tx.conn, proposal_id)
    assert proposal is not None
    return proposal


def _classify_delta_entry(
    tx: Any,
    channel: dict[str, Any],
    author_member_id: str,
    body: str,
    thread_root_id: str,
) -> SubmissionDecision | None:
    """J10 delta 入口（拆解设计 §11 / 裁决 #10）：本作者无非终态提案锚于本线程时判定——

      Agent 作者 + 线程根是本频道某任务锚点 + 正文含 <control> + parse 成功 +
      version==decomposition-delta.v1 → delta 提案（source=线程任务、base=body.base、by=作者）。

    人类作者 / 非任务线程 / 非 delta 版本 / parse 失败 → None（普通消息，现行为不变）。
    该 source 已有非终态提案（作者/形态不匹配未被 ① 命中）→ 忽略 + 诊断（防部分唯一索引爆炸）。
    """
    if not _is_agent_member(tx.conn, author_member_id):
        return None  # 人类作者发 delta control → 普通消息（人类直接编辑画布，不走 delta，C5）
    if "<control>" not in body:
        return None
    task = _task_by_root(tx.conn, channel["id"], thread_root_id)
    if task is None:
        return None  # 非任务线程
    parsed, parse_err = parse_control(body)
    if parse_err is not None or not isinstance(parsed, dict):
        return None  # parse 失败 → 普通消息（非 delta 入口）
    if parsed.get("version") != SCHEMA_DECOMPOSITION_DELTA_V1:
        return None  # 非 delta 版本 → 普通消息

    active = active_proposal_for_source(tx.conn, task["id"])
    if active is not None:
        return SubmissionDecision(
            card_kind=None, card_ref=None,
            _apply=lambda tx: _apply_delta_active_exists(tx, task, active),
        )

    canvas = canvas_service.fetch_canvas_by_channel(tx.conn, channel["id"])
    errors = [dict(e) for e in delta_domain.validate_delta(tx.conn, channel, canvas, parsed)]
    new_id = new_ulid()
    if errors:
        return SubmissionDecision(
            card_kind=None, card_ref=None,
            _apply=lambda tx: _apply_new_delta_repair(
                tx, channel, task, author_member_id, body, parsed, errors, new_id
            ),
        )
    new_hash = proposal_fingerprint(parsed)
    return SubmissionDecision(
        card_kind=CardKind.PROPOSAL.value, card_ref=new_id,
        _apply=lambda tx: _apply_new_delta_valid(
            tx, channel, task, author_member_id, parsed, new_hash, new_id
        ),
    )


def _create_delta_row_guarded(
    tx: Any, channel: dict[str, Any], task: dict[str, Any], author: str,
    *, new_id: str, body: dict[str, Any], proposal_hash: str, base_hash: str | None,
) -> dict[str, Any] | None:
    """SAVEPOINT 建 delta 行（SM-F2）：并发同 source 建案撞部分唯一索引 → 回 None（调用方降级
    忽略留痕），非本索引冲突重抛。"""
    try:
        with tx.conn.begin_nested():
            return create_delta_proposal(
                tx, proposal_id=new_id, workspace_id=channel["workspace_id"],
                channel_id=channel["id"], source_task_id=task["id"], proposed_by=author,
                body=body, proposal_hash=proposal_hash, base_hash=base_hash,
                status=ProposalStatus.DRAFTING,
            )
    except IntegrityError:
        if active_proposal_for_source(tx.conn, task["id"]) is None:
            raise  # 非部分唯一索引冲突（防御：其它完整性错误不吞）
        return None


def _apply_new_delta_valid(
    tx: Any, channel: dict[str, Any], task: dict[str, Any], author: str,
    parsed: dict[str, Any], new_hash: str, new_id: str,
) -> list[PendingInject]:
    """有效 delta → drafting → validating → present（awaiting/DELTA_PROPOSED）或直落 landing。"""
    proposal = _create_delta_row_guarded(
        tx, channel, task, author,
        new_id=new_id, body=parsed, proposal_hash=new_hash, base_hash=_delta_base_of(parsed),
    )
    if proposal is None:
        active = active_proposal_for_source(tx.conn, task["id"])
        assert active is not None
        return _apply_delta_active_exists(tx, task, active)
    proposal = _transition(tx, proposal, ProposalStatus.VALIDATING)
    return _present_or_land(tx, proposal, channel)


def _apply_new_delta_repair(
    tx: Any, channel: dict[str, Any], task: dict[str, Any], author: str,
    raw_body: str, parsed: dict[str, Any] | None, errors: list[dict[str, Any]], new_id: str,
) -> list[PendingInject]:
    """无效 delta → 建行 drafting → 复用 J8 修复循环（同配额/升级/S1 直投，携 delta 错误清单）。"""
    body = parsed if isinstance(parsed, dict) else {}
    proposal = _create_delta_row_guarded(
        tx, channel, task, author,
        new_id=new_id, body=body, proposal_hash=_fingerprint_lenient(body),
        base_hash=_delta_base_of(parsed),
    )
    if proposal is None:
        active = active_proposal_for_source(tx.conn, task["id"])
        assert active is not None
        return _apply_delta_active_exists(tx, task, active)
    return _apply_repair(tx, proposal, channel, raw_body, parsed, errors)


def _apply_delta_active_exists(
    tx: Any, task: dict[str, Any], active: dict[str, Any]
) -> list[PendingInject]:
    """source 已有非终态提案 → 忽略新 delta + 诊断留痕（reason=active_proposal_exists）。"""
    write_diagnostic(
        tx, DIAG_DUPLICATE_IGNORED,
        workspace_id=active["workspace_id"], channel_id=active["channel_id"],
        task_id=task["id"],
        payload={
            "source_task_id": task["id"], "active_proposal_id": active["id"],
            "reason": "active_proposal_exists",
        },
    )
    return []


def _final_status_for_mode(channel: dict[str, Any]) -> ProposalStatus:
    mode = channel.get("decomp_mode")
    if mode == DecompMode.DIRECT.value:
        return ProposalStatus.LANDING  # 直落：J9 执行器接（本模块止于 landing）
    return ProposalStatus.AWAITING_CONFIRM


def _present_or_land(
    tx: Any, proposal: dict[str, Any], channel: dict[str, Any]
) -> list[PendingInject]:
    """校验通过后的收尾：validating → awaiting_confirm（present）或 landing（直落）。

    形态感知（J10）：full → draft.presented + DRAFT_PRESENTED；delta → delta.proposed +
    DELTA_PROPOSED（载荷 ProposalData）。落地时二者同经 landing 分派（landing.py 按 batch.kind）。
    """
    is_delta = proposal["kind"] == ProposalKind.DELTA.value
    target = _final_status_for_mode(channel)
    proposal = _transition(tx, proposal, target)
    _emit_proposal_updated(tx, proposal)
    write_diagnostic(
        tx, delta_domain.DIAG_DELTA_PROPOSED if is_delta else DIAG_DRAFTED,
        workspace_id=proposal["workspace_id"], channel_id=proposal["channel_id"],
        task_id=proposal["source_task_id"],
        payload={
            "proposal_id": proposal["id"], "revision": proposal["revision"],
            "mode": channel.get("decomp_mode"),
        },
    )
    if target is ProposalStatus.AWAITING_CONFIRM:
        if is_delta:
            tx.emit(
                EventType.DELTA_PROPOSED, proposal["channel_id"],
                {"proposal": proposal_public(proposal)},
            )
        else:
            _emit_draft_presented(tx, proposal)
            write_diagnostic(
                tx, DIAG_DRAFT_PRESENTED,
                workspace_id=proposal["workspace_id"], channel_id=proposal["channel_id"],
                task_id=proposal["source_task_id"],
                payload={"proposal_id": proposal["id"], "revision": proposal["revision"]},
            )
    return []


def _apply_success_same(
    tx: Any,
    proposal: dict[str, Any],
    channel: dict[str, Any],
    parsed: dict[str, Any],
    new_hash: str,
) -> list[PendingInject]:
    try:
        proposal = _transition(tx, proposal, ProposalStatus.VALIDATING)
    except StaleTransition:
        # 竞败降级（SM-F1）：phase1 读到的状态已被并发推进（confirm/supersede/…）——按重复提交
        # 忽略留痕，不以过期读改写状态机。首个条件 UPDATE 即本事务写锁获取点，之后不再有竞态面。
        return _apply_duplicate_ignored(tx, proposal, reason="concurrent_state_change")
    # 同 revision 成功更新：body/proposal_hash 整体替换；**delta 一并刷新 base_hash**（code-review
    # 修复）——base_hash 是 F9 依据（create_delta_proposal 不变式 base_hash=body.base），漏刷则修复
    # 循环里 Agent 按 hint 改对 base 后同 revision 更新仍留旧错 base_hash，confirm 期 _confirm_delta
    # 以陈旧 base_hash 比当前基线误判过期 → 合法提案永被 DELTA_BASE_MISMATCH 打回（对称于
    # _insert_revision_row rev+1 路径已正确重算 base_hash）。full 提案 base_hash 恒 None，赋值无害。
    values: dict[str, Any] = {"body": parsed, "proposal_hash": new_hash}
    if proposal["kind"] == ProposalKind.DELTA.value:
        values["base_hash"] = _delta_base_of(parsed)
    tx.conn.execute(
        update(_PROPOSAL).where(_PROPOSAL.c.id == proposal["id"]).values(**values)
    )
    refreshed = fetch_proposal(tx.conn, proposal["id"])
    assert refreshed is not None
    return _present_or_land(tx, refreshed, channel)


def _insert_revision_row(
    tx: Any, old: dict[str, Any], *, new_id: str, status: ProposalStatus,
    body: dict[str, Any], proposal_hash: str,
) -> dict[str, Any]:
    """插入对话修正的新 revision 行（revision+1、repair_count 归零）；有效/无效版共用。

    形态承袭（J10）：kind 沿用旧行（delta 对话修正的新行仍 kind=delta）；base_hash 对 delta 取**新
    body** 的 base（新提案基于新画布基线），full 恒 None。
    """
    kind = old["kind"]
    base_hash: str | None = None
    if kind == ProposalKind.DELTA.value:
        b = body.get("base") if isinstance(body, dict) else None
        base_hash = b if isinstance(b, str) else None
    ts = now_iso()
    tx.conn.execute(
        insert(_PROPOSAL).values(
            id=new_id,
            workspace_id=old["workspace_id"],
            channel_id=old["channel_id"],
            source_task_id=old["source_task_id"],
            kind=kind,
            revision=old["revision"] + 1,
            status=status.value,
            body=body,
            proposal_hash=proposal_hash,
            base_hash=base_hash,
            landed_hash=None,
            adjustments=[],
            repair_count=0,
            proposed_by_member_id=old["proposed_by_member_id"],
            created_at=ts,
            updated_at=ts,
        )
    )
    proposal = fetch_proposal(tx.conn, new_id)
    assert proposal is not None
    return proposal


def _apply_success_revbump(
    tx: Any, old: dict[str, Any], channel: dict[str, Any], parsed: dict[str, Any],
    new_hash: str, new_id: str,
) -> list[PendingInject]:
    """对话修正 rev+1（有效版）：supersede 旧行（终态 superseded + draft.superseded）→ 插新行
    revision+1（repair_count 归零）→ 走校验通过收尾。

    supersede 竞败（SM-F1）→ 按重复提交忽略：旧行已被并发 confirm 推进（landing——落地中不可替换）
    或已被并发对手终态化；不以过期读为据建新 rev（消息 card_ref 指向未建行,提案卡渲染「不可用」,
    可接受的罕见竞态残影,远优于把 landing 行踩成 superseded/双落地）。"""
    try:
        _supersede(tx, old)
    except StaleTransition:
        return _apply_duplicate_ignored(tx, old, reason="concurrent_state_change")
    proposal = _insert_revision_row(
        tx, old, new_id=new_id, status=ProposalStatus.VALIDATING,
        body=parsed, proposal_hash=new_hash,
    )
    return _present_or_land(tx, proposal, channel)


def _apply_revbump_invalid(
    tx: Any, old: dict[str, Any], channel: dict[str, Any], raw_body: str,
    parsed: dict[str, Any] | None, errors: list[dict[str, Any]], new_id: str,
) -> list[PendingInject]:
    """对话修正 rev+1（**失败版**，§8.2 + §3）：awaiting_confirm 期收到无效新 <control>——新提案
    无论有效与否都是新 revision。supersede 旧行 → 插新行 revision+1（repair_count=0 配额全新；
    body=parsed 若有、CONTROL_PARSE 则占位 {}）→ 沿合法链 drafting→validating→repairing 走修复
    循环（attempt 1/2 直投）。supersede 竞败 → 忽略留痕（同 _apply_success_revbump 注记）。"""
    try:
        _supersede(tx, old)
    except StaleTransition:
        return _apply_duplicate_ignored(tx, old, reason="concurrent_state_change")
    body = parsed if parsed is not None else {}
    proposal = _insert_revision_row(
        tx, old, new_id=new_id, status=ProposalStatus.DRAFTING,
        body=body, proposal_hash=_fingerprint_lenient(body),
    )
    return _apply_repair(tx, proposal, channel, raw_body, parsed, errors)


def _apply_duplicate_ignored(
    tx: Any, proposal: dict[str, Any], *, reason: str | None = None
) -> list[PendingInject]:
    """忽略新提案不动状态：awaiting_confirm 期同指纹重提（§8.2）与 landing 期任何重提
    （reason='landing_in_progress'——落地执行中不可被替换）共用；留痕诊断。"""
    payload: dict[str, Any] = {"proposal_id": proposal["id"], "revision": proposal["revision"]}
    if reason is not None:
        payload["reason"] = reason
    write_diagnostic(
        tx, DIAG_DUPLICATE_IGNORED,
        workspace_id=proposal["workspace_id"], channel_id=proposal["channel_id"],
        task_id=proposal["source_task_id"],
        payload=payload,
    )
    return []


# ---------------------------------------------------------------- 修复循环（O7 / §7 / §13.3）


def build_error_envelope(*, proposal_revision: int, errors: list[dict[str, Any]]) -> dict[str, Any]:
    """错误清单信封 coagentia.decomposition-errors.v1（§6.3；含 proposal_revision + errors[]）。"""
    return {
        "schema": SCHEMA_DECOMPOSITION_ERRORS_V1,
        "proposal_revision": proposal_revision,
        "errors": errors,
    }


def repair_prompt(*, proposal_revision: int, errors: list[dict[str, Any]], attempt: int) -> str:
    """§13.3 修复提示模板（第 i/2 次；要求重输出完整 <control> 块而非补丁）。"""
    import json

    envelope = build_error_envelope(proposal_revision=proposal_revision, errors=errors)
    return (
        f"[system → 仅你可见] 你的拆解提案（rev.{proposal_revision}）未通过系统校验，共 "
        f"{len(errors)} 个错误。逐条修复后在本线程内重新输出**完整提案**（完整 <control> 块，不是"
        f"补丁）。错误清单：\n{json.dumps(envelope, ensure_ascii=False)}\n"
        f"注意：这是第 {attempt}/{MAX_REPAIRS} 次修复机会；再次失败将升级人类处理。"
    )


def _store_failed_body(
    tx: Any, proposal_id: str, parsed: dict[str, Any] | None
) -> None:
    """存最后一版提案 body 供 reconcile #6 从 body 重新校验推导重算（裁量：仅语义失败时可存 parsed
    JSON；CONTROL_PARSE 无 parsed → body 保持不变，重算得 body 现状的错误清单，可重发不 wedge）。"""
    if parsed is not None:
        tx.conn.execute(
            update(_PROPOSAL).where(_PROPOSAL.c.id == proposal_id).values(
                body=parsed, proposal_hash=_fingerprint_lenient(parsed)
            )
        )


def _apply_repair(
    tx: Any,
    proposal: dict[str, Any],
    channel: dict[str, Any],
    raw_body: str,
    parsed: dict[str, Any] | None,
    errors: list[dict[str, Any]],
) -> list[PendingInject]:
    """校验失败：配额未尽 → repairing + S1 直投错误清单；已尽（repair_count=2）→ failed + @人类。

    首个 _transition 竞败（SM-F1，仅 classify 直达路径可竞——rev+1/delta 建行路径进来时新行由
    本事务插入,无竞态面）→ 按重复提交忽略留痕。"""
    try:
        proposal = _transition(tx, proposal, ProposalStatus.VALIDATING)
    except StaleTransition:
        return _apply_duplicate_ignored(tx, proposal, reason="concurrent_state_change")
    _store_failed_body(tx, proposal["id"], parsed)
    refreshed = fetch_proposal(tx.conn, proposal["id"])
    assert refreshed is not None
    proposal = refreshed
    write_diagnostic(
        tx, DIAG_VALIDATION_FAILED,
        workspace_id=proposal["workspace_id"], channel_id=proposal["channel_id"],
        task_id=proposal["source_task_id"],
        payload={
            "proposal_id": proposal["id"], "proposal_revision": proposal["revision"],
            "errors": errors,
        },
    )
    if proposal["repair_count"] >= MAX_REPAIRS:
        return _escalate_failed(tx, proposal, channel, raw_body, errors)

    attempt = proposal["repair_count"] + 1
    tx.conn.execute(
        update(_PROPOSAL).where(_PROPOSAL.c.id == proposal["id"]).values(repair_count=attempt)
    )
    proposal = _transition(tx, proposal, ProposalStatus.REPAIRING)
    _emit_proposal_updated(tx, proposal)
    write_diagnostic(
        tx, DIAG_REPAIR_ATTEMPT,
        workspace_id=proposal["workspace_id"], channel_id=proposal["channel_id"],
        task_id=proposal["source_task_id"],
        payload={
            "proposal_id": proposal["id"], "attempt": attempt,
            "revision": proposal["revision"],
        },
    )
    prompt = repair_prompt(
        proposal_revision=proposal["revision"], errors=errors, attempt=attempt
    )
    return [PendingInject(
        agent_member_id=proposal["proposed_by_member_id"],
        body=prompt, kind=InjectKind.REPAIR, ref=proposal["id"], best_effort=True,
    )]


def _escalate_failed(
    tx: Any,
    proposal: dict[str, Any],
    channel: dict[str, Any],
    raw_body: str,
    errors: list[dict[str, Any]],
) -> list[PendingInject]:
    """修复配额穷尽 → failed + source 线程系统消息 @人类（附最后一版提案原文 + 全量错误清单）。"""
    import json

    # 延迟 import 避免 messages.service ↔ routes.__init__(canvas→system_nodes) 的加载序环。
    from coagentia_server.messages import service as messages_service

    proposal = _transition(tx, proposal, ProposalStatus.FAILED)
    source_task = _fetch_task(tx.conn, proposal["source_task_id"])
    thread_root = source_task["root_message_id"] if source_task else None
    humans = messages_service.channel_human_members(tx.conn, channel["id"])
    mention_txt = " ".join(f"@{h['name']}" for h in humans)
    envelope = build_error_envelope(proposal_revision=proposal["revision"], errors=errors)
    body = (
        f"拆解提案（rev.{proposal['revision']}）连续 {MAX_REPAIRS} 次修复仍未通过校验，已升级人类"
        f"{('：' + mention_txt) if mention_txt else '。'}\n"
        f"── 最后一版提案原文 ──\n{raw_body}\n"
        f"── 全量错误清单 ──\n{json.dumps(envelope, ensure_ascii=False)}"
    )
    messages_service.post_system_message(
        tx,
        workspace_id=proposal["workspace_id"],
        channel_id=channel["id"],
        body=body,
        thread_root_id=thread_root,
        mention_member_ids=[h["id"] for h in humans],
    )
    _emit_proposal_updated(tx, proposal)
    write_diagnostic(
        tx, DIAG_FAILED_ESCALATED,
        workspace_id=proposal["workspace_id"], channel_id=proposal["channel_id"],
        task_id=proposal["source_task_id"],
        payload={"proposal_id": proposal["id"], "revision": proposal["revision"], "errors": errors},
    )
    return []  # 失败态不再 inject Orchestrator（等人类）


# ---------------------------------------------------------------- 对账 #6（修复循环续传）


def repairing_reconcile_injects(
    conn: Connection, *, agent_member_ids: set[str]
) -> list[PendingInject]:
    """对账 #6（契约 D §4.4）：本机 Agent 名下 status='repairing' 的提案 → 从 body 重新校验推导重算
    完整错误清单 → 重发 S1 直投。

    裁量（勿加列）：不持久化「最后一版错误清单」，从 proposals.body（存的最后一版提案）经
    validate_proposal 重算——全量非增量天然可重发。语义失败时 body 即失败提案，重算复现原错误；
    CONTROL_PARSE 失败时 body 未更新，重算得 body 现状错误清单，可重发不 wedge（登记裁量）。
    """
    if not agent_member_ids:
        return []
    rows = conn.execute(
        select(_PROPOSAL, _CHANNEL.c.decomp_node_limit)
        .select_from(_PROPOSAL.join(_CHANNEL, _CHANNEL.c.id == _PROPOSAL.c.channel_id))
        .where(
            _PROPOSAL.c.status == ProposalStatus.REPAIRING.value,
            _PROPOSAL.c.proposed_by_member_id.in_(agent_member_ids),
        )
    ).mappings().all()
    injects: list[PendingInject] = []
    for row in rows:
        proposal = models.row_dict(row)
        if proposal["kind"] == ProposalKind.DELTA.value:
            channel = conn.execute(
                select(_CHANNEL).where(_CHANNEL.c.id == proposal["channel_id"])
            ).mappings().first()
            if channel is None:
                continue  # 频道行缺失（FK 下近不可达）——跳过勿以 {} 调校验器（SM-F3）
            canvas = canvas_service.fetch_canvas_by_channel(conn, proposal["channel_id"])
            errors = [
                dict(e)
                for e in delta_domain.validate_delta(
                    conn, dict(channel), canvas, proposal["body"]
                )
            ]
        else:
            env = Env(
                node_limit=int(row["decomp_node_limit"] or 12),
                member_ids=channel_member_ids(conn, proposal["channel_id"]),
                bound_project_ids=bound_project_ids(conn, proposal["channel_id"]),
            )
            errors = [dict(e) for e in validate_proposal(proposal["body"], env)]
        if not errors:
            continue  # body 现状已合法（罕见）→ 无可重发
        prompt = repair_prompt(
            proposal_revision=proposal["revision"], errors=errors,
            attempt=min(proposal["repair_count"], MAX_REPAIRS),
        )
        injects.append(PendingInject(
            agent_member_id=proposal["proposed_by_member_id"],
            body=prompt, kind=InjectKind.REPAIR, ref=proposal["id"], best_effort=True,
        ))
    return injects


# ---------------------------------------------------------------- AwaitingConfirm 24h 提醒（F5）


def awaiting_confirm_reminder_scan(tx: Any, *, cutoff_iso: str) -> int:
    """F5：awaiting_confirm 超 24h（updated_at <= cutoff_iso）无人确认 → source 线程系统消息
    @提案请求者（source 任务 created_by）。返回提醒条数。

    防重发纯推导（勿给 proposals 加列）：查 proposal.awaiting_reminder_sent 诊断（task_id=source、
    created_at > proposal.updated_at）已存在则跳过——updated_at 在 awaiting 转换钉住且稳定，故每次
    awaiting 恰提醒一次；rev+1 新行 updated_at 更晚，旧诊断不匹配 → 新行独立提醒。
    """
    from coagentia_server.messages import service as messages_service

    rows = tx.conn.execute(
        select(
            _PROPOSAL.c.id, _PROPOSAL.c.workspace_id, _PROPOSAL.c.channel_id,
            _PROPOSAL.c.source_task_id, _PROPOSAL.c.revision, _PROPOSAL.c.updated_at,
            _TASK.c.root_message_id, _TASK.c.created_by_member_id, _TASK.c.number,
        )
        .select_from(_PROPOSAL.join(_TASK, _TASK.c.id == _PROPOSAL.c.source_task_id))
        .where(
            _PROPOSAL.c.status == ProposalStatus.AWAITING_CONFIRM.value,
            _PROPOSAL.c.updated_at <= cutoff_iso,
        )
    ).mappings().all()
    sent = 0
    for row in rows:
        already = tx.conn.execute(
            select(_DIAG.c.seq).where(
                _DIAG.c.type == DIAG_AWAITING_REMINDER,
                _DIAG.c.task_id == row["source_task_id"],
                _DIAG.c.created_at > row["updated_at"],
            ).limit(1)
        ).first()
        if already is not None:
            continue
        requester = row["created_by_member_id"]
        member = tx.conn.execute(
            select(_MEMBER.c.name, _MEMBER.c.kind).where(
                _MEMBER.c.id == requester, _MEMBER.c.removed_at.is_(None)
            )
        ).mappings().first()
        mention_ids: list[str] = []
        who = "提案请求者"
        if member is not None and member["kind"] == MemberKind.HUMAN.value:
            mention_ids = [requester]
            who = f"@{member['name']}"
        body = (
            f"拆解提案（rev.{row['revision']}）已等待确认超过 24 小时仍无人处理，请及时在草稿画布上"
            f"确认或拒绝{('：' + who) if mention_ids else '。'}"
        )
        messages_service.post_system_message(
            tx,
            workspace_id=row["workspace_id"],
            channel_id=row["channel_id"],
            body=body,
            thread_root_id=row["root_message_id"],
            mention_member_ids=mention_ids,
        )
        write_diagnostic(
            tx, DIAG_AWAITING_REMINDER,
            workspace_id=row["workspace_id"], channel_id=row["channel_id"],
            task_id=row["source_task_id"],
            payload={"proposal_id": row["id"], "revision": row["revision"]},
        )
        sent += 1
    return sent


# ---------------------------------------------------------------- 投递 flush（best-effort）


def flush_injects(hub: Any, injects: list[PendingInject]) -> None:
    """经 daemon_hub.inject_orchestrator 投递；best_effort 者吞 DaemonOffline（修复循环靠对账 #6
    续传；context 注入靠人类重触发）。"""
    from coagentia_server.computers import DaemonOffline

    for inj in injects:
        try:
            hub.inject_orchestrator(inj.agent_member_id, inj.body, kind=inj.kind, ref=inj.ref)
        except DaemonOffline:
            if not inj.best_effort:
                raise


__all__ = [
    "AWAITING_CONFIRM_REMIND_HOURS",
    "MAX_REPAIRS",
    "NoOrchestrator",
    "PROPOSAL_TRANSITIONS",
    "PendingInject",
    "StaleTransition",
    "TERMINAL_STATUSES",
    "active_proposal_for_source",
    "awaiting_confirm_reminder_scan",
    "bound_project_ids",
    "build_error_envelope",
    "build_injection_body",
    "channel_member_ids",
    "classify_submission",
    "create_drafting_proposal",
    "fetch_proposal",
    "find_orchestrator",
    "flush_injects",
    "initiate_proposal",
    "maybe_trigger_t1",
    "orchestrator_prompt_sections",
    "repair_prompt",
    "repairing_reconcile_injects",
    "supersede_active_proposals",
    "write_diagnostic",
]
