"""M6 J4 Diff REST 验收：树关系、daemon 代理、超时与 TaskDetail 派生。"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from coagentia_contracts.daemon import DiffPayload
from coagentia_server.app import create_app
from coagentia_server.db import models
from coagentia_server.ledger.service import now_iso
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


def _delivery_rows(
    env: Env,
    *,
    with_worktree: bool = True,
    mismatched_project: bool = False,
) -> tuple[str, str, str, str]:
    channel_id = env.add_channel(name="delivery")
    message_id = env.add_message(channel_id, author=env.owner_id, body="deliver")
    task_id = nid()
    project_id = nid()
    tree_project_id = nid() if mismatched_project else project_id
    repo_path = r"D:\项目\repo"
    with env.engine.begin() as conn:
        for candidate in {project_id, tree_project_id}:
            conn.execute(
                insert(models.Project.__table__).values(
                    id=candidate,
                    workspace_id=env.ws_id,
                    computer_id=env.comp_id,
                    name=f"P-{candidate[-4:]}",
                    repo_path=repo_path,
                    created_at=now_iso(),
                )
            )
        conn.execute(
            insert(models.Task.__table__).values(
                id=task_id,
                workspace_id=env.ws_id,
                channel_id=channel_id,
                number=1,
                root_message_id=message_id,
                title="Diff task",
                status="in_review",
                level="l2",
                created_by_member_id=env.owner_id,
                project_id=project_id,
                writes_code=True,
                status_changed_at=now_iso(),
                created_at=now_iso(),
            )
        )
        if with_worktree:
            conn.execute(
                insert(models.Worktree.__table__).values(
                    id=nid(),
                    workspace_id=env.ws_id,
                    project_id=tree_project_id,
                    task_id=task_id,
                    branch=f"coagentia/task-{task_id}",
                    path=rf"D:\data\worktrees\{tree_project_id}\{task_id}",
                    status="active",
                    created_at=now_iso(),
                )
            )
    return task_id, project_id, channel_id, repo_path


def test_diff_without_matching_worktree_is_404_and_detail_has_null(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    client, env, _hub = ctx
    task_id, _project_id, _channel_id, _repo_path = _delivery_rows(
        env, with_worktree=False
    )

    response = client.get(f"/api/tasks/{task_id}/diff")
    detail = client.get(f"/api/tasks/{task_id}")

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert detail.status_code == 200
    assert detail.json()["worktree"] is None


def test_diff_rejects_worktree_from_another_project(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    client, env, _hub = ctx
    task_id, _project_id, _channel_id, _repo_path = _delivery_rows(
        env, mismatched_project=True
    )

    response = client.get(f"/api/tasks/{task_id}/diff")

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_diff_proxies_query_and_task_detail_derives_worktree(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    client, env, _hub = ctx
    task_id, project_id, _channel_id, repo_path = _delivery_rows(env)
    expected = DiffPayload(
        base_ref="release",
        head_ref=f"coagentia/task-{task_id}",
        files=[],
        total_additions=0,
        total_deletions=0,
        files_truncated=False,
    ).model_dump(mode="json")

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        daemon = StubDaemon(ws)
        daemon.hello([])
        daemon.recv_hello_ack()
        thread, box = _bg(
            lambda: client.get(f"/api/tasks/{task_id}/diff", params={"base": "release"})
        )
        query = daemon.recv()
        assert query["kind"] == "query" and query["type"] == "git.diff"
        assert query["data"] == {
            "project_id": project_id,
            "repo_path": repo_path,
            "task_id": task_id,
            "base": "release",
        }
        daemon.reply(query, expected)
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert "error" not in box
    response = box["response"]
    assert response.status_code == 200
    assert response.json() == expected
    detail = client.get(f"/api/tasks/{task_id}")
    assert detail.status_code == 200
    assert detail.json()["worktree"] == {
        "id": detail.json()["worktree"]["id"],
        "workspace_id": env.ws_id,
        "project_id": project_id,
        "task_id": task_id,
        "branch": f"coagentia/task-{task_id}",
        "path": rf"D:\data\worktrees\{project_id}\{task_id}",
        "status": "active",
        "merge_commit": None,
        "created_at": detail.json()["worktree"]["created_at"],
        "merged_at": None,
        "cleaned_at": None,
    }


def test_diff_query_timeout_is_daemon_offline(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    client, env, _hub = ctx
    task_id, _project_id, _channel_id, _repo_path = _delivery_rows(env)

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        daemon = StubDaemon(ws)
        daemon.hello([])
        daemon.recv_hello_ack()
        response = client.get(f"/api/tasks/{task_id}/diff")
        query = daemon.recv()

    assert query["kind"] == "query" and query["type"] == "git.diff"
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DAEMON_OFFLINE"
    assert "timeout" in response.json()["error"]["message"].lower()


def test_diff_without_daemon_connection_is_503(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    client, env, _hub = ctx
    task_id, _project_id, _channel_id, _repo_path = _delivery_rows(env)

    response = client.get(f"/api/tasks/{task_id}/diff")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DAEMON_OFFLINE"


def test_diff_query_failure_uses_existing_daemon_offline_family(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    client, env, _hub = ctx
    task_id, _project_id, _channel_id, _repo_path = _delivery_rows(env)

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        daemon = StubDaemon(ws)
        daemon.hello([])
        daemon.recv_hello_ack()
        thread, box = _bg(lambda: client.get(f"/api/tasks/{task_id}/diff"))
        query = daemon.recv()
        daemon.reply(query, {"error": "Diff ref 不存在"})
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert "error" not in box
    response = box["response"]
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DAEMON_OFFLINE"
    assert response.json()["error"]["message"] == "git.diff 查询失败: Diff ref 不存在"
