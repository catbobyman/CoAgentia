"""置 in_review 自动通知创建者（DEDAG 挂账 ⑥ 机制化；铺开 R2 实测教训）。

交付唤醒不依赖话术遵从：POST /tasks/{id}/status 落 in_review 时，任务线程落一条 durable
系统消息 + @创建者 mention 行（system+mention 对 Agent 视同唤醒触发，契约 D §8.2；对人类
走既有 mention 可见面——沉默提醒同款范式）。本套只测 REST 面行为（消息/mention/广播/
跳过分支）；唤醒投递归 hub _compute_trigger 既有覆盖。

范式照抄 test_tasks_state_machine.py（真 server TestClient / seeded_engine 旁路读库）+
test_tasks.py `_agent_headers`（契约 B §2 双头代理 Agent 身份）。
"""

from __future__ import annotations

import hashlib
from typing import Any

from coagentia_contracts.enums import MessageKind
from coagentia_contracts.ws import EventType
from coagentia_server.db import models
from coagentia_server.events import PendingEvent
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.engine import Engine

BUILD = "build"
AGENT_TEST_KEY = "cak_review_notify_test"

_TASK = models.Task.__table__
_MSG = models.Message.__table__
_MENTION = models.MessageMention.__table__
_MEMBER = models.Member.__table__
_READ = models.ReadPosition.__table__


def _channel(client: TestClient, name: str) -> dict:
    return next(c for c in client.get("/api/channels").json()["items"] if c["name"] == name)


def _member(client: TestClient, name: str) -> dict:
    return next(m for m in client.get("/api/members").json() if m["name"] == name)


def _agent_headers(engine: Engine, member_id: str) -> dict[str, str]:
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


def _mark_all_read(engine: Engine, member_id: str, channel_id: str) -> None:
    """把 Agent 在频道置为已读——绕开 freshness 门（G1），本套不测 held 域。"""
    with engine.begin() as conn:
        last = conn.execute(
            select(_MSG.c.id)
            .where(_MSG.c.channel_id == channel_id)
            .order_by(_MSG.c.id.desc())
            .limit(1)
        ).scalar_one()
        existing = conn.execute(
            select(_READ.c.member_id).where(
                _READ.c.member_id == member_id, _READ.c.channel_id == channel_id
            )
        ).first()
        values = {"last_read_message_id": last, "last_read_at": "2026-01-01T00:00:00.000Z"}
        if existing is None:
            conn.execute(
                _READ.insert().values(member_id=member_id, channel_id=channel_id, **values)
            )
        else:
            conn.execute(
                update(_READ)
                .where(_READ.c.member_id == member_id, _READ.c.channel_id == channel_id)
                .values(**values)
            )


def _new_task(
    client: TestClient, channel_id: str, headers: dict[str, str] | None = None
) -> dict:
    r = client.post(
        f"/api/channels/{channel_id}/messages",
        json={"body": "b", "as_task": {"title": "t"}},
        headers=headers or {},
    )
    assert r.status_code == 201, r.text
    return r.json()["task"]


def _root_message_id(engine: Engine, task_id: str) -> str:
    with engine.connect() as conn:
        return conn.execute(
            select(_TASK.c.root_message_id).where(_TASK.c.id == task_id)
        ).scalar_one()


def _notify_messages(engine: Engine, root_id: str) -> list[dict[str, Any]]:
    """任务线程内的待验收系统消息（kind=system + 关键词），按 created_at 序。"""
    with engine.connect() as conn:
        rows = conn.execute(
            select(_MSG)
            .where(_MSG.c.thread_root_id == root_id, _MSG.c.kind == MessageKind.SYSTEM.value)
            .order_by(_MSG.c.created_at)
        ).mappings()
        return [dict(r) for r in rows if "待验收" in r["body"]]


def _mentioned_ids(engine: Engine, message_id: str) -> set[str]:
    with engine.connect() as conn:
        return {
            r[0]
            for r in conn.execute(
                select(_MENTION.c.member_id).where(_MENTION.c.message_id == message_id)
            )
        }


def _to_in_review(client: TestClient, task_id: str, headers: dict[str, str]) -> None:
    for to in ("in_progress", "in_review"):
        r = client.post(f"/api/tasks/{task_id}/status", json={"to": to}, headers=headers)
        assert r.status_code == 200, (to, r.text)


# ---- 主路径：Agent 交付他人创建的任务 → 系统消息 + @创建者 mention + 广播 -----------


