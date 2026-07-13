"""M7b K6 成本核算 GET /usage 三层聚合（契约 B §13.4）。

真 TestClient + Env 精确插入（无需 daemon）：workspace/members/channels 由 Env 构造，tasks 与
token_usage_events 直接插入，再打 GET /api/usage 三层逐例断言。

守恒断言（每例）：响应形状 == {level, ref, usage, tasks_reporting, breakdown}；usage 五字段恒无
货币字段（W7）；level=task 恒 {0|1, 1}。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from coagentia_server.app import create_app
from coagentia_server.db import models
from coagentia_server.ledger.service import now_iso
from daemon_helpers import Env, nid
from fastapi.testclient import TestClient
from sqlalchemy import insert
from sqlalchemy.engine import Engine

_TASK = models.tbl(models.Task)
_TUE = models.tbl(models.TokenUsageEvent)

_USAGE_FIELDS = {
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "events",
}
_REPORT_FIELDS = {"level", "ref", "usage", "tasks_reporting", "breakdown"}


@pytest.fixture
def ctx(migrated_engine: Engine, tmp_path: Path) -> Iterator[tuple[TestClient, Env]]:
    app = create_app(engine=migrated_engine, data_root=tmp_path / "data")
    env = Env(migrated_engine)
    with TestClient(app) as client:
        yield client, env


def _task(
    env: Env,
    channel: str,
    *,
    number: int,
    owner: str | None = None,
    title: str | None = None,
) -> str:
    root = env.add_message(channel, kind="system", body=f"t{number}")
    tid = nid()
    with env.engine.begin() as c:
        c.execute(
            insert(_TASK).values(
                id=tid,
                workspace_id=env.ws_id,
                channel_id=channel,
                number=number,
                root_message_id=root,
                title=title or f"T{number}",
                status="todo",
                owner_member_id=owner,
                level="l1",
                created_by_member_id=env.owner_id,
                project_id=None,
                writes_code=False,
                status_changed_at=now_iso(),
                created_at=now_iso(),
            )
        )
    return tid


def _usage(
    env: Env,
    agent: str,
    *,
    task_id: str | None = None,
    channel_id: str | None = None,
    inp: int = 10,
    out: int = 5,
    cr: int = 0,
    cw: int = 0,
) -> None:
    with env.engine.begin() as c:
        c.execute(
            insert(_TUE).values(
                id=nid(),
                workspace_id=env.ws_id,
                agent_member_id=agent,
                task_id=task_id,
                channel_id=channel_id,
                input_tokens=inp,
                output_tokens=out,
                cache_read_tokens=cr,
                cache_write_tokens=cw,
                reported_at=now_iso(),
            )
        )


def _get(client: TestClient, level: str, ref: str, *, rollup: bool | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {"level": level, "ref": ref}
    if rollup is not None:
        params["rollup"] = str(rollup).lower()
    r = client.get("/api/usage", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _assert_shape_no_currency(body: dict[str, Any]) -> None:
    assert set(body) == _REPORT_FIELDS, body
    assert set(body["usage"]) == _USAGE_FIELDS, body["usage"]  # 恒无货币字段（W7）
    if body["breakdown"] is not None:
        for item in body["breakdown"]:
            assert set(item) == {"ref", "label", "usage"}, item
            assert set(item["usage"]) == _USAGE_FIELDS, item["usage"]


# ---------------------------------------------------------------- level=task


def test_task_level_aggregates_and_reporting_one(ctx: tuple[TestClient, Env]) -> None:
    client, env = ctx
    agent = env.add_agent("A", "offline")
    ch = env.add_channel(kind="channel", name="build")
    t = _task(env, ch, number=1)
    _usage(env, agent, task_id=t, inp=10, out=20, cr=1, cw=2)
    _usage(env, agent, task_id=t, inp=5, out=0, cr=0, cw=0)
    body = _get(client, "task", t)
    _assert_shape_no_currency(body)
    assert body["level"] == "task" and body["ref"] == t
    assert body["usage"] == {
        "input_tokens": 15,
        "output_tokens": 20,
        "cache_read_tokens": 1,
        "cache_write_tokens": 2,
        "events": 2,
    }
    # level=task 恒 {events>0?1:0, 1}。
    assert body["tasks_reporting"] == {"reporting": 1, "total": 1}
    assert body["breakdown"] is None


def test_task_level_empty_usage_all_zero_reporting_zero(ctx: tuple[TestClient, Env]) -> None:
    client, env = ctx
    ch = env.add_channel(kind="channel", name="build")
    t = _task(env, ch, number=1)  # 无任何 usage 事件
    body = _get(client, "task", t)
    _assert_shape_no_currency(body)
    assert body["usage"] == {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "events": 0,
    }
    # 恒 {0, 1}：分母恒 1（任务本身），无事件 → 0 上报。
    assert body["tasks_reporting"] == {"reporting": 0, "total": 1}


def test_task_level_unknown_ref_is_empty_not_404(ctx: tuple[TestClient, Env]) -> None:
    client, env = ctx
    body = _get(client, "task", nid())  # 不存在的 task_id
    _assert_shape_no_currency(body)
    assert body["usage"]["events"] == 0
    assert body["tasks_reporting"] == {"reporting": 0, "total": 1}


# ---------------------------------------------------------------- level=agent


def test_agent_level_owned_tasks_in_denominator(ctx: tuple[TestClient, Env]) -> None:
    client, env = ctx
    agent = env.add_agent("A", "offline")
    ch = env.add_channel(kind="channel", name="build")
    t1 = _task(env, ch, number=1, owner=agent)
    _task(env, ch, number=2, owner=agent)  # owner 任务但零上报 → 计入分母不计 reporting
    _usage(env, agent, task_id=t1, inp=10, out=5)
    # 该 agent 另有一条无归属任务的事件（task_id=None）→ 计入 usage 和，不计任何任务 reporting。
    _usage(env, agent, task_id=None, inp=100, out=0)
    body = _get(client, "agent", agent)
    _assert_shape_no_currency(body)
    assert body["level"] == "agent" and body["ref"] == agent
    # usage = 该 agent 全部事件（含无归属那条）。
    assert body["usage"]["input_tokens"] == 110 and body["usage"]["events"] == 2
    # total = owner 的所有任务（2），reporting = 有 usage 的 owner 任务数（仅 t1）。
    assert body["tasks_reporting"] == {"reporting": 1, "total": 2}
    assert body["breakdown"] is None


def test_agent_level_empty_set_all_zero(ctx: tuple[TestClient, Env]) -> None:
    client, env = ctx
    agent = env.add_agent("A", "offline")
    body = _get(client, "agent", agent)  # 该 agent 无任务无事件
    _assert_shape_no_currency(body)
    assert body["usage"]["events"] == 0 and body["usage"]["input_tokens"] == 0
    assert body["tasks_reporting"] == {"reporting": 0, "total": 0}


def test_agent_level_rollup_breakdown_per_owned_task(ctx: tuple[TestClient, Env]) -> None:
    client, env = ctx
    agent = env.add_agent("A", "offline")
    ch = env.add_channel(kind="channel", name="build")
    t1 = _task(env, ch, number=1, owner=agent, title="Alpha")
    t2 = _task(env, ch, number=2, owner=agent, title="Beta")
    _usage(env, agent, task_id=t1, inp=10, out=5)
    body = _get(client, "agent", agent, rollup=True)
    _assert_shape_no_currency(body)
    breakdown = body["breakdown"]
    assert breakdown is not None and len(breakdown) == 2  # 逐 owner 任务一项（含零上报 t2）
    by_ref = {item["ref"]: item for item in breakdown}
    assert by_ref[t1]["label"] == "Alpha"
    assert by_ref[t1]["usage"] == {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "events": 1,
    }
    # 零上报任务 usage 全 0 仍列入明细。
    assert by_ref[t2]["label"] == "Beta"
    assert by_ref[t2]["usage"]["events"] == 0


# ---------------------------------------------------------------- level=canvas


def test_canvas_level_channel_task_set(ctx: tuple[TestClient, Env]) -> None:
    client, env = ctx
    agent = env.add_agent("A", "offline")
    ch = env.add_channel(kind="channel", name="build")
    other = env.add_channel(kind="channel", name="ops")
    t1 = _task(env, ch, number=1)
    _task(env, ch, number=2)  # 同频道零上报（计入分母不计 reporting）
    t_other = _task(env, other, number=1)  # 别频道，不计入 ch 汇总
    _usage(env, agent, task_id=t1, channel_id=ch, inp=10, out=5)
    _usage(env, agent, task_id=t_other, channel_id=other, inp=999, out=999)
    body = _get(client, "canvas", ch)
    _assert_shape_no_currency(body)
    assert body["level"] == "canvas" and body["ref"] == ch
    # usage = 频道任务集 {t1,t2} 的事件和（别频道 t_other 不计）。
    assert body["usage"] == {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "events": 1,
    }
    assert body["tasks_reporting"] == {"reporting": 1, "total": 2}


def test_canvas_level_empty_channel_all_zero(ctx: tuple[TestClient, Env]) -> None:
    client, env = ctx
    ch = env.add_channel(kind="channel", name="empty")
    body = _get(client, "canvas", ch)  # 无任务
    _assert_shape_no_currency(body)
    assert body["usage"]["events"] == 0
    assert body["tasks_reporting"] == {"reporting": 0, "total": 0}


def test_canvas_level_rollup_breakdown_shape(ctx: tuple[TestClient, Env]) -> None:
    client, env = ctx
    agent = env.add_agent("A", "offline")
    ch = env.add_channel(kind="channel", name="build")
    t1 = _task(env, ch, number=1, title="One")
    t2 = _task(env, ch, number=2, title="Two")
    _usage(env, agent, task_id=t1, channel_id=ch, inp=7, out=3)
    body = _get(client, "canvas", ch, rollup=True)
    _assert_shape_no_currency(body)
    breakdown = body["breakdown"]
    assert breakdown is not None and len(breakdown) == 2
    by_ref = {item["ref"]: item for item in breakdown}
    assert by_ref[t1]["usage"]["input_tokens"] == 7 and by_ref[t1]["label"] == "One"
    assert by_ref[t2]["usage"]["events"] == 0 and by_ref[t2]["label"] == "Two"


# ---------------------------------------------------------------- 校验


def test_invalid_level_rejected(ctx: tuple[TestClient, Env]) -> None:
    client, _env = ctx
    r = client.get("/api/usage", params={"level": "bogus", "ref": "x"})
    assert r.status_code == 422  # UsageLevel 枚举值域外


def test_missing_ref_rejected(ctx: tuple[TestClient, Env]) -> None:
    client, _env = ctx
    r = client.get("/api/usage", params={"level": "task"})
    assert r.status_code == 422  # ref 必填
