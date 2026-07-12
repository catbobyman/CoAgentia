"""草稿确认域（J9；契约 B §5 S2 / §12.4、拆解设计 §8）——confirm CAS + 调整应用 + reject。

**adjustments op 精确形状**（拆解设计 §8.2 六种；B-M6-2 客户端与 J10 delta 部分接受同构消费）：

    {"op": "add_node",  "node": {…§5.1 node 全形状…}}      # temp_id 不得与现有节点冲突
    {"op": "remove_node", "temp_id": "N4"}                  # 连带删除该节点的全部关联边
    {"op": "add_edge",  "from": "N1", "to": "N2"}           # 已存在同 (from,to) 边 → 幂等忽略
    {"op": "remove_edge", "from": "N1", "to": "N2"}         # 目标边不存在 → 422（悬空引用）
    {"op": "edit_node", "temp_id": "N1", "changes": {…}}    # changes 键 ⊆ EDIT_NODE_FIELDS
    {"op": "edit_merge_plan", "merge_plan": "…" | null}

应用规则（确定性）：
- 按 adjustments 列表序**顺序应用**；每个 op 除上列键外不接受额外键（additionalProperties=false 同构
  V3 纪律）；未知 op / 引用悬空（remove/edit 目标不存在、add_node temp_id 冲突）→ 422
  VALIDATION_FAILED（details 含 index/reason，客户端可逐条定位）。
- `edit_node.changes` 允许键 = {title, task_plan, suggested_owner, project, writes_code, command}
  （§8.2 列举面；temp_id 是身份不可改，kind/system_action 类型变更 = remove+add，不提供改型面）。
  值逐键整体替换（null 合法——清空 suggested_owner/project）；值类型错误由结果图全量重验兜底。
- `add_edge` 端点存在性**不在应用期检查**（结果图重验 EDGE_UNKNOWN_NODE 全量收集统一报）；
  `remove_edge` 删除全部匹配 (from,to) 边。
- 结果 = 纯函数 apply_adjustments(proposals.body, proposals.adjustments)——落地执行器（landing.py）
  重启后从 DB 重算同一 landed 内容（恢复语义根基，拆解设计 §9.2）。

确认 CAS（B §5）：expected 三字段（proposal_hash / baseline_version / baseline_hash）任一不符 →
409 STALE_CONFIRM 携最新态 {proposal, baseline_version, baseline_hash}；调整后结果图过
kernel.validate_proposal 权威全量重验（env 现时重取——成员/绑定可能已变）；通过则落账
adjustments/landed_hash → awaiting_confirm→landing → create_batch(kind=decomp) → 202。
**confirm 事务不建任何画布节点**——落地是异步增量执行（landing.py 执行器）。
"""

from __future__ import annotations

import copy
from typing import Any

from coagentia_contracts import rest
from coagentia_contracts.enums import LandingBatchKind, ProposalKind, ProposalStatus
from coagentia_contracts.ws import EventType
from sqlalchemy import select, update
from sqlalchemy.engine import Connection

from coagentia_server.api import ApiError
from coagentia_server.db import models
from coagentia_server.ledger import service
from coagentia_server.messages import service as messages_service
from coagentia_server.orchestration import proposal as proposal_domain
from coagentia_server.routes.serialize import proposal_public

_PROPOSAL = models.tbl(models.Proposal)
_TASK = models.tbl(models.Task)

# 诊断类型（constants.DIAGNOSTIC_TYPES 已登记；拆解设计 §15）。
DIAG_DRAFT_CONFIRMED = "draft.confirmed"
DIAG_DRAFT_REJECTED = "draft.rejected"
DIAG_LANDING_STARTED = "landing.started"


class StaleTransition(Exception):
    """confirm/reject 条件转移竞败信号（硬关口重写，Fable）：`UPDATE … WHERE status=
    'awaiting_confirm'` rowcount=0 = 并发对手已推进状态。路由捕获后重取最新态回 409 STALE_CONFIRM。

    为什么必须条件 UPDATE 而非「读态检查 + _transition 无条件写」：pysqlite 方言下 SELECT 在首个
    DML 前跑在自动提交（无快照），两个并发 confirm 都能以过期读通过 Python 侧检查——无条件写会
    双双成功 → 双批双落地。WHERE 条件把状态机边（awaiting→landing/rejected）的执法原子化到写锁
    获取时刻，竞败方 rowcount=0。"""

