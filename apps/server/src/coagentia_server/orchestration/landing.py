"""落地执行器（J9；拆解设计 §9 / 契约 A §4.7 / B §12.5）——decomp 批次的异步增量执行。

**架构（主循环裁定）**：落地 = 异步增量执行。confirm 事务（draft.confirm_apply）只落账 + 建
running 批次 + 202；本执行器领取「running 且未 :done 的 decomp 批次」，从 **landed 内容 =
apply_adjustments(proposals.body, proposals.adjustments)**（确定性纯函数，重启后从 DB 重算同一
op 序列）重建全序列，**按「步」增量提交**——前段命中 hit 跳过、尾段 new 补齐（§9.2 恢复规则 3），
全部 op 过后在 :done 事务里写 done 标记 + baseline bump（批末恰一次）+「已落地」系统消息
（**只在 :done 后发**，§9.1 ⑤）+ landing.completed + 状态 landing→landed。同键异指纹 →
fail-closed 停批（§9.2 规则 2，独立收尾事务处置即持久）。

**步进式提交（硬关口重写，Fable）**：一步 = 一个节点 op + 该节点**全部入边 op**，同一 gateway_tx
原子提交；账本仍**逐 op 记行**（契约 A §4.7 op 目录形状不变），恢复粒度=步（步内崩溃整步回滚，
重入时整步重跑；已提交步逐 op hit 跳过）。**不变量：任何已落地节点的入边集在其落地时刻即完整**
——拓扑序保证入边来源节点先于本节点落地，故每个提交点上 derive_blocked 判定恒正确。这封死
M6A-EVIDENCE verify-surfaced 的「裸系统节点空成功」窗口在增量落地路径上的实体化（若节点 op 与
入边 op 分事务提交，提案声明的 merge/check 系统节点会在入边落地前被系统节点扫描判空成功——
success 终态不可 retry，下游 gating 被永久错误解锁；writes_code 节点同窗口被过早激活）。

**op 序列构造（确定性，M5b 教训 #C：批内顺序以构造序为准，重放按同一函数重建，勿从账本排序）**：
1. 提案节点按**拓扑序**逐节点成步（Kahn，就绪集取 temp_id 字典序最小者——确定性平局破除）；
   每步内 = 该节点 create_node + 其入边 create_edge（按 (from,to) 升序）；
2. **merge 系统节点自动追加**（裁决 #6：mode=decompose 且含 writes_code 节点且未显式声明 merge
   系统节点）——deps = writes_code **前沿**（无 writes_code 后代的 writes_code 节点：J5 合并面按
   祖先集取分支，连前沿即令全部 writes_code 节点成为 merge 祖先、分支合并无遗漏；典型形态
   （代码节点全为叶子）与契约文句「全部 writes_code 叶子之后」等价）；节点+依赖边一体一 op 一步；
3. **汇总节点条件追加**（§9.1 ③ + 裁决 #16：mode=decompose 且 agent 节点 >1）——is_summary 任务
   节点、owner=Orchestrator（proposed_by）；deps = 自动 merge 节点（若有）+ 非 writes_code 叶子，
   无自动 merge 时 = 全部叶子；节点+依赖边一体一 op 一步；
4. single_task：恰 1 节点、无边、无汇总、无 merge 追加（A7）。

op_id 全部用 constants 既有 OPID_DECOMP_* 格式；自动 merge 节点复用 OPID_DECOMP_NODE 格式、
temp_id 取 43 字符保留键（提案 temp_id 上限 32 字符——不可能撞键）。request_hash 只含 landed
内容派生的确定性输入（节点定义/坐标/依赖集），生成的 ULID 落 payload 供 hit 路径复原映射。

**对账 #4**（pending_landing_scan，hub 启动扫描 + 周期 loop + 事件触发共用）：
- kind=decomp 的 running 无 :done 批次 → 重入执行器（幂等：前段 hit 跳过尾段补齐）；
- proposals status=landing 且无批次（**只可能是直落**——confirm 事务原子地「转 landing + 建批」，
  不存在 confirm 后无批）→ 建批（confirmed_by='auto(channel-policy)'、content_hash=proposal_hash、
  landed_hash=proposal_hash、adjustments=[]）并执行。

**不经 ledger.replay.replay_batch/HandlerRegistry（登记裁量）**：replay_batch 是「单连接单事务整批」
模型，与 §9.2 增量恢复语义（逐 op 独立小事务、崩溃留已提交前缀）不相容；registry handler 签名
(Connection, entry) 无提交后事件面（执行器需 gateway_tx.emit）。故 op 分派用本模块单点 dict
（形同注册表），幂等语义仍完全由 ledger.service 三态承载；done_op_id 复用 replay 模块。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from coagentia_contracts.constants import (
    OPID_DECOMP_DONE,
    OPID_DECOMP_EDGE,
    OPID_DECOMP_NODE,
    OPID_DECOMP_SUMMARY,
    OPID_DELTA_OP,
)
from coagentia_contracts.entities import LandingBatchRow, TaskPlanBody
from coagentia_contracts.enums import (
    CanvasNodeKind,
    ContractKind,
    LandingBatchKind,
    LandingBatchStatus,
    MessageKind,
    ProposalStatus,
    SystemAction,
    SystemNodeStatus,
    TaskLevel,
    TaskStatus,
)
from coagentia_contracts.kernel.decomposition import proposal_fingerprint
from coagentia_contracts.kernel.fingerprint import fingerprint
from coagentia_contracts.ws import EventType
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError

from coagentia_server.canvas import service as canvas_service
from coagentia_server.computers.gateway_tx import gateway_tx
from coagentia_server.contracts import service as contracts_service
from coagentia_server.db import models
from coagentia_server.events import EventBus
from coagentia_server.ledger import replay as ledger_replay
from coagentia_server.ledger import service
from coagentia_server.orchestration import proposal as proposal_domain
from coagentia_server.orchestration.draft import apply_adjustments
from coagentia_server.routes.serialize import (
    canvas_edge_public,
    canvas_node_public,
    message_public,
    proposal_public,
    task_contract_public,
)
from coagentia_server.tasks import service as tasks_service

_PROPOSAL = models.tbl(models.Proposal)
_BATCH = models.tbl(models.LandingBatch)
_TASK = models.tbl(models.Task)
_MSG = models.tbl(models.Message)
_MEMBER = models.tbl(models.Member)
_EDGE = models.tbl(models.CanvasEdge)
_DIAG = models.tbl(models.DiagnosticEvent)

# 直落批次确认人字面量（契约 A §4.7 confirmed_by；拆解设计 §8.3）。
AUTO_CONFIRMED_BY = "auto(channel-policy)"

# 自动 merge 节点的保留 temp 键：43 字符 > 提案 temp_id 上限 32（V4 正则）→ 不可能与提案节点撞
# op_id（OPID_DECOMP_NODE 命名空间共用）。
AUTO_MERGE_KEY = "auto-merge-system-node-by-landing-executor"
_SUMMARY_LAYOUT_KEY = "__summary__"  # 仅布局虚拟键（op_id 走 OPID_DECOMP_SUMMARY，无撞键面）

# 诊断类型（constants.DIAGNOSTIC_TYPES 已登记）。
DIAG_OP_APPLIED = "landing.op_applied"
DIAG_OP_REPLAYED = "landing.op_replayed"
DIAG_COMPLETED = "landing.completed"
DIAG_STARTED = "landing.started"

_LAYOUT_DX = 260
_LAYOUT_DY = 140


# ---------------------------------------------------------------- landed 内容与 op 序列（纯函数）


@dataclass(frozen=True)
class LandingOp:
    """一个落地 op：op_id=幂等键（constants OPID_DECOMP_*）、request_hash=确定性指纹（只含 landed
    内容派生输入）、spec=处理器的确定性构建输入（生成的 ULID 不在此，落账本 payload）。"""

    op_id: str
    kind: str  # create_node / create_edge / create_merge_node / create_summary_node
    request_hash: str
    spec: dict[str, Any]


def landed_content(proposal: dict[str, Any]) -> dict[str, Any]:
    """landed 内容 = apply_adjustments(body, adjustments)（确定性；恢复语义根基 §9.2）。"""
    adjustments = proposal.get("adjustments") or []
    body = proposal["body"]
    assert isinstance(body, dict)
    return apply_adjustments(body, list(adjustments))


def _topo_order(node_ids: list[str], edges: list[tuple[str, str]]) -> list[str]:
    """Kahn 拓扑序，就绪集每轮取 temp_id 字典序最小（确定性平局破除；图已过 V9 无环）。"""
    indeg = {n: 0 for n in node_ids}
    adj: dict[str, list[str]] = {n: [] for n in node_ids}
    for a, b in edges:
        if a in adj and b in indeg:
            adj[a].append(b)
            indeg[b] += 1
    ready = sorted(n for n in node_ids if indeg[n] == 0)
    order: list[str] = []
    while ready:
        cur = ready.pop(0)
        order.append(cur)
        changed = False
        for m in adj[cur]:
            indeg[m] -= 1
            if indeg[m] == 0:
                ready.append(m)
                changed = True
        if changed:
            ready.sort()
    assert len(order) == len(node_ids), "落地图成环（应被 V9 拒于确认前）"
    return order


def _layout(keys: list[str], edges: list[tuple[str, str]]) -> dict[str, tuple[int, int]]:
    """最长路径分层坐标（照 templates._layout_positions 体例；含 merge/summary 虚拟键与虚拟边，
    使自动追加节点落在其依赖右侧）。同层按 keys 顺序纵向排开（keys=拓扑序+追加序，确定性）。"""
    depth = {k: 0 for k in keys}
    indeg = {k: 0 for k in keys}
    adj: dict[str, list[str]] = {k: [] for k in keys}
    for a, b in edges:
        if a in adj and b in indeg:
            adj[a].append(b)
            indeg[b] += 1
    queue = [k for k in keys if indeg[k] == 0]
    i = 0
    while i < len(queue):
        k = queue[i]
        i += 1
        for m in adj[k]:
            depth[m] = max(depth[m], depth[k] + 1)
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)
    row_count: dict[int, int] = {}
    pos: dict[str, tuple[int, int]] = {}
    for k in keys:
        d = depth[k]
        r = row_count.get(d, 0)
        row_count[d] = r + 1
        pos[k] = (d * _LAYOUT_DX, r * _LAYOUT_DY)
    return pos


def build_landing_plan(
    batch_id: str, landed: dict[str, Any], *, summary_owner: str
) -> list[list[LandingOp]]:
    """从 landed 内容确定性重建**步序列**（模块 docstring 排序规则；重启后同输入同序列）。

    每步 = 一个节点 op + 该节点全部入边 op（同一小事务原子提交——「已落地节点入边集恒完整」
    不变量的构造点）；summary_owner = proposal.proposed_by_member_id（行级稳定值，跨重放不变）。
    """
    raw_nodes = landed.get("nodes") or []
    nodes: dict[str, dict[str, Any]] = {}
    for n in raw_nodes:
        assert isinstance(n, dict) and isinstance(n.get("temp_id"), str)
        nodes[n["temp_id"]] = n
    edges: list[tuple[str, str]] = [
        (e["from"], e["to"])
        for e in (landed.get("edges") or [])
        if isinstance(e, dict)
    ]
    order = _topo_order(list(nodes), edges)
    mode = landed.get("mode")

    def is_agent(t: str) -> bool:
        return nodes[t].get("kind", "agent") == "agent"

    def is_code(t: str) -> bool:
        return nodes[t].get("writes_code") is True

    agent_ids = [t for t in order if is_agent(t)]
    code_ids = [t for t in order if is_code(t)]
    has_out = {a for a, _ in edges}
    leaves = [t for t in order if t not in has_out]

    has_explicit_merge = any(
        n.get("kind") == "system" and n.get("system_action") == "merge"
        for n in nodes.values()
    )
    add_merge = mode == "decompose" and bool(code_ids) and not has_explicit_merge
    add_summary = mode == "decompose" and len(agent_ids) > 1

    # merge deps = writes_code 前沿（无 writes_code 后代者）；逆拓扑传播 has_code_descendant。
    merge_deps: list[str] = []
    if add_merge:
        succ: dict[str, list[str]] = {t: [] for t in order}
        for a, b in edges:
            if a in succ and b in nodes:
                succ[a].append(b)
        has_code_desc: dict[str, bool] = {}
        for t in reversed(order):
            has_code_desc[t] = any(is_code(m) or has_code_desc[m] for m in succ[t])
        merge_deps = sorted(t for t in code_ids if not has_code_desc[t])

    summary_deps: list[str] = []
    if add_summary:
        if add_merge:
            summary_deps = [AUTO_MERGE_KEY] + sorted(t for t in leaves if not is_code(t))
        else:
            summary_deps = sorted(leaves)

    # 布局：全终图（提案节点 + 自动 merge + 汇总）一次分层，追加节点天然落依赖右侧。
    layout_keys = list(order)
    layout_edges = list(edges)
    if add_merge:
        layout_keys.append(AUTO_MERGE_KEY)
        layout_edges += [(d, AUTO_MERGE_KEY) for d in merge_deps]
    if add_summary:
        layout_keys.append(_SUMMARY_LAYOUT_KEY)
        layout_edges += [(d, _SUMMARY_LAYOUT_KEY) for d in summary_deps]
    pos = _layout(layout_keys, layout_edges)

    # 入边分组：edge (a,b) 归属节点 b 的步（拓扑序保证 a 已在先前步落地）。
    in_edges: dict[str, list[tuple[str, str]]] = {t: [] for t in nodes}
    for a, b in edges:
        if b in in_edges and a in nodes:
            in_edges[b].append((a, b))

    steps: list[list[LandingOp]] = []
    for t in order:
        node = nodes[t]
        x, y = pos[t]
        step: list[LandingOp] = [LandingOp(
            op_id=OPID_DECOMP_NODE.format(batch_id=batch_id, temp_id=t),
            kind="create_node",
            request_hash=fingerprint({"node": node, "pos": [x, y]}),
            spec={"temp_id": t, "node": node, "pos": [x, y]},
        )]
        for frm, to in sorted(in_edges[t]):
            step.append(LandingOp(
                op_id=OPID_DECOMP_EDGE.format(batch_id=batch_id, from_id=frm, to_id=to),
                kind="create_edge",
                request_hash=fingerprint({"from": frm, "to": to}),
                spec={"from": frm, "to": to},
            ))
        steps.append(step)
    if add_merge:
        x, y = pos[AUTO_MERGE_KEY]
        merge_spec: dict[str, Any] = {
            "temp_id": AUTO_MERGE_KEY, "deps": merge_deps, "pos": [x, y],
        }
        merge_hash_input: dict[str, Any] = {"deps": merge_deps, "pos": [x, y]}
        steps.append([LandingOp(
            op_id=OPID_DECOMP_NODE.format(batch_id=batch_id, temp_id=AUTO_MERGE_KEY),
            kind="create_merge_node",
            request_hash=fingerprint(merge_hash_input),
            spec=merge_spec,
        )])
    if add_summary:
        x, y = pos[_SUMMARY_LAYOUT_KEY]
        title = f"汇总交付：{len(agent_ids)} 个子任务"
        summary_spec: dict[str, Any] = {
            "title": title, "deps": summary_deps, "owner": summary_owner, "pos": [x, y],
        }
        steps.append([LandingOp(
            op_id=OPID_DECOMP_SUMMARY.format(batch_id=batch_id),
            kind="create_summary_node",
            request_hash=fingerprint(summary_spec),
            spec=summary_spec,
        )])
    return steps


# ---------------------------------------------------------------- op 处理器（每 op 一小事务内执行）


def _write_diag(
    conn: Connection,
    diag_type: str,
    *,
    workspace_id: str,
    channel_id: str,
    task_id: str | None,
    batch_id: str | None,
    payload: dict[str, Any],
) -> None:
    conn.execute(
        insert(_DIAG).values(
            workspace_id=workspace_id,
            agent_member_id=None,
            type=diag_type,
            channel_id=channel_id,
            task_id=task_id,
            batch_id=batch_id,
            payload=payload,
            created_at=service.now_iso(),
        )
    )


def _member_name(conn: Connection, member_id: str) -> str | None:
    return conn.execute(
        select(_MEMBER.c.name).where(_MEMBER.c.id == member_id, _MEMBER.c.removed_at.is_(None))
    ).scalar()


def _apply_create_node(
    tx: Any, ctx: _ExecContext, op: LandingOp
) -> dict[str, Any]:
    """提案节点 create_node 全链（照 routes/canvas.py:create_node / templates._instantiate_node
    体例）。agent：锚点系统消息 → L2 任务 → task_plan 作 TaskPlan 初稿 → agent 节点 → 按序广播；
    system：直接插 system 节点（idle 壳，无任务无锚点——同 canvas.py system 分支）。

    suggested_owner 落地语义 = O4 建议不锁定：任务 owner 恒 None，建议人选进锚点话术与「已落地」
    消息（claim 防重仍是唯一认领通道）。
    """
    node = op.spec["node"]
    temp_id = op.spec["temp_id"]
    x, y = op.spec["pos"]
    kind = node.get("kind", "agent")
    ts = service.now_iso()

    if kind == "system":
        node_row = canvas_service.insert_node(
            tx.conn,
            canvas_id=ctx.canvas["id"],
            kind=CanvasNodeKind.SYSTEM,
            task_id=None,
            is_summary=False,
            system_action=SystemAction(node["system_action"]),
            command=node.get("command"),
            system_status=SystemNodeStatus.IDLE,
            pos_x=x,
            pos_y=y,
            created_at=ts,
        )
        tx.emit(
            EventType.CANVAS_NODE_ADDED, ctx.channel_id, {"node": canvas_node_public(node_row)}
        )
        return {"temp_id": temp_id, "node_id": node_row["id"], "task_id": None}

    # agent 节点：锚点消息（含建议认领话术——建议人选进锚点，O4）。
    anchor_id = service.new_ulid()
    title = str(node.get("title") or "").strip() or "拆解任务节点"
    anchor_body = title
    suggested = node.get("suggested_owner")
    if isinstance(suggested, str):
        name = _member_name(tx.conn, suggested)
        if name is not None:
            anchor_body += f"\n建议认领：@{name}（建议非锁定，claim 后生效）"
    tx.conn.execute(
        insert(_MSG).values(
            id=anchor_id,
            workspace_id=ctx.workspace_id,
            channel_id=ctx.channel_id,
            thread_root_id=None,
            author_member_id=None,
            kind=MessageKind.SYSTEM,
            card_kind=None,
            card_ref=None,
            body=anchor_body,
            created_at=ts,
        )
    )
    task_row = tasks_service.create_task(
        tx,
        workspace_id=ctx.workspace_id,
        channel_id=ctx.channel_id,
        root_message_id=anchor_id,
        created_by=ctx.proposed_by,
        title=title,
        source_body=anchor_body,
        level=TaskLevel.L2,
        project_id=node.get("project"),
        writes_code=node.get("writes_code") is True,
    )
    contract_pub: dict[str, Any] | None = None
    plan = node.get("task_plan")
    if isinstance(plan, dict):
        # V10 已保证形状 ≥ TaskPlanBody 严格度（内核放行的提案落地不得爆炸——F7/F8 域不变量）。
        plan_body = TaskPlanBody.model_validate(plan)
        contract_row, _ = contracts_service.submit_contract(
            tx,
            task_id=task_row["id"],
            workspace_id=ctx.workspace_id,
            kind=ContractKind.TASK_PLAN,
            body_dict=plan_body.model_dump(mode="json"),
            created_by=ctx.proposed_by,
        )
        contract_pub = task_contract_public(contract_row)
    node_row = canvas_service.insert_node(
        tx.conn,
        canvas_id=ctx.canvas["id"],
        kind=CanvasNodeKind.AGENT,
        task_id=task_row["id"],
        is_summary=False,
        system_action=None,
        command=None,
        system_status=None,
        pos_x=x,
        pos_y=y,
        created_at=ts,
    )
    anchor_row = models.row_dict(
        tx.conn.execute(select(_MSG).where(_MSG.c.id == anchor_id)).mappings().first()
    )
    tx.emit(EventType.MESSAGE_CREATED, ctx.channel_id, {"message": message_public(anchor_row, [])})
    tasks_service.emit_task_created(tx, task_row)
    if contract_pub is not None:
        tx.emit(EventType.TASK_CONTRACT_CREATED, ctx.channel_id, {"contract": contract_pub})
    tx.emit(EventType.CANVAS_NODE_ADDED, ctx.channel_id, {"node": canvas_node_public(node_row)})
    return {
        "temp_id": temp_id,
        "node_id": node_row["id"],
        "task_id": task_row["id"],
        "message_id": anchor_id,
    }


def _insert_edge_idempotent(
    tx: Any, ctx: _ExecContext, from_node: str, to_node: str
) -> str | None:
    """SAVEPOINT triplet 幂等插边（照 templates._land_edges）：插入成功 emit edge_added 并回
    edge_id，triplet 撞唯一（重放/并发）→ None 不重复 emit。无环兜底不需：新节点与既有画布无
    交叉边、提案图已过 V9（确认时权威重验），落地连边不可能引入环。"""
    edge_id = service.new_ulid()
    try:
        with tx.conn.begin_nested():
            tx.conn.execute(
                insert(_EDGE).values(
                    id=edge_id,
                    canvas_id=ctx.canvas["id"],
                    from_node_id=from_node,
                    to_node_id=to_node,
                )
            )
    except IntegrityError:
        return None
    edge_row = models.row_dict(
        tx.conn.execute(select(_EDGE).where(_EDGE.c.id == edge_id)).mappings().first()
    )
    tx.emit(EventType.CANVAS_EDGE_ADDED, ctx.channel_id, {"edge": canvas_edge_public(edge_row)})
    return edge_id


def _apply_create_edge(tx: Any, ctx: _ExecContext, op: LandingOp) -> dict[str, Any]:
    frm, to = op.spec["from"], op.spec["to"]
    from_node = ctx.node_id(frm)
    to_node = ctx.node_id(to)
    edge_id = _insert_edge_idempotent(tx, ctx, from_node, to_node)
    return {"from": frm, "to": to, "from_node_id": from_node, "to_node_id": to_node,
            "edge_id": edge_id}


def _apply_create_merge_node(tx: Any, ctx: _ExecContext, op: LandingOp) -> dict[str, Any]:
    """自动 merge 系统节点（裁决 #6）：插 merge 节点（idle 壳）+ 依赖边（deps→merge）一体一 op。"""
    x, y = op.spec["pos"]
    node_row = canvas_service.insert_node(
        tx.conn,
        canvas_id=ctx.canvas["id"],
        kind=CanvasNodeKind.SYSTEM,
        task_id=None,
        is_summary=False,
        system_action=SystemAction.MERGE,
        command=None,
        system_status=SystemNodeStatus.IDLE,
        pos_x=x,
        pos_y=y,
        created_at=service.now_iso(),
    )
    tx.emit(EventType.CANVAS_NODE_ADDED, ctx.channel_id, {"node": canvas_node_public(node_row)})
    for dep in op.spec["deps"]:
        _insert_edge_idempotent(tx, ctx, ctx.node_id(dep), node_row["id"])
    return {"temp_id": op.spec["temp_id"], "node_id": node_row["id"], "task_id": None}


