"""浏览器 WS 传输层测试（契约 C 全文）：真 server 起 TestClient websocket_connect。

覆盖：hello（seq=1）· ping→pong（seq 单调）· 写端点广播回发起连接（channel_id/seq 正确）·
seq 单调无空洞 · 每个 M1 写端点触发对应事件（Envelope + EVENT_PAYLOADS 逐帧模型校验）·
断线后 REST 增量重建（契约 C §4）· diagnostic 订阅 sub/unsub 幂等与路由（§5/§8）·
owner presence 上下线（§2）。
"""

from __future__ import annotations

from typing import Any

from coagentia_contracts import ws
from coagentia_contracts.enums import PresenceStatus
from fastapi.testclient import TestClient

BUILD = "build"
PAT = "Pat"
SEED_COMPUTER = "01K0CMPT000000000000000001"


# ---------------------------------------------------------------- helpers


def envelope_of(raw: dict[str, Any]) -> ws.Envelope:
    """信封 + payload 逐帧过契约模型（契约 C §3：形状零偏差）。"""
    env = ws.Envelope.model_validate(raw)
    ws.EVENT_PAYLOADS[env.type].model_validate(env.data)
    return env


def recv(sock: Any) -> ws.Envelope:
    return envelope_of(sock.receive_json())


def _channel(client: TestClient, name: str) -> dict:
    return next(c for c in client.get("/api/channels").json()["items"] if c["name"] == name)


def _member(client: TestClient, name: str) -> dict:
    return next(m for m in client.get("/api/members").json() if m["name"] == name)


def _drain_open(sock: Any, *, first: bool) -> ws.Envelope:
    """吞掉连接建立帧：hello（seq 1）+（若首条连接）owner online。返回 hello 信封。"""
    hello = recv(sock)
    assert hello.type is ws.EventType.SYS_HELLO
    assert hello.seq == 1
    if first:
        pres = recv(sock)
        assert pres.type is ws.EventType.PRESENCE_CHANGED
        assert pres.data["status"] == PresenceStatus.ONLINE.value
    return hello


# ---------------------------------------------------------------- hello / 心跳


def test_hello_first_frame_seq_one(server_client: TestClient) -> None:
    with server_client.websocket_connect("/api/ws") as sock:
        hello = recv(sock)
        assert hello.type is ws.EventType.SYS_HELLO
        assert hello.seq == 1
        assert hello.data["protocol_v"] == ws.PROTOCOL_V
        assert hello.data["heartbeat_sec"] == ws.HEARTBEAT_SEC
        assert hello.data["conn_id"]
        assert hello.data["workspace_id"] == hello.workspace_id


def test_ping_pong_seq_monotonic(server_client: TestClient) -> None:
    with server_client.websocket_connect("/api/ws") as sock:
        _drain_open(sock, first=True)  # hello=1, presence=2
        sock.send_json({"type": "ping"})
        pong = recv(sock)
        assert pong.type is ws.EventType.SYS_PONG
        assert pong.seq == 3
        sock.send_json({"type": "ping"})
        pong2 = recv(sock)
        assert pong2.type is ws.EventType.SYS_PONG
        assert pong2.seq == 4


# ---------------------------------------------------------------- 写端点广播回发起连接


def test_post_message_broadcasts_to_originator(server_client: TestClient) -> None:
    build = _channel(server_client, BUILD)
    with server_client.websocket_connect("/api/ws") as sock:
        _drain_open(sock, first=True)
        r = server_client.post(
            f"/api/channels/{build['id']}/messages", json={"body": "契约即形状。"}
        )
        assert r.status_code == 201
        created = recv(sock)
        assert created.type is ws.EventType.MESSAGE_CREATED
        assert created.channel_id == build["id"]
        assert created.seq == 3  # hello=1, presence=2, message=3
        assert created.data["message"]["id"] == r.json()["message"]["id"]


# ---------------------------------------------------------------- 每个 M1 写端点触发对应事件


