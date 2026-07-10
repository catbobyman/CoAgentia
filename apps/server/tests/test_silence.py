"""F3 D5 沉默提醒与升级链测试（契约 B §10.5 / M4-HANDOFF §9a.4）。

两层：
1. 纯判定单测（tasks/silence.py）：threshold_hours 取值 / compute_last_activity / decide 三态
   黄金用例——防自激的时间语义直接钉在纯函数上。
2. 扫描集成（hub.run_silence_scan）：真 server（空库）+ 受控 Env 直插最小任务场景，手动
   await 扫描后对真库断言。覆盖三态阈值提醒 @ 目标 / override 覆盖 / 开关关不升级 / **自激
   防护**（提醒系统消息与 reminder_sent 事件不刷新 last_activity，真实活动才重置）/ 升级后静默
   / @Agent owner 提醒触发唤醒（mention 既有路径）。

用可注入的阈值/远古时间戳做确定性测试（不依赖真 24h；裁决 12 实机 PATCH 阈值，测试直插）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from coagentia_server.app import create_app
from coagentia_server.db import models
from coagentia_server.ledger.service import now_iso
from coagentia_server.tasks import silence
from daemon_helpers import AUTH, Env, StubDaemon, nid
from fastapi.testclient import TestClient
from sqlalchemy import insert, select
from sqlalchemy.engine import Engine

DAEMON_WS = "/api/daemon/ws"

# 远古锚点：任何默认阈值下都已超期（字典序=时序，故直接当"很久以前"用）。
ANCIENT = "2020-01-01T00:00:00.000Z"
ANCIENT_LATER = "2020-06-01T00:00:00.000Z"


@pytest.fixture
def ctx(migrated_engine: Engine, tmp_path: Path) -> Iterator[tuple[TestClient, Env, Any]]:
    """真 server（空库）+ 小超时 daemon 网关 + 受控 Env（关全部周期扫描，测试手动驱动）。"""
    app = create_app(engine=migrated_engine, data_root=tmp_path / "data")
    hub = app.state.daemon_hub
    hub.ack_timeout = 0.3
    hub.query_timeout = 0.3
    hub.reconcile_interval = 3600
    hub.reminder_interval = 3600
    hub.silence_interval = 3600
    env = Env(migrated_engine)
    with TestClient(app) as client:
        yield client, env, hub


def _scan(hub: Any) -> int:
    return asyncio.run_coroutine_threadsafe(hub.run_silence_scan(), hub._loop).result(timeout=5)


# ---------------------------------------------------------------- 库构造辅助


def _add_human(env: Env, name: str) -> str:
    mid = nid()
    with env.engine.begin() as c:
        c.execute(
            insert(models.Member.__table__).values(
                id=mid,
                workspace_id=env.ws_id,
                kind="human",
                name=name,
                role="member",
                created_at=now_iso(),
            )
        )
    return mid


def _add_task(
    env: Env,
    channel_id: str,
    *,
    number: int,
    status: str = "todo",
    owner: str | None = None,
    created_by: str | None = None,
    status_changed_at: str = ANCIENT,
    silence_override_h: int | None = None,
) -> tuple[str, str]:
    """建任务（含 system 锚点消息，锚点本身不算"非系统消息"）；返回 (task_id, root)。"""
    anchor = env.add_message(channel_id, author=None, kind="system", body="anchor")
    tid = nid()
    with env.engine.begin() as c:
        c.execute(
            insert(models.Task.__table__).values(
                id=tid,
                workspace_id=env.ws_id,
                channel_id=channel_id,
                number=number,
                root_message_id=anchor,
                title=f"T{number}",
                status=status,
                owner_member_id=owner,
                level="l1",
                created_by_member_id=created_by or env.owner_id,
                silence_override_h=silence_override_h,
                status_changed_at=status_changed_at,
                created_at=status_changed_at,
            )
        )
    return tid, anchor


def _add_thread_message(
    env: Env, channel_id: str, root: str, *, kind: str, created_at: str, author: str | None = None
) -> str:
    """在锚点线程内（thread_root_id=root）插一条消息，created_at 可控。"""
    mid = nid()
    with env.engine.begin() as c:
        c.execute(
            insert(models.Message.__table__).values(
                id=mid,
                workspace_id=env.ws_id,
                channel_id=channel_id,
                thread_root_id=root,
                author_member_id=author,
                kind=kind,
                body="x",
                created_at=created_at,
            )
        )
    return mid


def _add_event(env: Env, task_id: str, kind: str, *, created_at: str) -> None:
    """直插 task_events（不可变表允 INSERT）：模拟历史提醒/升级/真实活动留痕。"""
    with env.engine.begin() as c:
        c.execute(
            insert(models.TaskEvent.__table__).values(
                task_id=task_id,
                kind=kind,
                from_status=None,
                to_status=None,
                owner_member_id=None,
                actor_member_id=None,
                created_at=created_at,
            )
        )


def _set_channel(env: Env, channel_id: str, **vals: Any) -> None:
    from sqlalchemy import update

    with env.engine.begin() as c:
        c.execute(
            update(models.Channel.__table__)
            .where(models.Channel.__table__.c.id == channel_id)
            .values(**vals)
        )


# ---------------------------------------------------------------- 库查询辅助


def _event_kinds(env: Env, task_id: str) -> list[str]:
    with env.engine.connect() as c:
        return list(
            c.execute(
                select(models.TaskEvent.__table__.c.kind)
                .where(models.TaskEvent.__table__.c.task_id == task_id)
                .order_by(models.TaskEvent.__table__.c.seq)
            ).scalars()
        )


def _thread_reminder(env: Env, root: str) -> dict[str, Any] | None:
    """锚点线程内的系统消息（= 提醒消息）；取最新一条。"""
    with env.engine.connect() as c:
        row = c.execute(
            select(models.Message.__table__)
            .where(
                models.Message.__table__.c.thread_root_id == root,
                models.Message.__table__.c.kind == "system",
            )
            .order_by(models.Message.__table__.c.created_at.desc())
        ).mappings().first()
    return dict(row) if row else None


def _mainstream_system_msgs(
    env: Env, channel_id: str, *, exclude: set[str] = frozenset()  # type: ignore[assignment]
) -> list[dict[str, Any]]:
    """频道主流（thread_root_id IS NULL）系统消息；exclude 剔除任务锚点（本身即顶级系统消息）。"""
    with env.engine.connect() as c:
        rows = c.execute(
            select(models.Message.__table__).where(
                models.Message.__table__.c.channel_id == channel_id,
                models.Message.__table__.c.thread_root_id.is_(None),
                models.Message.__table__.c.kind == "system",
            )
        ).mappings()
        return [dict(r) for r in rows if r["id"] not in exclude]


def _mention_ids(env: Env, message_id: str) -> set[str]:
    with env.engine.connect() as c:
        return set(
            c.execute(
                select(models.MessageMention.__table__.c.member_id).where(
                    models.MessageMention.__table__.c.message_id == message_id
                )
            ).scalars()
        )


def _activities(env: Env, kind: str) -> list[dict[str, Any]]:
    with env.engine.connect() as c:
        rows = c.execute(
            select(models.ActivityItem.__table__).where(
                models.ActivityItem.__table__.c.kind == kind
            )
        ).mappings()
        return [dict(r) for r in rows]


# ================================================================ 纯判定单测（黄金用例）


def test_threshold_hours_by_status_and_override() -> None:
    kw = dict(remind_todo_h=24, remind_inprog_h=12, remind_review_h=24)
    assert silence.threshold_hours("todo", silence_override_h=None, **kw) == 24
    assert silence.threshold_hours("in_progress", silence_override_h=None, **kw) == 12
    assert silence.threshold_hours("in_review", silence_override_h=None, **kw) == 24
    # override 三态同值覆盖（裁决 8）。
    for status in ("todo", "in_progress", "in_review"):
        assert silence.threshold_hours(status, silence_override_h=7 * 24, **kw) == 168


def test_compute_last_activity_takes_latest_non_null() -> None:
    inp = silence.SilenceInputs(
        now="2026-01-01T00:00:00.000Z",
        threshold_h=24,
        remind_escalation=True,
        status_changed_at="2025-01-01T00:00:00.000Z",
        last_thread_msg_at="2025-06-01T00:00:00.000Z",
        last_event_at=None,
        last_reminder_at=None,
        last_escalated_at=None,
    )
    assert silence.compute_last_activity(inp) == "2025-06-01T00:00:00.000Z"
    inp2 = silence.SilenceInputs(**{**inp.__dict__, "last_thread_msg_at": None})
    assert silence.compute_last_activity(inp2) == "2025-01-01T00:00:00.000Z"


def _mk(**over: Any) -> silence.SilenceInputs:
    base = dict(
        now="2026-01-01T00:00:00.000Z",
        threshold_h=24,
        remind_escalation=True,
        status_changed_at=ANCIENT,
        last_thread_msg_at=None,
        last_event_at=None,
        last_reminder_at=None,
        last_escalated_at=None,
    )
    return silence.SilenceInputs(**{**base, **over})


def test_decide_first_reminder_when_over_threshold() -> None:
    assert silence.decide(_mk()) is silence.SilenceAction.REMIND
    # 未超阈值 → 静默。
    fresh = _mk(status_changed_at="2026-01-01T00:00:00.000Z")  # = now
    assert silence.decide(fresh) is None


def test_decide_no_reminder_when_already_reminded() -> None:
    # 已提醒（reminder 晚于 last_activity），未到再一个阈值周期 → 不再提醒也不升级。
    inp = _mk(last_reminder_at="2025-12-31T23:00:00.000Z")  # now-1h < 24h
    assert silence.decide(inp) is None


def test_decide_escalates_after_second_threshold() -> None:
    inp = _mk(last_reminder_at="2025-11-01T00:00:00.000Z")  # 距 now 远超 24h
    assert silence.decide(inp) is silence.SilenceAction.ESCALATE


def test_decide_escalation_off_stays_silent() -> None:
    inp = _mk(last_reminder_at="2025-11-01T00:00:00.000Z", remind_escalation=False)
    assert silence.decide(inp) is None


def test_decide_silent_after_escalation() -> None:
    inp = _mk(
        last_reminder_at="2025-11-01T00:00:00.000Z",
        last_escalated_at="2025-12-01T00:00:00.000Z",
    )
    assert silence.decide(inp) is None


def test_decide_new_activity_resets_chain() -> None:
    # reminder 与 escalated 都在，但真实新活动（晚于二者）刷新 last_activity → 整链重置。
    recent = "2025-12-31T23:59:00.000Z"  # now-1min → age < 24h
    inp = _mk(
        last_thread_msg_at=recent,
        last_reminder_at="2025-11-01T00:00:00.000Z",
        last_escalated_at="2025-12-01T00:00:00.000Z",
    )
    assert silence.decide(inp) is None  # 重置后未到阈值 → 静默
    # 若新活动同样远古超期，则重新走第一次提醒。
    inp2 = _mk(
        last_thread_msg_at=ANCIENT_LATER,
        last_reminder_at=ANCIENT,  # 早于新活动 → 失效
    )
    assert silence.decide(inp2) is silence.SilenceAction.REMIND


# ================================================================ 扫描集成


def test_reminder_todo_targets_creator(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, hub = ctx
    ch = env.add_channel(name="build")
    creator = _add_human(env, "Cara")
    tid, root = _add_task(env, ch, number=1, status="todo", created_by=creator)
    assert _scan(hub) == 1
    assert _event_kinds(env, tid) == ["reminder_sent"]
    rem = _thread_reminder(env, root)
    assert rem is not None
    assert _mention_ids(env, rem["id"]) == {creator}  # Todo → @创建者


def test_reminder_inprogress_targets_owner(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, hub = ctx
    ch = env.add_channel(name="build")
    owner = _add_human(env, "Owen")
    tid, root = _add_task(env, ch, number=1, status="in_progress", owner=owner)
    assert _scan(hub) == 1
    rem = _thread_reminder(env, root)
    assert rem is not None
    assert _mention_ids(env, rem["id"]) == {owner}  # In Progress → @owner


def test_reminder_inreview_targets_channel_humans(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, hub = ctx
    ch = env.add_channel(name="build")
    h1 = _add_human(env, "Hal")
    h2 = _add_human(env, "Hana")
    agent = env.add_agent("Bot", "idle")
    for m in (h1, h2, agent, env.owner_id):
        env.join(ch, m)
    tid, root = _add_task(env, ch, number=1, status="in_review")
    assert _scan(hub) == 1
    rem = _thread_reminder(env, root)
    assert rem is not None
    # In Review → @频道全体人类成员（h1/h2/owner），不含 agent。
    assert _mention_ids(env, rem["id"]) == {h1, h2, env.owner_id}


def test_override_raises_threshold_suppresses(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, hub = ctx
    ch = env.add_channel(name="build")
    # 远古锚点在默认 24h 下必超期；但 override=100 年 → 未超 → 不提醒（override 抬高阈值）。
    tid, root = _add_task(env, ch, number=1, status="todo", silence_override_h=100 * 365 * 24)
    assert _scan(hub) == 0
    assert _event_kinds(env, tid) == []
    assert _thread_reminder(env, root) is None


def test_override_zero_forces_immediate_reminder(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, hub = ctx
    ch = env.add_channel(name="build")
    # override=0 → 阈值 0h → 即便 in_review 默认 24h，也立刻提醒（三态覆盖 + 即时）。
    now_status = now_iso()
    tid, root = _add_task(
        env, ch, number=1, status="in_review", status_changed_at=now_status, silence_override_h=0
    )
    env.join(ch, env.owner_id)
    assert _scan(hub) == 1
    assert _event_kinds(env, tid) == ["reminder_sent"]


def test_escalation_off_no_escalation(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, hub = ctx
    ch = env.add_channel(name="build")
    _set_channel(env, ch, remind_escalation=False)
    tid, root = _add_task(env, ch, number=1, status="todo")
    # 预置远古 reminder_sent（模拟已提醒且距今远超阈值）；开关关 → 不升级。
    _add_event(env, tid, "reminder_sent", created_at=ANCIENT_LATER)
    _add_thread_message(env, ch, root, kind="system", created_at=ANCIENT_LATER)
    assert _scan(hub) == 0
    assert "escalated" not in _event_kinds(env, tid)
    assert _mainstream_system_msgs(env, ch, exclude={root}) == []


def test_full_chain_reminder_then_escalate_then_silent(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    """完整链：预置远古提醒 → 扫描升级（主流消息 + 置顶 activity + escalated）→ 再扫静默。"""
    client, env, hub = ctx
    ch = env.add_channel(name="build")
    env.join(ch, env.owner_id)
    tid, root = _add_task(env, ch, number=1, status="todo")
    # 预置：远古 reminder_sent 事件 + 锚点线程系统提醒消息（二者皆应被 last_activity 排除）。
    _add_event(env, tid, "reminder_sent", created_at=ANCIENT_LATER)
    _add_thread_message(env, ch, root, kind="system", created_at=ANCIENT_LATER)

    # 扫描 1：已提醒 + 距提醒远超阈值 + 开关默认开 → 升级（不因提醒产物自激重置）。
    assert _scan(hub) == 1
    assert _event_kinds(env, tid).count("escalated") == 1
    mainstream = _mainstream_system_msgs(env, ch, exclude={root})
    assert len(mainstream) == 1  # 频道主流升级消息（剔除任务锚点）
    acts = _activities(env, "silence_escalation")
    assert len(acts) == 1
    assert acts[0]["member_id"] == env.owner_id
    assert acts[0]["task_id"] == tid

    # 扫描 2：已升级（escalated 晚于 last_activity）→ 静默，无二次升级。
    assert _scan(hub) == 0
    assert _event_kinds(env, tid).count("escalated") == 1
    assert len(_mainstream_system_msgs(env, ch, exclude={root})) == 1


def test_thread_system_message_excluded_but_real_message_resets(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    """自激防护（消息侧）：锚点线程内**系统**消息（提醒产物）不刷新 last_activity → 仍升级；
    **非系统**真实消息刷新 last_activity → 重置整链不升级。"""
    client, env, hub = ctx
    ch = env.add_channel(name="build")
    env.join(ch, env.owner_id)
    # 两任务同构：远古锚点 + 远古 reminder_sent；仅线程内近期消息 kind 不同。
    tx, rootx = _add_task(env, ch, number=1, status="todo")
    ty, rooty = _add_task(env, ch, number=2, status="todo")
    for tid in (tx, ty):
        _add_event(env, tid, "reminder_sent", created_at=ANCIENT_LATER)
    # X：线程内近期**系统**消息（模拟提醒产物）→ 应被排除 → 升级。
    _add_thread_message(env, ch, rootx, kind="system", created_at=now_iso())
    # Y：线程内近期**非系统**真实消息 → 刷新 last_activity → 重置 → 不升级（age<阈值）。
    _add_thread_message(env, ch, rooty, kind="user", author=env.owner_id, created_at=now_iso())

    _scan(hub)
    assert _event_kinds(env, tx).count("escalated") == 1  # 系统消息被排除，链条走到升级
    assert "escalated" not in _event_kinds(env, ty)  # 真实消息重置链条


def test_reminder_event_excluded_but_real_event_resets(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    """自激防护（事件侧）：reminder_sent 事件不计入 last_activity → 仍升级；真实 task_events
    （如 claim）刷新 last_activity → 重置不升级。"""
    client, env, hub = ctx
    ch = env.add_channel(name="build")
    env.join(ch, env.owner_id)
    tx, _ = _add_task(env, ch, number=1, status="todo")
    ty, _ = _add_task(env, ch, number=2, status="todo")
    for tid in (tx, ty):
        _add_event(env, tid, "reminder_sent", created_at=ANCIENT_LATER)
    # Y 追加一条近期真实事件（claim）→ last_event_at 近期 → 重置。
    _add_event(env, ty, "claim", created_at=now_iso())

    _scan(hub)
    assert _event_kinds(env, tx).count("escalated") == 1  # 仅 reminder_sent → 被排除 → 升级
    assert "escalated" not in _event_kinds(env, ty)  # 真实 claim 事件重置链条


def test_done_and_closed_tasks_not_scanned(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, hub = ctx
    ch = env.add_channel(name="build")
    td, _ = _add_task(env, ch, number=1, status="done")
    tc, _ = _add_task(env, ch, number=2, status="closed")
    assert _scan(hub) == 0
    assert _event_kinds(env, td) == []
    assert _event_kinds(env, tc) == []


def test_agent_owner_reminder_triggers_wake(ctx: tuple[TestClient, Env, Any]) -> None:
    """@Agent owner 提醒经既有投递路径触发唤醒（mention 视同 @，reason=reminder）。"""
    client, env, hub = ctx
    ch = env.add_channel(name="build")
    bee = env.add_agent("Bee", "idle")
    env.join(ch, bee)
    tid, root = _add_task(env, ch, number=1, status="in_progress", owner=bee)
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(bee, "idle")])
        d.recv_hello_ack()
        d.sync()  # 握手对账：锚点系统消息非 @Bee，无触发
        assert _scan(hub) == 1
        wake = d.recv_instr()
        assert wake["type"] == "agent.wake"
        assert wake["data"]["reason"] == "reminder"  # 锚点线程系统提醒 + mention 视同 @
        d.ack(wake, "done")
        deliver = d.recv_instr()
        assert deliver["type"] == "message.deliver"
        d.ack(deliver, "done")
        d.sync()
    assert _event_kinds(env, tid) == ["reminder_sent"]
    rem = _thread_reminder(env, root)
    assert rem is not None
    assert _mention_ids(env, rem["id"]) == {bee}
