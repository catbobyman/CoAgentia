"""C3b：发消息生成人类 Activity（契约 B §9.7，M2 子集）。

两类：channel 频道的 mention（人类接收者、不给作者、不给 Agent）与 DM 的 dm（对端人类）。
Agent 成员永不作为接收者生成 activity（Activity 是人类聚合面）。真 server 断言。
"""

from __future__ import annotations

from coagentia_contracts.ws import EventType
from coagentia_server.db import models
from coagentia_server.events import PendingEvent
from coagentia_server.ledger import service
from fastapi.testclient import TestClient
from sqlalchemy import insert, select
from sqlalchemy.engine import Engine

BUILD = "build"

_ACTIVITY = models.ActivityItem.__table__
_MEMBER = models.Member.__table__
_CHANNEL = models.Channel.__table__
_CM = models.ChannelMember.__table__


def _channel(client: TestClient, name: str) -> dict:
    return next(c for c in client.get("/api/channels").json()["items"] if c["name"] == name)


def _member(client: TestClient, name: str) -> dict:
    return next(m for m in client.get("/api/members").json() if m["name"] == name)


def _add_human(engine: Engine, workspace_id: str, name: str) -> str:
    """插入一个人类成员（seed 只有 owner 一个人类，mention/DM 需第二个人类接收者）。"""
    mid = service.new_ulid()
    with engine.begin() as conn:
        conn.execute(
            insert(_MEMBER).values(
                id=mid,
                workspace_id=workspace_id,
                kind="human",
                name=name,
                role="member",
                removed_at=None,
                created_at=service.now_iso(),
            )
        )
    return mid


def _make_dm(engine: Engine, workspace_id: str, member_ids: list[str], dm_key: str) -> str:
    cid = service.new_ulid()
    with engine.begin() as conn:
        conn.execute(
            insert(_CHANNEL).values(
                id=cid,
                workspace_id=workspace_id,
                kind="dm",
                name=None,
                dm_key=dm_key,
                created_at=service.now_iso(),
            )
        )
        for m in member_ids:
            conn.execute(
                insert(_CM).values(
                    channel_id=cid, member_id=m, joined_at=service.now_iso()
                )
            )
    return cid


def _activities(engine: Engine, member_id: str) -> list[dict]:
    with engine.connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                select(_ACTIVITY).where(_ACTIVITY.c.member_id == member_id)
            ).mappings()
        ]


def _workspace_id(client: TestClient) -> str:
    return client.get("/api/workspace").json()["id"]


# ---------------------------------------------------------------- channel mention


def test_channel_mention_generates_activity_for_human(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    ws_id = _workspace_id(server_client)
    zoe = _add_human(seeded_engine, ws_id, "Zoe")
    build = _channel(server_client, BUILD)
    r = server_client.post(
        f"/api/channels/{build['id']}/messages", json={"body": "@Zoe 看下这个"}
    )
    assert r.status_code == 201, r.text
    acts = _activities(seeded_engine, zoe)
    assert len(acts) == 1
    a = acts[0]
    assert a["kind"] == "mention"
    assert a["channel_id"] == build["id"]
    assert a["message_id"] == r.json()["message"]["id"]
    assert a["done_at"] is None


def test_mention_self_no_activity(server_client: TestClient, seeded_engine: Engine) -> None:
    """作者 @自己 不生成 activity（B §9.7：不给作者本人）。"""
    build = _channel(server_client, BUILD)
    owner = _member(server_client, "Memcyo")
    r = server_client.post(
        f"/api/channels/{build['id']}/messages", json={"body": "@Memcyo 自言自语"}
    )
    assert r.status_code == 201
    assert _activities(seeded_engine, owner["id"]) == []


def test_mention_agent_no_activity(server_client: TestClient, seeded_engine: Engine) -> None:
    """@Agent 成员不生成 activity（Agent 永不作为接收者）。"""
    build = _channel(server_client, BUILD)
    pat = _member(server_client, "Pat")  # seed Agent
    r = server_client.post(
        f"/api/channels/{build['id']}/messages", json={"body": "@Pat 处理一下"}
    )
    assert r.status_code == 201
    assert _activities(seeded_engine, pat["id"]) == []


# ---------------------------------------------------------------- DM


def test_dm_generates_activity_for_peer_human(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    ws_id = _workspace_id(server_client)
    owner = _member(server_client, "Memcyo")
    zoe = _add_human(seeded_engine, ws_id, "Zoe")
    dm = _make_dm(seeded_engine, ws_id, [owner["id"], zoe], "dm-owner-zoe")
    r = server_client.post(f"/api/channels/{dm}/messages", json={"body": "私聊一下"})
    assert r.status_code == 201, r.text
    # 对端人类 Zoe 收到 dm activity
    zoe_acts = _activities(seeded_engine, zoe)
    assert len(zoe_acts) == 1
    assert zoe_acts[0]["kind"] == "dm"
    assert zoe_acts[0]["channel_id"] == dm
    # 作者本人（owner）无 activity
    assert _activities(seeded_engine, owner["id"]) == []


def test_dm_does_not_double_write_mention(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """DM 里即便 @对端也只落一条 dm（裁决：DM 不再生成 mention，避免同消息双写）。"""
    ws_id = _workspace_id(server_client)
    owner = _member(server_client, "Memcyo")
    zoe = _add_human(seeded_engine, ws_id, "Zoe")
    dm = _make_dm(seeded_engine, ws_id, [owner["id"], zoe], "dm-owner-zoe-2")
    r = server_client.post(f"/api/channels/{dm}/messages", json={"body": "@Zoe 私聊"})
    assert r.status_code == 201, r.text
    acts = _activities(seeded_engine, zoe)
    assert len(acts) == 1 and acts[0]["kind"] == "dm"


# ---------------------------------------------------------------- 广播


def test_activity_created_broadcast(server_client: TestClient, seeded_engine: Engine) -> None:
    ws_id = _workspace_id(server_client)
    zoe = _add_human(seeded_engine, ws_id, "Zoe")
    build = _channel(server_client, BUILD)
    events: list[PendingEvent] = []
    server_client.app.state.bus.subscribe(events.append)
    server_client.post(f"/api/channels/{build['id']}/messages", json={"body": "@Zoe hi"})
    created = [e for e in events if e.type is EventType.ACTIVITY_CREATED]
    assert len(created) == 1
    item = created[0].data["item"]
    assert item["kind"] == "mention"
    assert item["member_id"] == zoe
    assert created[0].channel_id is None  # 全局广播


def test_no_mention_no_activity(server_client: TestClient, seeded_engine: Engine) -> None:
    """普通频道消息无 @ → 不生成任何 activity。"""
    build = _channel(server_client, BUILD)
    events: list[PendingEvent] = []
    server_client.app.state.bus.subscribe(events.append)
    server_client.post(f"/api/channels/{build['id']}/messages", json={"body": "纯文本无提及"})
    assert [e for e in events if e.type is EventType.ACTIVITY_CREATED] == []
