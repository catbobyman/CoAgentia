"""4.3 成员/Agent/生命周期/Home/技能/诊断（契约 B §4.3）+ 4.4 提醒（§4.4）。"""

from __future__ import annotations

from typing import Any

from coagentia_contracts import entities, rest
from coagentia_contracts.enums import (
    AgentStatus,
    ChannelKind,
    ContractKind,
    LifecycleAction,
    MemberKind,
    MemberRole,
    MessageKind,
    ReminderKind,
    ReminderStatus,
)
from coagentia_contracts.ws import EventType
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import delete, insert, select, update

from coagentia_server.api import ApiError
from coagentia_server.db import models
from coagentia_server.deps import (
    Tx,
    acting_member,
    get_tx,
    is_admin,
    owner_member,
    require_admin,
    require_workspace,
)
from coagentia_server.ledger.service import new_ulid, now_iso
from coagentia_server.reminders import cadence as cadence_svc
from coagentia_server.routes.serialize import (
    agent_public,
    agent_skill_public,
    diagnostic_public,
    member_public,
    message_public,
    reminder_public,
)

router = APIRouter(prefix="/api", tags=["members"])

_MEMBER = models.tbl(models.Member)
_AGENT = models.tbl(models.Agent)
_SKILL = models.tbl(models.AgentSkill)
_DIAG = models.tbl(models.DiagnosticEvent)
_REMINDER = models.tbl(models.Reminder)
_TASK_CONTRACT = models.tbl(models.TaskContract)
_CHANNEL = models.tbl(models.Channel)
_MSG = models.tbl(models.Message)

# 提醒取消留痕类型（system. 命名空间，契约 A §4.6；见 open_issues：contracts 未登记专用类型）。
_DIAG_REMINDER_CANCELLED = "system.reminder_cancelled"

# L11 入职问候幂等标记（agent. 命名空间，DIAGNOSTIC_TYPES 开放集；同 _DIAG_REMINDER_CANCELLED
# 体例作本地专用类型）——一条即代表「该 Agent 已问候过」，重启/再上线不再重复（PRD FR-1.4）。
_DIAG_ONBOARDING_GREETING = "agent.onboarding_greeting"


def _fetch_member(tx: Tx, member_id: str) -> dict[str, Any]:
    row = tx.conn.execute(select(_MEMBER).where(_MEMBER.c.id == member_id)).mappings().first()
    if row is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "成员不存在")
    return dict(row)


def _fetch_agent(tx: Tx, member_id: str) -> dict[str, Any]:
    row = tx.conn.execute(select(_AGENT).where(_AGENT.c.member_id == member_id)).mappings().first()
    if row is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "Agent 不存在")
    return dict(row)


# ---------------------------------------------------------------- 成员


@router.get("/members", response_model=list[entities.MemberPublic])
def list_members(tx: Tx = Depends(get_tx), include_removed: bool = False) -> Any:
    ws = require_workspace(tx.conn)
    stmt = select(_MEMBER).where(_MEMBER.c.workspace_id == ws["id"])
    if not include_removed:
        stmt = stmt.where(_MEMBER.c.removed_at.is_(None))
    rows = tx.conn.execute(stmt.order_by(_MEMBER.c.created_at)).mappings()
    return [member_public(dict(r)) for r in rows]


