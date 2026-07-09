"""A5 daemon 网关测试（契约 D §2–§8 + §11 验收用例）。

驱动方式：假 daemon（StubDaemon）连真 server 的 /api/daemon/ws，网关侧全链路走真码。
覆盖：接入认证 / 握手 / 帧收发底座（ack 重发·串行）/ 对账 #1/#2/#3/#8 / 投递引擎四状态×触发 /
deliver ack 写 read_positions / 上报 usage 去重·diagnostics / 断连级联 / 双连接竞争 /
生命周期与 Home query 打通。
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from coagentia_server.app import create_app
from coagentia_server.ledger.service import new_ulid, now_iso
from daemon_helpers import AUTH, Env, StubDaemon
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from starlette.websockets import WebSocketDisconnect

DAEMON_WS = "/api/daemon/ws"


@pytest.fixture
def ctx(migrated_engine: Engine, tmp_path: Path) -> Iterator[tuple[TestClient, Env, Any]]:
    """真 server（空库）+ 小超时 daemon 网关 + 受控 Env。"""
    app = create_app(engine=migrated_engine, data_root=tmp_path / "data")
    hub = app.state.daemon_hub
    hub.ack_timeout = 0.3
    hub.query_timeout = 0.3
    hub.reconcile_interval = 3600  # 关周期自动扫描（测试手动驱动）
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


def _bg(fn: Callable[[], Any]) -> tuple[threading.Thread, dict[str, Any]]:
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["r"] = fn()
        except Exception as e:  # noqa: BLE001
            box["e"] = e

    t = threading.Thread(target=run)
    t.start()
    return t, box


# ---------------------------------------------------------------- 接入认证（契约 D §2）


def test_auth_rejects_missing_and_bad_key(ctx: tuple[TestClient, Env, Any]) -> None:
    client, _env, _hub = ctx
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(DAEMON_WS) as ws:  # 无 Authorization
            ws.receive_json()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(DAEMON_WS, headers={"Authorization": "Bearer nope"}) as ws:
            ws.receive_json()


# ---------------------------------------------------------------- 握手（契约 D §4.1）


def test_handshake_hello_ack_and_computer_connected(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    a = env.add_agent("A", "offline")  # offline → 无 resume/投递
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(a, "offline")], os="Windows 11", arch="x64", version="1.2.3")
        ack = d.recv_hello_ack()
        assert ack["data"]["computer_id"] == env.comp_id
        assert ack["data"]["workspace_id"] == env.ws_id
        assert ack["data"]["heartbeat_sec"] == 25
        d.sync()  # 屏障：无残留指令
        assert env.computer_status() == "connected"


# ---------------------------------------------------------------- §11.3 崩溃重启空表 → resume


def test_case3_crash_restart_resumes_by_last_known_status(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    client, env, _hub = ctx
    pat = env.add_agent("Pat", "idle")
    hank = env.add_agent("Hank", "busy")
    rin = env.add_agent("Rin", "idle")
    _orch = env.add_agent("Orch", "offline")  # 人工 Stop 过 → 不拉起
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([])  # 空进程表（崩溃重启）
        d.recv_hello_ack()
        started = []
        for _ in range(3):
            f = d.recv_instr()
            assert f["type"] == "agent.start"
            started.append(f["data"]["agent"]["agent_member_id"])
            d.ack(f, "done")
        assert started == [pat, hank, rin]  # 按 member_id 序；offline 的 Orch 不在内
        d.sync()  # 无第 4 帧


# ---------------------------------------------------------------- §11.2 ack 丢失重发 → noop


def test_case2_ack_loss_resend_same_frame_noop(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    env.add_agent("Solo", "idle")
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([])
        d.recv_hello_ack()
        f1 = d.recv_instr()
        assert f1["type"] == "agent.start"
        # 不 ack → 10s(测试 0.3s) 超时 → 原帧原样重发（同 frame_id）。
        f2 = d.recv_instr()
        assert f2["frame_id"] == f1["frame_id"]
        d.ack(f2, "noop")  # 幂等命中
        d.sync()


# ---------------------------------------------------------------- §11.5 遥测重传 ULID 去重


def test_case5_usage_batch_exactly_once(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    a = env.add_agent("A", "offline")
    events = [
        {
            "id": new_ulid(),
            "agent_member_id": a,
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "reported_at": now_iso(),
        }
        for _ in range(3)
    ]
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(a, "offline")])
        d.recv_hello_ack()
        fid1 = d.report("usage.batch", {"events": events})
        ack1 = d.recv()
        assert ack1["kind"] == "ack" and ack1["ref"] == fid1
        assert env.usage_count() == 3
        # 重传同批（ack 丢失场景）→ ULID 主键去重，恰 3 行。
        fid2 = d.report("usage.batch", {"events": events})
        ack2 = d.recv()
        assert ack2["ref"] == fid2
        assert env.usage_count() == 3


# ---------------------------------------------------------------- §11.6 双连接竞争 → 旧连接 4001


def test_case6_double_connection_supersedes_old(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    a = env.add_agent("A", "offline")
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws_a:
        da = StubDaemon(ws_a)
        da.hello([(a, "offline")])
        da.recv_hello_ack()
        with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws_b:
            db = StubDaemon(ws_b)
            db.hello([(a, "offline")])
            db.recv_hello_ack()  # 新连接正常服务
            with pytest.raises(WebSocketDisconnect) as ei:
                da.recv()  # 旧连接被顶掉
            assert ei.value.code == 4001


# ---------------------------------------------------------------- 上报 diagnostics（契约 D §7）


def test_diagnostics_batch_inserts_and_acks(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    a = env.add_agent("A", "offline")
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(a, "offline")])
        d.recv_hello_ack()
        ev = {"agent_member_id": a, "payload": {}, "at": now_iso()}
        fid = d.report(
            "diagnostics.batch",
            {"events": [{**ev, "type": "agent.tool_call"}, {**ev, "type": "agent.command"}]},
        )
        ack = d.recv()
        assert ack["kind"] == "ack" and ack["ref"] == fid
        assert env.diag_count() == 2


# ---------------------------------------------------------------- 对账 #1 presence 纠偏（§4.4）


def test_reconcile_presence_correction(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    a = env.add_agent("A", "starting")  # 库最后已知态 starting
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(a, "idle")])  # 进程表报 idle → 以 daemon 为准
        d.recv_hello_ack()
        d.sync()
        assert _poll(lambda: env.agent_status(a) == "idle")


# ---------------------------------------------------------------- 上报 agent.status_changed（§7）


def test_status_changed_is_sole_writer(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    a = env.add_agent("A", "starting")
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(a, "starting")])
        d.recv_hello_ack()
        d.report("agent.status_changed", {"agent_member_id": a, "status": "busy"})
        d.sync()
        assert _poll(lambda: env.agent_status(a) == "busy")


# ---------------------------------------------------------------- 投递引擎四状态×触发（§8）


def _mention_setup(env: Env, status: str) -> tuple[str, str]:
    """agent（给定状态）+ 一个 regular 频道并入群，返回 (agent_id, channel_id)。"""
    a = env.add_agent("A", status)
    ch = env.add_channel(kind="channel", name="build")
    env.join(ch, a)
    env.join(ch, env.owner_id)
    return a, ch


def test_delivery_idle_mention_wakes_and_delivers(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    a, ch = _mention_setup(env, "idle")
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(a, "idle")])
        d.recv_hello_ack()
        # 通过 REST 发 @mention 消息（post_message 按名解析 @A → member A）→ bus 驱动投递引擎。
        r = client.post(f"/api/channels/{ch}/messages", json={"body": "@A hi", "file_ids": []})
        assert r.status_code == 201
        wake = d.recv_instr()
        assert wake["type"] == "agent.wake"
        assert wake["data"]["reason"] == "mention"
        d.ack(wake, "done")
        deliver = d.recv_instr()
        assert deliver["type"] == "message.deliver"
        assert deliver["data"]["channel_id"] == ch
        d.ack(deliver, "done")
        d.sync()
        # deliver ack(done) → 写 read_positions（§8.3 投递游标即已读）。
        assert _poll(lambda: env.read_position(a, ch) == r.json()["message"]["id"])


def test_delivery_idle_plain_message_silent_backlog(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    a, ch = _mention_setup(env, "idle")
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(a, "idle")])
        d.recv_hello_ack()
        # 普通频道非 @ 消息 → 静默积压（不唤醒不投递）。
        client.post(f"/api/channels/{ch}/messages", json={"body": "hello all", "file_ids": []})
        d.sync()  # 无 wake/deliver 帧
        assert env.read_position(a, ch) is None


def test_delivery_busy_direct_deliver_no_wake(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    a, ch = _mention_setup(env, "busy")
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(a, "busy")])
        d.recv_hello_ack()
        r = client.post(f"/api/channels/{ch}/messages", json={"body": "work", "file_ids": []})
        deliver = d.recv_instr()  # busy → 直投，无 wake
        assert deliver["type"] == "message.deliver"
        d.ack(deliver, "done")
        d.sync()
        assert _poll(lambda: env.read_position(a, ch) == r.json()["message"]["id"])


def test_delivery_offline_agent_no_frames(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    a, ch = _mention_setup(env, "offline")
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(a, "offline")])  # 不在进程表可交付集
        d.recv_hello_ack()
        client.post(f"/api/channels/{ch}/messages", json={"body": "@A hi", "file_ids": []})
        d.sync()
        assert env.read_position(a, ch) is None


# ------------------------- 投递引擎正确性回归（#2 NOOP / #3 自我回环 / #5 并发重复）


def test_deliver_noop_does_not_advance_read_position(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    """#2b：deliver ack(noop) 不推进 read_position（noop=已投过，原 done 已推进）。

    否则跨频道 daemon 全局去重误判 noop 时会把落后频道 read_position 错误推进 → 消息被标记
    已读却从未投递 → 永久丢失。契约 D §5.2「仅 done 后写 read_positions」。
    """
    client, env, _hub = ctx
    a, ch = _mention_setup(env, "busy")
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(a, "busy")])
        d.recv_hello_ack()
        client.post(f"/api/channels/{ch}/messages", json={"body": "work", "file_ids": []})
        deliver = d.recv_instr()
        assert deliver["type"] == "message.deliver"
        d.ack(deliver, "noop")  # 已喂过 → noop
        d.sync()
        assert env.read_position(a, ch) is None  # noop 不推进


def test_backlog_excludes_agent_own_messages(ctx: tuple[TestClient, Env, Any]) -> None:
    """#3：积压批排除收件 Agent 自己发的消息（自己发的不回喂，避免自我应答回环）。"""
    client, env, _hub = ctx
    a, ch = _mention_setup(env, "busy")
    m_own = env.add_message(ch, author=a, body="my own note")  # Agent 自己发的
    m_owner = env.add_message(ch, author=env.owner_id, body="@A ping", mentions=(a,))
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(a, "busy")])
        d.recv_hello_ack()
        deliver = d.recv_instr()  # 握手对账：busy 直投积压
        assert deliver["type"] == "message.deliver"
        ids = [m["id"] for m in deliver["data"]["messages"]]
        assert m_owner in ids
        assert m_own not in ids  # 自己发的不回喂
        d.ack(deliver, "done")
        d.sync()


