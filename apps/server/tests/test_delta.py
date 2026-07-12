"""J10 增量变更（delta）测试（拆解设计 §11 / 契约 B §12.4 / 裁决 #10）。

分层（照 test_landing/test_proposals 体例）：
- 校验器（migrated_engine + 直插画布节点）：逐规则红绿 / hint / path 重映射 / NODE_ACTIVE / 结果图；
- classify delta 入口（_Tx 直驱 classify_submission）：Agent+任务线程+delta 版本建案 / 人类·顶级·
  非任务线程·非 delta 版本忽略 / 已有活动提案忽略+诊断 / 无效 delta 修复循环；
- confirm（HTTP + 单元）：CAS 409 / base 过期 409+failed+线程消息+诊断 / removed_ops 越界·全剔除·
  NODE_ACTIVE 422 / 部分接受落账+delta.adjusted+剔除清单消息 / 无剔除 hash==proposal_hash / 竞败；
- 落地（migrated_engine + bus）：全链增删 / 基线恰 bump 一次 / 已落地消息恰一条 / 重入零重复 /
  崩溃续段 / 执行期 NODE_ACTIVE fail-closed / 直落 delta 经扫描落地；
- O9：四画布结构写端点 Agent 403 + 人类放行分野。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from typing import Any

import pytest
from coagentia_contracts import rest
from coagentia_contracts.constants import SCHEMA_DECOMPOSITION_DELTA_V1
from coagentia_contracts.enums import LandingBatchStatus
from coagentia_contracts.kernel.decomposition import proposal_fingerprint
from coagentia_contracts.ws import EventType
from coagentia_server.canvas import service as canvas_service
from coagentia_server.db import models
from coagentia_server.events import EventBus, PendingEvent
from coagentia_server.ledger.service import new_ulid, now_iso
from coagentia_server.orchestration import delta as delta_domain
from coagentia_server.orchestration import landing as landing_domain
from coagentia_server.orchestration import proposal as pd
from coagentia_server.orchestration.role_templates import (
    ORCHESTRATOR_ROLE_KEY,
    upsert_builtin_role_templates,
)
from daemon_helpers import Env
from fastapi.testclient import TestClient
from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Engine

_PROPOSAL = models.tbl(models.Proposal)
_BATCH = models.tbl(models.LandingBatch)
_LEDGER = models.tbl(models.LedgerEntry)
_TASK = models.tbl(models.Task)
_MSG = models.tbl(models.Message)
_NODE = models.tbl(models.CanvasNode)
_EDGE = models.tbl(models.CanvasEdge)
_CANVAS = models.tbl(models.Canvas)
_DIAG = models.tbl(models.DiagnosticEvent)


# ---------------------------------------------------------------- _Tx + 场景


class _Tx:
    def __init__(self, conn: Any) -> None:
        self.conn = conn
        self.events: list[tuple[Any, str | None, dict[str, Any]]] = []

    def emit(self, etype: Any, channel_id: str | None, data: dict[str, Any]) -> None:
        self.events.append((etype, channel_id, data))


@contextlib.contextmanager
def _tx(engine: Engine) -> Any:
    conn = engine.connect()
    trans = conn.begin()
    tx = _Tx(conn)
    try:
        yield tx
        trans.commit()
    finally:
        conn.close()


def _seed(engine: Engine, *, decomp_mode: str = "draft") -> dict[str, str]:
    """channel + orchestrator + human + 空画布（已同步基线）+ 绑定 Project + source 任务。"""
    upsert_builtin_role_templates(engine)
    env = Env(engine)
    channel = env.add_channel(name="delta")
    orch = env.add_agent("Orch", "idle")
    human = env.owner_id
    canvas_id = new_ulid()
    with engine.begin() as c:
        c.execute(
            update(models.Agent.__table__)
            .where(models.Agent.__table__.c.member_id == orch)
            .values(role_template_key=ORCHESTRATOR_ROLE_KEY)
        )
        c.execute(
            update(models.Channel.__table__)
            .where(models.Channel.__table__.c.id == channel)
            .values(decomp_mode=decomp_mode)
        )
        c.execute(
            insert(_CANVAS).values(
                id=canvas_id, workspace_id=env.ws_id, channel_id=channel,
                baseline_version=0, baseline_hash="0" * 64, updated_at=now_iso(),
            )
        )
    env.join(channel, orch)
    env.join(channel, human)
    root_msg = env.add_message(channel, author=human, body="做个功能")
    task_id = new_ulid()
    project_id = new_ulid()
    with engine.begin() as c:
        c.execute(
            insert(_TASK).values(
                id=task_id, workspace_id=env.ws_id, channel_id=channel, number=1,
                root_message_id=root_msg, title="源任务", status="todo", level="l1",
                created_by_member_id=human, status_changed_at=now_iso(), created_at=now_iso(),
            )
        )
        c.execute(
            update(models.Channel.__table__)
            .where(models.Channel.__table__.c.id == channel)
            .values(next_task_number=2)
        )
        c.execute(
            insert(models.Project.__table__).values(
                id=project_id, workspace_id=env.ws_id, computer_id=env.comp_id,
                name="Repo", repo_path="/tmp/repo", dev_command="pytest", created_at=now_iso(),
            )
        )
        c.execute(
            insert(models.ChannelProject.__table__).values(
                channel_id=channel, project_id=project_id
            )
        )
    ids = {
        "ws": env.ws_id, "channel": channel, "canvas": canvas_id, "orch": orch,
        "human": human, "task": task_id, "root_msg": root_msg, "project": project_id,
    }
    _sync_baseline(engine, ids)
    return ids


def _sync_baseline(engine: Engine, ids: dict[str, str]) -> str:
    """把 canvas.baseline_hash 同步为当前结构快照（delta base 依据）；返回该 hash。"""
    with engine.begin() as c:
        h = canvas_service.compute_hash(c, ids["canvas"])
        c.execute(
            update(_CANVAS).where(_CANVAS.c.id == ids["canvas"])
            .values(baseline_hash=h, baseline_version=_CANVAS.c.baseline_version + 1)
        )
    return h


def _base(engine: Engine, ids: dict[str, str]) -> str:
    with engine.connect() as c:
        return c.execute(
            select(_CANVAS.c.baseline_hash).where(_CANVAS.c.id == ids["canvas"])
        ).scalar_one()


_node_counter = {"n": 1}


def _add_node(
    engine: Engine, ids: dict[str, str], title: str, *,
    kind: str = "agent", status: str = "todo",
    system_action: str | None = None, command: str | None = None,
    system_status: str | None = None,
) -> tuple[str, str | None]:
    """直插一个已落地节点；agent → 建任务+锚点。返回 (node_id, task_id|None)。不同步基线。"""
    node_id = new_ulid()
    task_id: str | None = None
    with engine.begin() as c:
        number = _node_counter["n"] = _node_counter["n"] + 1
        if kind == "agent":
            anchor = new_ulid()
            task_id = new_ulid()
            c.execute(insert(_MSG).values(
                id=anchor, workspace_id=ids["ws"], channel_id=ids["channel"],
                thread_root_id=None, author_member_id=None, kind="system",
                body=title, created_at=now_iso(),
            ))
            c.execute(insert(_TASK).values(
                id=task_id, workspace_id=ids["ws"], channel_id=ids["channel"], number=number,
                root_message_id=anchor, title=title, status=status, level="l2",
                created_by_member_id=ids["human"], status_changed_at=now_iso(),
                created_at=now_iso(),
            ))
            c.execute(update(models.Channel.__table__)
                      .where(models.Channel.__table__.c.id == ids["channel"])
                      .values(next_task_number=number + 1))
        c.execute(insert(_NODE).values(
            id=node_id, canvas_id=ids["canvas"], kind=kind, task_id=task_id,
            is_summary=False, system_action=system_action, command=command,
            system_status=system_status, pos_x=0, pos_y=0, created_at=now_iso(),
        ))
    return node_id, task_id


def _add_edge(engine: Engine, ids: dict[str, str], frm: str, to: str) -> None:
    with engine.begin() as c:
        c.execute(insert(_EDGE).values(
            id=new_ulid(), canvas_id=ids["canvas"], from_node_id=frm, to_node_id=to
        ))


def _channel_dict(engine: Engine, channel_id: str) -> dict[str, Any]:
    with engine.connect() as c:
        return dict(c.execute(
            select(models.Channel.__table__).where(models.Channel.__table__.c.id == channel_id)
        ).mappings().one())


def _canvas_dict(engine: Engine, channel_id: str) -> dict[str, Any]:
    with engine.connect() as c:
        return canvas_service.fetch_canvas_by_channel(c, channel_id)  # type: ignore[return-value]


def _plan(goal: str) -> dict[str, Any]:
    return {
        "goal": goal,
        "acceptance_criteria": [
            {"id": "AC1", "statement": f"{goal}完成", "verify_by": "command",
             "verify_ref": "pytest"},
        ],
    }


def _add_node_op(temp_id: str, title: str) -> dict[str, Any]:
    return {"op": "add_node", "node": {
        "temp_id": temp_id, "title": title, "kind": "agent", "task_plan": _plan(title)}}


def _delta_body(base: str, operations: list[Any], *, reason: str = "增量调整") -> dict[str, Any]:
    return {
        "version": SCHEMA_DECOMPOSITION_DELTA_V1,
        "base": base, "operations": operations, "reason": reason,
    }


def _control_msg(body: dict[str, Any]) -> str:
    return "增量提案：\n\n<control>" + json.dumps(body, ensure_ascii=False) + "</control>"


def _proposal_row(engine: Engine, pid: str) -> dict[str, Any]:
    with engine.connect() as c:
        return models.row_dict(
            c.execute(select(_PROPOSAL).where(_PROPOSAL.c.id == pid)).mappings().one()
        )


def _insert_delta(
    engine: Engine, ids: dict[str, str], body: dict[str, Any], *,
    status: str, adjustments: list[int] | None = None, source_task: str | None = None,
) -> str:
    pid = new_ulid()
    with engine.begin() as c:
        c.execute(insert(_PROPOSAL).values(
            id=pid, workspace_id=ids["ws"], channel_id=ids["channel"],
            source_task_id=source_task or ids["task"], kind="delta", revision=1, status=status,
            body=body, proposal_hash=proposal_fingerprint(body), base_hash=body.get("base"),
            landed_hash=None, adjustments=adjustments or [], repair_count=0,
            proposed_by_member_id=ids["orch"], created_at=now_iso(), updated_at=now_iso(),
        ))
    return pid


def _bus() -> tuple[EventBus, list[PendingEvent]]:
    bus = EventBus()
    events: list[PendingEvent] = []
    bus.subscribe(events.append)
    return bus, events


def _nodes(engine: Engine, ids: dict[str, str]) -> list[dict[str, Any]]:
    with engine.connect() as c:
        return [dict(r) for r in c.execute(
            select(_NODE).where(_NODE.c.canvas_id == ids["canvas"]).order_by(_NODE.c.id)
        ).mappings()]


def _edges(engine: Engine, ids: dict[str, str]) -> set[tuple[str, str]]:
    with engine.connect() as c:
        return {
            (r[0], r[1]) for r in c.execute(
                select(_EDGE.c.from_node_id, _EDGE.c.to_node_id)
                .where(_EDGE.c.canvas_id == ids["canvas"])
            )
        }


def _diag_count(engine: Engine, diag_type: str) -> int:
    with engine.connect() as c:
        return c.execute(
            select(func.count()).select_from(_DIAG).where(_DIAG.c.type == diag_type)
        ).scalar_one()


def _canvas_version(engine: Engine, ids: dict[str, str]) -> int:
    with engine.connect() as c:
        return c.execute(
            select(_CANVAS.c.baseline_version).where(_CANVAS.c.id == ids["canvas"])
        ).scalar_one()


# ================================================================ 校验器


def test_validate_delta_valid_green(migrated_engine: Engine) -> None:
    """有效 delta：加节点 + 连现有节点 + 删边 + 删非活动节点 → 零错误。"""
    ids = _seed(migrated_engine)
    a, _ = _add_node(migrated_engine, ids, "A")
    b, _ = _add_node(migrated_engine, ids, "B")
    c_node, _ = _add_node(migrated_engine, ids, "C")
    _add_edge(migrated_engine, ids, a, b)
    base = _sync_baseline(migrated_engine, ids)
    body = _delta_body(base, [
        _add_node_op("N1", "新节点"),
        {"op": "add_edge", "from": b, "to": "N1"},
        {"op": "remove_edge", "from": a, "to": b},
        {"op": "remove_node", "node_id": c_node},
    ])
    errors = delta_domain.validate_delta(
        migrated_engine.connect(), _channel_dict(migrated_engine, ids["channel"]),
        _canvas_dict(migrated_engine, ids["channel"]), body,
    )
    assert errors == [], errors


def test_validate_delta_schema_red(migrated_engine: Engine) -> None:
    """schema 层红：version / base / reason / operations / op 形状。"""
    ids = _seed(migrated_engine)
    ch = _channel_dict(migrated_engine, ids["channel"])
    cv = _canvas_dict(migrated_engine, ids["channel"])
    conn = migrated_engine.connect()

    def codes(body: Any) -> set[str]:
        return {e["code"] for e in delta_domain.validate_delta(conn, ch, cv, body)}

    assert "BAD_VERSION" in codes({"version": "x", "base": "0" * 64,
                                   "operations": [{"op": "add_edge", "from": "a", "to": "b"}],
                                   "reason": "r"})
    # base 非 64hex + reason 空 + operations 空
    c1 = codes({"version": SCHEMA_DECOMPOSITION_DELTA_V1, "base": "short",
                "operations": [], "reason": ""})
    assert "FIELD_INVALID" in c1
    # 未知顶层字段 + op 未知键 + 未知 op
    c2 = codes(_delta_body(_base(migrated_engine, ids), [
        {"op": "add_edge", "from": "a", "to": "b", "weight": 1},
        {"op": "frobnicate"},
    ]) | {"extra": 1})  # type: ignore[operator]
    assert "UNKNOWN_FIELD" in c2 and "FIELD_INVALID" in c2


def test_validate_delta_base_mismatch(migrated_engine: Engine) -> None:
    ids = _seed(migrated_engine)
    body = _delta_body("f" * 64, [_add_node_op("N1", "n")])
    codes = {e["code"] for e in delta_domain.validate_delta(
        migrated_engine.connect(), _channel_dict(migrated_engine, ids["channel"]),
        _canvas_dict(migrated_engine, ids["channel"]), body,
    )}
    assert "DELTA_BASE_MISMATCH" in codes


def test_validate_delta_remove_missing_and_active(migrated_engine: Engine) -> None:
    """remove_node 目标不存在 → FIELD_INVALID + hint（现有节点 id）；活动节点 → NODE_ACTIVE。"""
    ids = _seed(migrated_engine)
    a, task_a = _add_node(migrated_engine, ids, "A", status="in_progress")
    base = _sync_baseline(migrated_engine, ids)
    # 不存在目标
    errs = delta_domain.validate_delta(
        migrated_engine.connect(), _channel_dict(migrated_engine, ids["channel"]),
        _canvas_dict(migrated_engine, ids["channel"]),
        _delta_body(base, [{"op": "remove_node", "node_id": "01MISSINGNODE00000000000000"}]),
    )
    missing = next(e for e in errs if e["code"] == "FIELD_INVALID")
    assert "hint" in missing and a in missing["hint"]  # 候选清单含现有节点 id
    # 活动节点
    errs2 = delta_domain.validate_delta(
        migrated_engine.connect(), _channel_dict(migrated_engine, ids["channel"]),
        _canvas_dict(migrated_engine, ids["channel"]),
        _delta_body(base, [{"op": "remove_node", "node_id": a}]),
    )
    assert any(e["code"] == "NODE_ACTIVE" for e in errs2)


def test_validate_delta_edge_and_cycle(migrated_engine: Engine) -> None:
    """add_edge 端点悬空 → EDGE_UNKNOWN_NODE+hint；自环 → EDGE_SELF；结果成环 → GRAPH_CYCLE。"""
    ids = _seed(migrated_engine)
    a, _ = _add_node(migrated_engine, ids, "A")
    b, _ = _add_node(migrated_engine, ids, "B")
    _add_edge(migrated_engine, ids, a, b)
    base = _sync_baseline(migrated_engine, ids)
    ch = _channel_dict(migrated_engine, ids["channel"])
    cv = _canvas_dict(migrated_engine, ids["channel"])
    conn = migrated_engine.connect()
    # 悬空端点
    e1 = delta_domain.validate_delta(conn, ch, cv,
        _delta_body(base, [{"op": "add_edge", "from": a, "to": "01NOPE0000000000000000000000"}]))
    unk = next(e for e in e1 if e["code"] == "EDGE_UNKNOWN_NODE")
    assert "hint" in unk
    # 自环
    e2 = delta_domain.validate_delta(conn, ch, cv,
        _delta_body(base, [{"op": "add_edge", "from": a, "to": a}]))
    assert any(e["code"] == "EDGE_SELF" for e in e2)
    # 结果成环（现有 a→b，再加 b→a）
    e3 = delta_domain.validate_delta(conn, ch, cv,
        _delta_body(base, [{"op": "add_edge", "from": b, "to": a}]))
    assert any(e["code"] == "GRAPH_CYCLE" for e in e3)


def test_validate_delta_added_node_shape_path_remap(migrated_engine: Engine) -> None:
    """新增节点内形校验（信封+过滤）：缺 task_plan 的 agent → PLAN_MISSING，path 重映射到
    $.operations[j].node.task_plan（j = 该 add_node 的 op 下标）。"""
    ids = _seed(migrated_engine)
    base = _base(migrated_engine, ids)
    body = _delta_body(base, [
        {"op": "remove_edge", "from": "x", "to": "y"},  # op0（占位，触发其它错误也无妨）
        {"op": "add_node", "node": {"temp_id": "BAD", "title": "无计划", "kind": "agent"}},  # op1
    ])
    errs = delta_domain.validate_delta(
        migrated_engine.connect(), _channel_dict(migrated_engine, ids["channel"]),
        _canvas_dict(migrated_engine, ids["channel"]), body,
    )
    plan_err = next(e for e in errs if e["code"] == "PLAN_MISSING")
    assert plan_err["path"] == "$.operations[1].node.task_plan"


def test_validate_delta_dup_and_count(migrated_engine: Engine) -> None:
    """add_node.temp_id 撞现节点 → DUP_ID；结果节点总数超 decomp_node_limit → NODE_COUNT。"""
    ids = _seed(migrated_engine)
    a, _ = _add_node(migrated_engine, ids, "A")
    base = _sync_baseline(migrated_engine, ids)
    # temp_id == 现节点 ULID
    e1 = delta_domain.validate_delta(
        migrated_engine.connect(), _channel_dict(migrated_engine, ids["channel"]),
        _canvas_dict(migrated_engine, ids["channel"]),
        _delta_body(base, [{"op": "add_node", "node": {
            "temp_id": a, "title": "撞", "kind": "agent", "task_plan": _plan("撞")}}]),
    )
    assert any(e["code"] == "DUP_ID" for e in e1)
    # 上限：把频道 decomp_node_limit 设 1，加 2 个节点（现 1 + 2 = 3 > 1）
    with migrated_engine.begin() as c:
        c.execute(update(models.Channel.__table__)
                  .where(models.Channel.__table__.c.id == ids["channel"])
                  .values(decomp_node_limit=1))
    e2 = delta_domain.validate_delta(
        migrated_engine.connect(), _channel_dict(migrated_engine, ids["channel"]),
        _canvas_dict(migrated_engine, ids["channel"]),
        _delta_body(base, [_add_node_op("N1", "一"), _add_node_op("N2", "二")]),
    )
    assert any(e["code"] == "NODE_COUNT" for e in e2)


# ================================================================ classify delta 入口


def test_classify_delta_entry_creates_awaiting(migrated_engine: Engine) -> None:
    """Agent + 任务线程 + delta 版本 → 建 delta 提案（awaiting_confirm，DELTA_PROPOSED + 卡片）。"""
    ids = _seed(migrated_engine)
    base = _base(migrated_engine, ids)
    body = _delta_body(base, [_add_node_op("N1", "增量节点")])
    channel = _channel_dict(migrated_engine, ids["channel"])
    with _tx(migrated_engine) as tx:
        decision = pd.classify_submission(
            tx, channel=channel, author_member_id=ids["orch"],
            body=_control_msg(body), thread_root_id=ids["root_msg"],
        )
        assert decision is not None
        assert decision.card_kind == "proposal"
        decision.apply(tx)
        etypes = [e for e, _, _ in tx.events]
    assert EventType.DELTA_PROPOSED in etypes
    with migrated_engine.connect() as c:
        row = c.execute(
            select(_PROPOSAL).where(_PROPOSAL.c.source_task_id == ids["task"])
        ).mappings().one()
    assert row["kind"] == "delta" and row["status"] == "awaiting_confirm"
    assert row["base_hash"] == base
    assert _diag_count(migrated_engine, "delta.proposed") == 1


def test_classify_delta_entry_ignored_paths(migrated_engine: Engine) -> None:
    """人类作者 / 顶级消息 / 非任务线程 / 非 delta 版本 → None（普通消息）。"""
    ids = _seed(migrated_engine)
    base = _base(migrated_engine, ids)
    body = _delta_body(base, [_add_node_op("N1", "x")])
    channel = _channel_dict(migrated_engine, ids["channel"])
    with _tx(migrated_engine) as tx:
        # 人类作者
        assert pd.classify_submission(
            tx, channel=channel, author_member_id=ids["human"],
            body=_control_msg(body), thread_root_id=ids["root_msg"]) is None
        # 顶级消息（thread_root_id None）
        assert pd.classify_submission(
            tx, channel=channel, author_member_id=ids["orch"],
            body=_control_msg(body), thread_root_id=None) is None
        # 非任务线程根
        assert pd.classify_submission(
            tx, channel=channel, author_member_id=ids["orch"],
            body=_control_msg(body), thread_root_id=new_ulid()) is None
        # 非 delta 版本（full decomposition control）
        full = {"version": "coagentia.decomposition.v1", "source": ids["task"],
                "mode": "single_task", "summary": "s", "nodes": [_add_node_op("N1", "x")["node"]]}
        assert pd.classify_submission(
            tx, channel=channel, author_member_id=ids["orch"],
            body=_control_msg(full), thread_root_id=ids["root_msg"]) is None


def test_classify_delta_active_proposal_exists(migrated_engine: Engine) -> None:
    """source 已有非终态提案（他方）→ 忽略 + 诊断（reason=active_proposal_exists）。"""
    ids = _seed(migrated_engine)
    base = _base(migrated_engine, ids)
    # 预置一个别的非终态 delta（作者 = human，不会被 classify ① 命中 orch 作者）
    _insert_delta(migrated_engine, ids, _delta_body(base, [_add_node_op("X", "x")]),
                  status="awaiting_confirm")
    with migrated_engine.begin() as c:  # 改其作者为 human 使 orch 作者 ① 不命中
        c.execute(update(_PROPOSAL).where(_PROPOSAL.c.source_task_id == ids["task"])
                  .values(proposed_by_member_id=ids["human"]))
    body = _delta_body(base, [_add_node_op("N1", "新")])
    channel = _channel_dict(migrated_engine, ids["channel"])
    with _tx(migrated_engine) as tx:
        decision = pd.classify_submission(
            tx, channel=channel, author_member_id=ids["orch"],
            body=_control_msg(body), thread_root_id=ids["root_msg"])
        assert decision is not None and decision.card_kind is None
        decision.apply(tx)
    with migrated_engine.connect() as c:
        diag = c.execute(
            select(_DIAG.c.payload).where(_DIAG.c.type == "proposal.duplicate_ignored")
        ).mappings().first()
    assert diag is not None and diag["payload"]["reason"] == "active_proposal_exists"
    # 未新建第二个提案（部分唯一索引未爆）
    with migrated_engine.connect() as c:
        cnt = c.execute(
            select(func.count()).select_from(_PROPOSAL).where(
                _PROPOSAL.c.source_task_id == ids["task"])
        ).scalar_one()
    assert cnt == 1


def test_classify_delta_invalid_enters_repair(migrated_engine: Engine) -> None:
    """无效 delta（base 过期）→ 修复循环：建 repairing 提案 + repair 直投（配额沿用 J8）。"""
    ids = _seed(migrated_engine)
    body = _delta_body("f" * 64, [_add_node_op("N1", "x")])  # base 过期
    channel = _channel_dict(migrated_engine, ids["channel"])
    with _tx(migrated_engine) as tx:
        decision = pd.classify_submission(
            tx, channel=channel, author_member_id=ids["orch"],
            body=_control_msg(body), thread_root_id=ids["root_msg"])
        assert decision is not None
        injects = decision.apply(tx)
    assert len(injects) == 1 and injects[0].kind.value == "repair"
    row = _proposal_row(migrated_engine, _proposal_id_for(migrated_engine, ids["task"]))
    assert row["kind"] == "delta" and row["status"] == "repairing" and row["repair_count"] == 1
    assert _diag_count(migrated_engine, "proposal.validation_failed") == 1


def _proposal_id_for(engine: Engine, source_task: str) -> str:
    with engine.connect() as c:
        return c.execute(
            select(_PROPOSAL.c.id).where(_PROPOSAL.c.source_task_id == source_task)
        ).scalar_one()


# ================================================================ confirm（单元 + HTTP）


def _awaiting_delta(engine: Engine, ids: dict[str, str]) -> tuple[str, str, str, str]:
    """现画布 a→b，delta = 加 N1 + b→N1 + 删边 a→b + 删 c。返回 (pid, a, b, c)。"""
    a, _ = _add_node(engine, ids, "A")
    b, _ = _add_node(engine, ids, "B")
    c_node, _ = _add_node(engine, ids, "C")
    _add_edge(engine, ids, a, b)
    base = _sync_baseline(engine, ids)
    body = _delta_body(base, [
        _add_node_op("N1", "增量实现"),
        {"op": "add_edge", "from": b, "to": "N1"},
        {"op": "remove_edge", "from": a, "to": b},
        {"op": "remove_node", "node_id": c_node},
    ])
    pid = _insert_delta(engine, ids, body, status="awaiting_confirm")
    return pid, a, b, c_node


def test_delta_confirm_full_accept_hash_equals(migrated_engine: Engine) -> None:
    """无剔除确认：landed_hash == proposal_hash（契约 §12.4 #3）+ adjustments=[] 落账 +
    delta.confirmed 诊断 + delta 批建立。"""
    ids = _seed(migrated_engine)
    pid, *_ = _awaiting_delta(migrated_engine, ids)
    proposal = _proposal_row(migrated_engine, pid)
    with _tx(migrated_engine) as tx:
        batch, refreshed = delta_domain.delta_confirm_apply(
            tx, proposal=proposal, removed_ops=[],
            landed_hash=delta_domain.delta_landed_hash(proposal["body"], []),
            confirmed_by=ids["human"],
        )
        etypes = [e for e, _, _ in tx.events]
    assert refreshed["status"] == "landing"
    assert refreshed["landed_hash"] == proposal["proposal_hash"]  # 无剔除
    assert refreshed["adjustments"] == []
    assert batch.kind == "delta" and batch.content_hash == proposal["proposal_hash"]
    assert EventType.DELTA_CONFIRMED in etypes and EventType.LANDING_STARTED in etypes
    assert _diag_count(migrated_engine, "delta.confirmed") == 1
    assert _diag_count(migrated_engine, "delta.adjusted") == 0  # 无剔除不发 adjusted


def test_delta_confirm_partial_accept_lands_adjusted(migrated_engine: Engine) -> None:
    """部分接受（剔除 remove_node op）：delta_landed_hash != proposal_hash + adjustments 落账 +
    delta.adjusted 诊断 + 剔除清单进 source 线程 + 落地保留被剔节点。"""
    ids = _seed(migrated_engine, decomp_mode="draft")
    pid, a, b, c_node = _awaiting_delta(migrated_engine, ids)
    proposal = _proposal_row(migrated_engine, pid)
    removed = [3]  # 剔除 remove_node c
    with _tx(migrated_engine) as tx:
        batch, refreshed = delta_domain.delta_confirm_apply(
            tx, proposal=proposal, removed_ops=removed,
            landed_hash=delta_domain.delta_landed_hash(proposal["body"], removed),
            confirmed_by=ids["human"],
        )
    assert refreshed["adjustments"] == [3]
    assert refreshed["landed_hash"] != proposal["proposal_hash"]
    assert _diag_count(migrated_engine, "delta.adjusted") == 1
    with migrated_engine.connect() as conn:
        msg = conn.execute(
            select(func.count()).select_from(_MSG).where(
                _MSG.c.thread_root_id == ids["root_msg"], _MSG.c.body.like("%已剔除%"))
        ).scalar_one()
    assert msg == 1
    # 落地：c 保留（剔除了删除它的 op），a→b 删除、N1 新增。
    bus, _ = _bus()
    landing_domain.pending_landing_scan(migrated_engine, bus)
    assert _proposal_row(migrated_engine, pid)["status"] == "landed"
    node_ids = {n["id"] for n in _nodes(migrated_engine, ids)}
    assert c_node in node_ids  # 被剔除的删除未执行
    assert (a, b) not in _edges(migrated_engine, ids)


def test_delta_confirm_http_paths(server_client: TestClient) -> None:
    """HTTP：Agent 403（O9）/ removed_ops 越界 422 / 全剔除 422 / adjustments 非空 422 /
    base 过期 409 DELTA_BASE_MISMATCH + 提案 failed + 线程消息 + delta.rejected 诊断。"""
    engine: Engine = server_client.app.state.engine  # type: ignore[attr-defined]
    ids = _seed(engine)
    orch_headers = _agent_headers(server_client, engine, ids)
    pid, a, b, c_node = _awaiting_delta(engine, ids)
    canvas = _canvas_dict(engine, ids["channel"])
    proposal = _proposal_row(engine, pid)
    good = {
        "proposal_hash": proposal["proposal_hash"],
        "baseline_version": canvas["baseline_version"],
        "baseline_hash": canvas["baseline_hash"],
    }
    url = f"/api/proposals/{pid}/confirm"

    # Agent 403（O9 同门）
    r = server_client.post(url, json={"expected": good}, headers=orch_headers)
    assert r.status_code == 403 and rest.ErrorResponse.model_validate(r.json()).error.rule == "O9"
    # adjustments 非空 → 422
    adj = [{"op": "edit_merge_plan", "merge_plan": "x"}]
    r = server_client.post(url, json={"expected": good, "adjustments": adj})
    assert r.status_code == 422, r.text
    # removed_ops 越界 → 422
    r = server_client.post(url, json={"expected": good, "removed_ops": [99]})
    assert r.status_code == 422, r.text
    # 全剔除 → 422
    r = server_client.post(url, json={"expected": good, "removed_ops": [0, 1, 2, 3]})
    assert r.status_code == 422, r.text

    # base 过期 → 人类建节点推进画布基线，使 delta.base_hash 过期；客户端 expected 用新基线。
    server_client.post(
        f"/api/canvases/{ids['canvas']}/nodes", json={"title": "人类新增", "kind": "agent"})
    fresh_canvas = server_client.get(f"/api/channels/{ids['channel']}/canvas").json()["canvas"]
    stale_good = {
        "proposal_hash": proposal["proposal_hash"],
        "baseline_version": fresh_canvas["baseline_version"],
        "baseline_hash": fresh_canvas["baseline_hash"],
    }
    r = server_client.post(url, json={"expected": stale_good, "removed_ops": []})
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "DELTA_BASE_MISMATCH"
    assert _proposal_row(engine, pid)["status"] == "failed"  # F9：提案作废
    assert _diag_count(engine, "delta.rejected") == 1
    with engine.connect() as conn:
        thread = conn.execute(
            select(_MSG.c.body).where(
                _MSG.c.thread_root_id == ids["root_msg"], _MSG.c.body.like("%基线已过期%"))
        ).first()
    assert thread is not None


def test_delta_confirm_node_active_422(server_client: TestClient) -> None:
    """剩余 op 集重验含 NODE_ACTIVE（确认前删除目标转 in_progress）→ 422 NODE_ACTIVE。"""
    engine: Engine = server_client.app.state.engine  # type: ignore[attr-defined]
    ids = _seed(engine)
    pid, a, b, c_node = _awaiting_delta(engine, ids)
    canvas = _canvas_dict(engine, ids["channel"])
    proposal = _proposal_row(engine, pid)
    # 把 remove_node 目标 c 的任务置 in_progress（确认时才转活动）。
    with engine.begin() as conn:
        c_task = conn.execute(select(_NODE.c.task_id).where(_NODE.c.id == c_node)).scalar_one()
        conn.execute(update(_TASK).where(_TASK.c.id == c_task).values(status="in_progress"))
    good = {
        "proposal_hash": proposal["proposal_hash"],
        "baseline_version": canvas["baseline_version"],
        "baseline_hash": canvas["baseline_hash"],
    }
    r = server_client.post(f"/api/proposals/{pid}/confirm", json={"expected": good})
    assert r.status_code == 422, r.text
    err = rest.ErrorResponse.model_validate(r.json())
    assert err.error.code is rest.ErrorCode.NODE_ACTIVE
    assert _proposal_row(engine, pid)["status"] == "awaiting_confirm"  # 无副作用


def test_delta_confirm_stale_cas_409(server_client: TestClient) -> None:
    """CAS 三字段不符 → 409 STALE_CONFIRM 携最新态（delta 与 full 同门）。"""
    engine: Engine = server_client.app.state.engine  # type: ignore[attr-defined]
    ids = _seed(engine)
    pid, *_ = _awaiting_delta(engine, ids)
    proposal = _proposal_row(engine, pid)
    canvas = _canvas_dict(engine, ids["channel"])
    good = {
        "proposal_hash": proposal["proposal_hash"],
        "baseline_version": canvas["baseline_version"],
        "baseline_hash": canvas["baseline_hash"],
    }
    for field, bogus in (("proposal_hash", "e" * 64), ("baseline_hash", "e" * 64)):
        r = server_client.post(
            f"/api/proposals/{pid}/confirm", json={"expected": {**good, field: bogus}})
        assert r.status_code == 409, (field, r.text)
        assert r.json()["error"]["code"] == "STALE_CONFIRM"


# ================================================================ 落地执行


def test_delta_landing_full_chain(migrated_engine: Engine) -> None:
    """直落 delta 全链：加 N1 + 连 b→N1 + 删边 a→b + 删 c → 结构正确、基线恰 bump 一次、
    「增量已落地」消息恰一条。"""
    ids = _seed(migrated_engine, decomp_mode="direct")
    a, _ = _add_node(migrated_engine, ids, "A")
    b, _ = _add_node(migrated_engine, ids, "B")
    c_node, _ = _add_node(migrated_engine, ids, "C")
    _add_edge(migrated_engine, ids, a, b)
    base = _sync_baseline(migrated_engine, ids)
    ver_before = _canvas_version(migrated_engine, ids)
    body = _delta_body(base, [
        _add_node_op("N1", "增量实现"),
        {"op": "add_edge", "from": b, "to": "N1"},
        {"op": "remove_edge", "from": a, "to": b},
        {"op": "remove_node", "node_id": c_node},
    ])
    pid = _insert_delta(migrated_engine, ids, body, status="landing")

    bus, events = _bus()
    result = landing_domain.pending_landing_scan(migrated_engine, bus)
    assert len(result["created"]) == 1
    bid = result["created"][0]
    assert result["executed"][bid] == "landed"

    nodes = _nodes(migrated_engine, ids)
    node_ids = {n["id"] for n in nodes}
    assert c_node not in node_ids  # 已删
    assert a in node_ids and b in node_ids
    new_nodes = [n for n in nodes if n["id"] not in {a, b}]
    assert len(new_nodes) == 1  # N1 落地
    n1_id = new_nodes[0]["id"]
    edges = _edges(migrated_engine, ids)
    assert (a, b) not in edges and (b, n1_id) in edges
    assert _canvas_version(migrated_engine, ids) == ver_before + 1  # bump 恰一次
    with migrated_engine.connect() as conn:
        landed_msg = conn.execute(
            select(func.count()).select_from(_MSG).where(
                _MSG.c.channel_id == ids["channel"], _MSG.c.body.like("增量已落地%"))
        ).scalar_one()
    assert landed_msg == 1
    assert _proposal_row(migrated_engine, pid)["status"] == "landed"
    assert any(e.type is EventType.CANVAS_NODE_REMOVED for e in events)
    assert any(e.type is EventType.CANVAS_EDGE_REMOVED for e in events)


def test_delta_landing_reentry_no_duplicates(migrated_engine: Engine) -> None:
    """对账 #4 幂等重入：再扫 → already_done、零新产物、消息仍恰一条。"""
    ids = _seed(migrated_engine, decomp_mode="direct")
    a, _ = _add_node(migrated_engine, ids, "A")
    base = _sync_baseline(migrated_engine, ids)
    ops = [_add_node_op("N1", "n"), {"op": "add_edge", "from": a, "to": "N1"}]
    _insert_delta(migrated_engine, ids, _delta_body(base, ops), status="landing")
    bus, _ = _bus()
    landing_domain.pending_landing_scan(migrated_engine, bus)
    nodes_before = len(_nodes(migrated_engine, ids))
    ver_before = _canvas_version(migrated_engine, ids)
    bid = _batch_id(migrated_engine)
    second = landing_domain.execute_batch(migrated_engine, bus, bid)
    assert second == "already_done"
    landing_domain.pending_landing_scan(migrated_engine, bus)
    assert len(_nodes(migrated_engine, ids)) == nodes_before
    assert _canvas_version(migrated_engine, ids) == ver_before
    with migrated_engine.connect() as conn:
        assert conn.execute(
            select(func.count()).select_from(_MSG).where(_MSG.c.body.like("增量已落地%"))
        ).scalar_one() == 1