ADJUSTMENT_OPS: frozenset[str] = frozenset(
    {"add_node", "remove_node", "add_edge", "remove_edge", "edit_node", "edit_merge_plan"}
)
EDIT_NODE_FIELDS: frozenset[str] = frozenset(
    {"title", "task_plan", "suggested_owner", "project", "writes_code", "command"}
)
# 每个 op 的允许键集（op 形状 additionalProperties=false）。
_OP_KEYS: dict[str, frozenset[str]] = {
    "add_node": frozenset({"op", "node"}),
    "remove_node": frozenset({"op", "temp_id"}),
    "add_edge": frozenset({"op", "from", "to"}),
    "remove_edge": frozenset({"op", "from", "to"}),
    "edit_node": frozenset({"op", "temp_id", "changes"}),
    "edit_merge_plan": frozenset({"op", "merge_plan"}),
}


def _adj_error(index: int, reason: str, **extra: Any) -> ApiError:
    return ApiError(
        422,
        rest.ErrorCode.VALIDATION_FAILED,
        f"调整项 [{index}] 无效：{reason}",
        rule="B§12.4",
        details={"index": index, "reason": reason, **extra},
    )


def _find_node(nodes: list[Any], temp_id: str) -> dict[str, Any] | None:
    for n in nodes:
        if isinstance(n, dict) and n.get("temp_id") == temp_id:
            return n
    return None


def apply_adjustments(body: dict[str, Any], adjustments: list[Any]) -> dict[str, Any]:
    """确定性应用调整清单 → 调整后提案 body（op 形状见模块 docstring；违形 → 422）。

    纯函数：不读 DB、不依赖时钟；同 (body, adjustments) 恒得同结果（落地恢复语义所系）。
    结果图的语义合法性（环/悬空端点/契约完整）由调用方过 validate_proposal 全量重验。
    零调整 = 恒等（**逐键不动**——直落路径以 proposal_hash 作 landed_hash，恒等是其前提；
    edges 键缺席的 body 不得被补出空数组改变指纹）。
    """
    result = copy.deepcopy(body)
    if not adjustments:
        return result
    had_edges = isinstance(result.get("edges"), list)
    nodes = result.get("nodes")
    if not isinstance(nodes, list):
        nodes = []
        result["nodes"] = nodes
    edges = result.get("edges")
    if not isinstance(edges, list):
        edges = []
        result["edges"] = edges

    for idx, adj in enumerate(adjustments):
        if not isinstance(adj, dict):
            raise _adj_error(idx, "op 必须为对象")
        op = adj.get("op")
        if op not in ADJUSTMENT_OPS:
            raise _adj_error(idx, f"未知 op '{op}'", op=str(op))
        extra_keys = sorted(set(adj) - _OP_KEYS[op])
        if extra_keys:
            raise _adj_error(idx, f"op '{op}' 不接受额外键 {extra_keys}", op=op)

        if op == "add_node":
            node = adj.get("node")
            if not isinstance(node, dict) or not isinstance(node.get("temp_id"), str):
                raise _adj_error(idx, "add_node 须携完整节点对象（含 temp_id）", op=op)
            if _find_node(nodes, node["temp_id"]) is not None:
                raise _adj_error(
                    idx, f"temp_id '{node['temp_id']}' 已存在", op=op, temp_id=node["temp_id"]
                )
            nodes.append(copy.deepcopy(node))
        elif op == "remove_node":
            tid = adj.get("temp_id")
            if not isinstance(tid, str) or _find_node(nodes, tid) is None:
                raise _adj_error(idx, f"remove_node 目标 '{tid}' 不存在", op=op)
            result["nodes"] = nodes = [
                n for n in nodes if not (isinstance(n, dict) and n.get("temp_id") == tid)
            ]
            # 连带删除关联边（画布删节点同语义——确定性，客户端无需补 remove_edge）。
            result["edges"] = edges = [
                e for e in edges
                if not (isinstance(e, dict) and (e.get("from") == tid or e.get("to") == tid))
            ]
        elif op == "add_edge":
            frm, to = adj.get("from"), adj.get("to")
            if not isinstance(frm, str) or not isinstance(to, str):
                raise _adj_error(idx, "add_edge 的 from/to 须为字符串", op=op)
            if any(
                isinstance(e, dict) and e.get("from") == frm and e.get("to") == to
                for e in edges
            ):
                continue  # 幂等：同 (from,to) 已存在 → 忽略
            edges.append({"from": frm, "to": to})
        elif op == "remove_edge":
            frm, to = adj.get("from"), adj.get("to")
            matched = [
                e for e in edges
                if isinstance(e, dict) and e.get("from") == frm and e.get("to") == to
            ]
            if not matched:
                raise _adj_error(idx, f"remove_edge 目标 ({frm} → {to}) 不存在", op=op)
            result["edges"] = edges = [e for e in edges if e not in matched]
        elif op == "edit_node":
            tid = adj.get("temp_id")
            target = _find_node(nodes, tid) if isinstance(tid, str) else None
            if target is None:
                raise _adj_error(idx, f"edit_node 目标 '{tid}' 不存在", op=op)
            changes = adj.get("changes")
            if not isinstance(changes, dict) or not changes:
                raise _adj_error(idx, "edit_node 须携非空 changes 对象", op=op)
            bad = sorted(set(changes) - EDIT_NODE_FIELDS)
            if bad:
                raise _adj_error(idx, f"edit_node 不可改字段 {bad}", op=op, fields=bad)
            for key, value in changes.items():
                target[key] = copy.deepcopy(value)
        else:  # edit_merge_plan
            if "merge_plan" not in adj:
                raise _adj_error(idx, "edit_merge_plan 须携 merge_plan 键", op=op)
            mp = adj["merge_plan"]
            if mp is not None and not isinstance(mp, str):
                raise _adj_error(idx, "merge_plan 须为字符串或 null", op=op)
            result["merge_plan"] = mp
    # 原 body 无 edges 键且调整后仍无边 → 还原缺席（缺席 ≢ 空数组：指纹不因过程性补键漂移）。
    if not had_edges and result.get("edges") == []:
        del result["edges"]
    return result