def test_busy_concurrent_delivery_no_duplicate(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    """#5：busy 期相邻投递批在锁内重算积压，不重复喂较低 message_id。

    task1 直投 [m1] 后持 agent_lock 等 ack；task2（m2）阻塞在锁。ack1(done) 推进 read_position=m1
    并释放锁 → task2 取锁**重算**积压 → 仅 [m2]（不含已投的 m1）。修前 task2 会发锁外预算的 [m1,m2]。
    """
    client, env, _hub = ctx
    a, ch = _mention_setup(env, "busy")
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(a, "busy")])
        d.recv_hello_ack()
        r1 = client.post(f"/api/channels/{ch}/messages", json={"body": "one", "file_ids": []})
        m1 = r1.json()["message"]["id"]
        deliver1 = d.recv_instr()
        assert deliver1["type"] == "message.deliver"
        assert [m["id"] for m in deliver1["data"]["messages"]] == [m1]
        # m2 到达：task2 阻塞在 agent_lock（task1 尚未 ack）
        r2 = client.post(f"/api/channels/{ch}/messages", json={"body": "two", "file_ids": []})
        m2 = r2.json()["message"]["id"]
        d.ack(deliver1, "done")
        assert _poll(lambda: env.read_position(a, ch) == m1)
        deliver2 = d.recv_instr()
        assert deliver2["type"] == "message.deliver"
        assert [m["id"] for m in deliver2["data"]["messages"]] == [m2]  # 不重投 m1
        d.ack(deliver2, "done")
        d.sync()
        assert _poll(lambda: env.read_position(a, ch) == m2)


