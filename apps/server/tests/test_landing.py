"""J9 草稿确认与落地测试（契约 B §5 S2/§12.4/§12.5、拆解设计 §8/§9、A §4.7）。

分四层：
- 纯函数层：apply_adjustments 六 op 形状 / 违形与悬空引用 422；
- 执行器层（migrated_engine + EventBus 收集器，直驱 pending_landing_scan/execute_batch）：
  直落全链 / 落地产物（拓扑/锚点/L2 契约初稿/writes_code 贯通/汇总/merge 自动追加正反例/
  single_task）/ A5 崩溃续段 / fail-closed 重放停批 / 对账 #4 幂等重入 / baseline bump 恰一次；
- fail-closed 持久性（M5 挂账 B §12.5 #4）：persist_fail_closed 独立连接两路（批行已存/回滚消失）
  + tmpl node-mismatch HTTP 端到端（409 回滚后处置链持久）；
- HTTP 层（server_client + spy hub）：confirm CAS 逐路径（三字段/非 awaiting/Agent 403/delta 422/
  removed_ops 422/调整重验红例/202 形状）+ reject 逐路径 + 确认后后台执行器落地（真 hub 全链）。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import time
from typing import Any

import pytest
from coagentia_contracts import rest
from coagentia_contracts.enums import LandingBatchStatus, ProposalStatus
from coagentia_contracts.kernel.decomposition import proposal_fingerprint
from coagentia_contracts.ws import EventType
from coagentia_server.api import ApiError
from coagentia_server.db import models
from coagentia_server.events import EventBus, PendingEvent
from coagentia_server.ledger import service
from coagentia_server.ledger.service import new_ulid, now_iso
from coagentia_server.orchestration import draft as draft_domain
from coagentia_server.orchestration import landing as landing_domain
from coagentia_server.orchestration import proposal as pd
from coagentia_server.orchestration.role_templates import (
    ORCHESTRATOR_ROLE_KEY,
    upsert_builtin_role_templates,
)
from daemon_helpers import Env
from fastapi.testclient import TestClient
from perf_helpers import count_queries
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
_ACTIVITY = models.tbl(models.ActivityItem)
_CONTRACT = models.tbl(models.TaskContract)


# ---------------------------------------------------------------- 场景构造（照 test_proposals）


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
    """channel + orchestrator + human + canvas + source 任务 + 绑定 Project。"""
    upsert_builtin_role_templates(engine)
    env = Env(engine)
    channel = env.add_channel(name="land")
    orch = env.add_agent("Orch", "idle")
    human = env.owner_id
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
        # daemon_helpers.Env 不建画布——落地需要（每频道恰一画布，契约 A §6）。
        c.execute(
            insert(_CANVAS).values(
                id=new_ulid(), workspace_id=env.ws_id, channel_id=channel,
                baseline_version=0, baseline_hash="0" * 64, updated_at=now_iso(),
            )
        )
    env.join(channel, orch)
    env.join(channel, human)
    root_msg = env.add_message(channel, author=human, body="需要一个登录功能")
    task_id = new_ulid()
    project_id = new_ulid()
    with engine.begin() as c:
        c.execute(
            insert(_TASK).values(
                id=task_id, workspace_id=env.ws_id, channel_id=channel, number=1,
                root_message_id=root_msg, title="登录功能", status="todo", level="l1",
                created_by_member_id=human, status_changed_at=now_iso(), created_at=now_iso(),
            )
        )
        # 直插 number=1 后同步编号游标（否则落地 create_task 撞 UNIQUE(channel,number)）。
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
    return {
        "ws": env.ws_id, "channel": channel, "orch": orch, "human": human,
        "task": task_id, "root_msg": root_msg, "project": project_id,
    }


def _plan(goal: str) -> dict[str, Any]:
    return {
        "goal": goal,
        "acceptance_criteria": [
            {"id": "AC1", "statement": f"{goal} 完成", "verify_by": "command",
             "verify_ref": "pytest"},
        ],
    }


def _single_task_body(source: str) -> dict[str, Any]:
    return {
        "version": "coagentia.decomposition.v1",
        "source": source,
        "mode": "single_task",
        "summary": "单节点实现登录",
        "nodes": [
            {"temp_id": "N1", "title": "实现登录", "kind": "agent", "task_plan": _plan("实现登录")}
        ],
    }


def _decompose_body(ids: dict[str, str]) -> dict[str, Any]:
    """2 个 writes_code 实现节点（建议 owner=human）+ 1 个评审节点（依赖两实现）；
    未显式声明 merge → 落地自动追加；agent 节点 3 > 1 → 汇总追加。"""
    return {
        "version": "coagentia.decomposition.v1",
        "source": ids["task"],
        "mode": "decompose",
        "summary": "拆成两个并行实现 + 评审",
        "nodes": [
            {"temp_id": "impl_a", "title": "实现 A", "kind": "agent",
             "task_plan": _plan("实现 A"), "writes_code": True, "project": ids["project"],
             "suggested_owner": ids["human"]},
            {"temp_id": "impl_b", "title": "实现 B", "kind": "agent",
             "task_plan": _plan("实现 B"), "writes_code": True, "project": ids["project"],
             "suggested_owner": ids["human"]},
            {"temp_id": "review", "title": "评审", "kind": "agent",
             "task_plan": _plan("评审")},
        ],
        "edges": [
            {"from": "impl_a", "to": "review"},
            {"from": "impl_b", "to": "review"},
        ],
        "merge_plan": "A 先 B 后按 DAG 序合并",
    }


def _control_msg(body: dict[str, Any]) -> str:
    return "拆解提案：\n\n<control>" + json.dumps(body, ensure_ascii=False) + "</control>"


def _channel_row(engine: Engine, channel_id: str) -> dict[str, Any]:
    with engine.connect() as c:
        return dict(
            c.execute(
                select(models.Channel.__table__)
                .where(models.Channel.__table__.c.id == channel_id)
            ).mappings().one()
        )


def _proposal_row(engine: Engine, pid: str) -> dict[str, Any]:
    with engine.connect() as c:
        return models.row_dict(
            c.execute(select(_PROPOSAL).where(_PROPOSAL.c.id == pid)).mappings().one()
        )


def _submit_proposal(engine: Engine, ids: dict[str, str], body: dict[str, Any]) -> str:
    """建 drafting → classify_submission 提交有效提案；返回 proposal id（awaiting 或 landing）。"""
    with _tx(engine) as tx:
        proposal = pd.create_drafting_proposal(
            tx, workspace_id=ids["ws"], channel_id=ids["channel"],
            source_task_id=ids["task"], proposed_by=ids["orch"],
        )
        pid = proposal["id"]
    channel = _channel_row(engine, ids["channel"])
    with _tx(engine) as tx:
        decision = pd.classify_submission(
            tx, channel=channel, author_member_id=ids["orch"],
            body=_control_msg(body), thread_root_id=ids["root_msg"],
        )
        assert decision is not None
        decision.apply(tx)
    return pid


def _bus() -> tuple[EventBus, list[PendingEvent]]:
    bus = EventBus()
    events: list[PendingEvent] = []
    bus.subscribe(events.append)
    return bus, events


def _landed_msg_count(engine: Engine, channel_id: str) -> int:
    with engine.connect() as c:
        return c.execute(
            select(func.count()).select_from(_MSG).where(
                _MSG.c.channel_id == channel_id, _MSG.c.body.like("拆解已落地%")
            )
        ).scalar_one()


def _channel_nodes(engine: Engine, channel_id: str) -> list[dict[str, Any]]:
    with engine.connect() as c:
        canvas_id = c.execute(
            select(_CANVAS.c.id).where(_CANVAS.c.channel_id == channel_id)
        ).scalar_one()
        return [
            dict(r) for r in c.execute(
                select(_NODE).where(_NODE.c.canvas_id == canvas_id).order_by(_NODE.c.id)
            ).mappings()
        ]


def _channel_edges(engine: Engine, channel_id: str) -> list[tuple[str, str]]:
    with engine.connect() as c:
        canvas_id = c.execute(
            select(_CANVAS.c.id).where(_CANVAS.c.channel_id == channel_id)
        ).scalar_one()
        return [
            (r[0], r[1]) for r in c.execute(
                select(_EDGE.c.from_node_id, _EDGE.c.to_node_id)
                .where(_EDGE.c.canvas_id == canvas_id)
            )
        ]


def _canvas_version(engine: Engine, channel_id: str) -> int:
    with engine.connect() as c:
        return c.execute(
            select(_CANVAS.c.baseline_version).where(_CANVAS.c.channel_id == channel_id)
        ).scalar_one()


def _diag_count(engine: Engine, diag_type: str) -> int:
    with engine.connect() as c:
        return c.execute(
            select(func.count()).select_from(_DIAG).where(_DIAG.c.type == diag_type)
        ).scalar_one()


# ---------------------------------------------------------------- apply_adjustments 纯函数层


def test_apply_adjustments_six_op_shapes() -> None:
    body = {
        "version": "coagentia.decomposition.v1", "source": "T", "mode": "decompose",
        "summary": "s",
        "nodes": [
            {"temp_id": "N1", "title": "一", "kind": "agent", "task_plan": _plan("一")},
            {"temp_id": "N2", "title": "二", "kind": "agent", "task_plan": _plan("二")},
            {"temp_id": "N3", "title": "三", "kind": "agent", "task_plan": _plan("三")},
        ],
        "edges": [{"from": "N1", "to": "N2"}, {"from": "N2", "to": "N3"}],
        "merge_plan": None,
    }
    adjusted = draft_domain.apply_adjustments(body, [
        {"op": "remove_node", "temp_id": "N3"},          # 连带删 N2→N3 边
        {"op": "add_node", "node": {"temp_id": "N4", "title": "四", "kind": "agent",
                                    "task_plan": _plan("四")}},
        {"op": "add_edge", "from": "N2", "to": "N4"},
        {"op": "add_edge", "from": "N2", "to": "N4"},    # 重复 add_edge 幂等忽略
        {"op": "remove_edge", "from": "N1", "to": "N2"},
        {"op": "edit_node", "temp_id": "N1", "changes": {"title": "改名"}},
        {"op": "edit_merge_plan", "merge_plan": "先 N1"},
    ])
    temp_ids = [n["temp_id"] for n in adjusted["nodes"]]
    assert temp_ids == ["N1", "N2", "N4"]
    assert adjusted["edges"] == [{"from": "N2", "to": "N4"}]
    assert adjusted["nodes"][0]["title"] == "改名"
    assert adjusted["merge_plan"] == "先 N1"
    # 纯函数：原 body 未被修改（深拷贝）。
    assert [n["temp_id"] for n in body["nodes"]] == ["N1", "N2", "N3"]
    assert len(body["edges"]) == 2


def test_apply_adjustments_invalid_ops_422() -> None:
    body = {
        "nodes": [{"temp_id": "N1", "title": "一"}],
        "edges": [{"from": "N1", "to": "N1x"}],
    }
    cases: list[list[Any]] = [
        [{"op": "nope"}],                                        # 未知 op
        [{"op": "remove_node", "temp_id": "NX"}],                # 悬空 remove_node
        [{"op": "remove_edge", "from": "A", "to": "B"}],         # 悬空 remove_edge
        [{"op": "edit_node", "temp_id": "NX", "changes": {"title": "x"}}],  # 悬空 edit
        [{"op": "add_node", "node": {"temp_id": "N1"}}],         # temp_id 冲突
        [{"op": "add_node", "node": {"title": "缺 id"}}],        # 缺 temp_id
        [{"op": "edit_node", "temp_id": "N1", "changes": {"kind": "system"}}],  # 不可改字段
        [{"op": "edit_node", "temp_id": "N1", "changes": {}}],   # 空 changes
        [{"op": "add_edge", "from": "N1"}],                      # 缺 to（类型违形）
        [{"op": "edit_merge_plan"}],                             # 缺 merge_plan 键
        [{"op": "remove_node", "temp_id": "N1", "extra": 1}],    # 额外键
        ["not-an-object"],                                       # 非对象
    ]
    for adjustments in cases:
        with pytest.raises(ApiError) as exc_info:
            draft_domain.apply_adjustments(body, adjustments)
        assert exc_info.value.status == 422, adjustments
        assert exc_info.value.body.code is rest.ErrorCode.VALIDATION_FAILED


# ---------------------------------------------------------------- 直落全链 + 落地产物


def test_direct_mode_scan_lands_full_chain(migrated_engine: Engine) -> None:
    """直落（§8.3/A8）：landing 无批 → 建批 auto(channel-policy) → 执行 → landed。
    产物断言：任务 L2+TaskPlan 初稿 / writes_code 贯通 / merge 自动追加 / 汇总 owner=Orch /
    连边拓扑 / baseline bump 恰一次 / 已落地消息恰一条（@激活节点建议 owner）。"""
    ids = _seed(migrated_engine, decomp_mode="direct")
    pid = _submit_proposal(migrated_engine, ids, _decompose_body(ids))
    assert _proposal_row(migrated_engine, pid)["status"] == ProposalStatus.LANDING.value
    version_before = _canvas_version(migrated_engine, ids["channel"])

    bus, events = _bus()
    result = landing_domain.pending_landing_scan(migrated_engine, bus)
    assert len(result["created"]) == 1
    batch_id = result["created"][0]
    assert result["executed"][batch_id] == "landed"

    # 批次：auto(channel-policy)、content_hash=proposal_hash、done。
    proposal = _proposal_row(migrated_engine, pid)
    with migrated_engine.connect() as c:
        batch = dict(
            c.execute(select(_BATCH).where(_BATCH.c.id == batch_id)).mappings().one()
        )
    assert batch["confirmed_by"] == landing_domain.AUTO_CONFIRMED_BY
    assert batch["content_hash"] == proposal["proposal_hash"]
    assert batch["status"] == LandingBatchStatus.DONE.value
    assert batch["done_at"] is not None
    assert proposal["status"] == ProposalStatus.LANDED.value
    assert proposal["landed_hash"] == proposal["proposal_hash"]  # 直落无调整

    # 画布产物：3 agent + 1 auto-merge system + 1 summary = 5 节点。
    nodes = _channel_nodes(migrated_engine, ids["channel"])
    assert len(nodes) == 5
    agent_nodes = [n for n in nodes if n["kind"] == "agent" and not n["is_summary"]]
    system_nodes = [n for n in nodes if n["kind"] == "system"]
    summary_nodes = [n for n in nodes if n["is_summary"]]
    assert len(agent_nodes) == 3 and len(system_nodes) == 1 and len(summary_nodes) == 1
    assert system_nodes[0]["system_action"] == "merge"
    assert system_nodes[0]["system_status"] == "idle"

    # 任务：L2、writes_code/project 贯通、TaskPlan 初稿契约。
    with migrated_engine.connect() as c:
        tasks = {
            t["title"]: dict(t) for t in c.execute(
                select(_TASK).where(_TASK.c.channel_id == ids["channel"], _TASK.c.id != ids["task"])
            ).mappings()
        }
    assert set(tasks) == {"实现 A", "实现 B", "评审", "汇总交付：3 个子任务"}
    for title in ("实现 A", "实现 B"):
        assert tasks[title]["level"] == "l2"
        assert tasks[title]["writes_code"] in (True, 1)
        assert tasks[title]["project_id"] == ids["project"]
        assert tasks[title]["owner_member_id"] is None  # O4 建议不锁定
    assert tasks["评审"]["writes_code"] in (False, 0, None)
    summary_task = tasks["汇总交付：3 个子任务"]
    assert summary_task["owner_member_id"] == ids["orch"]  # 裁决 #16 owner=Orchestrator
    with migrated_engine.connect() as c:
        plan_count = c.execute(
            select(func.count()).select_from(_CONTRACT).where(
                _CONTRACT.c.task_id.in_(
                    [tasks[t]["id"] for t in ("实现 A", "实现 B", "评审")]
                ),
                _CONTRACT.c.kind == "task_plan",
            )
        ).scalar_one()
    assert plan_count == 3  # task_plan 作 L2 契约初稿
    # 汇总任务无 TaskPlan（裁量：引擎不代拟验收标准）。
    with migrated_engine.connect() as c:
        assert c.execute(
            select(func.count()).select_from(_CONTRACT).where(
                _CONTRACT.c.task_id == summary_task["id"]
            )
        ).scalar_one() == 0

    # 连边拓扑：a→review、b→review、a→merge、b→merge、merge→summary、review→summary = 6。
    by_task = {n["task_id"]: n["id"] for n in nodes if n["task_id"] is not None}
    n_a, n_b = by_task[tasks["实现 A"]["id"]], by_task[tasks["实现 B"]["id"]]
    n_rev = by_task[tasks["评审"]["id"]]
    n_sum = by_task[summary_task["id"]]
    n_merge = system_nodes[0]["id"]
    edges = set(_channel_edges(migrated_engine, ids["channel"]))
    assert edges == {
        (n_a, n_rev), (n_b, n_rev),
        (n_a, n_merge), (n_b, n_merge),
        (n_merge, n_sum), (n_rev, n_sum),
    }

    # baseline bump 恰一次（批末统一）。
    assert _canvas_version(migrated_engine, ids["channel"]) == version_before + 1

    # 已落地消息恰一条；mention = 激活节点（impl_a/impl_b 无上游）的建议 owner（human 去重一条）。
    assert _landed_msg_count(migrated_engine, ids["channel"]) == 1
    with migrated_engine.connect() as c:
        landed_msg = c.execute(
            select(_MSG).where(
                _MSG.c.channel_id == ids["channel"], _MSG.c.body.like("拆解已落地%")
            )
        ).mappings().one()
        mentions = list(c.execute(
            select(models.MessageMention.__table__.c.member_id).where(
                models.MessageMention.__table__.c.message_id == landed_msg["id"]
            )
        ).scalars())
    assert landed_msg["thread_root_id"] == ids["root_msg"]  # 进 source 线程
    assert mentions == [ids["human"]]

    # WS 事件：landing.started + landing.completed 各一（本 bus 收集）。
    etypes = [e.type for e in events]
    assert etypes.count(EventType.LANDING_STARTED) == 1
    assert etypes.count(EventType.LANDING_COMPLETED) == 1
    # 诊断：started/op_applied（9 op：3 节点+2 边+merge+summary... 共 3+2+1+1=7）/completed。
    assert _diag_count(migrated_engine, "landing.started") == 1
    assert _diag_count(migrated_engine, "landing.op_applied") == 7
    assert _diag_count(migrated_engine, "landing.completed") == 1


def test_landing_explicit_merge_not_duplicated(migrated_engine: Engine) -> None:
    """裁决 #6 反例：提案已显式声明 merge 系统节点 → 不自动追加（仍恰一个 merge）。"""
    ids = _seed(migrated_engine, decomp_mode="direct")
    body = {
        "version": "coagentia.decomposition.v1", "source": ids["task"], "mode": "decompose",
        "summary": "显式 merge",
        "nodes": [
            {"temp_id": "a", "title": "实现 A", "kind": "agent", "task_plan": _plan("A"),
             "writes_code": True, "project": ids["project"]},
            {"temp_id": "b", "title": "实现 B", "kind": "agent", "task_plan": _plan("B"),
             "writes_code": True, "project": ids["project"]},
            {"temp_id": "m", "title": "合并", "kind": "system", "system_action": "merge"},
        ],
        "edges": [{"from": "a", "to": "m"}, {"from": "b", "to": "m"}],
        "merge_plan": "a 先 b 后",
    }
    _submit_proposal(migrated_engine, ids, body)
    bus, _ = _bus()
    result = landing_domain.pending_landing_scan(migrated_engine, bus)
    assert list(result["executed"].values()) == ["landed"]
    nodes = _channel_nodes(migrated_engine, ids["channel"])
    merges = [n for n in nodes if n["system_action"] == "merge"]
    assert len(merges) == 1  # 显式声明 → 不重复追加
    # agent 2 + merge 1 + summary 1 = 4；汇总依赖全部叶子（= 显式 merge 节点）。
    assert len(nodes) == 4
    summary = next(n for n in nodes if n["is_summary"])
    edges = _channel_edges(migrated_engine, ids["channel"])
    assert (merges[0]["id"], summary["id"]) in edges


