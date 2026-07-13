"""M7 K2 daemon 预览长驻 dev server 进程域（契约 D §5.3 / §7）。

`check.run` 是"跑完即止"的短命令；预览 dev server 是**长驻**：起进程 → 健康检查轮询 vs
存活监控**并行竞速** → 可达上报 running 携 port / 夭折或超时上报 failed 携 log_tail（≤2KB）。
自然键 = preview_session_id：已在跑重发 → noop + 补报现状端口；已停/未知 → noop。

**端口唯一性靠进程内注册表，不靠 OS**（K2-cal 最关键坑）：win32 的 SO_REUSEADDR 让同端口
双绑不被 OS 拒绝（http.server/Vite 默认设该选项），故 daemon 必须自持端口唯一性（assigned
集合 + asyncio.Lock，撞则重取）。**判定归 server、执行归 daemon**：回收/超时阈值判定在 server
或契约默认，daemon 收指令即执行、只上报事实（port / log_tail / status）。win32 进程树终止 =
`taskkill /F /T`（复用 checks.py `_kill_process_tree`，K2-cal 验证覆盖孙进程）。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from coagentia_contracts.daemon import PreviewStartData, PreviewStatusData

from coagentia_daemon.checks import _kill_process_tree

# 参数默认（实现默认，非协议形状；出处见 PREVIEW-CALIBRATION §3.6 / 契约 D §5.3）。
HEALTH_TIMEOUT_SEC = 120.0  # 健康检查超时（契约 D §5.3）
HEALTH_POLL_SEC = 0.5  # TCP 连通轮询间隔
TCP_CONNECT_TIMEOUT_SEC = 0.5  # 单次探测连接超时
PREVIEW_LOG_TAIL_BYTES = 2 * 1024  # failed 携进程输出尾上限（契约 A v1.0.11 / D preview.status）
_ACQUIRE_TRIES = 50

PreviewStatus = Literal["starting", "running", "recycled", "failed"]
PreviewStatusCallback = Callable[[PreviewStatusData], Awaitable[None]]


def _pick_free_port() -> int:
    """绑 127.0.0.1:0 取内核分配端口后立即释放（K2-cal §3.1 取端口法）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _bounded_preview_tail(raw: bytes) -> str:
    """把原始尾部收敛为编码后 ≤2KB 的合法 UTF-8 文本（镜像 checks._bounded_utf8_tail 但 2KB）。"""
    raw = raw[-PREVIEW_LOG_TAIL_BYTES:]
    while raw and raw[0] & 0xC0 == 0x80:  # 去掉截断处的 UTF-8 续字节
        raw = raw[1:]
    text = raw.decode("utf-8", errors="replace")
    while len(text.encode("utf-8")) > PREVIEW_LOG_TAIL_BYTES:
        text = text[1:]
    return text


async def _read_tail(stream: asyncio.StreamReader | None, tail: bytearray) -> None:
    """持续排空子进程合流输出，仅保留末尾 2KB（防管道写满阻塞长驻进程）。"""
    if stream is None:
        return
    while chunk := await stream.read(8192):
        tail.extend(chunk)
        if len(tail) > PREVIEW_LOG_TAIL_BYTES:
            del tail[: len(tail) - PREVIEW_LOG_TAIL_BYTES]


class _PortRegistry:
    """daemon 进程内端口唯一性缓解手段：已分配端口集合 + 锁（K2-cal §3.2 权威）。

    Windows 不拒绝重复绑定（SO_REUSEADDR），故 daemon 不能靠 OS 保证端口唯一；用进程内注册表在
    并发 preview.start 间串行分配，`_pick_free_port` 结果撞注册表则重取；stop/failed/recycled 释放。
    """

    def __init__(self) -> None:
        self._assigned: set[int] = set()
        self._lock = asyncio.Lock()

    async def acquire(self) -> int:
        async with self._lock:
            for _ in range(_ACQUIRE_TRIES):
                port = _pick_free_port()
                if port not in self._assigned:
                    self._assigned.add(port)
                    return port
            raise RuntimeError("无法取得未占用的空闲端口")

    def release(self, port: int) -> None:
        self._assigned.discard(port)


@dataclass
class _Preview:
    """一个预览会话的进程域状态（自然键 = session_id）。"""

    session_id: str
    status: PreviewStatus
    port: int | None = None
    proc: asyncio.subprocess.Process | None = None
    tail: bytearray = field(default_factory=bytearray)
    reader: asyncio.Task[None] | None = None
    monitor: asyncio.Task[None] | None = None
    stopping: bool = False  # stop/shutdown 抢先标记：置位后 monitor 静默退出不上报 failed
    log_tail: str | None = None


