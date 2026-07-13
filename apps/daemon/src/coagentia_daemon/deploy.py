"""M7b K4 daemon 部署命令执行器（契约 D §5.3 / §7）。

`check.run` 是"跑完即止"且只回终态尾；部署命令须**流式**回日志（deploy.log 逐批携单调
chunk_seq）+ 结束回 deploy.finished（status/exit_code/url）。判定归 server、执行归 daemon：
30min 超时/杀树阈值走契约默认，daemon 收 deploy.run 即在 repo 主工作区跑命令、逐批上报日志、
结束上报终态。自然键 = deployment_id：已在跑重发 → noop；已终态重发 → 重报终态（不重跑，
副作用不可重放，铁律 3）。win32 进程树终止 = `taskkill /F /T`（复用 checks._kill_process_tree）。

URL 提取约定：部署工具（Vercel/Netlify 等）惯例把最终 URL 打在输出末尾——取**最后一个**匹配到
的 `https?://\\S+`；仅 success 吐 url，failed/超时吐 None。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from coagentia_contracts.daemon import (
    DeployFinishedData,
    DeployLogReportData,
    DeployRunData,
)

from coagentia_daemon.checks import _kill_process_tree

DEPLOY_TIMEOUT_SEC = 30 * 60.0  # 契约 D §5.3：部署超时 30min（同 check 上限口径）
_LOG_BATCH_LINES = 20  # 每批最多行数（流式响应性；满批或 0.5s 静默或 EOF 即 flush）
_LOG_BATCH_IDLE_SEC = 0.5  # 静默 flush 间隔
_URL_RE = re.compile(r"https?://\S+")

# on_log(lines)：DeployRunner 注入闭包，累加 chunk_seq 后转 report_deploy_log。
LogBatchCallback = Callable[[list[str]], Awaitable[None]]
DeployLogCallback = Callable[[DeployLogReportData], Awaitable[None]]
DeployFinishedCallback = Callable[[DeployFinishedData], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class DeployProcessResult:
    exit_code: int | None  # 超时 = None（契约 D §5.3 deploy.finished.exit_code null）
    url: str | None


DeployProcessRunner = Callable[..., Awaitable[DeployProcessResult]]


async def _stream_output(
    proc: asyncio.subprocess.Process,
    on_log: LogBatchCallback,
    url_holder: list[str | None],
) -> None:
    """逐行读子进程合流输出 → 累积成批（满 _LOG_BATCH_LINES / 静默 _LOG_BATCH_IDLE_SEC / EOF）→
    on_log(batch)；同时累计 URL 候选（保留最后一个）。防管道写满阻塞长命令。"""
    stream = proc.stdout
    if stream is None:
        return
    buffer: list[str] = []
    while True:
        try:
            line = await asyncio.wait_for(
                stream.readline(), timeout=_LOG_BATCH_IDLE_SEC
            )
        except TimeoutError:
            if buffer:
                await on_log(buffer)
                buffer = []
            continue
        if not line:  # EOF
            break
        text = line.decode("utf-8", errors="replace").rstrip("\r\n")
        buffer.append(text)
        for match in _URL_RE.finditer(text):
            url_holder[0] = match.group(0)  # 保留最后一个匹配的 URL
        if len(buffer) >= _LOG_BATCH_LINES:
            await on_log(buffer)
            buffer = []
    if buffer:
        await on_log(buffer)


async def run_deploy_process(
    data: DeployRunData,
    *,
    on_log: LogBatchCallback,
    timeout_sec: float = DEPLOY_TIMEOUT_SEC,
) -> DeployProcessResult:
    """在 repo 主工作区经平台 shell 执行部署命令，流式回日志 + 提取末行 URL。"""
    repo = Path(data.repo_path).expanduser().resolve()
    if not repo.is_dir():
        await on_log([f"repo_path 不存在或不是目录：{repo}"])
        return DeployProcessResult(127, None)

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
        await on_log([f"部署进程启动失败：{exc}"])
        return DeployProcessResult(127, None)

    url_holder: list[str | None] = [None]
    streamer = asyncio.create_task(_stream_output(proc, on_log, url_holder))
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
            await asyncio.wait_for(streamer, timeout=5.0)

    if timed_out:
        await on_log([f"[deploy timeout after {timeout_sec:g}s]"])
        return DeployProcessResult(None, None)  # 超时 = failed，exit_code null
    exit_code = proc.returncode if proc.returncode is not None else 1
    url = url_holder[0] if exit_code == 0 else None  # 仅 success 吐 url
    return DeployProcessResult(exit_code, url)


class DeployRunner:
    """deployment_id 自然键的部署执行器（CheckRunner 流式变体）。

    - `start`：起后台 task 跑 `_execute` 立即返回让 instr ack DONE；已在跑/已终态 → noop；
    - chunk_seq 从 0 单调递增（per deployment_id）；
    - `cancel/wait_closed`：shutdown 杀活跃部署树（不留孤儿）。
    """

    def __init__(
        self,
        *,
        runner: DeployProcessRunner = run_deploy_process,
        timeout_sec: float = DEPLOY_TIMEOUT_SEC,
    ) -> None:
        self._runner = runner
        self._timeout_sec = timeout_sec
        self._finished: dict[str, DeployFinishedData] = {}
        self._running: dict[str, asyncio.Task[None]] = {}
        self._chunk_seq: dict[str, int] = {}
        self._closing = False

    def start(
        self,
        data: DeployRunData,
        on_log: DeployLogCallback,
        on_finished: DeployFinishedCallback,
    ) -> tuple[bool, DeployFinishedData | None]:
        """后台起部署命令使 instr 立即 ack；同 deployment_id 在跑 → noop、终态 → 返回终态供重报。"""
        did = data.deployment_id
        if self._closing:
            return (False, None)
        if did in self._running:
            return (False, None)
        known = self._finished.get(did)
        if known is not None:
            return (False, known)
        task = asyncio.create_task(self._execute(data, on_log, on_finished))
        self._running[did] = task
        task.add_done_callback(lambda done: self._finish_task(did, done))
        return (True, None)

    async def _execute(
        self,
        data: DeployRunData,
        on_log: DeployLogCallback,
        on_finished: DeployFinishedCallback,
    ) -> None:
        did = data.deployment_id

        async def log_cb(lines: list[str]) -> None:
            seq = self._chunk_seq.get(did, 0)
            self._chunk_seq[did] = seq + 1
            await on_log(
                DeployLogReportData(deployment_id=did, chunk_seq=seq, lines=lines)
            )

        try:
            result = await self._runner(
                data, on_log=log_cb, timeout_sec=self._timeout_sec
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — 执行边界统一收敛为 deploy.failed
            result = DeployProcessResult(127, None)
        finished = DeployFinishedData(
            deployment_id=did,
            status="success" if result.exit_code == 0 else "failed",
            exit_code=result.exit_code,
            url=result.url,
        )
        self._finished[did] = finished
        await on_finished(finished)

    def _finish_task(self, deployment_id: str, task: asyncio.Task[None]) -> None:
        self._running.pop(deployment_id, None)
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
    "DEPLOY_TIMEOUT_SEC",
    "DeployProcessResult",
    "DeployRunner",
    "run_deploy_process",
]
