"""增量变更域（J10；拆解设计 §11 / 契约 B §12.4）——delta 校验器 + confirm 落账 + F9 处置。

delta 提案 = 落地后对**当前画布基线**的结构增量（加/删节点、连/断边）。判断归模型（Orchestrator 发
`<control>` delta 提案）、控制归代码（确定性校验、部分接受落账；幂等落地由 landing.py 执行）。

**与 full 提案的差异**：
- 引用系：full 用 temp_id 内部命名；delta 的 remove_* 引用现画布节点 ULID、add_node 引入 temp_id、
  add_edge 端点可为现节点 ULID 或新增 temp_id（结果图 = (现节点 − 删除) ∪ 新增）。
- 基线绑定：body.base = 提案时的 baseline_hash（F9：基线已推进 → 拒绝重出）。
- 调整面：full 的调整 = adjustments[] 六 op；delta 的调整 = removed_ops（部分接受，逐 op 剔除）。

校验内核复用（纪律 8）：新增节点内形校验 = 构造合法 decomposition 信封塞入全部 add_node 节点 →
过 kernel.validate_proposal → 过滤顶层/节点数类错误、保留 nodes[i] 内部错误并重映射 path 到
$.operations[j].node…；结果图无环复用 kernel.graph.detect_cycle。base/结构应用/NODE_ACTIVE 为
delta 私有语义，本模块实现。

循环 import 纪律：本模块不在模块级 import orchestration.proposal（proposal.py 在 classify 里 import
本模块）；需要 proposal_domain 的查询助手/诊断处以函数内局部 import 打断环。
"""

from __future__ import annotations

import re
from typing import Any

from coagentia_contracts import rest
from coagentia_contracts.constants import (
    OPID_DELTA_OP,  # noqa: F401  (landing.py 消费；此处仅登记引用面)
    SCHEMA_DECOMPOSITION_DELTA_V1,
    SCHEMA_DECOMPOSITION_V1,
)
from coagentia_contracts.enums import (
    CanvasNodeKind,
    LandingBatchKind,
    ProposalStatus,
    SystemNodeStatus,
    TaskStatus,
)
from coagentia_contracts.kernel import decomposition as kdec
from coagentia_contracts.kernel.decomposition import (
    Env,
    proposal_fingerprint,
    validate_proposal,
)
from coagentia_contracts.kernel.graph import detect_cycle
from coagentia_contracts.ws import EventType
from sqlalchemy import select, update
from sqlalchemy.engine import Connection

from coagentia_server.canvas import service as canvas_service
from coagentia_server.db import models
from coagentia_server.ledger import service
from coagentia_server.routes.serialize import proposal_public

_PROPOSAL = models.tbl(models.Proposal)
_TASK = models.tbl(models.Task)

# 诊断类型（constants.DIAGNOSTIC_TYPES 已登记；拆解设计 §15）。
DIAG_DELTA_PROPOSED = "delta.proposed"
DIAG_DELTA_ADJUSTED = "delta.adjusted"
DIAG_DELTA_CONFIRMED = "delta.confirmed"
DIAG_DELTA_REJECTED = "delta.rejected"

# delta op 允许键集（additionalProperties=false，同构 V3 纪律）。
_DELTA_OPS: frozenset[str] = frozenset(
    {"add_node", "remove_node", "add_edge", "remove_edge"}
)
_TOP_ALLOWED: frozenset[str] = frozenset({"version", "base", "operations", "reason"})
_OP_KEYS: dict[str, frozenset[str]] = {
    "add_node": frozenset({"op", "node"}),
    "remove_node": frozenset({"op", "node_id"}),
    "add_edge": frozenset({"op", "from", "to"}),
    "remove_edge": frozenset({"op", "from", "to"}),
}

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_NODE_PATH_RE = re.compile(r"^\$\.nodes\[(\d+)\](.*)$")

# 信封占位（定长合法字段，V4 恒过；过滤后不残留顶层错误）。
_ENVELOPE_SUMMARY = "delta 新增节点内形校验占位（不落地）"


def _err(code: str, path: str, message: str, hint: str | None = None) -> dict[str, Any]:
    e: dict[str, Any] = {"code": code, "path": path, "message": message}
    if hint is not None:
        e["hint"] = hint
    return e


