"""H3：每频道通知设置端点 + mode 消费门（契约 B §4.5/§11.4）。

覆盖：人类本人自治 / Agent 403 / dm 422 / GET 默认懒建 / PUT upsert /
mute 掐 mention activity 生成 / all·mentions 后端不变 / dm activity 恒生成 / 快照字段。
真 server 断言（TestClient + seeded_engine）。mode 门只作用**人类通知面**，不作用 Agent 投递层。
"""

from __future__ import annotations

import hashlib
from typing import Any

from coagentia_contracts import entities, rest
from coagentia_server.db import models
from coagentia_server.ledger import service
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

_MEMBER = models.tbl(models.Member)
_CHANNEL = models.tbl(models.Channel)
_CM = models.tbl(models.ChannelMember)
_NOTIF = models.tbl(models.ChannelNotificationSetting)
_ACTIVITY = models.tbl(models.ActivityItem)
_AGENT = models.tbl(models.Agent)
_COMPUTER = models.tbl(models.Computer)

BUILD = "build"


# ---------------------------------------------------------------- helpers


def _channel(client: TestClient, name: str) -> dict[str, Any]:
    return next(c for c in client.get("/api/channels").json()["items"] if c["name"] == name)


def _member(client: TestClient, name: str) -> dict[str, Any]:
    return next(m for m in client.get("/api/members").json() if m["name"] == name)


def _workspace_id(client: TestClient) -> str:
    return client.get("/api/workspace").json()["id"]


def _add_human(engine: Engine, workspace_id: str, name: str) -> str:
    """插一个人类成员（seed 只有 owner 一个人类，mention 需第二个人类接收者）。"""
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


def _set_mode(engine: Engine, channel_id: str, member_id: str, mode: str) -> None:
    """直插一行通知设置（绕过端点，供门/快照/dm 分支断言使用）。"""
    with engine.begin() as conn:
        conn.execute(
            insert(_NOTIF).values(channel_id=channel_id, member_id=member_id, mode=mode)
        )


def _activities(engine: Engine, member_id: str) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                select(_ACTIVITY).where(_ACTIVITY.c.member_id == member_id)
            ).mappings()
        ]


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
                insert(_CM).values(channel_id=cid, member_id=m, joined_at=service.now_iso())
            )
    return cid


def _agent_headers(engine: Engine, name: str) -> dict[str, str]:
    """把该 Agent 所属 Computer 的 api_key_hash 设为已知 key，返回代理请求头（契约 B §2）。"""
    key = f"cak_notif_{name}"
    with engine.begin() as conn:
        mid = conn.execute(select(_MEMBER.c.id).where(_MEMBER.c.name == name)).scalar_one()
        cid = conn.execute(
            select(_AGENT.c.computer_id).where(_AGENT.c.member_id == mid)
        ).scalar_one()
        conn.execute(
            update(_COMPUTER)
            .where(_COMPUTER.c.id == cid)
            .values(api_key_hash=hashlib.sha256(key.encode()).hexdigest())
        )
    return {"Authorization": f"Bearer {key}", "X-Acting-Member": mid}


# ---------------------------------------------------------------- 端点：GET/PUT


def test_get_default_lazy_create(server_client: TestClient, seeded_engine: Engine) -> None:
    """GET 无行 → 默认 {mode: all}，且不写库（懒建：仅 PUT 落行）。"""
    build = _channel(server_client, BUILD)
    owner = _member(server_client, "Memcyo")
    r = server_client.get(f"/api/channels/{build['id']}/notification-setting")
    assert r.status_code == 200, r.text
    pub = entities.ChannelNotificationSettingPublic.model_validate(r.json())
    assert pub.mode == "all"
    assert pub.channel_id == build["id"]
    assert pub.member_id == owner["id"]
    with seeded_engine.connect() as conn:  # GET 不落行
        row = conn.execute(
            select(_NOTIF).where(_NOTIF.c.channel_id == build["id"])
        ).mappings().first()
    assert row is None


