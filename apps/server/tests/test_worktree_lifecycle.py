"""M6a J3 server：激活/对账 #5/目录注入/force-start/keep_days 清理。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from coagentia_contracts.daemon import WorktreeStatusData
from coagentia_contracts.ws import EventType
from coagentia_server.app import create_app
from coagentia_server.db import models
from coagentia_server.ledger.service import now_iso
from coagentia_server.worktrees import service as worktree_service
from daemon_helpers import AUTH, Env, StubDaemon, nid
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, insert, select
from sqlalchemy.engine import Engine

DAEMON_WS = "/api/daemon/ws"
WORKTREE_PATH = r"D:\scratch\中文工程\worktrees\project\task"

_PROJECT = models.tbl(models.Project)
_CHANNEL_PROJECT = models.tbl(models.ChannelProject)
_CANVAS = models.tbl(models.Canvas)
_TASK = models.tbl(models.Task)
_NODE = models.tbl(models.CanvasNode)
_EDGE = models.tbl(models.CanvasEdge)
_WORKTREE = models.tbl(models.Worktree)
_MESSAGE = models.tbl(models.Message)


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


def _poll(fn: Callable[[], bool], timeout: float = 3.0) -> bool:
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
            insert(_CHANNEL_PROJECT).values(
                channel_id=channel_id, project_id=project_id
            )
        )
    return project_id


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
    *,
    number: int,
    owner: str | None,
    project_id: str | None,
    writes_code: bool,
    status: str = "todo",
    status_changed_at: str | None = None,
) -> tuple[str, str, str]:
    root_id = env.add_message(channel_id, kind="system", body=f"task {number}")
    task_id = nid()
    node_id = nid()
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
        c.execute(
            insert(_NODE).values(
                id=node_id,
                canvas_id=canvas_id,
                kind="agent",
                task_id=task_id,
                created_at=now_iso(),
            )
        )
    return task_id, node_id, root_id


def _edge(env: Env, canvas_id: str, from_node_id: str, to_node_id: str) -> None:
    with env.engine.begin() as c:
        c.execute(
            insert(_EDGE).values(
                id=nid(),
                canvas_id=canvas_id,
                from_node_id=from_node_id,
                to_node_id=to_node_id,
            )
        )


def _worktree(
    env: Env,
    *,
    task_id: str,
    project_id: str,
    status: str = "active",
    path: str = WORKTREE_PATH,
    merged_at: str | None = None,
    merge_commit: str | None = None,
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
                path=path,
                status=status,
                merge_commit=merge_commit,
                created_at=now_iso(),
                merged_at=merged_at,
            )
        )
    return worktree_id


def _worktree_row(env: Env, task_id: str) -> dict[str, Any] | None:
    with env.engine.connect() as c:
        row = c.execute(
            select(_WORKTREE).where(
                _WORKTREE.c.task_id == task_id
            )
        ).mappings().first()
    return dict(row) if row is not None else None


def _directory_message_count(env: Env, root_message_id: str) -> int:
    with env.engine.connect() as c:
        return c.execute(
            select(func.count())
            .select_from(_MESSAGE)
            .where(
                _MESSAGE.c.thread_root_id == root_message_id,
                _MESSAGE.c.body
                == worktree_service.directory_message(WORKTREE_PATH),
            )
        ).scalar_one()


def _report_active(d: StubDaemon, task_id: str, branch: str) -> None:
    d.report(
        "worktree.status",
        {
            "task_id": task_id,
            "status": "active",
            "branch": branch,
            "path": WORKTREE_PATH,
        },
    )


def _ack_activation(d: StubDaemon, *, node_id: str) -> dict[str, Any]:
    wake = d.recv_instr()
    assert wake["type"] == "agent.wake"
    assert wake["data"]["reason"] == "canvas_activation"
    assert wake["data"]["refs"]["node_id"] == node_id
    d.ack(wake, "done")
    deliver = d.recv_instr()
    assert deliver["type"] == "message.deliver"
    assert any(WORKTREE_PATH in item["body"] for item in deliver["data"]["messages"])
    d.ack(deliver, "done")
    return deliver


def test_reconcile_ensure_status_path_then_canvas_wake_is_idempotent(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    client, env, hub = ctx
    agent = env.add_agent("Coder", "idle")
    channel = env.add_channel(kind="channel", name="build")
    env.join(channel, agent)
    project = _project(env, channel)
    canvas = _canvas(env, channel)
    task_id, node_id, root_id = _task_node(
        env,
        channel,
        canvas,
        number=1,
        owner=agent,
        project_id=project,
        writes_code=True,
    )
    with env.engine.connect() as c:
        original_anchor_body = c.execute(
            select(_MESSAGE.c.body).where(
                _MESSAGE.c.id == root_id
            )
        ).scalar_one()

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(agent, "idle")])
        d.recv_hello_ack()
        ensure = d.recv_instr()
        assert ensure["type"] == "worktree.ensure"
        assert ensure["data"] == {
            "task_id": task_id,
            "project_id": project,
            "repo_path": r"D:\repos\demo",
            "branch": f"coagentia/task-{task_id}",
        }

        # 真顺序：daemon status(active) 先于 ack；server 必须先持久绝对 path，再发 wake。
        _report_active(d, task_id, ensure["data"]["branch"])
        assert _poll(lambda: (_worktree_row(env, task_id) or {}).get("path") == WORKTREE_PATH)
        assert _directory_message_count(env, root_id) == 1
        d.ack(ensure, "done")
        deliver = _ack_activation(d, node_id=node_id)
        assert _worktree_row(env, task_id)["status"] == "active"  # type: ignore[index]

        # 定向副本只改 body：id 不变，DB 锚点原文与 read 身份不变。
        delivered_anchor = next(
            item for item in deliver["data"]["messages"] if item["id"] == root_id
        )
        assert WORKTREE_PATH in delivered_anchor["body"]
        with env.engine.connect() as c:
            assert c.execute(
                select(_MESSAGE.c.body).where(
                    _MESSAGE.c.id == root_id
                )
            ).scalar_one() == original_anchor_body

        # 重复 status 与重复对账都不重复 durable 目录消息/ensure。
        _report_active(d, task_id, ensure["data"]["branch"])
        d.sync()
        assert _directory_message_count(env, root_id) == 1
        conn = hub._conns[env.comp_id]
        fut = asyncio.run_coroutine_threadsafe(hub.reconcile(conn), hub._loop)
        d.sync()
        fut.result(timeout=5)
        assert _directory_message_count(env, root_id) == 1


def test_task_updated_unblocks_and_immediately_ensures_once(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    client, env, _hub = ctx
    agent = env.add_agent("Coder", "idle")
    channel = env.add_channel(kind="channel", name="build")
    env.join(channel, agent)
    project = _project(env, channel)
    canvas = _canvas(env, channel)
    upstream, upstream_node, _ = _task_node(
        env,
        channel,
        canvas,
        number=1,
        owner=env.owner_id,
        project_id=None,
        writes_code=False,
        status="in_review",
    )
    downstream, downstream_node, _ = _task_node(
        env,
        channel,
        canvas,
        number=2,
        owner=agent,
        project_id=project,
        writes_code=True,
    )
    _edge(env, canvas, upstream_node, downstream_node)

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(agent, "idle")])
        d.recv_hello_ack()
        d.sync()  # 上游只读、下游 blocked：均无 ensure

        response = client.post(f"/api/tasks/{upstream}/status", json={"to": "done"})
        assert response.status_code == 200
        ensure = d.recv_instr()  # task.updated 提交后立即扫描，不等 60s
        assert ensure["type"] == "worktree.ensure"
        assert ensure["data"]["task_id"] == downstream
        _report_active(d, downstream, ensure["data"]["branch"])
        d.ack(ensure, "done")
        _ack_activation(d, node_id=downstream_node)

        # 同一 task.updated 再发一次，task 自然键+DB 重查不重发 ensure。
        client.app.state.bus.emit(  # type: ignore[union-attr]
            EventType.TASK_UPDATED,
            channel,
            {"task": response.json(), "change": None},
        )
        d.sync()
        assert _worktree_row(env, downstream) is not None


def test_force_start_blocked_writes_code_ensures_before_wake(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    client, env, _hub = ctx
    agent = env.add_agent("Coder", "idle")
    channel = env.add_channel(kind="channel", name="build")
    env.join(channel, agent)
    project = _project(env, channel)
    canvas = _canvas(env, channel)
    _, upstream_node, _ = _task_node(
        env,
        channel,
        canvas,
        number=1,
        owner=env.owner_id,
        project_id=None,
        writes_code=False,
    )
    task_id, node_id, root_id = _task_node(
        env,
        channel,
        canvas,
        number=2,
        owner=agent,
        project_id=project,
        writes_code=True,
    )
    _edge(env, canvas, upstream_node, node_id)

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(agent, "idle")])
        d.recv_hello_ack()
        d.sync()

        response = client.post(f"/api/tasks/{task_id}/force-start")
        assert response.status_code == 200
        ensure = d.recv_instr()
        assert ensure["type"] == "worktree.ensure"  # 任何 wake 前必须先 ensure
        _report_active(d, task_id, ensure["data"]["branch"])
        assert _poll(lambda: _directory_message_count(env, root_id) == 1)
        d.ack(ensure, "done")
        _ack_activation(d, node_id=node_id)


def test_cleanup_terminal_and_daemon_noop_converges_cleaned(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    client, env, hub = ctx
    agent = env.add_agent("Coder", "idle")
    channel = env.add_channel(kind="channel", name="build")
    env.join(channel, agent)
    project = _project(env, channel, keep_days=7)
    canvas = _canvas(env, channel)
    task_id, _, _ = _task_node(
        env,
        channel,
        canvas,
        number=1,
        owner=agent,
        project_id=project,
        writes_code=True,
        status="done",
        status_changed_at="2020-01-01T00:00:00.000Z",
    )
    _worktree(
        env,
        task_id=task_id,
        project_id=project,
        status="merged",
        merged_at="2020-01-02T00:00:00.000Z",
        merge_commit="merge-abc",
    )

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(agent, "idle")])
        d.recv_hello_ack()
        cleanup = d.recv_instr()
        assert cleanup["type"] == "worktree.cleanup"
        assert cleanup["data"]["task_id"] == task_id
        # 极端恢复：daemon 已无目录/登记，只能 noop 且不发 status；server 仍须收敛 cleaned。
        d.ack(cleanup, "noop")
        assert _poll(lambda: (_worktree_row(env, task_id) or {}).get("status") == "cleaned")
        assert _worktree_row(env, task_id)["cleaned_at"] is not None  # type: ignore[index]
        assert _worktree_row(env, task_id)["merge_commit"] == "merge-abc"  # type: ignore[index]

        conn = hub._conns[env.comp_id]
        fut = asyncio.run_coroutine_threadsafe(hub.reconcile(conn), hub._loop)
        d.sync()
        fut.result(timeout=5)  # cleaned 不再下发 cleanup


def test_cleanup_plans_cover_terminal_and_merged_anchors(migrated_engine: Engine) -> None:
    env = Env(migrated_engine)
    channel = env.add_channel(kind="channel", name="build")
    project = _project(env, channel, keep_days=7)
    canvas = _canvas(env, channel)
    terminal, _, _ = _task_node(
        env,
        channel,
        canvas,
        number=1,
        owner=None,
        project_id=project,
        writes_code=True,
        status="closed",
        status_changed_at="2020-01-01T00:00:00.000Z",
    )
    merged, _, _ = _task_node(
        env,
        channel,
        canvas,
        number=2,
        owner=None,
        project_id=project,
        writes_code=True,
        status="todo",  # 异常未终态也按 merged_at 保留期清理
    )
    cleaned, _, _ = _task_node(
        env,
        channel,
        canvas,
        number=3,
        owner=None,
        project_id=project,
        writes_code=True,
        status="done",
        status_changed_at="2020-01-01T00:00:00.000Z",
    )
    _worktree(env, task_id=terminal, project_id=project, status="active")
    _worktree(
        env,
        task_id=merged,
        project_id=project,
        status="merged",
        merged_at="2020-01-02T00:00:00.000Z",
        merge_commit="abc123",
    )
    _worktree(env, task_id=cleaned, project_id=project, status="cleaned")
    with env.engine.connect() as c:
        plans = worktree_service.cleanup_plans(
            c, computer_id=env.comp_id, now="2026-01-01T00:00:00.000Z"
        )
    assert [item.task_id for item in plans] == [terminal, merged]


def test_status_upsert_persists_basic_transition_fields(migrated_engine: Engine) -> None:
    env = Env(migrated_engine)
    channel = env.add_channel(kind="channel", name="build")
    project = _project(env, channel)
    canvas = _canvas(env, channel)
    task_id, _, _ = _task_node(
        env,
        channel,
        canvas,
        number=1,
        owner=None,
        project_id=project,
        writes_code=True,
    )
    branch = f"coagentia/task-{task_id}"
    with env.engine.begin() as c:
        active = worktree_service.apply_status(
            c,
            computer_id=env.comp_id,
            data=WorktreeStatusData(
                task_id=task_id,
                status="active",
                branch=branch,
                path=WORKTREE_PATH,
            ),
        )
        assert active is not None and active.became_active
    with env.engine.begin() as c:
        duplicate = worktree_service.apply_status(
            c,
            computer_id=env.comp_id,
            data=WorktreeStatusData(
                task_id=task_id,
                status="active",
                branch=branch,
                path=WORKTREE_PATH,
            ),
        )
        assert duplicate is not None and not duplicate.became_active
        conflicted = worktree_service.apply_status(
            c,
            computer_id=env.comp_id,
            data=WorktreeStatusData(
                task_id=task_id,
                status="conflicted",
                branch=branch,
                path=WORKTREE_PATH,
                conflict_files=["src/a.py"],
            ),
        )
        assert conflicted is not None and conflicted.row["status"] == "conflicted"
        assert "conflict_files" not in conflicted.row  # 瞬态上报不落列
        merged = worktree_service.apply_status(
            c,
            computer_id=env.comp_id,
            data=WorktreeStatusData(
                task_id=task_id,
                status="merged",
                branch=branch,
                path=WORKTREE_PATH,
                merge_commit="merge-def",
            ),
        )
        assert merged is not None
        assert merged.row["merge_commit"] == "merge-def"
        assert merged.row["merged_at"] is not None
        cleaned = worktree_service.apply_status(
            c,
            computer_id=env.comp_id,
            data=WorktreeStatusData(
                task_id=task_id,
                status="cleaned",
                branch=branch,
                path=WORKTREE_PATH,
            ),
        )
        assert cleaned is not None
        assert cleaned.row["merge_commit"] == "merge-def"
        assert cleaned.row["cleaned_at"] is not None


def test_existing_task_still_ensures_after_project_unbind(migrated_engine: Engine) -> None:
    env = Env(migrated_engine)
    channel = env.add_channel(kind="channel", name="build")
    project = _project(env, channel)
    canvas = _canvas(env, channel)
    task_id, _, _ = _task_node(
        env,
        channel,
        canvas,
        number=1,
        owner=None,
        project_id=project,
        writes_code=True,
    )
    with env.engine.begin() as c:
        c.execute(
            delete(_CHANNEL_PROJECT).where(
                _CHANNEL_PROJECT.c.channel_id == channel,
                _CHANNEL_PROJECT.c.project_id == project,
            )
        )
    with env.engine.connect() as c:
        plans = worktree_service.ensure_plans(c, task_id=task_id)
    assert [item.task_id for item in plans] == [task_id]


def test_directory_context_excludes_terminal_and_reblocked_tasks(
    migrated_engine: Engine,
) -> None:
    env = Env(migrated_engine)
    agent = env.add_agent("Coder", "idle")
    channel = env.add_channel(kind="channel", name="build")
    project = _project(env, channel)
    canvas = _canvas(env, channel)
    _, upstream_node, _ = _task_node(
        env,
        channel,
        canvas,
        number=1,
        owner=None,
        project_id=None,
        writes_code=False,
    )
    blocked, blocked_node, _ = _task_node(
        env,
        channel,
        canvas,
        number=2,
        owner=agent,
        project_id=project,
        writes_code=True,
    )
    terminal, _, _ = _task_node(
        env,
        channel,
        canvas,
        number=3,
        owner=agent,
        project_id=project,
        writes_code=True,
        status="done",
    )
    _edge(env, canvas, upstream_node, blocked_node)
    _worktree(env, task_id=blocked, project_id=project)
    _worktree(env, task_id=terminal, project_id=project, path=WORKTREE_PATH + "-terminal")
    with env.engine.connect() as c:
        contexts = worktree_service.directory_contexts(
            c, agent_member_id=agent, channel_id=channel
        )
    assert contexts == []


def test_briefing_delivery_injects_copy_without_mutating_db(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    client, env, _hub = ctx
    agent = env.add_agent("Coder", "idle")
    channel = env.add_channel(kind="channel", name="build")
    env.join(channel, agent)
    project = _project(env, channel)
    canvas = _canvas(env, channel)
    task_id, node_id, root_id = _task_node(
        env,
        channel,
        canvas,
        number=1,
        owner=agent,
        project_id=project,
        writes_code=True,
    )
    _worktree(env, task_id=task_id, project_id=project)
    env.set_read(agent, channel, root_id)
    briefing_id = env.add_message(
        channel,
        author=None,
        kind="system",
        body="工程简报：开始实现。",
        mentions=(agent,),
    )

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(agent, "idle")])
        d.recv_hello_ack()
        deliver = _ack_activation(d, node_id=node_id)
        briefing = next(
            item for item in deliver["data"]["messages"] if item["id"] == briefing_id
        )
        assert WORKTREE_PATH in briefing["body"]
        with env.engine.connect() as c:
            assert c.execute(
                select(_MESSAGE.c.body).where(
                    _MESSAGE.c.id == briefing_id
                )
            ).scalar_one() == "工程简报：开始实现。"


def test_ensure_failed_holds_wake_and_delivery_fail_closed(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    client, env, _hub = ctx
    agent = env.add_agent("Coder", "idle")
    channel = env.add_channel(kind="channel", name="build")
    env.join(channel, agent)
    project = _project(env, channel)
    canvas = _canvas(env, channel)
    task_id, _, _ = _task_node(
        env,
        channel,
        canvas,
        number=1,
        owner=agent,
        project_id=project,
        writes_code=True,
    )
    env.add_message(
        channel,
        author=None,
        kind="system",
        body="工程简报：开始实现。",
        mentions=(agent,),
    )

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(agent, "idle")])
        d.recv_hello_ack()
        ensure = d.recv_instr()
        assert ensure["type"] == "worktree.ensure"
        assert ensure["data"]["task_id"] == task_id
        d.ack(ensure, "failed")  # 无 worktree.status / 无绝对 path
        d.sync()  # 不得回退成普通 system mention→reminder wake
        assert _worktree_row(env, task_id) is None
        assert env.read_position(agent, channel) is None
        assert env.diag_count() == 1  # 复用既有 agent.command 诊断类型


def test_node_create_persists_delivery_fields_and_requires_bound_project(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    client, env, _hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    env.join(channel, env.owner_id)
    project = _project(env, channel)
    canvas = _canvas(env, channel)

    response = client.post(
        f"/api/canvases/{canvas}/nodes",
        json={
            "title": "实现功能",
            "kind": "agent",
            "writes_code": True,
            "project_id": project,
        },
    )
    assert response.status_code == 201
    task_id = response.json()["node"]["task_id"]
    with env.engine.connect() as c:
        row = c.execute(
            select(_TASK).where(_TASK.c.id == task_id)
        ).mappings().one()
    assert row["writes_code"] is True
    assert row["project_id"] == project

    invalid = client.post(
        f"/api/canvases/{canvas}/nodes",
        json={"title": "缺 Project", "kind": "agent", "writes_code": True},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["rule"] == "W2"

    system_invalid = client.post(
        f"/api/canvases/{canvas}/nodes",
        json={
            "title": "merge",
            "kind": "system",
            "system_action": "merge",
            "writes_code": True,
            "project_id": project,
        },
    )
    assert system_invalid.status_code == 422
    assert system_invalid.json()["error"]["rule"] == "W2"
