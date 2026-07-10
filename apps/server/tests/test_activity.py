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


# ---- WS 辅助


def _drain_hello(sock) -> None:  # noqa: ANN001
    sock.receive_json()  # sys.hello（+ 真 server 可能的 owner online）


def _drain_until(sock, target: EventType) -> dict:  # noqa: ANN001
    for _ in range(12):
        raw = sock.receive_json()
        if raw.get("type") == target.value:
            return raw
    raise AssertionError(f"未在限帧内收到 {target}")
