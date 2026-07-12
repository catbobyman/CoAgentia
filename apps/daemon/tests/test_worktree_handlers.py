"""M6 J3 daemon 帧接缝：worktree 指令后台化（#1）——status 先于 ack、reader 不阻塞、幂等重放。"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
from coagentia_daemon.git import WorktreeOperation
from coagentia_daemon.transport import TransportClosed
from coagentia_daemon.util import new_ulid
from helpers import RecordingTransport, instr, make_client, until


class _DropFirstAckTransport(RecordingTransport):
    def __init__(self) -> None:
        super().__init__()
        self.drop_ack = True

    async def send(self, frame: dict[str, object]) -> None:
        if frame.get("kind") == "ack" and self.drop_ack:
            self.drop_ack = False
            raise TransportClosed("drop ack")
        await super().send(frame)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "处理器 中文 repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(repo)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    for key, value in (
        ("user.name", "CoAgentia Test"),
        ("user.email", "test@coagentia.local"),
        ("core.autocrlf", "false"),
    ):
        subprocess.run(
            ["git", "-C", str(repo), "config", key, value],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo), "add", "--", "seed.txt"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "seed"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return repo


def _merge_data(repo: Path, task_id: str, project_id: str) -> dict[str, object]:
    return {
        "task_id": task_id,
        "project_id": project_id,
        "repo_path": str(repo),
        "branch": f"coagentia/task-{task_id}",
        "message": f"Merge task {task_id}",
    }


async def _drain(client: object) -> None:
    await until(lambda: not client._worktree_tasks)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_ensure_and_cleanup_report_status_before_ack(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    transport = RecordingTransport()
    client, _adapter, _ = make_client(tmp_path, transport=transport)
    task_id = new_ulid()
    project_id = new_ulid()
    branch = f"coagentia/task-{task_id}"
    ensure_data = {
        "task_id": task_id,
        "project_id": project_id,
        "repo_path": str(repo),
        "branch": branch,
    }

    ensure_frame = instr("worktree.ensure", ensure_data)
    await client.handle_instr(ensure_frame)
    await _drain(client)
    first_tail = transport.sent[-2:]
    assert [frame["kind"] for frame in first_tail] == ["report", "ack"]  # status→ack 保序不变
    assert first_tail[0]["type"] == "worktree.status"
    assert first_tail[0]["data"]["status"] == "active"
    assert first_tail[1]["result"] == "done"

    await client.handle_instr(ensure_frame)
    await _drain(client)
    second_tail = transport.sent[-2:]
    assert second_tail[0]["data"]["status"] == "active"
    assert second_tail[1]["result"] == "noop"

    cleanup_frame = instr("worktree.cleanup", {"task_id": task_id})
    await client.handle_instr(cleanup_frame)
    await _drain(client)
    cleanup_tail = transport.sent[-2:]
    assert cleanup_tail[0]["type"] == "worktree.status"
    assert cleanup_tail[0]["data"] == {
        "task_id": task_id,
        "status": "cleaned",
        "branch": branch,
        "path": str(client.paths.worktree_path(project_id, task_id).resolve()),
        "merge_commit": None,
        "conflict_files": None,
    }
    assert cleanup_tail[1]["result"] == "done"

    await client.handle_instr(cleanup_frame)
    await _drain(client)
    repeated_tail = transport.sent[-2:]
    assert repeated_tail[0]["data"]["status"] == "cleaned"
    assert repeated_tail[1]["result"] == "noop"


@pytest.mark.asyncio
async def test_same_merge_frame_replays_terminal_status(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    transport = RecordingTransport()
    client, _adapter, _ = make_client(tmp_path, transport=transport)
    task_id = new_ulid()
    project_id = new_ulid()
    await client.handle_instr(
        instr(
            "worktree.ensure",
            {
                "task_id": task_id,
                "project_id": project_id,
                "repo_path": str(repo),
                "branch": f"coagentia/task-{task_id}",
            },
        )
    )
    await _drain(client)
    target = client.paths.worktree_path(project_id, task_id)
    (target / "delivery.txt").write_text("done\n", encoding="utf-8")
    for args in (("add", "--", "delivery.txt"), ("commit", "-m", "delivery")):
        subprocess.run(
            ["git", "-C", str(target), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
    merge_frame = instr("worktree.merge", _merge_data(repo, task_id, project_id))

    await client.handle_instr(merge_frame)
    await _drain(client)
    assert transport.sent[-2]["data"]["status"] == "merged"
    assert transport.sent[-1]["result"] == "done"

    await client.handle_instr(merge_frame)
    await _drain(client)
    assert transport.sent[-2]["data"]["status"] == "merged"
    assert transport.sent[-1]["result"] == "noop"


@pytest.mark.asyncio
async def test_same_frame_retries_status_when_ack_send_was_lost(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    dropped = _DropFirstAckTransport()
    client, _adapter, _ = make_client(tmp_path, transport=dropped)
    task_id = new_ulid()
    project_id = new_ulid()
    frame = instr(
        "worktree.ensure",
        {
            "task_id": task_id,
            "project_id": project_id,
            "repo_path": str(repo),
            "branch": f"coagentia/task-{task_id}",
        },
    )

    # 首轮：后台任务里 ack 被 drop（TransportClosed 被 suppress，不再上抛）→ 只报 status。
    await client.handle_instr(frame)
    await _drain(client)
    assert [sent["type"] for sent in dropped.reports()] == ["worktree.status"]
    assert dropped.acks() == []

    # 换新传输重发同帧（frame_id 已不在 _worktree_tasks）→ 后台幂等重跑 → 再报 status + ack=noop。
    retry_transport = RecordingTransport()
    client._transport = retry_transport
    await client.handle_instr(frame)
    await _drain(client)
    assert [sent["type"] for sent in retry_transport.reports()] == ["worktree.status"]
    assert retry_transport.last_ack()["result"] == "noop"


@pytest.mark.asyncio
async def test_worktree_op_does_not_block_reader(tmp_path: Path) -> None:
    """#1 核心回归：worktree op 后台化 → handle_instr 立即返回，reader 仍能处理 ping→pong。"""
    transport = RecordingTransport()
    client, _adapter, _ = make_client(tmp_path, transport=transport)
    gate = asyncio.Event()

    async def blocking_merge(_data: object) -> WorktreeOperation:
        await gate.wait()
        return WorktreeOperation(True, None)

    client.git.merge = blocking_merge  # type: ignore[assignment]
    frame = instr("worktree.merge", _merge_data(tmp_path / "x", new_ulid(), new_ulid()))
    await asyncio.wait_for(client.handle_instr(frame), timeout=1.0)  # 不等 git 子进程
    assert client._worktree_tasks  # 后台任务在飞
    await client._dispatch({"kind": "ping"})  # reader 侧仍即时响应
    assert transport.sent[-1].get("kind") == "pong"
    gate.set()
    await _drain(client)


