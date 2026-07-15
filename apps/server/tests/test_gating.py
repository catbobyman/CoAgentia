"""M3b E5 投递 gating（裁决 2）：blocked 推导逐例 + 向 blocked 任务线程投递被压制。

- blocked 推导（canvas/service.blocked_task_ids / message_delivery_gated）：线性链 / 菱形 /
  上游 done 解锁，直接对真库连接断言（纯派生，不落库）。
- 投递 gating：向 blocked 任务线程发 @mention → owner agent **不**被唤醒；上游全 done 后
  → 唤醒。status 写不受 gating 限（agent 仍能改 blocked 任务状态，R4/R7 回归）。

驱动方式仿 test_daemon.py：假 daemon（StubDaemon）连真 server /api/daemon/ws，网关侧走真码。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from coagentia_server.app import create_app
from coagentia_server.canvas import service as canvas_service
from coagentia_server.db import models
from coagentia_server.ledger.service import now_iso
from daemon_helpers import AUTH, Env, StubDaemon, nid
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

DAEMON_WS = "/api/daemon/ws"


@pytest.fixture
def ctx(migrated_engine: Engine, tmp_path: Path) -> Iterator[tuple[TestClient, Env, Any]]:
    """真 server（空库）+ 小超时 daemon 网关 + 受控 Env（关周期扫描，测试手动驱动）。"""
    app = create_app(engine=migrated_engine, data_root=tmp_path / "data")
    hub = app.state.daemon_hub
    hub.ack_timeout = 0.3
    hub.query_timeout = 0.3
    hub.reconcile_interval = 3600
    hub.reminder_interval = 3600
    env = Env(migrated_engine)
    with TestClient(app) as client:
        yield client, env, hub


def _poll(fn: Callable[[], bool], timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if fn():
            return True
        time.sleep(0.02)
    return False


# ---------------------------------------------------------------- 库构造辅助（直插最小图）


def _add_canvas(env: Env, channel_id: str) -> str:
    cid = nid()
    with env.engine.begin() as c:
        c.execute(
            insert(models.Canvas.__table__).values(
                id=cid,
                workspace_id=env.ws_id,
                channel_id=channel_id,
                baseline_version=0,
                baseline_hash="gating-test",
                updated_at=now_iso(),
            )
        )
    return cid


def _add_task(
    env: Env,
    channel_id: str,
    *,
    number: int,
    status: str = "todo",
    owner: str | None = None,
    level: str = "l1",
) -> tuple[str, str]:
    """建任务（含 system 锚点消息）；返回 (task_id, root_message_id)。"""
    anchor = env.add_message(channel_id, author=None, kind="system", body="anchor")
    tid = nid()
    with env.engine.begin() as c:
        c.execute(
            insert(models.Task.__table__).values(
                id=tid,
                workspace_id=env.ws_id,
                channel_id=channel_id,
                number=number,
                root_message_id=anchor,
                title=f"T{number}",
                status=status,
                owner_member_id=owner,
                level=level,
                created_by_member_id=env.owner_id,
                status_changed_at=now_iso(),
                created_at=now_iso(),
            )
        )
    return tid, anchor


def _add_agent_node(
    env: Env,
    canvas_id: str,
    task_id: str,
    *,
    suggested: str | None = None,
    policy: str = "strict",
) -> str:
    node_id = nid()
    with env.engine.begin() as c:
        c.execute(
            insert(models.CanvasNode.__table__).values(
                id=node_id,
                canvas_id=canvas_id,
                kind="agent",
                task_id=task_id,
                is_summary=False,
                suggested_owner=suggested,  # B-1 ②′：解锁唤醒的建议认领人（None=不 @）
                upstream_policy=policy,  # partial → 上游终态（含 closed）即放行
                pos_x=0,
                pos_y=0,
                created_at=now_iso(),
            )
        )
    return node_id


def _add_system_node(
    env: Env, canvas_id: str, *, action: str = "merge", status: str = "idle"
) -> str:
    node_id = nid()
    with env.engine.begin() as c:
        c.execute(
            insert(models.CanvasNode.__table__).values(
                id=node_id,
                canvas_id=canvas_id,
                kind="system",
                task_id=None,
                is_summary=False,
                system_action=action,
                command=None,
                system_status=status,
                pos_x=0,
                pos_y=0,
                created_at=now_iso(),
            )
        )
    return node_id


def _add_summary_node(
    env: Env, canvas_id: str, task_id: str, *, policy: str = "partial"
) -> str:
    """汇总节点（is_summary agent 节点，W9 M8b L7）：默认 upstream_policy='partial'。"""
    node_id = nid()
    with env.engine.begin() as c:
        c.execute(
            insert(models.CanvasNode.__table__).values(
                id=node_id,
                canvas_id=canvas_id,
                kind="agent",
                task_id=task_id,
                is_summary=True,
                upstream_policy=policy,
                pos_x=0,
                pos_y=0,
                created_at=now_iso(),
            )
        )
    return node_id


def _add_edge(env: Env, canvas_id: str, from_id: str, to_id: str) -> str:
    edge_id = nid()
    with env.engine.begin() as c:
        c.execute(
            insert(models.CanvasEdge.__table__).values(
                id=edge_id,
                canvas_id=canvas_id,
                from_node_id=from_id,
                to_node_id=to_id,
            )
        )
    return edge_id


def _set_status(env: Env, task_id: str, status: str) -> None:
    with env.engine.begin() as c:
        c.execute(
            update(models.Task.__table__)
            .where(models.Task.__table__.c.id == task_id)
            .values(status=status)
        )


def _blocked(env: Env) -> set[str]:
    with env.engine.connect() as c:
        return canvas_service.blocked_task_ids(c)


def _gated(env: Env, *, root: str | None = None, mid: str = "01K0GATINGPROBE00000000000") -> bool:
    with env.engine.connect() as c:
        return canvas_service.message_delivery_gated(c, {"id": mid, "thread_root_id": root})


# ---------------------------------------------------------------- blocked 推导逐例（纯派生）


def test_blocked_linear_chain(migrated_engine: Engine) -> None:
    """线性链 A→B→C（皆 todo）：根 A 永不 blocked，B/C blocked。"""
    env = Env(migrated_engine)
    ch = env.add_channel(name="build")
    canvas = _add_canvas(env, ch)
    ta, ra = _add_task(env, ch, number=1)
    tb, rb = _add_task(env, ch, number=2)
    tc, rc = _add_task(env, ch, number=3)
    na = _add_agent_node(env, canvas, ta)
    nb = _add_agent_node(env, canvas, tb)
    ncnode = _add_agent_node(env, canvas, tc)
    _add_edge(env, canvas, na, nb)
    _add_edge(env, canvas, nb, ncnode)

    assert _blocked(env) == {tb, tc}
    # message_delivery_gated：线程根映射到任务后按 blocked 集判定。
    assert _gated(env, root=rb) is True
    assert _gated(env, root=rc) is True
    assert _gated(env, root=ra) is False  # 根任务不 blocked
    # 顶级消息本身即某任务锚点（thread_root None、id==锚点）→ 按其任务判定。
    assert _gated(env, root=None, mid=rb) is True
    assert _gated(env, root=None, mid=ra) is False
    # 非任务线程消息（无匹配任务）→ 永不 gated。
    assert _gated(env, root=None, mid="01K0NOTATASK000000000000AA") is False


def test_blocked_diamond(migrated_engine: Engine) -> None:
    """菱形 A→B、A→C、B→D、C→D：A done 后仅汇聚点 D blocked。"""
    env = Env(migrated_engine)
    ch = env.add_channel(name="build")
    canvas = _add_canvas(env, ch)
    ta, _ = _add_task(env, ch, number=1)
    tb, _ = _add_task(env, ch, number=2)
    tc, _ = _add_task(env, ch, number=3)
    td, _ = _add_task(env, ch, number=4)
    na = _add_agent_node(env, canvas, ta)
    nb = _add_agent_node(env, canvas, tb)
    ncnode = _add_agent_node(env, canvas, tc)
    nd = _add_agent_node(env, canvas, td)
    _add_edge(env, canvas, na, nb)
    _add_edge(env, canvas, na, ncnode)
    _add_edge(env, canvas, nb, nd)
    _add_edge(env, canvas, ncnode, nd)

    assert _blocked(env) == {tb, tc, td}  # A 未 done：B/C/D 全 blocked
    _set_status(env, ta, "done")
    assert _blocked(env) == {td}  # A done：B/C 解锁，D 仍缺 B/C


def test_upstream_done_unblocks(migrated_engine: Engine) -> None:
    """上游 done 解锁：A→B，A done 后 B 不再 blocked。"""
    env = Env(migrated_engine)
    ch = env.add_channel(name="build")
    canvas = _add_canvas(env, ch)
    ta, _ = _add_task(env, ch, number=1)
    tb, _ = _add_task(env, ch, number=2)
    na = _add_agent_node(env, canvas, ta)
    nb = _add_agent_node(env, canvas, tb)
    _add_edge(env, canvas, na, nb)

    assert _blocked(env) == {tb}
    _set_status(env, ta, "done")
    assert _blocked(env) == set()


def test_w9_partial_summary_unblocks_on_closed_upstream(migrated_engine: Engine) -> None:
    """W9 双档（M8b L7）：汇总节点 upstream_policy='partial'——上游任务 Closed（终态非 done）
    即放行汇总；同图 strict 下游仍 blocked（终态不等于 done）。防单点 Closed 卡死全 DAG。"""
    env = Env(migrated_engine)
    ch = env.add_channel(name="build")
    canvas = _add_canvas(env, ch)
    ta, _ = _add_task(env, ch, number=1)
    tsum, _ = _add_task(env, ch, number=2)  # 汇总任务
    tstrict, _ = _add_task(env, ch, number=3)  # strict 对照下游
    na = _add_agent_node(env, canvas, ta)
    nsum = _add_summary_node(env, canvas, tsum, policy="partial")
    nstrict = _add_agent_node(env, canvas, tstrict)  # 默认 strict
    _add_edge(env, canvas, na, nsum)
    _add_edge(env, canvas, na, nstrict)

    # A 仍 todo（未达终态）→ partial 也不放行（非「任一完成」；防脏读）。
    assert _blocked(env) == {tsum, tstrict}
    # A Closed（终态非 done）→ partial 汇总放行；strict 下游仍 blocked。
    _set_status(env, ta, "closed")
    assert _blocked(env) == {tstrict}
    # A 改 done → strict 也解锁（现状语义），partial 亦解锁。
    _set_status(env, ta, "done")
    assert _blocked(env) == set()


def test_system_node_success_satisfies(migrated_engine: Engine) -> None:
    """system 节点 system_status=='success' 计入 satisfied：check(success)→B 则 B 不 blocked。"""
    env = Env(migrated_engine)
    ch = env.add_channel(name="build")
    canvas = _add_canvas(env, ch)
    tb, _ = _add_task(env, ch, number=1)
    sysn = _add_system_node(env, canvas, action="check", status="idle")
    nb = _add_agent_node(env, canvas, tb)
    _add_edge(env, canvas, sysn, nb)

    assert _blocked(env) == {tb}  # 上游 system 未 success
    with env.engine.begin() as c:
        c.execute(
            update(models.CanvasNode.__table__)
            .where(models.CanvasNode.__table__.c.id == sysn)
            .values(system_status="success")
        )
    assert _blocked(env) == set()  # system success → 解锁


# ---------------------------------------------------------------- 投递 gating（唤醒压制）


def _blocked_setup(env: Env) -> tuple[str, str, str, str]:
    """A(todo)→B，B owner=agent Bee，Bee 与 owner 入群；返回 (channel, bee, task_b, root_b)。"""
    ch = env.add_channel(name="build")
    bee = env.add_agent("Bee", "idle")
    env.join(ch, bee)
    env.join(ch, env.owner_id)
    canvas = _add_canvas(env, ch)
    ta, _ = _add_task(env, ch, number=1, status="todo")
    tb, rb = _add_task(env, ch, number=2, status="todo", owner=bee)
    na = _add_agent_node(env, canvas, ta)
    nb = _add_agent_node(env, canvas, tb)
    _add_edge(env, canvas, na, nb)
    return ch, bee, ta, rb


def test_gated_mention_does_not_wake_owner_agent(ctx: tuple[TestClient, Env, Any]) -> None:
    """向 blocked 任务 B 的线程发 @Bee → gated → owner agent 不被唤醒（对照非 gated 会 wake）。"""
    client, env, _hub = ctx
    ch, bee, _ta, rb = _blocked_setup(env)
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(bee, "idle")])
        d.recv_hello_ack()
        d.sync()  # 握手对账：blocked 积压不构成触发，无残留帧
        r = client.post(
            f"/api/channels/{ch}/messages",
            json={"body": "@Bee 开始", "thread_root_id": rb, "file_ids": []},
        )
        assert r.status_code == 201
        d.sync()  # gated → 无 wake/deliver 帧（否则 recv_pong 会收到 instr 而失败）


def test_mention_wakes_after_upstream_done(ctx: tuple[TestClient, Env, Any]) -> None:
    """上游 A done → B 解锁 → 同一 @Bee 消息构成 mention 触发 → owner agent 被唤醒。"""
    client, env, _hub = ctx
    ch, bee, ta, rb = _blocked_setup(env)
    _set_status(env, ta, "done")  # 解锁 B
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(bee, "idle")])
        d.recv_hello_ack()
        d.sync()  # 握手对账：锚点系统消息非 @Bee，无触发
        r = client.post(
            f"/api/channels/{ch}/messages",
            json={"body": "@Bee 开始", "thread_root_id": rb, "file_ids": []},
        )
        assert r.status_code == 201
        wake = d.recv_instr()
        assert wake["type"] == "agent.wake"
        assert wake["data"]["reason"] == "mention"
        d.ack(wake, "done")
        deliver = d.recv_instr()
        assert deliver["type"] == "message.deliver"
        d.ack(deliver, "done")
        d.sync()


def test_status_write_not_gated(ctx: tuple[TestClient, Env, Any]) -> None:
    """gating 只作用投递层不限状态写（R4/R7）：agent 仍能改 blocked 任务状态。"""
    client, env, _hub = ctx
    ch = env.add_channel(name="build")
    bee = env.add_agent("Bee", "idle")
    env.join(ch, bee)
    canvas = _add_canvas(env, ch)
    ta, _ = _add_task(env, ch, number=1, status="todo")
    tb, _rb = _add_task(env, ch, number=2, status="todo", owner=bee)
    na = _add_agent_node(env, canvas, ta)
    nb = _add_agent_node(env, canvas, tb)
    _add_edge(env, canvas, na, nb)

    assert tb in _blocked(env)  # B 确 blocked
    # agent Bee（经 Computer Bearer + X-Acting-Member）改 blocked 任务状态 todo→in_progress。
    r = client.post(
        f"/api/tasks/{tb}/status",
        json={"to": "in_progress"},
        headers={**AUTH, "X-Acting-Member": bee},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "in_progress"


def test_non_owner_agent_also_gated_on_blocked_thread(ctx: tuple[TestClient, Env, Any]) -> None:
    """gating 与 msg 绑定（非收件 Agent 特定）：blocked 线程消息对频道内任一 agent 都压制唤醒。"""
    client, env, _hub = ctx
    ch, bee, _ta, rb = _blocked_setup(env)
    cass = env.add_agent("Cass", "idle")  # 另一个频道内 agent（非任务 owner）
    env.join(ch, cass)
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(bee, "idle"), (cass, "idle")])
        d.recv_hello_ack()
        d.sync()
        r = client.post(
            f"/api/channels/{ch}/messages",
            json={"body": "@Cass @Bee 看这里", "thread_root_id": rb, "file_ids": []},
        )
        assert r.status_code == 201
        d.sync()  # blocked 线程 → 对 Bee 与 Cass 均无 wake/deliver


def test_busy_prefix_flush_when_new_message_behind_held(ctx: tuple[TestClient, Env, Any]) -> None:
    """B-1 ②′：busy agent 的可投前缀不因位于 gated 之后而滞留——gated 锚点被**跳过**（不截断），
    其后的非 gated 新消息与滞留前缀一同冲洗（旧版 gated 截断会把它挡到 60s 对账，②′ 根除）。"""
    client, env, _hub = ctx
    ch, bee, _ta, rb = _blocked_setup(env)
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(bee, "busy")])
        d.recv_hello_ack()
        # 握手对账冲洗前缀 [anchor_a]（anchor_b 属 blocked 任务 B 线程，②′ 跳过）——ack failed：
        # read_position 不推进，anchor_a 成为「可投但滞留」的前缀。
        deliver0 = d.recv_instr()
        assert deliver0["type"] == "message.deliver"
        pending = [m["id"] for m in deliver0["data"]["messages"]]
        d.ack(deliver0, "failed")
        d.sync()
        # 新消息（顶级、非 gated）位于 gated anchor_b 之后 → ②′ 下 anchor_b 跳过不截断 → 新消息
        # 与滞留前缀 [anchor_a] 一同冲洗（旧版 gated 截断会把它挡到 60s 对账）。
        new_id = client.post(
            f"/api/channels/{ch}/messages", json={"body": "随口一句", "file_ids": []}
        ).json()["message"]["id"]
        deliver1 = d.recv_instr()
        assert deliver1["type"] == "message.deliver"
        got = [m["id"] for m in deliver1["data"]["messages"]]
        assert got == pending + [new_id], (got, pending, new_id)  # 前缀 + 越过 gated 的新消息
        d.ack(deliver1, "done")
        d.sync()


def test_blocked_thread_message_not_leaked_in_backlog(ctx: tuple[TestClient, Env, Any]) -> None:
    """B-1 ②′：blocked 线程消息**跳过不投**（不 leak 进投递批、不自成唤醒），但**不截断**其后的非
    gated 消息——后者照常投递并唤醒（根除前缀死锁）。被跳过消息水位越过后不再推送（F5：Agent 经
    get_thread 拉取补齐；本例 B 已有 owner 且无 suggested_owner，故无解锁唤醒——见专测）。"""
    client, env, _hub = ctx
    ch, bee, ta, rb = _blocked_setup(env)
    # 在 B（blocked）线程发 @Bee → gated（跳过），read_position 不推进，消息留积压。
    gated_id = client.post(
        f"/api/channels/{ch}/messages",
        json={"body": "@Bee 做 B", "thread_root_id": rb, "file_ids": []},
    ).json()["message"]["id"]

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(bee, "idle")])
        d.recv_hello_ack()
        d.sync()  # 纯 gated 积压：跳过后无可投触发 → 零 wake/deliver
        # 顶级 @Bee（非 gated）位于 gated 之后 → ②′ 跳过 gated 不截断 → 该消息照常投递并唤醒。
        good_id = client.post(
            f"/api/channels/{ch}/messages",
            json={"body": "@Bee 顺便看下别的", "file_ids": []},
        ).json()["message"]["id"]
        wake = d.recv_instr()
        assert wake["type"] == "agent.wake"
        assert wake["data"]["reason"] == "mention"
        d.ack(wake, "done")
        deliver = d.recv_instr()
        assert deliver["type"] == "message.deliver"
        delivered = {m["id"] for m in deliver["data"]["messages"]}
        assert good_id in delivered  # 非 gated 消息越过 gated 投出（死锁根除）
        assert gated_id not in delivered  # blocked 线程消息跳过、不 leak 进投递批
        d.ack(deliver, "done")
        d.sync()

    # 水位已越过 gated（good_id > gated_id）→ 上游 done 后 gated 不再推送（F5：get_thread 补齐）。
    _set_status(env, ta, "done")
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws2:
        d2 = StubDaemon(ws2)
        d2.hello([(bee, "idle")])
        d2.recv_hello_ack()
        d2.sync()  # gated 已在水位下无补投；B 有 owner + 无 suggested_owner → 无解锁唤醒


# ---------------------------------------------------------------- B-1 ②′ 解锁主动唤醒（刀2）


def _unblock_msgs(env: Env, root_id: str) -> list[Any]:
    """任务线程内的解锁唤醒系统消息（body 前缀 = 幂等标记 _UNBLOCK_PREFIX）。"""
    m = models.Message.__table__
    with env.engine.connect() as c:
        rows = c.execute(
            select(m.c.id, m.c.body).where(m.c.thread_root_id == root_id, m.c.kind == "system")
        ).all()
    return [r for r in rows if str(r[1]).startswith("上游已全部完成")]


def test_downstream_unblock_wakes_suggested_owner(ctx: tuple[TestClient, Env, Any]) -> None:
    """B-1 ②′ 刀2：上游 done → 下游未认领节点（有入边+建议人）解除 blocked → 任务线程发 @建议人
    系统消息 → REMINDER 唤醒建议人（补齐 F2『解锁无唤醒』缺口）。重连再扫幂等，不重发。"""
    client, env, _hub = ctx
    ch = env.add_channel(name="build")
    env.join(ch, env.owner_id)
    cass = env.add_agent("Cass", "idle")
    env.join(ch, cass)
    canvas = _add_canvas(env, ch)
    ta, _ra = _add_task(env, ch, number=1, status="done")  # 上游已终态
    tb, rb = _add_task(env, ch, number=2, status="todo")  # 下游未认领（owner=None）
    na = _add_agent_node(env, canvas, ta)
    nb = _add_agent_node(env, canvas, tb, suggested=cass)  # 有入边 + 建议人
    _add_edge(env, canvas, na, nb)
    assert tb not in _blocked(env)  # A done → B 已解除 blocked

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(cass, "idle")])
        d.recv_hello_ack()
        # 重连对账内 _scan_workspace_unblocked_nodes 发 @Cass 系统消息 → 该消息触发 REMINDER 唤醒。
        wake = d.recv_instr()
        assert wake["type"] == "agent.wake"
        assert wake["data"]["reason"] == "reminder"  # system + mention = REMINDER
        d.ack(wake, "done")
        deliver = d.recv_instr()
        assert deliver["type"] == "message.deliver"
        d.ack(deliver, "done")
        d.sync()
    assert len(_unblock_msgs(env, rb)) == 1  # 恰一条解锁消息

    # 重连再扫：解锁消息已存在 → 幂等不重发，Cass 不再被唤醒。
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws2:
        d2 = StubDaemon(ws2)
        d2.hello([(cass, "idle")])
        d2.recv_hello_ack()
        d2.sync()  # 无新解锁消息、无新唤醒
    assert len(_unblock_msgs(env, rb)) == 1  # 仍恰一条


def test_no_unblock_wake_when_claimed_or_no_suggested(ctx: tuple[TestClient, Env, Any]) -> None:
    """B-1 ②′ 刀2 守卫：已认领（owner 非空）或无 suggested_owner 的解锁节点**不**发解锁唤醒——前者
    由 worktree 激活路径接管、后者无有效唤醒目标。"""
    client, env, _hub = ctx
    ch = env.add_channel(name="build")
    env.join(ch, env.owner_id)
    dee = env.add_agent("Dee", "idle")
    env.join(ch, dee)
    canvas = _add_canvas(env, ch)
    ta, _ra = _add_task(env, ch, number=1, status="done")
    # B1：已认领（owner=Dee）+ 有建议人 → 不发（认领路径接管）。
    tb, rb = _add_task(env, ch, number=2, status="todo", owner=dee)
    # B2：未认领 + 无建议人 → 不发（无唤醒目标）。
    tc, rc = _add_task(env, ch, number=3, status="todo")
    na = _add_agent_node(env, canvas, ta)
    nb = _add_agent_node(env, canvas, tb, suggested=dee)
    ncc = _add_agent_node(env, canvas, tc)  # 无 suggested
    _add_edge(env, canvas, na, nb)
    _add_edge(env, canvas, na, ncc)

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(dee, "idle")])
        d.recv_hello_ack()
        d.sync()  # 两个下游都不满足条件 → 零解锁唤醒
    assert _unblock_msgs(env, rb) == []
    assert _unblock_msgs(env, rc) == []


def test_serial_chain_run_of_gated_anchors_does_not_deadlock(
    ctx: tuple[TestClient, Env, Any]
) -> None:
    """B-1 ②′ 刀1 realtest 复刻：串行链落地后**多个连续 blocked 锚点**排在『已落地』@入口人之前——
    旧版首个 gated 截断整条前缀 → 全频道 Agent 永久饿死；②′ 逐个跳过 gated 锚点 → 『已落地』唤醒
    照常送达入口人。"""
    client, env, _hub = ctx
    ch = env.add_channel(name="build")
    alice = env.add_agent("Alice", "idle")
    env.join(ch, alice)
    env.join(ch, env.owner_id)
    canvas = _add_canvas(env, ch)
    # A(todo,入口)→B→C 串行链：B、C 属 blocked 任务，其锚点 gated。
    ta, _ra = _add_task(env, ch, number=1, status="todo")
    tb, _rb = _add_task(env, ch, number=2, status="todo")
    tc, _rc = _add_task(env, ch, number=3, status="todo")
    na = _add_agent_node(env, canvas, ta)
    nb = _add_agent_node(env, canvas, tb)
    ncc = _add_agent_node(env, canvas, tc)
    _add_edge(env, canvas, na, nb)
    _add_edge(env, canvas, nb, ncc)
    assert tb in _blocked(env) and tc in _blocked(env)  # B、C 连续 gated

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(alice, "idle")])
        d.recv_hello_ack()
        d.sync()  # 握手：入口锚点非 @Alice，无触发；gated 锚点跳过不截断
        # 「已落地」@Alice（顶级、非 gated）排在 B、C 两个 gated 锚点之后。
        landed_id = client.post(
            f"/api/channels/{ch}/messages",
            json={"body": "@Alice 拆解已落地，可开工", "file_ids": []},
        ).json()["message"]["id"]
        wake = d.recv_instr()  # ②′：越过 B/C gated 锚点，「已落地」唤醒送达（旧版死锁于此）
        assert wake["type"] == "agent.wake"
        assert wake["data"]["reason"] == "mention"
        d.ack(wake, "done")
        deliver = d.recv_instr()
        assert deliver["type"] == "message.deliver"
        assert landed_id in {m["id"] for m in deliver["data"]["messages"]}
        d.ack(deliver, "done")
        d.sync()


def _add_running_batch(env: Env, channel_id: str, *, kind: str = "delta") -> str:
    bid = nid()
    with env.engine.begin() as c:
        c.execute(
            insert(models.LandingBatch.__table__).values(
                id=bid,
                workspace_id=env.ws_id,
                channel_id=channel_id,
                kind=kind,
                content_hash="x",
                source_ref="src",
                confirmed_by="auto",
                status="running",
                created_at=now_iso(),
            )
        )
    return bid


def test_unblock_scan_suppressed_during_running_landing(
    ctx: tuple[TestClient, Env, Any]
) -> None:
    """B-1 ②′ 刀2 守卫（对齐 _scan_channel_system_nodes 的 in_progress 门）：running 落地批期间不补
    发解锁唤醒——delta 先删后加的截断中间图会让下游瞬时解除 blocked，幂等消息发出不可撤，故落地期
    抑制；批 :done 后（LANDING_COMPLETED / 对账重扫）再补发。"""
    client, env, _hub = ctx
    ch = env.add_channel(name="build")
    env.join(ch, env.owner_id)
    cass = env.add_agent("Cass", "idle")
    env.join(ch, cass)
    canvas = _add_canvas(env, ch)
    ta, _ra = _add_task(env, ch, number=1, status="done")
    tb, rb = _add_task(env, ch, number=2, status="todo")
    na = _add_agent_node(env, canvas, ta)
    nb = _add_agent_node(env, canvas, tb, suggested=cass)
    _add_edge(env, canvas, na, nb)
    assert tb not in _blocked(env)  # A done → B 已解除 blocked
    _add_running_batch(env, ch)  # 但有 running 落地批 → 抑制

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(cass, "idle")])
        d.recv_hello_ack()
        d.sync()  # 落地期抑制 → 零解锁唤醒
    assert _unblock_msgs(env, rb) == []

    # 落地批 :done → 抑制解除 → 重连对账重扫补发。
    with env.engine.begin() as c:
        c.execute(update(models.LandingBatch.__table__).values(status="done"))
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws2:
        d2 = StubDaemon(ws2)
        d2.hello([(cass, "idle")])
        d2.recv_hello_ack()
        wake = d2.recv_instr()
        assert wake["type"] == "agent.wake"
        d2.ack(wake, "done")
        deliver = d2.recv_instr()
        assert deliver["type"] == "message.deliver"
        d2.ack(deliver, "done")
        d2.sync()
    assert len(_unblock_msgs(env, rb)) == 1


def test_batch_tail_gated_not_swallowed_by_watermark(
    ctx: tuple[TestClient, Env, Any]
) -> None:
    """B-1 ②′ 不变量 5（设计 §5 要求钉住的探针）：批尾连续 gated 时水位止于**最后实投消息**，不越过
    未投的 gated 尾巴——尾巴解锁后仍可投（若水位越过则尾巴永远在 read_position 下被 noop 吞没 = B-1
    饿死类回归，且全套测试仍绿）。用 busy agent 观测：首投只含非 gated 前缀、水位不含 gated 尾巴，
    解锁后尾巴补投。"""
    client, env, _hub = ctx
    ch = env.add_channel(name="build")
    bee = env.add_agent("Bee", "busy")
    env.join(ch, bee)
    env.join(ch, env.owner_id)
    canvas = _add_canvas(env, ch)
    ta, _ra = _add_task(env, ch, number=1, status="todo")  # 入口，非 blocked，anchor 非 gated
    tb, rb = _add_task(env, ch, number=2, status="todo")  # blocked
    na = _add_agent_node(env, canvas, ta)
    nb = _add_agent_node(env, canvas, tb)
    _add_edge(env, canvas, na, nb)
    assert tb in _blocked(env)
    # 批尾 = B（blocked）线程内的消息（gated），id 最大（在 anchor_a/anchor_b 之后）。
    tail_id = client.post(
        f"/api/channels/{ch}/messages",
        json={"body": "尾巴", "thread_root_id": rb, "file_ids": []},
    ).json()["message"]["id"]

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(bee, "busy")])
        d.recv_hello_ack()
        # busy 直投：anchor_a 投出，anchor_b + tail（gated 尾巴）跳过，水位止于 anchor_a。
        deliver = d.recv_instr()
        assert deliver["type"] == "message.deliver"
        assert tail_id not in {m["id"] for m in deliver["data"]["messages"]}  # 尾巴 gated 不投
        d.ack(deliver, "done")  # read_position 推进到实投最大 id（不含 tail）
        d.sync()
    # 解锁 B → tail 不再 gated。若首投水位曾越过 tail，则 tail 在 read_position 下永不重投（吞没）；
    # 正确实现下水位止于 anchor_a、tail 仍在其上 → 重连补投得到 tail。
    _set_status(env, ta, "done")
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws2:
        d2 = StubDaemon(ws2)
        d2.hello([(bee, "busy")])
        d2.recv_hello_ack()
        deliver2 = d2.recv_instr()
        assert deliver2["type"] == "message.deliver"
        assert tail_id in {m["id"] for m in deliver2["data"]["messages"]}  # 尾巴未被水位吞
        d2.ack(deliver2, "done")
        d2.sync()


def test_downstream_unblock_via_task_terminal_bus_path(
    ctx: tuple[TestClient, Env, Any]
) -> None:
    """B-1 ②′ 刀2 主路径（bus，非对账兜底）：上游经 REST 转终态时 TASK_UPDATED → _on_bus_event 即刻
    触发解锁扫描 → 下游 @建议人低延迟唤醒（不等重连/周期对账）。用 todo→closed（无 handoff 门）驱动
    真 REST 转态、partial 下游（终态含 closed 即放行）验证 bus 触发面（覆盖 to_status∈{done,closed}
    分支的 closed 侧）。"""
    client, env, _hub = ctx
    ch = env.add_channel(name="build")
    env.join(ch, env.owner_id)
    cass = env.add_agent("Cass", "idle")
    env.join(ch, cass)
    canvas = _add_canvas(env, ch)
    ta, _ra = _add_task(env, ch, number=1, status="todo")  # 上游，人类可直接 todo→closed
    tb, rb = _add_task(env, ch, number=2, status="todo")  # 下游未认领
    na = _add_agent_node(env, canvas, ta)
    nb = _add_agent_node(env, canvas, tb, suggested=cass, policy="partial")  # 终态即放行
    _add_edge(env, canvas, na, nb)
    assert tb in _blocked(env)  # A 未终态 → B blocked

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(cass, "idle")])
        d.recv_hello_ack()
        d.sync()  # 连接建立、B 仍 blocked → 无帧
        # 人类（无 X-Acting-Member = Owner）经 REST 把 A todo→closed（合法边，无 handoff 门）。
        r = client.post(f"/api/tasks/{ta}/status", json={"to": "closed"})
        assert r.status_code == 200
        # bus TASK_UPDATED(closed) → 解锁扫描 → @Cass 系统消息 → REMINDER 唤醒。
        wake = d.recv_instr()
        assert wake["type"] == "agent.wake"
        assert wake["data"]["reason"] == "reminder"
        d.ack(wake, "done")
        deliver = d.recv_instr()
        assert deliver["type"] == "message.deliver"
        d.ack(deliver, "done")
        d.sync()
    assert len(_unblock_msgs(env, rb)) == 1