def test_every_m1_write_endpoint_emits_event_seq_no_holes(server_client: TestClient) -> None:
    c = server_client
    build = _channel(c, BUILD)
    pat = _member(c, PAT)

    with c.websocket_connect("/api/ws") as sock:
        seqs: list[int] = [_drain_open(sock, first=True).seq, 2]  # hello=1, presence.online=2

        def step(fn, expected: list[ws.EventType]) -> list[ws.Envelope]:
            r = fn()
            if hasattr(r, "status_code"):
                assert r.status_code < 300, (r.status_code, r.text)
            frames = [recv(sock) for _ in expected]
            got = [f.type for f in frames]
            assert sorted(got, key=lambda t: t.value) == sorted(expected, key=lambda t: t.value), (
                got,
                expected,
            )
            seqs.extend(f.seq for f in frames)
            return frames

        # 工作区
        step(lambda: c.patch("/api/workspace", json={"ui_theme": "light"}),
             [ws.EventType.WORKSPACE_UPDATED])
        # 频道 CRUD + 成员
        ops = step(lambda: c.post("/api/channels", json={"name": "ops"}),
                   [ws.EventType.CHANNEL_CREATED])[0].data["channel"]["id"]
        step(lambda: c.patch(f"/api/channels/{ops}", json={"description": "ops room"}),
             [ws.EventType.CHANNEL_UPDATED])
        step(lambda: c.post(f"/api/channels/{ops}/members", json={"member_id": pat["id"]}),
             [ws.EventType.CHANNEL_MEMBER_ADDED])
        step(lambda: c.delete(f"/api/channels/{ops}/members/{pat['id']}"),
             [ws.EventType.CHANNEL_MEMBER_REMOVED])
        # 消息 + 已读
        msg = step(lambda: c.post(f"/api/channels/{build['id']}/messages", json={"body": "hi"}),
                   [ws.EventType.MESSAGE_CREATED])[0].data["message"]["id"]
        step(lambda: c.put(f"/api/channels/{build['id']}/read-position",
                           json={"last_read_message_id": msg}),
             [ws.EventType.READ_UPDATED])
        # 成员 / Agent
        step(lambda: c.patch(f"/api/members/{pat['id']}", json={"role": "admin"}),
             [ws.EventType.MEMBER_UPDATED])
        vega = step(lambda: c.post("/api/agents", json={
            "computer_id": SEED_COMPUTER, "name": "Vega",
            "runtime": "claude_code", "model": "claude-sonnet"}),
            [ws.EventType.MEMBER_CREATED])[0].data["member"]["id"]
        step(lambda: c.patch(f"/api/agents/{vega}", json={"model": "claude-opus"}),
             [ws.EventType.AGENT_UPDATED])
        # 提醒
        step(lambda: c.post("/api/reminders", json={
            "kind": "once", "cadence": "2026-07-10T09:00:00.000Z",
            "anchor_channel_id": build["id"]}),
            [ws.EventType.REMINDER_CREATED])
        # 机器
        step(lambda: c.patch(f"/api/computers/{SEED_COMPUTER}", json={"name": "Rig-2"}),
             [ws.EventType.COMPUTER_UPDATED])
        # 归档 ops（两事件：锚点系统消息 + 频道更新）
        step(lambda: c.post(f"/api/channels/{ops}/archive"),
             [ws.EventType.MESSAGE_CREATED, ws.EventType.CHANNEL_UPDATED])
        # 删除一条无消息的新频道（含消息的频道因不可变 FK 不可硬删，见 channels 路由）
        temp = step(lambda: c.post("/api/channels", json={"name": "temp"}),
                    [ws.EventType.CHANNEL_CREATED])[0].data["channel"]["id"]
        step(lambda: c.delete(f"/api/channels/{temp}"),
             [ws.EventType.CHANNEL_DELETED])

    # 连接内 seq 单调、无空洞：连续自然数 1..N（契约 C §3）。
    assert seqs == list(range(1, len(seqs) + 1)), seqs


# ---------------------------------------------------------------- 断线后 REST 增量重建（§4）


def test_disconnect_then_rest_incremental_rebuild(server_client: TestClient) -> None:
    c = server_client
    build = _channel(c, BUILD)

    with c.websocket_connect("/api/ws") as sock:
        _drain_open(sock, first=True)
        r0 = c.post(f"/api/channels/{build['id']}/messages", json={"body": "before"})
        recv(sock)  # MESSAGE_CREATED
        anchor = r0.json()["message"]["id"]
    # 断线期间到达的写（无 WS 消费者接收帧——载状态事件靠 REST 重建，契约 C §1/§4）
    url = f"/api/channels/{build['id']}/messages"
    b = c.post(url, json={"body": "during-1"}).json()["message"]["id"]
    d = c.post(url, json={"body": "during-2"}).json()["message"]["id"]

    # 重连后仅凭 REST 增量拉取（?after=<本地最新>）即重建断线窗口（契约 C §4.1）。
    with c.websocket_connect("/api/ws") as sock2:
        _drain_open(sock2, first=True)  # 归零后重连 → 再次 owner online
        page = c.get(f"/api/channels/{build['id']}/messages", params={"after": anchor}).json()
        ids = [m["id"] for m in page["items"]]
        assert ids == [b, d]