# ---------------------------------------------------------------- 对账 #3 投递补投（离线积压）


def test_case3_reconnect_backlog_delivery(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    a, ch = _mention_setup(env, "idle")
    # 离线期积压一条 @mention（直接落库，不经投递）。
    m = env.add_message(ch, author=env.owner_id, body="@A ping", mentions=(a,))
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(a, "idle")])
        d.recv_hello_ack()
        wake = d.recv_instr()
        assert wake["type"] == "agent.wake" and wake["data"]["reason"] == "mention"
        d.ack(wake, "done")
        deliver = d.recv_instr()
        assert deliver["type"] == "message.deliver"
        ids = [msg["id"] for msg in deliver["data"]["messages"]]
        assert m in ids
        d.ack(deliver, "done")
        d.sync()
        assert _poll(lambda: env.read_position(a, ch) == m)


# ---------------------------------------------------------------- 周期扫描 == 重连（§4.4）


def test_periodic_reconcile_same_as_reconnect(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, hub = ctx
    a, ch = _mention_setup(env, "idle")
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(a, "idle")])
        d.recv_hello_ack()
        d.sync()  # 握手对账：无积压
        conn = hub._conns[env.comp_id]
        # 离线后新积压一条 @mention，手动触发周期扫描（与重连同一 reconcile 代码）。
        m = env.add_message(ch, author=env.owner_id, body="@A later", mentions=(a,))
        fut = asyncio.run_coroutine_threadsafe(hub.reconcile(conn), hub._loop)
        wake = d.recv_instr()
        assert wake["type"] == "agent.wake"
        d.ack(wake, "done")
        deliver = d.recv_instr()
        assert m in [msg["id"] for msg in deliver["data"]["messages"]]
        d.ack(deliver, "done")
        fut.result(timeout=5)
        assert _poll(lambda: env.read_position(a, ch) == m)


