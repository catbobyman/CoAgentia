"""M6a J3 server：激活/对账 #5/目录注入/force-start/keep_days 清理。

DEDAG 批（2026-07-18）：画布/节点/边/blocked gating 退役——worktree 派生改纯任务驱动
（writes_code + project 绑定 + 非终态即派生）。wake 帧 reason=canvas_activation 帧名系契约 D
冻结沿用，refs.node_id 恒 None。原「trusted merge 挺过节点移除」用例随节点语义退役改写为
computer 归属门（merge 信任路径的完整行为已由 test_task_merge.py 覆盖）；原「上游 done 解锁」
「force-start 绕过 blocked」「reblocked 排除」等 gating 前提的用例按新语义改写。
"""

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
from daemon_helpers import AUTH, Env, StubDaemon, drain_revalidation, nid
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


def _task(
    env: Env,
    channel_id: str,
    *,
    number: int,
    owner: str | None,
    project_id: str | None,
    writes_code: bool,
    status: str = "todo",
    status_changed_at: str | None = None,
) -> tuple[str, str]:
    """DEDAG 精简版任务构造器：只插任务行（新语义下节点/边行无影响，不再建 canvas/node）。"""
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
    return task_id, root_id


def _worktree(
    env: Env,
    *,
    task_id: str,
    project_id: str,
    status: str = "active",
    path: str = WORKTREE_PATH,
    branch: str | None = None,
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
                branch=branch or f"coagentia/task-{task_id}",
                path=path,
                status=status,
                merge_commit=merge_commit,
                created_at=now_iso(),
                merged_at=merged_at,
            )
        )
    return worktree_id


def _aliased_worktrees(env: Env) -> tuple[str, str, str, str, str]:
    channel = env.add_channel(kind="channel", name="aliased cleanup")
    project = _project(env, channel)
    canvas = _canvas(env, channel)
    original_task, _, _ = _task_node(
        env,
        channel,
        canvas,
        number=1,
        owner=None,
        project_id=project,
        writes_code=True,
    )
    alias_task, _, _ = _task_node(
        env,
        channel,
        canvas,
        number=2,
        owner=None,
        project_id=project,
        writes_code=True,
    )
    branch = f"coagentia/task-{original_task}"
    original_tree = _worktree(
        env, task_id=original_task, project_id=project, branch=branch
    )
    alias_tree = _worktree(env, task_id=alias_task, project_id=project, branch=branch)
    return channel, original_task, original_tree, alias_tree, branch


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


def _ack_activation(d: StubDaemon) -> dict[str, Any]:
    wake = d.recv_instr()
    assert wake["type"] == "agent.wake"
    assert wake["data"]["reason"] == "canvas_activation"
    # DEDAG：canvas_activation 帧名系契约 D 冻结沿用，refs 不再携节点（node_id 恒 None）。
    assert wake["data"]["refs"]["node_id"] is None
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
    task_id, _, root_id = _task_node(
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
        deliver = _ack_activation(d)
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


def test_task_event_triggers_immediate_ensure_once(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    """DEDAG 改写（原「上游 done 解锁下游」随画布 gating 退役）：保留原用例的两点价值——
    task 事件触发**即时**扫描（不等 60s 周期对账）+ task 自然键/DB 重查幂等（同事件重发不重复
    ensure）。hello 时无 writes_code 任务，连接期间新建任务并 emit task.created 即触发派生。"""
    client, env, _hub = ctx
    agent = env.add_agent("Coder", "idle")
    channel = env.add_channel(kind="channel", name="build")
    env.join(channel, agent)
    project = _project(env, channel)

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(agent, "idle")])
        d.recv_hello_ack()
        d.sync()  # hello 时无 writes_code 任务：无 ensure

        task_id, root_id = _task(
            env, channel, number=1, owner=agent, project_id=project, writes_code=True
        )
        task_event = {"task": {"id": task_id, "writes_code": True}, "change": None}
        client.app.state.bus.emit(EventType.TASK_CREATED, channel, task_event)  # type: ignore[union-attr]
        ensure = d.recv_instr()  # task 事件提交后立即扫描，不等 60s
        assert ensure["type"] == "worktree.ensure"
        assert ensure["data"]["task_id"] == task_id
        _report_active(d, task_id, ensure["data"]["branch"])
        d.ack(ensure, "done")
        _ack_activation(d)

        # 同一 task 事件再发一次，task 自然键+DB 重查不重发 ensure（sync 直回 pong 即证无帧）。
        client.app.state.bus.emit(EventType.TASK_UPDATED, channel, task_event)  # type: ignore[union-attr]
        d.sync()
        assert _worktree_row(env, task_id) is not None
        assert _directory_message_count(env, root_id) == 1


