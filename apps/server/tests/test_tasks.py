"""任务域真 server 专属测试（C2）：建号、并发 claim 恰一成功、逐边状态机、留痕、TaskDetail 聚合。

mock 无业务逻辑（纪律 4），故建号/竞态/边表/幂等只在真 server 断言。形状/结构化拒绝的双跑
见 test_conformance_dual.py。
"""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor

import pytest
from coagentia_contracts import rest
from coagentia_contracts.constants import TASK_TRANSITIONS
from coagentia_contracts.enums import TaskStatus
from coagentia_contracts.ws import EventType
from coagentia_server.db import models
from coagentia_server.events import PendingEvent
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, text, update
from sqlalchemy.engine import Engine

BUILD = "build"
RESEARCH = "research"
AGENT_TEST_KEY = "cak_rest_agent_test"

_TASK = models.Task.__table__
_EVT = models.TaskEvent.__table__
_TUE = models.TokenUsageEvent.__table__


def _agent_headers(engine: Engine, member_id: str) -> dict[str, str]:
    """给 seed Agent 所属 Computer 注入已知测试 key，返回契约 B §2 双头（同 test_server_api）。"""
    digest = hashlib.sha256(AGENT_TEST_KEY.encode()).hexdigest()
    with engine.begin() as conn:
        computer_id = conn.execute(
            select(models.Agent.__table__.c.computer_id).where(
                models.Agent.__table__.c.member_id == member_id
            )
        ).scalar_one()
        conn.execute(
            update(models.Computer.__table__)
            .where(models.Computer.__table__.c.id == computer_id)
            .values(api_key_hash=digest)
        )
    return {"Authorization": f"Bearer {AGENT_TEST_KEY}", "X-Acting-Member": member_id}


def _channel(client: TestClient, name: str) -> dict:
    return next(c for c in client.get("/api/channels").json()["items"] if c["name"] == name)


def _member(client: TestClient, name: str) -> dict:
    return next(m for m in client.get("/api/members").json() if m["name"] == name)


def _new_task(client: TestClient, channel_id: str, title: str = "t", body: str = "b") -> dict:
    r = client.post(
        f"/api/channels/{channel_id}/messages", json={"body": body, "as_task": {"title": title}}
    )
    assert r.status_code == 201, r.text
    return r.json()["task"]


# ---------------------------------------------------------------- 建号（B §9.3.1）


def test_number_autoincrement_per_channel(server_client: TestClient) -> None:
    build = _channel(server_client, BUILD)
    research = _channel(server_client, RESEARCH)
    n1 = _new_task(server_client, build["id"], "a")["number"]
    n2 = _new_task(server_client, build["id"], "b")["number"]
    n3 = _new_task(server_client, build["id"], "c")["number"]
    assert [n2 - n1, n3 - n2] == [1, 1]  # 频道内严格 +1
    # 另一频道独立序（不共享全局计数）
    r1 = _new_task(server_client, research["id"], "r1")["number"]
    r2 = _new_task(server_client, research["id"], "r2")["number"]
    assert r2 - r1 == 1


def test_default_title_strips_markdown(server_client: TestClient) -> None:
    build = _channel(server_client, BUILD)
    r = server_client.post(
        f"/api/channels/{build['id']}/messages",
        json={"body": "## 修登录\n更多细节", "as_task": {}},
    )
    assert r.json()["task"]["title"] == "修登录"  # 首非空行剥 MD 前缀
    # 小数/版本号开头不得被有序号前缀 `\d+\.` 误剥（`3.14` != 有序列表 `1. `）。
    r2 = server_client.post(
        f"/api/channels/{build['id']}/messages",
        json={"body": "3.14 是圆周率", "as_task": {}},
    )
    assert r2.json()["task"]["title"] == "3.14 是圆周率"


# ---------------------------------------------------------------- convert 幂等（root_message_id）


def test_convert_idempotent_returns_same_task(server_client: TestClient) -> None:
    build = _channel(server_client, BUILD)
    msg = server_client.post(
        f"/api/channels/{build['id']}/messages", json={"body": "顶级"}
    ).json()["message"]
    r1 = server_client.post(f"/api/messages/{msg['id']}/task", json={"title": "首次"})
    r2 = server_client.post(f"/api/messages/{msg['id']}/task", json={"title": "重放"})
    assert r1.status_code == 201 and r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]  # 幂等返回既有
    assert r2.json()["title"] == "首次"  # 不覆盖