@router.patch("/members/{member_id}", response_model=entities.MemberPublic)
def patch_member(
    member_id: str, body: rest.MemberPatch, request: Request, tx: Tx = Depends(get_tx)
) -> Any:
    actor = acting_member(request, tx.conn)
    require_admin(actor)
    target = _fetch_member(tx, member_id)

    # R1：Agent 永不 Owner（服务层拒绝 + DB CHECK 兜底）。
    if target["kind"] == MemberKind.AGENT and body.role == MemberRole.OWNER:
        raise ApiError(403, rest.ErrorCode.PERMISSION_DENIED, "Agent 永不 Owner", rule="R1")
    # admin 仅可动 Member 级；owner 任意（契约 B §3.1）。
    if actor["role"] == MemberRole.ADMIN and (
        target["role"] == MemberRole.OWNER or body.role == MemberRole.OWNER
    ):
        raise ApiError(
            403, rest.ErrorCode.PERMISSION_DENIED, "admin 不能改动 owner 级角色", rule="admin"
        )

    tx.conn.execute(update(_MEMBER).where(_MEMBER.c.id == member_id).values(role=body.role))
    pub = member_public(_fetch_member(tx, member_id))
    tx.emit(EventType.MEMBER_UPDATED, None, {"member": pub})
    return pub


@router.get("/presence", response_model=rest.PresenceSnapshot)
def get_presence(tx: Tx = Depends(get_tx)) -> Any:
    """运行态合并视图：Agent 取 agents.status，人类 owner online（契约 B §4.3）。"""
    ws = require_workspace(tx.conn)
    members = tx.conn.execute(
        select(_MEMBER).where(
            _MEMBER.c.workspace_id == ws["id"], _MEMBER.c.removed_at.is_(None)
        )
    ).mappings()
    agent_status = {
        r["member_id"]: r["status"]
        for r in tx.conn.execute(select(_AGENT.c.member_id, _AGENT.c.status)).mappings()
    }
    items: list[dict[str, Any]] = []
    for m in members:
        if m["kind"] == MemberKind.AGENT:
            status = agent_status.get(m["id"], AgentStatus.OFFLINE.value)
        else:
            status = "online"  # MVP：本地浏览器 = Owner 在线（WS 连接判定接线于 A4）
        items.append(
            {"member_id": m["id"], "kind": m["kind"], "status": status, "busy_detail": None}
        )
    return {"items": items}


# ---------------------------------------------------------------- Agent


@router.post("/agents", response_model=entities.AgentPublic, status_code=201)
def create_agent(body: rest.AgentCreate, request: Request, tx: Tx = Depends(get_tx)) -> Any:
    ws = require_workspace(tx.conn)
    require_admin(acting_member(request, tx.conn))

    taken = tx.conn.execute(
        select(_MEMBER.c.id).where(
            _MEMBER.c.workspace_id == ws["id"],
            _MEMBER.c.removed_at.is_(None),
            _MEMBER.c.name.ilike(body.name),
        )
    ).first()
    if taken is not None:
        raise ApiError(409, rest.ErrorCode.NAME_TAKEN, f"成员名 {body.name} 已被占用")

    computer = tx.conn.execute(
        select(models.tbl(models.Computer).c.id).where(
            models.tbl(models.Computer).c.id == body.computer_id
        )
    ).first()
    if computer is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "机器不存在")

    # role_template_key（M6b）：提供了则须为已登记的角色模板 key，否则 422（details 携 key）。
    # 角色模板是全局字典表（A §4.1），无 workspace 维度；agents.role_template_key 无 FK（可增删）。
    if body.role_template_key is not None:
        _ROLE = models.tbl(models.AgentRoleTemplate)
        known = tx.conn.execute(
            select(_ROLE.c.key).where(_ROLE.c.key == body.role_template_key)
        ).first()
        if known is None:
            raise ApiError(
                422,
                rest.ErrorCode.VALIDATION_FAILED,
                f"未知的角色模板 key: {body.role_template_key}",
                details={"role_template_key": body.role_template_key},
            )

    ts = now_iso()
    member_id = new_ulid()
    creator = owner_member(tx.conn)
    tx.conn.execute(
        insert(_MEMBER).values(
            id=member_id,
            workspace_id=ws["id"],
            kind=MemberKind.AGENT,
            name=body.name,
            role=MemberRole.MEMBER,
            removed_at=None,
            created_at=ts,
        )
    )
    tx.conn.execute(
        insert(_AGENT).values(
            member_id=member_id,
            computer_id=body.computer_id,
            runtime=body.runtime,
            model=body.model,
            description=body.description,
            home_path=f"~/.coagentia/agents/{member_id}",
            status=AgentStatus.OFFLINE,
            created_by_member_id=creator["id"],
            role_template_key=body.role_template_key,
        )
    )
    member_pub = member_public(_fetch_member(tx, member_id))
    # daemon 启动流程是 A5 接缝（POST /agents 只落 member+agent 行，不连 daemon）。
    tx.emit(EventType.MEMBER_CREATED, None, {"member": member_pub})
    return agent_public(_fetch_agent(tx, member_id))


