"""DEDAG 任务级 merge 域单测（契约 B v1.6 §14）。

覆盖：prepare_merge 校验链（404/422 六拒绝路径）、REST /tasks/{id}/merge 门
（202 accepted / 202 merged 幂等 / 409 同 Project 串行 / 503 daemon 离线）、
apply_merge_report 完成处置（merged 别名同步 + 系统消息 + diagnostic；conflicted
自动建任务派回 + 幂等）、fail_merge 失败留痕。写法沿 test_worktree_lifecycle
既有模式（ctx fixture + Env 直插最小库；DEDAG 后任务行不再伴生画布节点）。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from coagentia_contracts.daemon import WorktreeStatusData
from coagentia_contracts.ws import EventType
from coagentia_server.app import create_app
from coagentia_server.computers.gateway_tx import gateway_tx
from coagentia_server.db import models
from coagentia_server.events import EventBus
from coagentia_server.ledger.service import now_iso
from coagentia_server.tasks import merge as merge_domain
from coagentia_server.worktrees import service as worktree_service
from daemon_helpers import AUTH, Env, StubDaemon, nid
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

DAEMON_WS = "/api/daemon/ws"
WORKTREE_PATH = r"D:\scratch\中文工程\worktrees\project\task"

_TASK = models.tbl(models.Task)
_PROJECT = models.tbl(models.Project)
_CHANNEL = models.tbl(models.Channel)
_CHANNEL_PROJECT = models.tbl(models.ChannelProject)
_WORKTREE = models.tbl(models.Worktree)
_MESSAGE = models.tbl(models.Message)
_MENTION = models.tbl(models.MessageMention)
_DIAG = models.tbl(models.DiagnosticEvent)


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


def _project(env: Env, channel_id: str) -> str:
    project_id = nid()
    with env.engine.begin() as c:
        c.execute(
            insert(_PROJECT).values(
                id=project_id,
                workspace_id=env.ws_id,
                computer_id=env.comp_id,
                name="Demo",
                repo_path=r"D:\repos\demo",
                worktree_keep_days=7,
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
    writes_code: bool = True,
    status: str = "done",
    owner: str | None = None,
) -> str:
    """最小任务行（DEDAG：纯任务行，无画布节点）。

    顺手推高 channels.next_task_number：冲突派回路径经 create_task/allocate_number 取号，
    直插行不推计数器会撞 UNIQUE(channel_id, number)。"""
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
                status_changed_at=now_iso(),
                created_at=now_iso(),
            )
        )
        c.execute(
            update(_CHANNEL)
            .where(_CHANNEL.c.id == channel_id)
            .values(next_task_number=number + 1)
        )
    return task_id


def _worktree(
    env: Env,
    *,
    task_id: str,
    project_id: str,
    status: str = "active",
    branch: str | None = None,
    path: str = WORKTREE_PATH,
    merge_commit: str | None = None,
    merged_at: str | None = None,
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


def _dangling_project_task(env: Env, channel_id: str) -> str:
    """任务/worktree 行指向不存在的 Project：覆盖 prepare_merge 的 404「Project 不存在」。

    连接级临时关 FK 直插（pysqlite 在 DML 前才真正 BEGIN，PRAGMA 于事务外生效）；
    插完恢复 ON，防连接回池后污染后续用例。"""
    ghost_project = nid()
    root_id = env.add_message(channel_id, kind="system", body="task ghost")
    task_id = nid()
    with env.engine.connect() as c:
        c.exec_driver_sql("PRAGMA foreign_keys=OFF")
        c.execute(
            insert(_TASK).values(
                id=task_id,
                workspace_id=env.ws_id,
                channel_id=channel_id,
                number=1,
                root_message_id=root_id,
                title="Ghost",
                status="done",
                owner_member_id=None,
                level="l2",
                created_by_member_id=env.owner_id,
                project_id=ghost_project,
                writes_code=True,
                status_changed_at=now_iso(),
                created_at=now_iso(),
            )
        )
        c.execute(
            insert(_WORKTREE).values(
                id=nid(),
                workspace_id=env.ws_id,
                project_id=ghost_project,
                task_id=task_id,
                branch=f"coagentia/task-{task_id}",
                path=WORKTREE_PATH,
                status="active",
                merge_commit=None,
                created_at=now_iso(),
                merged_at=None,
            )
        )
        c.commit()
        c.exec_driver_sql("PRAGMA foreign_keys=ON")
    return task_id


def _worktree_row(env: Env, task_id: str) -> dict[str, Any] | None:
    with env.engine.connect() as c:
        row = (
            c.execute(select(_WORKTREE).where(_WORKTREE.c.task_id == task_id))
            .mappings()
            .first()
        )
    return dict(row) if row is not None else None


def _merge_diags(env: Env) -> list[dict[str, Any]]:
    """task.merge 运行留痕行（diagnostic_events type='agent.command'，按 seq 序）。"""
    with env.engine.connect() as c:
        rows = c.execute(select(_DIAG).order_by(_DIAG.c.seq)).mappings().all()
    return [dict(r) for r in rows if (r["payload"] or {}).get("action") == "task.merge"]


def _system_bodies(env: Env, channel_id: str) -> list[str]:
    with env.engine.connect() as c:
        return list(
            c.execute(
                select(_MESSAGE.c.body)
                .where(_MESSAGE.c.channel_id == channel_id, _MESSAGE.c.kind == "system")
                .order_by(_MESSAGE.c.id)
            ).scalars()
        )


def _pending_has(hub: Any, task_id: str) -> bool:
    with hub._pending_lock:
        return task_id in hub._merge_pending


# ---------------------------------------------------------------- A. prepare_merge 校验链


def test_merge_404_when_task_missing(ctx: tuple[TestClient, Env, Any]) -> None:
    client, _env, _hub = ctx
    response = client.post(f"/api/tasks/{nid()}/merge")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_merge_404_when_project_row_missing(ctx: tuple[TestClient, Env, Any]) -> None:
    """校验链末端：任务/worktree 均合法但 Project 行不存在 → 404（在 worktree 校验之后）。"""
    client, env, _hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    task_id = _dangling_project_task(env, channel)
    response = client.post(f"/api/tasks/{task_id}/merge")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_merge_422_requires_writes_code_and_bound_project(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    client, env, _hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    project = _project(env, channel)
    readonly = _task(env, channel, number=1, project_id=project, writes_code=False)
    unbound = _task(env, channel, number=2, project_id=None, writes_code=True)

    for task_id in (readonly, unbound):
        response = client.post(f"/api/tasks/{task_id}/merge")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_FAILED"


def test_merge_422_requires_mergeable_worktree(ctx: tuple[TestClient, Env, Any]) -> None:
    """缺行 / cleaned 皆不可合并（merged 幂等、active/conflicted 可合并另测）。"""
    client, env, _hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    project = _project(env, channel)
    no_tree = _task(env, channel, number=1, project_id=project)
    cleaned = _task(env, channel, number=2, project_id=project)
    _worktree(env, task_id=cleaned, project_id=project, status="cleaned")

    for task_id in (no_tree, cleaned):
        response = client.post(f"/api/tasks/{task_id}/merge")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_FAILED"


def test_merge_422_requires_done_status(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, _hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    project = _project(env, channel)
    task_id = _task(env, channel, number=1, project_id=project, status="in_progress")
    _worktree(env, task_id=task_id, project_id=project)

    response = client.post(f"/api/tasks/{task_id}/merge")
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "TASK_TRANSITION_INVALID"
    assert error["details"] == {"from": "in_progress", "to": "merge", "allowed": ["done"]}


# ---------------------------------------------------------------- B–E. REST 端点门


def test_merge_accepted_dispatches_instr_and_registers_pending(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    """202 accepted：pending 登记 + diagnostic running 留痕 + 提交后下发 worktree.merge。"""
    client, env, hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    project = _project(env, channel)
    task_id = _task(env, channel, number=1, project_id=project)
    branch = f"coagentia/task-{task_id}"
    _worktree(env, task_id=task_id, project_id=project, branch=branch)

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([])
        d.recv_hello_ack()

        response = client.post(f"/api/tasks/{task_id}/merge")
        assert response.status_code == 202
        assert response.json() == {"task_id": task_id, "status": "accepted"}
        # 铁律 4：after_commit 在响应前按序执行 → pending 此刻已登记（运行身份）。
        with hub._pending_lock:
            assert hub._merge_pending[task_id] == (env.comp_id, project)

        instr = d.recv_instr()
        assert instr["type"] == "worktree.merge"
        assert instr["data"]["task_id"] == task_id
        assert instr["data"]["project_id"] == project
        assert instr["data"]["repo_path"] == r"D:\repos\demo"
        assert instr["data"]["branch"] == branch
        assert f"#1 Task 1 (task_id={task_id})" in instr["data"]["message"]
        d.ack(instr, "done")
        d.sync()
        # ack done 只表示 daemon 受理；merged/conflicted 上报前 pending 不清。
        assert _pending_has(hub, task_id)

    diags = _merge_diags(env)
    assert [item["payload"]["status"] for item in diags] == ["running"]
    assert diags[0]["task_id"] == task_id
    assert diags[0]["channel_id"] == channel
    assert diags[0]["payload"]["branch"] == branch
    assert diags[0]["payload"]["project_id"] == project


def test_merge_conflicted_worktree_retry_is_accepted(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    """conflicted 行可重触发合并（冲突解决后的 retry 路径，M6a retry 语义的任务级对应）。"""
    client, env, hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    project = _project(env, channel)
    task_id = _task(env, channel, number=1, project_id=project)
    _worktree(env, task_id=task_id, project_id=project, status="conflicted")

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([])
        d.recv_hello_ack()
        response = client.post(f"/api/tasks/{task_id}/merge")
        assert response.status_code == 202
        assert response.json() == {"task_id": task_id, "status": "accepted"}
        assert _pending_has(hub, task_id)
        instr = d.recv_instr()
        assert instr["type"] == "worktree.merge"
        d.ack(instr, "done")
        d.sync()


def test_merge_already_merged_is_idempotent_202(ctx: tuple[TestClient, Env, Any]) -> None:
    """merged 行（merge_commit 非空）→ 202 status=merged：不登记 pending、不留痕、不下发。"""
    client, env, hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    project = _project(env, channel)
    task_id = _task(env, channel, number=1, project_id=project)
    _worktree(
        env,
        task_id=task_id,
        project_id=project,
        status="merged",
        merge_commit="mc-1",
        merged_at=now_iso(),
    )

    for _ in range(2):  # 重复触发同样幂等
        response = client.post(f"/api/tasks/{task_id}/merge")
        assert response.status_code == 202
        assert response.json() == {"task_id": task_id, "status": "merged"}
    assert not _pending_has(hub, task_id)
    assert _merge_diags(env) == []


def test_merge_same_project_serial_409(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    project = _project(env, channel)
    task_id = _task(env, channel, number=1, project_id=project)
    _worktree(env, task_id=task_id, project_id=project)
    other_task = nid()
    with hub._pending_lock:  # 同 Project 已有进行中的合并（运行身份直插）
        hub._merge_pending[other_task] = (env.comp_id, project)

    response = client.post(f"/api/tasks/{task_id}/merge")
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "DEPLOY_IN_PROGRESS"
    assert error["rule"] == "W5"
    assert not _pending_has(hub, task_id)
    assert _merge_diags(env) == []  # 409 在留痕之前，事务无残留


def test_merge_daemon_offline_503(ctx: tuple[TestClient, Env, Any]) -> None:
    client, env, hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    project = _project(env, channel)
    task_id = _task(env, channel, number=1, project_id=project)
    _worktree(env, task_id=task_id, project_id=project)

    response = client.post(f"/api/tasks/{task_id}/merge")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DAEMON_OFFLINE"
    assert not _pending_has(hub, task_id)
    assert _merge_diags(env) == []


def test_merge_dispatch_ack_failed_clears_pending_and_notes_failure(
    ctx: tuple[TestClient, Env, Any],
) -> None:
    """下发 ack=failed → fail_merge 留痕（diagnostic failed + 消息）+ 清 pending（可重触发）。"""
    client, env, hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    project = _project(env, channel)
    task_id = _task(env, channel, number=1, project_id=project)
    _worktree(env, task_id=task_id, project_id=project)

    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([])
        d.recv_hello_ack()
        assert client.post(f"/api/tasks/{task_id}/merge").status_code == 202
        instr = d.recv_instr()
        assert instr["type"] == "worktree.merge"
        d.ack(instr, "failed")
        # _fail_task_merge 在 loop 线程 gateway_tx 落库 → 轮询收敛。
        assert _poll(lambda: not _pending_has(hub, task_id))
        assert _poll(
            lambda: ["running", "failed"]
            == [item["payload"]["status"] for item in _merge_diags(env)]
        )

    bodies = _system_bodies(env, channel)
    assert any("合并失败" in body and "可修复后重新触发合并" in body for body in bodies)


# ---------------------------------------------------------------- F–H. 完成/冲突/失败处置


def test_apply_merge_report_merged_syncs_alias_message_diag(migrated_engine: Engine) -> None:
    """merged 上报（沿 hub 调用序：apply_status → apply_merge_report）：原行置 merged +
    同物理树别名行同步 merged（含 worktree.updated 广播）+ 系统消息 + diagnostic merged。"""
    env = Env(migrated_engine)
    bus = EventBus()
    channel = env.add_channel(kind="channel", name="build")
    project = _project(env, channel)
    task_id = _task(env, channel, number=1, project_id=project)
    branch = f"coagentia/task-{task_id}"
    _worktree(env, task_id=task_id, project_id=project, branch=branch)
    # 别名行：同 project/path/branch 的冲突派回逻辑行（M6a alias 语义）。
    alias_task = _task(env, channel, number=2, project_id=project, status="in_progress")
    alias_tree = _worktree(env, task_id=alias_task, project_id=project, branch=branch)

    data = WorktreeStatusData(
        task_id=task_id, status="merged", branch=branch, path=WORKTREE_PATH,
        merge_commit="mc-f",
    )
    events: list[Any] = []
    token = bus.subscribe(events.append)
    try:
        with gateway_tx(env.engine, bus) as tx:
            result = worktree_service.apply_status(
                tx.conn, computer_id=env.comp_id, data=data, trusted_running_merge=True
            )
            assert result is not None
            merge_domain.apply_merge_report(
                tx,
                data=data,
                worktree_row=result.row,
                workspace_id=result.workspace_id,
                channel_id=result.channel_id,
            )
    finally:
        bus.unsubscribe(token)

    row = _worktree_row(env, task_id)
    assert row is not None
    assert row["status"] == "merged"
    assert row["merge_commit"] == "mc-f"
    assert row["merged_at"] is not None
    alias = _worktree_row(env, alias_task)
    assert alias is not None
    assert alias["status"] == "merged"
    assert alias["merge_commit"] == "mc-f"
    # 别名同步经 tx.emit 广播 worktree.updated（提交后 flush）。
    updated = [e for e in events if e.type == EventType.WORKTREE_UPDATED]
    assert [e.data["worktree"]["id"] for e in updated] == [alias_tree]

    bodies = _system_bodies(env, channel)
    assert any("任务 #1 已合并主干" in body and "mc-f" in body for body in bodies)
    diags = _merge_diags(env)
    assert [item["payload"]["status"] for item in diags] == ["merged"]
    assert diags[0]["payload"]["merge_commit"] == "mc-f"


def test_apply_merge_report_conflicted_creates_conflict_task_idempotently(
    migrated_engine: Engine,
) -> None:
    """conflicted 上报：diagnostic + 冲突消息 + 自动建 L2 解决冲突任务派回原 owner（锚点卡片
    含去重排序的冲突文件清单）+ 同树别名 worktree 行；重复上报不重建（幂等）。"""
    env = Env(migrated_engine)
    bus = EventBus()
    owner = env.add_agent("Coder", "idle")
    channel = env.add_channel(kind="channel", name="build")
    project = _project(env, channel)
    task_id = _task(env, channel, number=1, project_id=project, owner=owner)
    branch = f"coagentia/task-{task_id}"
    _worktree(env, task_id=task_id, project_id=project, branch=branch)

    data = WorktreeStatusData(
        task_id=task_id, status="conflicted", branch=branch, path=WORKTREE_PATH,
        conflict_files=["src/b.py", "src/a.py", "src/a.py"],  # 乱序 + 重复 → 去重排序
    )

    def report_once() -> None:
        with gateway_tx(env.engine, bus) as tx:
            result = worktree_service.apply_status(
                tx.conn, computer_id=env.comp_id, data=data, trusted_running_merge=True
            )
            assert result is not None
            merge_domain.apply_merge_report(
                tx,
                data=data,
                worktree_row=result.row,
                workspace_id=result.workspace_id,
                channel_id=result.channel_id,
            )

    report_once()

    with env.engine.connect() as c:
        tasks = [
            dict(r)
            for r in c.execute(
                select(_TASK).where(_TASK.c.channel_id == channel).order_by(_TASK.c.number)
            ).mappings()
        ]
    assert len(tasks) == 2
    conflict = tasks[1]
    assert conflict["title"] == "解决冲突"
    assert conflict["level"] == "l2"
    assert conflict["writes_code"] is True
    assert conflict["status"] == "todo"
    assert conflict["project_id"] == project
    assert conflict["owner_member_id"] == owner  # 派回原 owner
    # 锚点卡片消息：merge_conflict 卡 + @owner mention + 冲突文件清单（去重排序）+ diff 引用。
    with env.engine.connect() as c:
        anchor = (
            c.execute(select(_MESSAGE).where(_MESSAGE.c.id == conflict["root_message_id"]))
            .mappings()
            .one()
        )
        mentions = list(
            c.execute(
                select(_MENTION.c.member_id).where(
                    _MENTION.c.message_id == conflict["root_message_id"]
                )
            ).scalars()
        )
    assert anchor["card_kind"] == "merge_conflict"
    assert anchor["body"].startswith("@Coder ")
    assert f"#1 Task 1 (task_id={task_id})" in anchor["body"]
    assert "冲突文件:\n- src/a.py\n- src/b.py" in anchor["body"]
    assert f"GET /api/tasks/{task_id}/diff" in anchor["body"]
    assert mentions == [owner]
    # 别名 worktree 行：同 branch/path 指向冲突任务、active、未带 merge_commit。
    alias = _worktree_row(env, conflict["id"])
    assert alias is not None
    assert alias["branch"] == branch
    assert alias["path"] == WORKTREE_PATH
    assert alias["status"] == "active"
    assert alias["merge_commit"] is None
    # 冲突播报消息 + diagnostic conflicted（文件清单去重排序）。
    assert any("任务合并冲突" in body for body in _system_bodies(env, channel))
    diags = _merge_diags(env)
    assert [item["payload"]["status"] for item in diags] == ["conflicted"]
    assert diags[0]["payload"]["conflict_files"] == ["src/a.py", "src/b.py"]

    # 幂等：重复上报 conflicted（active 别名行仍在）→ 不再建第二个冲突任务/别名行。
    report_once()
    with env.engine.connect() as c:
        task_count = len(
            c.execute(select(_TASK.c.id).where(_TASK.c.channel_id == channel)).all()
        )
        tree_count = len(c.execute(select(_WORKTREE.c.id)).all())
    assert task_count == 2
    assert tree_count == 2


def test_fail_merge_notes_diag_and_message(migrated_engine: Engine) -> None:
    env = Env(migrated_engine)
    bus = EventBus()
    channel = env.add_channel(kind="channel", name="build")
    project = _project(env, channel)
    task_id = _task(env, channel, number=1, project_id=project)
    _worktree(env, task_id=task_id, project_id=project)

    with gateway_tx(env.engine, bus) as tx:
        merge_domain.fail_merge(tx, task_id=task_id, reason="daemon 离线，合并未下发")

    diags = _merge_diags(env)
    assert [item["payload"]["status"] for item in diags] == ["failed"]
    assert diags[0]["payload"]["reason"] == "daemon 离线，合并未下发"
    bodies = _system_bodies(env, channel)
    failures = [body for body in bodies if "合并失败" in body]
    assert len(failures) == 1
    assert "任务 #1 合并失败" in failures[0]
    assert "daemon 离线，合并未下发" in failures[0]
    assert "可修复后重新触发合并" in failures[0]

    # 任务不存在 → 静默 no-op（不留痕不发消息）。
    with gateway_tx(env.engine, bus) as tx:
        merge_domain.fail_merge(tx, task_id=nid(), reason="x")
    assert len(_merge_diags(env)) == 1
    assert len([body for body in _system_bodies(env, channel) if "合并失败" in body]) == 1