def test_convert_thread_reply_rejected(server_client: TestClient) -> None:
    build = _channel(server_client, BUILD)
    root = server_client.post(
        f"/api/channels/{build['id']}/messages", json={"body": "root"}
    ).json()["message"]
    reply = server_client.post(
        f"/api/channels/{build['id']}/messages",
        json={"body": "reply", "thread_root_id": root["id"]},
    ).json()["message"]
    r = server_client.post(f"/api/messages/{reply['id']}/task", json={})
    assert r.status_code == 422
    assert rest.ErrorResponse.model_validate(r.json()).error.code is (
        rest.ErrorCode.NOT_TOP_LEVEL_MESSAGE
    )


def test_convert_in_dm_rejected(server_client: TestClient) -> None:
    dm = next(c for c in server_client.get("/api/channels").json()["items"] if c["kind"] == "dm")
    msgs = server_client.get(f"/api/channels/{dm['id']}/messages").json()["items"]
    if not msgs:  # DM 无消息则先发一条
        m = server_client.post(f"/api/channels/{dm['id']}/messages", json={"body": "hi"}).json()
        mid = m["message"]["id"]
    else:
        mid = msgs[0]["id"]
    r = server_client.post(f"/api/messages/{mid}/task", json={})
    assert r.status_code == 422
    assert rest.ErrorResponse.model_validate(r.json()).error.code is rest.ErrorCode.TASK_IN_DM


# ---------------------------------------------------------------- claim 并发恰一成功（T2）


def test_concurrent_claim_exactly_one(server_client: TestClient) -> None:
    build = _channel(server_client, BUILD)
    task = _new_task(server_client, build["id"], "race")

    def _claim(_: int) -> int:
        return server_client.post(f"/api/tasks/{task['id']}/claim").status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(_claim, range(8)))

    assert codes.count(200) == 1, codes
    assert codes.count(409) == 7, codes
    owner = _member(server_client, "Memcyo")["id"]
    final = server_client.get(f"/api/tasks/{task['id']}").json()["task"]
    assert final["owner_member_id"] == owner
    # 败者携带 current_owner
    loser = server_client.post(f"/api/tasks/{task['id']}/claim")
    assert loser.status_code == 409
    err = rest.ErrorResponse.model_validate(loser.json())
    assert err.error.code is rest.ErrorCode.CLAIM_RACE
    assert err.error.details["current_owner"] == owner


def test_claim_links_todo_to_in_progress(server_client: TestClient) -> None:
    build = _channel(server_client, BUILD)
    task = _new_task(server_client, build["id"])
    r = server_client.post(f"/api/tasks/{task['id']}/claim")
    assert r.status_code == 200
    assert r.json()["status"] == "in_progress"  # 联动（裁决 1）


