"""M6 J3 daemon 帧接缝：status 先于 ack，重复指令回现状。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from coagentia_daemon.transport import TransportClosed
from coagentia_daemon.util import new_ulid
from helpers import RecordingTransport, instr, make_client


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
    first_tail = transport.sent[-2:]
    assert [frame["kind"] for frame in first_tail] == ["report", "ack"]
    assert first_tail[0]["type"] == "worktree.status"
    assert first_tail[0]["data"]["status"] == "active"
    assert first_tail[1]["result"] == "done"

    await client.handle_instr(ensure_frame)
    second_tail = transport.sent[-2:]
    assert second_tail[0]["data"]["status"] == "active"
    assert second_tail[1]["result"] == "noop"

    cleanup_frame = instr("worktree.cleanup", {"task_id": task_id})
    await client.handle_instr(cleanup_frame)
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
    branch = f"coagentia/task-{task_id}"
    await client.handle_instr(
        instr(
            "worktree.ensure",
            {
                "task_id": task_id,
                "project_id": project_id,
                "repo_path": str(repo),
                "branch": branch,
            },
        )
    )
    target = client.paths.worktree_path(project_id, task_id)
    (target / "delivery.txt").write_text("done\n", encoding="utf-8")
    for args in (
        ("add", "--", "delivery.txt"),
        ("commit", "-m", "delivery"),
    ):
        subprocess.run(
            ["git", "-C", str(target), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
    merge_frame = instr(
        "worktree.merge",
        {
            "task_id": task_id,
            "project_id": project_id,
            "repo_path": str(repo),
            "branch": branch,
            "message": f"Merge task {task_id}",
        },
    )

    await client.handle_instr(merge_frame)
    assert transport.sent[-2]["data"]["status"] == "merged"
    assert transport.sent[-1]["result"] == "done"

    await client.handle_instr(merge_frame)
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

    with pytest.raises(TransportClosed):
        await client.handle_instr(frame)
    assert [sent["type"] for sent in dropped.reports()] == ["worktree.status"]

    retry_transport = RecordingTransport()
    client._transport = retry_transport
    await client.handle_instr(frame)

    assert [sent["type"] for sent in retry_transport.reports()] == ["worktree.status"]
    assert retry_transport.last_ack()["result"] == "noop"