@pytest.mark.asyncio
async def test_worktree_hard_failure_acks_failed(tmp_path: Path) -> None:
    """merge 硬失败经后台仍以 ack FAILED 回流（server 侧 fail_dispatch 路径不变）。"""
    transport = RecordingTransport()
    client, _adapter, _ = make_client(tmp_path, transport=transport)

    async def failing_merge(_data: object) -> WorktreeOperation:
        raise RuntimeError("merge boom")

    client.git.merge = failing_merge  # type: ignore[assignment]
    frame = instr("worktree.merge", _merge_data(tmp_path / "x", new_ulid(), new_ulid()))
    await client.handle_instr(frame)
    await _drain(client)
    assert transport.last_ack()["result"] == "failed"
    assert transport.last_ack()["error"]["code"] == "HANDLER_ERROR"


@pytest.mark.asyncio
async def test_shutdown_cancels_inflight_worktree(tmp_path: Path) -> None:
    """shutdown 取消在飞 worktree 任务并等回收（断连不取消，仅 shutdown 取消）。"""
    transport = RecordingTransport()
    client, _adapter, _ = make_client(tmp_path, transport=transport)
    gate = asyncio.Event()

    async def blocking_merge(_data: object) -> WorktreeOperation:
        await gate.wait()
        return WorktreeOperation(True, None)

    client.git.merge = blocking_merge  # type: ignore[assignment]
    frame = instr("worktree.merge", _merge_data(tmp_path / "x", new_ulid(), new_ulid()))
    await client.handle_instr(frame)
    assert client._worktree_tasks
    await client.shutdown()
    assert not client._worktree_tasks
