"""M6a J5 daemon check.run：主工作区执行、UTF-8 尾、自然键与落盘重传。"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path

import pytest
from coagentia_contracts.daemon import CheckFinishedData, CheckRunData
from coagentia_daemon.buffer import TelemetryBuffer
from coagentia_daemon.checks import CheckProcessResult, CheckRunner, run_check_process
from coagentia_daemon.paths import DataPaths
from coagentia_daemon.util import new_ulid
from helpers import RecordingTransport, instr, make_client


def _run(repo: Path, *, command: str = "test") -> CheckRunData:
    return CheckRunData(
        run_id=new_ulid(),
        node_id=new_ulid(),
        project_id=new_ulid(),
        repo_path=str(repo),
        command=command,
    )


@pytest.mark.asyncio
async def test_real_check_runs_in_repo_and_caps_utf8_tail(tmp_path: Path) -> None:
    repo = tmp_path / "中文 repo"
    repo.mkdir()
    command = (
        f'"{sys.executable}" -c "from pathlib import Path; '
        "print(Path.cwd().name); print('x'*5000); print('中文尾')\""
    )
    result = await run_check_process(_run(repo, command=command), timeout_sec=10)
    assert result.exit_code == 0
    assert "中文尾" in result.output_tail
    assert len(result.output_tail.encode("utf-8")) <= 4096
    assert "中文 repo" not in result.output_tail  # 早期输出已被有界尾截掉。


@pytest.mark.skipif(sys.platform != "win32", reason="Windows taskkill /F /T 实机路径")
@pytest.mark.asyncio
async def test_timeout_kills_windows_process_tree(tmp_path: Path) -> None:
    command = f'"{sys.executable}" -c "import time; time.sleep(30)"'
    started = time.monotonic()
    result = await run_check_process(_run(tmp_path, command=command), timeout_sec=0.1)
    assert result.exit_code == 124
    assert "check timeout" in result.output_tail
    assert time.monotonic() - started < 8


@pytest.mark.skipif(sys.platform != "win32", reason="Windows taskkill /F /T 实机路径")
@pytest.mark.asyncio
async def test_cancellation_kills_windows_child_process(tmp_path: Path) -> None:
    command = (
        f'"{sys.executable}" -c "import os,time; '
        "open('child.pid','w').write(str(os.getpid())); time.sleep(30)\""
    )
    check = asyncio.create_task(
        run_check_process(_run(tmp_path, command=command), timeout_sec=60)
    )
    pid_path = tmp_path / "child.pid"
    deadline = time.monotonic() + 5
    while not pid_path.exists() and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    assert pid_path.exists(), "子进程未写出 pid"
    child_pid = int(pid_path.read_text(encoding="utf-8"))

    check.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(check, timeout=8)
    probe = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"if (Get-Process -Id {child_pid} -ErrorAction SilentlyContinue) {{ exit 0 }} "
            "else { exit 1 }",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        check=False,
    )
    assert probe.returncode == 1, f"child pid {child_pid} 仍存活"


@pytest.mark.asyncio
async def test_check_runner_success_failure_and_natural_key(tmp_path: Path) -> None:
    calls: list[str] = []

    async def fake(data: CheckRunData, timeout: float) -> CheckProcessResult:
        calls.append(data.run_id)
        return CheckProcessResult(0 if data.command == "ok" else 7, f"tail:{timeout:g}")

    runner = CheckRunner(runner=fake, timeout_sec=12)
    ok = _run(tmp_path, command="ok")
    changed, first = await runner.run(ok)
    assert changed and first.status == "success" and first.exit_code == 0
    changed, replay = await runner.run(ok)
    assert not changed and replay == first
    failed = _run(tmp_path, command="bad")
    changed, failure = await runner.run(failed)
    assert changed and failure.status == "failed" and failure.exit_code == 7
    assert calls == [ok.run_id, failed.run_id]


@pytest.mark.asyncio
async def test_check_handler_buffers_once_and_replays_without_execution(tmp_path: Path) -> None:
    transport = RecordingTransport()
    client, _adapter, _ = make_client(tmp_path, transport=transport)
    calls = 0

    async def fake(data: CheckRunData, timeout: float) -> CheckProcessResult:
        nonlocal calls
        calls += 1
        return CheckProcessResult(0, "all green")

    client.checks = CheckRunner(runner=fake)
    run = _run(tmp_path, command="ok")
    frame = instr("check.run", run.model_dump(mode="json"))
    await client.handle_instr(frame)
    assert transport.last_ack()["result"] == "done"
    for _ in range(20):
        if client.buffer.has_checks():
            break
        await asyncio.sleep(0)
    assert client.buffer.peek_checks(1)[0].run_id == run.run_id

    await client.handle_instr(frame)
    assert transport.last_ack()["result"] == "noop"
    assert calls == 1
    assert len(client.buffer.peek_checks(10)) == 1

    client.buffer.ack_checks([run.run_id])
    await asyncio.sleep(0)  # 让执行 Task 的 done callback 清掉 running 记忆。
    await client.handle_instr(frame)
    assert transport.last_ack()["result"] == "noop"
    assert calls == 1
    assert client.buffer.find_check(run.run_id) is not None


@pytest.mark.asyncio
async def test_long_check_acks_immediately_and_duplicate_is_running_noop(tmp_path: Path) -> None:
    transport = RecordingTransport()
    client, _adapter, _ = make_client(tmp_path, transport=transport)
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def slow(data: CheckRunData, timeout: float) -> CheckProcessResult:
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return CheckProcessResult(0, "done")

    client.checks = CheckRunner(runner=slow)
    run = _run(tmp_path, command="slow")
    frame = instr("check.run", run.model_dump(mode="json"))
    await client.handle_instr(frame)
    assert transport.last_ack()["result"] == "done"
    await asyncio.wait_for(entered.wait(), timeout=1)
    assert not client.buffer.has_checks()

    await client.handle_instr(frame)
    assert transport.last_ack()["result"] == "noop"
    assert calls == 1
    release.set()
    for _ in range(20):
        if client.buffer.has_checks():
            break
        await asyncio.sleep(0)
    assert client.buffer.peek_checks(1)[0].output_tail == "done"


def test_check_finished_buffer_persists_and_acks_by_run_id(tmp_path: Path) -> None:
    paths = DataPaths(tmp_path / "root")
    paths.ensure_dirs()
    first = CheckFinishedData(
        run_id=new_ulid(),
        node_id=new_ulid(),
        status="success",
        exit_code=0,
        output_tail="ok",
    )
    second = CheckFinishedData(
        run_id=new_ulid(),
        node_id=new_ulid(),
        status="failed",
        exit_code=1,
        output_tail="bad",
    )
    buffer = TelemetryBuffer(paths)
    buffer.append_check(first)
    buffer.append_check(second)
    buffer.append_check(first)
    restarted = TelemetryBuffer(paths)
    assert [item.run_id for item in restarted.peek_checks(10)] == [
        first.run_id,
        second.run_id,
    ]
    restarted.ack_checks([first.run_id])
    assert restarted.find_check(first.run_id) is None
    assert restarted.find_check(second.run_id) == second


@pytest.mark.asyncio
async def test_check_finished_retransmits_same_run_until_ack(tmp_path: Path) -> None:
    transport = RecordingTransport()
    client, _adapter, _ = make_client(tmp_path, transport=transport, ack_timeout=0.03)
    finished = CheckFinishedData(
        run_id=new_ulid(),
        node_id=new_ulid(),
        status="success",
        exit_code=0,
        output_tail="ok",
    )
    client.buffer.append_check(finished)

    await client._flush_checks()
    assert client.buffer.find_check(finished.run_id) == finished
    first = transport.reports("check.finished")[-1]
    assert first["data"]["run_id"] == finished.run_id

    flush = asyncio.create_task(client._flush_checks())
    await asyncio.sleep(0.01)
    second = transport.reports("check.finished")[-1]
    assert second["data"] == first["data"]
    client._resolve_report_ack(
        {"kind": "ack", "ref": second["frame_id"], "result": "done"}
    )
    await asyncio.wait_for(flush, timeout=2)
    assert client.buffer.find_check(finished.run_id) is None
