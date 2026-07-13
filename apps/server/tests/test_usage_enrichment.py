"""C6 usage 任务归属富化测试（契约 E §7.4）。

server 落库 usage 事件时，若上行 thread_root_id 命中 tasks.root_message_id → 写 task_id
（不落 thread_root_id 列）。三路：无提示→NULL；有提示无匹配→NULL；命中→task.id。
另验 GET /tasks/{id} 的 usage 聚合与 token_usage.reported 广播 payload 的 task_id。

驱动方式同 test_daemon.py：真 server（空库）+ StubDaemon 连 /api/daemon/ws，网关侧全链路走真码。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from coagentia_server.app import create_app
from coagentia_server.db import models
from coagentia_server.ledger.service import new_ulid, now_iso
from daemon_helpers import AUTH, Env, StubDaemon
from fastapi.testclient import TestClient
from perf_helpers import count_queries
from sqlalchemy import select
from sqlalchemy.engine import Engine

DAEMON_WS = "/api/daemon/ws"
_USAGE = models.TokenUsageEvent.__table__


@pytest.fixture
def ctx(migrated_engine: Engine, tmp_path: Path) -> Iterator[tuple[TestClient, Env, Any]]:
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


def _usage_task_id(engine: Engine, usage_id: str) -> str | None:
    with engine.connect() as c:
        return c.execute(
            select(_USAGE.c.task_id).where(_USAGE.c.id == usage_id)
        ).scalar_one()


def _new_task(client: TestClient, channel_id: str, body: str = "b") -> dict[str, Any]:
    """经 REST as_task 建任务，返回 {"message", "task"}（task.root_message_id == message.id）。"""
    r = client.post(
        f"/api/channels/{channel_id}/messages",
        json={"body": body, "as_task": {"title": "t"}},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _usage_event(agent_id: str, thread_root_id: str | None, tokens: int = 10) -> dict[str, Any]:
    return {
        "id": new_ulid(),
        "agent_member_id": agent_id,
        "thread_root_id": thread_root_id,
        "input_tokens": tokens,
        "output_tokens": tokens * 2,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "reported_at": now_iso(),
    }


# ---------------------------------------------------------------- 命中：thread_root_id → task_id


def test_usage_enrichment_hit_writes_task_id_and_aggregates(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    client, env, _hub = ctx
    a = env.add_agent("A", "offline")
    ch = env.add_channel(kind="channel", name="build")
    env.join(ch, a)
    env.join(ch, env.owner_id)
    created = _new_task(client, ch)
    task_id = created["task"]["id"]
    root_message_id = created["message"]["id"]
    assert created["task"]["root_message_id"] == root_message_id

    ev = _usage_event(a, thread_root_id=root_message_id)
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(a, "offline")])
        d.recv_hello_ack()
        fid = d.report("usage.batch", {"events": [ev]})
        ack = d.recv()
        assert ack["kind"] == "ack" and ack["ref"] == fid
    # 落库富化：该行 task_id == task.id。
    assert _usage_task_id(env.engine, ev["id"]) == task_id
    # TaskDetail.usage 聚合出正确 token 数（tokens=10 → input 10, output 20）。
    detail = client.get(f"/api/tasks/{task_id}").json()
    assert detail["usage"] == {
        "input_tokens": 10,
        "output_tokens": 20,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "events": 1,
    }


# ---------------------------------------------------------------- 未命中：提示无匹配 → NULL


def test_usage_enrichment_miss_leaves_task_id_null(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    a = env.add_agent("A", "offline")
    ev = _usage_event(a, thread_root_id=new_ulid())  # 不存在的 message id
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(a, "offline")])
        d.recv_hello_ack()
        fid = d.report("usage.batch", {"events": [ev]})
        ack = d.recv()
        assert ack["kind"] == "ack" and ack["ref"] == fid
    assert _usage_task_id(env.engine, ev["id"]) is None


# ------------------------------------------------------------ 缺提示：thread_root_id=None → NULL


def test_usage_enrichment_no_hint_leaves_task_id_null(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    client, env, _hub = ctx
    a = env.add_agent("A", "offline")
    ev = _usage_event(a, thread_root_id=None)
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(a, "offline")])
        d.recv_hello_ack()
        fid = d.report("usage.batch", {"events": [ev]})
        ack = d.recv()
        assert ack["kind"] == "ack" and ack["ref"] == fid
    assert _usage_task_id(env.engine, ev["id"]) is None


# ---------------------------------------------------------------- 富化提示但消息非任务根 → NULL


def test_usage_enrichment_non_task_root_message_null(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    client, env, _hub = ctx
    a = env.add_agent("A", "offline")
    ch = env.add_channel(kind="channel", name="build")
    # 普通消息（未转任务）：其 id 不命中 tasks.root_message_id。
    mid = env.add_message(ch, author=env.owner_id, body="plain")
    ev = _usage_event(a, thread_root_id=mid)
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(a, "offline")])
        d.recv_hello_ack()
        fid = d.report("usage.batch", {"events": [ev]})
        ack = d.recv()
        assert ack["kind"] == "ack" and ack["ref"] == fid
    assert _usage_task_id(env.engine, ev["id"]) is None


# ---------------------------------------------------------------- K7：批查归属/去重（非 N+1）


def test_usage_batch_query_count_is_constant(ctx: tuple[TestClient, Env, Any]) -> None:
    """K7 site 2：usage.batch 落库——去重存在性 + thread_root_id 归属各批查一次（非逐事件 N 查）。

    4 事件命中 4 个不同任务：归属 SELECT（root_message_id IN）恰 1 条、去重 SELECT
    （token_usage_events IN）恰 1 条；INSERT 仍 per-event（合法 O(n) 写）。落库富化逐字不变。
    """
    client, env, _hub = ctx
    a = env.add_agent("A", "offline")
    ch = env.add_channel(kind="channel", name="build")
    env.join(ch, a)
    env.join(ch, env.owner_id)
    tasks = [_new_task(client, ch, body=f"t{i}") for i in range(4)]
    events = [_usage_event(a, thread_root_id=t["message"]["id"]) for t in tasks]

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(a, "offline")])
        d.recv_hello_ack()
        with count_queries(env.engine) as q:
            fid = d.report("usage.batch", {"events": events})
            ack = d.recv()
            assert ack["kind"] == "ack" and ack["ref"] == fid

    attr = [s for s in q.dml if s.upper().startswith("SELECT") and "root_message_id" in s]
    assert len(attr) == 1, attr  # 归属批查恰一次（旧码 = 4 次）
    exist = [s for s in q.dml if s.upper().startswith("SELECT") and "token_usage_events" in s]
    assert len(exist) == 1, exist  # 去重存在性批查恰一次（旧码 = 4 次）
    # 全部事件落库 + 归属富化逐字不变。
    for ev, t in zip(events, tasks, strict=True):
        assert _usage_task_id(env.engine, ev["id"]) == t["task"]["id"]


# ---------------------------------------------------------------- WS：广播 payload task_id 富化


def test_usage_enrichment_broadcast_carries_task_id(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    client, env, _hub = ctx
    a = env.add_agent("A", "offline")
    ch = env.add_channel(kind="channel", name="build")
    env.join(ch, a)
    env.join(ch, env.owner_id)
    created = _new_task(client, ch)
    task_id = created["task"]["id"]
    root_message_id = created["message"]["id"]

    ev = _usage_event(a, thread_root_id=root_message_id)
    with client.websocket_connect("/api/ws") as browser:
        assert browser.receive_json()["type"] == "sys.hello"
        with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
            d = StubDaemon(ws)
            d.hello([(a, "offline")])
            d.recv_hello_ack()
            d.report("usage.batch", {"events": [ev]})
            # token_usage.reported（channel_id=None 全局广播）payload.task_id == 富化值。
            found: dict[str, Any] | None = None
            for _ in range(20):
                frame = browser.receive_json()
                if frame["type"] == "token_usage.reported":
                    found = frame
                    break
            assert found is not None, "未收到 token_usage.reported 广播"
            assert found["data"]["task_id"] == task_id
            assert found["data"]["agent_member_id"] == a