def test_force_start_wakes_after_directory_persisted(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    """DEDAG 改写（原 blocked 前提随画布 gating 退役）：writes_code 任务 hello 即收 ensure；
    force-start 收敛为人工催动直投（不查投递门）——wake 前目录必须已持久（顺序断言保留）。
    Agent 报 starting（不可投递）以隔离 message.created 触发的常规唤醒面，帧序确定 =
    ensure → (status/目录持久) → force-start wake → deliver。"""
    client, env, _hub = ctx
    agent = env.add_agent("Coder", "starting")
    channel = env.add_channel(kind="channel", name="build")
    env.join(channel, agent)
    project = _project(env, channel)
    task_id, root_id = _task(
        env, channel, number=1, owner=agent, project_id=project, writes_code=True
    )

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([(agent, "starting")])
        d.recv_hello_ack()
        ensure = d.recv_instr()
        assert ensure["type"] == "worktree.ensure"  # 无 blocked 门：hello 对账即派生
        assert ensure["data"]["task_id"] == task_id
        _report_active(d, task_id, ensure["data"]["branch"])
        d.ack(ensure, "done")
        # wake 前目录已持久：绝对 path 与 durable 目录消息先于任何唤醒落库。
        assert _poll(lambda: (_worktree_row(env, task_id) or {}).get("path") == WORKTREE_PATH)
        assert _poll(lambda: _directory_message_count(env, root_id) == 1)
        d.sync()  # starting 不可投递：目录消息不触发常规唤醒（无帧即 pong 直回）

        response = client.post(f"/api/tasks/{task_id}/force-start")
        assert response.status_code == 200
        wake = d.recv_instr()  # 催动直投：ensure 复核（path 已持久）通过后才发 wake
        assert wake["type"] == "agent.wake"
        assert wake["data"]["reason"] == "canvas_activation"
        assert wake["data"]["refs"]["node_id"] is None  # DEDAG：不携节点
        d.ack(wake, "done")
        deliver = d.recv_instr()
        assert deliver["type"] == "message.deliver"
        bodies = [item["body"] for item in deliver["data"]["messages"]]
        assert any(WORKTREE_PATH in body for body in bodies)  # 投递副本注入目录
        assert any("强制启动" in body for body in bodies)  # 催动锚点系统消息在批内
        d.ack(deliver, "done")
        d.sync()


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


def test_cleaned_report_emits_original_and_changed_alias_once(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    client, env, hub = ctx
    channel, task_id, original_tree, alias_tree, branch = _aliased_worktrees(env)
    events: list[Any] = []
    token = client.app.state.bus.subscribe(events.append)  # type: ignore[union-attr]
    try:
        with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
            daemon = StubDaemon(ws)
            daemon.hello([])
            daemon.recv_hello_ack()
            # 握手复验 original+alias 两个 active 行（#3），先消费再走原帧序。
            drain_revalidation(daemon, count=2)
            cleaned = {
                "task_id": task_id,
                "status": "cleaned",
                "branch": branch,
                "path": WORKTREE_PATH,
            }
            daemon.report("worktree.status", cleaned)
            daemon.sync()
            daemon.report("worktree.status", cleaned)
            daemon.sync()
            # L4a：worktree.status 是非 ack 上报（异步 writer 落库）——sync() 只保接收，须用
            # drain_reports 屏障等 writer 消费完再断言 emit（否则读到未处理的中间态）。
            asyncio.run_coroutine_threadsafe(
                hub.drain_reports(env.comp_id), hub._loop
            ).result(timeout=5)
    finally:
        client.app.state.bus.unsubscribe(token)  # type: ignore[union-attr]

    updates = [
        event
        for event in events
        if event.type == EventType.WORKTREE_UPDATED and event.channel_id == channel
    ]
    assert [event.data["worktree"]["id"] for event in updates] == [
        original_tree,
        alias_tree,
        original_tree,
    ]


def test_cleanup_convergence_emits_changed_alias_and_is_repeat_noop(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    client, env, hub = ctx
    channel, task_id, original_tree, alias_tree, _branch = _aliased_worktrees(env)
    events: list[Any] = []
    token = client.app.state.bus.subscribe(events.append)  # type: ignore[union-attr]
    try:
        hub._converge_worktree_cleaned(task_id, env.comp_id)
        hub._converge_worktree_cleaned(task_id, env.comp_id)
    finally:
        client.app.state.bus.unsubscribe(token)  # type: ignore[union-attr]

    updates = [
        event
        for event in events
        if event.type == EventType.WORKTREE_UPDATED and event.channel_id == channel
    ]
    assert [event.data["worktree"]["id"] for event in updates] == [
        original_tree,
        alias_tree,
    ]


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


def test_apply_status_trusted_merge_bypasses_computer_gate(migrated_engine: Engine) -> None:
    """DEDAG 改写（原「trusted merge 挺过节点移除」的节点前提随画布退役；merge 信任路径的完整
    行为——merged 落行/alias/冲突派回——已由 test_task_merge.py 覆盖）：apply_status 非信任路径
    以 Project.computer_id 归属为门（越界上报 → None 不污染事实源）；trusted_running_merge=
    任务级 merge 运行身份放行同一上报（merge 步上报不因归属门被丢弃）。"""
    env = Env(migrated_engine)
    channel = env.add_channel(kind="channel", name="build")
    project = _project(env, channel)
    task_id, _ = _task(
        env, channel, number=1, owner=None, project_id=project, writes_code=True
    )
    branch = f"coagentia/task-{task_id}"
    other_rig = "01K5CMPT00000000000000000B"
    with env.engine.begin() as c:
        # 归属机上报 active：正常落行。
        active = worktree_service.apply_status(
            c, computer_id=env.comp_id,
            data=WorktreeStatusData(
                task_id=task_id, status="active", branch=branch, path=WORKTREE_PATH),
        )
        assert active is not None and active.became_active
        # 非信任路径：非归属 computer 的越界上报被拒（None，不污染事实源）。
        rejected = worktree_service.apply_status(
            c, computer_id=other_rig,
            data=WorktreeStatusData(
                task_id=task_id, status="merged", branch=branch,
                path=WORKTREE_PATH, merge_commit="merge-x",
            ),
        )
        assert rejected is None
        # 越界上报确未污染行：状态仍为 active（同事务内读，未提交态可见）。
        assert c.execute(
            select(_WORKTREE.c.status).where(_WORKTREE.c.task_id == task_id)
        ).scalar_one() == "active"
        # 信任路径（任务级 merge 运行身份）：同一上报放行并落 merged。
        merged = worktree_service.apply_status(
            c, computer_id=other_rig,
            data=WorktreeStatusData(
                task_id=task_id, status="merged", branch=branch,
                path=WORKTREE_PATH, merge_commit="merge-x",
            ),
            trusted_running_merge=True,
        )
        assert merged is not None, "信任路径不得被 computer 归属门丢弃"
        assert merged.row["merge_commit"] == "merge-x"
        assert merged.row["merged_at"] is not None


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


def test_ensure_plans_covers_reopened_task_with_cleaned_worktree(migrated_engine: Engine) -> None:
    """F3 回归（M6 review）：closed→todo reopen 的 writes_code 任务，其 worktree 行已 cleaned
    时须重新进 ensure 派生（cleaned 视同无树）；active/merged 行仍不派生（语义不变）。"""
    env = Env(migrated_engine)
    channel = env.add_channel(kind="channel", name="build")
    project = _project(env, channel)
    canvas = _canvas(env, channel)
    task_id, _, _ = _task_node(
        env, channel, canvas, number=1, owner=None, project_id=project, writes_code=True,
    )
    _worktree(env, task_id=task_id, project_id=project, status="cleaned")
    with env.engine.connect() as c:
        plans = worktree_service.ensure_plans(c, task_id=task_id)
    assert [item.task_id for item in plans] == [task_id], "cleaned 行任务须重新派生"
    # active 行不重复派生（原语义）。
    with env.engine.begin() as c:
        c.execute(
            models.tbl(models.Worktree).update()
            .where(models.tbl(models.Worktree).c.task_id == task_id)
            .values(status="active")
        )
    with env.engine.connect() as c:
        assert worktree_service.ensure_plans(c, task_id=task_id) == []


def test_directory_context_excludes_terminal_tasks(
    migrated_engine: Engine,
) -> None:
    """DEDAG 改写（原「reblocked 排除」随画布 gating 退役）：directory_contexts 只排除终态任务
    （done/closed），存活任务的 active 树正常给出（终态排除断言保留，blocked 部分删除）。"""
    env = Env(migrated_engine)
    agent = env.add_agent("Coder", "idle")
    channel = env.add_channel(kind="channel", name="build")
    project = _project(env, channel)
    live, _ = _task(
        env, channel, number=1, owner=agent, project_id=project, writes_code=True
    )
    done, _ = _task(
        env, channel, number=2, owner=agent, project_id=project, writes_code=True,
        status="done",
    )
    closed, _ = _task(
        env, channel, number=3, owner=agent, project_id=project, writes_code=True,
        status="closed",
    )
    _worktree(env, task_id=live, project_id=project)
    _worktree(env, task_id=done, project_id=project, path=WORKTREE_PATH + "-done")
    _worktree(env, task_id=closed, project_id=project, path=WORKTREE_PATH + "-closed")
    with env.engine.connect() as c:
        contexts = worktree_service.directory_contexts(
            c, agent_member_id=agent, channel_id=channel
        )
    assert [item.task_id for item in contexts] == [live]
    assert contexts[0].path == WORKTREE_PATH


def test_briefing_delivery_injects_copy_without_mutating_db(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    client, env, _hub = ctx
    agent = env.add_agent("Coder", "idle")
    channel = env.add_channel(kind="channel", name="build")
    env.join(channel, agent)
    project = _project(env, channel)
    canvas = _canvas(env, channel)
    task_id, _, root_id = _task_node(
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
        drain_revalidation(d)  # 握手复验既有 active 行（#3），先消费再走原帧序
        deliver = _ack_activation(d)
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
    events: list[Any] = []
    token = client.app.state.bus.subscribe(events.append)  # type: ignore[union-attr]
    try:
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
            assert _poll(lambda: env.diag_count() == 1)  # 复用既有 agent.command 诊断类型
    finally:
        client.app.state.bus.unsubscribe(token)  # type: ignore[union-attr]

    # #2：诊断行归属 owner/channel（人类可循 task/channel 定位），并广播 DIAGNOSTIC_APPENDED。
    _DIAG = models.tbl(models.DiagnosticEvent)
    with env.engine.connect() as c:
        diag = c.execute(select(_DIAG)).mappings().one()
    assert diag["agent_member_id"] == agent
    assert diag["channel_id"] == channel
    assert diag["task_id"] == task_id
    assert diag["payload"]["instruction"] == "worktree.ensure"
    assert diag["payload"]["result"] == "failed"
    appended = [e for e in events if e.type == EventType.DIAGNOSTIC_APPENDED]
    assert len(appended) == 1
    assert appended[0].data["agent_member_id"] == agent
    assert appended[0].data["events"][0]["task_id"] == task_id


def test_ensure_failures_escalate_once_at_third_failure(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    """#2：worktree ensure 失败达阈值(3) → 一次性升级喊人（频道系统消息 + fail_closed activity）；
    第 4 次失败不再重复升级（严格 == 阈值）。"""
    client, env, _hub = ctx
    agent = env.add_agent("Coder", "idle")
    channel = env.add_channel(kind="channel", name="build")
    env.join(channel, agent)
    env.join(channel, env.owner_id)  # 升级对象=频道人类成员
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
    _ACTIVITY = models.tbl(models.ActivityItem)

    def _escalations() -> int:
        with env.engine.connect() as c:
            return c.execute(
                select(func.count())
                .select_from(_MESSAGE)
                .where(
                    _MESSAGE.c.channel_id == channel,
                    _MESSAGE.c.body.like("%工作区创建失败%"),
                )
            ).scalar_one()

    def _fail_once(*, expect_rescan: bool = False) -> None:
        with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
            d = StubDaemon(ws)
            d.hello([(agent, "idle")])
            d.recv_hello_ack()
            ensure = d.recv_instr()
            assert ensure["type"] == "worktree.ensure"
            d.ack(ensure, "failed")
            if expect_rescan:
                # 升级消息本身经 MESSAGE_CREATED → _deliver_message → 低延迟扫描再发一次
                # ensure；消费并同样 ack failed（诊断 +1，且严格 ==3 不会二次升级）。
                rescan = d.recv_instr()
                assert rescan["type"] == "worktree.ensure"
                d.ack(rescan, "failed")
            d.sync()

    # 第 3 次失败触发升级（严格 == 阈值）；升级消息触发的再扫描失败(第 4 次)与后续失败不再升级。
    for expected_diags, expected_escalations, rescan in (
        (1, 0, False),
        (2, 0, False),
        (4, 1, True),
        (5, 1, False),
    ):
        _fail_once(expect_rescan=rescan)
        assert _poll(lambda n=expected_diags: env.diag_count() == n)
        assert _escalations() == expected_escalations

    with env.engine.connect() as c:
        activities = list(
            c.execute(
                select(_ACTIVITY)
                .where(_ACTIVITY.c.kind == "fail_closed", _ACTIVITY.c.task_id == task_id)
            ).mappings()
        )
    assert [a["member_id"] for a in activities] == [env.owner_id]  # 只喊人类、只喊一次


def test_ensure_failures_without_owner_do_not_escalate(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    """#2 负例：无 owner 的任务 ensure 失败 3 次只积诊断，不升级喊人。"""
    client, env, _hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    env.join(channel, env.owner_id)
    project = _project(env, channel)
    canvas = _canvas(env, channel)
    _task_node(
        env,
        channel,
        canvas,
        number=1,
        owner=None,
        project_id=project,
        writes_code=True,
    )

    for expected in (1, 2, 3):
        with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
            d = StubDaemon(ws)
            d.hello([])
            d.recv_hello_ack()
            ensure = d.recv_instr()
            assert ensure["type"] == "worktree.ensure"
            d.ack(ensure, "failed")
            d.sync()
        assert _poll(lambda n=expected: env.diag_count() == n)

    with env.engine.connect() as c:
        escalations = c.execute(
            select(func.count())
            .select_from(_MESSAGE)
            .where(
                _MESSAGE.c.channel_id == channel,
                _MESSAGE.c.body.like("%工作区创建失败%"),
            )
        ).scalar_one()
    assert escalations == 0


def test_cleanup_failures_do_not_escalate(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    """#2 负例：cleanup 失败不升级（升级门仅 worktree.ensure）。

    DEDAG：done+active 树是待 merge 输入不清理——种子改 closed（终态弃单）触发 cleanup。"""
    client, env, _hub = ctx
    agent = env.add_agent("Coder", "idle")
    channel = env.add_channel(kind="channel", name="build")
    env.join(channel, agent)
    env.join(channel, env.owner_id)
    project = _project(env, channel, keep_days=0)
    canvas = _canvas(env, channel)
    task_id, _, _ = _task_node(
        env,
        channel,
        canvas,
        number=1,
        owner=agent,
        project_id=project,
        writes_code=True,
        status="closed",
        status_changed_at="2020-01-01T00:00:00.000Z",
    )
    _worktree(env, task_id=task_id, project_id=project)

    for expected in (1, 2, 3):
        with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
            d = StubDaemon(ws)
            d.hello([(agent, "idle")])
            d.recv_hello_ack()
            cleanup = d.recv_instr()
            assert cleanup["type"] == "worktree.cleanup"
            d.ack(cleanup, "failed")
            d.sync()
        assert _poll(lambda n=expected: env.diag_count() == n)

    with env.engine.connect() as c:
        escalations = c.execute(
            select(func.count())
            .select_from(_MESSAGE)
            .where(
                _MESSAGE.c.channel_id == channel,
                _MESSAGE.c.body.like("%工作区创建失败%"),
            )
        ).scalar_one()
    assert escalations == 0


def test_reconnect_revalidates_active_row_but_periodic_reconcile_does_not(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    """#3：reconnect 握手对既有 active 行重下发 ensure（daemon 幂等，树没了则重建）；
    周期 reconcile（revalidate_worktrees 默认 False）不重下发，避免噪声。"""
    client, env, hub = ctx
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
    _worktree(env, task_id=task_id, project_id=project)

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([])
        d.recv_hello_ack()
        [ensure] = drain_revalidation(d)
        assert ensure["data"] == {
            "task_id": task_id,
            "project_id": project,
            "repo_path": r"D:\repos\demo",
            "branch": f"coagentia/task-{task_id}",
        }
        # 周期对账（不带复验）：既有行不重下发——sync 直接回 pong 即证无 instr 帧。
        hub._run_sync(hub.reconcile(hub._conns[env.comp_id]))
        d.sync()


def test_conflicted_row_is_not_revalidated_on_reconnect(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    """#3 回归：conflicted 行绝不复验——re-ensure 会让 daemon 报 active，把冲突态覆盖回 active。"""
    client, env, hub = ctx
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
    _worktree(env, task_id=task_id, project_id=project, status="conflicted")

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([])
        d.recv_hello_ack()
        # 显式驱动带复验的 reconcile（阻塞至完成）：conflicted 行若被误复验，ensure 帧
        # 会先于 pong 到达，下面的 sync 在 recv_pong 处断言失败。
        hub._run_sync(hub.reconcile(hub._conns[env.comp_id], revalidate_worktrees=True))
        d.sync()


def test_revalidation_plans_only_cover_active_rows_of_live_tasks(
    migrated_engine: Engine,
) -> None:
    """#3 单测：revalidation_plans 仅含 active 行 × 未终态任务；conflicted/merged/终态任务排除。"""
    env = Env(migrated_engine)
    channel = env.add_channel(kind="channel", name="build")
    project = _project(env, channel)
    canvas = _canvas(env, channel)

    def seed(number: int, *, task_status: str, tree_status: str) -> str:
        task_id, _, _ = _task_node(
            env,
            channel,
            canvas,
            number=number,
            owner=None,
            project_id=project,
            writes_code=True,
            status=task_status,
        )
        _worktree(env, task_id=task_id, project_id=project, status=tree_status)
        return task_id

    live = seed(1, task_status="todo", tree_status="active")
    seed(2, task_status="todo", tree_status="conflicted")
    seed(3, task_status="done", tree_status="active")
    seed(4, task_status="todo", tree_status="merged")

    with env.engine.connect() as c:
        plans = worktree_service.revalidation_plans(c, computer_id=env.comp_id)
        assert [p.task_id for p in plans] == [live]
        assert plans[0].repo_path == r"D:\repos\demo"
        assert plans[0].branch == f"coagentia/task-{live}"
        assert plans[0].computer_id == env.comp_id
        # task_id 过滤器（hub._ensure_worktree 复验路径用）
        assert [p.task_id for p in worktree_service.revalidation_plans(c, task_id=live)] == [live]
        assert worktree_service.revalidation_plans(c, task_id=live[:-2] + "ZZ") == []
        # 其它 computer 无计划
        other_rig = "01K5CMPT00000000000000000B"
        assert worktree_service.revalidation_plans(c, computer_id=other_rig) == []


def test_as_task_persists_delivery_fields_and_requires_bound_project(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    """DEDAG（B v1.6）：as_task 扩展字段是交付字段唯一入口——writes_code/project_id 落任务行；
    writes_code 缺 project / project 未绑本频道 → 422（原画布建节点面已退役）。"""
    client, env, _hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    env.join(channel, env.owner_id)
    project = _project(env, channel)

    response = client.post(
        f"/api/channels/{channel}/messages",
        json={
            "body": "实现功能",
            "as_task": {"title": "实现功能", "writes_code": True, "project_id": project},
        },
    )
    assert response.status_code == 201
    task_id = response.json()["task"]["id"]
    with env.engine.connect() as c:
        row = c.execute(
            select(_TASK).where(_TASK.c.id == task_id)
        ).mappings().one()
    assert row["writes_code"] is True
    assert row["project_id"] == project

    invalid = client.post(
        f"/api/channels/{channel}/messages",
        json={"body": "缺 Project", "as_task": {"writes_code": True}},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "VALIDATION_FAILED"

    other_channel = env.add_channel(kind="channel", name="other")
    env.join(other_channel, env.owner_id)
    unbound_project = _project(env, other_channel)
    unbound = client.post(
        f"/api/channels/{channel}/messages",
        json={
            "body": "错绑 Project",
            "as_task": {"writes_code": True, "project_id": unbound_project},
        },
    )
    assert unbound.status_code == 422
    assert unbound.json()["error"]["code"] == "VALIDATION_FAILED"
