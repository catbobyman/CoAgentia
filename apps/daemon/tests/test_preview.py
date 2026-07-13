"""M7 K2 daemon 预览长驻进程域（PreviewRunner）：真子进程实机测试。

dev_command 用零依赖命令 `<py> -m http.server %PORT%/$PORT --bind 127.0.0.1`；每测 finally 经
`runner.wait_closed()` 逐个 taskkill 活跃子进程收尾（无孤儿）。win32 专属探针（netstat 反查孙 PID /
taskkill 杀树 / 存活监控）以 skipif 门控，跨平台不变量（端口注入/幂等/失败日志尾/端口注册表）通吃。
"""

from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest
from coagentia_contracts.daemon import PreviewStartData, PreviewStatusData
from coagentia_daemon.preview import PreviewRunner, _PortRegistry
from coagentia_daemon.util import new_ulid
from helpers import RecordingTransport, instr, make_client, until

PY = sys.executable


def _dev_command() -> str:
    """健康的零依赖长驻命令；PORT 引用由平台 shell 展开（win32 %PORT% / posix $PORT）。"""
    port_ref = "%PORT%" if sys.platform == "win32" else "$PORT"
    return f'"{PY}" -m http.server {port_ref} --bind 127.0.0.1'


def _start_data(worktree: Path, command: str | None = None) -> PreviewStartData:
    return PreviewStartData(
        preview_session_id=new_ulid(),
        task_id=new_ulid(),
        worktree_path=str(worktree),
        dev_command=command or _dev_command(),
    )


class Reports:
    """收集 report_cb 上报的 preview.status 帧。"""

    def __init__(self) -> None:
        self.items: list[PreviewStatusData] = []

    async def cb(self, data: PreviewStatusData) -> None:
        self.items.append(data)

    def by_status(self, status: str) -> list[PreviewStatusData]:
        return [d for d in self.items if d.status == status]


def _http_status(port: int) -> int:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as r:
        return int(r.status)


