"""M7b K4 server 部署域：POST 端点五路径 / deploy.log 落盘链 / GET log 翻页 / deploy.finished
终态 CAS + 结果卡 / 对账 #10 两分支 / compute_token_summary 新账。

体例同 test_preview_domain.py（真 TestClient + StubDaemon 驱动 /api/daemon/ws 网关侧）。POST 成功
路径需真 git 仓库（server 直查主干 HEAD），用 subprocess git init + 空提交构造。
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from coagentia_contracts.ws import EventType
from coagentia_server.app import create_app
from coagentia_server.db import models
from coagentia_server.ledger.service import new_ulid, now_iso
from coagentia_server.routes.deployments import compute_token_summary
from daemon_helpers import AUTH, Env, StubDaemon, nid
from fastapi.testclient import TestClient
from sqlalchemy import insert, select
from sqlalchemy.engine import Engine

DAEMON_WS = "/api/daemon/ws"
DEPLOY_COMMAND = "echo deployed"

_PROJECT = models.tbl(models.Project)
_CHANNEL_PROJECT = models.tbl(models.ChannelProject)
_TASK = models.tbl(models.Task)
_WORKTREE = models.tbl(models.Worktree)
_DEPLOYMENT = models.tbl(models.Deployment)
_MSG = models.tbl(models.Message)
_MENTION = models.tbl(models.MessageMention)
_TUE = models.tbl(models.TokenUsageEvent)


@pytest.fixture
def ctx(migrated_engine: Engine, tmp_path: Path) -> Iterator[tuple[TestClient, Env, Any]]:
    app = create_app(engine=migrated_engine, data_root=tmp_path / "data")
    hub = app.state.daemon_hub
    hub.ack_timeout = 0.3
    hub.query_timeout = 0.3
    hub.reconcile_interval = 3600
    hub.reminder_interval = 3600
    hub.preview_recycle_interval = 3600
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


def _git_repo(tmp_path: Path) -> str:
    repo = tmp_path / f"repo-{new_ulid()}"
    repo.mkdir()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    import os

    full_env = {**os.environ, **env}
    for args in (
        ["git", "init"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "t"],
        ["git", "commit", "--allow-empty", "-m", "init"],
    ):
        subprocess.run(
            args, cwd=str(repo), check=True, capture_output=True, env=full_env, timeout=15
        )
    return str(repo)


def _project(
    env: Env,
    channel_ids: list[str],
    *,
    repo_path: str,
    deploy_command: str | None = DEPLOY_COMMAND,
) -> str:
    project_id = nid()
    with env.engine.begin() as c:
        c.execute(
            insert(_PROJECT).values(
                id=project_id,
                workspace_id=env.ws_id,
                computer_id=env.comp_id,
                name="Demo",
                repo_path=repo_path,
                dev_command="run",
                deploy_command=deploy_command,
                created_at=now_iso(),
            )
        )
        for cid in channel_ids:
            c.execute(insert(_CHANNEL_PROJECT).values(channel_id=cid, project_id=project_id))
    return project_id


def _insert_deployment(
    env: Env,
    project_id: str,
    *,
    status: str,
    triggered_by: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    log_path: str | None = None,
) -> str:
    did = nid()
    with env.engine.begin() as c:
        c.execute(
            insert(_DEPLOYMENT).values(
                id=did,
                workspace_id=env.ws_id,
                project_id=project_id,
                triggered_by_member_id=triggered_by or env.owner_id,
                branch="main",
                commit_hash="abc123",
                command=DEPLOY_COMMAND,
                status=status,
                log_path=log_path,
                started_at=started_at,
                finished_at=finished_at,
                created_at=now_iso(),
            )
        )
    return did


def _deployment_row(env: Env, did: str) -> dict[str, Any]:
    with env.engine.connect() as c:
        return dict(c.execute(select(_DEPLOYMENT).where(_DEPLOYMENT.c.id == did)).mappings().one())


def _deploy_cards(env: Env, channel_id: str) -> list[dict[str, Any]]:
    with env.engine.connect() as c:
        return [
            dict(r)
            for r in c.execute(
                select(_MSG).where(
                    _MSG.c.channel_id == channel_id, _MSG.c.card_kind == "deployment"
                )
            ).mappings()
        ]


def _report_ack(d: StubDaemon, rtype: str, data: dict[str, Any]) -> None:
    """报一条 ack 类上报（deploy.log/deploy.finished）并消费其 ack 帧（否则污染后续 recv）。"""
    fid = d.report(rtype, data)
    ack = d.recv()
    assert ack["kind"] == "ack" and ack["ref"] == fid, ack


# ---------------------------------------------------------------- POST 五路径


def test_post_creates_queued_and_dispatches_run(
    ctx: tuple[TestClient, Env, Any], tmp_path: Path
) -> None:
    client, env, _hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    repo = _git_repo(tmp_path)
    project = _project(env, [channel], repo_path=repo)
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([])
        d.recv_hello_ack()
        resp = client.post(f"/api/projects/{project}/deployments")
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "queued"
        assert "log_path" not in body  # Public 剔除内部列
        assert body["commit_hash"] and len(body["commit_hash"]) >= 7
        run = d.recv_instr()
        assert run["type"] == "deploy.run"
        assert run["data"]["deployment_id"] == body["id"]
        assert run["data"]["command"] == DEPLOY_COMMAND
        d.ack(run, "done")


def test_post_agent_triggerer_allowed_r8(
    ctx: tuple[TestClient, Env, Any], tmp_path: Path
) -> None:
    client, env, _hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    agent = env.add_agent("Coder", "offline")
    repo = _git_repo(tmp_path)
    project = _project(env, [channel], repo_path=repo)
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([])
        d.recv_hello_ack()
        resp = client.post(
            f"/api/projects/{project}/deployments",
            headers={**AUTH, "X-Acting-Member": agent},
        )
        assert resp.status_code == 201  # R8 全员含 Agent，无角色门
        assert resp.json()["triggered_by_member_id"] == agent
        d.ack(d.recv_instr(), "done")


def test_post_422_no_deploy_command(ctx: tuple[TestClient, Env, Any], tmp_path: Path) -> None:
    client, env, _hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    repo = _git_repo(tmp_path)
    project = _project(env, [channel], repo_path=repo, deploy_command=None)
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([])
        d.recv_hello_ack()
        resp = client.post(f"/api/projects/{project}/deployments")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_FAILED"


def test_post_503_daemon_offline(ctx: tuple[TestClient, Env, Any], tmp_path: Path) -> None:
    client, env, _hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    repo = _git_repo(tmp_path)
    project = _project(env, [channel], repo_path=repo)
    resp = client.post(f"/api/projects/{project}/deployments")  # 不连 daemon
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "DAEMON_OFFLINE"
    with env.engine.connect() as c:
        assert c.execute(select(_DEPLOYMENT)).first() is None  # 503 不建行


def test_post_409_when_active_deployment_exists(
    ctx: tuple[TestClient, Env, Any], tmp_path: Path
) -> None:
    client, env, _hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    repo = _git_repo(tmp_path)
    project = _project(env, [channel], repo_path=repo)
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([])
        d.recv_hello_ack()
        # 连接后建行（避对账 #10 在连接时 fail-close 掉预置 running）：已有非终态部署 → 409。
        _insert_deployment(env, project, status="running")
        resp = client.post(f"/api/projects/{project}/deployments")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "DEPLOY_IN_PROGRESS"


def test_post_idempotency_key_returns_existing(
    ctx: tuple[TestClient, Env, Any], tmp_path: Path
) -> None:
    client, env, _hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    repo = _git_repo(tmp_path)
    project = _project(env, [channel], repo_path=repo)
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([])
        d.recv_hello_ack()
        headers = {"Idempotency-Key": "dep-key-1"}
        first = client.post(f"/api/projects/{project}/deployments", headers=headers)
        assert first.status_code == 201
        d.ack(d.recv_instr(), "done")
        second = client.post(f"/api/projects/{project}/deployments", headers=headers)
        assert second.status_code == 200  # 命中键 → 返旧
        assert second.json()["id"] == first.json()["id"]
        d.sync()  # 无第二个 deploy.run 下发


# ---------------------------------------------------------------- deploy.log 落盘链 + GET log


def test_deploy_log_persists_promotes_running_and_get_log(
    ctx: tuple[TestClient, Env, Any], tmp_path: Path
) -> None:
    client, env, _hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    repo = _git_repo(tmp_path)
    project = _project(env, [channel], repo_path=repo)
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([])
        d.recv_hello_ack()
        # 连接后建 queued 行（避对账在连接时重发 deploy.run）。
        did = _insert_deployment(env, project, status="queued")
        _report_ack(d, "deploy.log", {"deployment_id": did, "chunk_seq": 0, "lines": ["l0", "l1"]})
        # 首条 log → queued→running + 落盘 + log_path 写回。
        assert _poll(lambda: _deployment_row(env, did)["status"] == "running")
        assert _deployment_row(env, did)["log_path"] is not None
        assert _deployment_row(env, did)["started_at"] is not None
        _report_ack(d, "deploy.log", {"deployment_id": did, "chunk_seq": 1, "lines": ["l2"]})
        # chunk_seq 去重：重发已收 seq=1 不追加。
        _report_ack(d, "deploy.log", {"deployment_id": did, "chunk_seq": 1, "lines": ["dup"]})
        d.sync()
    page = client.get(f"/api/deployments/{did}/log").json()
    assert page["lines"] == ["l0", "l1", "l2"]  # dup 未落
    assert page["truncated"] is False
    # 翻页：after=2 只回第 3 行之后（此处已到末尾）。
    page2 = client.get(f"/api/deployments/{did}/log?after=2").json()
    assert page2["lines"] == ["l2"]


def test_get_log_empty_when_no_log_path(
    ctx: tuple[TestClient, Env, Any], tmp_path: Path
) -> None:
    client, env, _hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    repo = _git_repo(tmp_path)
    project = _project(env, [channel], repo_path=repo)
    did = _insert_deployment(env, project, status="queued")
    page = client.get(f"/api/deployments/{did}/log").json()
    assert page == {"lines": [], "next_after": None, "truncated": False}


# ---------------------------------------------------------------- deploy.finished 终态 + 结果卡


def test_deploy_finished_terminal_and_result_cards_to_all_channels(
    ctx: tuple[TestClient, Env, Any], tmp_path: Path
) -> None:
    client, env, _hub = ctx
    ch1 = env.add_channel(kind="channel", name="build")
    ch2 = env.add_channel(kind="channel", name="ops")
    agent = env.add_agent("Coder", "offline")
    repo = _git_repo(tmp_path)
    project = _project(env, [ch1, ch2], repo_path=repo)
    events: list[Any] = []
    token = client.app.state.bus.subscribe(events.append)  # type: ignore[union-attr]
    try:
        with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
            d = StubDaemon(ws)
            d.hello([])
            d.recv_hello_ack()
            # 连接后建 running 行（避对账 fail-close）；agent offline 且不入频道，不触发下发。
            did = _insert_deployment(
                env, project, status="running", triggered_by=agent, started_at=now_iso()
            )
            _report_ack(
                d,
                "deploy.finished",
                {"deployment_id": did, "status": "success", "exit_code": 0,
                 "url": "https://demo.example.com"},
            )
            assert _poll(lambda: _deployment_row(env, did)["status"] == "success")
            assert _poll(lambda: len(_deploy_cards(env, ch1)) == 1)
            assert _poll(lambda: len(_deploy_cards(env, ch2)) == 1)
            d.sync()
    finally:
        client.app.state.bus.unsubscribe(token)  # type: ignore[union-attr]
    row = _deployment_row(env, did)
    assert row["url"] == "https://demo.example.com" and row["exit_code"] == 0
    assert row["finished_at"] is not None
    # 结果卡 mention 触发者（Agent）。
    card = _deploy_cards(env, ch1)[0]
    with env.engine.connect() as c:
        mentions = [
            r[0]
            for r in c.execute(
                select(_MENTION.c.member_id).where(_MENTION.c.message_id == card["id"])
            )
        ]
    assert mentions == [agent]
    assert card["card_ref"] == did
    updated = [e for e in events if e.type == EventType.DEPLOYMENT_UPDATED]
    assert any(u.data["deployment"]["status"] == "success" for u in updated)


def test_deploy_finished_noop_when_already_terminal(
    ctx: tuple[TestClient, Env, Any], tmp_path: Path
) -> None:
    client, env, _hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    repo = _git_repo(tmp_path)
    project = _project(env, [channel], repo_path=repo)
    did = _insert_deployment(env, project, status="success", finished_at=now_iso())
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([])
        d.recv_hello_ack()
        _report_ack(
            d,
            "deploy.finished",
            {"deployment_id": did, "status": "failed", "exit_code": 1},
        )
        d.sync()
    # CAS 未命中已终态 → 不覆盖、不发卡。
    assert _deployment_row(env, did)["status"] == "success"
    assert _deploy_cards(env, channel) == []


# ---------------------------------------------------------------- 对账 #10 两分支


def test_reconcile_10_running_fail_closed_on_restart(
    ctx: tuple[TestClient, Env, Any], tmp_path: Path
) -> None:
    client, env, _hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    agent = env.add_agent("Coder", "offline")
    repo = _git_repo(tmp_path)
    project = _project(env, [channel], repo_path=repo)
    did = _insert_deployment(
        env, project, status="running", triggered_by=agent, started_at=now_iso()
    )
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([])  # 无 boot_nonce → daemon_restarted 口径
        d.recv_hello_ack()
        # running 部署 → fail-closed（副作用不可重放，不重跑）+ @触发者结果卡。
        assert _poll(lambda: _deployment_row(env, did)["status"] == "failed")
        assert _deployment_row(env, did)["exit_code"] is None
        assert _poll(lambda: len(_deploy_cards(env, channel)) == 1)
        d.sync()  # 无 deploy.run 重发（不自动重跑）


def test_reconcile_10_queued_safe_redispatch_on_restart(
    ctx: tuple[TestClient, Env, Any], tmp_path: Path
) -> None:
    client, env, _hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    repo = _git_repo(tmp_path)
    project = _project(env, [channel], repo_path=repo)
    did = _insert_deployment(env, project, status="queued")
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([])
        d.recv_hello_ack()
        # queued（未 ack 未开跑）→ 安全重发 deploy.run。
        run = d.recv_instr()
        assert run["type"] == "deploy.run"
        assert run["data"]["deployment_id"] == did
        d.ack(run, "done")
    assert _deployment_row(env, did)["status"] == "queued"  # 未 fail-close


def test_reconcile_10_same_nonce_jitter_leaves_running(
    ctx: tuple[TestClient, Env, Any], tmp_path: Path
) -> None:
    client, env, _hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    repo = _git_repo(tmp_path)
    project = _project(env, [channel], repo_path=repo)
    nonce = nid()
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([], boot_nonce=nonce)
        d.recv_hello_ack()
    # 断连期建 running 行；同 nonce 重连（WS jitter）→ 不动。
    did = _insert_deployment(env, project, status="running", started_at=now_iso())
    with client.websocket_connect(DAEMON_WS, headers=AUTH) as ws:
        d = StubDaemon(ws)
        d.hello([], boot_nonce=nonce)
        d.recv_hello_ack()
        d.sync()  # 无 deploy.run 下发
    assert _deployment_row(env, did)["status"] == "running"  # 存活未 fail-close
    assert _deploy_cards(env, channel) == []


# ---------------------------------------------------------------- compute_token_summary 新账


def _task_with_worktree(
    env: Env, channel: str, project: str, *, number: int, merged_at: str | None
) -> str:
    root = env.add_message(channel, kind="system", body=f"t{number}")
    task_id = nid()
    with env.engine.begin() as c:
        c.execute(
            insert(_TASK).values(
                id=task_id,
                workspace_id=env.ws_id,
                channel_id=channel,
                number=number,
                root_message_id=root,
                title=f"T{number}",
                status="done",
                level="l2",
                created_by_member_id=env.owner_id,
                project_id=project,
                writes_code=True,
                status_changed_at=now_iso(),
                created_at=now_iso(),
            )
        )
        c.execute(
            insert(_WORKTREE).values(
                id=nid(),
                workspace_id=env.ws_id,
                project_id=project,
                task_id=task_id,
                branch=f"b{number}",
                path=f"/wt/{number}",
                status="merged",
                merged_at=merged_at,
                created_at=now_iso(),
            )
        )
    return task_id


def _usage(env: Env, agent: str, task_id: str, *, inp: int = 10) -> None:
    with env.engine.begin() as c:
        c.execute(
            insert(_TUE).values(
                id=nid(),
                workspace_id=env.ws_id,
                agent_member_id=agent,
                task_id=task_id,
                input_tokens=inp,
                output_tokens=5,
                cache_read_tokens=0,
                cache_write_tokens=0,
                reported_at=now_iso(),
            )
        )


def test_token_summary_first_time_all_merged(
    ctx: tuple[TestClient, Env, Any], tmp_path: Path
) -> None:
    client, env, _hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    agent = env.add_agent("Coder", "offline")
    repo = _git_repo(tmp_path)
    project = _project(env, [channel], repo_path=repo)
    t1 = _task_with_worktree(env, channel, project, number=1, merged_at=now_iso())
    t2 = _task_with_worktree(env, channel, project, number=2, merged_at=now_iso())
    _usage(env, agent, t1, inp=10)  # t2 无 usage（计入分母不计 reporting）
    until = now_iso()
    with env.engine.connect() as c:
        ts = compute_token_summary(c, project, until)
    assert ts.usage.input_tokens == 10 and ts.usage.events == 1
    assert ts.tasks_reporting.total == 2 and ts.tasks_reporting.reporting == 1
    assert set(ts.task_ids) == {t1, t2}


def test_token_summary_only_increment_since_last_success(
    ctx: tuple[TestClient, Env, Any], tmp_path: Path
) -> None:
    client, env, _hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    agent = env.add_agent("Coder", "offline")
    repo = _git_repo(tmp_path)
    project = _project(env, [channel], repo_path=repo)
    # 上一 success 部署，finished_at = 分界点。
    old_task = _task_with_worktree(env, channel, project, number=1, merged_at=now_iso())
    _usage(env, agent, old_task)
    boundary = now_iso()
    _insert_deployment(
        env, project, status="success", finished_at=boundary
    )
    # 边界后新 merged 任务。
    new_task = _task_with_worktree(env, channel, project, number=2, merged_at=now_iso())
    _usage(env, agent, new_task, inp=20)
    until = now_iso()
    with env.engine.connect() as c:
        ts = compute_token_summary(c, project, until)
    # 只算边界后：old_task 不计入。
    assert ts.tasks_reporting.total == 1
    assert ts.task_ids == [new_task]
    assert ts.usage.input_tokens == 20


def test_token_summary_failed_deploy_does_not_advance_bound(
    ctx: tuple[TestClient, Env, Any], tmp_path: Path
) -> None:
    client, env, _hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    agent = env.add_agent("Coder", "offline")
    repo = _git_repo(tmp_path)
    project = _project(env, [channel], repo_path=repo)
    t1 = _task_with_worktree(env, channel, project, number=1, merged_at=now_iso())
    _usage(env, agent, t1)
    # failed 部署不推进下界（下界只认 success）。
    _insert_deployment(env, project, status="failed", finished_at=now_iso())
    with env.engine.connect() as c:
        ts = compute_token_summary(c, project, now_iso())
    assert ts.tasks_reporting.total == 1 and ts.task_ids == [t1]


def test_token_summary_empty_set(ctx: tuple[TestClient, Env, Any], tmp_path: Path) -> None:
    client, env, _hub = ctx
    channel = env.add_channel(kind="channel", name="build")
    repo = _git_repo(tmp_path)
    project = _project(env, [channel], repo_path=repo)
    with env.engine.connect() as c:
        ts = compute_token_summary(c, project, now_iso())
    assert ts.usage.input_tokens == 0 and ts.usage.events == 0
    assert ts.tasks_reporting.total == 0 and ts.task_ids == []
