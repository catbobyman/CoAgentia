"""O8 汇总执行域（M8b L8，汇总设计 §3/§4/§6）：有界状态摘要 + 协调循环护栏。

判定归 server：轮计数、stall 指纹、replan 预算、放行策略全部 server 权威落库（`summary_runs`
表，一切计数推进走**条件 UPDATE CAS**——M6 三度印证 + CR-M8-1 合流）。Orchestrator 只感知注入的
摘要系统消息与反馈——**零新帧、零新事件类型**（摘要 = 线程系统消息 persist_message 既有路径）。

单点纪律（M8-HANDOFF §6 #7）：摘要拼接/截断规则活在 `collect_summary_inputs`；轮/stall/replan
判定活在本模块 CAS 函数；前端只消费不复算。护栏可见（D4 延伸）：摘要/阻断/恢复全走系统消息进
账本，人机同源。
"""

from __future__ import annotations

from typing import Any

from coagentia_contracts.enums import (
    CanvasNodeKind,
    ContractKind,
    SystemNodeStatus,
    TaskStatus,
)
from coagentia_contracts.kernel.fingerprint import fingerprint
from sqlalchemy import case, insert, literal, select, update
from sqlalchemy.engine import Connection

from coagentia_server.canvas import service as canvas_service
from coagentia_server.contracts import service as contracts_service
from coagentia_server.db import models
from coagentia_server.ledger import service
from coagentia_server.messages import service as messages_service

# 护栏常量（汇总设计 §6.3 / 裁决 #3，PRD O8 数字原样；先常量单点，频道级可配后置）。
MAX_ROUNDS = 8
MAX_STALL = 3
REPLAN_BUDGET = 1

# 摘要有界规则（§4.1）。
_MAX_NODES = 12  # V6 拆解节点上限天然有界
_MAX_DELIVERABLES = 5
_TRUNC = 160  # evidence 结论 / open_risks 单条截断
_MAX_BYTES = 8192  # 整体 ≤ 8KB

_SR = models.tbl(models.SummaryRun)
_NODE = models.tbl(models.CanvasNode)
_EDGE = models.tbl(models.CanvasEdge)
_TASK = models.tbl(models.Task)
_CANVAS = models.tbl(models.Canvas)
_MEMBER = models.tbl(models.Member)
_DIAG = models.tbl(models.DiagnosticEvent)


# ---------------------------------------------------------------- 前驱与摘要输入（§4）


def _predecessor_node_ids(conn: Connection, summary_node_id: str) -> list[str]:
    """汇总节点直接前驱节点 id（升序，确定性）。"""
    rows = conn.execute(
        select(_EDGE.c.from_node_id)
        .where(_EDGE.c.to_node_id == summary_node_id)
        .order_by(_EDGE.c.from_node_id)
    ).scalars()
    return list(rows)


def _truncate(text: str | None) -> str:
    s = (text or "").strip().replace("\n", " ")
    return s if len(s) <= _TRUNC else s[: _TRUNC - 1] + "…"