def test_landing_no_writes_code_no_merge(migrated_engine: Engine) -> None:
    """无 writes_code → 不追加 merge；agent >1 → 仍追加汇总（依赖全部叶子）。"""
    ids = _seed(migrated_engine, decomp_mode="direct")
    body = {
        "version": "coagentia.decomposition.v1", "source": ids["task"], "mode": "decompose",
        "summary": "纯调研两步",
        "nodes": [
            {"temp_id": "s1", "title": "调研", "kind": "agent", "task_plan": _plan("调研")},
            {"temp_id": "s2", "title": "写报告", "kind": "agent", "task_plan": _plan("报告")},
        ],
        "edges": [{"from": "s1", "to": "s2"}],
    }
    _submit_proposal(migrated_engine, ids, body)
    bus, _ = _bus()
    landing_domain.pending_landing_scan(migrated_engine, bus)
    nodes = _channel_nodes(migrated_engine, ids["channel"])
    assert [n for n in nodes if n["kind"] == "system"] == []  # 无 merge 追加
    assert len([n for n in nodes if n["is_summary"]]) == 1


def test_single_task_lands_without_summary_or_merge(migrated_engine: Engine) -> None:
    """A7：single_task 恰 1 节点、无边、无汇总、无 merge。"""
    ids = _seed(migrated_engine, decomp_mode="direct")
    _submit_proposal(migrated_engine, ids, _single_task_body(ids["task"]))
    bus, _ = _bus()
    result = landing_domain.pending_landing_scan(migrated_engine, bus)
    assert list(result["executed"].values()) == ["landed"]
    nodes = _channel_nodes(migrated_engine, ids["channel"])
    assert len(nodes) == 1
    assert nodes[0]["kind"] == "agent" and not nodes[0]["is_summary"]
    assert _channel_edges(migrated_engine, ids["channel"]) == []
    assert _landed_msg_count(migrated_engine, ids["channel"]) == 1