def test_delta_landing_step_atomic_crash_resume(migrated_engine: Engine) -> None:
    """步原子 + 崩溃续段：新增节点与其入边同一步——边处理器炸 → 整步回滚（节点也不留）；重入补齐。"""
    ids = _seed(migrated_engine, decomp_mode="direct")
    a, _ = _add_node(migrated_engine, ids, "A")
    base = _sync_baseline(migrated_engine, ids)
    ops = [_add_node_op("N1", "n"), {"op": "add_edge", "from": a, "to": "N1"}]
    _insert_delta(migrated_engine, ids, _delta_body(base, ops), status="landing")
    bus, _ = _bus()

    real = landing_domain._DELTA_HANDLERS["create_edge"]

    def boom(tx: Any, ctx: Any, op: Any) -> dict[str, Any]:
        raise RuntimeError("simulated crash")

    landing_domain._DELTA_HANDLERS["create_edge"] = boom
    try:
        with pytest.raises(RuntimeError, match="simulated crash"):
            landing_domain.pending_landing_scan(migrated_engine, bus)
    finally:
        landing_domain._DELTA_HANDLERS["create_edge"] = real

    # N1 节点与其入边同一步 → 整步回滚，N1 不落（现画布仅 a）。
    assert {n["id"] for n in _nodes(migrated_engine, ids)} == {a}
    # 重入补齐。
    bid = _batch_id(migrated_engine)
    assert landing_domain.execute_batch(migrated_engine, bus, bid) == "landed"
    nodes = _nodes(migrated_engine, ids)
    assert len(nodes) == 2
    n1 = next(n["id"] for n in nodes if n["id"] != a)
    assert (a, n1) in _edges(migrated_engine, ids)