def test_unclaim_links_back_and_only_owner(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    build = _channel(server_client, BUILD)
    task = _new_task(server_client, build["id"])
    server_client.post(f"/api/tasks/{task['id']}/claim")

    # 他人（Pat Agent 主体）unclaim → 403
    pat = _member(server_client, "Pat")
    headers = _agent_headers(seeded_engine, pat["id"])
    forbidden = server_client.post(f"/api/tasks/{task['id']}/unclaim", headers=headers)
    assert forbidden.status_code == 403
    assert rest.ErrorResponse.model_validate(forbidden.json()).error.code is (
        rest.ErrorCode.PERMISSION_DENIED
    )

    # 本人 unclaim → owner 置空 + 联动回 todo
    r = server_client.post(f"/api/tasks/{task['id']}/unclaim")
    assert r.status_code == 200
    assert r.json()["owner_member_id"] is None and r.json()["status"] == "todo"


# ---------------------------------------------------------------- assign（不动 status）


def test_assign_no_status_change_and_null_clears(server_client: TestClient) -> None:
    build = _channel(server_client, BUILD)
    task = _new_task(server_client, build["id"])
    pat = _member(server_client, "Pat")
    r = server_client.post(f"/api/tasks/{task['id']}/assign", json={"member_id": pat["id"]})
    assert r.status_code == 200
    assert r.json()["owner_member_id"] == pat["id"]
    assert r.json()["status"] == "todo"  # assign 不联动 status
    # 取消指派
    r2 = server_client.post(f"/api/tasks/{task['id']}/assign", json={"member_id": None})
    assert r2.json()["owner_member_id"] is None and r2.json()["status"] == "todo"


def test_assign_unknown_member_404(server_client: TestClient) -> None:
    build = _channel(server_client, BUILD)
    task = _new_task(server_client, build["id"])
    r = server_client.post(
        f"/api/tasks/{task['id']}/assign", json={"member_id": "01JZKJ7GG0000000000000000X"}
    )
    assert r.status_code == 404


# ---------------------------------------------------------------- 状态机逐边（T4）

_LEGAL_EDGES = [(src, dst) for src, dsts in TASK_TRANSITIONS.items() for dst in dsts]
_ALL_PAIRS = [(a, b) for a in TaskStatus for b in TaskStatus if a != b]
_ILLEGAL_EDGES = [p for p in _ALL_PAIRS if p not in _LEGAL_EDGES]


def _force_status(engine: Engine, task_id: str, status: TaskStatus) -> None:
    with engine.begin() as conn:
        conn.execute(update(_TASK).where(_TASK.c.id == task_id).values(status=status.value))


@pytest.mark.parametrize("src,dst", _LEGAL_EDGES)
def test_every_legal_edge_ok(
    server_client: TestClient, seeded_engine: Engine, src: TaskStatus, dst: TaskStatus
) -> None:
    build = _channel(server_client, BUILD)
    task = _new_task(server_client, build["id"])
    _force_status(seeded_engine, task["id"], src)
    r = server_client.post(f"/api/tasks/{task['id']}/status", json={"to": dst.value})
    assert r.status_code == 200, (src, dst, r.text)
    assert r.json()["status"] == dst.value


@pytest.mark.parametrize("src,dst", _ILLEGAL_EDGES)
def test_every_illegal_edge_422(
    server_client: TestClient, seeded_engine: Engine, src: TaskStatus, dst: TaskStatus
) -> None:
    build = _channel(server_client, BUILD)
    task = _new_task(server_client, build["id"])
    _force_status(seeded_engine, task["id"], src)
    r = server_client.post(f"/api/tasks/{task['id']}/status", json={"to": dst.value})
    assert r.status_code == 422, (src, dst, r.text)
    err = rest.ErrorResponse.model_validate(r.json())
    assert err.error.code is rest.ErrorCode.TASK_TRANSITION_INVALID
    assert err.error.details["from"] == src.value and err.error.details["to"] == dst.value


def test_idempotent_same_status_no_event(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    build = _channel(server_client, BUILD)
    task = _new_task(server_client, build["id"])  # todo
    r = server_client.post(f"/api/tasks/{task['id']}/status", json={"to": "todo"})
    assert r.status_code == 200 and r.json()["status"] == "todo"
    with seeded_engine.connect() as conn:
        n = conn.execute(
            select(_EVT).where(_EVT.c.task_id == task["id"])
        ).fetchall()
    assert n == []  # 同态不写事件、不广播


def test_done_is_terminal(server_client: TestClient, seeded_engine: Engine) -> None:
    build = _channel(server_client, BUILD)
    task = _new_task(server_client, build["id"])
    _force_status(seeded_engine, task["id"], TaskStatus.DONE)
    for target in ("todo", "in_progress", "in_review", "closed"):
        r = server_client.post(f"/api/tasks/{task['id']}/status", json={"to": target})
        assert r.status_code == 422


# ---------------------------------------------------------------- 留痕矩阵（T5）+ 不可变触发器


def test_task_events_ledger(server_client: TestClient, seeded_engine: Engine) -> None:
    build = _channel(server_client, BUILD)
    task = _new_task(server_client, build["id"])
    me = _member(server_client, "Memcyo")["id"]
    server_client.post(f"/api/tasks/{task['id']}/claim")  # claim + 联动 status_change
    server_client.post(f"/api/tasks/{task['id']}/unclaim")  # unclaim + 联动 status_change

    with seeded_engine.connect() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                select(_EVT).where(_EVT.c.task_id == task["id"]).order_by(_EVT.c.seq)
            ).mappings()
        ]
    kinds = [r["kind"] for r in rows]
    assert kinds == ["claim", "status_change", "unclaim", "status_change"]
    # claim 行携带新 owner
    assert rows[0]["owner_member_id"] == me and rows[0]["actor_member_id"] == me
    # 联动 status_change 行
    assert (rows[1]["from_status"], rows[1]["to_status"]) == ("todo", "in_progress")
    assert (rows[3]["from_status"], rows[3]["to_status"]) == ("in_progress", "todo")
    assert rows[2]["owner_member_id"] is None  # unclaim 置空


