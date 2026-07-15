"""M4b F5 护栏 freshness 门 + HeldDraft 三键人类干预 + G5 升级（真 server 专属）。

- freshness 门（裁决 1–6）：Agent 有未读 → 202 held；人类/系统主体不扣；未读空放行；无 read_position
  全量未读；线程 vs 主流 scope 隔离；门位次（校验类 4xx 优先于 held）；rehold 递增 + G5 升级。
- 三键（裁决 7–10）：release 原载荷落消息（author=原 Agent、含 as_task）；discard/reevaluate 直投
  （daemon 离线 503 且回滚）；仅人类（Agent 403 G3）；终态 409 携最新态；reevaluate 推进游标。

驱动方式仿 test_force_start：假 daemon 连真 server /api/daemon/ws；discard/reevaluate 经 _run_sync
同步等 ack，故 REST 在后台线程发起，主线程驱动 daemon ack。
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from coagentia_contracts import rest
from coagentia_server.app import create_app
from coagentia_server.db import models
from daemon_helpers import AUTH, Env
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.engine import Engine

DAEMON_WS = "/api/daemon/ws"

_MSG = models.Message.__table__
_HELD = models.HeldDraft.__table__
_DIAG = models.DiagnosticEvent.__table__
_ACT = models.ActivityItem.__table__


@pytest.fixture
def ctx(migrated_engine: Engine, tmp_path: Path) -> Iterator[tuple[TestClient, Env, Any]]:
    app = create_app(engine=migrated_engine, data_root=tmp_path / "data")
    hub = app.state.daemon_hub
    hub.ack_timeout = 0.3
    hub.query_timeout = 0.3
    hub.reconcile_interval = 3600
    hub.reminder_interval = 3600
    hub.silence_interval = 3600
    hub.held_interval = 3600  # 关 G4 自动重评估 loop（测试手动驱动 run_held_scan）
    env = Env(migrated_engine)
    with TestClient(app) as client:
        yield client, env, hub


def _bg(fn: Any) -> tuple[threading.Thread, dict[str, Any]]:
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["r"] = fn()
        except Exception as e:  # noqa: BLE001
            box["e"] = e

    t = threading.Thread(target=run)
    t.start()
    return t, box


def _agent_headers(agent_id: str) -> dict[str, str]:
    return {**AUTH, "X-Acting-Member": agent_id}


def _set_channel(env: Env, channel_id: str, **values: Any) -> None:
    with env.engine.begin() as c:
        c.execute(
            update(models.Channel.__table__)
            .where(models.Channel.__table__.c.id == channel_id)
            .values(**values)
        )


def _held_status(env: Env, held_id: str) -> str:
    with env.engine.connect() as c:
        return c.execute(select(_HELD.c.status).where(_HELD.c.id == held_id)).scalar_one()


def _diag_count(env: Env, diag_type: str) -> int:
    with env.engine.connect() as c:
        return c.execute(
            select(func.count()).select_from(_DIAG).where(_DIAG.c.type == diag_type)
        ).scalar_one()


def _agent_msg_count(env: Env, channel_id: str, agent_id: str) -> int:
    with env.engine.connect() as c:
        return c.execute(
            select(func.count()).select_from(_MSG).where(
                _MSG.c.channel_id == channel_id, _MSG.c.author_member_id == agent_id
            )
        ).scalar_one()


def _backdate_reeval(env: Env, held_id: str, when: str = "2020-01-01T00:00:00.000Z") -> None:
    """把 next_reeval_at 回拨到过去（模拟 held_reeval_min 倒计时到点）供 G4 扫描选中。"""
    with env.engine.begin() as c:
        c.execute(update(_HELD).where(_HELD.c.id == held_id).values(next_reeval_at=when))


def _run_held_scan(hub: Any) -> int:
    return asyncio.run_coroutine_threadsafe(hub.run_held_scan(), hub._loop).result(timeout=5)


def _hold_once(client: TestClient, channel_id: str, agent_id: str, body: str = "草稿正文") -> dict:
    """经真 freshness 门扣一次草稿（无 daemon）：返回 HeldDraftPublic dict，断言 202。"""
    r = client.post(
        f"/api/channels/{channel_id}/messages",
        json={"body": body},
        headers=_agent_headers(agent_id),
    )
    assert r.status_code == 202, r.text
    return rest.MessageHeld.model_validate(r.json()).held_draft.model_dump()


# ---------------------------------------------------------------- freshness 门


def test_agent_with_unread_is_held(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    ch = env.add_channel(name="build")
    bee = env.add_agent("Bee", "idle")
    env.join(ch, bee)
    env.join(ch, env.owner_id)
    env.add_message(ch, author=env.owner_id, body="有新消息")  # bee 未读

    r = client.post(
        f"/api/channels/{ch}/messages",
        json={"body": "我的草稿回复"},
        headers=_agent_headers(bee),
    )
    assert r.status_code == 202
    held = rest.MessageHeld.model_validate(r.json()).held_draft
    assert held.status == "held"
    assert held.held_count == 1
    assert held.draft_body == "我的草稿回复"
    assert held.reasons.total_unread == 1
    assert _agent_msg_count(env, ch, bee) == 0  # 不写消息
    assert _diag_count(env, "guard.held") == 1


def test_idempotent_replay_returns_first_result_over_hold(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    """幂等命中先于 freshness 门（评审 #4）：已登记首次结果的重放，即便期间出现新未读也回原
    消息（201 + 原 id），而非被误扣成 202 held（否则人类放行会产生重复消息，违 §1）。"""
    client, env, _hub = ctx
    ch = env.add_channel(name="build")
    bee = env.add_agent("Bee", "idle")
    env.join(ch, bee)
    env.join(ch, env.owner_id)
    hdr = {**_agent_headers(bee), "Idempotency-Key": "k-dup-1"}

    # 首发：无未读 → 201 落库 M1，账本登记 key→M1。
    r1 = client.post(f"/api/channels/{ch}/messages", json={"body": "结论"}, headers=hdr)
    assert r1.status_code == 201
    m1_id = r1.json()["message"]["id"]

    # 期间他人发消息制造未读（Agent 未读）。
    env.add_message(ch, author=env.owner_id, body="插入的新消息")

    # 同键同 body 重放：必须回原 M1（201），不被 freshness 门扣成 held。
    r2 = client.post(f"/api/channels/{ch}/messages", json={"body": "结论"}, headers=hdr)
    assert r2.status_code == 201, r2.text
    assert r2.json()["message"]["id"] == m1_id
    # 未产生新 held 行、未重复落库。
    assert client.get("/api/held-drafts", params={"channel_id": ch}).json()["items"] == []
    assert _agent_msg_count(env, ch, bee) == 1


def test_human_message_never_held(ctx: tuple[TestClient, Env, Any]) -> None:
    """人类主体永不 held（裁决 1）：owner 发言即便有 Agent 未读也 201 正常落库。"""
    client, env, _hub = ctx
    ch = env.add_channel(name="build")
    bee = env.add_agent("Bee", "idle")
    env.join(ch, bee)
    env.add_message(ch, author=bee, body="agent 说")  # owner 未读

    r = client.post(f"/api/channels/{ch}/messages", json={"body": "owner 回复"})
    assert r.status_code == 201


def test_agent_no_unread_passes(ctx: tuple[TestClient, Env, Any]) -> None:
    """未读集空 → 放行（裁决 2）：空频道 Agent 发言 201。"""
    client, env, _hub = ctx
    ch = env.add_channel(name="build")
    bee = env.add_agent("Bee", "idle")
    env.join(ch, bee)

    r = client.post(
        f"/api/channels/{ch}/messages", json={"body": "首发"}, headers=_agent_headers(bee)
    )
    assert r.status_code == 201


def test_read_position_clears_unread(ctx: tuple[TestClient, Env, Any]) -> None:
    """read_position 推进到最新 → 未读集空 → 放行（裁决 2）。"""
    client, env, _hub = ctx
    ch = env.add_channel(name="build")
    bee = env.add_agent("Bee", "idle")
    env.join(ch, bee)
    m1 = env.add_message(ch, author=env.owner_id, body="m1")
    env.set_read(bee, ch, m1)  # bee 已读到 m1

    r = client.post(
        f"/api/channels/{ch}/messages", json={"body": "回复"}, headers=_agent_headers(bee)
    )
    assert r.status_code == 201


def test_thread_scope_isolated_from_main(ctx: tuple[TestClient, Env, Any]) -> None:
    """线程 vs 主流 scope 隔离（裁决 1）：线程内无未读则放行，同时主流有未读仍不影响线程发言。"""
    client, env, _hub = ctx
    ch = env.add_channel(name="build")
    bee = env.add_agent("Bee", "idle")
    env.join(ch, bee)
    env.join(ch, env.owner_id)
    root_a = env.add_message(ch, author=bee, body="bee 自己的顶级")  # 线程 A 根（自己发，不计未读）
    env.add_message(ch, author=env.owner_id, body="owner 顶级")  # 主流未读

    # 线程 A scope 内无他人未读 → 放行。
    r_thread = client.post(
        f"/api/channels/{ch}/messages",
        json={"body": "线程回复", "thread_root_id": root_a},
        headers=_agent_headers(bee),
    )
    assert r_thread.status_code == 201
    # 主流 scope 有未读（owner 顶级）→ held。
    r_main = client.post(
        f"/api/channels/{ch}/messages", json={"body": "主流草稿"}, headers=_agent_headers(bee)
    )
    assert r_main.status_code == 202


def test_validation_precedes_hold(ctx: tuple[TestClient, Env, Any]) -> None:
    """门位次（裁决 5）：校验类 4xx 优先于 held——归档频道对有未读的 Agent 仍回 409 而非 202。"""
    client, env, _hub = ctx
    ch = env.add_channel(name="build")
    bee = env.add_agent("Bee", "idle")
    env.join(ch, bee)
    env.add_message(ch, author=env.owner_id, body="未读")
    from coagentia_server.ledger.service import now_iso

    _set_channel(env, ch, archived_at=now_iso())

    r = client.post(
        f"/api/channels/{ch}/messages", json={"body": "草稿"}, headers=_agent_headers(bee)
    )
    assert r.status_code == 409
    assert rest.ErrorResponse.model_validate(r.json()).error.code is rest.ErrorCode.CHANNEL_ARCHIVED


def test_rehold_increments_and_escalates(ctx: tuple[TestClient, Env, Any]) -> None:
    """再扣同活动行 held_count+1（裁决 3）；达阈值 → G5 升级一次（裁决 6），已升级不二次升级。"""
    client, env, _hub = ctx
    ch = env.add_channel(name="build")
    bee = env.add_agent("Bee", "idle")
    env.join(ch, bee)
    env.join(ch, env.owner_id)
    _set_channel(env, ch, held_escalate_n=2)
    env.add_message(ch, author=env.owner_id, body="未读")

    h1 = _hold_once(client, ch, bee)
    assert h1["held_count"] == 1 and h1["escalated_at"] is None
    h2 = _hold_once(client, ch, bee)
    assert h2["id"] == h1["id"]  # 同活动行，不建新行
    assert h2["held_count"] == 2
    assert h2["escalated_at"] is not None  # 达阈值 → 升级
    assert _diag_count(env, "guard.escalated") == 1
    # 升级发 scope 系统消息 @人类 + held_escalation activity 给 owner。
    with env.engine.connect() as c:
        sys_n = c.execute(
            select(func.count()).select_from(_MSG).where(
                _MSG.c.channel_id == ch, _MSG.c.kind == "system"
            )
        ).scalar_one()
        act_n = c.execute(
            select(func.count()).select_from(_ACT).where(
                _ACT.c.member_id == env.owner_id, _ACT.c.kind == "held_escalation"
            )
        ).scalar_one()
    assert sys_n == 1 and act_n == 1

    h3 = _hold_once(client, ch, bee)  # 已升级 → 再扣不二次喊人
    assert h3["held_count"] == 3
    assert h3["escalated_at"] == h2["escalated_at"]  # escalated_at 不变
    assert _diag_count(env, "guard.escalated") == 1


def test_list_held_drafts(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    ch = env.add_channel(name="build")
    bee = env.add_agent("Bee", "idle")
    env.join(ch, bee)
    env.add_message(ch, author=env.owner_id, body="未读")
    held = _hold_once(client, ch, bee)

    page = rest.Page[Any].model_validate(
        client.get("/api/held-drafts", params={"status": "held"}).json()
    )
    assert [h["id"] for h in page.items] == [held["id"]]
    # channel_id 过滤
    other = client.get("/api/held-drafts", params={"channel_id": "01K5CMPT0000000000000ZZZZZ"})
    assert other.json()["items"] == []


def test_list_defaults_to_active_excludes_terminal(ctx: tuple[TestClient, Env, Any]) -> None:
    """status 省略 → 默认只回活动态（held/reevaluating），排除终态（评审 #1）。

    否则终态历史随频道生命周期无界累积、keyset 升序把老的终态填满首页把活动 held 挤到后页。
    """
    client, env, _hub = ctx
    ch = env.add_channel(name="build")
    bee = env.add_agent("Bee", "idle")
    env.join(ch, bee)
    env.join(ch, env.owner_id)
    env.add_message(ch, author=env.owner_id, body="未读1")
    old = _hold_once(client, ch, bee, body="先扣再放行")
    client.post(f"/api/held-drafts/{old['id']}/release").raise_for_status()  # → released（终态）
    env.add_message(ch, author=env.owner_id, body="未读2")
    active = _hold_once(client, ch, bee, body="现行被扣")

    # 默认（无 status）：只回活动态，终态 released 不出现。
    default_ids = [h["id"] for h in client.get("/api/held-drafts").json()["items"]]
    assert default_ids == [active["id"]]
    # 显式 status=released 仍精确回终态行。
    rel = client.get("/api/held-drafts", params={"status": "released"}).json()["items"]
    assert [h["id"] for h in rel] == [old["id"]]


# ---------------------------------------------------------------- 三键：release（不依赖 daemon）


def test_release_sends_original_payload(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    ch = env.add_channel(name="build")
    bee = env.add_agent("Bee", "idle")
    env.join(ch, bee)
    env.join(ch, env.owner_id)
    env.add_message(ch, author=env.owner_id, body="未读")
    held = _hold_once(client, ch, bee, body="放行我")

    r = client.post(f"/api/held-drafts/{held['id']}/release")  # owner 人类干预
    assert r.status_code == 200, r.text
    resp = rest.HeldDraftReleaseResponse.model_validate(r.json())
    assert resp.message.body == "放行我"
    assert resp.message.author_member_id == bee  # author = 原 Agent
    assert resp.held_draft.status == "released"
    assert resp.held_draft.resolution == "released"
    assert resp.held_draft.resolved_by_member_id == env.owner_id
    assert _agent_msg_count(env, ch, bee) == 1  # 消息真落库
    assert _diag_count(env, "guard.released") == 1


def test_release_executes_as_task(ctx: tuple[TestClient, Env, Any]) -> None:
    """release 原样执行携带的 as_task 意图（裁决 8）。"""
    client, env, _hub = ctx
    ch = env.add_channel(name="build")
    bee = env.add_agent("Bee", "idle")
    env.join(ch, bee)
    env.add_message(ch, author=env.owner_id, body="未读")
    r_hold = client.post(
        f"/api/channels/{ch}/messages",
        json={"body": "转任务草稿", "as_task": {"title": "护栏放行任务"}},
        headers=_agent_headers(bee),
    )
    assert r_hold.status_code == 202
    held = rest.MessageHeld.model_validate(r_hold.json()).held_draft

    client.post(f"/api/held-drafts/{held.id}/release").raise_for_status()
    tasks = client.get("/api/tasks", params={"channel_id": ch}).json()["items"]
    assert any(t["title"] == "护栏放行任务" for t in tasks)


def test_release_on_archived_channel_409(ctx: tuple[TestClient, Env, Any]) -> None:
    """基础校验重跑（裁决 8）：hold 后频道归档 → release 回 409 CHANNEL_ARCHIVED。"""
    client, env, _hub = ctx
    ch = env.add_channel(name="build")
    bee = env.add_agent("Bee", "idle")
    env.join(ch, bee)
    env.add_message(ch, author=env.owner_id, body="未读")
    held = _hold_once(client, ch, bee)
    from coagentia_server.ledger.service import now_iso

    _set_channel(env, ch, archived_at=now_iso())

    r = client.post(f"/api/held-drafts/{held['id']}/release")
    assert r.status_code == 409
    assert rest.ErrorResponse.model_validate(r.json()).error.code is rest.ErrorCode.CHANNEL_ARCHIVED
    assert _held_status(env, held["id"]) == "held"  # 状态不落


# ---------------------------------------------------------------- 三键：仅人类 / 终态


def test_three_key_agent_forbidden(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    ch = env.add_channel(name="build")
    bee = env.add_agent("Bee", "idle")
    env.join(ch, bee)
    env.add_message(ch, author=env.owner_id, body="未读")
    held = _hold_once(client, ch, bee)

    for ep in ("release", "discard", "reevaluate"):
        r = client.post(f"/api/held-drafts/{held['id']}/{ep}", headers=_agent_headers(bee))
        assert r.status_code == 403, ep
        err = rest.ErrorResponse.model_validate(r.json())
        assert err.error.code is rest.ErrorCode.PERMISSION_DENIED
        assert err.error.rule == "G3"


def test_terminal_held_409_with_details(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    ch = env.add_channel(name="build")
    bee = env.add_agent("Bee", "idle")
    env.join(ch, bee)
    env.add_message(ch, author=env.owner_id, body="未读")
    held = _hold_once(client, ch, bee)
    client.post(f"/api/held-drafts/{held['id']}/release").raise_for_status()  # → released（终态）

    for ep in ("release", "discard", "reevaluate"):
        r = client.post(f"/api/held-drafts/{held['id']}/{ep}")
        assert r.status_code == 409, ep
        err = rest.ErrorResponse.model_validate(r.json())
        assert err.error.code is rest.ErrorCode.HELD_DRAFT_RESOLVED
        assert err.error.details["held_draft"]["status"] == "released"  # 携当前最新态


# ---------------------------------------------------------------- 三键：discard（依赖 daemon）


def test_discard_offline_503_rolls_back(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    ch = env.add_channel(name="build")
    bee = env.add_agent("Bee", "idle")
    env.join(ch, bee)
    env.add_message(ch, author=env.owner_id, body="未读")
    held = _hold_once(client, ch, bee)  # 无 daemon 连接

    r = client.post(f"/api/held-drafts/{held['id']}/discard")
    assert r.status_code == 503
    assert _held_status(env, held["id"]) == "held"  # 回滚，状态不落


def test_discard_online_injects_and_discards(ctx: tuple[TestClient, Env, Any]) -> None:
    from daemon_helpers import StubDaemon

    client, env, _hub = ctx
    ch = env.add_channel(name="build")
    bee = env.add_agent("Bee", "idle")
    env.join(ch, bee)
    env.add_message(ch, author=env.owner_id, body="未读")
    held = _hold_once(client, ch, bee)

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(bee, "idle")])
        d.recv_hello_ack()
        d.sync()
        t, box = _bg(lambda: client.post(f"/api/held-drafts/{held['id']}/discard"))
        instr = d.recv_instr()
        assert instr["type"] == "message.inject"  # 直投 guard_feedback
        assert instr["data"]["source"]["kind"] == "guard_feedback"
        d.ack(instr, "done")
        t.join(timeout=5)
        assert "e" not in box, box.get("e")
        assert box["r"].status_code == 200
        assert box["r"].json()["held_draft"]["status"] == "discarded"
    assert _diag_count(env, "guard.discarded") == 1


# ---------------------------------------------------------------- 三键：reevaluate（依赖 daemon）


def test_reevaluate_offline_503(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    ch = env.add_channel(name="build")
    bee = env.add_agent("Bee", "idle")
    env.join(ch, bee)
    env.add_message(ch, author=env.owner_id, body="未读")
    held = _hold_once(client, ch, bee)

    r = client.post(f"/api/held-drafts/{held['id']}/reevaluate")
    assert r.status_code == 503
    assert _held_status(env, held["id"]) == "held"  # 未提交 reevaluating


def test_reevaluate_advances_read_position(ctx: tuple[TestClient, Env, Any]) -> None:
    """reevaluate 委托 hub：wake+deliver+inject；deliver ack 推进游标（防复扣，裁决 10）。"""
    from daemon_helpers import StubDaemon

    client, env, _hub = ctx
    ch = env.add_channel(name="build")
    bee = env.add_agent("Bee", "idle")
    env.join(ch, bee)
    env.join(ch, env.owner_id)
    m1 = env.add_message(ch, author=env.owner_id, body="未读")
    held = _hold_once(client, ch, bee)
    assert env.read_position(bee, ch) is None

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(bee, "idle")])
        d.recv_hello_ack()
        d.sync()  # 握手：普通消息静默积压，无残留帧
        t, box = _bg(lambda: client.post(f"/api/held-drafts/{held['id']}/reevaluate"))
        wake = d.recv_instr()
        assert wake["type"] == "agent.wake"
        d.ack(wake, "done")
        deliver = d.recv_instr()
        assert deliver["type"] == "message.deliver"
        d.ack(deliver, "done")
        inject = d.recv_instr()
        assert inject["type"] == "message.inject"
        assert inject["data"]["source"]["kind"] == "guard_feedback"
        d.ack(inject, "done")
        t.join(timeout=5)
        assert "e" not in box, box.get("e")
        assert box["r"].status_code == 200
        assert box["r"].json()["held_draft"]["status"] == "reevaluating"
        assert box["r"].json()["held_draft"]["resolution"] is None  # 非终态
    assert env.read_position(bee, ch) == m1  # deliver ack 推进游标


def test_reevaluate_guard_rejects_concurrently_resolved(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    """终态守卫（评审 #5）：路由校验与 hub UPDATE 间若行被并发终解，UPDATE 限活动态影响 0 行 →
    HeldDraftResolved（不复活已终解草稿）。直接调 hub.reevaluate_held 模拟 TOCTOU 竞态窗口。"""
    from coagentia_server.computers import HeldDraftResolved
    from coagentia_server.ledger.service import now_iso
    from daemon_helpers import StubDaemon

    client, env, hub = ctx
    ch = env.add_channel(name="build")
    bee = env.add_agent("Bee", "idle")
    env.join(ch, bee)
    env.add_message(ch, author=env.owner_id, body="未读")
    held = _hold_once(client, ch, bee)

    # 模拟并发 discard 已提交终态（在路由 _reject_terminal 通过之后、hub UPDATE 之前）。
    with env.engine.begin() as c:
        c.execute(
            update(_HELD).where(_HELD.c.id == held["id"]).values(
                status="discarded", resolution="discarded",
                resolved_by_member_id=env.owner_id, resolved_at=now_iso(),
            )
        )

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(bee, "idle")])
        d.recv_hello_ack()
        d.sync()
        # 直接调 hub 桥（绕过路由 _reject_terminal）：守卫 UPDATE 影响 0 行 → 在 _run_sync(组合)
        # 之前抛 HeldDraftResolved（无 daemon 帧下发），路由据此回 409。
        with pytest.raises(HeldDraftResolved):
            hub.reevaluate_held(held["id"], env.owner_id)
    # 行仍为 discarded，未被复活成 reevaluating（不产生矛盾终态字段）。
    assert _held_status(env, held["id"]) == "discarded"


# ---------------------------------------------------------------- G4 定时自动重评估（裁决 4/6）


def test_held_scan_online_reevaluates_and_advances_cursor(ctx: tuple[TestClient, Env, Any]) -> None:
    """G4 到点 held + 在线 Agent：run_held_scan 置 reevaluating + guard.reevaluate_requested；在线
    组合 wake+deliver(推进 read_position)+inject；游标前移后模拟重发过门不复扣（裁决 4）。"""
    from daemon_helpers import StubDaemon

    client, env, hub = ctx
    ch = env.add_channel(name="build")
    bee = env.add_agent("Bee", "idle")
    env.join(ch, bee)
    env.join(ch, env.owner_id)
    m1 = env.add_message(ch, author=env.owner_id, body="未读")
    held = _hold_once(client, ch, bee)
    assert env.read_position(bee, ch) is None
    _backdate_reeval(env, held["id"])  # next_reeval_at 到点

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(bee, "idle")])
        d.recv_hello_ack()
        d.sync()  # 握手：普通消息静默积压，无残留帧
        t, box = _bg(lambda: _run_held_scan(hub))
        wake = d.recv_instr()
        assert wake["type"] == "agent.wake"
        d.ack(wake, "done")
        deliver = d.recv_instr()
        assert deliver["type"] == "message.deliver"
        d.ack(deliver, "done")
        inject = d.recv_instr()
        assert inject["type"] == "message.inject"
        assert inject["data"]["source"]["kind"] == "guard_feedback"
        d.ack(inject, "done")
        t.join(timeout=5)
        assert "e" not in box, box.get("e")
        assert box["r"] == 1  # 一行进入重评估

    assert _held_status(env, held["id"]) == "reevaluating"
    assert _diag_count(env, "guard.reevaluate_requested") == 1
    assert env.read_position(bee, ch) == m1  # deliver ack 推进游标（关键：防复扣死循环）
    # 游标越过 m1 → 未读集空 → 重发过门不复扣（201），held_count 不再 +1。
    r = client.post(
        f"/api/channels/{ch}/messages", json={"body": "重发"}, headers=_agent_headers(bee)
    )
    assert r.status_code == 201


def test_held_scan_offline_leaves_held_for_retry(ctx: tuple[TestClient, Env, Any]) -> None:
    """Agent 离线：G4 **不翻状态**、行留 held 下轮重试（评审 #6）。

    旧实现离线也置 reevaluating，但扫描只选 status='held' → 翻后再不被选、对账无 held 感知 →
    行永卡 reevaluating（Agent 从不被重评估、附件永久 GC 豁免）。修为「在线先探再翻状态」。
    """
    client, env, hub = ctx
    ch = env.add_channel(name="build")
    bee = env.add_agent("Bee", "idle")
    env.join(ch, bee)
    env.add_message(ch, author=env.owner_id, body="未读")
    held = _hold_once(client, ch, bee)  # 无 daemon 连接
    _backdate_reeval(env, held["id"])

    assert _run_held_scan(hub) == 0  # 离线 → 本轮不翻状态
    assert _held_status(env, held["id"]) == "held"  # 仍 held，下轮 Agent 在线再翻
    assert _diag_count(env, "guard.reevaluate_requested") == 0
    assert env.read_position(bee, ch) is None  # 未投递，游标不动


def test_held_scan_excludes_escalated(ctx: tuple[TestClient, Env, Any]) -> None:
    """升级后停自动（裁决 6）：escalated_at 非空的到点行被 G4 扫描排除，状态保持 held。"""
    client, env, hub = ctx
    ch = env.add_channel(name="build")
    bee = env.add_agent("Bee", "idle")
    env.join(ch, bee)
    env.join(ch, env.owner_id)
    _set_channel(env, ch, held_escalate_n=1)  # 第一次扣即升级
    env.add_message(ch, author=env.owner_id, body="未读")
    held = _hold_once(client, ch, bee)
    assert held["escalated_at"] is not None  # 已升级
    _backdate_reeval(env, held["id"])  # 即便到点

    assert _run_held_scan(hub) == 0  # 升级后停自动，不选中
    assert _held_status(env, held["id"]) == "held"  # 状态不变
    assert _diag_count(env, "guard.reevaluate_requested") == 0


def test_held_scan_skips_not_yet_due(ctx: tuple[TestClient, Env, Any]) -> None:
    """未到点（next_reeval_at 在未来，缺省 held_reeval_min=5 分钟）→ 不选中。"""
    client, env, hub = ctx
    ch = env.add_channel(name="build")
    bee = env.add_agent("Bee", "idle")
    env.join(ch, bee)
    env.add_message(ch, author=env.owner_id, body="未读")
    held = _hold_once(client, ch, bee)  # next_reeval_at = now + 5min（未到点）

    assert _run_held_scan(hub) == 0
    assert _held_status(env, held["id"]) == "held"


def test_deliver_advances_cursor_breaks_rehold_loop(ctx: tuple[TestClient, Env, Any]) -> None:
    """死循环反例（裁决 4）：仅 inject 不推进 read_position → 重发仍见未读 → 复扣（held_count+1）；
    完整组合含 deliver 推进游标（此处直接模拟 ack 后游标前移）→ 重发过门不复扣（201）。"""
    client, env, _hub = ctx
    ch = env.add_channel(name="build")
    bee = env.add_agent("Bee", "idle")
    env.join(ch, bee)
    env.join(ch, env.owner_id)
    m1 = env.add_message(ch, author=env.owner_id, body="未读")
    h1 = _hold_once(client, ch, bee)
    assert h1["held_count"] == 1

    # 仅 inject（read_position 不动）：重发仍见 m1 未读 → 复扣同活动行（held_count+1）。
    h2 = _hold_once(client, ch, bee)
    assert h2["id"] == h1["id"] and h2["held_count"] == 2  # 无限复扣的病态路径

    # 完整组合的 deliver ack 会推进 read_position 越过 m1（此处直接模拟游标前移）。
    env.set_read(bee, ch, m1)
    r = client.post(
        f"/api/channels/{ch}/messages", json={"body": "重发"}, headers=_agent_headers(bee)
    )
    assert r.status_code == 201  # 未读集空 → 过门不复扣（死循环被打破）