def collect_summary_inputs(
    conn: Connection, canvas_id: str, summary_node_id: str
) -> dict[str, Any]:
    """有界状态摘要输入（§4.2，server 侧拼接，不新增 REST 端点）：遍历汇总节点直接前驱 → 逐 task
    读 active TaskHandoff（≤12 节点无 N+1 之虞）→ 结构化。**未覆盖清单** = 前驱中到达终态但非
    Done 的节点（partial 放行代价，报告须标注——诚实性不押模型自觉，账本兜底）。"""
    pred_ids = _predecessor_node_ids(conn, summary_node_id)[:_MAX_NODES]
    nodes_out: list[dict[str, Any]] = []
    for nid in pred_ids:
        node = (
            conn.execute(select(_NODE).where(_NODE.c.id == nid)).mappings().first()
        )
        if node is None:
            continue
        node = dict(node)
        entry: dict[str, Any] = {
            "node_id": nid,
            "kind": node["kind"],
            "number": None,
            "title": None,
            "owner_name": None,
            "status": None,
            "done": False,
            "handoff_revision": None,
            "deliverables": [],
            "evidence": [],
            "open_risks": [],
        }
        if node["kind"] == CanvasNodeKind.AGENT and node["task_id"] is not None:
            task = (
                conn.execute(select(_TASK).where(_TASK.c.id == node["task_id"]))
                .mappings()
                .first()
            )
            if task is not None:
                task = dict(task)
                entry["number"] = task["number"]
                entry["title"] = task["title"]
                entry["status"] = task["status"]
                entry["done"] = task["status"] == TaskStatus.DONE
                if task["owner_member_id"] is not None:
                    owner = conn.execute(
                        select(_MEMBER.c.name).where(_MEMBER.c.id == task["owner_member_id"])
                    ).scalar()
                    entry["owner_name"] = owner
                handoff = contracts_service.active_contract(
                    conn, node["task_id"], ContractKind.TASK_HANDOFF
                )
                if handoff is not None:
                    entry["handoff_revision"] = handoff["revision"]
                    body = handoff["body"] or {}
                    entry["deliverables"] = [
                        d.get("path", "") for d in (body.get("deliverables") or [])
                    ]
                    entry["evidence"] = [
                        {"type": e.get("type"), "conclusion": e.get("conclusion")}
                        for e in (body.get("evidence") or [])
                    ]
                    entry["open_risks"] = list(body.get("open_risks") or [])
        else:  # system 节点
            entry["title"] = f"系统节点·{node['system_action']}"
            entry["status"] = node["system_status"]
            entry["done"] = node["system_status"] == SystemNodeStatus.SUCCESS
        nodes_out.append(entry)

    covered = [n for n in nodes_out if n["done"]]
    uncovered = [n for n in nodes_out if not n["done"]]
    return {
        "summary_node_id": summary_node_id,
        "canvas_id": canvas_id,
        "nodes": nodes_out,
        "covered_count": len(covered),
        "total_count": len(nodes_out),
        "uncovered": uncovered,
    }


def summary_fingerprint(conn: Connection, inputs: dict[str, Any]) -> str:
    """stall 指纹（§6.2，复用 fingerprint 内核，纪律 8 第二组）：前驱 [id+状态+active handoff
    revision] + 未覆盖集合 + 画布基线指纹——server 计算。前驱/未覆盖已排序确定。"""
    baseline = conn.execute(
        select(_CANVAS.c.baseline_hash).where(_CANVAS.c.id == inputs["canvas_id"])
    ).scalar()
    payload = {
        "nodes": [
            {"id": n["node_id"], "status": n["status"], "rev": n["handoff_revision"]}
            for n in sorted(inputs["nodes"], key=lambda x: x["node_id"])
        ],
        "uncovered": sorted(n["node_id"] for n in inputs["uncovered"]),
        "baseline": baseline,
    }
    return fingerprint(payload)


def render_summary_message(inputs: dict[str, Any], *, round_count: int) -> str:
    """结构化 markdown 摘要（§4.1），进线程系统消息（人类可见、可搜索）。整体 ≤ 8KB，超限截断。"""
    total = inputs["total_count"]
    covered = inputs["covered_count"]
    lines: list[str] = [f"**汇总输入摘要**（第 {round_count} 轮 / 上限 {MAX_ROUNDS}）"]
    cover_line = f"覆盖：{covered}/{total} 个上游节点已 Done"
    if inputs["uncovered"]:
        parts = [
            f"#{n['number'] or '?'}「{n['title'] or n['node_id']}」（{n['status']}）"
            for n in inputs["uncovered"]
        ]
        cover_line += "；未覆盖：" + "、".join(parts)
    lines.append(cover_line)
    for n in inputs["nodes"]:
        head = f"- #{n['number'] or '?'} {n['title'] or n['node_id']}"
        if n["owner_name"]:
            head += f" · {n['owner_name']}"
        head += f" · {n['status']}"
        lines.append(head)
        if n["deliverables"]:
            shown = n["deliverables"][:_MAX_DELIVERABLES]
            extra = len(n["deliverables"]) - len(shown)
            dl = "，".join(shown) + (f" +{extra} more" if extra > 0 else "")
            lines.append(f"  deliverables: {dl}")
        for e in n["evidence"]:
            lines.append(f"  evidence[{e['type']}]: {_truncate(e['conclusion'])}")
        for r in n["open_risks"]:
            lines.append(f"  risk: {_truncate(r)}")
    lines.append("提示：总报告须逐条照抄上方「未覆盖」清单（W9 诚实性）。")
    body = "\n".join(lines)
    if len(body.encode("utf-8")) > _MAX_BYTES:
        body = body.encode("utf-8")[: _MAX_BYTES - 20].decode("utf-8", "ignore")
        body += "\n…（truncated）"
    return body