def _apply_create_summary_node(tx: Any, ctx: _ExecContext, op: LandingOp) -> dict[str, Any]:
    """汇总节点（§9.1 ③/裁决 #16）：is_summary 任务节点 + owner=Orchestrator（proposed_by，创建期
    直写 owner 列——非流转不写 task_events，task.created 事件已携 owner）+ 依赖边（deps → 汇总）。
    无 TaskPlan 初稿（引擎不代拟验收标准；升格补契约路径可用）。"""
    x, y = op.spec["pos"]
    title = op.spec["title"]
    owner = op.spec["owner"]
    ts = service.now_iso()
    anchor_id = service.new_ulid()
    tx.conn.execute(
        insert(_MSG).values(
            id=anchor_id,
            workspace_id=ctx.workspace_id,
            channel_id=ctx.channel_id,
            thread_root_id=None,
            author_member_id=None,
            kind=MessageKind.SYSTEM,
            card_kind=None,
            card_ref=None,
            body=title,
            created_at=ts,
        )
    )
    task_row = tasks_service.create_task(
        tx,
        workspace_id=ctx.workspace_id,
        channel_id=ctx.channel_id,
        root_message_id=anchor_id,
        created_by=ctx.proposed_by,
        title=title,
        source_body=title,
        level=TaskLevel.L2,
    )
    tx.conn.execute(
        update(_TASK).where(_TASK.c.id == task_row["id"]).values(owner_member_id=owner)
    )
    task_row = tasks_service.fetch_task(tx.conn, task_row["id"])
    node_row = canvas_service.insert_node(
        tx.conn,
        canvas_id=ctx.canvas["id"],
        kind=CanvasNodeKind.AGENT,
        task_id=task_row["id"],
        is_summary=True,
        system_action=None,
        command=None,
        system_status=None,
        pos_x=x,
        pos_y=y,
        created_at=ts,
    )
    anchor_row = models.row_dict(
        tx.conn.execute(select(_MSG).where(_MSG.c.id == anchor_id)).mappings().first()
    )
    tx.emit(EventType.MESSAGE_CREATED, ctx.channel_id, {"message": message_public(anchor_row, [])})
    tasks_service.emit_task_created(tx, task_row)
    tx.emit(EventType.CANVAS_NODE_ADDED, ctx.channel_id, {"node": canvas_node_public(node_row)})
    for dep in op.spec["deps"]:
        _insert_edge_idempotent(tx, ctx, ctx.node_id(dep), node_row["id"])
    return {"node_id": node_row["id"], "task_id": task_row["id"], "message_id": anchor_id}