# ---------------------------------------------------------------- diagnostic 订阅（§5/§8）


def test_diagnostic_sub_unsub_idempotent_and_routing(server_client: TestClient) -> None:
    c = server_client
    pat = _member(c, PAT)
    hub = c.app.state.ws_hub

    with c.websocket_connect("/api/ws") as sock:
        _drain_open(sock, first=True)
        # 重复 sub 幂等（契约 C §5）——ping/pong 往返确保上行已被顺序处理。
        sock.send_json({"type": "sub", "stream": "diagnostic", "agent_member_id": pat["id"]})
        sock.send_json({"type": "sub", "stream": "diagnostic", "agent_member_id": pat["id"]})
        sock.send_json({"type": "ping"})
        assert recv(sock).type is ws.EventType.SYS_PONG
        conn = next(iter(hub._conns))
        assert conn.diagnostic_subs == {pat["id"]}

        # 路由：订阅后 diagnostic.appended 转发到本连接（§8 订阅制）。
        c.app.state.bus.emit(
            ws.EventType.DIAGNOSTIC_APPENDED, None,
            {"agent_member_id": pat["id"], "events": []},
        )
        appended = recv(sock)
        assert appended.type is ws.EventType.DIAGNOSTIC_APPENDED
        assert appended.data["agent_member_id"] == pat["id"]

        # unsub 幂等 + 退订后不再转发（发 diagnostic 后紧跟 ping → 下一帧应为 pong）。
        sock.send_json({"type": "unsub", "stream": "diagnostic", "agent_member_id": pat["id"]})
        sock.send_json({"type": "unsub", "stream": "diagnostic", "agent_member_id": pat["id"]})
        sock.send_json({"type": "ping"})
        assert recv(sock).type is ws.EventType.SYS_PONG
        assert conn.diagnostic_subs == set()

        c.app.state.bus.emit(
            ws.EventType.DIAGNOSTIC_APPENDED, None,
            {"agent_member_id": pat["id"], "events": []},
        )
        sock.send_json({"type": "ping"})
        nxt = recv(sock)
        assert nxt.type is ws.EventType.SYS_PONG  # 退订后无 diagnostic 帧插入


# ---------------------------------------------------------------- 多标签全量广播（§2）


def test_multi_tab_each_connection_full_broadcast(server_client: TestClient) -> None:
    c = server_client
    build = _channel(c, BUILD)
    with c.websocket_connect("/api/ws") as a:
        _drain_open(a, first=True)  # a: hello=1, presence=2
        with c.websocket_connect("/api/ws") as b:
            _drain_open(b, first=False)  # b: hello=1（已在线，无 online 广播）
            c.post(f"/api/channels/{build['id']}/messages", json={"body": "broadcast"})
            fa, fb = recv(a), recv(b)
            assert fa.type is fb.type is ws.EventType.MESSAGE_CREATED
            assert fa.channel_id == fb.channel_id == build["id"]
            # 每连接独立 seq（契约 C §3）：a 续 3，b 续 2。
            assert fa.seq == 3
            assert fb.seq == 2


# ---------------------------------------------------------------- owner presence 上下线（§2 末）


def test_owner_presence_online_first_offline_last(server_client: TestClient) -> None:
    hub = server_client.app.state.ws_hub
    recorded: list[PresenceStatus] = []
    original = hub._broadcast_owner_presence

    async def spy(status: PresenceStatus) -> None:
        recorded.append(status)
        await original(status)

    hub._broadcast_owner_presence = spy
    try:
        with server_client.websocket_connect("/api/ws") as a:
            _drain_open(a, first=True)  # 首条连接 → online
            with server_client.websocket_connect("/api/ws") as b:
                _drain_open(b, first=False)  # 第二条 → 不重复广播
            # b 断开：仍有 a → 不发 offline
        # a 断开：最后一条 → offline（对 Agent freshness 语义可见）
    finally:
        hub._broadcast_owner_presence = original

    assert recorded == [PresenceStatus.ONLINE, PresenceStatus.OFFLINE]