# ---------------------------------------------------------------- confirm 落账（A3）+ 对账 #4


def test_confirm_apply_lands_adjusted_content(migrated_engine: Engine) -> None:
    """A3（单元路径）：删一节点 + 改建议 owner 后确认 → 落地结果与调整一致；账本含
    proposal_hash/landed_hash/adjustments/confirmed_by；执行由对账 #4 扫描接手（confirm 后
    崩溃语义——建批未执行 → 重入补齐）。"""
    ids = _seed(migrated_engine)  # draft 模式
    pid = _submit_proposal(migrated_engine, ids, _decompose_body(ids))
    assert _proposal_row(migrated_engine, pid)["status"] == ProposalStatus.AWAITING_CONFIRM.value

    adjustments = [
        {"op": "remove_node", "temp_id": "review"},
        {"op": "edit_node", "temp_id": "impl_a", "changes": {"suggested_owner": None}},
    ]
    proposal = _proposal_row(migrated_engine, pid)
    adjusted = draft_domain.apply_adjustments(proposal["body"], adjustments)
    landed_hash = proposal_fingerprint(adjusted)
    assert landed_hash != proposal["proposal_hash"]
    with _tx(migrated_engine) as tx:
        batch, refreshed = draft_domain.confirm_apply(
            tx, proposal=proposal, adjustments=adjustments,
            landed_hash=landed_hash, confirmed_by=ids["human"],
        )
        etypes = [e for e, _, _ in tx.events]
    assert refreshed["status"] == ProposalStatus.LANDING.value
    assert EventType.DRAFT_CONFIRMED in etypes and EventType.LANDING_STARTED in etypes

    # 落账断言：adjustments / landed_hash / confirmed_by / content_hash。
    row = _proposal_row(migrated_engine, pid)
    assert row["adjustments"] == adjustments
    assert row["landed_hash"] == landed_hash
    assert batch.confirmed_by == ids["human"]
    assert batch.content_hash == landed_hash
    assert _diag_count(migrated_engine, "draft.confirmed") == 1

    # 对账 #4：confirm 后引擎未跑（模拟建批后崩溃）→ 扫描领 running 批补齐。
    bus, _ = _bus()
    result = landing_domain.pending_landing_scan(migrated_engine, bus)
    assert result["created"] == []  # 批已存在（confirm 原子建批），不再建
    assert result["executed"][batch.id] == "landed"

    # 调整生效：review 被删 → 2 实现 + merge + 汇总 = 4 节点；无「评审」任务。
    nodes = _channel_nodes(migrated_engine, ids["channel"])
    assert len(nodes) == 4
    with migrated_engine.connect() as c:
        titles = set(c.execute(
            select(_TASK.c.title).where(
                _TASK.c.channel_id == ids["channel"], _TASK.c.id != ids["task"]
            )
        ).scalars())
    assert "评审" not in titles
    # impl_a 建议 owner 已清空 → 已落地消息 mention 只剩 impl_b 的建议人。
    with migrated_engine.connect() as c:
        landed_msg = c.execute(
            select(_MSG).where(
                _MSG.c.channel_id == ids["channel"], _MSG.c.body.like("拆解已落地%")
            )
        ).mappings().one()
        mentions = list(c.execute(
            select(models.MessageMention.__table__.c.member_id).where(
                models.MessageMention.__table__.c.message_id == landed_msg["id"]
            )
        ).scalars())
    assert mentions == [ids["human"]]  # impl_b 仍建议 human（去重后一条）


