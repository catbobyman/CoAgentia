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
from sqlalchemy import insert, update
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


def _add_agent_node(env: Env, canvas_id: str, task_id: str) -> str:
    node_id = nid()
    with env.engine.begin() as c:
        c.execute(
            insert(models.CanvasNode.__table__).values(
                id=node_id,
                canvas_id=canvas_id,
                kind="agent",
                task_id=task_id,
                is_summary=False,
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
    """F6 回归（M6 review）：busy agent 的可投前缀不因「触发消息位于 held 之后」而滞留——
    新消息不在前缀内只取消以它为由的唤醒，不取消前缀冲洗（旧版对 busy 无条件冲洗，
    收窄成等 60s 对账是回归）。"""
    client, env, _hub = ctx
    ch, bee, _ta, rb = _blocked_setup(env)
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(bee, "busy")])
        d.recv_hello_ack()
        # 握手对账冲洗前缀 [anchor_a]（anchor_b 属 blocked 任务 B 线程被扣）——ack failed：
        # read_position 不推进，anchor_a 成为「可投但滞留」的前缀。
        deliver0 = d.recv_instr()
        assert deliver0["type"] == "message.deliver"
        pending = [m["id"] for m in deliver0["data"]["messages"]]
        d.ack(deliver0, "failed")
        d.sync()
        # 新消息位于 held（anchor_b）之后 → 不在可投前缀内，不构成唤醒/直投理由；
        # 修复后其事件仍冲洗既有前缀 [anchor_a]；修复前 membership 门直接 continue，
        # 前缀滞留到 60s 对账。
        r = client.post(
            f"/api/channels/{ch}/messages", json={"body": "随口一句", "file_ids": []}
        )
        assert r.status_code == 201
        deliver1 = d.recv_instr()
        assert deliver1["type"] == "message.deliver"
        got = [m["id"] for m in deliver1["data"]["messages"]]
        assert got == pending, (got, pending)  # 冲洗的仍是滞留前缀，不含 held 之后的新消息
        d.ack(deliver1, "done")
        d.sync()


def test_blocked_thread_message_not_leaked_in_backlog(ctx: tuple[TestClient, Env, Any]) -> None:
    """gating 只投 held 前连续前缀；later 不越过最大 id，解锁后整段按序补投。"""
    client, env, _hub = ctx
    ch, bee, ta, rb = _blocked_setup(env)
    # 先在 B（blocked）线程发 @Bee → gated（held），read_position 不推进，消息留积压。
    gated_id = client.post(
        f"/api/channels/{ch}/messages",
        json={"body": "@Bee 做 B", "thread_root_id": rb, "file_ids": []},
    ).json()["message"]["id"]

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(bee, "idle")])
        d.recv_hello_ack()
        d.sync()  # 握手对账：积压里 blocked 线程消息不构成触发，无残留 wake/deliver
        # 无关顶级 @Bee 虽非 gated，但位于 held 之后；不可越过它先投（daemon 以 max id 去重）。
        good_id = client.post(
            f"/api/channels/{ch}/messages",
            json={"body": "@Bee 顺便看下别的", "file_ids": []},
        ).json()["message"]["id"]
        d.sync()  # 连续前缀在 held 处截断：零 wake/零 deliver，游标不越过

    # 上游 A done → B 解锁 → 之前被 gate 的消息不再 gated，且未被消费 → 重连补投时可被投递。
    _set_status(env, ta, "done")
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws2:
        d2 = StubDaemon(ws2)
        d2.hello([(bee, "idle")])
        d2.recv_hello_ack()
        wake = d2.recv_instr()  # reconcile 补投：解锁后 @Bee 积压构成 mention 触发
        assert wake["type"] == "agent.wake"
        d2.ack(wake, "done")
        deliver = d2.recv_instr()
        assert deliver["type"] == "message.deliver"
        redelivered = {m["id"] for m in deliver["data"]["messages"]}
        assert gated_id in redelivered  # 之前被 gate 的消息解锁后补投，未丢
        assert good_id in redelivered  # held 后的消息也从未提前喂给 daemon，按序同批补投
        d2.ack(deliver, "done")
        d2.sync()