def _node_id_hint(current_node_ids: list[str]) -> str:
    if not current_node_ids:
        return "画布当前无节点，无可引用目标"
    listing = "、".join(sorted(current_node_ids))
    return f"现有节点 id：{listing}"


# ---------------------------------------------------------------- landed 内容与指纹


def remaining_operations(operations: list[Any], removed: set[int] | list[int]) -> list[Any]:
    """剔除 removed_ops 后的操作集（op_id 用原始下标，故本函数只用于指纹/摘要，不构造 op_id）。"""
    removed_set = set(removed)
    return [op for i, op in enumerate(operations) if i not in removed_set]


def delta_landed_hash(body: dict[str, Any], removed: set[int] | list[int]) -> str:
    """剔除后的 delta 落地指纹（契约 §12.4 #3：无剔除时天然 == proposal_hash）。"""
    ops = body.get("operations") or []
    remaining_body = {**body, "operations": remaining_operations(list(ops), removed)}
    return proposal_fingerprint(remaining_body)


# ---------------------------------------------------------------- 现画布快照读


def _current_canvas_state(
    conn: Connection, canvas: dict[str, Any]
) -> tuple[dict[str, dict[str, Any]], set[tuple[str, str]]]:
    """现画布 = delta 应用基线：{node_id: node_row} + 边 (from,to) 集。"""
    nodes = canvas_service.fetch_nodes(conn, canvas["id"])
    node_by_id = {n["id"]: n for n in nodes}
    edges = {
        (e["from_node_id"], e["to_node_id"])
        for e in canvas_service.fetch_edges(conn, canvas["id"])
    }
    return node_by_id, edges


def _task_status(conn: Connection, task_id: str) -> str | None:
    return conn.execute(
        select(_TASK.c.status).where(_TASK.c.id == task_id)
    ).scalar()


def _node_is_active(conn: Connection, node: dict[str, Any]) -> bool:
    """F10：remove_node 目标是否"进行中"——agent 任务 in_progress/in_review、system 节点 running。"""
    if node["kind"] == CanvasNodeKind.AGENT.value:
        tid = node.get("task_id")
        if tid is None:
            return False
        status = _task_status(conn, tid)
        return status in (TaskStatus.IN_PROGRESS.value, TaskStatus.IN_REVIEW.value)
    return node.get("system_status") == SystemNodeStatus.RUNNING.value


# ---------------------------------------------------------------- 新增节点内形校验（信封+过滤）


def _validate_added_nodes(
    operations: list[Any], env: Env, source_task_id: str
) -> list[dict[str, Any]]:
    """构造合法 decomposition 信封塞入全部 add_node 节点 → kernel.validate_proposal → 过滤顶层/
    节点数(V6)/merge_plan(V13) 类错误、保留 nodes[i] 内部错误（V3/V4/V7/V10/V11/V12/V14）并把
    path `$.nodes[i]…` 重映射为 `$.operations[j].node…`（j = 该 add_node 的 op 下标）。"""
    add_node_indices: list[int] = []
    added_nodes: list[Any] = []
    for j, op in enumerate(operations):
        if isinstance(op, dict) and op.get("op") == "add_node" and isinstance(op.get("node"), dict):
            add_node_indices.append(j)
            added_nodes.append(op["node"])
    if not added_nodes:
        return []
    envelope = {
        "version": SCHEMA_DECOMPOSITION_V1,
        "source": source_task_id,
        "mode": "decompose",
        "summary": _ENVELOPE_SUMMARY,
        "nodes": added_nodes,
        "edges": [],
    }
    raw = validate_proposal(envelope, env)
    out: list[dict[str, Any]] = []
    for e in raw:
        m = _NODE_PATH_RE.match(e.get("path", ""))
        if m is None:
            continue  # 顶层/NODE_COUNT/merge_plan 类——过滤（结果图规则另判）
        i = int(m.group(1))
        if i >= len(add_node_indices):
            continue
        remapped = dict(e)
        remapped["path"] = f"$.operations[{add_node_indices[i]}].node{m.group(2)}"
        out.append(remapped)
    return out


# ---------------------------------------------------------------- validate_delta（拆解设计 §11）