# ---------------------------------------------------------------- CAS 最新态（S2 409 载荷）


def stale_latest(
    conn: Connection, proposal: dict[str, Any], canvas: dict[str, Any]
) -> dict[str, Any]:
    """409 STALE_CONFIRM 的 latest 载荷（B §5 ①/02 §1.3a）：客户端刷新草稿重审的数据源。"""
    return {
        "proposal": proposal_public(proposal),
        "baseline_version": canvas["baseline_version"],
        "baseline_hash": canvas["baseline_hash"],
    }


# ---------------------------------------------------------------- confirm 落账（事务内副作用段）


def confirm_apply(
    tx: Any,
    *,
    proposal: dict[str, Any],
    adjustments: list[Any],
    landed_hash: str,
    confirmed_by: str,
) -> tuple[Any, dict[str, Any]]:
    """confirm 事务的写入段（调用方已完成 CAS/调整/重验，本段纯落账，不再抛可失败校验）：

    落账 adjustments/landed_hash + awaiting_confirm→landing = **单条条件 UPDATE 原子完成**
    （WHERE status='awaiting_confirm'；rowcount≠1 → StaleTransition——防双确认双批，见类注）→
    create_batch(kind=decomp, content_hash=landed_hash, source_ref=proposal_id) →
    draft.confirmed + proposal.updated + landing.started 事件与诊断。返回 (batch_row, 刷新后
    proposal_row)。**不建任何画布节点**——落地属异步执行器（landing.py），202 后由
    landing.completed 收尾（B §5）。
    """
    res = tx.conn.execute(
        update(_PROPOSAL)
        .where(
            _PROPOSAL.c.id == proposal["id"],
            _PROPOSAL.c.status == ProposalStatus.AWAITING_CONFIRM.value,
        )
        .values(
            adjustments=adjustments,
            landed_hash=landed_hash,
            status=ProposalStatus.LANDING.value,
            updated_at=service.now_iso(),
        )
    )
    if res.rowcount != 1:
        raise StaleTransition(proposal["id"])
    refreshed = proposal_domain.fetch_proposal(tx.conn, proposal["id"])
    assert refreshed is not None

    batch = service.create_batch(
        tx.conn,
        workspace_id=refreshed["workspace_id"],
        channel_id=refreshed["channel_id"],
        kind=LandingBatchKind.DECOMP,
        content_hash=landed_hash,
        source_ref=refreshed["id"],
        confirmed_by=confirmed_by,
    )

    tx.emit(EventType.DRAFT_CONFIRMED, refreshed["channel_id"], {"proposal_id": refreshed["id"]})
    tx.emit(
        EventType.PROPOSAL_UPDATED, refreshed["channel_id"],
        {"proposal": proposal_public(refreshed)},
    )
    tx.emit(
        EventType.LANDING_STARTED, refreshed["channel_id"],
        {"batch": batch.model_dump(mode="json")},
    )
    proposal_domain.write_diagnostic(
        tx, DIAG_DRAFT_CONFIRMED,
        workspace_id=refreshed["workspace_id"], channel_id=refreshed["channel_id"],
        task_id=refreshed["source_task_id"],
        payload={
            "proposal_id": refreshed["id"], "revision": refreshed["revision"],
            "proposal_hash": refreshed["proposal_hash"], "landed_hash": landed_hash,
            "adjustments_count": len(adjustments), "confirmed_by": confirmed_by,
        },
    )
    proposal_domain.write_diagnostic(
        tx, DIAG_LANDING_STARTED,
        workspace_id=refreshed["workspace_id"], channel_id=refreshed["channel_id"],
        task_id=refreshed["source_task_id"],
        payload={"batch_id": batch.id, "proposal_id": refreshed["id"], "landed_hash": landed_hash},
    )
    return batch, refreshed