# ---------------------------------------------------------------- §11.8 reminder 离线触发 → 补唤醒


def test_case8_reminder_offline_fire_then_reconcile_wake(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    client, env, hub = ctx
    a = env.add_agent("A", "idle")
    ch = env.add_channel(kind="channel", name="build")
    env.join(ch, a)
    rid = env.add_reminder(a, ch, next_fire_at="2020-01-01T00:00:00.000Z")  # 已到点
    # daemon 离线：reminder 到点 → 锚点系统消息照发、调度照推进。
    fired = asyncio.run_coroutine_threadsafe(hub.run_reminder_scan(), hub._loop).result(timeout=5)
    assert fired == 1
    assert env.reminder_status(rid) == "done"
    assert env.system_message_count(ch) == 1
    # 重连 → 对账 #3 命中锚点（视同 @mention）→ 补唤醒 reminder。
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(a, "idle")])
        d.recv_hello_ack()
        wake = d.recv_instr()
        assert wake["type"] == "agent.wake"
        assert wake["data"]["reason"] == "reminder"
        d.ack(wake, "done")
        deliver = d.recv_instr()
        assert deliver["type"] == "message.deliver"
        d.ack(deliver, "done")
        d.sync()


# ---------------------------------------------------------------- 断连级联（契约 D §2）