_HANDLERS = {
    "create_node": _apply_create_node,
    "create_edge": _apply_create_edge,
    "create_merge_node": _apply_create_merge_node,
    "create_summary_node": _apply_create_summary_node,
}


# ---------------------------------------------------------------- 执行器


class _ExecContext:
    """一次执行运行的跨 op 上下文：temp_id → 落地产物（node_id/task_id）映射（hit 路径从账本
    payload 复原、new 路径从处理器返回值累积）+ 频道/画布/提案静态信息。"""

    def __init__(
        self,
        *,
        workspace_id: str,
        channel_id: str,
        canvas: dict[str, Any],
        proposed_by: str,
        source_task_id: str,
        existing_node_ids: set[str] | None = None,
    ) -> None:
        self.workspace_id = workspace_id
        self.channel_id = channel_id
        self.canvas = canvas
        self.proposed_by = proposed_by
        self.source_task_id = source_task_id
        self.by_temp: dict[str, dict[str, Any]] = {}
        # delta：边端点可引用现画布节点 ULID（静态快照）；decomp 恒空（仅 temp_id 内部命名）。
        self.existing_node_ids: set[str] = existing_node_ids or set()

    def absorb(self, payload: dict[str, Any]) -> None:
        temp_id = payload.get("temp_id")
        if isinstance(temp_id, str):
            self.by_temp[temp_id] = payload

    def node_id(self, ref: str) -> str:
        """解析边端点 ref → 落地节点 ULID：新增节点走 by_temp 映射，现画布节点（delta）原样返回。"""
        entry = self.by_temp.get(ref)
        if entry is not None:
            return entry["node_id"]
        if ref in self.existing_node_ids:
            return ref
        raise AssertionError(f"edge 引用的节点 '{ref}' 尚未落地（op 序列构造缺陷）")


