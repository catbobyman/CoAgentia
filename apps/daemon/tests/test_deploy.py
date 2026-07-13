"""M7b K4 daemon 部署执行器：流式日志 + chunk_seq 单调 + url 提取 + 超时杀树 + 自然键幂等 +
deploy-log/deploy-finished 缓冲重传。体例同 test_checks.py / test_preview.py。"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest
from coagentia_contracts.daemon import (
    DeployFinishedData,
    DeployLogReportData,
    DeployRunData,
)
from coagentia_daemon.buffer import TelemetryBuffer
from coagentia_daemon.deploy import DeployProcessResult, DeployRunner, run_deploy_process
from coagentia_daemon.paths import DataPaths
from coagentia_daemon.util import new_ulid
from helpers import RecordingTransport, instr, make_client


def _run(repo: Path, *, command: str = "deploy") -> DeployRunData:
    return DeployRunData(
        deployment_id=new_ulid(),
        repo_path=str(repo),
        command=command,
        branch="main",
        commit_hash="abc123",
    )


# ---------------------------------------------------------------- run_deploy_process（真子进程）


@pytest.mark.asyncio
async def test_real_deploy_streams_logs_and_extracts_last_url(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    command = (
        f'"{sys.executable}" -c "print(\'building\'); '
        "print('deployed to https://old.example.com'); "
        "print('final https://final.example.com/app')\""
    )
    batches: list[list[str]] = []

    async def on_log(lines: list[str]) -> None:
        batches.append(lines)

    result = await run_deploy_process(_run(repo, command=command), on_log=on_log, timeout_sec=10)
    assert result.exit_code == 0
    assert result.url == "https://final.example.com/app"  # 取最后一个 URL
    all_lines = [line for batch in batches for line in batch]
    assert any("building" in line for line in all_lines)


@pytest.mark.asyncio
async def test_deploy_nonexistent_repo_fails_127(tmp_path: Path) -> None:
    async def on_log(lines: list[str]) -> None:
        pass

    data = DeployRunData(
        deployment_id=new_ulid(),
        repo_path=str(tmp_path / "nope"),
        command="echo x",
        branch="main",
    )
    result = await run_deploy_process(data, on_log=on_log, timeout_sec=5)
    assert result.exit_code == 127
    assert result.url is None


@pytest.mark.asyncio
async def test_failed_deploy_yields_no_url(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    command = (
        f'"{sys.executable}" -c "print(\'https://should-not-leak.example.com\'); '
        'import sys; sys.exit(3)"'
    )

    async def on_log(lines: list[str]) -> None:
        pass

    result = await run_deploy_process(_run(repo, command=command), on_log=on_log, timeout_sec=10)
    assert result.exit_code == 3
    assert result.url is None  # 仅 success 吐 url


@pytest.mark.skipif(sys.platform != "win32", reason="Windows taskkill /F /T 实机路径")
@pytest.mark.asyncio
async def test_deploy_timeout_kills_process_tree(tmp_path: Path) -> None:
    command = f'"{sys.executable}" -c "import time; time.sleep(30)"'

    async def on_log(lines: list[str]) -> None:
        pass

    started = time.monotonic()
    result = await run_deploy_process(
        _run(tmp_path, command=command), on_log=on_log, timeout_sec=0.2
    )
    assert result.exit_code is None  # 超时 = exit_code null
    assert result.url is None
    assert time.monotonic() - started < 8


# -------------------------------------------------------------- DeployRunner（自然键 + chunk_seq）


@pytest.mark.asyncio
async def test_runner_streams_monotonic_chunk_seq_and_finished(tmp_path: Path) -> None:
    async def fake(data: DeployRunData, *, on_log, timeout_sec: float) -> DeployProcessResult:
        await on_log(["line-0", "line-1"])
        await on_log(["line-2"])
        return DeployProcessResult(0, "https://ok.example.com")

    logs: list[DeployLogReportData] = []
    finished: list[DeployFinishedData] = []

    async def on_log(data: DeployLogReportData) -> None:
        logs.append(data)

    async def on_finished(data: DeployFinishedData) -> None:
        finished.append(data)

    runner = DeployRunner(runner=fake)
    data = _run(tmp_path)
    started, known = runner.start(data, on_log, on_finished)
    assert started and known is None
    for _ in range(50):  # 等后台 _execute 自然跑完（wait_closed 会取消，不能用）
        if finished:
            break
        await asyncio.sleep(0.01)
    assert [d.chunk_seq for d in logs] == [0, 1]  # per-deployment 单调递增
    assert logs[0].lines == ["line-0", "line-1"]
    assert finished[0].status == "success"
    assert finished[0].url == "https://ok.example.com"


@pytest.mark.asyncio
async def test_runner_natural_key_noop_when_running(tmp_path: Path) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def slow(data: DeployRunData, *, on_log, timeout_sec: float) -> DeployProcessResult:
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return DeployProcessResult(0, None)

    async def _noop(_: object) -> None:
        pass

    runner = DeployRunner(runner=slow)
    data = _run(tmp_path)
    started1, _ = runner.start(data, _noop, _noop)
    assert started1
    await asyncio.wait_for(entered.wait(), timeout=1)
    started2, known = runner.start(data, _noop, _noop)  # 同 deployment_id 在跑 → noop
    assert not started2 and known is None
    assert calls == 1
    release.set()
    await runner.wait_closed()


@pytest.mark.asyncio
async def test_runner_terminal_returns_known_on_replay(tmp_path: Path) -> None:
    async def fake(data: DeployRunData, *, on_log, timeout_sec: float) -> DeployProcessResult:
        return DeployProcessResult(1, None)

    async def _noop(_: object) -> None:
        pass

    runner = DeployRunner(runner=fake)
    data = _run(tmp_path)
    finished: list[DeployFinishedData] = []

    async def on_finished(d: DeployFinishedData) -> None:
        finished.append(d)

    runner.start(data, _noop, on_finished)
    for _ in range(50):  # 等 _execute 自然跑完（finished 记入 _finished）
        if finished:
            break
        await asyncio.sleep(0.01)
    # 终态后重发（同 deployment_id）→ noop + 返回已知终态供重报。
    started, known = runner.start(data, _noop, on_finished)
    assert not started
    assert known is not None and known.status == "failed"


# ---------------------------------------------------------------- handler + buffer 接线


@pytest.mark.asyncio
async def test_deploy_handler_acks_done_and_buffers_finished(tmp_path: Path) -> None:
    transport = RecordingTransport()
    client, _adapter, _ = make_client(tmp_path, transport=transport)

    async def fake(data: DeployRunData, *, on_log, timeout_sec: float) -> DeployProcessResult:
        await on_log(["deploying..."])
        return DeployProcessResult(0, "https://x.example.com")

    client.deploys = DeployRunner(runner=fake)
    data = _run(tmp_path)
    frame = instr("deploy.run", data.model_dump(mode="json"))
    await client.handle_instr(frame)
    assert transport.last_ack()["result"] == "done"  # 起后台 task 即 ack

    for _ in range(50):
        if client.buffer.has_deploy_finished():
            break
        await asyncio.sleep(0.01)
    fin = client.buffer.peek_deploy_finished(1)[0]
    assert fin.deployment_id == data.deployment_id
    assert fin.status == "success"
    assert client.buffer.has_deploy_logs()


@pytest.mark.asyncio
async def test_deploy_handler_replays_buffered_finished_without_rerun(tmp_path: Path) -> None:
    transport = RecordingTransport()
    client, _adapter, _ = make_client(tmp_path, transport=transport)
    data = _run(tmp_path)
    # 预置已终态缓冲（模拟先前跑完未 ack）：重发 deploy.run → 重报终态、不重跑。
    client.buffer.append_deploy_finished(
        DeployFinishedData(deployment_id=data.deployment_id, status="success", exit_code=0)
    )
    calls = 0

    async def fake(d: DeployRunData, *, on_log, timeout_sec: float) -> DeployProcessResult:
        nonlocal calls
        calls += 1
        return DeployProcessResult(0, None)

    client.deploys = DeployRunner(runner=fake)
    await client.handle_instr(instr("deploy.run", data.model_dump(mode="json")))
    assert transport.last_ack()["result"] == "noop"  # 已终态缓冲 → noop
    assert calls == 0  # 未重跑（副作用不可重放）


# ---------------------------------------------------------------- buffer 落盘 / 去重 / 重传


def test_deploy_log_buffer_dedup_and_persist(tmp_path: Path) -> None:
    paths = DataPaths(tmp_path / "root")
    paths.ensure_dirs()
    did = new_ulid()
    buffer = TelemetryBuffer(paths)
    buffer.append_deploy_log(DeployLogReportData(deployment_id=did, chunk_seq=0, lines=["a"]))
    buffer.append_deploy_log(DeployLogReportData(deployment_id=did, chunk_seq=1, lines=["b"]))
    buffer.append_deploy_log(DeployLogReportData(deployment_id=did, chunk_seq=0, lines=["a2"]))
    # (deployment_id, chunk_seq) 去重：chunk_seq=0 被替换，非新增。
    restarted = TelemetryBuffer(paths)
    logs = restarted.peek_deploy_logs(10)
    assert [ln.chunk_seq for ln in logs] == [0, 1]
    assert logs[0].lines == ["a2"]
    restarted.ack_deploy_log(did, 0)
    assert [ln.chunk_seq for ln in restarted.peek_deploy_logs(10)] == [1]


def test_deploy_finished_buffer_dedup_by_deployment_and_find(tmp_path: Path) -> None:
    paths = DataPaths(tmp_path / "root")
    paths.ensure_dirs()
    did = new_ulid()
    buffer = TelemetryBuffer(paths)
    buffer.append_deploy_finished(
        DeployFinishedData(deployment_id=did, status="failed", exit_code=1)
    )
    buffer.append_deploy_finished(
        DeployFinishedData(deployment_id=did, status="success", exit_code=0)
    )
    restarted = TelemetryBuffer(paths)
    assert len(restarted.peek_deploy_finished(10)) == 1  # 同 deployment_id 去重
    assert restarted.find_deploy_finished(did).status == "success"  # type: ignore[union-attr]
    restarted.ack_deploy_finished([did])
    assert restarted.find_deploy_finished(did) is None


@pytest.mark.asyncio
async def test_deploy_log_retransmits_until_ack(tmp_path: Path) -> None:
    transport = RecordingTransport()
    client, _adapter, _ = make_client(tmp_path, transport=transport, ack_timeout=0.03)
    did = new_ulid()
    client.buffer.append_deploy_log(
        DeployLogReportData(deployment_id=did, chunk_seq=0, lines=["x"])
    )
    await client._flush_deploy_logs()  # ack 超时 → 保留待重传
    assert client.buffer.has_deploy_logs()
    first = transport.reports("deploy.log")[-1]
    assert first["data"]["deployment_id"] == did

    flush = asyncio.create_task(client._flush_deploy_logs())
    await asyncio.sleep(0.01)
    second = transport.reports("deploy.log")[-1]  # 原样重发
    assert second["data"] == first["data"]
    client._resolve_report_ack(
        {"kind": "ack", "ref": second["frame_id"], "result": "done"}
    )
    await asyncio.wait_for(flush, timeout=2)
    assert not client.buffer.has_deploy_logs()


@pytest.mark.asyncio
async def test_deploy_finished_retransmits_until_ack(tmp_path: Path) -> None:
    transport = RecordingTransport()
    client, _adapter, _ = make_client(tmp_path, transport=transport, ack_timeout=0.03)
    did = new_ulid()
    client.buffer.append_deploy_finished(
        DeployFinishedData(deployment_id=did, status="success", exit_code=0)
    )
    await client._flush_deploy_finished()
    assert client.buffer.has_deploy_finished()
    flush = asyncio.create_task(client._flush_deploy_finished())
    await asyncio.sleep(0.01)
    frame = transport.reports("deploy.finished")[-1]
    client._resolve_report_ack(
        {"kind": "ack", "ref": frame["frame_id"], "result": "done"}
    )
    await asyncio.wait_for(flush, timeout=2)
    assert not client.buffer.has_deploy_finished()