@router.get("/agents/{member_id}", response_model=entities.AgentPublic)
def get_agent(member_id: str, tx: Tx = Depends(get_tx)) -> Any:
    return agent_public(_fetch_agent(tx, member_id))


@router.patch("/agents/{member_id}", response_model=entities.AgentPublic)
def patch_agent(
    member_id: str, body: rest.AgentPatch, request: Request, tx: Tx = Depends(get_tx)
) -> Any:
    agent = _fetch_agent(tx, member_id)
    actor = acting_member(request, tx.conn)
    # R3：runtime/技能只归创建者与 admin。
    if actor["id"] != agent["created_by_member_id"] and not is_admin(actor):
        raise ApiError(
            403, rest.ErrorCode.PERMISSION_DENIED, "仅创建者或 admin 可改 Agent", rule="R3"
        )
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    if changes:
        tx.conn.execute(update(_AGENT).where(_AGENT.c.member_id == member_id).values(**changes))
    pub = agent_public(_fetch_agent(tx, member_id))
    tx.emit(EventType.AGENT_UPDATED, None, {"agent": pub})  # UI：下次启动生效
    return pub


@router.delete("/agents/{member_id}", status_code=204)
def delete_agent(member_id: str, request: Request, tx: Tx = Depends(get_tx)) -> Response:
    agent = _fetch_agent(tx, member_id)
    actor = acting_member(request, tx.conn)
    if actor["id"] != agent["created_by_member_id"] and not is_admin(actor):
        raise ApiError(
            403, rest.ErrorCode.PERMISSION_DENIED, "仅创建者或 admin 可删 Agent", rule="R3"
        )
    ts = now_iso()
    # 软删 member 行（消息归属保留身份）；删 agent 行 + 其技能（COMPUTER_HAS_AGENTS 反映活体）。
    tx.conn.execute(delete(_SKILL).where(_SKILL.c.agent_member_id == member_id))
    tx.conn.execute(delete(_AGENT).where(_AGENT.c.member_id == member_id))
    tx.conn.execute(update(_MEMBER).where(_MEMBER.c.id == member_id).values(removed_at=ts))
    tx.emit(EventType.MEMBER_REMOVED, None, {"member": member_public(_fetch_member(tx, member_id))})
    return Response(status_code=204)


@router.post("/agents/{member_id}/lifecycle")
def agent_lifecycle(
    member_id: str, body: rest.LifecycleRequest, request: Request, tx: Tx = Depends(get_tx)
) -> Any:
    _fetch_agent(tx, member_id)
    actor = acting_member(request, tx.conn)
    # R2：Agent 主体不能操作生命周期。
    if actor["kind"] == MemberKind.AGENT:
        raise ApiError(
            403, rest.ErrorCode.PERMISSION_DENIED, "Agent 不能操作生命周期", rule="R2"
        )
    # 生命周期同步语义：连接的 daemon 下发指令等 ack；无连接 → 503（不参与对账补发，契约 D §4.3）。
    from coagentia_server.computers import DaemonOffline

    hub = request.app.state.daemon_hub
    try:
        result = hub.send_lifecycle(member_id, body.action)
    except DaemonOffline as exc:
        raise ApiError(
            503, rest.ErrorCode.DAEMON_OFFLINE, "daemon 离线，无法执行生命周期指令"
        ) from exc
    # L11：首次成功上线 → 一次性入职问候（工作区开关 + 幂等标记双门；PRD FR-1.4，裁决 #9 默认关）。
    if body.action == LifecycleAction.START and result != "failed":
        _maybe_onboarding_greet(tx, request, member_id)
    return {"result": result}


