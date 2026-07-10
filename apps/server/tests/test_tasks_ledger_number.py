"""C2 硬化：任务域账本留痕逐 kind 字段 + 同频道并发建号（真 server fixture）。

(a) claim/unclaim/assign/status_change 各自向不可变表 task_events 追加行，且逐 kind 字段
    正确（owner_member_id 在 claim/assign 落"新 owner"，unclaim/取消指派落 NULL，actor 恒为
    动作发起者）；unclaim 仅本人可，非 owner 尝试被 403 拒绝且不落任何事件行。
(b) 同一频道并发建多个任务：断言分配的 number 连续、无重复，且 UNIQUE(channel_id, number)
    约束确实成立（直接插重号 → IntegrityError）。

留痕/建号/竞态是纯服务端不变量（mock 无业务逻辑），故只在真 server 断言。范式照抄
test_tasks.py（_channel/_member/_agent_headers 三件套）。本文件不改任何产品代码。
"""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor

import pytest
import sqlalchemy
from coagentia_contracts import rest
from coagentia_server.db import models
from coagentia_server.ledger import service
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

BUILD = "build"
RESEARCH = "research"
AGENT_TEST_KEY = "cak_ledger_number_agent"

_TASK = models.Task.__table__
_EVT = models.TaskEvent.__table__
_MSG = models.Message.__table__


# ---------------------------------------------------------------- 助手（同 test_tasks 口径）


def _agent_headers(engine: Engine, member_id: str) -> dict[str, str]:
    """给 seed Agent 所属 Computer 注入已知测试 key，返回契约 B §2 双头。"""
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


def _events(engine: Engine, task_id: str) -> list[dict]:
    with engine.connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                select(_EVT).where(_EVT.c.task_id == task_id).order_by(_EVT.c.seq)
            ).mappings()
        ]


# ---------------------------------------------------------------- (a) 逐 kind 留痕字段


def test_claim_event_fields(server_client: TestClient, seeded_engine: Engine) -> None:
    """claim → 主动作 claim 行 owner=actor=认领者；联动 status_change 行 todo→in_progress。"""
    build = _channel(server_client, BUILD)
    me = _member(server_client, "Memcyo")["id"]
    task = _new_task(server_client, build["id"])

    assert server_client.post(f"/api/tasks/{task['id']}/claim").status_code == 200
    rows = _events(seeded_engine, task["id"])

    assert [r["kind"] for r in rows] == ["claim", "status_change"]
    claim = rows[0]
    # claim 行：owner_member_id 落新 owner（认领者），actor 为发起者，无状态迁移列
    assert claim["owner_member_id"] == me
    assert claim["actor_member_id"] == me
    assert claim["from_status"] is None and claim["to_status"] is None
    # 联动 status_change 行：owner 列不填，from/to 记实际迁移，actor 一致
    linked = rows[1]
    assert linked["from_status"] == "todo" and linked["to_status"] == "in_progress"
    assert linked["actor_member_id"] == me
    assert linked["owner_member_id"] is None