def validate_delta(
    conn: Connection,
    channel: dict[str, Any],
    canvas: dict[str, Any] | None,
    body: object,
) -> list[dict[str, Any]]:
    """delta 全量校验（错误项形状同 kernel {code,path,message,hint?}，全量收集不遇错即停）。

    1. 自身 schema（version/base/reason/operations + 逐 op 形状 additionalProperties=false）；
    2. base ≠ canvas.baseline_hash → DELTA_BASE_MISMATCH（提交期进修复循环；confirm 期另有 409）；
    3. 结构应用（现画布为基线）：remove_node 目标须存在且非活动（NODE_ACTIVE）、remove_edge 目标须
       存在、add_edge 端点须存在于结果图、add_node.temp_id 不撞现节点/其他新增；
    4. 结果图无环、节点总数 ≤ decomp_node_limit；
    5. 新增节点内形校验（信封+过滤）。
    """
    errors: list[dict[str, Any]] = []
    if not isinstance(body, dict):
        return [_err(kdec.CODE_FIELD_INVALID, "$", "delta 提案必须为 JSON 对象")]
    if canvas is None:
        return [_err(kdec.CODE_FIELD_INVALID, "$", "频道无画布，无法应用增量")]

    # -- 1a. version const
    if body.get("version") != SCHEMA_DECOMPOSITION_DELTA_V1:
        errors.append(_err(
            kdec.CODE_BAD_VERSION, "$.version",
            f"version 必须为 '{SCHEMA_DECOMPOSITION_DELTA_V1}'",
        ))

    # -- 1b. 顶层未知字段
    for key in body:
        if key not in _TOP_ALLOWED:
            errors.append(_err(
                kdec.CODE_UNKNOWN_FIELD, f"$.{key}",
                f"未知字段 '{key}'（delta schema 不接受额外字段）",
            ))

    # -- 1c. base（64hex）
    base = body.get("base")
    base_ok = isinstance(base, str) and _SHA256_RE.match(base) is not None
    if "base" not in body or not isinstance(base, str):
        errors.append(_err(kdec.CODE_FIELD_INVALID, "$.base", "base 为必填字符串（基线指纹）"))
    elif not base_ok:
        errors.append(_err(kdec.CODE_FIELD_INVALID, "$.base", "base 必须为 64 位小写十六进制指纹"))

    # -- 1d. reason（非空 string）
    reason = body.get("reason")
    if "reason" not in body or not isinstance(reason, str) or reason == "":
        errors.append(_err(kdec.CODE_FIELD_INVALID, "$.reason", "reason 为必填非空字符串"))

    # -- 1e. operations（非空 list + 逐 op 形状）
    operations = body.get("operations")
    ops_is_list = isinstance(operations, list)
    if "operations" not in body or not ops_is_list:
        errors.append(_err(kdec.CODE_FIELD_INVALID, "$.operations", "operations 为必填数组"))
    elif len(operations) == 0:
        errors.append(_err(kdec.CODE_FIELD_INVALID, "$.operations", "operations 不得为空"))
    op_list: list[Any] = operations if ops_is_list else []

    node_by_id, current_edges = _current_canvas_state(conn, canvas)
    current_node_ids = set(node_by_id)

    # -- 2. base 对齐当前基线（hint 携当前基线值——§6.3 hint 义务的关键例：Orchestrator 无画布
    # 读面，修复循环靠此一轮自愈；角色话术第 8 条如实承诺该反馈）。
    if base_ok and base != canvas["baseline_hash"]:
        errors.append(_err(
            rest.ErrorCode.DELTA_BASE_MISMATCH.value, "$.base",
            "base 与当前画布基线不符（基线已推进）",
            f"当前画布基线：{canvas['baseline_hash']}（据此重出增量提案）",
        ))

    # -- 3. 结构应用：逐 op 形状校验 + 引用/存在性；同时累积结果图节点/边集。
    added_temp_ids: set[str] = set()
    removed_node_ids: set[str] = set()
    add_edges: list[tuple[str, str]] = []
    remove_edge_targets: list[tuple[str, str]] = []

    for j, op in enumerate(op_list):
        opath = f"$.operations[{j}]"
        if not isinstance(op, dict):
            errors.append(_err(kdec.CODE_FIELD_INVALID, opath, "操作必须为对象"))
            continue
        kind = op.get("op")
        if kind not in _DELTA_OPS:
            errors.append(_err(
                kdec.CODE_FIELD_INVALID, f"{opath}.op",
                f"op 必须为 {sorted(_DELTA_OPS)} 之一",
            ))
            continue
        extra = sorted(set(op) - _OP_KEYS[kind])
        if extra:
            errors.append(_err(
                kdec.CODE_FIELD_INVALID, opath,
                f"op '{kind}' 不接受额外键 {extra}",
            ))

        if kind == "add_node":
            node = op.get("node")
            if not isinstance(node, dict) or not isinstance(node.get("temp_id"), str):
                errors.append(_err(
                    kdec.CODE_FIELD_INVALID, f"{opath}.node",
                    "add_node 须携完整节点对象（含字符串 temp_id）",
                ))
                continue
            tid = node["temp_id"]
            if tid in current_node_ids or tid in added_temp_ids:
                errors.append(_err(
                    kdec.CODE_DUP_ID, f"{opath}.node.temp_id",
                    f"temp_id '{tid}' 与现有节点 id 或其他新增撞",
                ))
            added_temp_ids.add(tid)
        elif kind == "remove_node":
            nid = op.get("node_id")
            if not isinstance(nid, str):
                errors.append(_err(
                    kdec.CODE_FIELD_INVALID, f"{opath}.node_id", "remove_node 须携字符串 node_id"))
                continue
            if nid not in current_node_ids:
                errors.append(_err(
                    kdec.CODE_FIELD_INVALID, f"{opath}.node_id",
                    f"remove_node 目标 '{nid}' 不是现画布节点",
                    _node_id_hint(list(current_node_ids)),
                ))
                continue
            if _node_is_active(conn, node_by_id[nid]):
                errors.append(_err(
                    rest.ErrorCode.NODE_ACTIVE.value, f"{opath}.node_id",
                    f"节点 '{nid}' 的任务进行中/在评审（或系统节点运行中），须先 Close 再删",
                ))
            removed_node_ids.add(nid)
        elif kind == "add_edge":
            frm, to = op.get("from"), op.get("to")
            if not isinstance(frm, str) or not isinstance(to, str):
                errors.append(_err(
                    kdec.CODE_FIELD_INVALID, opath, "add_edge 的 from/to 须为字符串"))
                continue
            add_edges.append((frm, to))
        else:  # remove_edge
            frm, to = op.get("from"), op.get("to")
            if not isinstance(frm, str) or not isinstance(to, str):
                errors.append(_err(
                    kdec.CODE_FIELD_INVALID, opath, "remove_edge 的 from/to 须为字符串"))
                continue
            if (frm, to) not in current_edges:
                errors.append(_err(
                    kdec.CODE_FIELD_INVALID, opath,
                    f"remove_edge 目标 ({frm} → {to}) 不是现画布边",
                ))
            remove_edge_targets.append((frm, to))

    # -- 结果图节点/边集（现节点 − 删除 ∪ 新增；现边 − 删除 ∪ 新增）。
    result_node_ids = (current_node_ids - removed_node_ids) | added_temp_ids
    surviving_edges = {
        (a, b) for (a, b) in current_edges
        if a not in removed_node_ids and b not in removed_node_ids
    }
    for pair in remove_edge_targets:
        surviving_edges.discard(pair)
    result_edges: set[tuple[str, str]] = set(surviving_edges)

    # add_edge 端点存在性（结果图）+ 自环 + 重复（逐 op 报，用其 op 下标定位）。
    add_edge_j = [j for j, op in enumerate(op_list)
                  if isinstance(op, dict) and op.get("op") == "add_edge"]
    for k, (frm, to) in enumerate(add_edges):
        opath = f"$.operations[{add_edge_j[k]}]"
        if frm == to:
            errors.append(_err(kdec.CODE_EDGE_SELF, opath, f"禁止自环（from 与 to 同为 '{frm}'）"))
            continue
        if frm not in result_node_ids:
            errors.append(_err(
                kdec.CODE_EDGE_UNKNOWN_NODE, f"{opath}.from",
                f"边引用了结果图中不存在的节点 '{frm}'",
                _node_id_hint(sorted(result_node_ids)),
            ))
        if to not in result_node_ids:
            errors.append(_err(
                kdec.CODE_EDGE_UNKNOWN_NODE, f"{opath}.to",
                f"边引用了结果图中不存在的节点 '{to}'",
                _node_id_hint(sorted(result_node_ids)),
            ))
        if (frm, to) in result_edges:
            errors.append(_err(
                kdec.CODE_FIELD_INVALID, opath, f"边 ({frm} → {to}) 已存在于结果图"))
        result_edges.add((frm, to))

    # -- 4. 结果图无环 + 节点总数上限。
    cycle = detect_cycle(sorted(result_node_ids), sorted(result_edges))
    if cycle is not None:
        errors.append(_err(
            kdec.CODE_GRAPH_CYCLE, "$.operations",
            f"应用后画布存在环：{' → '.join(cycle)}",
        ))
    node_limit = int(channel.get("decomp_node_limit") or 12)
    if len(result_node_ids) > node_limit:
        errors.append(_err(
            kdec.CODE_NODE_COUNT, "$.operations",
            f"应用后节点总数 {len(result_node_ids)} 超过上限 {node_limit}",
        ))

    # -- 5. 新增节点内形校验（信封+过滤）。
    if op_list:
        from coagentia_server.orchestration import proposal as proposal_domain

        env: Env = {
            "node_limit": node_limit,
            "member_ids": proposal_domain.channel_member_ids(conn, channel["id"]),
            "bound_project_ids": proposal_domain.bound_project_ids(conn, channel["id"]),
        }
        source_task_id = str(channel.get("id"))  # 占位 source 恒过 V4；真 source 由外层锚定
        errors.extend(_validate_added_nodes(op_list, env, source_task_id))

    return errors


