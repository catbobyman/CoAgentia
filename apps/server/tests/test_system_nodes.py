"""M6a J5：系统节点自动触发、check/merge 推进、retry 与冲突派回。"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from coagentia_contracts.ws import EventType
from coagentia_server.app import create_app
from coagentia_server.db import models
from coagentia_server.ledger.service import now_iso
from coagentia_server.worktrees import service as worktree_service
from daemon_helpers import AUTH, Env, StubDaemon, nid
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.engine import Engine

DAEMON_WS = "/api/daemon/ws"

_PROJECT = models.tbl(models.Project)
_COMPUTER = models.tbl(models.Computer)
_CHANNEL_PROJECT = models.tbl(models.ChannelProject)
_CHANNEL = models.tbl(models.Channel)
_CANVAS = models.tbl(models.Canvas)
_TASK = models.tbl(models.Task)
_NODE = models.tbl(models.CanvasNode)
_EDGE = models.tbl(models.CanvasEdge)
_WORKTREE = models.tbl(models.Worktree)
_MESSAGE = models.tbl(models.Message)
_DIAG = models.tbl(models.DiagnosticEvent)


@pytest.fixture
def ctx(migrated_engine: Engine, tmp_path: Path) -> Iterator[tuple[TestClient, Env, Any]]:
    app = create_app(engine=migrated_engine, data_root=tmp_path / "data")
    hub = app.state.daemon_hub
    hub.ack_timeout = 0.5
    hub.reconcile_interval = 3600
    env = Env(migrated_engine)
    with TestClient(app) as client:
        yield client, env, hub


def _poll(fn: Callable[[], bool], timeout: float = 4.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if fn():
            return True
        time.sleep(0.02)
    return False


def _project(env: Env, channel_id: str, *, keep_days: int = 7) -> str:
    project_id = nid()
    with env.engine.begin() as c:
        c.execute(
            insert(_PROJECT).values(
                id=project_id,
                workspace_id=env.ws_id,
                computer_id=env.comp_id,
                name="Demo",
                repo_path=r"D:\repos\demo",
                worktree_keep_days=keep_days,
                created_at=now_iso(),
            )
        )
        c.execute(
            insert(_CHANNEL_PROJECT).values(channel_id=channel_id, project_id=project_id)
        )
    return project_id


def _computer(env: Env, *, key: str) -> tuple[str, dict[str, str]]:
    computer_id = nid()
    with env.engine.begin() as c:
        c.execute(
            insert(_COMPUTER).values(
                id=computer_id,
                workspace_id=env.ws_id,
                name=f"Rig {computer_id[-4:]}",
                api_key_hash=hashlib.sha256(key.encode()).hexdigest(),
                status="offline",
                created_at=now_iso(),
            )
        )
    return computer_id, {"Authorization": f"Bearer {key}"}


def _canvas(env: Env, channel_id: str) -> str:
    canvas_id = nid()
    with env.engine.begin() as c:
        c.execute(
            insert(_CANVAS).values(
                id=canvas_id,
                workspace_id=env.ws_id,
                channel_id=channel_id,
                baseline_hash="0" * 64,
                updated_at=now_iso(),
            )
        )
    return canvas_id


def _task_node(
    env: Env,
    channel_id: str,
    canvas_id: str,
    project_id: str,
    *,
    number: int,
    owner: str | None = None,
    status: str = "done",
    path: str | None = None,
) -> tuple[str, str]:
    root_id = env.add_message(channel_id, kind="system", body=f"task {number}")
    task_id, node_id = nid(), nid()
    worktree_path = path or rf"D:\trees\{project_id}\{task_id}"
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
                writes_code=True,
                status_changed_at=now_iso(),
                created_at=now_iso(),
            )
        )
        c.execute(
            update(_CHANNEL)
            .where(_CHANNEL.c.id == channel_id)
            .values(next_task_number=max(number + 1, 2))
        )
        c.execute(
            insert(_NODE).values(
                id=node_id,
                canvas_id=canvas_id,
                kind="agent",
                task_id=task_id,
                created_at=now_iso(),
            )
        )
        c.execute(
            insert(_WORKTREE).values(
                id=nid(),
                workspace_id=env.ws_id,
                project_id=project_id,
                task_id=task_id,
                branch=f"coagentia/task-{task_id}",
                path=worktree_path,
                status="active",
                created_at=now_iso(),
            )
        )
    return task_id, node_id


def _system_node(
    env: Env,
    canvas_id: str,
    *,
    action: str,
    status: str = "idle",
    command: str | None = None,
) -> str:
    node_id = nid()
    with env.engine.begin() as c:
        c.execute(
            insert(_NODE).values(
                id=node_id,
                canvas_id=canvas_id,
                kind="system",
                system_action=action,
                command=command,
                system_status=status,
                created_at=now_iso(),
            )
        )
    return node_id


def _edge(env: Env, canvas_id: str, source: str, target: str) -> None:
    with env.engine.begin() as c:
        c.execute(
            insert(_EDGE).values(
                id=nid(),
                canvas_id=canvas_id,
                from_node_id=source,
                to_node_id=target,
            )
        )


def _node_status(env: Env, node_id: str) -> str:
    with env.engine.connect() as c:
        return c.execute(
            select(_NODE.c.system_status).where(_NODE.c.id == node_id)
        ).scalar_one()


def _worktree(env: Env, task_id: str) -> dict[str, Any]:
    with env.engine.connect() as c:
        return dict(
            c.execute(select(_WORKTREE).where(_WORKTREE.c.task_id == task_id))
            .mappings()
            .one()
        )


def _messages(env: Env, channel_id: str) -> list[str]:
    with env.engine.connect() as c:
        return list(
            c.execute(
                select(_MESSAGE.c.body)
                .where(_MESSAGE.c.channel_id == channel_id)
                .order_by(_MESSAGE.c.created_at, _MESSAGE.c.id)
            ).scalars()
        )


def test_check_auto_trigger_success_output_and_terminal_replay(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    client, env, _hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    project = _project(env, channel)
    canvas = _canvas(env, channel)
    _, task_node = _task_node(env, channel, canvas, project, number=1)
    check_node = _system_node(env, canvas, action="check", command="uv run pytest -q")
    _edge(env, canvas, task_node, check_node)

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        daemon = StubDaemon(ws)
        daemon.hello([])
        daemon.recv_hello_ack()
        run = daemon.recv_instr()
        assert run["type"] == "check.run"
        assert run["data"]["node_id"] == check_node
        assert run["data"]["project_id"] == project
        assert run["data"]["command"] == "uv run pytest -q"

        # 重复画布事件发生在 check 终态前：不得把同 run_id 再下发一次。
        client.app.state.bus.emit(  # type: ignore[union-attr]
            EventType.CANVAS_NODE_UPDATED,
            channel,
            {"node": {"id": check_node, "system_status": "running"}},
        )
        daemon.ack(run, "done")
        daemon.sync()

        report_id = daemon.report(
            "check.finished",
            {
                "run_id": run["data"]["run_id"],
                "node_id": check_node,
                "status": "success",
                "exit_code": 0,
                "output_tail": "2 passed\n中文输出",
            },
        )
        report_ack = daemon.recv()
        assert report_ack["kind"] == "ack" and report_ack["ref"] == report_id
        assert _poll(lambda: _node_status(env, check_node) == "success")

        bodies = _messages(env, channel)
        assert any(
            f"node_id: {check_node}" in body and "2 passed\n中文输出" in body
            for body in bodies
        )
        before = len(bodies)
        duplicate_id = daemon.report(
            "check.finished",
            {
                "run_id": run["data"]["run_id"],
                "node_id": check_node,
                "status": "success",
                "exit_code": 0,
                "output_tail": "2 passed\n中文输出",
            },
        )
        duplicate_ack = daemon.recv()
        assert duplicate_ack["ref"] == duplicate_id
        daemon.sync()
        assert len(_messages(env, channel)) == before


def test_check_failure_output_retry_uses_new_run_id(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    client, env, _hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    project = _project(env, channel)
    canvas = _canvas(env, channel)
    _, task_node = _task_node(env, channel, canvas, project, number=1)
    check_node = _system_node(env, canvas, action="check", command="pnpm test")
    _edge(env, canvas, task_node, check_node)

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        daemon = StubDaemon(ws)
        daemon.hello([])
        daemon.recv_hello_ack()
        first = daemon.recv_instr()
        first_run_id = first["data"]["run_id"]
        report_id = daemon.report(
            "check.finished",
            {
                "run_id": first_run_id,
                "node_id": check_node,
                "status": "failed",
                "exit_code": 2,
                "output_tail": "FAILED tests/test_demo.py::test_x",
            },
        )
        assert daemon.recv()["ref"] == report_id
        daemon.ack(first, "done")
        assert _poll(lambda: _node_status(env, check_node) == "failed")
        assert any(
            f"node_id: {check_node}" in body and "FAILED tests/test_demo.py::test_x" in body
            for body in _messages(env, channel)
        )

        retried = client.post(f"/api/canvas-nodes/{check_node}/retry")
        assert retried.status_code == 202
        second = daemon.recv_instr()
        assert second["type"] == "check.run"
        assert second["data"]["run_id"] != first_run_id
        second_report = daemon.report(
            "check.finished",
            {
                "run_id": second["data"]["run_id"],
                "node_id": check_node,
                "status": "success",
                "exit_code": 0,
                "output_tail": "all green",
            },
        )
        assert daemon.recv()["ref"] == second_report
        daemon.ack(second, "done")
        assert _poll(lambda: _node_status(env, check_node) == "success")


def test_running_check_reconnect_reuses_same_run_id(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    client, env, _hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    project = _project(env, channel)
    canvas = _canvas(env, channel)
    _, task_node = _task_node(env, channel, canvas, project, number=1)
    check_node = _system_node(env, canvas, action="check", command="long check")
    _edge(env, canvas, task_node, check_node)

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        first_daemon = StubDaemon(ws)
        first_daemon.hello([])
        first_daemon.recv_hello_ack()
        first = first_daemon.recv_instr()
        first_daemon.ack(first, "done")
        first_daemon.sync()
    assert _node_status(env, check_node) == "running"

    moved_computer, _ = _computer(env, key="cak_check_moved_project")
    with env.engine.begin() as c:
        c.execute(
            update(_PROJECT)
            .where(_PROJECT.c.id == project)
            .values(computer_id=moved_computer, repo_path=r"D:\repos\moved")
        )
        c.execute(
            update(_NODE).where(_NODE.c.id == check_node).values(command="changed check")
        )

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        second_daemon = StubDaemon(ws)
        second_daemon.hello([])
        second_daemon.recv_hello_ack()
        replay = second_daemon.recv_instr()
        assert replay["type"] == "check.run"
        assert replay["data"] == first["data"]
        report_id = second_daemon.report(
            "check.finished",
            {
                "run_id": replay["data"]["run_id"],
                "node_id": check_node,
                "status": "success",
                "exit_code": 0,
                "output_tail": "recovered",
            },
        )
        assert second_daemon.recv()["ref"] == report_id
        second_daemon.ack(replay, "noop")
        assert _poll(lambda: _node_status(env, check_node) == "success")


def test_retry_only_failed_and_generates_new_check_run(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    client, env, _hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    project = _project(env, channel)
    canvas = _canvas(env, channel)
    _, upstream = _task_node(env, channel, canvas, project, number=1)
    nodes = {
        state: _system_node(env, canvas, action="check", status=state, command="test")
        for state in ("idle", "running", "success", "failed")
    }
    for node_id in nodes.values():
        _edge(env, canvas, upstream, node_id)

    for state in ("idle", "running", "success"):
        response = client.post(f"/api/canvas-nodes/{nodes[state]}/retry")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "SYSTEM_NODE_NOT_RETRYABLE"
        assert response.json()["error"]["details"]["status"] == state

    retried = client.post(f"/api/canvas-nodes/{nodes['failed']}/retry")
    assert retried.status_code == 202
    assert retried.json()["system_status"] == "running"
    with env.engine.connect() as c:
        payload = c.execute(
            select(_DIAG.c.payload)
            .where(
                _DIAG.c.type == "agent.command",
                func.json_extract(_DIAG.c.payload, "$.node_id") == nodes["failed"],
                func.json_extract(_DIAG.c.payload, "$.action") == "check.run",
            )
            .order_by(_DIAG.c.seq.desc())
        ).scalar_one()
    assert payload["run_id"]


def test_merge_dag_order_persists_each_commit_and_finishes_once(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    client, env, _hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    project = _project(env, channel)
    canvas = _canvas(env, channel)
    first_task, first_node = _task_node(env, channel, canvas, project, number=1)
    second_task, second_node = _task_node(env, channel, canvas, project, number=2)
    merge_node = _system_node(env, canvas, action="merge")
    _edge(env, canvas, first_node, second_node)
    _edge(env, canvas, second_node, merge_node)

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        daemon = StubDaemon(ws)
        daemon.hello([])
        daemon.recv_hello_ack()

        first = daemon.recv_instr()
        assert first["type"] == "worktree.merge"
        assert first["data"]["task_id"] == first_task
        moved_computer, _ = _computer(env, key="cak_merge_moved_project")
        with env.engine.begin() as c:
            c.execute(
                update(_PROJECT)
                .where(_PROJECT.c.id == project)
                .values(computer_id=moved_computer, repo_path=r"D:\repos\moved")
            )
            c.execute(
                update(_TASK)
                .where(_TASK.c.id == second_task)
                .values(title="Changed Task 2")
            )
        daemon.report(
            "worktree.status",
            {
                "task_id": first_task,
                "status": "merged",
                "branch": first["data"]["branch"],
                "path": _worktree(env, first_task)["path"],
                "merge_commit": "merge-1",
            },
        )
        assert _poll(lambda: _worktree(env, first_task)["merge_commit"] == "merge-1")
        daemon.ack(first, "done")

        second = daemon.recv_instr()
        assert second["type"] == "worktree.merge"
        assert second["data"]["task_id"] == second_task
        assert second["data"]["repo_path"] == r"D:\repos\demo"
        assert "Task 2" in second["data"]["message"]
        assert "Changed Task 2" not in second["data"]["message"]
        daemon.report(
            "worktree.status",
            {
                "task_id": second_task,
                "status": "merged",
                "branch": second["data"]["branch"],
                "path": _worktree(env, second_task)["path"],
                "merge_commit": "merge-2",
            },
        )
        assert _poll(lambda: _node_status(env, merge_node) == "success")
        daemon.ack(second, "done")
        daemon.sync()

    assert _worktree(env, first_task)["status"] == "merged"
    assert _worktree(env, second_task)["status"] == "merged"
    assert _worktree(env, second_task)["merge_commit"] == "merge-2"
    assert any(f"node_id: {merge_node}" in body for body in _messages(env, channel))


def test_merge_report_without_commit_fails_and_retry_reissues_command(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    client, env, _hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    project = _project(env, channel)
    canvas = _canvas(env, channel)
    task_id, task_node = _task_node(env, channel, canvas, project, number=1)
    merge_node = _system_node(env, canvas, action="merge")
    _edge(env, canvas, task_node, merge_node)
    _, wrong_auth = _computer(env, key="cak_wrong_merge_reporter")

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        daemon = StubDaemon(ws)
        daemon.hello([])
        daemon.recv_hello_ack()
        first = daemon.recv_instr()
        with client.websocket_connect(DAEMON_WS, headers=wrong_auth) as wrong_ws:
            wrong = StubDaemon(wrong_ws)
            wrong.hello([])
            wrong.recv_hello_ack()
            wrong.report(
                "worktree.status",
                {
                    "task_id": task_id,
                    "status": "merged",
                    "branch": first["data"]["branch"],
                    "path": _worktree(env, task_id)["path"],
                },
            )
            wrong.sync()
        assert _node_status(env, merge_node) == "running"
        assert _worktree(env, task_id)["status"] == "active"

        daemon.report(
            "worktree.status",
            {
                "task_id": task_id,
                "status": "merged",
                "branch": first["data"]["branch"],
                "path": _worktree(env, task_id)["path"],
            },
        )
        assert _poll(lambda: _node_status(env, merge_node) == "failed")
        daemon.ack(first, "done")
        assert _worktree(env, task_id)["status"] == "active"
        assert _worktree(env, task_id)["merge_commit"] is None

        retried = client.post(f"/api/canvas-nodes/{merge_node}/retry")
        assert retried.status_code == 202
        second = daemon.recv_instr()
        assert second["type"] == "worktree.merge"
        assert second["data"]["task_id"] == task_id
        daemon.report(
            "worktree.status",
            {
                "task_id": task_id,
                "status": "merged",
                "branch": second["data"]["branch"],
                "path": _worktree(env, task_id)["path"],
                "merge_commit": "valid-merge",
            },
        )
        assert _poll(lambda: _node_status(env, merge_node) == "success")
        daemon.ack(second, "done")


def test_merge_conflict_creates_same_tree_task_then_retry_succeeds(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    client, env, _hub = ctx
    owner = env.add_agent("Coder", "offline")
    channel = env.add_channel(kind="channel", name="build")
    env.join(channel, owner)
    project = _project(env, channel, keep_days=0)
    canvas = _canvas(env, channel)
    task_id, task_node = _task_node(
        env, channel, canvas, project, number=1, owner=owner
    )
    original_tree = _worktree(env, task_id)
    merge_node = _system_node(env, canvas, action="merge")
    _edge(env, canvas, task_node, merge_node)

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        daemon = StubDaemon(ws)
        daemon.hello([])
        daemon.recv_hello_ack()
        merge = daemon.recv_instr()
        assert merge["type"] == "worktree.merge"

        daemon.report(
            "worktree.status",
            {
                "task_id": task_id,
                "status": "conflicted",
                "branch": merge["data"]["branch"],
                "path": original_tree["path"],
                "conflict_files": ["src/b.py", "src/a.py"],
            },
        )
        assert _poll(lambda: _node_status(env, merge_node) == "failed")
        daemon.ack(merge, "done")

        with env.engine.connect() as c:
            conflict_task = dict(
                c.execute(
                    select(_TASK)
                    .where(_TASK.c.title == "解决冲突", _TASK.c.id != task_id)
                )
                .mappings()
                .one()
            )
            conflict_node = c.execute(
                select(_NODE.c.id).where(_NODE.c.task_id == conflict_task["id"])
            ).scalar_one()
            edge = c.execute(
                select(_EDGE.c.id).where(
                    _EDGE.c.from_node_id == conflict_node,
                    _EDGE.c.to_node_id == merge_node,
                )
            ).scalar_one_or_none()
        assert conflict_task["owner_member_id"] == owner
        assert conflict_task["level"] == "l2"
        assert conflict_task["writes_code"] is True
        assert conflict_task["project_id"] == project
        assert edge is not None
        alias_tree = _worktree(env, conflict_task["id"])
        assert alias_tree["path"] == original_tree["path"]
        assert alias_tree["branch"] == original_tree["branch"]
        assert alias_tree["status"] == "active"
        assert _worktree(env, task_id)["status"] == "conflicted"
        with env.engine.connect() as c:
            plans = worktree_service.cleanup_plans(
                c, computer_id=env.comp_id, now="2099-01-01T00:00:00.000Z"
            )
        assert plans == []  # 原任务虽 done，活动冲突 alias 仍占同一物理树。

        anchor = next(body for body in _messages(env, channel) if "双方 Diff 引用" in body)
        assert f"node_id: {merge_node}" in anchor
        assert "冲突文件:\n- src/a.py\n- src/b.py" in anchor
        assert f"GET /api/tasks/{task_id}/diff" in anchor

        blocked_retry = client.post(f"/api/canvas-nodes/{merge_node}/retry")
        assert blocked_retry.status_code == 409
        assert blocked_retry.json()["error"]["details"]["blocked"] is True

        with env.engine.begin() as c:
            c.execute(
                update(_TASK)
                .where(_TASK.c.id == conflict_task["id"])
                .values(status="done", status_changed_at=now_iso())
            )
        with env.engine.connect() as c:
            plans = worktree_service.cleanup_plans(
                c, computer_id=env.comp_id, now="2099-01-01T00:00:00.000Z"
            )
        assert plans == []  # 冲突任务已 done 但尚未 retry，failed merge 仍须保树。
        with env.engine.connect() as c:
            rollback = c.begin()
            c.execute(
                delete(_EDGE).where(
                    (_EDGE.c.from_node_id == merge_node)
                    | (_EDGE.c.to_node_id == merge_node)
                )
            )
            c.execute(delete(_NODE).where(_NODE.c.id == merge_node))
            plans_without_merge = worktree_service.cleanup_plans(
                c, computer_id=env.comp_id, now="2099-01-01T00:00:00.000Z"
            )
            rollback.rollback()
        assert [plan.task_id for plan in plans_without_merge] == [task_id]
        retried = client.post(f"/api/canvas-nodes/{merge_node}/retry")
        assert retried.status_code == 202
        retry_merge = daemon.recv_instr()
        assert retry_merge["type"] == "worktree.merge"
        assert retry_merge["data"]["task_id"] == task_id

        daemon.report(
            "worktree.status",
            {
                "task_id": task_id,
                "status": "merged",
                "branch": retry_merge["data"]["branch"],
                "path": original_tree["path"],
                "merge_commit": "resolved-merge",
            },
        )
        assert _poll(lambda: _node_status(env, merge_node) == "success")
        daemon.ack(retry_merge, "done")
        daemon.sync()

    assert _worktree(env, task_id)["merge_commit"] == "resolved-merge"
    assert _worktree(env, conflict_task["id"])["merge_commit"] == "resolved-merge"
    assert _worktree(env, conflict_task["id"])["status"] == "merged"
    with env.engine.connect() as c:
        plans = worktree_service.cleanup_plans(
            c, computer_id=env.comp_id, now="2099-01-01T00:00:00.000Z"
        )
    assert [plan.task_id for plan in plans] == [task_id]
