"""M3b E5 force-start（裁决 3）：人类强制启动任务 = 解除本次投递 gating + 双留痕。

- 人类 force-start：留痕 task_events(force_start) 行 + 任务线程系统消息 + owner agent 被
  唤醒一次（假 daemon 收 AGENT_WAKE + MESSAGE_DELIVER）。
- Agent 调 force-start → 403 rule=C3（且前置拒绝不留痕）。
- 不改 status、不删边（owner agent 但 daemon 离线 → best-effort 仅留痕）。

驱动方式仿 test_daemon.py：假 daemon 连真 server /api/daemon/ws；force_start_wake 经 _run_sync
同步等 ack，故 REST 在后台线程发起，主线程驱动 daemon ack。
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from coagentia_contracts import rest
from coagentia_server.app import create_app
from coagentia_server.db import models
from coagentia_server.ledger.service import now_iso
from daemon_helpers import AUTH, Env, StubDaemon, nid
from fastapi.testclient import TestClient
from sqlalchemy import func, insert, select
from sqlalchemy.engine import Engine

DAEMON_WS = "/api/daemon/ws"


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


# ---------------------------------------------------------------- 库构造辅助


def _add_canvas(env: Env, channel_id: str) -> str:
    cid = nid()
    with env.engine.begin() as c:
        c.execute(
            insert(models.Canvas.__table__).values(
                id=cid,
                workspace_id=env.ws_id,
                channel_id=channel_id,
                baseline_version=0,
                baseline_hash="fs-test",
                updated_at=now_iso(),
            )
        )
    return cid


def _add_task(
    env: Env,
    channel_id: str,
    *,
    number: int,
    status: str = "todo",
    owner: str | None = None,
) -> tuple[str, str]:
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
                created_by_member_id=env.owner_id,
                status_changed_at=now_iso(),
                created_at=now_iso(),
            )
        )
    return tid, anchor


def _add_agent_node(env: Env, canvas_id: str, task_id: str) -> str:
    node_id = nid()
    with env.engine.begin() as c:
        c.execute(
            insert(models.CanvasNode.__table__).values(
                id=node_id,
                canvas_id=canvas_id,
                kind="agent",
                task_id=task_id,
                is_summary=False,
                pos_x=0,
                pos_y=0,
                created_at=now_iso(),
            )
        )
    return node_id


def _add_edge(env: Env, canvas_id: str, from_id: str, to_id: str) -> str:
    edge_id = nid()
    with env.engine.begin() as c:
        c.execute(
            insert(models.CanvasEdge.__table__).values(
                id=edge_id, canvas_id=canvas_id, from_node_id=from_id, to_node_id=to_id
            )
        )
    return edge_id


_TASK = models.Task.__table__
_EVT = models.TaskEvent.__table__
_MSG = models.Message.__table__
_EDGE = models.CanvasEdge.__table__


def _force_start_count(env: Env, task_id: str) -> int:
    with env.engine.connect() as c:
        return c.execute(
            select(func.count())
            .select_from(_EVT)
            .where(_EVT.c.task_id == task_id, _EVT.c.kind == "force_start")
        ).scalar_one()


def _thread_sys_msg_count(env: Env, root_message_id: str) -> int:
    with env.engine.connect() as c:
        return c.execute(
            select(func.count())
            .select_from(_MSG)
            .where(_MSG.c.thread_root_id == root_message_id, _MSG.c.kind == "system")
        ).scalar_one()


def _task_status(env: Env, task_id: str) -> str:
    with env.engine.connect() as c:
        return c.execute(select(_TASK.c.status).where(_TASK.c.id == task_id)).scalar_one()


def _edge_exists(env: Env, edge_id: str) -> bool:
    with env.engine.connect() as c:
        return c.execute(select(_EDGE.c.id).where(_EDGE.c.id == edge_id)).first() is not None


# ---------------------------------------------------------------- 人类 force-start：留痕 + 唤醒


def test_force_start_traces_and_wakes_owner_agent(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    ch = env.add_channel(name="build")
    bee = env.add_agent("Bee", "idle")
    env.join(ch, bee)
    env.join(ch, env.owner_id)
    tid, root = _add_task(env, ch, number=1, status="todo", owner=bee)
    env.add_message(ch, author=env.owner_id, body="do it")  # 构成 owner 积压

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(bee, "idle")])
        d.recv_hello_ack()
        d.sync()  # 握手对账：普通消息静默积压，无残留帧
        # force_start_wake 经 _run_sync 阻塞等 ack → REST 走后台线程，主线程驱动 ack。
        t, box = _bg(lambda: client.post(f"/api/tasks/{tid}/force-start"))
        wake = d.recv_instr()
        assert wake["type"] == "agent.wake"  # override 直投一次 wake（绕过 blocked 门）
        d.ack(wake, "done")
        deliver = d.recv_instr()
        assert deliver["type"] == "message.deliver"
        d.ack(deliver, "done")
        t.join(timeout=5)
        assert "e" not in box, box.get("e")
        assert box["r"].status_code == 200
        assert box["r"].json()["status"] == "todo"  # 响应任务未变

    assert _force_start_count(env, tid) == 1  # 留痕 1：task_events(force_start)
    assert _thread_sys_msg_count(env, root) == 1  # 留痕 2：任务线程系统消息
    assert _task_status(env, tid) == "todo"  # 不改 status


# ---------------------------------------------------------------- Agent 调用 → 403 C3


def test_agent_force_start_forbidden(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    ch = env.add_channel(name="build")
    bee = env.add_agent("Bee", "idle")
    env.join(ch, bee)
    tid, _root = _add_task(env, ch, number=1, status="todo", owner=bee)

    r = client.post(
        f"/api/tasks/{tid}/force-start", headers={**AUTH, "X-Acting-Member": bee}
    )
    assert r.status_code == 403
    err = rest.ErrorResponse.model_validate(r.json())
    assert err.error.code is rest.ErrorCode.PERMISSION_DENIED
    assert err.error.rule == "C3"
    assert _force_start_count(env, tid) == 0  # 前置拒绝，整事务回滚不留痕


# ---------------------------------------------------------------- 不改 status / 不删边（离线）


def test_force_start_keeps_status_and_edges(ctx: tuple[TestClient, Env, Any]) -> None:
    """owner agent 但 daemon 离线 → force_start_wake best-effort no-op；留痕仍写、status/边不变。"""
    client, env, _hub = ctx
    ch = env.add_channel(name="build")
    bee = env.add_agent("Bee", "idle")  # 无 daemon 连接
    env.join(ch, bee)
    canvas = _add_canvas(env, ch)
    ta, _ = _add_task(env, ch, number=1, status="todo")
    tb, _rb = _add_task(env, ch, number=2, status="todo", owner=bee)
    na = _add_agent_node(env, canvas, ta)
    nb = _add_agent_node(env, canvas, tb)
    eid = _add_edge(env, canvas, na, nb)

    r = client.post(f"/api/tasks/{tb}/force-start")  # daemon 离线 → 仅留痕，不阻塞
    assert r.status_code == 200
    assert r.json()["status"] == "todo"
    assert _task_status(env, tb) == "todo"  # 不改 status
    assert _edge_exists(env, eid)  # 不删边
    assert _force_start_count(env, tb) == 1  # 仍留痕


# ---------------------------------------------------------------- owner 为人类 → best-effort 仅留痕


def test_force_start_human_owner_traces_only(ctx: tuple[TestClient, Env, Any]) -> None:
    """owner 是人类（无 daemon 投递面）→ force_start_wake 静默 no-op；留痕照写。"""
    client, env, _hub = ctx
    ch = env.add_channel(name="build")
    tid, root = _add_task(env, ch, number=1, status="todo", owner=env.owner_id)

    r = client.post(f"/api/tasks/{tid}/force-start")
    assert r.status_code == 200
    assert _force_start_count(env, tid) == 1
    assert _thread_sys_msg_count(env, root) == 1
