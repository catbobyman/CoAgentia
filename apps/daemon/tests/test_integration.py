"""daemon ↔ 真 server 集成（契约 D §2/§4）：真 uvicorn server + 真 websockets daemon。

覆盖：hello/hello_ack 握手打通（computers.status→connected）；崩溃重启空进程表 → server 对账 #2
自动 resume（status∈{idle,busy,starting} 的 Agent 拉起，人工 Stop=offline 的不拉起）。
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

import pytest
from coagentia_daemon.adapter import FakeAdapter
from coagentia_daemon.buffer import TelemetryBuffer
from coagentia_daemon.client import DaemonClient
from coagentia_daemon.paths import DataPaths
from conftest import IntegrationEnv
from helpers import fake_runner, until


def _mk_daemon(
    base_url: str, env: IntegrationEnv, tmp_path: Path, *, adapter: FakeAdapter | None = None
) -> DaemonClient:
    paths = DataPaths(tmp_path / "daemon-root")
    paths.ensure_dirs()
    return DaemonClient(
        server_url=base_url,
        api_key=env.api_key,
        adapter=adapter or FakeAdapter(),
        buffer=TelemetryBuffer(paths),
        paths=paths,
        os_name="Windows 11",
        arch="AMD64",
        runner=fake_runner,
    )


@pytest.mark.asyncio
async def test_handshake_connects_real_server(
    running_server: tuple[str, IntegrationEnv], tmp_path: Path
) -> None:
    base_url, env = running_server
    env.add_agent("A", "offline")  # offline → 无 resume/投递，握手干净
    client = _mk_daemon(base_url, env, tmp_path)
    task = asyncio.create_task(client.run())
    try:
        await asyncio.wait_for(client.connected.wait(), timeout=15)
        assert client.hello_ack is not None
        assert client.hello_ack.computer_id == env.comp_id
        assert client.hello_ack.workspace_id == env.ws_id
        assert client.hello_ack.heartbeat_sec == 25
        await until(lambda: env.computer_status() == "connected", timeout=10)
    finally:
        client.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_crash_restart_resumes_last_known_not_stopped(
    running_server: tuple[str, IntegrationEnv], tmp_path: Path
) -> None:
    base_url, env = running_server
    pat = env.add_agent("Pat", "idle")  # 最后已知态 idle → 应 resume
    orch = env.add_agent("Orch", "offline")  # 人工 Stop → 不拉起
    adapter = FakeAdapter()  # 空进程表（崩溃重启）
    client = _mk_daemon(base_url, env, tmp_path, adapter=adapter)
    task = asyncio.create_task(client.run())
    try:
        await asyncio.wait_for(client.connected.wait(), timeout=15)
        # server 对账 #2 → agent.start(Pat)；daemon 幂等启动。
        await until(
            lambda: pat in [a.agent_member_id for a in adapter.process_table()], timeout=10
        )
        # 人工 Stop 过的 Orch 不在拉起集合内。
        assert orch not in adapter.starts
        assert adapter.starts == [pat]
        await until(lambda: env.agent_status(pat) == "idle", timeout=10)
    finally:
        client.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
