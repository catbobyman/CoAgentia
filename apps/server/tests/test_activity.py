"""C4 真 server：GET /activity（filter 三档 + 倒序分页）+ POST /activity/{id}/done（幂等 + 广播）。

自足：直接向 activity_items 插行（不依赖 messages.py 并行开发的 mention/dm 生成逻辑）。
"""

from __future__ import annotations

from coagentia_contracts import entities, rest
from coagentia_contracts.ws import EventType
from coagentia_server.db import models
from fastapi.testclient import TestClient
from sqlalchemy import insert, select
from sqlalchemy.engine import Engine

_ACT = models.ActivityItem.__table__
_MEMBER = models.Member.__table__
_WS = models.Workspace.__table__


def _owner_and_ws(engine: Engine) -> tuple[str, str]:
    with engine.connect() as conn:
        ws_id = conn.execute(select(_WS.c.id).limit(1)).scalar_one()
        owner = conn.execute(
            select(_MEMBER.c.id).where(_MEMBER.c.kind == "human", _MEMBER.c.role == "owner")
        ).scalar_one()
    return owner, ws_id


def _insert_activity(
    engine: Engine,
    ws_id: str,
    member_id: str,
    act_id: str,
    kind: str,
    created_at: str,
    done_at: str | None = None,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(_ACT).values(
                id=act_id,
                workspace_id=ws_id,
                member_id=member_id,
                kind=kind,
                channel_id=None,
                message_id=None,
                task_id=None,
                created_at=created_at,
                done_at=done_at,
            )
        )


def _seed_activities(engine: Engine) -> tuple[str, str, str]:
    """三条：mention/dm 未处理、system 已处理；created_at 递增。返回 (mention,dm,system) id。"""
    owner, ws_id = _owner_and_ws(engine)
    ids = (
        "01K0ACT0000000000000000001",
        "01K0ACT0000000000000000002",
        "01K0ACT0000000000000000003",
    )
    _insert_activity(engine, ws_id, owner, ids[0], "mention", "2026-07-09T10:00:00.000Z")
    _insert_activity(engine, ws_id, owner, ids[1], "dm", "2026-07-09T11:00:00.000Z")
    _insert_activity(
        engine, ws_id, owner, ids[2], "system", "2026-07-09T12:00:00.000Z",
        done_at="2026-07-09T12:30:00.000Z",
    )
    return ids


def test_activity_filters_all_unread_mentions(
    seeded_engine: Engine, server_client: TestClient
) -> None:
    mention_id, dm_id, system_id = _seed_activities(seeded_engine)

    all_page = rest.Page[entities.ActivityItemPublic].model_validate(
        server_client.get("/api/activity").json()
    )
    # 倒序：system(12:00) > dm(11:00) > mention(10:00)。
    assert [a.id for a in all_page.items] == [system_id, dm_id, mention_id]

    unread = rest.Page[entities.ActivityItemPublic].model_validate(
        server_client.get("/api/activity", params={"filter": "unread"}).json()
    )
    assert [a.id for a in unread.items] == [dm_id, mention_id]  # system 已 done → 排除
    assert all(a.done_at is None for a in unread.items)

    mentions = rest.Page[entities.ActivityItemPublic].model_validate(
        server_client.get("/api/activity", params={"filter": "mentions"}).json()
    )
    assert [a.id for a in mentions.items] == [mention_id]


def test_activity_cursor_pagination_desc(
    seeded_engine: Engine, server_client: TestClient
) -> None:
    mention_id, dm_id, system_id = _seed_activities(seeded_engine)
    first = server_client.get("/api/activity", params={"limit": 2}).json()
    assert [a["id"] for a in first["items"]] == [system_id, dm_id]
    assert first["next_cursor"] == dm_id
    second = server_client.get(
        "/api/activity", params={"limit": 2, "after": first["next_cursor"]}
    ).json()
    assert [a["id"] for a in second["items"]] == [mention_id]
    assert second["next_cursor"] is None


def test_activity_done_idempotent_and_broadcast(
    seeded_engine: Engine, server_client: TestClient
) -> None:
    mention_id, _dm_id, _system_id = _seed_activities(seeded_engine)
    with server_client.websocket_connect("/api/ws") as sock:
        _drain_hello(sock)
        r = server_client.post(f"/api/activity/{mention_id}/done")
        assert r.status_code == 200
        done = entities.ActivityItemPublic.model_validate(r.json())
        assert done.done_at is not None
        evt = _drain_until(sock, EventType.ACTIVITY_DONE)
        assert evt["data"]["item_id"] == mention_id

    # 幂等重放：done_at 不变、无异常。
    again = server_client.post(f"/api/activity/{mention_id}/done")
    assert again.status_code == 200
    assert again.json()["done_at"] == done.done_at

    # done 后从 unread 过滤中排除。
    unread = server_client.get("/api/activity", params={"filter": "unread"}).json()
    assert mention_id not in [a["id"] for a in unread["items"]]