def _fail_close_batch(
    tx: Any, batch: LandingBatchRow, proposal: dict[str, Any] | None, *, reason: str
) -> None:
    """执行器内 fail-closed（提交路径——inline 处置随本小事务提交即持久，§9.2 规则 2）：
    批处置链（mark_fail_closed：status/诊断/告警卡/activity）+ proposal landing→failed（裁量：
    终态腾出部分唯一索引，人类处理后可重新触发拆解；M6a #10 fail-closed 注释先例同向）+
    landing.fail_closed / proposal.updated 广播。"""
    service.mark_fail_closed(tx.conn, batch.id, reason=reason)
    fresh_batch = service._fetch_batch(tx.conn, batch.id)
    assert fresh_batch is not None
    tx.emit(
        EventType.LANDING_FAIL_CLOSED, batch.channel_id,
        {"batch": fresh_batch.model_dump(mode="json")},
    )
    if proposal is not None and proposal["status"] == ProposalStatus.LANDING.value:
        failed = proposal_domain._transition(tx, proposal, ProposalStatus.FAILED)
        tx.emit(
            EventType.PROPOSAL_UPDATED, failed["channel_id"],
            {"proposal": proposal_public(failed)},
        )


def _fetch_proposal_for_batch(conn: Connection, batch: LandingBatchRow) -> dict[str, Any] | None:
    row = conn.execute(
        select(_PROPOSAL).where(_PROPOSAL.c.id == batch.source_ref)
    ).mappings().first()
    return models.row_dict(row) if row is not None else None