def test_scan_idempotent_reentry(migrated_engine: Engine) -> None:
    """对账 #4 幂等重入：落地完成后再扫 → already_done、零新产物、消息仍恰一条。"""
    ids = _seed(migrated_engine, decomp_mode="direct")
    _submit_proposal(migrated_engine, ids, _decompose_body(ids))
    bus, _ = _bus()
    first = landing_domain.pending_landing_scan(migrated_engine, bus)
    batch_id = first["created"][0]
    nodes_before = len(_channel_nodes(migrated_engine, ids["channel"]))
    version_before = _canvas_version(migrated_engine, ids["channel"])

    second = landing_domain.pending_landing_scan(migrated_engine, bus)
    assert second["created"] == [] and second["executed"] == {}  # done 批不再入选
    third = landing_domain.execute_batch(migrated_engine, bus, batch_id)
    assert third == "already_done"
    assert len(_channel_nodes(migrated_engine, ids["channel"])) == nodes_before
    assert _canvas_version(migrated_engine, ids["channel"]) == version_before
    assert _landed_msg_count(migrated_engine, ids["channel"]) == 1


# ---------------------------------------------------------------- A5 崩溃续段


def test_crash_mid_landing_resumes_without_duplicates(migrated_engine: Engine) -> None:
    """A5：执行 K 个 op 后 kill（模拟：edge 处理器抛异常）→ 重入 → 前段 hit 跳过、尾段补齐、
    任务无重复无缺失、「已落地」恰一条、landed 终态。"""
    ids = _seed(migrated_engine, decomp_mode="direct")
    pid = _submit_proposal(migrated_engine, ids, _decompose_body(ids))
    bus, _ = _bus()

    real_edge_handler = landing_domain._HANDLERS["create_edge"]

    def boom(tx: Any, ctx: Any, op: Any) -> dict[str, Any]:
        raise RuntimeError("simulated crash")

    landing_domain._HANDLERS["create_edge"] = boom
    try:
        with pytest.raises(RuntimeError, match="simulated crash"):
            landing_domain.pending_landing_scan(migrated_engine, bus)
    finally:
        landing_domain._HANDLERS["create_edge"] = real_edge_handler

    # 崩溃点（步进式）：impl_a/impl_b 两步（无入边节点）已提交；review 步 = 节点+两入边同一
    # 事务，边处理器炸 → **整步回滚**（review 节点也不留——「已落地节点入边集恒完整」不变量）。
    with migrated_engine.connect() as c:
        batch_id = c.execute(select(_BATCH.c.id)).scalar_one()
        node_ops = c.execute(
            select(func.count()).select_from(_LEDGER).where(
                _LEDGER.c.batch_id == batch_id, _LEDGER.c.kind == "create_node"
            )
        ).scalar_one()
    assert node_ops == 2
    assert len(_channel_nodes(migrated_engine, ids["channel"])) == 2
    assert _landed_msg_count(migrated_engine, ids["channel"]) == 0  # 批未完不发消息
    assert _proposal_row(migrated_engine, pid)["status"] == ProposalStatus.LANDING.value

    # 重入（对账 #4）：前段 3 节点 hit 跳过、尾段补齐、终态 landed。
    result = landing_domain.pending_landing_scan(migrated_engine, bus)
    assert result["executed"][batch_id] == "landed"
    nodes = _channel_nodes(migrated_engine, ids["channel"])
    assert len(nodes) == 5  # 3 agent + merge + summary，无重复
    with migrated_engine.connect() as c:
        task_count = c.execute(
            select(func.count()).select_from(_TASK).where(
                _TASK.c.channel_id == ids["channel"], _TASK.c.id != ids["task"]
            )
        ).scalar_one()
    assert task_count == 4  # 3 提案任务 + 1 汇总，无重复
    assert _landed_msg_count(migrated_engine, ids["channel"]) == 1
    assert _proposal_row(migrated_engine, pid)["status"] == ProposalStatus.LANDED.value
    # 前段命中留痕（步进：impl_a/impl_b 两步已提交）。
    assert _diag_count(migrated_engine, "landing.op_replayed") == 2


