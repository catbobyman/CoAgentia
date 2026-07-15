"""M6 check 系统节点的本机命令执行器（契约 D §5.3 / §7）。

命令在 Project 主工作区执行，stdout/stderr 合流后仅保留 UTF-8 尾部 4KB。超时在 Windows
使用 ``taskkill /F /T`` 清理整棵进程树；其它平台终止本次启动的 shell 进程。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from coagentia_contracts.daemon import CheckFinishedData, CheckRunData

CHECK_TIMEOUT_SEC = 30 * 60.0
OUTPUT_TAIL_BYTES = 4 * 1024


@dataclass(frozen=True, slots=True)
class CheckProcessResult:
    exit_code: int
    output_tail: str


CheckProcessRunner = Callable[[CheckRunData, float], Awaitable[CheckProcessResult]]
CheckFinishedCallback = Callable[[CheckFinishedData], Awaitable[None]]


async def run_check_process(
    data: CheckRunData, timeout_sec: float = CHECK_TIMEOUT_SEC
) -> CheckProcessResult:
    """在 repo 主工作区经平台 shell 执行既有 command，并有界采集输出尾。"""
    repo = Path(data.repo_path).expanduser().resolve()
    if not repo.is_dir():
        return CheckProcessResult(127, f"repo_path 不存在或不是目录：{repo}")

    env = dict(os.environ)
    env.update(
        {
            "LC_ALL": "C.UTF-8",
            "LANG": "C.UTF-8",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    try:
        proc = await asyncio.create_subprocess_shell(
            data.command,
            cwd=str(repo),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
    except OSError as exc:
        return CheckProcessResult(127, _bounded_utf8_tail(str(exc).encode("utf-8")))

    tail = bytearray()
    reader = asyncio.create_task(_read_tail(proc.stdout, tail))
    timed_out = False
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout_sec)
    except TimeoutError:
        timed_out = True
        await _kill_process_tree(proc)
    except asyncio.CancelledError:
        await asyncio.shield(_kill_process_tree(proc))
        raise
    finally:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(reader, timeout=3.0)

    if timed_out:
        marker = f"\n[check timeout after {timeout_sec:g}s]\n".encode()
        _append_tail(tail, marker)
        exit_code = 124
    else:
        exit_code = proc.returncode if proc.returncode is not None else 1
    return CheckProcessResult(exit_code, _bounded_utf8_tail(bytes(tail)))


async def _read_tail(
    stream: asyncio.StreamReader | None, tail: bytearray
) -> None:
    if stream is None:
        return
    while chunk := await stream.read(8192):
        _append_tail(tail, chunk)


def _append_tail(tail: bytearray, chunk: bytes) -> None:
    tail.extend(chunk)
    if len(tail) > OUTPUT_TAIL_BYTES:
        del tail[: len(tail) - OUTPUT_TAIL_BYTES]


def _bounded_utf8_tail(raw: bytes) -> str:
    """把原始尾部收敛为编码后不超过 4KB 的合法 UTF-8 文本。"""
    raw = raw[-OUTPUT_TAIL_BYTES:]
    while raw and raw[0] & 0xC0 == 0x80:
        raw = raw[1:]
    text = raw.decode("utf-8", errors="replace")
    while len(text.encode("utf-8")) > OUTPUT_TAIL_BYTES:
        text = text[1:]
    return text


async def _kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    killed_tree = False
    if sys.platform == "win32" and proc.pid:
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/F",
                "/T",
                "/PID",
                str(proc.pid),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out_raw, err_raw = await killer.communicate()
            out_raw.decode("utf-8", errors="replace")
            err_raw.decode("utf-8", errors="replace")
            killed_tree = killer.returncode == 0
        except Exception:
            killed_tree = False
    if not killed_tree:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(proc.wait(), timeout=3.0)


class CheckRunner:
    """run_id 自然键执行器；同进程重发只重报终态，不重复跑命令。"""

    def __init__(
        self,
        *,
        runner: CheckProcessRunner = run_check_process,
        timeout_sec: float = CHECK_TIMEOUT_SEC,
    ) -> None:
        self._runner = runner
        self._timeout_sec = timeout_sec
        self._finished: dict[str, CheckFinishedData] = {}
        self._running: dict[str, asyncio.Task[None]] = {}
        self._closing = False

    async def run(self, data: CheckRunData) -> tuple[bool, CheckFinishedData]:
        known = self._finished.get(data.run_id)
        if known is not None:
            return False, known
        try:
            result = await self._runner(data, self._timeout_sec)
        except Exception as exc:  # noqa: BLE001 - 执行边界统一收敛为 check.failed
            result = CheckProcessResult(127, _bounded_utf8_tail(str(exc).encode()))
        finished = CheckFinishedData(
            run_id=data.run_id,
            node_id=data.node_id,
            status="success" if result.exit_code == 0 else "failed",
            exit_code=result.exit_code,
            output_tail=result.output_tail,
        )
        self._finished[data.run_id] = finished
        return True, finished

    def start(
        self, data: CheckRunData, on_finished: CheckFinishedCallback
    ) -> tuple[bool, CheckFinishedData | None]:
        """后台启动长命令，使 instr 可立即 ack；同 run_id 在跑/终态均自然键 noop。"""
        if self._closing:
            return False, None
        if data.run_id in self._running:
            return False, None
        known = self._finished.get(data.run_id)
        if known is not None:
            return False, known
        task = asyncio.create_task(self._execute(data, on_finished))
        self._running[data.run_id] = task
        task.add_done_callback(lambda done: self._finish_task(data.run_id, done))
        return True, None

    async def _execute(
        self, data: CheckRunData, on_finished: CheckFinishedCallback
    ) -> None:
        _changed, finished = await self.run(data)
        await on_finished(finished)

    def _finish_task(self, run_id: str, task: asyncio.Task[None]) -> None:
        self._running.pop(run_id, None)
        if not task.cancelled():
            task.exception()  # 取走回调/落盘异常，避免后台 Task warning。

    def cancel(self) -> None:
        self._closing = True
        for task in tuple(self._running.values()):
            task.cancel()

    async def wait_closed(self) -> None:
        self.cancel()
        if self._running:
            await asyncio.gather(*tuple(self._running.values()), return_exceptions=True)


__all__ = [
    "CHECK_TIMEOUT_SEC",
    "OUTPUT_TAIL_BYTES",
    "CheckProcessResult",
    "CheckRunner",
    "run_check_process",
]