def test_activity_done_missing_returns_404(server_client: TestClient) -> None:
    r = server_client.post("/api/activity/01K0MISSINGACT0000000000000/done")
    assert r.status_code == 404
    err = rest.ErrorResponse.model_validate(r.json())
    assert err.error.code is rest.ErrorCode.NOT_FOUND


def test_activity_done_scoped_to_owner(
    seeded_engine: Engine, server_client: TestClient
) -> None:
    """归属门（M2 二轮 review 修复）：非 Owner 的条目按不存在处理（404），行不被改动。

    修复前 done 端点不查归属也不走 acting_member——任何主体可清掉他人未读。
    """
    _owner, ws_id = _owner_and_ws(seeded_engine)
    with seeded_engine.connect() as conn:
        other = conn.execute(
            select(_MEMBER.c.id).where(_MEMBER.c.kind == "agent").limit(1)
        ).scalar_one()
    other_item = "01K0ACT0000000000000000009"
    _insert_activity(
        seeded_engine, ws_id, other, other_item, "mention", "2026-07-09T13:00:00.000Z"
    )
    r = server_client.post(f"/api/activity/{other_item}/done")
    assert r.status_code == 404
    with seeded_engine.connect() as conn:
        done_at = conn.execute(
            select(_ACT.c.done_at).where(_ACT.c.id == other_item)
        ).scalar_one()
    assert done_at is None

    # Agent 持 Computer Bearer 代理也不能清 Owner 的未读（acting_member 解析成 agent 成员
    # → 与人类条目归属不匹配 → 404）。
    import hashlib

    from sqlalchemy import update as sa_update

    key = "cak_activity_scope_test"
    with seeded_engine.begin() as conn:
        pat = conn.execute(
            select(_MEMBER.c.id).where(_MEMBER.c.name == "Pat")
        ).scalar_one()
        computer_id = conn.execute(
            select(models.Agent.__table__.c.computer_id).where(
                models.Agent.__table__.c.member_id == pat
            )
        ).scalar_one()
        conn.execute(
            sa_update(models.Computer.__table__)
            .where(models.Computer.__table__.c.id == computer_id)
            .values(api_key_hash=hashlib.sha256(key.encode()).hexdigest())
        )
    owner_item = "01K0ACT000000000000000000A"
    _insert_activity(
        seeded_engine, ws_id, _owner, owner_item, "mention", "2026-07-09T14:00:00.000Z"
    )
    r = server_client.post(
        f"/api/activity/{owner_item}/done",
        headers={"Authorization": f"Bearer {key}", "X-Acting-Member": pat},
    )
    assert r.status_code == 404
    with seeded_engine.connect() as conn:
        done_at = conn.execute(
            select(_ACT.c.done_at).where(_ACT.c.id == owner_item)
        ).scalar_one()
    assert done_at is None


def test_activity_actor_member_id_from_message_author(
    seeded_engine: Engine, server_client: TestClient
) -> None:
    """actor_member_id = 触发消息的作者（Public 派生字段，M2 二轮 review 修复）。

    Agent Pat @Memcyo → Owner 的 activity 项 actor=Pat、member_id=Owner。
    """
    import hashlib

    from sqlalchemy import update as sa_update

    key = "cak_activity_actor_test"
    with seeded_engine.begin() as conn:
        pat = conn.execute(
            select(_MEMBER.c.id).where(_MEMBER.c.name == "Pat")
        ).scalar_one()
        computer_id = conn.execute(
            select(models.Agent.__table__.c.computer_id).where(
                models.Agent.__table__.c.member_id == pat
            )
        ).scalar_one()
        conn.execute(
            sa_update(models.Computer.__table__)
            .where(models.Computer.__table__.c.id == computer_id)
            .values(api_key_hash=hashlib.sha256(key.encode()).hexdigest())
        )
    build = next(
        c
        for c in server_client.get("/api/channels").json()["items"]
        if c["name"] == "build"
    )
    r = server_client.post(
        f"/api/channels/{build['id']}/messages",
        json={"body": "@Memcyo 请验收 actor 字段"},
        headers={"Authorization": f"Bearer {key}", "X-Acting-Member": pat},
    )
    assert r.status_code == 201, r.text
    owner, _ws = _owner_and_ws(seeded_engine)
    page = rest.Page[entities.ActivityItemPublic].model_validate(
        server_client.get("/api/activity").json()
    )
    item = next(a for a in page.items if a.message_id == r.json()["message"]["id"])
    assert item.kind.value == "mention"
    assert item.member_id == owner  # 接收者
    assert item.actor_member_id == pat  # 行为人=消息作者


# ---- WS 辅助


def _drain_hello(sock) -> None:  # noqa: ANN001
    sock.receive_json()  # sys.hello（+ 真 server 可能的 owner online）


def _drain_until(sock, target: EventType) -> dict:  # noqa: ANN001
    for _ in range(12):
        raw = sock.receive_json()
        if raw.get("type") == target.value:
            return raw
    raise AssertionError(f"未在限帧内收到 {target}")