def test_task_events_immutable(server_client: TestClient, seeded_engine: Engine) -> None:
    build = _channel(server_client, BUILD)
    task = _new_task(server_client, build["id"])
    server_client.post(f"/api/tasks/{task['id']}/status", json={"to": "closed"})
    import sqlalchemy

    with pytest.raises((sqlalchemy.exc.IntegrityError, sqlalchemy.exc.OperationalError)):
        with seeded_engine.begin() as conn:
            conn.execute(text("UPDATE task_events SET kind='claim' WHERE task_id=:t"),
                         {"t": task["id"]})
    with pytest.raises((sqlalchemy.exc.IntegrityError, sqlalchemy.exc.OperationalError)):
        with seeded_engine.begin() as conn:
            conn.execute(text("DELETE FROM task_events WHERE task_id=:t"), {"t": task["id"]})


# ---------------------------------------------------------------- TaskDetail usage 聚合


def test_task_detail_usage_zero_then_aggregates(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    build = _channel(server_client, BUILD)
    task = _new_task(server_client, build["id"])
    detail = server_client.get(f"/api/tasks/{task['id']}").json()
    assert detail["usage"] == {
        "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
        "cache_write_tokens": 0, "events": 0,
    }
    # 手插两条 token_usage_events 归属该 task
    ws_id = server_client.get("/api/workspace").json()["id"]
    agent = _member(server_client, "Pat")["id"]
    from coagentia_server.ledger import service

    with seeded_engine.begin() as conn:
        for i in range(2):
            conn.execute(
                insert(_TUE).values(
                    id=service.new_ulid(), workspace_id=ws_id, agent_member_id=agent,
                    task_id=task["id"], channel_id=build["id"],
                    input_tokens=10, output_tokens=5, cache_read_tokens=2, cache_write_tokens=1,
                    source_session=f"s{i}", reported_at=service.now_iso(),
                )
            )
    detail = server_client.get(f"/api/tasks/{task['id']}").json()
    assert detail["usage"] == {
        "input_tokens": 20, "output_tokens": 10, "cache_read_tokens": 4,
        "cache_write_tokens": 2, "events": 2,
    }


# ---------------------------------------------------------------- PATCH + 广播


def test_patch_title_broadcasts(server_client: TestClient) -> None:
    build = _channel(server_client, BUILD)
    task = _new_task(server_client, build["id"])
    events: list[PendingEvent] = []
    server_client.app.state.bus.subscribe(events.append)
    r = server_client.patch(f"/api/tasks/{task['id']}", json={"title": "改后标题"})
    assert r.status_code == 200 and r.json()["title"] == "改后标题"
    updated = [e for e in events if e.type is EventType.TASK_UPDATED]
    assert len(updated) == 1 and updated[0].data["change"] is None


def test_claim_broadcasts_single_task_updated(server_client: TestClient) -> None:
    """一动作一帧（设计裁决 D-WS）：claim + 联动只广播 1 条 task.updated。"""
    build = _channel(server_client, BUILD)
    task = _new_task(server_client, build["id"])
    events: list[PendingEvent] = []
    server_client.app.state.bus.subscribe(events.append)
    server_client.post(f"/api/tasks/{task['id']}/claim")
    updated = [e for e in events if e.type is EventType.TASK_UPDATED]
    assert len(updated) == 1
    change = updated[0].data["change"]
    assert change["kind"] == "claim"
    assert (change["from_status"], change["to_status"]) == ("todo", "in_progress")