# ---------------------------------------------------------------- reject（§8.2）


def reject_proposal(
    tx: Any, *, proposal: dict[str, Any], reason: str | None
) -> dict[str, Any]:
    """拒绝草稿（仅 awaiting_confirm，前置由 route 把守）：status→rejected + 理由进 source 线程
    （系统消息——Orchestrator 可读的纠正信号，无 reason 也发一条拒绝留痕；**不 @Orchestrator**：
    拒绝是被动纠正记录，注入下次拆解上下文的线程摘要即可，主动唤醒会诱发未经请求的重提）+
    draft.rejected / proposal.updated 事件 + 诊断。转移 = 条件 UPDATE（同 confirm_apply，防与
    并发 confirm/reject 竞态双写；rowcount≠1 → StaleTransition）。"""
    res = tx.conn.execute(
        update(_PROPOSAL)
        .where(
            _PROPOSAL.c.id == proposal["id"],
            _PROPOSAL.c.status == ProposalStatus.AWAITING_CONFIRM.value,
        )
        .values(status=ProposalStatus.REJECTED.value, updated_at=service.now_iso())
    )
    if res.rowcount != 1:
        raise StaleTransition(proposal["id"])
    rejected = proposal_domain.fetch_proposal(tx.conn, proposal["id"])
    assert rejected is not None

    source_task = tx.conn.execute(
        select(_TASK).where(_TASK.c.id == rejected["source_task_id"])
    ).mappings().first()
    thread_root = source_task["root_message_id"] if source_task is not None else None
    is_delta = rejected["kind"] == ProposalKind.DELTA.value
    label = "增量提案" if is_delta else "拆解提案"
    body = f"{label}（rev.{rejected['revision']}）已被拒绝。"
    if reason is not None and reason.strip():
        body += f"\n拒绝理由：{reason.strip()}"
    messages_service.post_system_message(
        tx,
        workspace_id=rejected["workspace_id"],
        channel_id=rejected["channel_id"],
        body=body,
        thread_root_id=thread_root,
    )

    # 形态感知（J10）：delta → DELTA_REJECTED（载荷 ProposalData）+ delta.rejected 诊断；
    # full → DRAFT_REJECTED（ProposalRefData）+ draft.rejected 诊断。
    if is_delta:
        from coagentia_server.orchestration import delta as delta_domain

        tx.emit(
            EventType.DELTA_REJECTED, rejected["channel_id"],
            {"proposal": proposal_public(rejected)},
        )
        diag_type = delta_domain.DIAG_DELTA_REJECTED
    else:
        tx.emit(EventType.DRAFT_REJECTED, rejected["channel_id"], {"proposal_id": rejected["id"]})
        diag_type = DIAG_DRAFT_REJECTED
    tx.emit(
        EventType.PROPOSAL_UPDATED, rejected["channel_id"],
        {"proposal": proposal_public(rejected)},
    )
    proposal_domain.write_diagnostic(
        tx, diag_type,
        workspace_id=rejected["workspace_id"], channel_id=rejected["channel_id"],
        task_id=rejected["source_task_id"],
        payload={
            "proposal_id": rejected["id"], "revision": rejected["revision"],
            "reason": reason,
        },
    )
    return rejected


__all__ = [
    "ADJUSTMENT_OPS",
    "StaleTransition",
    "EDIT_NODE_FIELDS",
    "apply_adjustments",
    "confirm_apply",
    "reject_proposal",
    "stale_latest",
]