def test_disconnect_cascades_offline_but_keeps_agent_status(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    client, env, _hub = ctx
    a = env.add_agent("A", "idle")
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(a, "idle")])
        d.recv_hello_ack()
        d.sync()
        assert env.computer_status() == "connected"
    # 断连 → computers.status=offline，但 agents.status 保留最后已知态（resume 依据）。
    assert _poll(lambda: env.computer_status() == "offline")
    assert env.agent_status(a) == "idle"


# ---------------------------------------------------------------- 生命周期打通（契约 D §4.3/§5）


def test_lifecycle_dispatches_instr_when_connected(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    a = env.add_agent("A", "offline")
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(a, "offline")])
        d.recv_hello_ack()
        # REST 生命周期在后台线程发起（同步语义：等 ack）。
        t, box = _bg(lambda: client.post(f"/api/agents/{a}/lifecycle", json={"action": "start"}))
        f = d.recv_instr()
        assert f["type"] == "agent.start"
        assert f["data"]["agent"]["agent_member_id"] == a
        d.ack(f, "done")
        t.join(timeout=5)
        assert box["r"].status_code == 200
        assert box["r"].json()["result"] == "done"


def test_lifecycle_503_when_no_daemon(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    a = env.add_agent("A", "offline")  # 无连接
    r = client.post(f"/api/agents/{a}/lifecycle", json={"action": "start"})
    assert r.status_code == 503


# ---------------------------------------------------------------- Home query 打通（契约 D §6）


def test_home_tree_query_reply(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    a = env.add_agent("A", "offline")
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(a, "offline")])
        d.recv_hello_ack()
        t, box = _bg(
            lambda: client.get(f"/api/agents/{a}/home/tree", params={"path": "/"})
        )
        q = d.recv()
        assert q["kind"] == "query" and q["type"] == "home.tree"
        d.reply(
            q,
            {"entries": [{"name": "M.md", "kind": "file", "size_bytes": 12, "mtime": now_iso()}]},
        )
        t.join(timeout=5)
        assert box["r"].status_code == 200
        assert box["r"].json()["entries"][0]["name"] == "M.md"


def test_protocol_version_too_high_closes_4400(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    a = env.add_agent("A", "offline")
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(a, "offline")], v=2)  # 过高协议版本（契约 D §2）
        with pytest.raises(WebSocketDisconnect) as ei:
            d.recv()
        assert ei.value.code == 4400


def test_runtimes_detected_updates_computer(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    a = env.add_agent("A", "offline")
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(a, "offline")])
        d.recv_hello_ack()
        d.report(
            "runtimes.detected",
            {"runtimes": [{"runtime": "claude_code", "installed": True, "models": ["opus"]}]},
        )
        d.sync()
        assert _poll(lambda: env.detected_runtimes() == [
            {"runtime": "claude_code", "installed": True, "models": ["opus"]}
        ])


def test_computer_connected_broadcast_to_browser(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    a = env.add_agent("A", "offline")
    with client.websocket_connect("/api/ws") as browser:
        assert browser.receive_json()["type"] == "sys.hello"  # seq 1
        assert browser.receive_json()["type"] == "presence.changed"  # owner online
        with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
            d = StubDaemon(ws)
            d.hello([(a, "offline")])
            d.recv_hello_ack()
            types = {browser.receive_json()["type"], browser.receive_json()["type"]}
            assert types == {"computer.connected", "computer.updated"}


def test_home_tree_timeout_503(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    a = env.add_agent("A", "offline")
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(a, "offline")])
        d.recv_hello_ack()
        # 不 reply → query 超时（测试 0.3s）→ 发起端点回 503 DAEMON_OFFLINE。
        r = client.get(f"/api/agents/{a}/home/tree", params={"path": "/"})
        assert r.status_code == 503
        q = d.recv()  # 帧确已下发（超时非因未发）
        assert q["type"] == "home.tree"