# ---------------------------------------------------------------- summary_runs CAS（§6.3/§6.4）


def get_run(conn: Connection, task_id: str) -> dict[str, Any] | None:
    row = conn.execute(select(_SR).where(_SR.c.task_id == task_id)).mappings().first()
    return dict(row) if row is not None else None


def ensure_run(
    tx: Any, *, task_id: str, canvas_id: str, workspace_id: str
) -> dict[str, Any]:
    """lazy 建行（§6.4：汇总节点 gating 首次解除，非落地即建）。已存在则原样返回（并发下第二个
    INSERT 触发 PK 冲突 → 重读，条件 UPDATE 才是计数推进的串行化点）。"""
    existing = get_run(tx.conn, task_id)
    if existing is not None:
        return existing
    ts = service.now_iso()
    try:
        with tx.conn.begin_nested():
            tx.conn.execute(
                insert(_SR).values(
                    task_id=task_id,
                    canvas_id=canvas_id,
                    workspace_id=workspace_id,
                    round_count=0,
                    stall_count=0,
                    replan_used=0,
                    last_fingerprint=None,
                    blocked_at=None,
                    created_at=ts,
                    updated_at=ts,
                )
            )
    except Exception:  # noqa: BLE001 — PK 冲突（并发建行）→ 重读既有行
        pass
    row = get_run(tx.conn, task_id)
    assert row is not None
    return row


def advance_progress(tx: Any, *, task_id: str, new_fp: str) -> dict[str, Any]:
    """结构进展轮（§6.1，scan 入口，条件 UPDATE CAS）：仅当**状态较上次已发生变化**（fp 变化或首次
    激活）才计一轮并推进 last_fingerprint、stall 归零——对同一状态的重复 bus 扫描**幂等**（fp 未变即
    不重计，防返工锚点 4「不在多入口重复计数」）。返回 `{counted, just_blocked, ...row}`；counted 为
    True 时 caller 发/追发摘要系统消息（首次或有变化才发，§4.2 防刷屏）。触顶 round≥8 阻断。"""
    row = get_run(tx.conn, task_id)
    assert row is not None
    first = row["round_count"] == 0 and row["last_fingerprint"] is None
    if not first and row["last_fingerprint"] == new_fp:
        row["counted"] = False
        row["just_blocked"] = False
        return row
    tx.conn.execute(
        update(_SR)
        .where(_SR.c.task_id == task_id)
        .values(
            round_count=_SR.c.round_count + 1,
            stall_count=literal(0),
            last_fingerprint=new_fp,
            updated_at=service.now_iso(),
        )
    )
    row = get_run(tx.conn, task_id)
    assert row is not None
    just_blocked = False
    if row["blocked_at"] is None and row["round_count"] >= MAX_ROUNDS:
        just_blocked = _set_blocked(tx, task_id)
        row = get_run(tx.conn, task_id)
        assert row is not None
    row["counted"] = True
    row["just_blocked"] = just_blocked
    return row