def _maybe_onboarding_greet(tx: Tx, request: Request, agent_member_id: str) -> None:
    """新 Agent 首次上线且工作区开启欢迎语 → 一次性问候（PRD FR-1.4；裁决 #9 默认关）。

    双门：① 工作区 onboarding_greeting 开关（seed 默认 false）；② diagnostic 幂等标记未落。
    问候本体经 tx.after_commit 提交后 best-effort 直投（离线静默——问候不阻断上线；铁律 4：
    跨进程等 ack 的直投不得跨持锁事务）。标记写在提交前，故「重启不重复」airtight——上线
    前须先 send_lifecycle(START) 成功，daemon 必在线，问候几乎必达，标记不会白烧。"""
    ws = require_workspace(tx.conn)
    if not ws.get("onboarding_greeting"):
        return
    already = tx.conn.execute(
        select(_DIAG.c.seq)
        .where(
            _DIAG.c.agent_member_id == agent_member_id,
            _DIAG.c.type == _DIAG_ONBOARDING_GREETING,
        )
        .limit(1)
    ).first()
    if already is not None:
        return
    hub = request.app.state.daemon_hub
    # 上线刚成功 daemon 必在线；预检只作防御（离线则不落标记，下次上线再问候）。
    if not hub.agent_daemon_online(agent_member_id):
        return
    all_channel = tx.conn.execute(
        select(_CHANNEL.c.id).where(
            _CHANNEL.c.workspace_id == ws["id"],
            _CHANNEL.c.kind == ChannelKind.CHANNEL,
            _CHANNEL.c.name == "all",
        )
    ).first()
    tx.conn.execute(
        insert(_DIAG).values(
            workspace_id=ws["id"],
            agent_member_id=agent_member_id,
            type=_DIAG_ONBOARDING_GREETING,
            channel_id=all_channel[0] if all_channel is not None else None,
            payload={"trigger": "lifecycle_start"},
            created_at=now_iso(),
        )
    )

    def _fire() -> None:
        from coagentia_server.computers import DaemonOffline

        try:
            hub.inject_onboarding_greeting(agent_member_id, ref=agent_member_id)
        except DaemonOffline:
            pass  # 离线静默——问候 best-effort，标记已落防重复

    tx.after_commit(_fire)


@router.get("/agents/{member_id}/home/tree")
def home_tree(member_id: str, request: Request, tx: Tx = Depends(get_tx), path: str = "/") -> Any:
    _fetch_agent(tx, member_id)
    from coagentia_server.computers import DaemonOffline

    hub = request.app.state.daemon_hub
    try:
        return hub.query_home_tree(member_id, path)
    except DaemonOffline as exc:
        raise ApiError(503, rest.ErrorCode.DAEMON_OFFLINE, "daemon 离线，无法浏览 Home") from exc


@router.get("/agents/{member_id}/home/file")
def home_file(member_id: str, path: str, request: Request, tx: Tx = Depends(get_tx)) -> Any:
    _fetch_agent(tx, member_id)
    from coagentia_server.computers import DaemonOffline

    hub = request.app.state.daemon_hub
    try:
        return hub.query_home_file(member_id, path)
    except DaemonOffline as exc:
        raise ApiError(503, rest.ErrorCode.DAEMON_OFFLINE, "daemon 离线，无法读取文件") from exc


# ---------------------------------------------------------------- 技能


@router.get("/agents/{member_id}/skills", response_model=list[entities.AgentSkillPublic])
def get_skills(member_id: str, tx: Tx = Depends(get_tx)) -> Any:
    _fetch_agent(tx, member_id)
    rows = tx.conn.execute(
        select(_SKILL).where(_SKILL.c.agent_member_id == member_id).order_by(_SKILL.c.skill)
    ).mappings()
    return [agent_skill_public(dict(r)) for r in rows]