def test_unclaim_event_fields_and_owner_only(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """非 owner unclaim → 403 且不落事件；本人 unclaim → owner 置 NULL + 联动回 todo。"""
    build = _channel(server_client, BUILD)
    me = _member(server_client, "Memcyo")["id"]
    task = _new_task(server_client, build["id"])
    assert server_client.post(f"/api/tasks/{task['id']}/claim").status_code == 200
    before = _events(seeded_engine, task["id"])
    assert len(before) == 2  # claim + 联动 status_change

    # 非 owner（Pat Agent 主体）尝试释放 → 403 且不得写任何事件
    pat = _member(server_client, "Pat")
    headers = _agent_headers(seeded_engine, pat["id"])
    forbidden = server_client.post(f"/api/tasks/{task['id']}/unclaim", headers=headers)
    assert forbidden.status_code == 403
    assert rest.ErrorResponse.model_validate(forbidden.json()).error.code is (
        rest.ErrorCode.PERMISSION_DENIED
    )
    assert _events(seeded_engine, task["id"]) == before  # 拒绝路径零留痕

    # 本人释放 → unclaim 行 owner=NULL、actor=me；联动 status_change in_progress→todo
    assert server_client.post(f"/api/tasks/{task['id']}/unclaim").status_code == 200
    rows = _events(seeded_engine, task["id"])
    assert [r["kind"] for r in rows] == ["claim", "status_change", "unclaim", "status_change"]
    unclaim = rows[2]
    assert unclaim["owner_member_id"] is None  # 释放后无 owner
    assert unclaim["actor_member_id"] == me
    assert unclaim["from_status"] is None and unclaim["to_status"] is None
    assert (rows[3]["from_status"], rows[3]["to_status"]) == ("in_progress", "todo")
    assert rows[3]["actor_member_id"] == me


def test_assign_event_fields_owner_landed(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """assign → owner 落被指派者、actor 落指派者、不写 status_change；null 取消落 NULL。"""
    build = _channel(server_client, BUILD)
    me = _member(server_client, "Memcyo")["id"]  # 浏览器 Owner = 指派动作发起者
    pat = _member(server_client, "Pat")["id"]
    task = _new_task(server_client, build["id"])

    r = server_client.post(f"/api/tasks/{task['id']}/assign", json={"member_id": pat})
    assert r.status_code == 200 and r.json()["owner_member_id"] == pat
    rows = _events(seeded_engine, task["id"])
    assert [x["kind"] for x in rows] == ["assign"]  # assign 不联动 status，单行
    assert rows[0]["owner_member_id"] == pat  # 落"新 owner"=被指派者
    assert rows[0]["actor_member_id"] == me  # actor=指派者，非被指派者
    assert rows[0]["from_status"] is None and rows[0]["to_status"] is None

    # 取消指派（member_id=null）→ 追加一条 assign 行，owner_member_id 落 NULL
    r2 = server_client.post(f"/api/tasks/{task['id']}/assign", json={"member_id": None})
    assert r2.status_code == 200 and r2.json()["owner_member_id"] is None
    rows = _events(seeded_engine, task["id"])
    assert [x["kind"] for x in rows] == ["assign", "assign"]
    assert rows[1]["owner_member_id"] is None  # 取消指派 = 新 owner 为空
    assert rows[1]["actor_member_id"] == me


def test_status_change_event_fields(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """独立 /status 迁移 → 单条 status_change，from/to 精确、owner 列不填、actor=发起者。"""
    build = _channel(server_client, BUILD)
    me = _member(server_client, "Memcyo")["id"]
    task = _new_task(server_client, build["id"])  # todo

    r = server_client.post(f"/api/tasks/{task['id']}/status", json={"to": "in_progress"})
    assert r.status_code == 200 and r.json()["status"] == "in_progress"
    rows = _events(seeded_engine, task["id"])
    assert [x["kind"] for x in rows] == ["status_change"]
    evt = rows[0]
    assert (evt["from_status"], evt["to_status"]) == ("todo", "in_progress")
    assert evt["actor_member_id"] == me
    assert evt["owner_member_id"] is None  # status_change 不承载 owner 变更


def test_assign_by_agent_actor_is_agent(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """assign 的 actor 取实际主体：Agent 代理指派时 actor=该 Agent，owner=被指派者。"""
    build = _channel(server_client, BUILD)
    pat = _member(server_client, "Pat")
    hank = _member(server_client, "Hank")["id"]
    task = _new_task(server_client, build["id"])

    headers = _agent_headers(seeded_engine, pat["id"])
    r = server_client.post(
        f"/api/tasks/{task['id']}/assign", json={"member_id": hank}, headers=headers
    )
    assert r.status_code == 200 and r.json()["owner_member_id"] == hank
    rows = _events(seeded_engine, task["id"])
    assert [x["kind"] for x in rows] == ["assign"]
    assert rows[0]["owner_member_id"] == hank  # 新 owner = 被指派 Hank
    assert rows[0]["actor_member_id"] == pat["id"]  # actor = 代理发起的 Pat


# ---------------------------------------------------------------- (b) 并发建号


def test_concurrent_create_numbers_contiguous_unique(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """同频道并发建 N 任务：全部 201、number 连续无重复；DB 内该频道号码集恰为连续区间。"""
    research = _channel(server_client, RESEARCH)
    n = 12

    def _create(i: int):  # noqa: ANN202
        r = server_client.post(
            f"/api/channels/{research['id']}/messages",
            json={"body": f"并发任务 {i}", "as_task": {"title": f"t{i}"}},
        )
        return r.status_code, (r.json()["task"]["number"] if r.status_code == 201 else None)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_create, range(n)))

    codes = [c for c, _ in results]
    numbers = [num for _, num in results if num is not None]
    # 并发写不得因锁竞争丢请求：全部 201（busy_timeout + 原子 UPDATE…RETURNING 应保证）
    assert codes.count(201) == n, codes
    # 连续且无重复：分配号恰为 [min, min+n) 区间
    assert len(set(numbers)) == n, sorted(numbers)  # 无重号
    lo = min(numbers)
    assert sorted(numbers) == list(range(lo, lo + n)), sorted(numbers)  # 严格连续

    # 落库口径核对：research 频道的 task 号码集与 API 返回一致、且唯一
    with seeded_engine.connect() as conn:
        db_numbers = [
            r[0]
            for r in conn.execute(
                select(_TASK.c.number).where(_TASK.c.channel_id == research["id"])
            ).all()
        ]
    assert sorted(db_numbers) == sorted(numbers)
    assert len(set(db_numbers)) == len(db_numbers)


def test_channel_number_unique_constraint_enforced(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """UNIQUE(channel_id, number) 确实成立：直接插入重号 → IntegrityError。"""
    research = _channel(server_client, RESEARCH)
    ws_id = server_client.get("/api/workspace").json()["id"]
    me = _member(server_client, "Memcyo")["id"]
    existing = _new_task(server_client, research["id"])  # 占用某个 number
    dup_number = existing["number"]

    # 新建一条顶级消息作为重号任务的 root（root_message_id UNIQUE，避开与该约束混淆）
    msg = server_client.post(
        f"/api/channels/{research['id']}/messages", json={"body": "重号探针"}
    ).json()["message"]

    ts = service.now_iso()
    with pytest.raises((sqlalchemy.exc.IntegrityError, sqlalchemy.exc.OperationalError)):
        with seeded_engine.begin() as conn:
            conn.execute(
                insert(_TASK).values(
                    id=service.new_ulid(),
                    workspace_id=ws_id,
                    channel_id=research["id"],
                    number=dup_number,  # 与 existing 撞号（同频道）
                    root_message_id=msg["id"],
                    title="dup",
                    created_by_member_id=me,
                    status_changed_at=ts,
                    created_at=ts,
                )
            )
