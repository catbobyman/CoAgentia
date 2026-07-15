"""PS-WT W2 验收：fs 代理、管理台合账矩阵、清理门四连、孤儿清理（设计 §5）。

合账矩阵穷举用纯函数 reconcile_console（快、确定）；fs 代理 / 清理端点用真 server + StubDaemon
驱动 /api/daemon/ws（同 test_diff 骨架：阻塞调用挪后台线程，daemon 侧 recv/reply/ack）。
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from coagentia_contracts.ws import EventType
from coagentia_server.app import create_app
from coagentia_server.db import models
from coagentia_server.ledger.service import now_iso
from coagentia_server.worktrees import console as console_service
from daemon_helpers import AUTH, Env, StubDaemon, nid
from fastapi.testclient import TestClient
from sqlalchemy import insert
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


def _bg(fn: Callable[[], Any]) -> tuple[threading.Thread, dict[str, Any]]:
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["response"] = fn()
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc

    thread = threading.Thread(target=run)
    thread.start()
    return thread, box


def _project(env: Env, *, name: str = "P", computer_id: str | None = None) -> str:
    pid = nid()
    with env.engine.begin() as c:
        c.execute(
            insert(models.Project.__table__).values(
                id=pid,
                workspace_id=env.ws_id,
                computer_id=computer_id or env.comp_id,
                name=name,
                repo_path=r"D:\repo",
                created_at=now_iso(),
            )
        )
    return pid


def _task(
    env: Env, channel_id: str, project_id: str, *, number: int, status: str = "in_review"
) -> str:
    tid = nid()
    msg = env.add_message(channel_id, author=env.owner_id, body="x")
    with env.engine.begin() as c:
        c.execute(
            insert(models.Task.__table__).values(
                id=tid,
                workspace_id=env.ws_id,
                channel_id=channel_id,
                number=number,
                root_message_id=msg,
                title=f"Task {number}",
                status=status,
                level="l2",
                created_by_member_id=env.owner_id,
                project_id=project_id,
                writes_code=True,
                status_changed_at=now_iso(),
                created_at=now_iso(),
            )
        )
    return tid


def _worktree(
    env: Env, *, task_id: str, project_id: str, status: str, branch: str | None = None
) -> str:
    wid = nid()
    with env.engine.begin() as c:
        c.execute(
            insert(models.Worktree.__table__).values(
                id=wid,
                workspace_id=env.ws_id,
                project_id=project_id,
                task_id=task_id,
                branch=branch or f"coagentia/task-{task_id}",
                path=rf"D:\wt\{project_id}\{task_id}",
                status=status,
                created_at=now_iso(),
                merged_at=now_iso() if status in ("merged", "cleaned") else None,
                cleaned_at=now_iso() if status == "cleaned" else None,
            )
        )
    return wid


def _preview(env: Env, *, task_id: str, worktree_id: str, status: str = "running") -> str:
    pid = nid()
    with env.engine.begin() as c:
        c.execute(
            insert(models.PreviewSession.__table__).values(
                id=pid,
                workspace_id=env.ws_id,
                task_id=task_id,
                worktree_id=worktree_id,
                status=status,
                started_at=now_iso(),
            )
        )
    return pid


def _scan_entry(project_id: str, task_id: str, **over: Any) -> dict[str, Any]:
    entry = {
        "project_id": project_id,
        "task_id": task_id,
        "path": rf"D:\wt\{project_id}\{task_id}",
        "branch": f"coagentia/task-{task_id}",
        "head_commit": "abc123",
        "dirty": False,
        "ahead": 0,
        "behind": 0,
        "error": None,
    }
    entry.update(over)
    return entry


def _agent_headers(env: Env) -> dict[str, str]:
    agent_id = env.add_agent("A", "idle")
    return {"X-Acting-Member": agent_id, **AUTH}


# ============================================================ 合账矩阵（纯函数穷举）


def _db_row(
    *,
    project_id: str,
    task_id: str,
    status: str,
    computer_id: str = "01K5CMPT00000000000000000A",
    wid: str = "01K5WTREE0000000000000000A",
) -> dict[str, Any]:
    return {
        "id": wid,
        "workspace_id": "01K5WKSP00000000000000000A",
        "project_id": project_id,
        "task_id": task_id,
        "branch": f"coagentia/task-{task_id}",
        "path": rf"D:\wt\{project_id}\{task_id}",
        "status": status,
        "merge_commit": None,
        "created_at": now_iso(),
        "merged_at": None,
        "cleaned_at": None,
        "computer_id": computer_id,
        "project_name": "P",
        "task_title": "T",
        "channel_id": "01K5CHAN00000000000000000A",
    }


CID = "01K5CMPT00000000000000000A"
PID = "01K5PROJ00000000000000000A"


def test_reconcile_db_and_disk_is_ok_with_live() -> None:
    """象限①：DB active ∩ 磁盘 → derived=ok + live 字段。"""
    row = _db_row(project_id=PID, task_id="01K5TASKAAAAAAAAAAAAAAAAAA", status="active")
    scans = {CID: ("ok", [_scan_entry(PID, "01K5TASKAAAAAAAAAAAAAAAAAA", dirty=True, behind=3)])}
    items, statuses = console_service.reconcile_console(
        db_rows=[row], scans=scans, project_names={PID: "P"}, task_info={}, live=True
    )
    assert len(items) == 1
    assert items[0].derived == "ok"
    assert items[0].live is not None
    assert items[0].live.dirty is True
    assert items[0].live.behind == 3
    assert [s.status for s in statuses] == ["ok"]


def test_reconcile_active_without_disk_is_missing() -> None:
    """象限②：DB active 磁盘无 → derived=missing、live=None。"""
    row = _db_row(project_id=PID, task_id="01K5TASKBBBBBBBBBBBBBBBBBB", status="active")
    scans = {CID: ("ok", [])}
    items, _ = console_service.reconcile_console(
        db_rows=[row], scans=scans, project_names={PID: "P"}, task_info={}, live=True
    )
    assert items[0].derived == "missing"
    assert items[0].live is None


def test_reconcile_disk_only_is_orphan() -> None:
    """象限③：磁盘有树无 DB 登记 → 追加孤儿行 derived=orphan、id=None。"""
    scans = {CID: ("ok", [_scan_entry(PID, "01K5ORPHANAAAAAAAAAAAAAAAA")])}
    items, _ = console_service.reconcile_console(
        db_rows=[], scans=scans, project_names={PID: "P"}, task_info={}, live=True
    )
    assert len(items) == 1
    orphan = items[0]
    assert orphan.derived == "orphan"
    assert orphan.id is None
    assert orphan.status is None
    assert orphan.task_id == "01K5ORPHANAAAAAAAAAAAAAAAA"
    assert orphan.project_name == "P"
    assert orphan.live is not None  # 孤儿携磁盘 live 数据


def test_reconcile_terminal_without_disk_is_ok() -> None:
    """象限④：DB merged/cleaned 磁盘无 → derived=ok（终态无树正常形态）。"""
    merged = _db_row(project_id=PID, task_id="01K5TASKCCCCCCCCCCCCCCCCCC", status="merged")
    cleaned = _db_row(
        project_id=PID, task_id="01K5TASKDDDDDDDDDDDDDDDDDD", status="cleaned",
        wid="01K5WTREE0000000000000000B",
    )
    scans = {CID: ("ok", [])}
    items, _ = console_service.reconcile_console(
        db_rows=[merged, cleaned], scans=scans, project_names={PID: "P"}, task_info={}, live=True
    )
    assert {it.task_id: it.derived for it in items} == {
        "01K5TASKCCCCCCCCCCCCCCCCCC": "ok",
        "01K5TASKDDDDDDDDDDDDDDDDDD": "ok",
    }
    assert all(it.live is None for it in items)


def test_reconcile_cleaned_with_disk_appends_orphan_drift() -> None:
    """cleaned 有树 = 清理漂移：cleaned 行保 ok，另追加孤儿行浮出漂移（设计 §5.2 表末行）。"""
    cleaned = _db_row(project_id=PID, task_id="01K5TASKEEEEEEEEEEEEEEEEEE", status="cleaned")
    scans = {CID: ("ok", [_scan_entry(PID, "01K5TASKEEEEEEEEEEEEEEEEEE")])}
    items, _ = console_service.reconcile_console(
        db_rows=[cleaned], scans=scans, project_names={PID: "P"}, task_info={}, live=True
    )
    derived = sorted(it.derived for it in items)
    assert derived == ["ok", "orphan"]  # 登记 cleaned 行 + 漂移孤儿行


def test_reconcile_machine_offline_keeps_db_state_no_live() -> None:
    """整机离线：该机行保 DB 态 derived=ok、live=None；scans 标 offline。"""
    row = _db_row(project_id=PID, task_id="01K5TASKFFFFFFFFFFFFFFFFFF", status="active")
    scans: dict[str, console_service.ScanOutcome] = {CID: ("offline", None)}
    items, statuses = console_service.reconcile_console(
        db_rows=[row], scans=scans, project_names={PID: "P"}, task_info={}, live=True
    )
    assert items[0].derived == "ok"  # 无从判定丢失/漂移 → 保 DB 态
    assert items[0].live is None
    assert [(s.computer_id, s.status) for s in statuses] == [(CID, "offline")]


def test_reconcile_live_zero_is_pure_skeleton() -> None:
    """live=0：纯 DB 骨架，全部 derived=ok、live=None、scans=[]。"""
    active = _db_row(project_id=PID, task_id="01K5TASKGGGGGGGGGGGGGGGGGG", status="active")
    items, statuses = console_service.reconcile_console(
        db_rows=[active], scans={}, project_names={PID: "P"}, task_info={}, live=False
    )
    assert items[0].derived == "ok"
    assert items[0].live is None
    assert statuses == []


def test_reconcile_orphan_pulls_task_info_when_present() -> None:
    """孤儿行 task 仍存在（未删任务）→ 补 task_title/channel_id 供前端跳转。"""
    scans = {CID: ("ok", [_scan_entry(PID, "01K5ORPHANBBBBBBBBBBBBBBBB")])}
    items, _ = console_service.reconcile_console(
        db_rows=[],
        scans=scans,
        project_names={PID: "P"},
        task_info={"01K5ORPHANBBBBBBBBBBBBBBBB": ("孤儿任务", "01K5CHAN00000000000000000A")},
        live=True,
    )
    assert items[0].task_title == "孤儿任务"
    assert items[0].channel_id == "01K5CHAN00000000000000000A"


# ============================================================ fs 代理（§5.1）


def test_fs_offline_is_503(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    response = client.get(f"/api/computers/{env.comp_id}/fs")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DAEMON_OFFLINE"


def test_fs_unknown_computer_is_404(ctx: tuple[TestClient, Env, Any]) -> None:
    client, _env, _hub = ctx
    response = client.get("/api/computers/01K5CMPT0000000000000000ZZ/fs")
    assert response.status_code == 404


def test_fs_agent_actor_is_403(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    response = client.get(f"/api/computers/{env.comp_id}/fs", headers=_agent_headers(env))
    assert response.status_code == 403


def test_fs_proxies_reply(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    expected = {
        "entries": [
            {"name": "C:\\", "path": "C:\\", "has_git": False, "denied": False},
            {"name": "repo", "path": r"C:\repo", "has_git": True, "denied": False},
        ],
        "truncated": False,
    }
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        daemon = StubDaemon(ws)
        daemon.hello([])
        daemon.recv_hello_ack()
        thread, box = _bg(
            lambda: client.get(f"/api/computers/{env.comp_id}/fs", params={"path": "C:\\"})
        )
        query = daemon.recv()
        assert query["kind"] == "query" and query["type"] == "fs.tree"
        assert query["data"] == {"path": "C:\\"}
        daemon.reply(query, expected)
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert "error" not in box, box.get("error")
    response = box["response"]
    assert response.status_code == 200
    assert response.json() == expected


# ============================================================ 管理台读面（§5.2）


def test_console_live_zero_skeleton(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    channel = env.add_channel(name="c")
    pid = _project(env)
    t1 = _task(env, channel, pid, number=1, status="in_review")
    t2 = _task(env, channel, pid, number=2, status="done")
    _worktree(env, task_id=t1, project_id=pid, status="active")
    _worktree(env, task_id=t2, project_id=pid, status="cleaned")

    response = client.get("/api/worktrees", params={"live": 0})
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 2
    assert {it["derived"] for it in body["items"]} == {"ok"}
    assert all(it["live"] is None for it in body["items"])
    assert body["scans"] == []


def test_console_member_read_no_admin(ctx: tuple[TestClient, Env, Any]) -> None:
    """读面 = workspace 成员（浏览器 Owner），非 admin 门。"""
    client, _env, _hub = ctx
    response = client.get("/api/worktrees", params={"live": 0})
    assert response.status_code == 200


def test_console_agent_actor_is_403(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    response = client.get(
        "/api/worktrees", params={"live": 0}, headers=_agent_headers(env)
    )
    assert response.status_code == 403


def test_console_live_machine_offline(ctx: tuple[TestClient, Env, Any]) -> None:
    """live=1 但该机无 daemon 连接 → scans 标 offline，行保 DB 态 live=None。"""
    client, env, _hub = ctx
    channel = env.add_channel(name="c")
    pid = _project(env)
    t1 = _task(env, channel, pid, number=1, status="in_review")
    _worktree(env, task_id=t1, project_id=pid, status="merged")

    response = client.get("/api/worktrees", params={"live": 1})
    assert response.status_code == 200
    body = response.json()
    assert body["scans"] == [{"computer_id": env.comp_id, "status": "offline"}]
    assert body["items"][0]["derived"] == "ok"
    assert body["items"][0]["live"] is None


def test_console_live_reconciles_ok_and_orphan(ctx: tuple[TestClient, Env, Any]) -> None:
    """live=1 真扫描：merged 树 ∩ 磁盘 → ok+live；磁盘多出的树 → orphan。"""
    client, env, _hub = ctx
    channel = env.add_channel(name="c")
    pid = _project(env)
    t1 = _task(env, channel, pid, number=1, status="done")
    _worktree(env, task_id=t1, project_id=pid, status="merged")
    orphan_task = "01K5ORPHANCCCCCCCCCCCCCCCC"

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        daemon = StubDaemon(ws)
        daemon.hello([])
        daemon.recv_hello_ack()
        # merged 非 active → 无握手复验 ensure（revalidation 仅 active）。
        thread, box = _bg(lambda: client.get("/api/worktrees", params={"live": 1}))
        query = daemon.recv()
        assert query["kind"] == "query" and query["type"] == "worktree.scan"
        daemon.reply(
            query,
            {"entries": [_scan_entry(pid, t1, dirty=True), _scan_entry(pid, orphan_task)]},
        )
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert "error" not in box, box.get("error")
    body = box["response"].json()
    by_task = {it["task_id"]: it for it in body["items"]}
    assert by_task[t1]["derived"] == "ok"
    assert by_task[t1]["live"]["dirty"] is True
    assert by_task[orphan_task]["derived"] == "orphan"
    assert by_task[orphan_task]["id"] is None
    assert body["scans"] == [{"computer_id": env.comp_id, "status": "ok"}]


# ============================================================ 清理门四连（§5.3）


def test_cleanup_active_is_not_terminal_409(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    channel = env.add_channel(name="c")
    pid = _project(env)
    t1 = _task(env, channel, pid, number=1)
    wid = _worktree(env, task_id=t1, project_id=pid, status="active")

    response = client.post(f"/api/worktrees/{wid}/cleanup")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "WORKTREE_NOT_TERMINAL"


def test_cleanup_preview_active_409(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    channel = env.add_channel(name="c")
    pid = _project(env)
    t1 = _task(env, channel, pid, number=1)
    wid = _worktree(env, task_id=t1, project_id=pid, status="merged")
    _preview(env, task_id=t1, worktree_id=wid, status="running")

    response = client.post(f"/api/worktrees/{wid}/cleanup")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "WORKTREE_PREVIEW_ACTIVE"


def test_cleanup_agent_actor_is_403(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    channel = env.add_channel(name="c")
    pid = _project(env)
    t1 = _task(env, channel, pid, number=1)
    wid = _worktree(env, task_id=t1, project_id=pid, status="merged")

    response = client.post(f"/api/worktrees/{wid}/cleanup", headers=_agent_headers(env))
    assert response.status_code == 403
    assert response.json()["error"]["rule"] == "O9"


def test_cleanup_offline_is_503(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    channel = env.add_channel(name="c")
    pid = _project(env)
    t1 = _task(env, channel, pid, number=1)
    wid = _worktree(env, task_id=t1, project_id=pid, status="merged")

    response = client.post(f"/api/worktrees/{wid}/cleanup")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DAEMON_OFFLINE"


def test_cleanup_happy_converges_cleaned_and_broadcasts(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    client, env, _hub = ctx
    channel = env.add_channel(name="c")
    pid = _project(env)
    t1 = _task(env, channel, pid, number=1, status="done")
    wid = _worktree(env, task_id=t1, project_id=pid, status="merged")

    events: list[Any] = []
    token = client.app.state.bus.subscribe(events.append)  # type: ignore[union-attr]
    try:
        with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
            daemon = StubDaemon(ws)
            daemon.hello([])
            daemon.recv_hello_ack()
            thread, box = _bg(lambda: client.post(f"/api/worktrees/{wid}/cleanup"))
            cleanup = daemon.recv_instr()
            assert cleanup["type"] == "worktree.cleanup"
            assert cleanup["data"]["task_id"] == t1
            daemon.ack(cleanup, "done")
            thread.join(timeout=5)
    finally:
        client.app.state.bus.unsubscribe(token)  # type: ignore[union-attr]

    assert not thread.is_alive()
    assert "error" not in box, box.get("error")
    response = box["response"]
    assert response.status_code == 200
    assert response.json()["status"] == "cleaned"
    assert response.json()["cleaned_at"] is not None
    updates = [e for e in events if e.type == EventType.WORKTREE_UPDATED]
    assert [e.data["worktree"]["id"] for e in updates] == [wid]


def test_cleanup_second_send_after_cleaned_is_409(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    """并发/重复清理：首发收敛 cleaned 后二发门校验拒非终态 → 409（CAS 纪律的门层实现）。"""
    client, env, _hub = ctx
    channel = env.add_channel(name="c")
    pid = _project(env)
    t1 = _task(env, channel, pid, number=1, status="done")
    wid = _worktree(env, task_id=t1, project_id=pid, status="merged")

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        daemon = StubDaemon(ws)
        daemon.hello([])
        daemon.recv_hello_ack()
        thread, box = _bg(lambda: client.post(f"/api/worktrees/{wid}/cleanup"))
        cleanup = daemon.recv_instr()
        daemon.ack(cleanup, "done")
        thread.join(timeout=5)
        assert box["response"].status_code == 200
        # 二发：worktree 已 cleaned，门校验 status 非 merged/conflicted → 409。
        second = client.post(f"/api/worktrees/{wid}/cleanup")

    assert second.status_code == 409
    assert second.json()["error"]["code"] == "WORKTREE_NOT_TERMINAL"


def test_finalize_console_cleanup_is_idempotent(ctx: tuple[TestClient, Env, Any]) -> None:
    """finalize 幂等（daemon 异步 worktree.status 上报可能先行收敛）：首发 CAS 收敛+广播，
    二发行已 cleaned → 返回同行、不重复广播。"""
    client, env, hub = ctx
    channel = env.add_channel(name="c")
    pid = _project(env)
    t1 = _task(env, channel, pid, number=1, status="done")
    wid = _worktree(env, task_id=t1, project_id=pid, status="merged")

    events: list[Any] = []
    token = client.app.state.bus.subscribe(events.append)  # type: ignore[union-attr]
    try:
        first = hub.finalize_console_cleanup(task_id=t1, computer_id=env.comp_id)
        second = hub.finalize_console_cleanup(task_id=t1, computer_id=env.comp_id)
    finally:
        client.app.state.bus.unsubscribe(token)  # type: ignore[union-attr]

    assert first is not None and first["id"] == wid and first["status"] == "cleaned"
    assert second is not None and second["status"] == "cleaned"
    updates = [e for e in events if e.type == EventType.WORKTREE_UPDATED]
    assert len(updates) == 1  # 幂等：仅首发广播一次


# ============================================================ 孤儿清理（§5.3）


def test_cleanup_orphan_not_orphan_409(ctx: tuple[TestClient, Env, Any]) -> None:
    """存在非 cleaned 登记行 → 409 WORKTREE_NOT_ORPHAN（防把登记树当孤儿删）。"""
    client, env, _hub = ctx
    channel = env.add_channel(name="c")
    pid = _project(env)
    t1 = _task(env, channel, pid, number=1)
    _worktree(env, task_id=t1, project_id=pid, status="active")

    response = client.post(
        f"/api/computers/{env.comp_id}/worktrees/cleanup-orphan",
        json={"project_id": pid, "task_id": t1},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "WORKTREE_NOT_ORPHAN"


def test_cleanup_orphan_offline_is_503(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    pid = _project(env)
    response = client.post(
        f"/api/computers/{env.comp_id}/worktrees/cleanup-orphan",
        json={"project_id": pid, "task_id": "01K5ORPHANDDDDDDDDDDDDDDDD"},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DAEMON_OFFLINE"


def test_cleanup_orphan_agent_actor_is_403(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    pid = _project(env)
    response = client.post(
        f"/api/computers/{env.comp_id}/worktrees/cleanup-orphan",
        json={"project_id": pid, "task_id": "01K5ORPHANDDDDDDDDDDDDDDDD"},
        headers=_agent_headers(env),
    )
    assert response.status_code == 403


def test_cleanup_orphan_removed_carries_project_task_ids(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    """无非 cleaned 登记行 + daemon 在线 → 下发（携 project_id）→ removed=True，不广播。"""
    client, env, _hub = ctx
    pid = _project(env)
    orphan_task = nid()  # 有效 ULID：WorktreeCleanupData.task_id 是 Ulid 类型

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        daemon = StubDaemon(ws)
        daemon.hello([])
        daemon.recv_hello_ack()
        thread, box = _bg(
            lambda: client.post(
                f"/api/computers/{env.comp_id}/worktrees/cleanup-orphan",
                json={"project_id": pid, "task_id": orphan_task},
            )
        )
        cleanup = daemon.recv_instr()
        assert cleanup["type"] == "worktree.cleanup"
        assert cleanup["data"]["task_id"] == orphan_task
        assert cleanup["data"]["project_id"] == pid
        daemon.ack(cleanup, "done")
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert "error" not in box, box.get("error")
    response = box["response"]
    assert response.status_code == 200
    assert response.json() == {"project_id": pid, "task_id": orphan_task, "removed": True}