def note_wakeup(tx: Any, *, task_id: str, new_fp: str) -> dict[str, Any]:
    """无进展唤醒轮（§6.1/§6.3，delivery 入口，条件 UPDATE CAS）：Orchestrator 被消息唤醒但状态未
    变（空转，F1）——round_count += 1；fp 未变 → stall_count += 1，fp 变化 → stall 归零并推进
    last_fingerprint。CASE 表达式把「比对 + 推进」压进单条原子 UPDATE（防并发唤醒竞态双计）。返回
    推进后行 + `just_blocked`（本轮首次触顶 round≥8 或 stall≥3，caller 发 @人类 + 诊断）。"""
    # stall 的条件推进须与 round 递增同一原子 UPDATE（比对旧 last_fingerprint）：CASE 单语句。
    tx.conn.execute(
        update(_SR)
        .where(_SR.c.task_id == task_id)
        .values(
            round_count=_SR.c.round_count + 1,
            stall_count=case(
                (_SR.c.last_fingerprint == new_fp, _SR.c.stall_count + 1),
                else_=literal(0),
            ),
            last_fingerprint=new_fp,
            updated_at=service.now_iso(),
        )
    )
    row = get_run(tx.conn, task_id)
    assert row is not None
    just_blocked = False
    if row["blocked_at"] is None and (
        row["round_count"] >= MAX_ROUNDS or row["stall_count"] >= MAX_STALL
    ):
        just_blocked = _set_blocked(tx, task_id)
        row = get_run(tx.conn, task_id)
        assert row is not None
    row["just_blocked"] = just_blocked
    return row


def add_repeat_stall(tx: Any, *, task_id: str) -> dict[str, Any] | None:
    """重复决策加倍（§6.3）：Orchestrator 本轮 delta proposal_hash 与上次相同 → stall 额外 +1。
    可能就此触顶（返回行含 just_blocked）。无行（未进入汇总期）→ None 无操作。"""
    if get_run(tx.conn, task_id) is None:
        return None
    tx.conn.execute(
        update(_SR)
        .where(_SR.c.task_id == task_id)
        .values(stall_count=_SR.c.stall_count + 1, updated_at=service.now_iso())
    )
    row = get_run(tx.conn, task_id)
    assert row is not None
    just_blocked = False
    if row["blocked_at"] is None and row["stall_count"] >= MAX_STALL:
        just_blocked = _set_blocked(tx, task_id)
        row = get_run(tx.conn, task_id)
        assert row is not None
    row["just_blocked"] = just_blocked
    return row


def post_coordination_block(
    tx: Any, *, channel_id: str, ctx: dict[str, Any], run: dict[str, Any]
) -> None:
    """协调触顶阻断的护栏可见留痕（§6.3，护栏可见）：@人类系统消息（携三计数事实 + 恢复指引）+
    DiagnosticEvent(summary.coordination_blocked)。hub scan / delta 触顶共用（单点防漂移）。"""
    reason = (
        f"轮数触顶（{run['round_count']}/{MAX_ROUNDS}）"
        if run["round_count"] >= MAX_ROUNDS
        else f"空转触顶（stall {run['stall_count']}/{MAX_STALL}）"
    )
    humans = messages_service.channel_human_members(tx.conn, channel_id)
    messages_service.post_system_message(
        tx,
        workspace_id=ctx["workspace_id"],
        channel_id=channel_id,
        body=(
            f"⚠️ 汇总协调已阻断：{reason}。已停止自动唤醒——请在本线程发言或对汇总节点 force-start "
            f"以恢复（恢复归零轮/stall 计数，replan 预算不重置）。"
        ),
        thread_root_id=ctx["thread_root_id"],
        mention_member_ids=[h["id"] for h in humans],
    )
    tx.conn.execute(
        insert(_DIAG).values(
            workspace_id=ctx["workspace_id"],
            agent_member_id=ctx["owner_id"],
            channel_id=channel_id,
            task_id=ctx["task_id"],
            type="summary.coordination_blocked",
            payload={
                "task_id": ctx["task_id"],
                "round_count": run["round_count"],
                "stall_count": run["stall_count"],
                "replan_used": run["replan_used"],
                "reason": reason,
            },
            created_at=service.now_iso(),
        )
    )