# ---------------------------------------------------------------- confirm 落账（§11 部分接受）


def delta_confirm_apply(
    tx: Any,
    *,
    proposal: dict[str, Any],
    removed_ops: list[int],
    landed_hash: str,
    confirmed_by: str,
) -> tuple[Any, dict[str, Any]]:
    """delta confirm 事务写入段（调用方已完成 CAS/base/剔除重验，本段纯落账）：

    条件 UPDATE awaiting→landing SET adjustments=sorted(removed_ops), landed_hash=delta_landed_hash
    （rowcount≠1 → StaleTransition 防双确认双批）→ create_batch(kind=DELTA, hash=landed_hash,
    source_ref=proposal_id) → DELTA_CONFIRMED + PROPOSAL_UPDATED + LANDING_STARTED 事件 +
    delta.confirmed 诊断（removed_ops 非空时另 delta.adjusted 诊断 + source 线程剔除清单系统消息）。
    """
    from coagentia_server.orchestration import draft as draft_domain
    from coagentia_server.orchestration import proposal as proposal_domain

    adjustments = sorted(removed_ops)
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
        raise draft_domain.StaleTransition(proposal["id"])
    refreshed = proposal_domain.fetch_proposal(tx.conn, proposal["id"])
    assert refreshed is not None

    batch = service.create_batch(
        tx.conn,
        workspace_id=refreshed["workspace_id"],
        channel_id=refreshed["channel_id"],
        kind=LandingBatchKind.DELTA,
        content_hash=landed_hash,
        source_ref=refreshed["id"],
        confirmed_by=confirmed_by,
    )

    tx.emit(EventType.DELTA_CONFIRMED, refreshed["channel_id"],
            {"proposal": proposal_public(refreshed)})
    tx.emit(EventType.PROPOSAL_UPDATED, refreshed["channel_id"],
            {"proposal": proposal_public(refreshed)})
    tx.emit(EventType.LANDING_STARTED, refreshed["channel_id"],
            {"batch": batch.model_dump(mode="json")})
    proposal_domain.write_diagnostic(
        tx, DIAG_DELTA_CONFIRMED,
        workspace_id=refreshed["workspace_id"], channel_id=refreshed["channel_id"],
        task_id=refreshed["source_task_id"],
        payload={
            "proposal_id": refreshed["id"], "revision": refreshed["revision"],
            "landed_hash": landed_hash, "removed_ops": adjustments, "confirmed_by": confirmed_by,
        },
    )
    if adjustments:
        proposal_domain.write_diagnostic(
            tx, DIAG_DELTA_ADJUSTED,
            workspace_id=refreshed["workspace_id"], channel_id=refreshed["channel_id"],
            task_id=refreshed["source_task_id"],
            payload={"proposal_id": refreshed["id"], "removed_ops": adjustments},
        )
        _post_removed_ops_message(tx, refreshed, adjustments)
    return batch, refreshed