class _OpRaced(Exception):
    """record 返回非 new（并发对手在 lookup 与 record 间抢先落账）：回滚本步小事务重跑整步
    （重跑 lookup 命中 hit 复用对手产物）。单进程下不可达（landing lock 串行），跨进程防线。"""


class _StepMismatch(Exception):
    """步内某 op 同键异指纹（§9.2 规则 2）：中止本步事务（步内已做 op 一并回滚——失败批不留
    半步产物），由外层在独立收尾事务里 fail-closed 处置（提交即持久）。"""

    def __init__(self, op_id: str) -> None:
        super().__init__(op_id)
        self.op_id = op_id


class _NodeBecameActive(Exception):
    """delta 执行期复核（J10）：remove_node 目标任务在确认后转 in_progress/in_review（或系统节点
    running）——整批 fail-closed（不静默删活动节点）。中止本步事务，外层独立收尾 fail-closed。"""

    def __init__(self, node_id: str) -> None:
        super().__init__(node_id)
        self.node_id = node_id


def execute_batch(engine: Engine, bus: EventBus, batch_id: str) -> str:
    """执行/续跑一个落地批次（§9.2 恢复语义）。返回 'landed' | 'fail_closed' | 'already_done'。

    按 batch.kind 分派：decomp（J9 现路径不动）与 delta（J10）共用步进 runner（_run_steps）与
    fail-closed 处置，各自构造步序列 + :done 事务（decomp/delta 前缀不同、增删语义不同）。
    """
    with engine.connect() as conn:
        batch = service._fetch_batch(conn, batch_id)
        assert batch is not None, f"unknown batch_id: {batch_id}"
        if batch.done_at is not None or batch.status == LandingBatchStatus.DONE:
            return "already_done"
        if batch.status == LandingBatchStatus.FAIL_CLOSED:
            return "fail_closed"
        proposal = _fetch_proposal_for_batch(conn, batch)
        canvas = canvas_service.fetch_canvas_by_channel(conn, batch.channel_id)

    if proposal is None or canvas is None:
        # 数据完整性破坏（提案行/画布消失）——fail-closed 告警，不静默。
        with gateway_tx(engine, bus) as tx:
            _fail_close_batch(tx, batch, None, reason="landing source missing")
        return "fail_closed"

    if batch.kind == LandingBatchKind.DELTA.value:
        return _execute_delta_batch(engine, bus, batch, proposal, canvas)
    return _execute_decomp_batch(engine, bus, batch, proposal, canvas)


def _run_steps(
    engine: Engine,
    bus: EventBus,
    batch: LandingBatchRow,
    proposal: dict[str, Any],
    ctx: _ExecContext,
    steps: list[list[LandingOp]],
    handlers: dict[str, Any],
) -> str:
    """共享步进 runner（decomp/delta 同款，避免双实现漂移）：每步一个 gateway_tx，步内逐 op
    lookup 三态——hit 跳过（op_replayed 诊断）、mismatch/NODE_ACTIVE 中止本步并独立事务 fail-closed
    停批、absent 执行处理器 + record + op_applied 诊断；步事务提交即持久（kill 后从已提交前缀续跑，
    步内崩溃整步回滚重跑）。返回 'ok'（全步过）| 'fail_closed'。"""
    for step in steps:
        try:
            for _attempt in range(2):  # _OpRaced 重跑整步一次（重跑对手已提交 op 必 hit）
                try:
                    with gateway_tx(engine, bus) as tx:
                        for op in step:
                            look = service.lookup(tx.conn, op.op_id, op.request_hash)
                            if look["status"] == "hit":
                                payload = look["entry"].payload
                                assert isinstance(payload, dict)
                                ctx.absorb(payload)
                                _write_diag(
                                    tx.conn, DIAG_OP_REPLAYED,
                                    workspace_id=batch.workspace_id,
                                    channel_id=batch.channel_id,
                                    task_id=proposal["source_task_id"], batch_id=batch.id,
                                    payload={"op_id": op.op_id, "kind": op.kind},
                                )
                                continue
                            if look["status"] == "mismatch":
                                raise _StepMismatch(op.op_id)
                            payload = handlers[op.kind](tx, ctx, op)
                            res = service.record(
                                tx.conn, op.op_id, op.kind, payload,
                                request_hash=op.request_hash, batch_id=batch.id,
                                actor=None,
                            )
                            if res["status"] != "new":
                                raise _OpRaced(op.op_id)
                            ctx.absorb(payload)
                            _write_diag(
                                tx.conn, DIAG_OP_APPLIED,
                                workspace_id=batch.workspace_id, channel_id=batch.channel_id,
                                task_id=proposal["source_task_id"], batch_id=batch.id,
                                payload={"op_id": op.op_id, "kind": op.kind},
                            )
                    break  # 本步提交成功
                except _OpRaced:
                    continue  # 回滚本步小事务后重跑整步：lookup 命中对手已提交产物
            else:  # pragma: no cover - 两跑仍竞态（理论不可达）
                raise AssertionError(f"landing step 竞态未收敛：{step[0].op_id}")
        except _StepMismatch as exc:
            with gateway_tx(engine, bus) as tx:
                _fail_close_batch(
                    tx, batch, proposal, reason=f"op fingerprint mismatch: {exc.op_id}"
                )
            return "fail_closed"
        except _NodeBecameActive as exc:
            # delta 执行期复核：目标节点已转活动 → 整批 fail-closed（步事务已回滚，无残留）。
            with gateway_tx(engine, bus) as tx:
                _fail_close_batch(
                    tx, batch, proposal, reason=f"node became active: {exc.node_id}"
                )
            return "fail_closed"
    return "ok"