def _set_blocked(tx: Any, task_id: str) -> bool:
    """置 blocked_at（CAS：仅 blocked_at IS NULL 才写，防并发双阻断）。返回本调用是否首次置位。"""
    result = tx.conn.execute(
        update(_SR)
        .where(_SR.c.task_id == task_id, _SR.c.blocked_at.is_(None))
        .values(blocked_at=service.now_iso(), updated_at=service.now_iso())
    )
    return result.rowcount == 1


def recover(tx: Any, *, task_id: str) -> bool:
    """恢复（§6.3 / 裁决 #8）：人类线程发言 / force-start → round_count/stall_count 归零、blocked_at
    清空、last_fingerprint 清空（**replan_used 不重置**——预算随人类介入不续杯）。返回是否有行。"""
    if get_run(tx.conn, task_id) is None:
        return False
    tx.conn.execute(
        update(_SR)
        .where(_SR.c.task_id == task_id)
        .values(
            round_count=0,
            stall_count=0,
            blocked_at=None,
            last_fingerprint=None,
            updated_at=service.now_iso(),
        )
    )
    return True


def consume_replan(tx: Any, *, task_id: str) -> bool:
    """CAS 消费 replan 预算（§6.3）：replan_used < REPLAN_BUDGET 才原子 +1，返回是否消费成功。超额
    → False（caller 403 rule=O8）。条件 UPDATE 把「预算判定 + 推进」压进单语句——并发两 delta 提案
    只放行一次（防 phase1 读 phase2 写的 TOCTOU 双花）。恢复不重置 replan_used（裁决 #8）。"""
    result = tx.conn.execute(
        update(_SR)
        .where(_SR.c.task_id == task_id, _SR.c.replan_used < REPLAN_BUDGET)
        .values(replan_used=_SR.c.replan_used + 1, updated_at=service.now_iso())
    )
    return result.rowcount == 1


def replan_exhausted(conn: Connection, task_id: str) -> bool:
    """汇总期该任务 replan 预算是否已耗尽（≥ REPLAN_BUDGET）。无行 = 未进入汇总期 = 不受限。"""
    run = get_run(conn, task_id)
    return run is not None and run["replan_used"] >= REPLAN_BUDGET


def active_summary_task(conn: Connection, canvas_id: str) -> str | None:
    """本画布处于**汇总执行期**的汇总任务（summary_runs 行已建 = 汇总节点已解除 gating；任务非
    终态、未阻断）。replan 预算判定消费（§6.3）——无则该画布未进入汇总期，delta 不受 replan 限。"""
    task_ids = conn.execute(
        select(_NODE.c.task_id).where(
            _NODE.c.canvas_id == canvas_id,
            _NODE.c.kind == CanvasNodeKind.AGENT,
            _NODE.c.is_summary,
        )
    ).scalars()
    for task_id in task_ids:
        run = get_run(conn, task_id)
        if run is None or run["blocked_at"] is not None:
            continue
        status = conn.execute(
            select(_TASK.c.status).where(_TASK.c.id == task_id)
        ).scalar()
        if status in (TaskStatus.DONE, TaskStatus.CLOSED):
            continue
        return task_id
    return None


# ---------------------------------------------------------------- 汇总任务解析（gating/唤醒消费）


def summary_task_for_thread(
    conn: Connection, msg: dict[str, Any]
) -> str | None:
    """消息线程根 → 汇总任务 id（若该线程锚定一个 is_summary 节点的任务），否则 None。gating 与轮
    计数消费此解析——非汇总线程零成本短路。"""
    root_id = msg.get("thread_root_id") or msg.get("id")
    if root_id is None:
        return None
    task_id = conn.execute(
        select(_TASK.c.id).where(_TASK.c.root_message_id == root_id)
    ).scalar()
    if task_id is None:
        return None
    is_summary = conn.execute(
        select(_NODE.c.is_summary).where(
            _NODE.c.task_id == task_id, _NODE.c.kind == CanvasNodeKind.AGENT
        )
    ).scalar()
    return task_id if is_summary else None


