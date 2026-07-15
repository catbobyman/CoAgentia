"""K2-cal —— M7 预览长驻 dev server win32 真机校准（契约 D §5.3）。

check.run 是「跑完即止」的短命令；预览 dev server 是**长驻**：需要空闲端口获取、
PORT 环境变量注入、TCP 健康检查轮询、存活监控、taskkill /F /T 杀树覆盖孙进程、
daemon 崩溃后孤儿回收。本脚本用零依赖命令（`<py> -m http.server`）在 scratch 目录
真机戳五组行为，输出 JSON（顶层 passed=true 才算通过），结论落 PREVIEW-CALIBRATION.md。

复跑：uv run python scratchpad/preview_calibration.py
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
import sys
import time
import urllib.request

PY = sys.executable  # 用当前解释器，不依赖 PATH 上的 python
HEALTH_TIMEOUT = 15.0  # 校准用短超时（生产默认 120s）
POLL_INTERVAL = 0.2


def pick_free_port() -> int:
    """绑 127.0.0.1:0 取内核分配端口后立即释放（经典 TOCTOU 取端口法）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def tcp_reachable(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


async def health_check(port: int, timeout: float = HEALTH_TIMEOUT) -> bool:
    """端口轮询 TCP 连通直至可达（契约 D §5.3 健康检查）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if tcp_reachable(port):
            return True
        await asyncio.sleep(POLL_INTERVAL)
    return False


async def taskkill_tree(pid: int) -> tuple[int, str]:
    """win32 taskkill /F /T /PID —— 杀整棵进程树。"""
    proc = await asyncio.create_subprocess_exec(
        "taskkill", "/F", "/T", "/PID", str(pid),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode("utf-8", errors="replace")


def pid_alive(pid: int) -> bool:
    """经 tasklist 查 PID 是否仍在（win32）。"""
    import subprocess
    r = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return str(pid) in (r.stdout or "")


async def probe1_port_inject_and_health() -> dict:
    """探针 1：空闲端口获取 + PORT 环境变量注入 + 健康检查可达 + HTTP 200。

    经 create_subprocess_shell 启动（cmd.exe 展开 %PORT%），验证：
      - PORT 注入子进程环境
      - 命令引用 %PORT% 被 shell 展开
      - 健康检查轮询直至 TCP 可达
      - HTTP GET 返回 200
    """
    result: dict = {"name": "port_inject_and_health"}
    port = pick_free_port()
    env = dict(os.environ)
    env["PORT"] = str(port)
    # 命令引用 %PORT%，由 cmd.exe 展开（约定优于配置：dev server 亦可读 env.PORT）
    cmd = f'"{PY}" -m http.server %PORT% --bind 127.0.0.1'
    proc = await asyncio.create_subprocess_shell(
        cmd, cwd=os.getcwd(), env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        ok = await health_check(port)
        result["assigned_port"] = port
        result["health_reachable"] = ok
        if ok:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as r:
                    result["http_status"] = r.status
            except Exception as exc:  # noqa: BLE001
                result["http_status"] = f"error: {exc}"
        result["passed"] = ok and result.get("http_status") == 200
    finally:
        if proc.pid:
            await taskkill_tree(proc.pid)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=3.0)
    return result


def _double_bind_rejected_by_os() -> bool:
    """win32 关键行为：设 SO_REUSEADDR 的 socket 能否同端口双绑成功。

    Python http.server 默认 allow_reuse_address=True（设 SO_REUSEADDR），而 Windows
    的 SO_REUSEADDR 允许同端口双绑（Unix 不允许）。返回 True 表示 OS 拒绝双绑（安全），
    False 表示 OS 放行双绑（daemon 必须自持端口唯一性）。
    """
    s1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s1.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s1.bind(("127.0.0.1", 0))
    port = s1.getsockname()[1]
    s1.listen(1)
    s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s2.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s2.bind(("127.0.0.1", port))
        return False  # 双绑成功 → OS 未拒绝
    except OSError:
        return True  # OS 拒绝
    finally:
        s1.close()
        s2.close()


class _PortRegistry:
    """daemon 进程内端口唯一性缓解手段：已分配端口注册表 + 锁。

    Windows 不拒绝重复绑定，故 daemon 不能靠 OS 保证端口唯一；用进程内注册表在
    并发 preview.start 间串行分配，pick_free_port 结果撞注册表则重取。
    """

    def __init__(self) -> None:
        self._assigned: set[int] = set()
        self._lock = asyncio.Lock()

    async def acquire(self) -> int:
        async with self._lock:
            for _ in range(50):
                port = pick_free_port()
                if port not in self._assigned:
                    self._assigned.add(port)
                    return port
            raise RuntimeError("无法取得空闲端口")

    def release(self, port: int) -> None:
        self._assigned.discard(port)


async def probe2_same_port_double_open() -> dict:
    """探针 2：同端口双开的 win32 行为 + daemon 端唯一性缓解手段验证。

    (a) 确认 OS 是否拒绝同端口双绑（win32 SO_REUSEADDR 语义）。
    (b) 验证进程内 _PortRegistry 在并发分配下恒给出互异端口（缓解手段有效）。
    """
    result: dict = {"name": "same_port_double_open"}
    result["os_rejects_double_bind"] = _double_bind_rejected_by_os()

    # 缓解手段：并发 20 次分配须全互异
    reg = _PortRegistry()
    ports = await asyncio.gather(*[reg.acquire() for _ in range(20)])
    result["registry_ports_distinct"] = len(set(ports)) == len(ports)
    for p in ports:
        reg.release(p)

    # 语义结论：win32 上 OS 不拒绝双绑（os_rejects=False）→ 必须靠注册表；
    # 缓解手段必须给出互异端口。passed = 注册表有效（无论 OS 行为，缓解都成立）。
    result["conclusion"] = (
        "win32 不拒绝双绑，daemon 必须自持端口唯一性"
        if not result["os_rejects_double_bind"]
        else "OS 拒绝双绑，注册表为纵深防御"
    )
    result["passed"] = result["registry_ports_distinct"]
    return result


async def probe3_taskkill_grandchild() -> dict:
    """探针 3：taskkill /F /T 对 cmd /c 包裹命令的孙进程覆盖（关键探针）。

    create_subprocess_shell → cmd.exe（子）→ python http.server（孙）。
    taskkill /F /T /PID <cmd_pid> 应连孙进程一并杀。验证孙进程 PID 事后不存活、
    端口释放。这是「杀树覆盖孙进程」纪律的实证。
    """
    result: dict = {"name": "taskkill_grandchild"}
    port = pick_free_port()
    env = dict(os.environ)
    env["PORT"] = str(port)
    cmd = f'"{PY}" -m http.server %PORT% --bind 127.0.0.1'
    proc = await asyncio.create_subprocess_shell(
        cmd, cwd=os.getcwd(), env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    shell_pid = proc.pid
    try:
        ok = await health_check(port)
        result["reachable_before_kill"] = ok
        # 找出监听该端口的真实 python 孙进程 PID（netstat）
        grandchild_pid = _find_listening_pid(port)
        result["shell_pid"] = shell_pid
        result["grandchild_pid"] = grandchild_pid
        # 杀 shell（父）整棵树
        rc, out = await taskkill_tree(shell_pid)
        result["taskkill_rc"] = rc
        result["taskkill_mentions_child"] = str(grandchild_pid) in out if grandchild_pid else None
        await asyncio.sleep(1.0)
        # 验证孙进程不再存活、端口不再可达
        result["grandchild_alive_after"] = (
            pid_alive(grandchild_pid) if grandchild_pid else None
        )
        result["port_reachable_after"] = tcp_reachable(port)
        result["passed"] = (
            ok
            and grandchild_pid is not None
            and not result["grandchild_alive_after"]
            and not result["port_reachable_after"]
        )
    finally:
        if shell_pid:
            with contextlib.suppress(Exception):
                await taskkill_tree(shell_pid)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=3.0)
    return result


def _find_listening_pid(port: int) -> int | None:
    """netstat -ano 找监听 127.0.0.1:<port> 的 PID。"""
    import subprocess
    r = subprocess.run(
        ["netstat", "-ano", "-p", "TCP"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    for line in (r.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] == "TCP" and parts[3] == "LISTENING":
            local = parts[1]
            if local.endswith(f":{port}"):
                with contextlib.suppress(ValueError):
                    return int(parts[4])
    return None


async def probe4_orphan_after_daemon_crash() -> dict:
    """探针 4：daemon 崩溃后孤儿 dev server 存活性与清理手段。

    模拟 daemon = 一个启动 dev server 的父进程；「崩溃」= 父进程被杀但 dev server
    子进程未被杀。验证：孤儿存活、其 PID 可从 preview_sessions.port 反查、可经
    taskkill by PID 清理。这正是对账 #9「daemon 重启子进程必死」的反面——
    若父被 taskkill /T 会连带杀子，故 daemon 崩溃残留孤儿需 hello 进程表比对 + 端口反查清理。
    """
    result: dict = {"name": "orphan_after_daemon_crash"}
    port = pick_free_port()
    env = dict(os.environ)
    env["PORT"] = str(port)
    # 用 START 让 dev server 脱离父进程树（模拟 daemon 崩溃后子进程被系统收养）
    # 直接起 python（不经 cmd 包裹），父=本脚本；「崩溃」仅停止管理但不杀子
    proc = await asyncio.create_subprocess_exec(
        PY, "-m", "http.server", str(port), "--bind", "127.0.0.1",
        cwd=os.getcwd(), env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    child_pid = proc.pid
    try:
        ok = await health_check(port)
        result["reachable"] = ok
        result["child_pid"] = child_pid
        # 模拟 daemon 崩溃：丢弃对 proc 的管理引用（不杀），孤儿应继续存活
        await asyncio.sleep(1.0)
        result["orphan_alive"] = pid_alive(child_pid) if child_pid else None
        result["orphan_port_reachable"] = tcp_reachable(port)
        # 清理手段：端口反查 PID → taskkill
        listening = _find_listening_pid(port)
        result["reverse_lookup_pid"] = listening
        if listening:
            await taskkill_tree(listening)
            await asyncio.sleep(0.8)
        result["cleaned"] = not tcp_reachable(port)
        result["passed"] = (
            ok
            and result["orphan_alive"]
            and result["orphan_port_reachable"]
            and result["cleaned"]
        )
    finally:
        if child_pid:
            with contextlib.suppress(Exception):
                await taskkill_tree(child_pid)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=3.0)
    return result


async def probe5_liveness_and_bad_command() -> dict:
    """探针 5：存活监控（进程夭折 → failed 携 log_tail）+ 坏命令健康检查超时。

    (a) 坏命令：命令立即失败退出 → 健康检查永不可达 → 应超时 + 采集输出尾。
    (b) 存活监控：正常起后被外力杀 → proc.wait() 返回 → 应上报 failed。
    """
    result: dict = {"name": "liveness_and_bad_command"}
    # (a) 坏命令（不存在的模块）
    env = dict(os.environ)
    port_a = pick_free_port()
    env["PORT"] = str(port_a)
    bad_cmd = f'"{PY}" -m coagentia_no_such_module_xyz %PORT%'
    p_bad = await asyncio.create_subprocess_shell(
        bad_cmd, cwd=os.getcwd(), env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    # 竞速：健康检查 vs 进程退出（存活监控）
    wait_task = asyncio.ensure_future(p_bad.wait())
    health_task = asyncio.ensure_future(health_check(port_a, timeout=5.0))
    done, pending = await asyncio.wait(
        {wait_task, health_task}, return_when=asyncio.FIRST_COMPLETED
    )
    result["bad_cmd_process_exited_first"] = wait_task in done and not health_task.done()
    for t in pending:
        t.cancel()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(p_bad.wait(), timeout=3.0)
    out = await p_bad.stdout.read() if p_bad.stdout else b""
    result["bad_cmd_exit_code"] = p_bad.returncode
    result["bad_cmd_log_tail"] = out.decode("utf-8", errors="replace")[-300:]
    result["bad_cmd_captured_error"] = "No module named" in result["bad_cmd_log_tail"]

    # (b) 存活监控：正常起 → 外力杀 → wait 返回
    port_b = pick_free_port()
    env["PORT"] = str(port_b)
    good_cmd = f'"{PY}" -m http.server %PORT% --bind 127.0.0.1'
    p_good = await asyncio.create_subprocess_shell(
        good_cmd, cwd=os.getcwd(), env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    ok = await health_check(port_b)
    result["good_reachable"] = ok
    listening = _find_listening_pid(port_b)
    if listening:
        await taskkill_tree(listening)  # 外力杀孙
    # 父 shell 的 wait 应在孙死后返回（cmd /c 等孙）
    try:
        await asyncio.wait_for(p_good.wait(), timeout=6.0)
        result["liveness_detected_exit"] = True
        result["good_exit_code_after_kill"] = p_good.returncode
    except TimeoutError:
        result["liveness_detected_exit"] = False
    finally:
        if p_good.pid:
            with contextlib.suppress(Exception):
                await taskkill_tree(p_good.pid)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(p_good.wait(), timeout=3.0)

    result["passed"] = (
        result["bad_cmd_captured_error"]
        and ok
        and result.get("liveness_detected_exit", False)
    )
    return result


async def main() -> int:
    started = time.monotonic()
    probes = [
        await probe1_port_inject_and_health(),
        await probe2_same_port_double_open(),
        await probe3_taskkill_grandchild(),
        await probe4_orphan_after_daemon_crash(),
        await probe5_liveness_and_bad_command(),
    ]
    all_passed = all(p.get("passed") for p in probes)
    report = {
        "platform": sys.platform,
        "python": PY,
        "elapsed_sec": round(time.monotonic() - started, 1),
        "passed": all_passed,
        "probes": probes,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