def _execute_decomp_batch(
    engine: Engine, bus: EventBus, batch: LandingBatchRow,
    proposal: dict[str, Any], canvas: dict[str, Any],
) -> str:
    """decomp 批次执行（J9 现路径）：landed 内容重算校验 → 步序列 → 步进 runner → :done 事务。"""
    landed = landed_content(proposal)
    if proposal_fingerprint(landed) != batch.content_hash:
        with gateway_tx(engine, bus) as tx:
            _fail_close_batch(tx, batch, proposal, reason="landed content recompute mismatch")
        return "fail_closed"

    steps = build_landing_plan(
        batch.id, landed, summary_owner=proposal["proposed_by_member_id"]
    )
    ctx = _ExecContext(
        workspace_id=batch.workspace_id,
        channel_id=batch.channel_id,
        canvas=canvas,
        proposed_by=proposal["proposed_by_member_id"],
        source_task_id=proposal["source_task_id"],
    )
    if _run_steps(engine, bus, batch, proposal, ctx, steps, _HANDLERS) == "fail_closed":
        return "fail_closed"

    # :done 事务（§9.1 ⑤：消息只在 :done 后发；baseline bump 批末恰一次）。
    with gateway_tx(engine, bus) as tx:
        done_oid = OPID_DECOMP_DONE.format(batch_id=batch.id)
        res = service.record(
            tx.conn, done_oid, "mark_done", {"batch_id": batch.id}, batch_id=batch.id
        )
        if res["status"] == "hit":
            return "landed"  # 并发完成者已收尾（消息/转态齐备），本跑零副作用
        assert res["status"] == "new"  # payload 恒 {batch_id} → 不可能 mismatch
        service.mark_done(tx.conn, batch.id)

        _bump_and_post_done(tx, batch, canvas)
        _post_landed_message(tx, ctx, batch, proposal, landed)
        _finish_landed(tx, batch, proposal)
    return "landed"


def _bump_and_post_done(tx: Any, batch: LandingBatchRow, canvas: dict[str, Any]) -> None:
    """baseline bump（批末恰一次）+ CANVAS_BASELINE_ADVANCED 广播（decomp/delta 共用）。"""
    version, hash_, changed = canvas_service.advance_baseline(tx, canvas["id"])
    if changed:
        tx.emit(
            EventType.CANVAS_BASELINE_ADVANCED, batch.channel_id,
            {"canvas_id": canvas["id"], "baseline_version": version, "baseline_hash": hash_},
        )


def _finish_landed(tx: Any, batch: LandingBatchRow, proposal: dict[str, Any]) -> None:
    """landing→landed 转态 + LANDING_COMPLETED + landing.completed 诊断（decomp/delta 共用）。"""
    fresh = proposal_domain.fetch_proposal(tx.conn, proposal["id"])
    assert fresh is not None
    if fresh["status"] == ProposalStatus.LANDING.value:
        fresh = proposal_domain._transition(tx, fresh, ProposalStatus.LANDED)
        tx.emit(
            EventType.PROPOSAL_UPDATED, fresh["channel_id"],
            {"proposal": proposal_public(fresh)},
        )
    done_batch = service._fetch_batch(tx.conn, batch.id)
    assert done_batch is not None
    tx.emit(
        EventType.LANDING_COMPLETED, batch.channel_id,
        {"batch": done_batch.model_dump(mode="json")},
    )
    _write_diag(
        tx.conn, DIAG_COMPLETED,
        workspace_id=batch.workspace_id, channel_id=batch.channel_id,
        task_id=proposal["source_task_id"], batch_id=batch.id,
        payload={"batch_id": batch.id, "landed_hash": batch.content_hash},
    )


# ---------------------------------------------------------------- delta 落地（J10；拆解设计 §11）


def build_delta_plan(
    batch_id: str, operations: list[Any], removed: set[int]
) -> list[list[LandingOp]]:
    """从 delta operations（剔除 removed_ops）确定性重建步序列——op_id 用**原始下标** OPID_DELTA_OP
    （重启从 DB body+adjustments 重算同序列，M5b 教训 #C）。步序（拆解设计 §11 落地，确定性）：
      ① remove_edge 逐 op 一步 → ② remove_node 逐 op 一步（级联删关联边）→
      ③ add_node 按新增子图拓扑序（平局 temp_id 字典序）成步，每步 = 该节点 create_node + 其全部
         入边 create_edge 同一 gateway_tx（「已落地节点入边集恒完整」不变量，同 J9 步原子）→
      ④ 其余 add_edge（现→现、新→现）逐 op 一步。
    """
    kept: list[tuple[int, dict[str, Any]]] = [
        (i, op) for i, op in enumerate(operations)
        if i not in removed and isinstance(op, dict)
    ]
    steps: list[list[LandingOp]] = []

    for i, op in kept:  # ① remove_edge
        if op.get("op") == "remove_edge":
            steps.append([LandingOp(
                op_id=OPID_DELTA_OP.format(batch_id=batch_id, index=i),
                kind="delta_remove_edge",
                request_hash=fingerprint({"from": op["from"], "to": op["to"]}),
                spec={"from": op["from"], "to": op["to"]},
            )])
    for i, op in kept:  # ② remove_node
        if op.get("op") == "remove_node":
            steps.append([LandingOp(
                op_id=OPID_DELTA_OP.format(batch_id=batch_id, index=i),
                kind="delta_remove_node",
                request_hash=fingerprint({"node_id": op["node_id"]}),
                spec={"node_id": op["node_id"]},
            )])

    add_node_ops = [(i, op) for i, op in kept if op.get("op") == "add_node"]
    add_edge_ops = [(i, op) for i, op in kept if op.get("op") == "add_edge"]
    added_temp = [op["node"]["temp_id"] for _i, op in add_node_ops]
    added_set = set(added_temp)
    node_op_index = {op["node"]["temp_id"]: i for i, op in add_node_ops}
    node_by_temp = {op["node"]["temp_id"]: op["node"] for _i, op in add_node_ops}

    in_edges: dict[str, list[tuple[int, str, str]]] = {t: [] for t in added_temp}
    add_edge_existing: list[tuple[int, str, str]] = []
    inner_edges: list[tuple[str, str]] = []
    for i, op in add_edge_ops:
        frm, to = op["from"], op["to"]
        if to in added_set:
            in_edges[to].append((i, frm, to))
            if frm in added_set:
                inner_edges.append((frm, to))
        else:
            add_edge_existing.append((i, frm, to))

    order = _topo_order(added_temp, inner_edges)
    pos = _layout(order, inner_edges)
    for t in order:  # ③ add_node + 其全部入边
        node = node_by_temp[t]
        x, y = pos[t]
        step: list[LandingOp] = [LandingOp(
            op_id=OPID_DELTA_OP.format(batch_id=batch_id, index=node_op_index[t]),
            kind="create_node",
            request_hash=fingerprint({"node": node, "pos": [x, y]}),
            spec={"temp_id": t, "node": node, "pos": [x, y]},
        )]
        for i, frm, to in sorted(in_edges[t], key=lambda e: (e[1], e[2])):
            step.append(LandingOp(
                op_id=OPID_DELTA_OP.format(batch_id=batch_id, index=i),
                kind="create_edge",
                request_hash=fingerprint({"from": frm, "to": to}),
                spec={"from": frm, "to": to},
            ))
        steps.append(step)
    for i, frm, to in add_edge_existing:  # ④ 其余 add_edge
        steps.append([LandingOp(
            op_id=OPID_DELTA_OP.format(batch_id=batch_id, index=i),
            kind="create_edge",
            request_hash=fingerprint({"from": frm, "to": to}),
            spec={"from": frm, "to": to},
        )])
    return steps