@router.put("/agents/{member_id}/skills", response_model=list[entities.AgentSkillPublic])
def put_skills(
    member_id: str, body: rest.SkillsPut, request: Request, tx: Tx = Depends(get_tx)
) -> Any:
    agent = _fetch_agent(tx, member_id)
    actor = acting_member(request, tx.conn)
    if actor["id"] != agent["created_by_member_id"] and not is_admin(actor):
        raise ApiError(
            403, rest.ErrorCode.PERMISSION_DENIED, "仅创建者或 admin 可授技能", rule="R3"
        )
    ts = now_iso()
    # R6 全量替换制，授予留痕。
    tx.conn.execute(delete(_SKILL).where(_SKILL.c.agent_member_id == member_id))
    for skill in dict.fromkeys(body.skills):  # 去重保序
        tx.conn.execute(
            insert(_SKILL).values(
                agent_member_id=member_id,
                skill=skill,
                granted_by_member_id=actor["id"],
                granted_at=ts,
            )
        )
    tx.emit(EventType.AGENT_UPDATED, None, {"agent": agent_public(_fetch_agent(tx, member_id))})
    rows = tx.conn.execute(
        select(_SKILL).where(_SKILL.c.agent_member_id == member_id).order_by(_SKILL.c.skill)
    ).mappings()
    return [agent_skill_public(dict(r)) for r in rows]


# ---------------------------------------------------------------- 诊断


