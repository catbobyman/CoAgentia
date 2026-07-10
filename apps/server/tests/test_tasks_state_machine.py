"""C2 任务状态机逐边专项硬化（契约 B §9.1；纪律 7 单一事实源）。

全部用例从 `contracts.constants.TASK_TRANSITIONS` **派生**，测试内不复制第二份边表字面量：
- 合法边：POST /tasks/{id}/status → 200 + 状态流转 + 恰写一条 status_change(from/to/actor 正确)。
- 非法边：422 TASK_TRANSITION_INVALID（HTTP 按 C0/契约 B §3 登记为 422）+ details{from,to,allowed}
  与 TASK_TRANSITIONS[src] 一致，且不落库。
- 同态边（current→current，含终态自环）：幂等 200，不写事件、不广播（裁决 2）。
- 一动作一 task.updated 帧（设计裁决 D-WS）：一次合法流转恰广播 1 帧、change 的 from/to 正确。
- done/closed 终态出边规则（DONE 无出边、CLOSED 仅 reopen→todo）符合 B §9.1。

范式照抄 test_tasks.py（真 server TestClient、_force_status 直改库置态、seeded_engine 旁路读库）。
"""

from __future__ import annotations

import pytest
from coagentia_contracts import rest
from coagentia_contracts.constants import TASK_TRANSITIONS
from coagentia_contracts.enums import TaskEventKind, TaskStatus
from coagentia_contracts.ws import EventType
from coagentia_server.db import models
from coagentia_server.events import PendingEvent
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.engine import Engine

BUILD = "build"

_TASK = models.Task.__table__
_EVT = models.TaskEvent.__table__

# ---- 从契约常量派生边集（单一事实源；不在测试内另写一份边表） -------------------
_LEGAL_EDGES = [(src, dst) for src, dsts in TASK_TRANSITIONS.items() for dst in dsts]
_ILLEGAL_EDGES = [
    (src, dst)
    for src in TaskStatus
    for dst in TaskStatus
    if src != dst and dst not in TASK_TRANSITIONS[src]
]
_LEGAL_IDS = [f"{s.value}->{d.value}" for s, d in _LEGAL_EDGES]
_ILLEGAL_IDS = [f"{s.value}->{d.value}" for s, d in _ILLEGAL_EDGES]
_STATUS_IDS = [s.value for s in TaskStatus]


# ---- 测试辅助 --------------------------------------------------------------------


def _channel(client: TestClient, name: str) -> dict:
    return next(c for c in client.get("/api/channels").json()["items"] if c["name"] == name)


def _member(client: TestClient, name: str) -> dict:
    return next(m for m in client.get("/api/members").json() if m["name"] == name)


def _new_task(client: TestClient, channel_id: str, body: str = "b") -> dict:
    r = client.post(
        f"/api/channels/{channel_id}/messages", json={"body": body, "as_task": {"title": "t"}}
    )
    assert r.status_code == 201, r.text
    return r.json()["task"]


def _force_status(engine: Engine, task_id: str, status: TaskStatus) -> None:
    """直改库置于起始态（旁路端点，不写 task_events）——纯状态机置景。"""
    with engine.begin() as conn:
        conn.execute(update(_TASK).where(_TASK.c.id == task_id).values(status=status.value))


def _events_of(engine: Engine, task_id: str) -> list[dict]:
    with engine.connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                select(_EVT).where(_EVT.c.task_id == task_id).order_by(_EVT.c.seq)
            ).mappings()
        ]


@pytest.fixture
def build_id(server_client: TestClient) -> str:
    return _channel(server_client, BUILD)["id"]


@pytest.fixture
def owner_id(server_client: TestClient) -> str:
    """无鉴权头请求解析出的浏览器 Owner 主体（seed 中的 Memcyo）。"""
    return _member(server_client, "Memcyo")["id"]


# ---- 合法边：流转成功 + 恰写一条 status_change（from/to/actor 正确）----------------


@pytest.mark.parametrize("src,dst", _LEGAL_EDGES, ids=_LEGAL_IDS)
def test_legal_edge_flows_and_records_event(
    server_client: TestClient,
    seeded_engine: Engine,
    build_id: str,
    owner_id: str,
    src: TaskStatus,
    dst: TaskStatus,
) -> None:
    task = _new_task(server_client, build_id)
    _force_status(seeded_engine, task["id"], src)

    r = server_client.post(f"/api/tasks/{task['id']}/status", json={"to": dst.value})
    assert r.status_code == 200, (src, dst, r.text)
    assert r.json()["status"] == dst.value

    rows = _events_of(seeded_engine, task["id"])
    assert len(rows) == 1, (src, dst, rows)  # 一次流转 = 恰一条留痕
    ev = rows[0]
    assert ev["kind"] == TaskEventKind.STATUS_CHANGE.value
    assert ev["from_status"] == src.value
    assert ev["to_status"] == dst.value
    assert ev["actor_member_id"] == owner_id


# ---- 非法边：422 TASK_TRANSITION_INVALID + details{from,to,allowed} + 不落库 --------


