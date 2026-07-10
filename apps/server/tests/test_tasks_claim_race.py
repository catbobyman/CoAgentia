"""C2 硬化专项：并发 claim 恰一成功（T2）。

真 server（conftest 的 server_client：alembic upgrade head + seed_database + create_app）上，
用 ThreadPoolExecutor + threading.Barrier 对**同一 task** 发起 N 个同刻 claim，验证条件更新闸门
（`UPDATE ... WHERE owner_member_id IS NULL` + rowcount）在真并发下严格恰一成功：

- 恰好 1 个 200 且 owner 落定（+联动 todo→in_progress）；
- 其余 N-1 个 409 CLAIM_RACE、rule=T2、details.current_owner 指向胜者；
- task_events 恰增一条 claim（+胜者联动的一条 status_change），claim 行 owner/actor=胜者；
- **无 5xx**——SQLite 写锁竞争必须收敛为契约 409，不得泄漏为服务器错误。

test_tasks.py 已有一个轻量 smoke（test_concurrent_claim_exactly_one）；本文件加压：Barrier 对齐
同刻发射、多并发度参数化、多轮复跑降偶发漏检、以及多身份（Owner+Agents）同场竞争。
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest
from coagentia_contracts import rest
from coagentia_server.db import models
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.engine import Engine

BUILD = "build"
OWNER = "Memcyo"
AGENT_TEST_KEY = "cak_rest_agent_test"
BARRIER_TIMEOUT_S = 30.0

_TASK = models.Task.__table__
_EVT = models.TaskEvent.__table__


# ---------------------------------------------------------------- 取数助手


def _channel(client: TestClient, name: str) -> dict:
    return next(c for c in client.get("/api/channels").json()["items"] if c["name"] == name)


def _member(client: TestClient, name: str) -> dict:
    return next(m for m in client.get("/api/members").json() if m["name"] == name)


def _new_task(client: TestClient, channel_id: str, title: str = "race") -> dict:
    r = client.post(
        f"/api/channels/{channel_id}/messages",
        json={"body": "b", "as_task": {"title": title}},
    )
    assert r.status_code == 201, r.text
    return r.json()["task"]


def _task_events(engine: Engine, task_id: str) -> list[dict]:
    with engine.connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                select(_EVT).where(_EVT.c.task_id == task_id).order_by(_EVT.c.seq)
            ).mappings()
        ]


def _install_shared_agent_key(engine: Engine) -> None:
    """给 seed 里 agents 共用的 Computer 注入已知测试 key（多身份竞争用，同 test_tasks 口径）。"""
    digest = hashlib.sha256(AGENT_TEST_KEY.encode()).hexdigest()
    with engine.begin() as conn:
        conn.execute(update(models.Computer.__table__).values(api_key_hash=digest))


def _agent_headers(member_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {AGENT_TEST_KEY}", "X-Acting-Member": member_id}


# ---------------------------------------------------------------- 并发编排


def _race_claim(
    client: TestClient, task_id: str, headers_per_thread: list[dict[str, str] | None]
) -> list[tuple[int, dict]]:
    """让 N 个线程在 Barrier 处对齐后同刻 POST /claim；返回各线程 (status_code, body)。"""
    n = len(headers_per_thread)
    barrier = threading.Barrier(n)

    def _worker(idx: int) -> tuple[int, dict]:
        headers = headers_per_thread[idx]
        barrier.wait(timeout=BARRIER_TIMEOUT_S)  # 对齐同刻发射，最大化写锁竞争
        resp = client.post(f"/api/tasks/{task_id}/claim", headers=headers)
        return resp.status_code, resp.json()

    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(_worker, range(n)))


def _assert_exactly_one_winner(
    results: list[tuple[int, dict]], allowed_owner_ids: set[str]
) -> str:
    """通用不变量断言，返回胜者 owner_member_id。"""
    codes = [c for c, _ in results]
    # 锁竞争不得泄漏为 5xx——所有败者必须是结构化 409。
    assert not any(c >= 500 for c in codes), codes
    assert codes.count(200) == 1, codes
    assert codes.count(409) == len(results) - 1, codes

    winner_body = next(body for code, body in results if code == 200)
    winner_owner = winner_body["owner_member_id"]
    assert winner_owner in allowed_owner_ids, winner_owner
    assert winner_body["status"] == "in_progress"  # claim 联动 todo→in_progress（裁决 1）

    for code, body in results:
        if code == 409:
            err = rest.ErrorResponse.model_validate(body)  # 形状过契约模型
            assert err.error.code is rest.ErrorCode.CLAIM_RACE
            assert err.error.rule == "T2"
            assert err.error.details is not None
            assert err.error.details["current_owner"] == winner_owner
    return winner_owner


def _assert_single_claim_event(engine: Engine, task_id: str, winner_owner: str) -> None:
    rows = _task_events(engine, task_id)
    kinds = [str(r["kind"]) for r in rows]
    # 恰一 claim；胜者联动补一条 status_change；败者一行不写（rowcount=0 早退）。
    assert kinds.count("claim") == 1, kinds
    assert kinds == ["claim", "status_change"], kinds
    claim_row = next(r for r in rows if str(r["kind"]) == "claim")
    assert claim_row["owner_member_id"] == winner_owner
    assert claim_row["actor_member_id"] == winner_owner
    status_row = next(r for r in rows if str(r["kind"]) == "status_change")
    assert (status_row["from_status"], status_row["to_status"]) == ("todo", "in_progress")


# ---------------------------------------------------------------- 同身份并发（Owner）


@pytest.mark.parametrize("n", [4, 8, 16])
def test_concurrent_claim_exactly_one_winner(
    server_client: TestClient, seeded_engine: Engine, n: int
) -> None:
    build = _channel(server_client, BUILD)
    owner_id = _member(server_client, OWNER)["id"]
    task = _new_task(server_client, build["id"])

    results = _race_claim(server_client, task["id"], [None] * n)

    winner_owner = _assert_exactly_one_winner(results, {owner_id})
    assert winner_owner == owner_id  # 浏览器身份唯一 → 胜者必是 Owner

    # DB 终态 == HTTP 胜者。
    final = server_client.get(f"/api/tasks/{task['id']}").json()["task"]
    assert final["owner_member_id"] == owner_id
    assert final["status"] == "in_progress"

    _assert_single_claim_event(seeded_engine, task["id"], owner_id)


def test_concurrent_claim_repeated_rounds(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """多轮复跑：偶发竞态漏检的防线（每轮独立 task，逐轮断言不变量）。"""
    build = _channel(server_client, BUILD)
    owner_id = _member(server_client, OWNER)["id"]

    for round_no in range(6):
        task = _new_task(server_client, build["id"], title=f"round-{round_no}")
        results = _race_claim(server_client, task["id"], [None] * 8)
        winner_owner = _assert_exactly_one_winner(results, {owner_id})
        assert winner_owner == owner_id
        _assert_single_claim_event(seeded_engine, task["id"], owner_id)


# ---------------------------------------------------------------- 多身份同场竞争


@pytest.fixture
def multi_identity_client(server_client: TestClient, seeded_engine: Engine) -> Iterator[TestClient]:
    _install_shared_agent_key(seeded_engine)
    yield server_client


def test_concurrent_claim_multi_identity_one_winner(
    multi_identity_client: TestClient, seeded_engine: Engine
) -> None:
    """Owner + 三个 Agent 主体同刻抢同一 task：闸门与身份无关，仍恰一胜出。"""
    client = multi_identity_client
    build = _channel(client, BUILD)
    owner_id = _member(client, OWNER)["id"]
    agent_ids = [_member(client, name)["id"] for name in ("Pat", "Hank", "Rin")]
    allowed = {owner_id, *agent_ids}

    task = _new_task(client, build["id"])

    # 8 线程轮转分配四个身份（Owner=None 头即浏览器）。
    identities: list[dict[str, str] | None] = [None] + [_agent_headers(a) for a in agent_ids]
    headers_per_thread = [identities[i % len(identities)] for i in range(8)]

    results = _race_claim(client, task["id"], headers_per_thread)

    winner_owner = _assert_exactly_one_winner(results, allowed)
    final = client.get(f"/api/tasks/{task['id']}").json()["task"]
    assert final["owner_member_id"] == winner_owner  # DB 终态 == HTTP 胜者
    _assert_single_claim_event(seeded_engine, task["id"], winner_owner)