def _post_removed_ops_message(
    tx: Any, proposal: dict[str, Any], removed_ops: list[int]
) -> None:
    """部分接受的剔除清单进 source 线程（Orchestrator 可读的纠正信号，拆解设计 §11）。"""
    from coagentia_server.messages import service as messages_service

    thread_root = _source_thread_root(tx.conn, proposal["source_task_id"])
    listing = "、".join(f"#{i}" for i in removed_ops)
    body = (
        f"增量提案（rev.{proposal['revision']}）经人类部分接受，已剔除以下操作："
        f"{listing}（剩余操作将落地）。"
    )
    messages_service.post_system_message(
        tx,
        workspace_id=proposal["workspace_id"],
        channel_id=proposal["channel_id"],
        body=body,
        thread_root_id=thread_root,
    )


# ---------------------------------------------------------------- F9 base 过期处置（confirm 期）


def delta_base_mismatch_fail(tx: Any, *, proposal: dict[str, Any]) -> dict[str, Any]:
    """F9：confirm 期 base 过期——同事务把提案 awaiting→failed（条件 UPDATE，竞败→StaleTransition）
    + source 线程系统消息（要求基于新基线重出）+ DELTA_REJECTED + PROPOSAL_UPDATED + delta.rejected
    诊断。返回刷新后 failed 提案行（路由据此构造 409 JSONResponse 使事务提交）。"""
    from coagentia_server.messages import service as messages_service
    from coagentia_server.orchestration import draft as draft_domain
    from coagentia_server.orchestration import proposal as proposal_domain

    res = tx.conn.execute(
        update(_PROPOSAL)
        .where(
            _PROPOSAL.c.id == proposal["id"],
            _PROPOSAL.c.status == ProposalStatus.AWAITING_CONFIRM.value,
        )
        .values(status=ProposalStatus.FAILED.value, updated_at=service.now_iso())
    )
    if res.rowcount != 1:
        raise draft_domain.StaleTransition(proposal["id"])
    failed = proposal_domain.fetch_proposal(tx.conn, proposal["id"])
    assert failed is not None

    thread_root = _source_thread_root(tx.conn, failed["source_task_id"])
    messages_service.post_system_message(
        tx,
        workspace_id=failed["workspace_id"],
        channel_id=failed["channel_id"],
        body=(
            f"增量提案（rev.{failed['revision']}）的基线已过期（画布基线在确认前已推进），"
            "已作废。请基于最新基线重新生成增量提案。"
        ),
        thread_root_id=thread_root,
    )
    tx.emit(EventType.DELTA_REJECTED, failed["channel_id"],
            {"proposal": proposal_public(failed)})
    tx.emit(EventType.PROPOSAL_UPDATED, failed["channel_id"],
            {"proposal": proposal_public(failed)})
    proposal_domain.write_diagnostic(
        tx, DIAG_DELTA_REJECTED,
        workspace_id=failed["workspace_id"], channel_id=failed["channel_id"],
        task_id=failed["source_task_id"],
        payload={"proposal_id": failed["id"], "revision": failed["revision"],
                 "reason": "base_mismatch"},
    )
    return failed


def _source_thread_root(conn: Connection, source_task_id: str) -> str | None:
    return conn.execute(
        select(_TASK.c.root_message_id).where(_TASK.c.id == source_task_id)
    ).scalar()


__all__ = [
    "DIAG_DELTA_ADJUSTED",
    "DIAG_DELTA_CONFIRMED",
    "DIAG_DELTA_PROPOSED",
    "DIAG_DELTA_REJECTED",
    "delta_base_mismatch_fail",
    "delta_confirm_apply",
    "delta_landed_hash",
    "remaining_operations",
    "validate_delta",
]