def test_agent_delivery_notifies_human_creator(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    build = _channel(server_client, BUILD)["id"]
    owner = _member(server_client, "Memcyo")
    pat = _member(server_client, "Pat")
    task = _new_task(server_client, build)  # 创建者 = 浏览器 Owner（Memcyo）
    root_id = _root_message_id(seeded_engine, task["id"])
    headers = _agent_headers(seeded_engine, pat["id"])

    captured: list[PendingEvent] = []
    server_client.app.state.bus.subscribe(captured.append)
    _to_in_review(server_client, task["id"], headers)

    msgs = _notify_messages(seeded_engine, root_id)
    assert len(msgs) == 1, msgs
    msg = msgs[0]
    assert msg["author_member_id"] is None  # durable 系统消息
    assert f"#{task['number']}" in msg["body"] and "@Memcyo" in msg["body"]
    assert _mentioned_ids(seeded_engine, msg["id"]) == {owner["id"]}  # 恰 @ 创建者一人
    # 提交后广播 message.created（bus 驱动 hub 投递引擎；前端线程实时刷新同源）。
    created = [
        e
        for e in captured
        if e.type is EventType.MESSAGE_CREATED and e.data["message"]["id"] == msg["id"]
    ]
    assert len(created) == 1


def test_agent_delivery_notifies_agent_creator(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """Agent 创建（Orchestrator create_task 同道）→ 另一 Agent 交付 → @创建者 Agent。"""
    build = _channel(server_client, BUILD)["id"]
    orch = _member(server_client, "Orchestrator")
    hank = _member(server_client, "Hank")
    _mark_all_read(seeded_engine, orch["id"], build)  # 绕开 freshness 门（非本套被测面）
    task = _new_task(
        server_client, build, headers=_agent_headers(seeded_engine, orch["id"])
    )
    root_id = _root_message_id(seeded_engine, task["id"])

    _to_in_review(server_client, task["id"], _agent_headers(seeded_engine, hank["id"]))

    msgs = _notify_messages(seeded_engine, root_id)
    assert len(msgs) == 1, msgs
    assert "@Orchestrator" in msgs[0]["body"]
    assert _mentioned_ids(seeded_engine, msgs[0]["id"]) == {orch["id"]}


# ---- 跳过分支：自己交付自己创建的 / 创建者已移除 ------------------------------------


def test_self_delivery_skips_notification(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    build = _channel(server_client, BUILD)["id"]
    task = _new_task(server_client, build)  # 创建者 = Owner，流转者也是 Owner
    root_id = _root_message_id(seeded_engine, task["id"])

    _to_in_review(server_client, task["id"], headers={})

    assert _notify_messages(seeded_engine, root_id) == []


def test_removed_creator_skips_silently(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    build = _channel(server_client, BUILD)["id"]
    owner = _member(server_client, "Memcyo")
    pat = _member(server_client, "Pat")
    task = _new_task(server_client, build)
    root_id = _root_message_id(seeded_engine, task["id"])
    with seeded_engine.begin() as conn:
        conn.execute(
            update(_MEMBER)
            .where(_MEMBER.c.id == owner["id"])
            .values(removed_at="2026-01-01T00:00:00.000Z")
        )

    _to_in_review(server_client, task["id"], _agent_headers(seeded_engine, pat["id"]))

    # 流转本身成功（200 已在 helper 内断言），仅通知静默跳过。
    assert _notify_messages(seeded_engine, root_id) == []


# ---- 重交付 / 同态幂等 --------------------------------------------------------------


def test_redelivery_notifies_each_transition_and_same_state_does_not(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    build = _channel(server_client, BUILD)["id"]
    pat = _member(server_client, "Pat")
    task = _new_task(server_client, build)
    root_id = _root_message_id(seeded_engine, task["id"])
    headers = _agent_headers(seeded_engine, pat["id"])

    _to_in_review(server_client, task["id"], headers)
    # 同态重发：幂等短路，不重复通知。
    r = server_client.post(
        f"/api/tasks/{task['id']}/status", json={"to": "in_review"}, headers=headers
    )
    assert r.status_code == 200
    assert len(_notify_messages(seeded_engine, root_id)) == 1
    # 退回返工再交付：每次成功转换各通知一次。
    r = server_client.post(
        f"/api/tasks/{task['id']}/status", json={"to": "in_progress"}, headers=headers
    )
    assert r.status_code == 200
    r = server_client.post(
        f"/api/tasks/{task['id']}/status", json={"to": "in_review"}, headers=headers
    )
    assert r.status_code == 200
    assert len(_notify_messages(seeded_engine, root_id)) == 2
