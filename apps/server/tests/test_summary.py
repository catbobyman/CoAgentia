"""O8 汇总执行域（M8b L8，汇总设计 §3/§4/§6）：有界摘要 + 协调循环护栏。

- 纯逻辑：collect_summary_inputs（覆盖/未覆盖）、render（有界截断）、fingerprint（随状态变）。
- CAS：ensure（lazy 幂等）、advance_progress（fp 变才计轮 + 幂等 + round 触顶）、note_wakeup
  （fp 未变 stall++ + stall 触顶）、consume_replan（预算 CAS）、recover（归零留 replan）、
  add_repeat_stall（触顶）。
- 服务级：blocked_at 抑制投递 gating、force-start / 人类发言恢复、replan 第 2 次 403 rule=O8。
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

from coagentia_contracts.enums import ContractKind
from coagentia_server.app import create_app
from coagentia_server.canvas import service as canvas_service
from coagentia_server.contracts import service as contracts_service
from coagentia_server.db import models
from coagentia_server.ledger.service import new_ulid, now_iso
from coagentia_server.orchestration import summary as summary_domain
from daemon_helpers import Env
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine


class _Tx:
    def __init__(self, conn: Any) -> None:
        self.conn = conn
        self.events: list[Any] = []

    def emit(self, etype: Any, channel_id: str | None, data: dict[str, Any]) -> None:
        self.events.append((etype, channel_id, data))


@contextlib.contextmanager
def _tx(engine: Engine) -> Any:
    conn = engine.connect()
    trans = conn.begin()
    try:
        yield _Tx(conn)
        trans.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- 场景构造


def _build(engine: Engine, *, summary_policy: str = "partial") -> dict[str, str]:
    """频道 + 画布 + 上游 agent 任务/节点 + 汇总 agent 任务/节点（owner=orch）+ 边（上游→汇总）。"""
    env = Env(engine)
    ch = env.add_channel(name="sum")
    orch = env.add_agent("Orch", "idle")
    env.join(ch, orch)
    env.join(ch, env.owner_id)
    canvas_id = new_ulid()
    up_anchor = env.add_message(ch, author=None, kind="system", body="up")
    sum_anchor = env.add_message(ch, author=None, kind="system", body="sum")
    up_task, sum_task = new_ulid(), new_ulid()
    up_node, sum_node = new_ulid(), new_ulid()
    with engine.begin() as c:
        c.execute(
            insert(models.Canvas.__table__).values(
                id=canvas_id, workspace_id=env.ws_id, channel_id=ch,
                baseline_version=0, baseline_hash="base", updated_at=now_iso(),
            )
        )
        for tid, anchor, num in ((up_task, up_anchor, 1), (sum_task, sum_anchor, 2)):
            c.execute(
                insert(models.Task.__table__).values(
                    id=tid, workspace_id=env.ws_id, channel_id=ch, number=num,
                    root_message_id=anchor, title=f"T{num}", status="todo",
                    owner_member_id=orch if tid == sum_task else None, level="l2",
                    created_by_member_id=env.owner_id, status_changed_at=now_iso(),
                    created_at=now_iso(),
                )
            )
        c.execute(
            insert(models.CanvasNode.__table__).values(
                id=up_node, canvas_id=canvas_id, kind="agent", task_id=up_task,
                is_summary=False, pos_x=0, pos_y=0, created_at=now_iso(),
            )
        )
        c.execute(
            insert(models.CanvasNode.__table__).values(
                id=sum_node, canvas_id=canvas_id, kind="agent", task_id=sum_task,
                is_summary=True, upstream_policy=summary_policy, pos_x=0, pos_y=0,
                created_at=now_iso(),
            )
        )
        c.execute(
            insert(models.CanvasEdge.__table__).values(
                id=new_ulid(), canvas_id=canvas_id, from_node_id=up_node, to_node_id=sum_node,
            )
        )
    return {
        "ws": env.ws_id, "channel": ch, "orch": orch, "canvas": canvas_id,
        "up_task": up_task, "sum_task": sum_task, "up_node": up_node, "sum_node": sum_node,
        "sum_root": sum_anchor,
    }


def _set_status(engine: Engine, task_id: str, status: str) -> None:
    with engine.begin() as c:
        c.execute(
            update(models.Task.__table__)
            .where(models.Task.__table__.c.id == task_id)
            .values(status=status)
        )


def _run(engine: Engine, task_id: str) -> dict[str, Any] | None:
    with engine.connect() as c:
        return summary_domain.get_run(c, task_id)


# -------------------------------------------------------------- CAS：advance / note_wakeup


def test_ensure_run_lazy_idempotent(migrated_engine: Engine) -> None:
    ids = _build(migrated_engine)
    with _tx(migrated_engine) as tx:
        r1 = summary_domain.ensure_run(
            tx, task_id=ids["sum_task"], canvas_id=ids["canvas"], workspace_id=ids["ws"]
        )
        assert r1["round_count"] == 0 and r1["blocked_at"] is None
    with _tx(migrated_engine) as tx:
        r2 = summary_domain.ensure_run(
            tx, task_id=ids["sum_task"], canvas_id=ids["canvas"], workspace_id=ids["ws"]
        )
        assert r2["task_id"] == ids["sum_task"]  # 二次不重建，原样返回


def test_advance_progress_counts_on_change_idempotent_on_same(migrated_engine: Engine) -> None:
    ids = _build(migrated_engine)
    with _tx(migrated_engine) as tx:
        summary_domain.ensure_run(
            tx, task_id=ids["sum_task"], canvas_id=ids["canvas"], workspace_id=ids["ws"]
        )
        a1 = summary_domain.advance_progress(tx, task_id=ids["sum_task"], new_fp="fp_A")
        assert a1["counted"] and a1["round_count"] == 1 and a1["stall_count"] == 0
        # 同 fp 重复扫描 → 幂等，不重计（防重复 bus 扫描刷屏/双计）
        a2 = summary_domain.advance_progress(tx, task_id=ids["sum_task"], new_fp="fp_A")
        assert not a2["counted"] and a2["round_count"] == 1
        # fp 变化 → 进展轮，round++ stall 归零
        a3 = summary_domain.advance_progress(tx, task_id=ids["sum_task"], new_fp="fp_B")
        assert a3["counted"] and a3["round_count"] == 2


def test_advance_progress_round_topout_blocks(migrated_engine: Engine) -> None:
    ids = _build(migrated_engine)
    with _tx(migrated_engine) as tx:
        summary_domain.ensure_run(
            tx, task_id=ids["sum_task"], canvas_id=ids["canvas"], workspace_id=ids["ws"]
        )
        last = None
        for i in range(summary_domain.MAX_ROUNDS):
            last = summary_domain.advance_progress(tx, task_id=ids["sum_task"], new_fp=f"fp_{i}")
        assert last is not None and last["round_count"] == summary_domain.MAX_ROUNDS
        assert last["just_blocked"] and last["blocked_at"] is not None


def test_note_wakeup_stall_on_unchanged_fp_and_topout(migrated_engine: Engine) -> None:
    ids = _build(migrated_engine)
    with _tx(migrated_engine) as tx:
        summary_domain.ensure_run(
            tx, task_id=ids["sum_task"], canvas_id=ids["canvas"], workspace_id=ids["ws"]
        )
        # 首次唤醒推进 last_fp
        w1 = summary_domain.note_wakeup(tx, task_id=ids["sum_task"], new_fp="fp")
        assert w1["round_count"] == 1 and w1["stall_count"] == 0
        # 同 fp 反复唤醒（空转）→ stall 累加，≥3 阻断
        w2 = summary_domain.note_wakeup(tx, task_id=ids["sum_task"], new_fp="fp")
        w3 = summary_domain.note_wakeup(tx, task_id=ids["sum_task"], new_fp="fp")
        assert w2["stall_count"] == 1 and w3["stall_count"] == 2
        w4 = summary_domain.note_wakeup(tx, task_id=ids["sum_task"], new_fp="fp")
        assert w4["stall_count"] == summary_domain.MAX_STALL and w4["just_blocked"]


def test_note_wakeup_stall_resets_on_fp_change(migrated_engine: Engine) -> None:
    ids = _build(migrated_engine)
    with _tx(migrated_engine) as tx:
        summary_domain.ensure_run(
            tx, task_id=ids["sum_task"], canvas_id=ids["canvas"], workspace_id=ids["ws"]
        )
        summary_domain.note_wakeup(tx, task_id=ids["sum_task"], new_fp="a")
        summary_domain.note_wakeup(tx, task_id=ids["sum_task"], new_fp="a")  # stall=1
        changed = summary_domain.note_wakeup(tx, task_id=ids["sum_task"], new_fp="b")
        assert changed["stall_count"] == 0  # fp 变化 → stall 归零（进展）


def test_add_repeat_stall_topout(migrated_engine: Engine) -> None:
    ids = _build(migrated_engine)
    with _tx(migrated_engine) as tx:
        summary_domain.ensure_run(
            tx, task_id=ids["sum_task"], canvas_id=ids["canvas"], workspace_id=ids["ws"]
        )
        r1 = summary_domain.add_repeat_stall(tx, task_id=ids["sum_task"])
        r2 = summary_domain.add_repeat_stall(tx, task_id=ids["sum_task"])
        r3 = summary_domain.add_repeat_stall(tx, task_id=ids["sum_task"])
        assert r1 is not None and r1["stall_count"] == 1
        assert r2 is not None and r2["stall_count"] == 2
        assert r3 is not None and r3["stall_count"] == 3 and r3["just_blocked"]
        # 无行（非汇总期）→ None 无副作用
        assert summary_domain.add_repeat_stall(tx, task_id=ids["up_task"]) is None


# ---------------------------------------------------------------- CAS：replan / recover


def test_consume_replan_cas_budget(migrated_engine: Engine) -> None:
    ids = _build(migrated_engine)
    with _tx(migrated_engine) as tx:
        summary_domain.ensure_run(
            tx, task_id=ids["sum_task"], canvas_id=ids["canvas"], workspace_id=ids["ws"]
        )
        assert summary_domain.consume_replan(tx, task_id=ids["sum_task"]) is True  # 预算 1 → ok
        assert summary_domain.consume_replan(tx, task_id=ids["sum_task"]) is False  # 超额
        assert summary_domain.replan_exhausted(tx.conn, ids["sum_task"]) is True


def test_recover_resets_counts_keeps_replan(migrated_engine: Engine) -> None:
    ids = _build(migrated_engine)
    with _tx(migrated_engine) as tx:
        summary_domain.ensure_run(
            tx, task_id=ids["sum_task"], canvas_id=ids["canvas"], workspace_id=ids["ws"]
        )
        summary_domain.consume_replan(tx, task_id=ids["sum_task"])
        for _ in range(4):  # 首次推进 last_fp（stall=0），其后三次同 fp → stall 达 3 阻断
            summary_domain.note_wakeup(tx, task_id=ids["sum_task"], new_fp="x")
        blocked = summary_domain.get_run(tx.conn, ids["sum_task"])
        assert blocked is not None and blocked["blocked_at"] is not None
        assert summary_domain.recover(tx, task_id=ids["sum_task"]) is True
        after = summary_domain.get_run(tx.conn, ids["sum_task"])
        assert after is not None
        assert after["round_count"] == 0 and after["stall_count"] == 0
        assert after["blocked_at"] is None and after["last_fingerprint"] is None
        assert after["replan_used"] == 1  # 裁决 #8：恢复不重置 replan
        # 无行任务恢复 → False 无副作用
        assert summary_domain.recover(tx, task_id=ids["up_task"]) is False


def test_active_summary_task_gated_by_run_and_terminal(migrated_engine: Engine) -> None:
    ids = _build(migrated_engine)
    with migrated_engine.connect() as c:
        assert summary_domain.active_summary_task(c, ids["canvas"]) is None  # 未建行
    with _tx(migrated_engine) as tx:
        summary_domain.ensure_run(
            tx, task_id=ids["sum_task"], canvas_id=ids["canvas"], workspace_id=ids["ws"]
        )
    with migrated_engine.connect() as c:
        assert summary_domain.active_summary_task(c, ids["canvas"]) == ids["sum_task"]
    _set_status(migrated_engine, ids["sum_task"], "done")  # F8 终态失效
    with migrated_engine.connect() as c:
        assert summary_domain.active_summary_task(c, ids["canvas"]) is None


# ------------------------------------------------------------ 纯逻辑：collect/render/fingerprint


def test_collect_summary_inputs_covered_uncovered(migrated_engine: Engine) -> None:
    ids = _build(migrated_engine)
    # 上游 closed（终态非 done）→ 未覆盖；带 TaskHandoff 交付物/证据
    _set_status(migrated_engine, ids["up_task"], "closed")
    with _tx(migrated_engine) as tx:
        contracts_service.submit_contract(
            tx, task_id=ids["up_task"], workspace_id=ids["ws"],
            kind=ContractKind.TASK_HANDOFF,
            body_dict={
                "version": "coagentia.task-handoff.v1",
                "from_member": ids["orch"], "to_member": ids["orch"],
                "deliverables": [{"path": "/a.py", "kind": "file"}],
                "evidence": [{"type": "test", "ref": "pytest", "conclusion": "全绿"}],
                "open_risks": ["边角未覆盖"], "verify_plan": "复跑",
            },
            created_by=ids["orch"],
        )
    with migrated_engine.connect() as c:
        inputs = summary_domain.collect_summary_inputs(c, ids["canvas"], ids["sum_node"])
    assert inputs["total_count"] == 1 and inputs["covered_count"] == 0
    assert len(inputs["uncovered"]) == 1
    node = inputs["nodes"][0]
    assert node["status"] == "closed" and node["deliverables"] == ["/a.py"]
    body = summary_domain.render_summary_message(inputs, round_count=1)
    assert "未覆盖" in body and "第 1 轮" in body and "/a.py" in body


def test_summary_fingerprint_changes_with_state(migrated_engine: Engine) -> None:
    ids = _build(migrated_engine)
    with migrated_engine.connect() as c:
        inputs = summary_domain.collect_summary_inputs(c, ids["canvas"], ids["sum_node"])
        fp1 = summary_domain.summary_fingerprint(c, inputs)
    _set_status(migrated_engine, ids["up_task"], "done")
    with migrated_engine.connect() as c:
        inputs2 = summary_domain.collect_summary_inputs(c, ids["canvas"], ids["sum_node"])
        fp2 = summary_domain.summary_fingerprint(c, inputs2)
    assert fp1 != fp2 and len(fp1) == 64


def test_blocked_at_suppresses_delivery_gating(migrated_engine: Engine) -> None:
    """O8 阻断双面（§6.3）：汇总任务 blocked_at 非空 → message_delivery_gated 抑制该线程投递；
    清空 blocked_at → 恢复投递（上游已 done，图 gating 亦解除）。"""
    ids = _build(migrated_engine)
    _set_status(migrated_engine, ids["up_task"], "done")  # 图 gating 解除
    msg = {"thread_root_id": ids["sum_root"], "id": new_ulid()}
    with migrated_engine.connect() as c:
        assert canvas_service.message_delivery_gated(c, msg) is False  # 未阻断
    with _tx(migrated_engine) as tx:
        summary_domain.ensure_run(
            tx, task_id=ids["sum_task"], canvas_id=ids["canvas"], workspace_id=ids["ws"]
        )
        summary_domain._set_blocked(tx, ids["sum_task"])
    with migrated_engine.connect() as c:
        assert canvas_service.message_delivery_gated(c, msg) is True  # 阻断中 → 抑制
    with _tx(migrated_engine) as tx:
        summary_domain.recover(tx, task_id=ids["sum_task"])
    with migrated_engine.connect() as c:
        assert canvas_service.message_delivery_gated(c, msg) is False  # 恢复 → 解抑制


def test_summary_task_for_thread_and_context(migrated_engine: Engine) -> None:
    ids = _build(migrated_engine)
    with migrated_engine.connect() as c:
        # 汇总线程消息 → 解析出汇总任务
        assert summary_domain.summary_task_for_thread(
            c, {"thread_root_id": ids["sum_root"]}
        ) == ids["sum_task"]
        # 非汇总任务线程 → None
        assert (
            summary_domain.summary_task_for_thread(c, {"id": "01K0NOTHREAD0000000000000A"})
            is None
        )
        ctx = summary_domain.node_context_for_task(c, ids["sum_task"])
        assert ctx is not None and ctx["node_id"] == ids["sum_node"]
        assert ctx["owner_id"] == ids["orch"]
        assert summary_domain.node_context_for_task(c, ids["up_task"]) is None  # 非汇总节点


# ---------------------------------------------------------------- 集成：hub 扫描驱动


def _summary_messages(engine: Engine, thread_root: str) -> list[str]:
    with engine.connect() as c:
        rows = c.execute(
            select(models.Message.__table__.c.body).where(
                models.Message.__table__.c.thread_root_id == thread_root,
                models.Message.__table__.c.author_member_id.is_(None),
            )
        ).scalars()
    return [b for b in rows if "汇总输入摘要" in b]


def test_scan_posts_summary_on_unblock_and_idempotent(
    migrated_engine: Engine, tmp_path: Path
) -> None:
    """hub 扫描（M8b L8）：上游 done 解除 partial 汇总 gating → 建行 + 发一条摘要系统消息（round 1，
    @Orchestrator）；同状态重复扫描幂等（不重发、不重计）。"""
    app = create_app(engine=migrated_engine, data_root=tmp_path / "data")
    hub = app.state.daemon_hub
    ids = _build(migrated_engine)
    _set_status(migrated_engine, ids["up_task"], "done")

    async def _run_scan() -> None:
        await hub._scan_channel_summary_nodes(ids["channel"])
        await hub._scan_channel_summary_nodes(ids["channel"])  # 幂等第二扫

    asyncio.run(_run_scan())

    run = _run(migrated_engine, ids["sum_task"])
    assert run is not None and run["round_count"] == 1  # 恰一进展轮（幂等）
    msgs = _summary_messages(migrated_engine, ids["sum_root"])
    assert len(msgs) == 1 and "第 1 轮" in msgs[0]  # 恰一摘要消息（防刷屏）


def test_scan_skips_when_blocked(migrated_engine: Engine, tmp_path: Path) -> None:
    """阻断态（blocked_at）→ 扫描不再计轮/发消息（§6.3 停自动唤醒），等人类恢复。"""
    app = create_app(engine=migrated_engine, data_root=tmp_path / "data")
    hub = app.state.daemon_hub
    ids = _build(migrated_engine)
    _set_status(migrated_engine, ids["up_task"], "done")
    with _tx(migrated_engine) as tx:
        summary_domain.ensure_run(
            tx, task_id=ids["sum_task"], canvas_id=ids["canvas"], workspace_id=ids["ws"]
        )
        summary_domain._set_blocked(tx, ids["sum_task"])
    asyncio.run(hub._scan_channel_summary_nodes(ids["channel"]))
    assert _summary_messages(migrated_engine, ids["sum_root"]) == []  # 阻断 → 不发
    run = _run(migrated_engine, ids["sum_task"])
    assert run is not None and run["round_count"] == 0  # 未计轮