class PreviewRunner:
    """preview_session_id 自然键的长驻 dev server 管理器（CheckRunner 长驻变体）。

    - `start`：起进程后立即返回让 handler ack DONE，健康检查/存活监控在后台 monitor 上报；
    - `stop`：杀树 + 上报 recycled；
    - `wait_closed`：shutdown 逐个杀活跃子进程（清洁关闭无孤儿，K2-cal §3.4）。
    """

    def __init__(
        self,
        *,
        health_timeout: float = HEALTH_TIMEOUT_SEC,
        poll_interval: float = HEALTH_POLL_SEC,
        connect_timeout: float = TCP_CONNECT_TIMEOUT_SEC,
    ) -> None:
        self._health_timeout = health_timeout
        self._poll_interval = poll_interval
        self._connect_timeout = connect_timeout
        self._registry = _PortRegistry()
        self._previews: dict[str, _Preview] = {}
        self._closing = False

    # ---------------------------------------------------------------- start（自然键幂等）

    async def start(
        self, data: PreviewStartData, report_cb: PreviewStatusCallback
    ) -> tuple[bool, PreviewStatusData | None]:
        """起 dev server；同 session_id 已存在 → noop + 返回现状（含端口）供 handler 补报。

        返回 (started, status)：started=True → ack DONE（健康检查异步经 monitor 上报）；
        started=False → ack NOOP。status 非空则由 handler 补报（现状端口 / 预生成 failed）。
        """
        session_id = data.preview_session_id
        if self._closing:
            return (False, None)
        existing = self._previews.get(session_id)
        if existing is not None:
            # 已在跑/已终态 → noop + 补报现状（"已在跑 → 上报端口"，契约 D §5.3）。
            return (False, self._status_of(existing))

        port = await self._registry.acquire()
        # 必须用 shell（非 exec）：%PORT%/$PORT 由 shell 展开，npm run dev 类命令本就是 shell 串
        # （K2-cal §3.1）。PORT 注入子进程环境，dev server 亦可读 process.env.PORT（约定优于配置）。
        env = dict(os.environ)
        env.update(
            {
                "PORT": str(port),
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
            }
        )
        try:
            proc = await asyncio.create_subprocess_shell(
                data.dev_command,
                cwd=str(Path(data.worktree_path)),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as exc:
            # 起进程即失败（如 worktree_path 非目录）→ 释放端口 + 记终态 + 预生成 failed 供补报。
            self._registry.release(port)
            pv = _Preview(
                session_id=session_id,
                status="failed",
                log_tail=_bounded_preview_tail(str(exc).encode("utf-8")),
            )
            self._previews[session_id] = pv
            return (True, self._status_of(pv))

        pv = _Preview(session_id=session_id, status="starting", port=port, proc=proc)
        pv.reader = asyncio.create_task(_read_tail(proc.stdout, pv.tail))
        self._previews[session_id] = pv
        pv.monitor = asyncio.create_task(self._run_monitor(pv, report_cb))
        pv.monitor.add_done_callback(lambda task: self._finish_monitor(task))
        return (True, None)

    # ------------------------------------------------------------ 后台 monitor（健康 vs 存活竞速）

    async def _run_monitor(self, pv: _Preview, report_cb: PreviewStatusCallback) -> None:
        try:
            await self._monitor(pv, report_cb)
        finally:
            # 停 tail 读取（进程已死时 stdout 早 EOF，reader 多半自然结束；此处兜底）。
            if pv.reader is not None and not pv.reader.done():
                pv.reader.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pv.reader

    async def _monitor(self, pv: _Preview, report_cb: PreviewStatusCallback) -> None:
        assert pv.proc is not None
        reachable = await self._await_ready(pv)
        if pv.stopping:
            return  # stop/shutdown 抢先杀进程 → 由其上报 recycled，monitor 不再上报。
        if reachable:
            pv.status = "running"
            await report_cb(
                PreviewStatusData(preview_session_id=pv.session_id, status="running", port=pv.port)
            )
            # 就绪后续挂存活监控：进程退出（dev server 自行崩溃/被外力杀）→ failed 携 log_tail。
            await pv.proc.wait()
            if pv.stopping:
                return
            await self._report_failed(pv, report_cb)
            return
        # 未就绪（健康超时或进程夭折）→ 杀树（进程若还活）+ failed 携 log_tail。
        await _kill_process_tree(pv.proc)
        if pv.stopping:
            return
        await self._report_failed(pv, report_cb)

    async def _report_failed(self, pv: _Preview, report_cb: PreviewStatusCallback) -> None:
        pv.status = "failed"
        pv.log_tail = await self._finalize_tail(pv)
        await report_cb(
            PreviewStatusData(
                preview_session_id=pv.session_id, status="failed", log_tail=pv.log_tail
            )
        )
        if pv.port is not None:
            self._registry.release(pv.port)

    async def _await_ready(self, pv: _Preview) -> bool:
        """健康检查（TCP 轮询）与存活监控（proc.wait）**并行竞速** FIRST_COMPLETED。

        坏命令/夭折进程先退出 → 立即判未就绪，不空等 120s 健康超时（K2-cal §3.5）。
        """
        assert pv.proc is not None
        wait_task = asyncio.ensure_future(pv.proc.wait())
        health_task = asyncio.ensure_future(self._health_check(pv.port))
        try:
            await asyncio.wait({wait_task, health_task}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (wait_task, health_task):
                if not task.done():
                    task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        return (
            health_task.done()
            and not health_task.cancelled()
            and health_task.result() is True
        )

    async def _health_check(self, port: int | None) -> bool:
        if port is None:
            return False
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._health_timeout
        while loop.time() < deadline:
            if await self._tcp_reachable(port):
                return True
            await asyncio.sleep(self._poll_interval)
        return False

    async def _tcp_reachable(self, port: int) -> bool:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", port), timeout=self._connect_timeout
            )
        except (OSError, TimeoutError):
            return False
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return True

    async def _finalize_tail(self, pv: _Preview) -> str:
        """收尾 tail：等 reader 排空剩余输出后再有界收敛（进程已死 → stdout EOF 很快）。"""
        if pv.reader is not None and not pv.reader.done():
            with contextlib.suppress(Exception):
                await asyncio.wait_for(asyncio.shield(pv.reader), timeout=3.0)
        return _bounded_preview_tail(bytes(pv.tail))

    # ---------------------------------------------------------------- stop（自然键幂等）

    async def stop(self, session_id: str) -> tuple[bool, PreviewStatusData | None]:
        """杀树 + 上报 recycled；已停/未知/已终态 → noop（契约 D §5.3；回收判定在 server）。"""
        pv = self._previews.get(session_id)
        if pv is None or pv.status in ("recycled", "failed"):
            return (False, None)
        pv.stopping = True  # 抢先置位：monitor 见 stopping 静默退出，不抢报 failed。
        if pv.proc is not None:
            await _kill_process_tree(pv.proc)
        if pv.monitor is not None and not pv.monitor.done():
            with contextlib.suppress(Exception):
                await asyncio.wait_for(asyncio.shield(pv.monitor), timeout=5.0)
        if pv.port is not None:
            self._registry.release(pv.port)
        pv.status = "recycled"
        return (True, PreviewStatusData(preview_session_id=session_id, status="recycled"))

    # ---------------------------------------------------------------- shutdown（清洁关闭无孤儿）

    def cancel(self) -> None:
        self._closing = True

    async def wait_closed(self) -> None:
        """shutdown：逐个 taskkill 所有活跃预览子进程，等 monitor 收敛（K2-cal §3.4）。"""
        self._closing = True
        monitors: list[asyncio.Task[None]] = []
        for pv in list(self._previews.values()):
            if pv.status in ("starting", "running") and not pv.stopping:
                pv.stopping = True
                if pv.proc is not None:
                    with contextlib.suppress(Exception):
                        await _kill_process_tree(pv.proc)
                if pv.port is not None:
                    self._registry.release(pv.port)
                pv.status = "recycled"
            if pv.monitor is not None and not pv.monitor.done():
                monitors.append(pv.monitor)
        if monitors:
            await asyncio.gather(*monitors, return_exceptions=True)

    # ---------------------------------------------------------------- 内部

    def _status_of(self, pv: _Preview) -> PreviewStatusData:
        """由当前进程域状态构造上报帧：running/starting 携 port，failed 携 log_tail。"""
        port = pv.port if pv.status in ("starting", "running") else None
        log_tail = pv.log_tail if pv.status == "failed" else None
        return PreviewStatusData(
            preview_session_id=pv.session_id, status=pv.status, port=port, log_tail=log_tail
        )

    def _finish_monitor(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            task.exception()  # 取走异常避免后台 Task warning（同 CheckRunner._finish_task）。


__all__ = [
    "HEALTH_TIMEOUT_SEC",
    "PREVIEW_LOG_TAIL_BYTES",
    "PreviewRunner",
]