def _node_active_at_exec(conn: Connection, node: dict[str, Any]) -> bool:
    if node["kind"] == CanvasNodeKind.AGENT.value:
        tid = node.get("task_id")
        if tid is None:
            return False
        status = conn.execute(select(_TASK.c.status).where(_TASK.c.id == tid)).scalar()
        return status in (TaskStatus.IN_PROGRESS.value, TaskStatus.IN_REVIEW.value)
    return node.get("system_status") == SystemNodeStatus.RUNNING.value


def _apply_delta_remove_edge(tx: Any, ctx: _ExecContext, op: LandingOp) -> dict[str, Any]:
    """删除现画布边（幂等：目标边已消失 → 无操作成功落账）。from/to = 现画布节点 ULID。"""
    frm, to = op.spec["from"], op.spec["to"]
    edge = tx.conn.execute(
        select(_EDGE).where(
            _EDGE.c.canvas_id == ctx.canvas["id"],
            _EDGE.c.from_node_id == frm,
            _EDGE.c.to_node_id == to,
        )
    ).mappings().first()
    edge_id: str | None = None
    if edge is not None:
        edge_id = str(edge["id"])
        canvas_service.delete_edge(tx.conn, edge_id)
        tx.emit(EventType.CANVAS_EDGE_REMOVED, ctx.channel_id, {"edge_id": edge_id})
    return {"op": "remove_edge", "from": frm, "to": to, "edge_id": edge_id}


def _apply_delta_remove_node(tx: Any, ctx: _ExecContext, op: LandingOp) -> dict[str, Any]:
    """删除现画布节点（解除引用不删任务，同 C8）：执行期复核目标非活动（in_progress/in_review 或
    running system → _NodeBecameActive 整批 fail-closed）；目标已消失 → 幂等无操作。级联删边。"""
    node_id = op.spec["node_id"]
    node = canvas_service.fetch_node(tx.conn, ctx.canvas["id"], node_id)
    if node is None:
        return {"op": "remove_node", "node_id": node_id, "removed": False}
    if _node_active_at_exec(tx.conn, node):
        raise _NodeBecameActive(node_id)
    for edge in canvas_service.incident_edges(tx.conn, ctx.canvas["id"], node_id):
        canvas_service.delete_edge(tx.conn, edge["id"])
        tx.emit(EventType.CANVAS_EDGE_REMOVED, ctx.channel_id, {"edge_id": edge["id"]})
    canvas_service.delete_node(tx.conn, node_id)
    tx.emit(EventType.CANVAS_NODE_REMOVED, ctx.channel_id, {"node_id": node_id})
    return {"op": "remove_node", "node_id": node_id, "removed": True}


_DELTA_HANDLERS = {
    "create_node": _apply_create_node,       # 复用 J9 提案节点全链
    "create_edge": _apply_create_edge,       # 复用 J9 幂等插边（ctx.node_id 解析现节点/新增）
    "delta_remove_edge": _apply_delta_remove_edge,
    "delta_remove_node": _apply_delta_remove_node,
}


def _execute_delta_batch(
    engine: Engine, bus: EventBus, batch: LandingBatchRow,
    proposal: dict[str, Any], canvas: dict[str, Any],
) -> str:
    """delta 批次执行（J10）：剔除后 landed 内容重算指纹校验（fail-closed 兜底）→ 步序列 → 步进
    runner → :done 事务（baseline bump + 「增量已落地」系统消息 + landing→landed，批末恰一次）。"""
    body = proposal["body"]
    assert isinstance(body, dict)
    operations = list(body.get("operations") or [])
    removed = {int(i) for i in (proposal.get("adjustments") or [])}
    remaining = {
        **body, "operations": [op for i, op in enumerate(operations) if i not in removed]
    }
    if proposal_fingerprint(remaining) != batch.content_hash:
        with gateway_tx(engine, bus) as tx:
            _fail_close_batch(
                tx, batch, proposal, reason="delta landed content recompute mismatch"
            )
        return "fail_closed"

    with engine.connect() as conn:
        existing_ids = set(canvas_service.node_ids(conn, canvas["id"]))
    steps = build_delta_plan(batch.id, operations, removed)
    ctx = _ExecContext(
        workspace_id=batch.workspace_id,
        channel_id=batch.channel_id,
        canvas=canvas,
        proposed_by=proposal["proposed_by_member_id"],
        source_task_id=proposal["source_task_id"],
        existing_node_ids=existing_ids,
    )
    if _run_steps(engine, bus, batch, proposal, ctx, steps, _DELTA_HANDLERS) == "fail_closed":
        return "fail_closed"

    with gateway_tx(engine, bus) as tx:
        done_oid = ledger_replay.done_op_id("delta", batch.id)
        res = service.record(
            tx.conn, done_oid, "mark_done", {"batch_id": batch.id}, batch_id=batch.id
        )
        if res["status"] == "hit":
            return "landed"  # 并发完成者已收尾
        assert res["status"] == "new"
        service.mark_done(tx.conn, batch.id)
        _bump_and_post_done(tx, batch, canvas)
        _post_delta_landed_message(tx, batch, proposal, operations, removed)
        _finish_landed(tx, batch, proposal)
    return "landed"


def _post_delta_landed_message(
    tx: Any, batch: LandingBatchRow, proposal: dict[str, Any],
    operations: list[Any], removed: set[int],
) -> None:
    """「增量已落地」系统消息（恰一条，:done 事务内，source 线程）：增删摘要 + 剔除数。"""
    from coagentia_server.messages import service as messages_service

    kept = [op for i, op in enumerate(operations) if i not in removed and isinstance(op, dict)]
    add_nodes = sum(1 for op in kept if op.get("op") == "add_node")
    remove_nodes = sum(1 for op in kept if op.get("op") == "remove_node")
    add_edges = sum(1 for op in kept if op.get("op") == "add_edge")
    remove_edges = sum(1 for op in kept if op.get("op") == "remove_edge")
    text = (
        f"增量已落地（rev.{proposal['revision']}）："
        f"新增 {add_nodes} 节点 / {add_edges} 边，删除 {remove_nodes} 节点 / {remove_edges} 边。"
    )
    if removed:
        text += f" 已剔除 {len(removed)} 项操作。"
    source_root = tx.conn.execute(
        select(_TASK.c.root_message_id).where(_TASK.c.id == proposal["source_task_id"])
    ).scalar()
    messages_service.post_system_message(
        tx,
        workspace_id=batch.workspace_id,
        channel_id=batch.channel_id,
        body=text,
        thread_root_id=source_root,
    )


