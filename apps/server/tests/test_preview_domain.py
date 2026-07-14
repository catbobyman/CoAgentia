"""M7a K3 server 预览域：ensure+touch/回收三触发/对账 #9/preview.status CAS/单活跃竞态/广播。

体例同 test_worktree_lifecycle.py（真 TestClient + StubDaemon 驱动 /api/daemon/ws 网关侧；
受控最小库 Env）。dev_command 用零依赖 http 服务串（K2-cal），端口/健康检查归 daemon，本层只测
server 判定与状态机边写（一律条件 UPDATE）。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from coagentia_contracts.ws import EventType
from coagentia_server.app import create_app
from coagentia_server.db import models
from coagentia_server.ledger.service import now_iso
from daemon_helpers import AUTH, Env, StubDaemon, drain_revalidation, nid
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

DAEMON_WS = "/api/daemon/ws"
WORKTREE_PATH = r"D:\scratch\preview\worktrees\project\task"
DEV_COMMAND = "python -m http.server %PORT%"

_PROJECT = models.tbl(models.Project)
_CHANNEL_PROJECT = models.tbl(models.ChannelProject)
_TASK = models.tbl(models.Task)
_WORKTREE = models.tbl(models.Worktree)
_PREVIEW = models.tbl(models.PreviewSession)
_DIAG = models.tbl(models.DiagnosticEvent)


@pytest.fixture
def ctx(migrated_engine: Engine, tmp_path: Path) -> Iterator[tuple[TestClient, Env, Any]]:
    app = create_app(engine=migrated_engine, data_root=tmp_path / "data")
    hub = app.state.daemon_hub
    hub.ack_timeout = 0.3
    hub.query_timeout = 0.3
    hub.reconcile_interval = 3600
    hub.reminder_interval = 3600
    hub.preview_recycle_interval = 3600  # 手动驱动扫描，禁后台 loop 干扰
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


def _project(
    env: Env,
    channel_id: str,
    *,
    dev_command: str | None = DEV_COMMAND,
    preview_idle_min: int = 30,
    keep_days: int = 7,
) -> str:
    project_id = nid()
    with env.engine.begin() as c:
        c.execute(
            insert(_PROJECT).values(
                id=project_id,
                workspace_id=env.ws_id,
                computer_id=env.comp_id,
                name="Demo",
                repo_path=r"D:\repos\demo",
                dev_command=dev_command,
                preview_idle_min=preview_idle_min,
                worktree_keep_days=keep_days,
                created_at=now_iso(),
            )
        )
        c.execute(
            insert(_CHANNEL_PROJECT).values(channel_id=channel_id, project_id=project_id)
        )
    return project_id


def _task(
    env: Env,
    channel_id: str,
    *,
    number: int,
    project_id: str | None,
    owner: str | None = None,
    writes_code: bool = True,
    status: str = "todo",
    status_changed_at: str | None = None,
) -> str:
    root_id = env.add_message(channel_id, kind="system", body=f"task {number}")
    task_id = nid()
    with env.engine.begin() as c:
        c.execute(
            insert(_TASK).values(
                id=task_id,
                workspace_id=env.ws_id,
                channel_id=channel_id,
                number=number,
                root_message_id=root_id,
                title=f"Task {number}",
                status=status,
                owner_member_id=owner,
                level="l2",
                created_by_member_id=env.owner_id,
                project_id=project_id,
                writes_code=writes_code,
                status_changed_at=status_changed_at or now_iso(),
                created_at=now_iso(),
            )
        )
    return task_id


def _worktree(
    env: Env, *, task_id: str, project_id: str, status: str = "active"
) -> str:
    worktree_id = nid()
    with env.engine.begin() as c:
        c.execute(
            insert(_WORKTREE).values(
                id=worktree_id,
                workspace_id=env.ws_id,
                project_id=project_id,
                task_id=task_id,
                branch=f"coagentia/task-{task_id}",
                path=WORKTREE_PATH,
                status=status,
                created_at=now_iso(),
            )
        )
    return worktree_id


def _seed(
    env: Env, *, dev_command: str | None = DEV_COMMAND, with_worktree: bool = True, **pkw: Any
) -> tuple[str, str, str | None]:
    """频道 + Project(dev_command) + 非终态 writes_code 任务 (+ active worktree)。返回
    (channel, task_id, worktree_id)。"""
    channel = env.add_channel(kind="channel", name="build")
    project = _project(env, channel, dev_command=dev_command, **pkw)
    task_id = _task(env, channel, number=1, project_id=project)
    worktree_id = (
        _worktree(env, task_id=task_id, project_id=project) if with_worktree else None
    )
    return channel, task_id, worktree_id


def _insert_preview(
    env: Env,
    *,
    task_id: str,
    worktree_id: str,
    status: str = "running",
    port: int | None = 5001,
    started_at: str | None = None,
    last_active_at: str | None = None,
) -> str:
    pid = nid()
    ts = now_iso()
    with env.engine.begin() as c:
        c.execute(
            insert(_PREVIEW).values(
                id=pid,
                workspace_id=env.ws_id,
                task_id=task_id,
                worktree_id=worktree_id,
                port=port,
                status=status,
                started_at=started_at or ts,
                last_active_at=last_active_at or ts,
            )
        )
    return pid


def _preview_row(env: Env, preview_id: str) -> dict[str, Any]:
    with env.engine.connect() as c:
        return dict(
            c.execute(select(_PREVIEW).where(_PREVIEW.c.id == preview_id)).mappings().one()
        )


def _active_row(env: Env, task_id: str) -> dict[str, Any] | None:
    with env.engine.connect() as c:
        row = (
            c.execute(
                select(_PREVIEW).where(
                    _PREVIEW.c.task_id == task_id,
                    _PREVIEW.c.status.in_(models.PREVIEW_ACTIVE_STATUSES),
                )
            )
            .mappings()
            .first()
        )
    return dict(row) if row is not None else None


# ---------------------------------------------------------------- ensure / touch 语义


def test_ensure_creates_row_and_dispatches_start(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    _, task_id, worktree_id = _seed(env)
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([])
        d.recv_hello_ack()
        drain_revalidation(d)  # 握手复验 active worktree（#3）先消费

        resp = client.post(f"/api/tasks/{task_id}/preview")
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "starting"
        assert body["port"] is None
        session_id = body["id"]

        start = d.recv_instr()
        assert start["type"] == "preview.start"
        assert start["data"] == {
            "preview_session_id": session_id,
            "task_id": task_id,
            "worktree_path": WORKTREE_PATH,
            "dev_command": DEV_COMMAND,
        }
        d.ack(start, "done")

        row = _active_row(env, task_id)
        assert row is not None and row["status"] == "starting"
        assert row["worktree_id"] == worktree_id


def test_touch_only_advances_last_active_no_redispatch(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    client, env, _hub = ctx
    _, task_id, _ = _seed(env)
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([])
        d.recv_hello_ack()
        drain_revalidation(d)

        first = client.post(f"/api/tasks/{task_id}/preview")
        assert first.status_code == 201
        start = d.recv_instr()
        assert start["type"] == "preview.start"
        d.ack(start, "done")
        la1 = _active_row(env, task_id)["last_active_at"]  # type: ignore[index]

        time.sleep(0.01)  # ms 分辨率下让第二次 touch 时间戳可区分
        second = client.post(f"/api/tasks/{task_id}/preview")
        assert second.status_code == 200  # 幂等命中，非新建
        assert second.json()["id"] == first.json()["id"]
        d.sync()  # 无第二个 preview.start 帧（touch 不重下发）——若有则 recv_pong 断言失败

        la2 = _active_row(env, task_id)["last_active_at"]  # type: ignore[index]
        assert la2 > la1  # touch 推进 last_active_at


# ---------------------------------------------------------------- 三拒绝路径


def test_post_404_when_no_worktree(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    _, task_id, _ = _seed(env, with_worktree=False)
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([])
        d.recv_hello_ack()
        d.sync()
        resp = client.post(f"/api/tasks/{task_id}/preview")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_post_503_when_daemon_offline(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    _, task_id, _ = _seed(env)
    # 不连 daemon → 离线
    resp = client.post(f"/api/tasks/{task_id}/preview")
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "DAEMON_OFFLINE"
    assert _active_row(env, task_id) is None  # 503 不建 starting 孤行


def test_post_422_when_no_dev_command(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    _, task_id, _ = _seed(env, dev_command=None)
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([])
        d.recv_hello_ack()
        drain_revalidation(d)
        resp = client.post(f"/api/tasks/{task_id}/preview")
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "VALIDATION_FAILED"
    assert "hint" in err["details"]
    assert err["details"]["project_id"]


# ---------------------------------------------------------------- preview.status 处理（CAS）


def test_status_running_carries_port_and_broadcasts(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    client, env, _hub = ctx
    channel, task_id, worktree_id = _seed(env)
    assert worktree_id is not None
    events: list[Any] = []
    token = client.app.state.bus.subscribe(events.append)  # type: ignore[union-attr]
    try:
        with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
            d = StubDaemon(ws)
            d.hello([])
            d.recv_hello_ack()
            drain_revalidation(d)
            session_id = _insert_preview(
                env, task_id=task_id, worktree_id=worktree_id, status="starting", port=None
            )
            d.report(
                "preview.status",
                {"preview_session_id": session_id, "status": "running", "port": 5137},
            )
            d.sync()
            assert _poll(lambda: _preview_row(env, session_id)["status"] == "running")
            assert _preview_row(env, session_id)["port"] == 5137
    finally:
        client.app.state.bus.unsubscribe(token)  # type: ignore[union-attr]

    updates = [
        e for e in events if e.type == EventType.PREVIEW_UPDATED and e.channel_id == channel
    ]
    assert len(updates) == 1
    assert updates[0].data["preview"]["status"] == "running"
    assert updates[0].data["preview"]["port"] == 5137
    # FR-11.3 进程状态入 diagnostic：running → 登记 preview.started 诊断行（owner=None 不广播）。
    with env.engine.connect() as c:
        diag = (
            c.execute(select(_DIAG).where(_DIAG.c.task_id == task_id)).mappings().first()
        )
    assert diag is not None and diag["type"] == "preview.started"
    assert diag["payload"]["preview_session_id"] == session_id


def test_status_failed_records_fail_log_tail(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    _, task_id, worktree_id = _seed(env)
    assert worktree_id is not None
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([])
        d.recv_hello_ack()
        drain_revalidation(d)
        session_id = _insert_preview(
            env, task_id=task_id, worktree_id=worktree_id, status="starting", port=None
        )
        d.report(
            "preview.status",
            {
                "preview_session_id": session_id,
                "status": "failed",
                "log_tail": "Traceback: bad dev_command\n",
            },
        )
        d.sync()
        assert _poll(lambda: _preview_row(env, session_id)["status"] == "failed")
        assert "bad dev_command" in _preview_row(env, session_id)["fail_log_tail"]
    # FR-11.3：failed 是进程状态，落 preview.failed 诊断行（Fable 补齐，同 started/recycled）。
    with env.engine.connect() as c:
        diag = c.execute(select(_DIAG).where(_DIAG.c.task_id == task_id)).mappings().first()
    assert diag is not None and diag["type"] == "preview.failed"
    assert diag["payload"]["preview_session_id"] == session_id


def test_duplicate_running_frame_is_idempotent_no_regress(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    """重复/乱序 running 帧幂等：CAS(WHERE status='starting') 首帧命中一次，重复帧 rowcount=0
    不回退、不重播广播。"""
    client, env, hub = ctx
    channel, task_id, worktree_id = _seed(env)
    assert worktree_id is not None
    events: list[Any] = []
    token = client.app.state.bus.subscribe(events.append)  # type: ignore[union-attr]
    try:
        with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
            d = StubDaemon(ws)
            d.hello([])
            d.recv_hello_ack()
            drain_revalidation(d)
            session_id = _insert_preview(
                env, task_id=task_id, worktree_id=worktree_id, status="starting", port=None
            )
            frame = {"preview_session_id": session_id, "status": "running", "port": 5200}
            d.report("preview.status", frame)
            d.sync()
            d.report("preview.status", frame)  # 冗余补报
            d.sync()
            # L4a：preview.status 非 ack 上报（异步 writer 落库）——drain 屏障等消费完再读 DB/emit。
            asyncio.run_coroutine_threadsafe(
                hub.drain_reports(env.comp_id), hub._loop
            ).result(timeout=5)
            assert _preview_row(env, session_id)["status"] == "running"
            assert _preview_row(env, session_id)["port"] == 5200
    finally:
        client.app.state.bus.unsubscribe(token)  # type: ignore[union-attr]
    updates = [
        e for e in events if e.type == EventType.PREVIEW_UPDATED and e.channel_id == channel
    ]
    assert len(updates) == 1  # 仅首帧广播，冗余帧 CAS 未命中不重播


def test_failed_frame_after_running_does_not_resurrect_starting(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    """乱序：running→failed 合法（存活监控上报进程夭折）；随后迟到 running 帧不复活（terminal
    单调）。"""
    client, env, _hub = ctx
    _, task_id, worktree_id = _seed(env)
    assert worktree_id is not None
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([])
        d.recv_hello_ack()
        drain_revalidation(d)
        session_id = _insert_preview(
            env, task_id=task_id, worktree_id=worktree_id, status="starting", port=None
        )
        run = {"preview_session_id": session_id, "status": "running", "port": 5300}
        d.report("preview.status", run)
        d.sync()
        d.report(
            "preview.status",
            {"preview_session_id": session_id, "status": "failed", "log_tail": "crash"},
        )
        d.sync()
        assert _poll(lambda: _preview_row(env, session_id)["status"] == "failed")
        # 迟到 running 帧（乱序）：CAS(WHERE status='starting') 未命中 → 不复活 failed。
        d.report("preview.status", run)
        d.sync()
        assert _preview_row(env, session_id)["status"] == "failed"


def test_status_recycled_advances_and_broadcasts(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    """preview.status recycled（daemon stop 确认，缺口 #5）：running→recycled CAS 命中一次 →
    recycled_at 落、广播一条 preview.updated、落 preview.recycled 诊断行；随后重复 recycled 帧 /
    迟到 failed 帧 CAS(WHERE status in active) rowcount=0 幂等 noop（不重播、不改行）。"""
    client, env, _hub = ctx
    channel, task_id, worktree_id = _seed(env)
    assert worktree_id is not None
    events: list[Any] = []
    token = client.app.state.bus.subscribe(events.append)  # type: ignore[union-attr]
    try:
        with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
            d = StubDaemon(ws)
            d.hello([])
            d.recv_hello_ack()
            drain_revalidation(d)
            session_id = _insert_preview(
                env, task_id=task_id, worktree_id=worktree_id, status="running"
            )
            d.report("preview.status", {"preview_session_id": session_id, "status": "recycled"})
            d.sync()
            assert _poll(lambda: _preview_row(env, session_id)["status"] == "recycled")
            assert _preview_row(env, session_id)["recycled_at"] is not None
            # 幂等：重复 recycled + 迟到 failed 均落在 CAS 起态门外 → rowcount=0 noop。
            d.report("preview.status", {"preview_session_id": session_id, "status": "recycled"})
            d.sync()
            d.report(
                "preview.status",
                {"preview_session_id": session_id, "status": "failed", "log_tail": "late"},
            )
            d.sync()
            row = _preview_row(env, session_id)
            assert row["status"] == "recycled"  # 终态未被迟到 failed 复活
            assert row["fail_log_tail"] is None  # 迟到 failed 帧无副作用
    finally:
        client.app.state.bus.unsubscribe(token)  # type: ignore[union-attr]
    updates = [
        e for e in events if e.type == EventType.PREVIEW_UPDATED and e.channel_id == channel
    ]
    assert len(updates) == 1  # 仅首个 recycled 广播，冗余/迟到帧不重播
    assert updates[0].data["preview"]["status"] == "recycled"
    # preview.recycled 诊断行（owner=None → 不广播 DIAGNOSTIC_APPENDED）。
    with env.engine.connect() as c:
        diag = c.execute(select(_DIAG).where(_DIAG.c.task_id == task_id)).mappings().first()
    assert diag is not None and diag["type"] == "preview.recycled"
    assert diag["payload"]["preview_session_id"] == session_id


def test_agent_owner_preview_diagnostic_broadcasts(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    """FR-11.3（缺口 #6）：预览任务 owner 为 Agent 时，进程状态诊断除落库外还广播
    DIAGNOSTIC_APPENDED（归属 owner/channel 让人类可循，同 _record_worktree_failure 体例）。
    running → preview.started 诊断，agent_member_id 正确。"""
    client, env, _hub = ctx
    channel, task_id, worktree_id = _seed(env)
    assert worktree_id is not None
    # owner 指向 Agent（status=offline：非 _RESUMABLE/_DELIVERABLE → reconcile 不额外下发帧）。
    agent_id = env.add_agent("Builder", "offline")
    with env.engine.begin() as c:
        c.execute(
            update(_TASK).where(_TASK.c.id == task_id).values(owner_member_id=agent_id)
        )
    events: list[Any] = []
    token = client.app.state.bus.subscribe(events.append)  # type: ignore[union-attr]
    try:
        with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
            d = StubDaemon(ws)
            d.hello([])
            d.recv_hello_ack()
            drain_revalidation(d)
            session_id = _insert_preview(
                env, task_id=task_id, worktree_id=worktree_id, status="starting", port=None
            )
            d.report(
                "preview.status",
                {"preview_session_id": session_id, "status": "running", "port": 5160},
            )
            d.sync()
            assert _poll(lambda: _preview_row(env, session_id)["status"] == "running")
    finally:
        client.app.state.bus.unsubscribe(token)  # type: ignore[union-attr]
    diags = [e for e in events if e.type == EventType.DIAGNOSTIC_APPENDED]
    assert len(diags) == 1  # Agent owner → 恰一条 DIAGNOSTIC_APPENDED
    assert diags[0].data["agent_member_id"] == agent_id
    pub = diags[0].data["events"][0]
    assert pub["type"] == "preview.started"  # running → preview.started 诊断类型
    assert pub["agent_member_id"] == agent_id
    assert pub["payload"]["preview_session_id"] == session_id


# ---------------------------------------------------------------- 回收三触发


def test_recycle_idle_scan_dispatches_stop(ctx: tuple[TestClient, Env, Any]) -> None:
    """触发①idle：last_active_at 超 preview_idle_min → 下发 preview.stop。"""
    client, env, hub = ctx
    _, task_id, worktree_id = _seed(env, preview_idle_min=30)
    assert worktree_id is not None
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([])
        d.recv_hello_ack()
        drain_revalidation(d)
        session_id = _insert_preview(
            env,
            task_id=task_id,
            worktree_id=worktree_id,
            status="running",
            last_active_at="2020-01-01T00:00:00.000Z",  # 远超 30min idle
        )
        fut = asyncio.run_coroutine_threadsafe(
            hub._run_preview_recycle_scan(), hub._loop
        )
        stop = d.recv_instr()
        assert stop["type"] == "preview.stop"
        assert stop["data"] == {"preview_session_id": session_id}
        d.ack(stop, "done")
        fut.result(timeout=5)


def test_recycle_idle_scan_skips_fresh_preview(ctx: tuple[TestClient, Env, Any]) -> None:
    """idle 负例：last_active_at 新鲜（未超 idle）→ 不下发 stop。"""
    client, env, hub = ctx
    _, task_id, worktree_id = _seed(env, preview_idle_min=30)
    assert worktree_id is not None
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([])
        d.recv_hello_ack()
        drain_revalidation(d)
        _insert_preview(env, task_id=task_id, worktree_id=worktree_id, status="running")
        hub._run_sync(hub._run_preview_recycle_scan())
        d.sync()  # 无 preview.stop 帧


def test_idle_recycle_honors_zero_idle_min(ctx: tuple[TestClient, Env, Any]) -> None:
    """preview_idle_min=0 立即回收（锚定 Fable 修 `or 30`→`is not None`）：与
    test_recycle_idle_scan_skips_fresh_preview 同构（新鲜 running 预览），仅 idle_min=0 → idle 扫描
    立即下发 preview.stop。若 `or 30` 未改，0 被误当 30 → 新鲜预览不回收、无 stop 帧、超时失败。"""
    client, env, hub = ctx
    _, task_id, worktree_id = _seed(env, preview_idle_min=0)
    assert worktree_id is not None
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([])
        d.recv_hello_ack()
        drain_revalidation(d)
        # 新鲜 running 预览（last_active_at=now）：idle_min=0 下任何正 idle 即到期。
        session_id = _insert_preview(
            env, task_id=task_id, worktree_id=worktree_id, status="running"
        )
        fut = asyncio.run_coroutine_threadsafe(
            hub._run_preview_recycle_scan(), hub._loop
        )
        stop = d.recv_instr()
        assert stop["type"] == "preview.stop"
        assert stop["data"] == {"preview_session_id": session_id}
        d.ack(stop, "done")
        fut.result(timeout=5)


def test_recycle_on_task_terminal(ctx: tuple[TestClient, Env, Any]) -> None:
    """触发②任务终态：task→done 的 TASK_UPDATED → 即回收活跃预览（下发 stop）。"""
    client, env, _hub = ctx
    channel, task_id, worktree_id = _seed(env)
    assert worktree_id is not None
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([])
        d.recv_hello_ack()
        drain_revalidation(d)
        session_id = _insert_preview(
            env, task_id=task_id, worktree_id=worktree_id, status="running"
        )
        client.app.state.bus.emit(  # type: ignore[union-attr]
            EventType.TASK_UPDATED,
            channel,
            {
                "task": {"id": task_id, "writes_code": True},
                "change": {"to_status": "done"},
            },
        )
        stop = d.recv_instr()
        assert stop["type"] == "preview.stop"
        assert stop["data"] == {"preview_session_id": session_id}
        d.ack(stop, "done")


def test_recycle_cleanup_preempt_before_worktree_cleanup(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    """触发③cleanup 前置：cleanup 下发前先回收其上活跃预览（stop 先于 worktree.cleanup）。"""
    client, env, hub = ctx
    _, task_id, worktree_id = _seed(env, keep_days=0)
    assert worktree_id is not None
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([])
        d.recv_hello_ack()
        drain_revalidation(d)
        session_id = _insert_preview(
            env, task_id=task_id, worktree_id=worktree_id, status="running"
        )
        # 事后置任务终态（连接时非终态 → hello reconcile 不 cleanup/不 fail-close）。
        with env.engine.begin() as c:
            c.execute(
                update(_TASK)
                .where(_TASK.c.id == task_id)
                .values(status="done", status_changed_at="2020-01-01T00:00:00.000Z")
            )
        fut = asyncio.run_coroutine_threadsafe(
            hub._cleanup_worktree(task_id, env.comp_id), hub._loop
        )
        stop = d.recv_instr()
        assert stop["type"] == "preview.stop"  # 先回收预览
        assert stop["data"] == {"preview_session_id": session_id}
        d.ack(stop, "done")
        cleanup = d.recv_instr()
        assert cleanup["type"] == "worktree.cleanup"  # 后删 worktree
        d.ack(cleanup, "noop")
        fut.result(timeout=5)


# ---------------------------------------------------------------- 对账 #9


def test_reconcile_9_reconnect_fails_active_preview(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    """对账 #9（v1.0.5 真重启口径）：reconnect hello 无 boot_nonce/预览快照（真重启或旧 daemon）
    → 本机活跃预览（running）失进程置 failed('daemon restarted')，不自动重拉（无 preview.start
    帧）。"""
    client, env, _hub = ctx
    channel, task_id, worktree_id = _seed(env)
    assert worktree_id is not None
    session_id = _insert_preview(
        env, task_id=task_id, worktree_id=worktree_id, status="running"
    )
    events: list[Any] = []
    token = client.app.state.bus.subscribe(events.append)  # type: ignore[union-attr]
    try:
        with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
            d = StubDaemon(ws)
            d.hello([])
            d.recv_hello_ack()
            drain_revalidation(d)  # active worktree 复验（预览纠偏在 reconcile 末尾）
            assert _poll(lambda: _preview_row(env, session_id)["status"] == "failed")
            assert _preview_row(env, session_id)["fail_log_tail"] == "daemon restarted"
            d.sync()  # 不自动重拉：无 preview.start 帧（drain 后仅 pong）
    finally:
        client.app.state.bus.unsubscribe(token)  # type: ignore[union-attr]
    updates = [
        e for e in events if e.type == EventType.PREVIEW_UPDATED and e.channel_id == channel
    ]
    assert any(u.data["preview"]["status"] == "failed" for u in updates)


def test_reconcile_9_jitter_preserves_live_preview(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    """对账 #9 v1.0.5 核心：reconnect hello 预览快照含存活条目 → 该 running 行**原样存活**
    （survive WS jitter）；同机无条目的另一活跃行照旧 fail-close（作对账已完成的观察锚点）。"""
    client, env, _hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    project = _project(env, channel)
    task_live = _task(env, channel, number=1, project_id=project)
    task_lost = _task(env, channel, number=2, project_id=project)
    wt_live = _worktree(env, task_id=task_live, project_id=project)
    wt_lost = _worktree(env, task_id=task_lost, project_id=project)
    live_id = _insert_preview(
        env, task_id=task_live, worktree_id=wt_live, status="running", port=5001
    )
    lost_id = _insert_preview(
        env, task_id=task_lost, worktree_id=wt_lost, status="running", port=5002
    )
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello(
            [],
            boot_nonce=nid(),
            previews=[
                {"preview_session_id": live_id, "status": "running", "port": 5001}
            ],
        )
        d.recv_hello_ack()
        drain_revalidation(d, count=2)  # 两 active worktree 各一条复验 ensure
        # 无快照条目的行 fail-close = 对账已跑完的锚点；存活条目行必须原样 running。
        assert _poll(lambda: _preview_row(env, lost_id)["status"] == "failed")
        assert _preview_row(env, live_id)["status"] == "running"
        assert _preview_row(env, live_id)["port"] == 5001
        d.sync()  # 存活行无 preview.stop / preview.start 下发


def test_reconcile_9_replay_promotes_starting_row(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    """对账 #9 v1.0.5 快照重放：断连期丢失的 running 上报经 hello 快照恢复——starting 行被推进为
    running 携 port（同 preview.status CAS 口径）。"""
    client, env, _hub = ctx
    channel, task_id, worktree_id = _seed(env)
    assert worktree_id is not None
    session_id = _insert_preview(
        env, task_id=task_id, worktree_id=worktree_id, status="starting", port=None
    )
    events: list[Any] = []
    token = client.app.state.bus.subscribe(events.append)  # type: ignore[union-attr]
    try:
        with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
            d = StubDaemon(ws)
            d.hello(
                [],
                boot_nonce=nid(),
                previews=[
                    {"preview_session_id": session_id, "status": "running", "port": 6001}
                ],
            )
            d.recv_hello_ack()
            drain_revalidation(d)
            assert _poll(lambda: _preview_row(env, session_id)["status"] == "running")
            assert _preview_row(env, session_id)["port"] == 6001
            d.sync()
    finally:
        client.app.state.bus.unsubscribe(token)  # type: ignore[union-attr]
    updates = [
        e for e in events if e.type == EventType.PREVIEW_UPDATED and e.channel_id == channel
    ]
    assert any(u.data["preview"]["status"] == "running" for u in updates)


def test_reconcile_9_replay_recovers_lost_failed_report(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    """对账 #9 v1.0.5 快照重放：断连期 dev server 崩溃、failed 上报丢失 → 重连 hello 快照携终态
    条目补落（fail_log_tail = daemon 侧真实输出尾，非定型文案）。"""
    client, env, _hub = ctx
    _, task_id, worktree_id = _seed(env)
    assert worktree_id is not None
    session_id = _insert_preview(
        env, task_id=task_id, worktree_id=worktree_id, status="running", port=5001
    )
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello(
            [],
            boot_nonce=nid(),
            previews=[
                {"preview_session_id": session_id, "status": "failed", "log_tail": "boom"}
            ],
        )
        d.recv_hello_ack()
        drain_revalidation(d)
        assert _poll(lambda: _preview_row(env, session_id)["status"] == "failed")
        assert _preview_row(env, session_id)["fail_log_tail"] == "boom"
        d.sync()


def test_reconcile_9_same_nonce_missing_entry_process_lost(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    """对账 #9 v1.0.5 措辞分档：boot_nonce 未变（同 daemon 进程 jitter）但快照无该会话（start
    指令断连期丢失）→ failed('preview process lost')，与真重启 'daemon restarted' 区分。"""
    client, env, _hub = ctx
    _, task_id, worktree_id = _seed(env)
    assert worktree_id is not None
    nonce = nid()
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([], boot_nonce=nonce, previews=[])
        d.recv_hello_ack()
        drain_revalidation(d)
    # 断连期建行（start 指令永失）；同 nonce 重连 → 同进程口径 fail-close。
    session_id = _insert_preview(
        env, task_id=task_id, worktree_id=worktree_id, status="running", port=5001
    )
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([], boot_nonce=nonce, previews=[])
        d.recv_hello_ack()
        drain_revalidation(d)
        assert _poll(lambda: _preview_row(env, session_id)["status"] == "failed")
        assert _preview_row(env, session_id)["fail_log_tail"] == "preview process lost"


def test_reconcile_9_orphan_live_entry_gets_stop(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    """对账 #9 v1.0.5 反向泄漏防护：快照中存活但 DB 行已非活跃（断连期 server 已 fail-close，如
    starting 超时）→ 下发 preview.stop 杀进程；行终态不被 recycled 上报覆盖（CAS 起态门）。"""
    client, env, _hub = ctx
    _, task_id, worktree_id = _seed(env)
    assert worktree_id is not None
    session_id = _insert_preview(
        env, task_id=task_id, worktree_id=worktree_id, status="failed", port=5001
    )
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello(
            [],
            boot_nonce=nid(),
            previews=[
                {"preview_session_id": session_id, "status": "running", "port": 5001}
            ],
        )
        d.recv_hello_ack()
        drain_revalidation(d)
        stop = d.recv_instr()
        assert stop["type"] == "preview.stop"
        assert stop["data"] == {"preview_session_id": session_id}
        d.ack(stop, "done")
        assert _preview_row(env, session_id)["status"] == "failed"  # 终态不回退


def test_reconcile_9_terminal_task_preview_recycled_on_reconnect(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    """对账 #9 v1.0.5 回收触发②补扫：断连期任务转终态（stop 下发丢失）→ 重连时存活预览补下发
    preview.stop（存活不等于该活着——回收判定归 server）。"""
    client, env, _hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    project = _project(env, channel)
    task_id = _task(env, channel, number=1, project_id=project, status="done")
    worktree_id = _worktree(env, task_id=task_id, project_id=project)
    session_id = _insert_preview(
        env, task_id=task_id, worktree_id=worktree_id, status="running", port=5001
    )
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello(
            [],
            boot_nonce=nid(),
            previews=[
                {"preview_session_id": session_id, "status": "running", "port": 5001}
            ],
        )
        d.recv_hello_ack()
        stop = d.recv_instr()
        assert stop["type"] == "preview.stop"
        assert stop["data"] == {"preview_session_id": session_id}
        d.ack(stop, "done")
        # 存活行在 daemon 确认（recycled 上报）前保持 running——判定归 server、事实归 daemon。
        assert _preview_row(env, session_id)["status"] == "running"


def test_reconcile_9_starting_timeout_fails(ctx: tuple[TestClient, Env, Any]) -> None:
    """对账 #9：周期扫描 starting 超时（连接仍在但迟迟未收 preview.status）→ failed；running
    行不被周期触碰。"""
    client, env, hub = ctx
    _, task_id, worktree_id = _seed(env)
    assert worktree_id is not None
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([])
        d.recv_hello_ack()
        drain_revalidation(d)
        starting_id = _insert_preview(
            env, task_id=task_id, worktree_id=worktree_id, status="starting", port=None
        )
        hub.preview_starting_timeout_sec = 0.0  # 任何 starting 立即超时
        # 周期对账（revalidate=False）：不重下发 worktree.ensure，只收 starting 超时。
        hub._run_sync(hub.reconcile(hub._conns[env.comp_id]))
        d.sync()
        assert _preview_row(env, starting_id)["status"] == "failed"
        assert "starting timeout" in _preview_row(env, starting_id)["fail_log_tail"]


def test_reconcile_9_periodic_leaves_running_preview(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    """对账 #9 负例：周期对账不动 running 预览（连接在=daemon 存活监控可达；仅 reconnect 才
    fail-close running）。"""
    client, env, hub = ctx
    _, task_id, worktree_id = _seed(env)
    assert worktree_id is not None
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([])
        d.recv_hello_ack()
        drain_revalidation(d)
        running_id = _insert_preview(
            env, task_id=task_id, worktree_id=worktree_id, status="running"
        )
        hub.preview_starting_timeout_sec = 0.0
        hub._run_sync(hub.reconcile(hub._conns[env.comp_id]))
        d.sync()
        assert _preview_row(env, running_id)["status"] == "running"


# ---------------------------------------------------------------- 单活跃索引竞态


def test_concurrent_double_post_yields_single_active_row(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    """并发双 POST：单活跃部分唯一索引兜底 → 恰一活跃行（IntegrityError 回读现有活跃行 touch）。

    以「预置活跃行 + POST」模拟索引竞态命中：POST 建 starting 行触发 IntegrityError → 回读现有
    活跃行返回 200，不建第二行、不重下发 start。"""
    client, env, _hub = ctx
    _, task_id, worktree_id = _seed(env)
    assert worktree_id is not None
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([])
        d.recv_hello_ack()
        drain_revalidation(d)
        existing = _insert_preview(
            env, task_id=task_id, worktree_id=worktree_id, status="running"
        )
        resp = client.post(f"/api/tasks/{task_id}/preview")
        assert resp.status_code == 200  # 命中现有活跃行（非新建 201）
        assert resp.json()["id"] == existing
        d.sync()  # 无新 preview.start 帧

    with env.engine.connect() as c:
        active = list(
            c.execute(
                select(_PREVIEW.c.id).where(
                    _PREVIEW.c.task_id == task_id,
                    _PREVIEW.c.status.in_(models.PREVIEW_ACTIVE_STATUSES),
                )
            )
        )
    assert len(active) == 1  # 恰一活跃行


# ---------------------------------------------------------------- GET / DELETE


def test_get_reads_active_then_recent_else_404(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    _, task_id, worktree_id = _seed(env)
    assert worktree_id is not None
    # 无会话 → 404
    assert client.get(f"/api/tasks/{task_id}/preview").status_code == 404
    # 有活跃 → 返回活跃行（纯读无副作用）
    running_id = _insert_preview(env, task_id=task_id, worktree_id=worktree_id, status="running")
    got = client.get(f"/api/tasks/{task_id}/preview")
    assert got.status_code == 200
    assert got.json()["id"] == running_id
    assert got.json()["status"] == "running"


def test_delete_dispatches_stop(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    _, task_id, worktree_id = _seed(env)
    assert worktree_id is not None
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([])
        d.recv_hello_ack()
        drain_revalidation(d)
        session_id = _insert_preview(
            env, task_id=task_id, worktree_id=worktree_id, status="running"
        )
        resp = client.delete(f"/api/tasks/{task_id}/preview")
        assert resp.status_code == 200
        assert resp.json()["id"] == session_id
        stop = d.recv_instr()
        assert stop["type"] == "preview.stop"
        assert stop["data"] == {"preview_session_id": session_id}
        d.ack(stop, "done")


def test_delete_404_when_no_active(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    _, task_id, _ = _seed(env)
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([])
        d.recv_hello_ack()
        drain_revalidation(d)
        resp = client.delete(f"/api/tasks/{task_id}/preview")
    assert resp.status_code == 404


def test_tx_after_commit_runs_on_success_and_skips_on_rollback(
    migrated_engine: Engine,
) -> None:
    """Tx.after_commit 契约（Fable CAS 修复锚定）：preview.start 下发挪到提交后回调——成功路径
    在 commit + 事件 flush 之后执行（get_tx else 分支），异常回滚路径不执行（不会向 daemon 下发
    一个永不落库的 starting 行）。commit-before-callback 由 get_tx 结构保证，实机 verify 再压实。"""
    from types import SimpleNamespace

    from coagentia_server.deps import get_tx
    from coagentia_server.events import EventBus

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(engine=migrated_engine, bus=EventBus()))
    )

    ran: list[str] = []
    gen = get_tx(request)  # type: ignore[arg-type]
    tx = next(gen)
    tx.after_commit(lambda: ran.append("ok"))
    with pytest.raises(StopIteration):
        next(gen)  # 触发 else：commit → 事件 flush → after_commit 回调
    assert ran == ["ok"]

    ran2: list[str] = []
    gen2 = get_tx(request)  # type: ignore[arg-type]
    tx2 = next(gen2)
    tx2.after_commit(lambda: ran2.append("x"))
    with pytest.raises(ValueError):
        gen2.throw(ValueError("boom"))  # 回滚路径：except 分支不执行 after_commit
    assert ran2 == []