@router.get(
    "/agents/{member_id}/diagnostics",
    response_model=rest.Page[entities.DiagnosticEventPublic],
)
def get_diagnostics(
    member_id: str,
    tx: Tx = Depends(get_tx),
    after_seq: int = 0,
    type: str | None = None,
    limit: int = rest.PAGE_DEFAULT_LIMIT,
) -> Any:
    _fetch_agent(tx, member_id)
    limit = min(max(1, limit), rest.PAGE_MAX_LIMIT)
    stmt = select(_DIAG).where(
        _DIAG.c.agent_member_id == member_id, _DIAG.c.seq > after_seq
    )
    if type is not None:
        stmt = stmt.where(_DIAG.c.type == type)
    rows = list(
        tx.conn.execute(stmt.order_by(_DIAG.c.seq).limit(limit + 1)).mappings()
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = str(page[-1]["seq"]) if has_more and page else None
    return {"items": [diagnostic_public(dict(r)) for r in page], "next_cursor": next_cursor}


@router.get("/agents/{member_id}/diagnostics/export")
def export_diagnostics(member_id: str, tx: Tx = Depends(get_tx)) -> Response:
    _fetch_agent(tx, member_id)
    rows = tx.conn.execute(
        select(_DIAG).where(_DIAG.c.agent_member_id == member_id).order_by(_DIAG.c.seq)
    ).mappings()
    lines = [
        f"[{r['seq']}] {r['created_at']} {r['type']} {r['payload']}" for r in rows
    ]
    text = "\n".join(lines) + ("\n" if lines else "")
    return Response(content=text or "(no diagnostics)\n", media_type="text/plain")


# ---------------------------------------------------------------- 4.4 提醒


def _reminder_agent(request: Request, tx: Tx, ws_id: str) -> dict[str, Any]:
    """提醒 = Agent 主体自设（FR-3.9）：X-Acting-Member 指向 Agent，否则回退首个 Agent。"""
    actor = acting_member(request, tx.conn)
    if actor["kind"] == MemberKind.AGENT:
        return actor
    row = tx.conn.execute(
        select(_MEMBER)
        .where(
            _MEMBER.c.workspace_id == ws_id,
            _MEMBER.c.kind == MemberKind.AGENT,
            _MEMBER.c.removed_at.is_(None),
        )
        .order_by(_MEMBER.c.created_at)
        .limit(1)
    ).mappings().first()
    if row is None:
        raise ApiError(422, rest.ErrorCode.VALIDATION_FAILED, "提醒需 Agent 主体，但无可用 Agent")
    return dict(row)


@router.post("/reminders", response_model=entities.ReminderPublic, status_code=201)
def create_reminder(body: rest.ReminderCreate, request: Request, tx: Tx = Depends(get_tx)) -> Any:
    ws = require_workspace(tx.conn)
    is_recurring = body.kind == ReminderKind.RECURRING.value
    # D1-L2：recurring 必须内联 loop_contract（缺 → 422；先于主体解析，与 mock/§4.4 一致）。
    if is_recurring and body.loop_contract is None:
        raise ApiError(
            422,
            rest.ErrorCode.VALIDATION_FAILED,
            "循环 reminder 必须内联 LoopContract",
            rule="D1-L2",
            details={"missing": ["loop_contract"]},
        )
    # once 不该带契约（LoopContract 是循环上岗契约，一次性提醒无循环面）。
    if not is_recurring and body.loop_contract is not None:
        raise ApiError(
            422,
            rest.ErrorCode.VALIDATION_FAILED,
            "once reminder 不接受 loop_contract",
            details={"unexpected": ["loop_contract"]},
        )
    # recurring：cadence 须为合法值域（interval 或 cron 五段式，B §11.5）且与契约 cadence 一致。
    # 值域判定走 cadence 单点（纪律 7），端点仅负责把 ValueError 转 422。
    if is_recurring:
        assert body.loop_contract is not None  # 上门已保证
        try:
            cadence_svc.validate(body.cadence)
        except ValueError as exc:
            raise ApiError(
                422, rest.ErrorCode.VALIDATION_FAILED, str(exc), details={"field": "cadence"}
            ) from exc
        if body.loop_contract.cadence != body.cadence:
            raise ApiError(
                422,
                rest.ErrorCode.VALIDATION_FAILED,
                "reminders.cadence 与 loop_contract.cadence 不一致",
                details={
                    "reminder_cadence": body.cadence,
                    "contract_cadence": body.loop_contract.cadence,
                },
            )

    channel = tx.conn.execute(
        select(_CHANNEL.c.id).where(_CHANNEL.c.id == body.anchor_channel_id)
    ).first()
    if channel is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "锚点频道不存在")

    agent = _reminder_agent(request, tx, ws["id"])
    ts = now_iso()
    reminder_id = new_ulid()
    contract_id: str | None = None
    # 同事务建挂接契约行 + 回填 loop_contract_id（LoopContract 随建即生效，无人确认门——B §4.4）。
    # 契约行先落、reminder 直接带 loop_contract_id 插入（**非** reminder 先 None 再 UPDATE）：
    # reminders 的 CHECK ck_reminders_recurring_needs_contract 由 SQLite **即时**执法（CHECK 不可
    # 延迟），先插 loop_contract_id=None 的 recurring 行会当场违约。task_contracts 的 XOR
    # (task_id IS NULL / reminder_id 非空) 由本插入满足。
    if is_recurring:
        assert body.loop_contract is not None
        contract_id = new_ulid()
        tx.conn.execute(
            insert(_TASK_CONTRACT).values(
                id=contract_id,
                workspace_id=ws["id"],
                task_id=None,
                reminder_id=reminder_id,
                kind=ContractKind.LOOP_CONTRACT.value,
                version=body.loop_contract.version,
                body=body.loop_contract.model_dump(mode="json"),
                revision=1,
                superseded_at=None,
                created_by_member_id=agent["id"],
                created_at=ts,
            )
        )
    tx.conn.execute(
        insert(_REMINDER).values(
            id=reminder_id,
            workspace_id=ws["id"],
            agent_member_id=agent["id"],
            kind=body.kind,
            cadence=body.cadence,
            anchor_channel_id=body.anchor_channel_id,
            anchor_message_id=body.anchor_message_id,
            anchor_task_id=body.anchor_task_id,
            loop_contract_id=contract_id,
            # once：A3 落锚点即 now（首扫即触发）；recurring：cadence 单点算首触发锚点
            # （interval=建后一个周期、cron=创建时刻后首个命中；避免建即触发的意外——code-review
            # 修），之后 run_reminder_scan 按 cadence 类型塌缩重排。
            next_fire_at=(
                cadence_svc.initial_fire(ts, body.cadence) if is_recurring else ts
            ),
            status=ReminderStatus.ACTIVE,
            cancelled_by_member_id=None,
            created_at=ts,
        )
    )
    row = models.row_dict(
        tx.conn.execute(select(_REMINDER).where(_REMINDER.c.id == reminder_id)).mappings().first()
    )
    pub = reminder_public(row)
    tx.emit(EventType.REMINDER_CREATED, body.anchor_channel_id, {"reminder": pub})
    return pub