def node_context_for_task(conn: Connection, task_id: str) -> dict[str, Any] | None:
    """汇总任务 → 其 is_summary 节点上下文 {node_id, canvas_id, workspace_id, owner_id,
    thread_root_id}；非汇总任务 → None。delivery 侧无进展唤醒计数消费（需 node/canvas 算指纹）。"""
    node = (
        conn.execute(
            select(_NODE.c.id, _NODE.c.canvas_id, _NODE.c.is_summary).where(
                _NODE.c.task_id == task_id, _NODE.c.kind == CanvasNodeKind.AGENT
            )
        )
        .mappings()
        .first()
    )
    if node is None or not node["is_summary"]:
        return None
    workspace_id = conn.execute(
        select(_CANVAS.c.workspace_id).where(_CANVAS.c.id == node["canvas_id"])
    ).scalar()
    task = (
        conn.execute(
            select(_TASK.c.owner_member_id, _TASK.c.root_message_id).where(_TASK.c.id == task_id)
        )
        .mappings()
        .first()
    )
    if task is None:
        return None
    return {
        "node_id": node["id"],
        "canvas_id": node["canvas_id"],
        "workspace_id": workspace_id,
        "owner_id": task["owner_member_id"],
        "thread_root_id": task["root_message_id"],
        "task_id": task_id,
    }


def is_summary_blocked(conn: Connection, task_id: str) -> bool:
    """该汇总任务是否处于协调阻断中（blocked_at 非空）——gating 双面消费（§6.3 抑制自动唤醒）。"""
    run = get_run(conn, task_id)
    return run is not None and run["blocked_at"] is not None


def candidate_summary_nodes(conn: Connection, channel_id: str) -> list[dict[str, Any]]:
    """频道画布内可协调的汇总节点：is_summary agent 节点、图 gating 已解除、任务非终态（F8 终态
    失效不参与）。返回 [{node_id, task_id, owner_id, canvas_id, workspace_id, thread_root_id}]。
    阻断态（blocked_at）不在此过滤——scan 内 advance/post 会各自按 blocked_at 抑制（§6.3）。"""
    canvas = canvas_service.fetch_canvas_by_channel(conn, channel_id)
    if canvas is None:
        return []
    nodes = canvas_service.fetch_nodes(conn, canvas["id"])
    summary_nodes = [
        n
        for n in nodes
        if n["is_summary"] and n["kind"] == CanvasNodeKind.AGENT and n["task_id"]
    ]
    if not summary_nodes:
        return []
    blocked = canvas_service.blocked_node_ids(conn, canvas["id"])
    out: list[dict[str, Any]] = []
    for n in summary_nodes:
        if n["id"] in blocked:
            continue  # gating 未解除——上游尚未全部到达终态
        task = (
            conn.execute(
                select(_TASK.c.status, _TASK.c.owner_member_id, _TASK.c.root_message_id).where(
                    _TASK.c.id == n["task_id"]
                )
            )
            .mappings()
            .first()
        )
        if task is None or task["status"] in (TaskStatus.DONE, TaskStatus.CLOSED):
            continue  # F8：汇总任务终态 → summary_run 失效，不参与协调
        out.append(
            {
                "node_id": n["id"],
                "task_id": n["task_id"],
                "owner_id": task["owner_member_id"],
                "canvas_id": canvas["id"],
                "workspace_id": canvas["workspace_id"],
                "thread_root_id": task["root_message_id"],
            }
        )
    return out


__all__ = [
    "MAX_ROUNDS",
    "MAX_STALL",
    "REPLAN_BUDGET",
    "collect_summary_inputs",
    "summary_fingerprint",
    "render_summary_message",
    "get_run",
    "ensure_run",
    "advance_progress",
    "note_wakeup",
    "add_repeat_stall",
    "recover",
    "consume_replan",
    "replan_exhausted",
    "active_summary_task",
    "post_coordination_block",
    "summary_task_for_thread",
    "node_context_for_task",
    "is_summary_blocked",
    "candidate_summary_nodes",
]