def test_delta_landing_exec_node_active_fail_closed(migrated_engine: Engine) -> None:
    """执行期复核：remove_node 目标落地前转 in_progress → 整批 fail-closed（不静默删活动节点）。"""
    ids = _seed(migrated_engine, decomp_mode="direct")
    a, _ = _add_node(migrated_engine, ids, "A")
    c_node, c_task = _add_node(migrated_engine, ids, "C")
    base = _sync_baseline(migrated_engine, ids)
    pid = _insert_delta(migrated_engine, ids,
                        _delta_body(base, [{"op": "remove_node", "node_id": c_node}]),
                        status="landing")
    # 校验通过（todo）后目标转活动。
    with migrated_engine.begin() as conn:
        conn.execute(update(_TASK).where(_TASK.c.id == c_task).values(status="in_progress"))
    bus, events = _bus()
    result = landing_domain.pending_landing_scan(migrated_engine, bus)
    bid = _batch_id(migrated_engine)
    assert result["executed"][bid] == "fail_closed"
    with migrated_engine.connect() as conn:
        status = conn.execute(select(_BATCH.c.status).where(_BATCH.c.id == bid)).scalar_one()
    assert status == LandingBatchStatus.FAIL_CLOSED.value
    assert c_node in {n["id"] for n in _nodes(migrated_engine, ids)}  # 活动节点未删
    assert _proposal_row(migrated_engine, pid)["status"] == "failed"
    assert any(e.type is EventType.LANDING_FAIL_CLOSED for e in events)