@router.get("/agents/{member_id}/reminders", response_model=list[entities.ReminderPublic])
def list_reminders(member_id: str, tx: Tx = Depends(get_tx)) -> Any:
    _fetch_agent(tx, member_id)
    rows = tx.conn.execute(
        select(_REMINDER)
        .where(_REMINDER.c.agent_member_id == member_id)
        .order_by(_REMINDER.c.created_at)
    ).mappings()
    return [reminder_public(dict(r)) for r in rows]


@router.delete("/reminders/{reminder_id}", status_code=204)
def cancel_reminder(reminder_id: str, request: Request, tx: Tx = Depends(get_tx)) -> Response:
    row = tx.conn.execute(
        select(_REMINDER).where(_REMINDER.c.id == reminder_id)
    ).mappings().first()
    if row is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "提醒不存在")
    reminder = dict(row)
    actor = acting_member(request, tx.conn)
    ts = now_iso()
    tx.conn.execute(
        update(_REMINDER)
        .where(_REMINDER.c.id == reminder_id)
        .values(status=ReminderStatus.CANCELLED, cancelled_by_member_id=actor["id"])
    )
    # 锚点处发系统消息（author=NULL）。
    sys_msg_id = new_ulid()
    tx.conn.execute(
        insert(_MSG).values(
            id=sys_msg_id,
            workspace_id=reminder["workspace_id"],
            channel_id=reminder["anchor_channel_id"],
            thread_root_id=None,
            author_member_id=None,
            kind=MessageKind.SYSTEM,
            card_kind=None,
            card_ref=None,
            body=f"提醒已取消（reminder {reminder_id}）。",
            created_at=ts,
        )
    )
    # 写诊断留痕（system. 命名空间；见 open_issues）。
    tx.conn.execute(
        insert(_DIAG).values(
            workspace_id=reminder["workspace_id"],
            agent_member_id=reminder["agent_member_id"],
            type=_DIAG_REMINDER_CANCELLED,
            channel_id=reminder["anchor_channel_id"],
            task_id=None,
            batch_id=None,
            payload={"reminder_id": reminder_id, "cancelled_by": actor["id"]},
            created_at=ts,
        )
    )
    sys_msg = models.row_dict(
        tx.conn.execute(select(_MSG).where(_MSG.c.id == sys_msg_id)).mappings().first()
    )
    fresh_reminder = models.row_dict(
        tx.conn.execute(select(_REMINDER).where(_REMINDER.c.id == reminder_id)).mappings().first()
    )
    tx.emit(
        EventType.MESSAGE_CREATED,
        reminder["anchor_channel_id"],
        {"message": message_public(sys_msg)},
    )
    tx.emit(
        EventType.REMINDER_UPDATED,
        reminder["anchor_channel_id"],
        {"reminder": reminder_public(fresh_reminder)},
    )
    return Response(status_code=204)