@pytest.mark.parametrize("src,dst", _ILLEGAL_EDGES, ids=_ILLEGAL_IDS)
def test_illegal_edge_rejected_with_contract_details(
    server_client: TestClient,
    seeded_engine: Engine,
    build_id: str,
    src: TaskStatus,
    dst: TaskStatus,
) -> None:
    task = _new_task(server_client, build_id)
    _force_status(seeded_engine, task["id"], src)

    r = server_client.post(f"/api/tasks/{task['id']}/status", json={"to": dst.value})
    assert r.status_code == 422, (src, dst, r.text)  # C0/契约 B §3 登记 422
    err = rest.ErrorResponse.model_validate(r.json())
    assert err.error.code is rest.ErrorCode.TASK_TRANSITION_INVALID
    details = err.error.details
    assert details["from"] == src.value
    assert details["to"] == dst.value
    # allowed 直接由契约常量派生，端点必须回吐同一集合
    assert details["allowed"] == sorted(s.value for s in TASK_TRANSITIONS[src])
    # 非法边不改状态、不落留痕
    assert server_client.get(f"/api/tasks/{task['id']}").json()["task"]["status"] == src.value
    assert _events_of(seeded_engine, task["id"]) == []


# ---- 同态边（含终态自环）：幂等 200，不写事件、不广播 ------------------------------


@pytest.mark.parametrize("status", list(TaskStatus), ids=_STATUS_IDS)
def test_self_transition_idempotent_no_event_no_broadcast(
    server_client: TestClient,
    seeded_engine: Engine,
    build_id: str,
    status: TaskStatus,
) -> None:
    task = _new_task(server_client, build_id)
    _force_status(seeded_engine, task["id"], status)
    before = _events_of(seeded_engine, task["id"])

    captured: list[PendingEvent] = []
    server_client.app.state.bus.subscribe(captured.append)
    r = server_client.post(f"/api/tasks/{task['id']}/status", json={"to": status.value})

    assert r.status_code == 200, (status, r.text)
    assert r.json()["status"] == status.value
    assert _events_of(seeded_engine, task["id"]) == before  # 同态不写事件
    task_frames = [
        e for e in captured if e.type in (EventType.TASK_UPDATED, EventType.TASK_CREATED)
    ]
    assert task_frames == []  # 同态不广播


# ---- D-WS：一次合法流转恰广播 1 帧 task.updated，change 的 from/to 正确 --------------


def test_legal_transition_broadcasts_single_frame(
    server_client: TestClient, seeded_engine: Engine, build_id: str, owner_id: str
) -> None:
    task = _new_task(server_client, build_id)  # todo
    captured: list[PendingEvent] = []
    server_client.app.state.bus.subscribe(captured.append)

    r = server_client.post(f"/api/tasks/{task['id']}/status", json={"to": "in_progress"})
    assert r.status_code == 200

    updated = [e for e in captured if e.type is EventType.TASK_UPDATED]
    assert len(updated) == 1  # 一动作一帧
    change = updated[0].data["change"]
    assert change["kind"] == TaskEventKind.STATUS_CHANGE
    assert (change["from_status"], change["to_status"]) == (TaskStatus.TODO, TaskStatus.IN_PROGRESS)
    assert change["actor_member_id"] == owner_id


# ---- 终态出边规则（B §9.1）：DONE 无出边、CLOSED 仅 reopen→todo ---------------------


def test_done_is_terminal(
    server_client: TestClient, seeded_engine: Engine, build_id: str
) -> None:
    # 契约属性：done 出边为空集（终态）。
    assert not TASK_TRANSITIONS[TaskStatus.DONE], "契约 B §9.1：done 应为终态（空出边）"
    task = _new_task(server_client, build_id)
    _force_status(seeded_engine, task["id"], TaskStatus.DONE)
    for dst in (s for s in TaskStatus if s is not TaskStatus.DONE):
        r = server_client.post(f"/api/tasks/{task['id']}/status", json={"to": dst.value})
        assert r.status_code == 422, (dst, r.text)
        assert (
            rest.ErrorResponse.model_validate(r.json()).error.code
            is rest.ErrorCode.TASK_TRANSITION_INVALID
        )
    assert _events_of(seeded_engine, task["id"]) == []


def test_closed_reopens_only_to_todo(
    server_client: TestClient, seeded_engine: Engine, build_id: str
) -> None:
    # 契约属性：closed 唯一出边 = reopen→todo。
    assert TASK_TRANSITIONS[TaskStatus.CLOSED] == frozenset(
        {TaskStatus.TODO}
    ), "契约 B §9.1：closed 仅可 reopen→todo"

    # reopen 合法
    task = _new_task(server_client, build_id)
    _force_status(seeded_engine, task["id"], TaskStatus.CLOSED)
    r = server_client.post(f"/api/tasks/{task['id']}/status", json={"to": "todo"})
    assert r.status_code == 200 and r.json()["status"] == "todo"

    # 其余目标非法
    for dst in (TaskStatus.IN_PROGRESS, TaskStatus.IN_REVIEW, TaskStatus.DONE):
        t2 = _new_task(server_client, build_id)
        _force_status(seeded_engine, t2["id"], TaskStatus.CLOSED)
        r2 = server_client.post(f"/api/tasks/{t2['id']}/status", json={"to": dst.value})
        assert r2.status_code == 422, (dst, r2.text)
        assert (
            rest.ErrorResponse.model_validate(r2.json()).error.code
            is rest.ErrorCode.TASK_TRANSITION_INVALID
        )