def _post_landed_message(
    tx: Any,
    ctx: _ExecContext,
    batch: LandingBatchRow,
    proposal: dict[str, Any],
    landed: dict[str, Any],
) -> None:
    """「已落地」系统消息（恰一条，:done 事务内）：节点清单 + @激活节点建议 owner 唤醒话术。

    mention 集（§9.3 裁量登记）= 落地后**立即激活**（无上游依赖）的 agent 节点的 suggested_owner
    去重（成员在册未软删）；blocked 下游节点的建议人不 @（不过早唤醒，激活由 gating 推进接管）。
    """
    from coagentia_server.messages import service as messages_service

    raw_nodes = [n for n in (landed.get("nodes") or []) if isinstance(n, dict)]
    edges = [
        (e.get("from"), e.get("to"))
        for e in (landed.get("edges") or [])
        if isinstance(e, dict)
    ]
    has_upstream = {b for _a, b in edges}

    lines: list[str] = []
    mention_ids: list[str] = []
    for node in raw_nodes:
        temp_id = node.get("temp_id")
        entry = ctx.by_temp.get(temp_id) if isinstance(temp_id, str) else None
        if node.get("kind", "agent") == "system":
            lines.append(f"- 系统节点：{node.get('system_action')}")
            continue
        task_id = entry.get("task_id") if entry else None
        task = tasks_service.fetch_task(tx.conn, task_id) if task_id else None
        label = f"#{task['number']} {task['title']}" if task else str(node.get("title") or "")
        suggested = node.get("suggested_owner")
        activated = temp_id not in has_upstream
        if isinstance(suggested, str):
            name = _member_name(tx.conn, suggested)
            if name is not None:
                if activated:
                    label += f"（已激活，建议认领：@{name}）"
                    if suggested not in mention_ids:
                        mention_ids.append(suggested)
                else:
                    label += f"（待上游解锁，建议认领：{name}）"
        elif not activated:
            label += "（待上游解锁）"
        lines.append(f"- {label}")

    auto_merge = ctx.by_temp.get(AUTO_MERGE_KEY)
    if auto_merge is not None:
        lines.append("- 系统节点：merge（引擎自动追加，全部代码任务合并后推进下游）")
    body = (
        f"拆解已落地：{len(raw_nodes)} 个提案节点已上画布"
        f"（批次 {batch.id[-8:]}，rev.{proposal['revision']}）。\n" + "\n".join(lines)
    )
    if mention_ids:
        body += "\n已激活节点可立即认领开工（claim 防重，建议非锁定）。"

    source_task = tx.conn.execute(
        select(_TASK.c.root_message_id).where(_TASK.c.id == proposal["source_task_id"])
    ).scalar()
    messages_service.post_system_message(
        tx,
        workspace_id=batch.workspace_id,
        channel_id=batch.channel_id,
        body=body,
        thread_root_id=source_task,
        mention_member_ids=mention_ids,
    )


# ---------------------------------------------------------------- 对账 #4 + 直落扫描


def _direct_orphan_ids(engine: Engine, proposal_kind: str, batch_kind: str) -> list[str]:
    """直落孤儿 = status=landing 且给定 kind 且无同 kind 落地批的提案（confirm 事务原子建批，故
    无批的 landing 提案只可能来自 J8/J10 直落分支）。"""
    with engine.connect() as conn:
        return list(conn.execute(
            select(_PROPOSAL.c.id)
            .where(
                _PROPOSAL.c.status == ProposalStatus.LANDING.value,
                _PROPOSAL.c.kind == proposal_kind,
                ~select(_BATCH.c.id)
                .where(_BATCH.c.kind == batch_kind, _BATCH.c.source_ref == _PROPOSAL.c.id)
                .exists(),
            )
            .order_by(_PROPOSAL.c.id)
        ).scalars())


def _create_direct_batch(
    engine: Engine, bus: EventBus, pid: str, batch_kind: LandingBatchKind
) -> str | None:
    """为一个直落孤儿建批（content_hash=landed_hash=proposal_hash、adjustments 保持 []、
    confirmed_by=auto(channel-policy)）；返回 batch_id 或 None（竞态已被推进/建批）。"""
    with gateway_tx(engine, bus) as tx:
        proposal = proposal_domain.fetch_proposal(tx.conn, pid)
        if proposal is None or proposal["status"] != ProposalStatus.LANDING.value:
            return None  # 竞态：状态已被推进
        exists = tx.conn.execute(
            select(_BATCH.c.id).where(
                _BATCH.c.kind == batch_kind.value, _BATCH.c.source_ref == pid
            )
        ).first()
        if exists is not None:
            return None  # 竞态：对手已建批
        tx.conn.execute(
            update(_PROPOSAL).where(_PROPOSAL.c.id == pid)
            .values(landed_hash=proposal["proposal_hash"])
        )
        batch = service.create_batch(
            tx.conn,
            workspace_id=proposal["workspace_id"],
            channel_id=proposal["channel_id"],
            kind=batch_kind,
            content_hash=proposal["proposal_hash"],
            source_ref=pid,
            confirmed_by=AUTO_CONFIRMED_BY,
        )
        tx.emit(
            EventType.LANDING_STARTED, proposal["channel_id"],
            {"batch": batch.model_dump(mode="json")},
        )
        _write_diag(
            tx.conn, DIAG_STARTED,
            workspace_id=proposal["workspace_id"], channel_id=proposal["channel_id"],
            task_id=proposal["source_task_id"], batch_id=batch.id,
            payload={
                "batch_id": batch.id, "proposal_id": pid,
                "landed_hash": proposal["proposal_hash"], "mode": "direct",
            },
        )
        return batch.id


def pending_landing_scan(engine: Engine, bus: EventBus) -> dict[str, Any]:
    """落地待办扫描（hub 启动/周期/事件触发共用；幂等重入安全——record 三态兜）：

    ① 直落孤儿建批：status=landing 且无同 kind 批的 full（→ decomp 批）与 delta（→ delta 批）提案，
      confirmed_by='auto(channel-policy)'、content_hash=landed_hash=proposal_hash、adjustments=[]；
    ② kind ∈ {decomp, delta} 且 running 的批次（含 ① 新建）按 id 序 execute_batch（按 kind 分派）。

    返回 {"created": [...batch_id], "executed": {batch_id: result}}。
    """
    created: list[str] = []
    for pid in _direct_orphan_ids(engine, "full", LandingBatchKind.DECOMP.value):
        bid = _create_direct_batch(engine, bus, pid, LandingBatchKind.DECOMP)
        if bid is not None:
            created.append(bid)
    for pid in _direct_orphan_ids(engine, "delta", LandingBatchKind.DELTA.value):
        bid = _create_direct_batch(engine, bus, pid, LandingBatchKind.DELTA)
        if bid is not None:
            created.append(bid)

    with engine.connect() as conn:
        running = conn.execute(
            select(_BATCH.c.id)
            .where(
                _BATCH.c.kind.in_(
                    [LandingBatchKind.DECOMP.value, LandingBatchKind.DELTA.value]
                ),
                _BATCH.c.status == LandingBatchStatus.RUNNING.value,
            )
            .order_by(_BATCH.c.id)
        ).scalars().all()

    executed: dict[str, str] = {}
    for bid in running:
        executed[bid] = execute_batch(engine, bus, bid)
    return {"created": created, "executed": executed}


__all__ = [
    "AUTO_CONFIRMED_BY",
    "AUTO_MERGE_KEY",
    "LandingOp",
    "build_delta_plan",
    "build_landing_plan",
    "execute_batch",
    "landed_content",
    "pending_landing_scan",
]