# ---------------------------------------------------------------- fail-closed：重放停批 + 持久性


def test_replay_mismatch_fail_closes_batch(migrated_engine: Engine) -> None:
    """§9.2 规则 2（执行器提交路径）：同键异指纹 → 停批 + 告警持久 + proposal→failed。"""
    ids = _seed(migrated_engine)
    pid = _submit_proposal(migrated_engine, ids, _decompose_body(ids))
    proposal = _proposal_row(migrated_engine, pid)
    with _tx(migrated_engine) as tx:
        batch, _ = draft_domain.confirm_apply(
            tx, proposal=proposal, adjustments=[],
            landed_hash=proposal["proposal_hash"], confirmed_by=ids["human"],
        )
    # 账本预置同键异指纹（外部改动模拟）：拓扑序第一个节点 op = impl_a。
    op_id = f"decomp:{batch.id}:node:impl_a"
    with migrated_engine.begin() as c:
        c.execute(
            insert(_LEDGER).values(
                op_id=op_id, request_hash="f" * 64, batch_id=batch.id,
                actor_member_id=None, kind="create_node", payload={},
                created_at=now_iso(),
            )
        )
    bus, events = _bus()
    assert landing_domain.execute_batch(migrated_engine, bus, batch.id) == "fail_closed"

    with migrated_engine.connect() as c:
        status = c.execute(select(_BATCH.c.status).where(_BATCH.c.id == batch.id)).scalar_one()
        card = c.execute(
            select(func.count()).select_from(_MSG).where(
                _MSG.c.card_ref == batch.id, _MSG.c.card_kind == "fail_closed"
            )
        ).scalar_one()
        activity = c.execute(
            select(func.count()).select_from(_ACTIVITY).where(
                _ACTIVITY.c.kind == "fail_closed", _ACTIVITY.c.channel_id == ids["channel"]
            )
        ).scalar_one()
    assert status == LandingBatchStatus.FAIL_CLOSED.value
    assert _diag_count(migrated_engine, "landing.fail_closed") == 1
    assert card == 1
    assert activity == 1  # 频道人类成员（human）恰一条（B §9.7 #2 随 M6 启用）
    assert _proposal_row(migrated_engine, pid)["status"] == ProposalStatus.FAILED.value
    assert _landed_msg_count(migrated_engine, ids["channel"]) == 0
    assert any(e.type is EventType.LANDING_FAIL_CLOSED for e in events)
    # 停批后不再被扫描领取（running 筛选）。
    rescan = landing_domain.pending_landing_scan(migrated_engine, bus)
    assert batch.id not in rescan["executed"]


def test_persist_fail_closed_update_path(migrated_engine: Engine) -> None:
    """persist_fail_closed（批行已提交存在 → UPDATE 路径）：独立连接落盘处置链全套。"""
    ids = _seed(migrated_engine)
    with migrated_engine.begin() as c:
        batch = service.create_batch(
            c, workspace_id=ids["ws"], channel_id=ids["channel"], kind="decomp",
            content_hash="a" * 64, source_ref="prop-x", confirmed_by=ids["human"],
        )
    service.persist_fail_closed(migrated_engine, batch, reason="unit update path")
    with migrated_engine.connect() as c:
        status = c.execute(select(_BATCH.c.status).where(_BATCH.c.id == batch.id)).scalar_one()
        card = c.execute(
            select(func.count()).select_from(_MSG).where(_MSG.c.card_ref == batch.id)
        ).scalar_one()
        activity = c.execute(
            select(func.count()).select_from(_ACTIVITY).where(
                _ACTIVITY.c.kind == "fail_closed", _ACTIVITY.c.channel_id == ids["channel"]
            )
        ).scalar_one()
    assert status == LandingBatchStatus.FAIL_CLOSED.value
    assert card == 1 and activity == 1
    assert _diag_count(migrated_engine, "landing.fail_closed") == 1


