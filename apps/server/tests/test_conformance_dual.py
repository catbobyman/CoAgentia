"""契约一致性**双跑**（纪律 3）：把 M1 适用的形状/错误断言参数化为对
[mock app, 真 server app] 双跑——两实现对同一契约形状零偏差。

- 真 server fixture = create_app(注入临时库 alembic upgrade + seed) + TestClient；
- mock fixture = 既有 mock app + /__mock/reset 复位；
- M2/mock-only（GET /api/tasks、/__mock/play 时间线回放）不在本文件双跑；
- WS 双跑（A4 补齐）：hello/ping-pong/写端点广播的信封与 seq 语义对 [mock, 真 server] 零偏差；
  真 server 首连额外发 owner online（契约 C §2），故断言按目标事件类型收敛（drain_until）。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from coagentia_contracts import entities, rest, ws
from coagentia_server.app import create_app
from coagentia_server.db.engine import make_engine, sqlite_url
from coagentia_server.db.seed import seed_database
from fastapi.testclient import TestClient

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"

DualClient = tuple[str, TestClient]


@pytest.fixture(params=["mock", "real"])
def dual(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[DualClient]:
    if request.param == "mock":
        from coagentia_mock.app import app as mock_app

        client = TestClient(mock_app)
        client.post("/__mock/reset")  # 复位 store，测试间隔离
        yield "mock", client
    else:
        url = sqlite_url(tmp_path / "conf.db")
        cfg = Config(str(ALEMBIC_INI))
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, "head")
        engine = make_engine(url=url)
        seed_database(engine)
        app = create_app(engine=engine, data_root=tmp_path / "data")
        with TestClient(app) as client:
            yield "real", client
        engine.dispose()


def _build_channel(client: TestClient) -> dict:
    return next(c for c in client.get("/api/channels").json()["items"] if c["name"] == "build")


def _dm(client: TestClient) -> dict:
    return next(c for c in client.get("/api/channels").json()["items"] if c["kind"] == "dm")


def _member(client: TestClient, name: str) -> dict:
    return next(m for m in client.get("/api/members").json() if m["name"] == name)


# ---------------------------------------------------------------- 形状（读端点）


def test_workspace_shape(dual: DualClient) -> None:
    _, client = dual
    entities.WorkspacePublic.model_validate(client.get("/api/workspace").json())


def test_computers_shape_and_sensitive_column_stripped(dual: DualClient) -> None:
    _, client = dual
    for c in client.get("/api/computers").json():
        entities.ComputerPublic.model_validate(c)
        assert "api_key_hash" not in c  # 敏感列剔除（契约 A §8.2）


def test_members_shape(dual: DualClient) -> None:
    _, client = dual
    for m in client.get("/api/members").json():
        entities.MemberPublic.model_validate(m)


def test_presence_shape(dual: DualClient) -> None:
    _, client = dual
    rest.PresenceSnapshot.model_validate(client.get("/api/presence").json())


def test_channels_snapshot_with_read_positions(dual: DualClient) -> None:
    _, client = dual
    snap = rest.ChannelsSnapshot.model_validate(client.get("/api/channels").json())
    assert snap.read_positions, "自身 read-position 随 GET /channels 附带（契约 B §6）"


def test_messages_page_shape(dual: DualClient) -> None:
    _, client = dual
    build = _build_channel(client)
    page = rest.Page[entities.MessagePublic].model_validate(
        client.get(f"/api/channels/{build['id']}/messages").json()
    )
    assert page.items, "P1 消息流不为空"


def test_agent_detail_shape(dual: DualClient) -> None:
    _, client = dual
    pat = _member(client, "Pat")
    entities.AgentPublic.model_validate(client.get(f"/api/agents/{pat['id']}").json())


# ---------------------------------------------------------------- 错误路径（形状零偏差）


def test_task_in_dm(dual: DualClient) -> None:
    _, client = dual
    dm = _dm(client)
    r = client.post(
        f"/api/channels/{dm['id']}/messages", json={"body": "x", "as_task": {"title": "t"}}
    )
    assert r.status_code == 422
    err = rest.ErrorResponse.model_validate(r.json())
    assert err.error.code is rest.ErrorCode.TASK_IN_DM
    assert err.error.rule == "FR-5.1"


def test_r1_agent_never_owner(dual: DualClient) -> None:
    _, client = dual
    pat = _member(client, "Pat")
    r = client.patch(f"/api/members/{pat['id']}", json={"role": "owner"})
    assert r.status_code == 403
    err = rest.ErrorResponse.model_validate(r.json())
    assert err.error.code is rest.ErrorCode.PERMISSION_DENIED and err.error.rule == "R1"


def test_reminder_recurring_requires_loop_contract(dual: DualClient) -> None:
    _, client = dual
    ch = client.get("/api/channels").json()["items"][0]
    r = client.post(
        "/api/reminders",
        json={"kind": "recurring", "cadence": "0 9 * * *", "anchor_channel_id": ch["id"]},
    )
    assert r.status_code == 422
    err = rest.ErrorResponse.model_validate(r.json())
    assert err.error.code is rest.ErrorCode.VALIDATION_FAILED and err.error.rule == "D1-L2"


def test_not_top_level_message_as_task(dual: DualClient) -> None:
    _, client = dual
    build = _build_channel(client)
    r = client.post(
        f"/api/channels/{build['id']}/messages",
        json={"body": "x", "thread_root_id": build["id"], "as_task": {"title": "t"}},
    )
    assert r.status_code == 422
    err = rest.ErrorResponse.model_validate(r.json())
    assert err.error.code is rest.ErrorCode.NOT_TOP_LEVEL_MESSAGE and err.error.rule == "T3"


# ---------------------------------------------------------------- 文件 staging 往返


def test_file_staging_roundtrip(dual: DualClient) -> None:
    _, client = dual
    r = client.post("/api/files", files={"file": ("spec.md", b"# hi", "text/markdown")})
    assert r.status_code == 201
    meta = entities.FilePublic.model_validate(r.json())
    assert meta.message_id is None  # staging 态（契约 D §9.2）
    assert client.get(f"/api/files/{meta.id}/content").content == b"# hi"


# ---------------------------------------------------------------- 消息发送形状（写端点）


def test_post_message_shape(dual: DualClient) -> None:
    _, client = dual
    build = _build_channel(client)
    r = client.post(f"/api/channels/{build['id']}/messages", json={"body": "契约即形状。"})
    assert r.status_code == 201
    created = rest.MessageCreated.model_validate(r.json())
    assert created.task is None  # M1：tasks 是 M2 表，as_task 缺省 → task=null


# ---------------------------------------------------------------- WS 信封与广播（A4 双跑）


def _envelope_of(raw: dict) -> ws.Envelope:
    env = ws.Envelope.model_validate(raw)
    ws.EVENT_PAYLOADS[env.type].model_validate(env.data)  # payload 逐帧过契约模型
    return env


def _drain_until(sock, target: ws.EventType, last_seq: int) -> ws.Envelope:
    """读到 target 类型帧为止，逐帧校验信封 + seq 连接内单调（契约 C §3）。"""
    for _ in range(10):
        env = _envelope_of(sock.receive_json())
        assert env.seq > last_seq, (env.seq, last_seq)
        last_seq = env.seq
        if env.type is target:
            return env
    raise AssertionError(f"未在限帧内收到 {target}")


def test_ws_hello_ping_broadcast(dual: DualClient) -> None:
    _, client = dual
    build = _build_channel(client)
    with client.websocket_connect("/api/ws") as sock:
        hello = _envelope_of(sock.receive_json())
        assert hello.type is ws.EventType.SYS_HELLO and hello.seq == 1

        sock.send_json({"type": "ping"})
        pong = _drain_until(sock, ws.EventType.SYS_PONG, hello.seq)

        r = client.post(f"/api/channels/{build['id']}/messages", json={"body": "契约即形状。"})
        assert r.status_code == 201
        created = _drain_until(sock, ws.EventType.MESSAGE_CREATED, pong.seq)
        assert created.channel_id == build["id"]
        assert created.data["message"]["id"] == r.json()["message"]["id"]