def test_put_upsert_lazy_create_then_update(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """PUT 懒建后再 PUT 更新同一行（复合 PK，不重复插）；GET 回读最新。"""
    build = _channel(server_client, BUILD)
    owner = _member(server_client, "Memcyo")
    r1 = server_client.put(
        f"/api/channels/{build['id']}/notification-setting", json={"mode": "mentions"}
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["mode"] == "mentions"
    r2 = server_client.put(
        f"/api/channels/{build['id']}/notification-setting", json={"mode": "mute"}
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["mode"] == "mute"
    with seeded_engine.connect() as conn:
        rows = list(
            conn.execute(
                select(_NOTIF).where(
                    _NOTIF.c.channel_id == build["id"], _NOTIF.c.member_id == owner["id"]
                )
            ).mappings()
        )
    assert len(rows) == 1 and rows[0]["mode"] == "mute"
    g = server_client.get(f"/api/channels/{build['id']}/notification-setting")
    assert g.json()["mode"] == "mute"


def test_human_autonomy_no_admin_gate(server_client: TestClient) -> None:
    """人类本人自治设置成功（成员自治，无 admin 门；Owner 主体过）——区别于 patch_channel。"""
    build = _channel(server_client, BUILD)
    r = server_client.put(
        f"/api/channels/{build['id']}/notification-setting", json={"mode": "mentions"}
    )
    assert r.status_code == 200, r.text


def test_agent_subject_forbidden(server_client: TestClient, seeded_engine: Engine) -> None:
    """Agent 主体（Bearer+X-Acting-Member）GET/PUT → 403 PERMISSION_DENIED（通知是人类面）。"""
    build = _channel(server_client, BUILD)
    headers = _agent_headers(seeded_engine, "Pat")
    g = server_client.get(
        f"/api/channels/{build['id']}/notification-setting", headers=headers
    )
    assert g.status_code == 403, g.text
    err = rest.ErrorResponse.model_validate(g.json())
    assert err.error.code is rest.ErrorCode.PERMISSION_DENIED
    p = server_client.put(
        f"/api/channels/{build['id']}/notification-setting",
        json={"mode": "mute"},
        headers=headers,
    )
    assert p.status_code == 403, p.text
    with seeded_engine.connect() as conn:  # 403 不落行
        assert conn.execute(select(_NOTIF)).first() is None


def test_dm_channel_notif_422(server_client: TestClient) -> None:
    """kind=dm GET/PUT → 422 NOTIF_IN_DM（DM 必达，无设置面）。"""
    dm = next(
        c for c in server_client.get("/api/channels").json()["items"] if c["kind"] == "dm"
    )
    g = server_client.get(f"/api/channels/{dm['id']}/notification-setting")
    assert g.status_code == 422, g.text
    assert rest.ErrorResponse.model_validate(g.json()).error.code is rest.ErrorCode.NOTIF_IN_DM
    p = server_client.put(
        f"/api/channels/{dm['id']}/notification-setting", json={"mode": "mute"}
    )
    assert p.status_code == 422, p.text
    assert rest.ErrorResponse.model_validate(p.json()).error.code is rest.ErrorCode.NOTIF_IN_DM


# ---------------------------------------------------------------- mode 消费门


def test_mute_suppresses_mention_activity(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """接收者该频道 mode=mute → @其 不生成 mention activity（§11.4 #3 唯一消费点）。"""
    ws_id = _workspace_id(server_client)
    zoe = _add_human(seeded_engine, ws_id, "Zoe")
    build = _channel(server_client, BUILD)
    _set_mode(seeded_engine, build["id"], zoe, "mute")
    r = server_client.post(
        f"/api/channels/{build['id']}/messages", json={"body": "@Zoe 看下"}
    )
    assert r.status_code == 201, r.text
    assert _activities(seeded_engine, zoe) == []


def test_all_and_mentions_backend_unchanged(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """mode=all / mentions → mention activity 照常生成（后端不变，差异全在前端）。"""
    ws_id = _workspace_id(server_client)
    build = _channel(server_client, BUILD)
    zoe = _add_human(seeded_engine, ws_id, "Zoe")
    _set_mode(seeded_engine, build["id"], zoe, "mentions")
    r = server_client.post(f"/api/channels/{build['id']}/messages", json={"body": "@Zoe A"})
    assert r.status_code == 201, r.text
    za = _activities(seeded_engine, zoe)
    assert len(za) == 1 and za[0]["kind"] == "mention"
    ann = _add_human(seeded_engine, ws_id, "Ann")
    _set_mode(seeded_engine, build["id"], ann, "all")
    r2 = server_client.post(f"/api/channels/{build['id']}/messages", json={"body": "@Ann B"})
    assert r2.status_code == 201, r2.text
    aa = _activities(seeded_engine, ann)
    assert len(aa) == 1 and aa[0]["kind"] == "mention"


def test_no_setting_row_generates_activity(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """无设置行 = 默认 all → mention activity 照常生成（懒建不改事实）。"""
    ws_id = _workspace_id(server_client)
    zoe = _add_human(seeded_engine, ws_id, "Zoe")
    build = _channel(server_client, BUILD)
    r = server_client.post(f"/api/channels/{build['id']}/messages", json={"body": "@Zoe hi"})
    assert r.status_code == 201, r.text
    assert len(_activities(seeded_engine, zoe)) == 1


def test_dm_activity_always_generated_ignores_mute(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """dm activity 恒生成：对端在该 DM 有 mute 行（直插绕过 422）仍落 dm activity（DM 必达）。"""
    ws_id = _workspace_id(server_client)
    owner = _member(server_client, "Memcyo")
    zoe = _add_human(seeded_engine, ws_id, "Zoe")
    dm = _make_dm(seeded_engine, ws_id, [owner["id"], zoe], "dm-notif-zoe")
    _set_mode(seeded_engine, dm, zoe, "mute")  # 门不作用 dm 分支
    r = server_client.post(f"/api/channels/{dm}/messages", json={"body": "私聊"})
    assert r.status_code == 201, r.text
    acts = _activities(seeded_engine, zoe)
    assert len(acts) == 1 and acts[0]["kind"] == "dm"


def test_mute_via_endpoint_suppresses_owner_mention(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """端到端：Owner PUT mute build → Agent @Owner 不进 Owner 的 Activity（端点 + 门闭环）。"""
    build = _channel(server_client, BUILD)
    owner = _member(server_client, "Memcyo")
    server_client.put(
        f"/api/channels/{build['id']}/notification-setting", json={"mode": "mute"}
    )
    headers = _agent_headers(seeded_engine, "Pat")
    r = server_client.post(
        f"/api/channels/{build['id']}/messages",
        json={"body": "@Memcyo 请看"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    assert _activities(seeded_engine, owner["id"]) == []


# ---------------------------------------------------------------- 快照字段


def test_snapshot_notification_settings_field(server_client: TestClient) -> None:
    """ChannelsSnapshot.notification_settings = 本人非默认行；PUT 后出现，回 all 后消失。"""
    build = _channel(server_client, BUILD)
    owner = _member(server_client, "Memcyo")
    snap0 = server_client.get("/api/channels").json()
    rest.ChannelsSnapshot.model_validate(snap0)
    assert snap0["notification_settings"] == []
    server_client.put(
        f"/api/channels/{build['id']}/notification-setting", json={"mode": "mute"}
    )
    snap1 = server_client.get("/api/channels").json()
    rest.ChannelsSnapshot.model_validate(snap1)
    ns = snap1["notification_settings"]
    assert len(ns) == 1
    assert ns[0]["channel_id"] == build["id"]
    assert ns[0]["member_id"] == owner["id"]
    assert ns[0]["mode"] == "mute"
    server_client.put(
        f"/api/channels/{build['id']}/notification-setting", json={"mode": "all"}
    )
    snap2 = server_client.get("/api/channels").json()
    assert snap2["notification_settings"] == []