def test_tmpl_node_mismatch_fail_closed_persists_after_rollback(
    server_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M5 挂账确定性测试（B §12.5 #4）：tmpl 实例化命中账本同键异指纹 → ApiError 409 外层回滚后，
    批次行 status='fail_closed'、诊断行、告警卡、activity **均持久存在**（独立连接落盘；批行本身
    随回滚消失 → persist 以 INSERT 重建为 fail_closed），且回滚有效（零任务落库）。"""
    engine: Engine = server_client.app.state.engine  # type: ignore[attr-defined]
    # 频道（自动建画布）+ owner 入频道（activity 接收者）。
    owner = next(
        m for m in server_client.get("/api/members").json()
        if m["kind"] == "human" and m["role"] == "owner"
    )
    channel = server_client.post(
        "/api/channels", json={"name": "tmpl-fc", "member_ids": [owner["id"]]}
    ).json()["id"]
    tri = next(t for t in server_client.get("/api/templates").json() if t["builtin"])
    mapping = {ro["placeholder"]: owner["id"] for ro in tri["body"]["roles"]}
    first_key = tri["body"]["nodes"][0]["key"]

    # 钉死本次实例化的 batch_id（路由第一次 new_ulid 调用），预置同键异指纹账本行（batch_id NULL
    # ——批行属未提交事务，账本行不带 FK 即可命中 lookup mismatch）。
    forced_batch = new_ulid()
    with engine.begin() as c:
        c.execute(
            insert(_LEDGER).values(
                op_id=f"tmpl:{forced_batch}:{first_key}", request_hash="f" * 64,
                batch_id=None, actor_member_id=None, kind="create_node", payload={},
                created_at=now_iso(),
            )
        )
    real_new_ulid = service.new_ulid
    state = {"first": True}

    def fixed_first(*args: Any, **kwargs: Any) -> str:
        if state["first"]:
            state["first"] = False
            return forced_batch
        return real_new_ulid()

    monkeypatch.setattr("coagentia_server.ledger.service.new_ulid", fixed_first)
    resp = server_client.post(
        f"/api/templates/{tri['id']}/instantiate",
        json={"channel_id": channel, "role_mapping": mapping},
    )
    monkeypatch.undo()
    assert resp.status_code == 409, resp.text
    err = rest.ErrorResponse.model_validate(resp.json())
    assert err.error.code is rest.ErrorCode.IDEMPOTENCY_MISMATCH

    with engine.connect() as c:
        # 批次行以 fail_closed 持久（外层回滚已撤销 create_batch → persist INSERT 重建）。
        batch_status = c.execute(
            select(_BATCH.c.status).where(_BATCH.c.id == forced_batch)
        ).scalar_one()
        diag = c.execute(
            select(func.count()).select_from(_DIAG).where(
                _DIAG.c.type == "landing.fail_closed", _DIAG.c.batch_id == forced_batch
            )
        ).scalar_one()
        card = c.execute(
            select(func.count()).select_from(_MSG).where(
                _MSG.c.card_ref == forced_batch, _MSG.c.card_kind == "fail_closed"
            )
        ).scalar_one()
        activity = c.execute(
            select(func.count()).select_from(_ACTIVITY).where(
                _ACTIVITY.c.kind == "fail_closed", _ACTIVITY.c.channel_id == channel
            )
        ).scalar_one()
        tasks = c.execute(
            select(func.count()).select_from(_TASK).where(_TASK.c.channel_id == channel)
        ).scalar_one()
    assert batch_status == LandingBatchStatus.FAIL_CLOSED.value
    assert diag == 1 and card == 1 and activity == 1
    assert tasks == 0  # 回滚有效：半落地任务不存在


# ---------------------------------------------------------------- HTTP 层：confirm CAS / reject


AGENT_KEY = "cak_landing_test"


def _http_orchestrator(client: TestClient, channel_id: str) -> dict[str, Any]:
    comp = client.get("/api/computers").json()[0]
    resp = client.post("/api/agents", json={
        "computer_id": comp["id"], "name": "OrchLand", "runtime": "claude_code",
        "model": "m", "role_template_key": "orchestrator",
    })
    assert resp.status_code == 201, resp.text
    agent = resp.json()
    client.post(f"/api/channels/{channel_id}/members", json={"member_id": agent["member_id"]})
    digest = hashlib.sha256(AGENT_KEY.encode()).hexdigest()
    engine: Engine = client.app.state.engine  # type: ignore[attr-defined]
    with engine.begin() as c:
        c.execute(
            update(models.Computer.__table__)
            .where(models.Computer.__table__.c.id == comp["id"])
            .values(api_key_hash=digest)
        )
    headers = {"Authorization": f"Bearer {AGENT_KEY}", "X-Acting-Member": agent["member_id"]}
    return {"member_id": agent["member_id"], "headers": headers}


class _SpyHub:
    def agent_daemon_online(self, agent_member_id: str) -> bool:
        return True

    def inject_orchestrator(self, *a: Any, **k: Any) -> str:
        return "done"


def _awaiting_via_http(client: TestClient, name: str) -> dict[str, Any]:
    """decompose(text) → orch 发有效 <control>（single_task）→ awaiting_confirm；
    返回 {channel, proposal_id, source_task_id, expected(=CAS 三字段), orch}。"""
    engine: Engine = client.app.state.engine  # type: ignore[attr-defined]
    channel = client.post("/api/channels", json={"name": name, "member_ids": []}).json()["id"]
    orch = _http_orchestrator(client, channel)
    client.app.state.daemon_hub = _SpyHub()  # type: ignore[attr-defined]
    proposal = client.post(
        f"/api/channels/{channel}/decompose", json={"text": "做个登录功能"}
    ).json()
    with engine.connect() as c:
        root_msg = c.execute(
            select(_TASK.c.root_message_id).where(_TASK.c.id == proposal["source_task_id"])
        ).scalar_one()
    with engine.begin() as c:
        c.execute(
            insert(models.ReadPosition.__table__).values(
                member_id=orch["member_id"], channel_id=channel,
                last_read_message_id=root_msg, last_read_at=now_iso(),
            )
        )
    body = _single_task_body(proposal["source_task_id"])
    r = client.post(
        f"/api/channels/{channel}/messages",
        json={"body": _control_msg(body), "thread_root_id": root_msg},
        headers=orch["headers"],
    )
    assert r.status_code == 201, r.text
    fresh = client.get(f"/api/proposals/{proposal['id']}").json()
    assert fresh["status"] == "awaiting_confirm"
    canvas = client.get(f"/api/channels/{channel}/canvas").json()["canvas"]
    return {
        "channel": channel,
        "proposal_id": proposal["id"],
        "source_task_id": proposal["source_task_id"],
        "root_msg": root_msg,
        "orch": orch,
        "expected": {
            "proposal_hash": fresh["proposal_hash"],
            "baseline_version": canvas["baseline_version"],
            "baseline_hash": canvas["baseline_hash"],
        },
    }


def _wait(cond: Any, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_confirm_cas_stale_paths_http(server_client: TestClient) -> None:
    """CAS 逐路径：404 / Agent 403 / expected 三字段各自不符 409 携最新态 / removed_ops 422 /
    调整违形 422 / 调整重验红例 422 携 V 系错误清单。"""
    ctx = _awaiting_via_http(server_client, "cas-paths")
    url = f"/api/proposals/{ctx['proposal_id']}/confirm"
    good = ctx["expected"]

    # 404
    r = server_client.post(
        f"/api/proposals/{new_ulid()}/confirm", json={"expected": good}
    )
    assert r.status_code == 404

    # Agent 403（O9）
    r = server_client.post(url, json={"expected": good}, headers=ctx["orch"]["headers"])
    assert r.status_code == 403, r.text
    assert rest.ErrorResponse.model_validate(r.json()).error.rule == "O9"

    # expected 三字段各自不符 → 409 STALE_CONFIRM 携最新态形状。
    for field, bogus in (
        ("proposal_hash", "e" * 64),
        ("baseline_version", 999),
        ("baseline_hash", "e" * 64),
    ):
        stale = {**good, field: bogus}
        r = server_client.post(url, json={"expected": stale})
        assert r.status_code == 409, (field, r.text)
        payload = r.json()
        assert payload["error"]["code"] == "STALE_CONFIRM"
        latest = payload["latest"]
        assert latest["proposal"]["id"] == ctx["proposal_id"]
        assert latest["baseline_version"] == good["baseline_version"]
        assert latest["baseline_hash"] == good["baseline_hash"]

    # removed_ops 对 full 提案 → 422。
    r = server_client.post(url, json={"expected": good, "removed_ops": [0]})
    assert r.status_code == 422

    # 调整违形（悬空 remove_node）→ 422。
    r = server_client.post(
        url, json={"expected": good, "adjustments": [{"op": "remove_node", "temp_id": "NX"}]}
    )
    assert r.status_code == 422

    # 调整重验红例：single_task 加第二节点 + 自环边 → V 系错误清单（NODE_COUNT/EDGE_SELF）。
    bad_adjust = [
        {"op": "add_node", "node": {"temp_id": "N2", "title": "多余", "kind": "agent",
                                    "task_plan": _plan("多余")}},
        {"op": "add_edge", "from": "N2", "to": "N2"},
    ]
    r = server_client.post(url, json={"expected": good, "adjustments": bad_adjust})
    assert r.status_code == 422, r.text
    err = rest.ErrorResponse.model_validate(r.json())
    codes = {e["code"] for e in err.error.details["errors"]}  # type: ignore[index]
    assert "NODE_COUNT" in codes and "EDGE_SELF" in codes

    # 全部失败路径后提案仍 awaiting_confirm（无副作用泄漏）。
    assert server_client.get(
        f"/api/proposals/{ctx['proposal_id']}"
    ).json()["status"] == "awaiting_confirm"


def test_confirm_success_202_and_background_landing(server_client: TestClient) -> None:
    """确认成功：202 {batch, proposal(landing)} → 真 hub 后台执行器落地 → landed +
    画布节点 + 已落地消息恰一条（B §5「202 + landing.completed 收尾」全链）。"""
    ctx = _awaiting_via_http(server_client, "cas-success")
    engine: Engine = server_client.app.state.engine  # type: ignore[attr-defined]
    r = server_client.post(
        f"/api/proposals/{ctx['proposal_id']}/confirm", json={"expected": ctx["expected"]}
    )
    assert r.status_code == 202, r.text
    result = rest.ProposalConfirmResult.model_validate(r.json())
    assert result.proposal.status is ProposalStatus.LANDING
    assert result.batch.kind == "decomp"
    assert result.batch.source_ref == ctx["proposal_id"]
    assert result.batch.content_hash == ctx["expected"]["proposal_hash"]  # 无调整
    owner = next(
        m for m in server_client.get("/api/members").json()
        if m["kind"] == "human" and m["role"] == "owner"
    )
    assert result.batch.confirmed_by == owner["id"]

    # 真 hub（bus 触发）异步落地 → landed；画布 1 节点；已落地消息恰一条。
    assert _wait(
        lambda: server_client.get(f"/api/proposals/{ctx['proposal_id']}").json()["status"]
        == "landed"
    ), "后台执行器未在时限内落地"
    canvas = server_client.get(f"/api/channels/{ctx['channel']}/canvas").json()
    assert len(canvas["nodes"]) == 1
    assert _landed_msg_count(engine, ctx["channel"]) == 1
    # 重复 confirm（已 landing/landed）→ 409 STALE_CONFIRM 携最新态（客户端由 latest 见结局）。
    r2 = server_client.post(
        f"/api/proposals/{ctx['proposal_id']}/confirm", json={"expected": ctx["expected"]}
    )
    assert r2.status_code == 409
    assert r2.json()["latest"]["proposal"]["status"] == "landed"


def test_confirm_delta_shared_endpoint(server_client: TestClient) -> None:
    """J10：delta 提案走 confirm/reject 同两端点（不再 422 占位）——空 operations 确认（全剔除）→
    422、reject → 200 DELTA_REJECTED（Agent 403 门与 full 同）。"""
    engine: Engine = server_client.app.state.engine  # type: ignore[attr-defined]
    channel = server_client.post(
        "/api/channels", json={"name": "delta-gate", "member_ids": []}
    ).json()["id"]
    task = server_client.post(
        f"/api/channels/{channel}/messages", json={"body": "x", "as_task": {"title": "x"}}
    ).json()["task"]
    canvas = server_client.get(f"/api/channels/{channel}/canvas").json()["canvas"]
    pid = new_ulid()
    with engine.begin() as c:
        ws_id = c.execute(select(models.Workspace.__table__.c.id)).scalar_one()
        owner_id = c.execute(
            select(models.Member.__table__.c.id).where(
                models.Member.__table__.c.kind == "human"
            ).limit(1)
        ).scalar_one()
        c.execute(
            insert(_PROPOSAL).values(
                id=pid, workspace_id=ws_id, channel_id=channel, source_task_id=task["id"],
                kind="delta", revision=1, status="awaiting_confirm", body={},
                proposal_hash="a" * 64, base_hash=canvas["baseline_hash"], landed_hash=None,
                adjustments=[], repair_count=0, proposed_by_member_id=owner_id,
                created_at=now_iso(), updated_at=now_iso(),
            )
        )
    expected = {
        "proposal_hash": "a" * 64,
        "baseline_version": canvas["baseline_version"],
        "baseline_hash": canvas["baseline_hash"],
    }
    # 空 operations → 全部剔除 → 422（不再是形态占位 422）。
    r = server_client.post(f"/api/proposals/{pid}/confirm", json={"expected": expected})
    assert r.status_code == 422, r.text
    # reject 走同端点 → 200 rejected（DELTA_REJECTED）。
    r = server_client.post(f"/api/proposals/{pid}/reject", json={"reason": "不需要"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "rejected"
    assert _diag_count(engine, "delta.rejected") == 1


def test_reject_paths_http(server_client: TestClient) -> None:
    """reject 逐路径：Agent 403 / 成功（理由进 source 线程 + rejected 终态 + 诊断）/
    非 awaiting 重复拒绝 409 携最新态 / 拒绝后 confirm 也 409。"""
    ctx = _awaiting_via_http(server_client, "reject-paths")
    engine: Engine = server_client.app.state.engine  # type: ignore[attr-defined]
    url = f"/api/proposals/{ctx['proposal_id']}/reject"

    r = server_client.post(url, json={"reason": "x"}, headers=ctx["orch"]["headers"])
    assert r.status_code == 403  # Agent 403（O9 同门）

    r = server_client.post(url, json={"reason": "拆得太碎，重新想"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "rejected"
    with engine.connect() as c:
        thread_msgs = c.execute(
            select(_MSG.c.body).where(
                _MSG.c.thread_root_id == ctx["root_msg"], _MSG.c.kind == "system"
            )
        ).scalars().all()
    assert any("拆得太碎，重新想" in b for b in thread_msgs)  # 理由进 source 线程
    assert _diag_count(engine, "draft.rejected") >= 1

    # 重复拒绝 → 409 STALE_CONFIRM 携最新态（rejected）。
    r2 = server_client.post(url, json={})
    assert r2.status_code == 409
    assert r2.json()["latest"]["proposal"]["status"] == "rejected"
    # 拒绝后 confirm → 409。
    r3 = server_client.post(
        f"/api/proposals/{ctx['proposal_id']}/confirm", json={"expected": ctx["expected"]}
    )
    assert r3.status_code == 409


def test_reject_without_reason_still_leaves_trace(migrated_engine: Engine) -> None:
    """无 reason 也发一条拒绝留痕（单元路径）。"""
    ids = _seed(migrated_engine)
    pid = _submit_proposal(migrated_engine, ids, _single_task_body(ids["task"]))
    proposal = _proposal_row(migrated_engine, pid)
    with _tx(migrated_engine) as tx:
        rejected = draft_domain.reject_proposal(tx, proposal=proposal, reason=None)
        etypes = [e for e, _, _ in tx.events]
    assert rejected["status"] == ProposalStatus.REJECTED.value
    assert EventType.DRAFT_REJECTED in etypes
    with migrated_engine.connect() as c:
        trace = c.execute(
            select(func.count()).select_from(_MSG).where(
                _MSG.c.thread_root_id == ids["root_msg"],
                _MSG.c.body.like("拆解提案%已被拒绝%"),
            )
        ).scalar_one()
    assert trace == 1


# ---------------------------------------------------------------- 硬关口重写回归（Fable）


def test_step_atomicity_no_bare_system_node(migrated_engine: Engine) -> None:
    """B1（步进式落地）：提案声明的 system 节点与其入边同一步原子提交——崩溃于该步时系统节点
    **不落地**（旧 flat 设计会留下无入边裸 check 节点，被系统节点扫描判空成功 → success 终态
    不可 retry、下游 gating 永久错误解锁，即 M6A-EVIDENCE 预警窗口在增量落地路径的实体化）。
    恢复后节点与其入边同批出现。"""
    ids = _seed(migrated_engine, decomp_mode="direct")
    body = {
        "version": "coagentia.decomposition.v1",
        "source": ids["task"],
        "mode": "decompose",
        "summary": "实现 + 显式 check 门",
        "nodes": [
            {"temp_id": "impl_a", "title": "实现 A", "kind": "agent",
             "task_plan": _plan("实现 A"), "writes_code": True, "project": ids["project"],
             "suggested_owner": ids["human"]},
            {"temp_id": "impl_b", "title": "实现 B", "kind": "agent",
             "task_plan": _plan("实现 B"), "writes_code": True, "project": ids["project"]},
            {"temp_id": "gate", "title": "校验门", "kind": "system",
             "system_action": "check", "command": "pytest -q"},
        ],
        "edges": [
            {"from": "impl_a", "to": "gate"},
            {"from": "impl_b", "to": "gate"},
        ],
        "merge_plan": "合并后跑校验",
    }
    _submit_proposal(migrated_engine, ids, body)
    bus, _ = _bus()

    real = landing_domain._HANDLERS["create_node"]

    def boom_on_system(tx: Any, ctx: Any, op: Any) -> dict[str, Any]:
        if op.spec["node"].get("kind") == "system":
            raise RuntimeError("simulated crash at system node step")
        return real(tx, ctx, op)

    landing_domain._HANDLERS["create_node"] = boom_on_system
    try:
        with pytest.raises(RuntimeError, match="system node step"):
            landing_domain.pending_landing_scan(migrated_engine, bus)
    finally:
        landing_domain._HANDLERS["create_node"] = real

    # 崩溃前缀：两实现节点已落地；system 节点及其入边整步回滚——画布上不存在无入边裸节点。
    nodes = _channel_nodes(migrated_engine, ids["channel"])
    assert len(nodes) == 2
    assert all(n["kind"] != "system" for n in nodes)
    assert _channel_edges(migrated_engine, ids["channel"]) == []

    # 恢复：check 节点与其两条入边同批出现；auto merge（含 code 且无显式 merge）也追加。
    landing_domain.pending_landing_scan(migrated_engine, bus)
    nodes = _channel_nodes(migrated_engine, ids["channel"])
    system_nodes = [n for n in nodes if n["kind"] == "system"]
    assert {n["system_action"] for n in system_nodes} == {"check", "merge"}
    check_node = next(n for n in system_nodes if n["system_action"] == "check")
    edges = _channel_edges(migrated_engine, ids["channel"])
    incoming = [e for e in edges if e[1] == check_node["id"]]
    assert len(incoming) == 2


def test_confirm_conditional_transition_blocks_double_confirm(migrated_engine: Engine) -> None:
    """B2（条件转移）：并发对手已把提案推进（模拟 DB 后置写 landing），持过期行的 confirm_apply
    必须 StaleTransition 竞败、不建第二个落地批——旧「读态检查 + 无条件 UPDATE」在 pysqlite
    自动提交读下双确认会双双成功 → 双批双落地。reject 同一防线。"""
    ids = _seed(migrated_engine, decomp_mode="draft")
    pid = _submit_proposal(migrated_engine, ids, _decompose_body(ids))
    stale_row = _proposal_row(migrated_engine, pid)
    assert stale_row["status"] == ProposalStatus.AWAITING_CONFIRM.value

    with migrated_engine.begin() as c:
        c.execute(
            update(_PROPOSAL).where(_PROPOSAL.c.id == pid)
            .values(status=ProposalStatus.LANDING.value)
        )

    with _tx(migrated_engine) as tx:
        with pytest.raises(draft_domain.StaleTransition):
            draft_domain.confirm_apply(
                tx, proposal=stale_row, adjustments=[],
                landed_hash=stale_row["proposal_hash"], confirmed_by=ids["human"],
            )
    with migrated_engine.connect() as c:
        batches = c.execute(select(func.count()).select_from(_BATCH)).scalar_one()
    assert batches == 0  # 竞败方零副作用（真实场景对手的批由对手事务持有）

    with _tx(migrated_engine) as tx:
        with pytest.raises(draft_domain.StaleTransition):
            draft_domain.reject_proposal(tx, proposal=stale_row, reason="并发拒绝")


# ---------------------------------------------------------------- K7：_post_landed_message 批查护栏


def test_post_landed_message_query_count_constant(migrated_engine: Engine) -> None:
    """K7 site 1：「已落地」消息构造的节点任务/建议人查询批量化——查询条数不随节点数增长。

    直调 `_post_landed_message`（N=1 与 N=4），比较其对 tasks/members 表的 SELECT 条数：批取后
    建议人名恒 1 条 IN、节点任务恒 1 条 IN（+ source 根消息 1 条），O(1) 非旧逐节点 fetch_task/
    _member_name 的 O(n)。
    """
    from types import SimpleNamespace

    env = Env(migrated_engine)
    channel = env.add_channel(name="land")
    root_msg = env.add_message(channel, author=env.owner_id, body="src")
    source_task = new_ulid()
    with migrated_engine.begin() as c:
        c.execute(insert(_TASK).values(
            id=source_task, workspace_id=env.ws_id, channel_id=channel, number=1,
            root_message_id=root_msg, title="src", status="todo", level="l1",
            created_by_member_id=env.owner_id, status_changed_at=now_iso(), created_at=now_iso(),
        ))

    def _scenario(n: int) -> tuple[dict[str, Any], Any]:
        nodes: list[dict[str, Any]] = []
        by_temp: dict[str, dict[str, Any]] = {}
        for i in range(n):
            owner = env.add_agent(f"A{i}-{n}", "idle")
            tid = new_ulid()
            node_root = env.add_message(channel, author=env.owner_id, body=f"node{i}-{n}")
            with migrated_engine.begin() as c:
                c.execute(insert(_TASK).values(
                    id=tid, workspace_id=env.ws_id, channel_id=channel, number=100 + n * 10 + i,
                    root_message_id=node_root, title=f"node{i}", status="todo", level="l2",
                    created_by_member_id=env.owner_id, status_changed_at=now_iso(),
                    created_at=now_iso(),
                ))
            temp = f"T{i}"
            nodes.append(
                {"temp_id": temp, "kind": "agent", "title": f"node{i}", "suggested_owner": owner}
            )
            by_temp[temp] = {"temp_id": temp, "task_id": tid}
        ctx = landing_domain._ExecContext(
            workspace_id=env.ws_id, channel_id=channel, canvas={"id": "c"},
            proposed_by=env.owner_id, source_task_id=source_task,
        )
        ctx.by_temp = by_temp
        return {"nodes": nodes, "edges": [], "mode": "decompose"}, ctx

    batch = SimpleNamespace(id="B" * 26, workspace_id=env.ws_id, channel_id=channel)
    proposal = {"revision": 1, "source_task_id": source_task}

    def _measure(n: int) -> tuple[int, int]:
        landed, ctx = _scenario(n)
        with count_queries(migrated_engine) as q:
            with _tx(migrated_engine) as tx:
                landing_domain._post_landed_message(tx, ctx, batch, proposal, landed)
        sel = [s for s in q.dml if s.upper().startswith("SELECT")]
        member_sel = [s for s in sel if " members" in s.lower()]
        task_sel = [s for s in sel if " tasks" in s.lower()]
        return len(member_sel), len(task_sel)

    m1, t1 = _measure(1)
    m4, t4 = _measure(4)
    assert m1 == m4 == 1, (m1, m4)  # 建议人名批查恰 1 条（旧码 = n 条 _member_name）
    assert t1 == t4 == 2, (t1, t4)  # 节点任务批查(1) + source 根消息查(1)（旧码 = n+1 条）