def _batch_id(engine: Engine) -> str:
    with engine.connect() as c:
        return c.execute(
            select(_BATCH.c.id).where(_BATCH.c.kind == "delta")
        ).scalars().first()  # type: ignore[return-value]


# ================================================================ O9 拦截


def _agent_headers(client: TestClient, engine: Engine, ids: dict[str, str]) -> dict[str, str]:
    """把 seed 的 orch Agent 挂到有 api_key 的 computer 上，返回 Agent 主体请求头。"""
    key = "cak_delta_o9"
    digest = hashlib.sha256(key.encode()).hexdigest()
    with engine.begin() as c:
        comp_id = c.execute(
            select(models.Agent.__table__.c.computer_id).where(
                models.Agent.__table__.c.member_id == ids["orch"])
        ).scalar_one()
        c.execute(update(models.Computer.__table__)
                  .where(models.Computer.__table__.c.id == comp_id)
                  .values(api_key_hash=digest))
    return {"Authorization": f"Bearer {key}", "X-Acting-Member": ids["orch"]}


def test_o9_canvas_writes_agent_403_human_ok(server_client: TestClient) -> None:
    """O9：四结构写端点对 Agent 403 rule=O9；人类（owner）放行。"""
    engine: Engine = server_client.app.state.engine  # type: ignore[attr-defined]
    ids = _seed(engine)
    headers = _agent_headers(server_client, engine, ids)
    cv = ids["canvas"]

    # 人类建两节点 + 一边（放行）。
    a = server_client.post(f"/api/canvases/{cv}/nodes", json={"title": "A", "kind": "agent"})
    assert a.status_code == 201, a.text
    a_id = a.json()["node"]["id"]
    b = server_client.post(f"/api/canvases/{cv}/nodes", json={"title": "B", "kind": "agent"})
    b_id = b.json()["node"]["id"]
    e = server_client.post(f"/api/canvases/{cv}/edges",
                           json={"from_node_id": a_id, "to_node_id": b_id})
    assert e.status_code == 201, e.text
    edge_id = e.json()["edge"]["id"]

    # Agent 主体：四端点均 403 rule=O9。
    cases = [
        server_client.post(f"/api/canvases/{cv}/nodes",
                           json={"title": "X", "kind": "agent"}, headers=headers),
        server_client.delete(f"/api/canvases/{cv}/nodes/{a_id}", headers=headers),
        server_client.post(f"/api/canvases/{cv}/edges",
                           json={"from_node_id": b_id, "to_node_id": a_id}, headers=headers),
        server_client.delete(f"/api/canvases/{cv}/edges/{edge_id}", headers=headers),
    ]
    for r in cases:
        assert r.status_code == 403, r.text
        assert rest.ErrorResponse.model_validate(r.json()).error.rule == "O9"

    # 人类删边/删节点放行。
    assert server_client.delete(f"/api/canvases/{cv}/edges/{edge_id}").status_code == 200
    assert server_client.delete(f"/api/canvases/{cv}/nodes/{a_id}").status_code == 200