def _tcp_reachable(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _find_listening_pid(port: int) -> int | None:
    """netstat -ano 反查监听 127.0.0.1:<port> 的孙进程 PID（win32）。"""
    r = subprocess.run(
        ["netstat", "-ano", "-p", "TCP"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    for line in (r.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] == "TCP" and parts[3] == "LISTENING":
            if parts[1].endswith(f":{port}"):
                try:
                    return int(parts[4])
                except ValueError:
                    return None
    return None


def _pid_alive(pid: int) -> bool:
    r = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return str(pid) in (r.stdout or "")


def _taskkill(pid: int) -> None:
    subprocess.run(
        ["taskkill", "/F", "/T", "/PID", str(pid)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


# --------------------------------------------------------------------------- 跨平台


@pytest.mark.asyncio
async def test_start_injects_port_and_reports_running_http_200(tmp_path: Path) -> None:
    runner = PreviewRunner(health_timeout=15.0, poll_interval=0.1)
    reports = Reports()
    data = _start_data(tmp_path)
    try:
        started, status = await runner.start(data, reports.cb)
        assert started and status is None  # 起进程即 ack DONE，健康检查异步上报
        await until(lambda: bool(reports.by_status("running")), timeout=15)
        running = reports.by_status("running")[-1]
        assert running.port is not None
        assert running.preview_session_id == data.preview_session_id
        # 健康检查可达 + HTTP 200 = PORT 确注入且 dev server 绑到分配端口
        assert await asyncio.to_thread(_http_status, running.port) == 200
    finally:
        await runner.wait_closed()


@pytest.mark.asyncio
async def test_start_idempotent_reports_current_port(tmp_path: Path) -> None:
    runner = PreviewRunner(health_timeout=15.0, poll_interval=0.1)
    reports = Reports()
    data = _start_data(tmp_path)
    try:
        await runner.start(data, reports.cb)
        await until(lambda: bool(reports.by_status("running")), timeout=15)
        port = reports.by_status("running")[-1].port
        # 同 preview_session_id 二次 start → noop + 补报现状端口，不重开进程
        started2, status2 = await runner.start(data, reports.cb)
        assert not started2
        assert status2 is not None
        assert status2.status == "running" and status2.port == port
    finally:
        await runner.wait_closed()


@pytest.mark.asyncio
async def test_stop_unknown_and_repeated_is_noop(tmp_path: Path) -> None:
    runner = PreviewRunner(health_timeout=15.0, poll_interval=0.1)
    reports = Reports()
    try:
        # 未知 session → noop
        stopped, status = await runner.stop(new_ulid())
        assert not stopped and status is None
        # 起 → 停（recycled）→ 再停（noop）
        data = _start_data(tmp_path)
        await runner.start(data, reports.cb)
        await until(lambda: bool(reports.by_status("running")), timeout=15)
        stopped1, st1 = await runner.stop(data.preview_session_id)
        assert stopped1 and st1 is not None and st1.status == "recycled"
        stopped2, st2 = await runner.stop(data.preview_session_id)
        assert not stopped2 and st2 is None
    finally:
        await runner.wait_closed()


@pytest.mark.asyncio
async def test_bad_command_reports_failed_with_log_tail(tmp_path: Path) -> None:
    runner = PreviewRunner(health_timeout=30.0, poll_interval=0.1)  # 长超时证明"不空等"
    reports = Reports()
    bad = f'"{PY}" -m coagentia_no_such_module_xyz'
    data = _start_data(tmp_path, command=bad)
    try:
        started, _ = await runner.start(data, reports.cb)
        assert started
        # 进程先退出（存活监控竞速胜出）→ 立即 failed，不等 30s 健康超时
        await until(lambda: bool(reports.by_status("failed")), timeout=8)
        failed = reports.by_status("failed")[-1]
        assert failed.log_tail is not None and "No module named" in failed.log_tail
        assert len(failed.log_tail.encode("utf-8")) <= 2 * 1024
    finally:
        await runner.wait_closed()


@pytest.mark.asyncio
async def test_health_timeout_reports_failed(tmp_path: Path) -> None:
    runner = PreviewRunner(health_timeout=1.0, poll_interval=0.1)
    reports = Reports()
    # 进程存活但从不绑定端口 → 健康检查超时 → 杀树 + failed
    cmd = f'"{PY}" -c "import time; time.sleep(30)"'
    data = _start_data(tmp_path, command=cmd)
    try:
        started, _ = await runner.start(data, reports.cb)
        assert started
        await until(lambda: bool(reports.by_status("failed")), timeout=8)
        assert reports.by_status("failed")
    finally:
        await runner.wait_closed()


@pytest.mark.asyncio
async def test_invalid_worktree_reports_failed_immediately(tmp_path: Path) -> None:
    runner = PreviewRunner()
    reports = Reports()
    data = _start_data(tmp_path / "does_not_exist")
    # 起进程即失败（cwd 非目录 → OSError）→ ack DONE + 预生成 failed，端口不泄漏
    started, status = await runner.start(data, reports.cb)
    assert started
    assert status is not None and status.status == "failed"
    await runner.wait_closed()


@pytest.mark.asyncio
async def test_wait_closed_kills_active_preview_no_orphan(tmp_path: Path) -> None:
    runner = PreviewRunner(health_timeout=15.0, poll_interval=0.1)
    reports = Reports()
    data = _start_data(tmp_path)
    started, _ = await runner.start(data, reports.cb)
    assert started
    await until(lambda: bool(reports.by_status("running")), timeout=15)
    port = reports.by_status("running")[-1].port
    assert port is not None and await asyncio.to_thread(_tcp_reachable, port)
    # shutdown 逐个杀子 → 无孤儿（端口不再可达）
    await runner.wait_closed()
    await asyncio.sleep(0.5)
    assert not await asyncio.to_thread(_tcp_reachable, port)


@pytest.mark.asyncio
async def test_preview_start_handler_acks_done_then_natural_key_noop(tmp_path: Path) -> None:
    transport = RecordingTransport()
    client, _adapter, _ = make_client(tmp_path, transport=transport)
    client.previews = PreviewRunner(health_timeout=15.0, poll_interval=0.1)
    data = _start_data(tmp_path)
    try:
        # 起进程立即 ack DONE；健康检查异步 → running 上报
        await client.handle_instr(instr("preview.start", data.model_dump(mode="json")))
        assert transport.last_ack()["result"] == "done"

        def running_reports() -> list[dict[str, object]]:
            return [
                r
                for r in transport.reports("preview.status")
                if r["data"]["status"] == "running"
            ]

        await until(lambda: bool(running_reports()), timeout=15)
        assert running_reports()[-1]["data"]["port"]
        # 同 preview_session_id、新 frame_id → 自然键 noop + 补报现状端口
        await client.handle_instr(instr("preview.start", data.model_dump(mode="json")))
        assert transport.last_ack()["result"] == "noop"
    finally:
        await client.previews.wait_closed()


@pytest.mark.asyncio
async def test_preview_stop_handler_acks_done_then_noop(tmp_path: Path) -> None:
    transport = RecordingTransport()
    client, _adapter, _ = make_client(tmp_path, transport=transport)
    client.previews = PreviewRunner(health_timeout=15.0, poll_interval=0.1)
    data = _start_data(tmp_path)
    stop_payload = {"preview_session_id": data.preview_session_id}
    try:
        await client.handle_instr(instr("preview.start", data.model_dump(mode="json")))

        def has_status(status: str) -> bool:
            return any(
                r["data"]["status"] == status for r in transport.reports("preview.status")
            )

        await until(lambda: has_status("running"), timeout=15)
        # 停 → ack DONE + recycled 上报
        await client.handle_instr(instr("preview.stop", stop_payload))
        assert transport.last_ack()["result"] == "done"
        assert has_status("recycled")
        # 再停 → 自然键 noop
        await client.handle_instr(instr("preview.stop", stop_payload))
        assert transport.last_ack()["result"] == "noop"
    finally:
        await client.previews.wait_closed()


@pytest.mark.asyncio
async def test_port_registry_concurrent_acquire_distinct() -> None:
    reg = _PortRegistry()
    ports = await asyncio.gather(*[reg.acquire() for _ in range(20)])
    assert len(set(ports)) == len(ports)  # 并发分配全互异
    for p in ports:
        reg.release(p)


# --------------------------------------------------------------------------- win32 专属


@pytest.mark.skipif(sys.platform != "win32", reason="netstat/taskkill 反查孙进程 = win32 实机路径")
@pytest.mark.asyncio
async def test_liveness_external_kill_grandchild_reports_failed(tmp_path: Path) -> None:
    runner = PreviewRunner(health_timeout=15.0, poll_interval=0.1)
    reports = Reports()
    data = _start_data(tmp_path)
    try:
        await runner.start(data, reports.cb)
        await until(lambda: bool(reports.by_status("running")), timeout=15)
        port = reports.by_status("running")[-1].port
        assert port is not None
        gpid = _find_listening_pid(port)
        assert gpid is not None
        _taskkill(gpid)  # 外力杀孙（模拟 dev server 自崩）→ 存活监控应捕获 → failed
        await until(lambda: bool(reports.by_status("failed")), timeout=8)
        assert reports.by_status("failed")
    finally:
        await runner.wait_closed()


@pytest.mark.skipif(sys.platform != "win32", reason="netstat/tasklist 反查孙进程 = win32 实机路径")
@pytest.mark.asyncio
async def test_stop_kills_tree_grandchild_dead_and_port_released(tmp_path: Path) -> None:
    runner = PreviewRunner(health_timeout=15.0, poll_interval=0.1)
    reports = Reports()
    data = _start_data(tmp_path)
    try:
        await runner.start(data, reports.cb)
        await until(lambda: bool(reports.by_status("running")), timeout=15)
        port = reports.by_status("running")[-1].port
        assert port is not None
        gpid = _find_listening_pid(port)
        assert gpid is not None and _pid_alive(gpid)
        stopped, st = await runner.stop(data.preview_session_id)
        assert stopped and st is not None and st.status == "recycled"
        await asyncio.sleep(0.6)
        assert not _pid_alive(gpid)  # taskkill /F /T 连孙一并杀
        assert not _tcp_reachable(port)  # 端口释放
    finally:
        await runner.wait_closed()
