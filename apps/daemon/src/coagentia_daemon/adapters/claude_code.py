"""Claude Code 适配器（契约 E 全文落地；A7 替换 A6 FakeAdapter）。

两层：
- `ClaudeCodeProcess`：**每进程**驱动（base.RuntimeAdapter / E §9）——命令行拼装、asyncio 子进程、
  stdout stream-json 逐行解析 → FrameRouter → 四回调；start/stop/feed/reset_session_args。
- `ClaudeCodeAdapter`：daemon 侧**管理器**（A6 RuntimeAdapter 接口，DaemonClient 不变）——每 Agent
  一个 ClaudeCodeProcess，管会话簿记 / 三档重置 / 崩溃熔断（§4/§5）/ 输入编码（§6）。

子进程用 win32 Proactor loop（cli 已设策略）。`spawn` 可注入 → 单测/冒烟脱离真 claude。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from coagentia_contracts.daemon import (
    AgentBoot,
    DaemonAgentState,
    DiagnosticEventIn,
)
from coagentia_contracts.enums import AgentStatus, Runtime, WakeReason

if TYPE_CHECKING:
    from coagentia_daemon.adapters.codex import CodexProcess

from coagentia_daemon.adapter import AdapterSink
from coagentia_daemon.adapters import cmdline, encoding
from coagentia_daemon.adapters.frames import FrameRouter
from coagentia_daemon.paths import DataPaths
from coagentia_daemon.util import new_ulid, now_iso

# 崩溃拉起退避（§5：1s → 5s → 15s；5 分钟窗 ≥3 次 → 放弃 error）
CRASH_BACKOFF: tuple[float, ...] = (1.0, 5.0, 15.0)
CRASH_WINDOW_SEC = 300.0
CRASH_MAX = 3
_STOP_GRACE_SEC = 5.0  # §5：关 stdin → 等 5s 优雅退出 → terminate/kill
AUTH_RECOVERY_DELAYS: tuple[float, ...] = (0.0, 0.5, 1.0, 2.0)
_AUTH_ERROR_MARKERS = (
    "failed to authenticate",
    "oauth session expired",
    "authentication_error",
)

# 会话续接判据的簿记键（按 runtime）：claude=session_id（--resume），codex=conversation_id
# （thread/resume）。管理器骨架 runtime 无关，仅此处按 runtime 取键（适配器边界内特化，纪律 8）。
_RESUME_KEY: dict[Runtime, str] = {
    Runtime.CLAUDE_CODE: "session_id",
    Runtime.CODEX: "conversation_id",
}


class ProcLike(Protocol):
    """asyncio 子进程的最小接口（可注入桩）。"""

    stdin: Any
    stdout: Any
    returncode: int | None

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    async def wait(self) -> int: ...


SpawnFn = Callable[[list[str], str, dict[str, str]], Awaitable[ProcLike]]


async def _default_spawn(argv: list[str], cwd: str, env: dict[str, str]) -> ProcLike:
    return await asyncio.create_subprocess_exec(  # type: ignore[return-value]
        *argv,
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


class ClaudeCodeProcess:
    """单 Agent 的 claude 子进程驱动（base.RuntimeAdapter / E §9）。"""

    def __init__(
        self,
        agent_member_id: str,
        sink: AdapterSink,
        paths: DataPaths,
        *,
        server_url: str,
        api_key: str,
        spawn: SpawnFn | None = None,
        on_exit: Callable[[str, int | None], Awaitable[None]] | None = None,
        ulid: Callable[[], str] = new_ulid,
        now: Callable[[], str] = now_iso,
    ) -> None:
        self.agent_member_id = agent_member_id
        self._sink = sink
        self._paths = paths
        self._server_url = server_url
        self._api_key = api_key
        self._spawn = spawn or _default_spawn
        self._on_exit = on_exit
        self._now = now
        self.router = FrameRouter(
            agent_member_id,
            sink,
            ulid=ulid,
            now=now,
            on_session=self._persist_session,
        )
        self._proc: ProcLike | None = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self.stderr_tail: deque[str] = deque(maxlen=50)
        self._resume_args: list[str] = []
        self.pid: int | None = None
        self._config_dir: Path | None = None
        self._last_input: str | None = None
        self._auth_retry_used = False

    # -------------------------------------------------------- 会话簿记

    def _persist_session(self, session_id: str) -> None:
        self._paths.write_session(self.agent_member_id, {"session_id": session_id})

    def _resume_session_id(self) -> str | None:
        return self._paths.read_session(self.agent_member_id).get("session_id")

    def reset_session_args(self) -> list[str]:
        """三档重置的会话层命令行差异（§4）：当前进程的 --resume 参数（空 = 新会话）。"""
        return list(self._resume_args)

    # -------------------------------------------------------- 生命周期（E §9）

    async def start(self, boot: AgentBoot, resume: bool) -> None:
        home = self._paths.ensure_agent_home(self.agent_member_id)
        config_dir = Path(cmdline.build_env(str(home))["CLAUDE_CONFIG_DIR"])
        self._config_dir = config_dir
        mcp_path = cmdline.materialize_mcp_config(
            config_dir,
            agent_member_id=self.agent_member_id,
            server_url=self._server_url,
            api_key=self._api_key,
        )
        cmdline.materialize_credentials(config_dir)  # 凭证物化（§2/FR-2.3）
        self._materialize_skills(config_dir, boot.skills)
        resume_id = self._resume_session_id() if resume else None
        self._resume_args = ["--resume", resume_id] if resume_id else []
        argv = cmdline.build_argv(
            boot, mcp_config_path=mcp_path, resume_session_id=resume_id
        )
        env = cmdline.build_env(str(home))
        self.router.reset_run()  # 复位本次 spawn 的运行态（confirmed/turn/phase）
        self._proc = await self._spawn(argv, str(home), env)
        self.pid = getattr(self._proc, "pid", None)
        self._sink.on_diagnostic(
            self._diag(
                "agent.process_started", {"pid": self.pid, "resume": bool(resume_id)}
            )
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        # stderr 必须持续排空：--verbose 会灌满 stderr 管道 → 否则子进程写阻塞死锁。
        if getattr(self._proc, "stderr", None) is not None:
            self._stderr_task = asyncio.create_task(self._drain_stderr())

    def _materialize_skills(self, config_dir: Path, skills: list[str]) -> None:
        """技能白名单物化占位（§2/R6）：白名单外技能不可见。

        M1 仅落地隔离目录 + 白名单清单文件；真实技能复制/链接随技能库落地（open_issue）。
        """
        skills_dir = config_dir / "skills"
        with contextlib.suppress(OSError):
            skills_dir.mkdir(parents=True, exist_ok=True)
            (config_dir / "coagentia-skills.json").write_text(
                _json_dumps({"allowed": list(skills)}), encoding="utf-8"
            )

    async def _read_loop(self) -> None:
        proc = self._proc
        assert proc is not None
        stdout = proc.stdout
        try:
            while True:
                line = await stdout.readline()
                if not line:
                    break
                await self._on_line(line)
        except asyncio.CancelledError:
            raise  # stop() 主动取消 → 不触发退出回调
        except Exception:  # noqa: BLE001 — 读循环内任何异常都不外抛，视作进程终结
            pass
        returncode = await _safe_wait(proc)
        if self._on_exit is not None:
            await self._on_exit(self.agent_member_id, returncode)

    async def _drain_stderr(self) -> None:
        """持续排空 stderr（保留末尾若干行供崩溃诊断），防止管道满导致子进程阻塞。"""
        proc = self._proc
        stderr = getattr(proc, "stderr", None)
        if stderr is None:
            return
        try:
            while True:
                line = await stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace").rstrip()
                if text:
                    self.stderr_tail.append(text)
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            return

    async def _on_line(self, line: bytes | str) -> None:
        text = line.decode("utf-8", "replace") if isinstance(line, bytes) else line
        text = text.strip()
        if not text:
            return
        try:
            frame = _json_loads(text)
        except ValueError:
            self.router.unknown_counts["<non-json>"] = (
                self.router.unknown_counts.get("<non-json>", 0) + 1
            )
            return
        if isinstance(frame, dict):
            await self.router.process(frame)
            if self._is_auth_failure(frame):
                await self._retry_after_auth_failure()

    async def feed(self, text: str) -> None:
        """写入一个 turn 的输入（§6.4：写 stdin 即 ack）。

        text = 管理器渲染的**运行时无关正文**（encoding.render_*，纪律 8）；载体（stream-json
        user 帧封装）是 claude 侧特化，落在本 Process（区别于 codex 的 turn/start input）。
        """
        if self._config_dir is not None:
            cmdline.materialize_credentials(self._config_dir)
        self._last_input = text
        self._auth_retry_used = False
        await self._write_input(text)

    async def _write_input(self, text: str) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise RuntimeError("process not running")
        self.router.begin_turn()  # 抑制随后的 init→idle 误报（init 帧在首输入后到）
        data = (encoding.user_frame_line(text) + "\n").encode("utf-8")  # 载体封装（claude 特化）
        proc.stdin.write(data)
        drain = getattr(proc.stdin, "drain", None)
        if drain is not None:
            await drain()

    @staticmethod
    def _is_auth_failure(frame: dict[str, Any]) -> bool:
        if frame.get("type") != "result":
            return False
        if not (frame.get("is_error") or frame.get("api_error_status")):
            return False
        text = _json_dumps(frame).casefold()
        return any(marker in text for marker in _AUTH_ERROR_MARKERS)

    async def _retry_after_auth_failure(self) -> None:
        """等待并吸收其他 Agent 刷新的 OAuth 凭证，然后把失败 turn 自动重投一次。"""
        if self._auth_retry_used or self._last_input is None or self._config_dir is None:
            return
        self._auth_retry_used = True
        for delay in AUTH_RECOVERY_DELAYS:
            await asyncio.sleep(delay)
            if cmdline.materialize_credentials(self._config_dir):
                await self._write_input(self._last_input)
                return

    async def stop(self) -> None:
        """关 stdin → 等 5s 优雅退出 → terminate → kill（§5）。"""
        proc = self._proc
        if proc is None:
            return
        with contextlib.suppress(Exception):
            if proc.stdin is not None:
                proc.stdin.close()
        try:
            await asyncio.wait_for(proc.wait(), timeout=_STOP_GRACE_SEC)
        except (TimeoutError, Exception):  # noqa: BLE001
            with contextlib.suppress(Exception):
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except (TimeoutError, Exception):  # noqa: BLE001
                with contextlib.suppress(Exception):
                    proc.kill()
        for task in (self._reader_task, self._stderr_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._proc = None

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    def set_turn_context(self, channel_id: str | None, thread_root_id: str | None) -> None:
        self.router.set_turn_context(channel_id, thread_root_id)

    def _diag(self, dtype: str, payload: dict[str, Any]) -> DiagnosticEventIn:
        return DiagnosticEventIn(
            agent_member_id=self.agent_member_id, type=dtype, payload=payload, at=self._now()
        )


# ============================================================ 管理器（A6 接口）


class _AgentEntry:
    __slots__ = (
        "boot",
        "process",
        "status",
        "last_delivered",
        "stopping",
        "resume_used",
        "reached_idle",
        "crash_times",
        "restart_task",
    )

    def __init__(self, boot: AgentBoot, process: ClaudeCodeProcess | CodexProcess) -> None:
        self.boot = boot
        self.process = process
        self.status = AgentStatus.STARTING
        # 去重游标**按 channel_id 维度**：不同频道各自"已喂过的最大 message_id"，避免频道 A 的较大
        # message_id 误压制频道 B 较早消息的投递（契约 D §5.2；跨频道乱序丢消息 #2）。
        self.last_delivered: dict[str, str] = {}
        self.stopping = False
        self.resume_used = False
        self.reached_idle = False
        self.crash_times: deque[float] = deque()
        self.restart_task: asyncio.Task | None = None


class _AgentSink:
    """每 Agent 的 sink 代理：透传真 sink + 记 status（进程表）/ reached_idle（降级判定）。"""

    def __init__(self, entry: _AgentEntry, real: AdapterSink) -> None:
        self._entry = entry
        self._real = real

    async def on_status_changed(
        self, agent_member_id: str, status: AgentStatus, error_detail: str | None = None
    ) -> None:
        self._entry.status = status
        if status == AgentStatus.IDLE:
            self._entry.reached_idle = True
        await self._real.on_status_changed(agent_member_id, status, error_detail)

    async def on_activity(self, agent_member_id: str, detail: str) -> None:
        await self._real.on_activity(agent_member_id, detail)

    def on_usage(self, event: Any) -> None:
        self._real.on_usage(event)

    def on_diagnostic(self, event: Any) -> None:
        self._real.on_diagnostic(event)


class RuntimeManager:
    """daemon 侧 runtime 管理器（A6 RuntimeAdapter 接口；DaemonClient / handlers 不变）。

    按 `boot.runtime` 分派进程类（ClaudeCodeProcess / CodexProcess，E2 §1）——会话簿记 / 三档
    重置 / 崩溃熔断 / 去重游标骨架 runtime 无关原样复用。历史名 `ClaudeCodeAdapter` 保留为别名。
    """

    def __init__(
        self,
        paths: DataPaths,
        *,
        server_url: str,
        api_key: str,
        spawn: SpawnFn | None = None,
        ulid: Callable[[], str] = new_ulid,
        now: Callable[[], str] = now_iso,
    ) -> None:
        self.paths = paths
        self._server_url = server_url
        self._api_key = api_key
        self._spawn = spawn
        self._ulid = ulid
        self._now = now
        self._sink: AdapterSink | None = None
        self._agents: dict[str, _AgentEntry] = {}

    def bind(self, sink: AdapterSink) -> None:
        self._sink = sink

    # -------------------------------------------------------- 生命周期

    async def start(self, boot: AgentBoot) -> bool:
        aid = boot.agent_member_id
        existing = self._agents.get(aid)
        if existing is not None and existing.process.is_running():
            return False  # 已在跑 → noop（自然键幂等）
        resume = self._has_resumable_session(boot)
        await self._launch(boot, resume=resume)
        return True

    def _has_resumable_session(self, boot: AgentBoot) -> bool:
        key = _RESUME_KEY.get(boot.runtime, "session_id")
        return bool(self.paths.read_session(boot.agent_member_id).get(key))

    def _new_process(
        self, aid: str, runtime: Runtime
    ) -> ClaudeCodeProcess | CodexProcess:
        """按 runtime 分派进程类（E2 §1；codex 惰性 import 避免模块级循环依赖）。"""
        if runtime == Runtime.CODEX:
            from coagentia_daemon.adapters.codex import CodexProcess

            cls: type = CodexProcess
        else:
            cls = ClaudeCodeProcess
        return cls(
            aid,
            _placeholder_sink(),  # 占位，_launch 内即刻替换为 _AgentSink
            self.paths,
            server_url=self._server_url,
            api_key=self._api_key,
            spawn=self._spawn,
            on_exit=self._on_process_exit,
            ulid=self._ulid,
            now=self._now,
        )

    async def _launch(self, boot: AgentBoot, *, resume: bool) -> _AgentEntry:
        aid = boot.agent_member_id
        entry = self._agents.get(aid)
        if entry is None:
            process = self._new_process(aid, boot.runtime)
            entry = _AgentEntry(boot, process)
            self._agents[aid] = entry
        entry.boot = boot
        entry.stopping = False
        entry.reached_idle = False
        entry.resume_used = resume
        # 绑定每 Agent sink（含 status 记录）
        entry.process._sink = _AgentSink(entry, self._require_sink())
        entry.process.router._sink = entry.process._sink
        await self._emit(entry, AgentStatus.STARTING)
        await entry.process.start(boot, resume=resume)
        # 就绪 idle（§5）：spawn 成功即可接收输入（stdin 缓冲）。实测本 CLI 的 init 帧在
        # 首个 stdin 输入后才到（E §11.3），就绪解耦于 init；会话确认另由 router.confirmed 记。
        await self._emit(entry, AgentStatus.IDLE)
        return entry

    async def stop(self, agent_member_id: str) -> bool:
        entry = self._agents.pop(agent_member_id, None)
        if entry is None:
            return False
        entry.stopping = True
        if entry.restart_task is not None:
            entry.restart_task.cancel()
        await entry.process.stop()
        await self._emit(entry, AgentStatus.OFFLINE)
        return True

    async def restart(self, boot: AgentBoot) -> None:
        # 一档：保 session 保 Home。
        await self._respawn(boot, resume=True)

    async def reset_session(self, boot: AgentBoot) -> None:
        # 二档：新会话（清 session 簿记，保 Home）。
        self.paths.clear_session(boot.agent_member_id)
        await self._respawn(boot, resume=False)

    async def reset_full(self, boot: AgentBoot) -> None:
        # 三档：Home 已由 handler 清空；清 session + 新会话。
        self.paths.clear_session(boot.agent_member_id)
        await self._respawn(boot, resume=False)

    async def _respawn(self, boot: AgentBoot, *, resume: bool) -> None:
        aid = boot.agent_member_id
        entry = self._agents.get(aid)
        if entry is not None:
            entry.stopping = True
            if entry.restart_task is not None:
                entry.restart_task.cancel()
            await entry.process.stop()
        await self._launch(boot, resume=resume)

    async def wake(self, agent_member_id: str, reason: WakeReason, refs: Any) -> bool:
        entry = self._agents.get(agent_member_id)
        if entry is None or entry.status == AgentStatus.BUSY:
            return False  # 未在跑 / 已清醒 → noop（deliver 照常）
        await self._emit(entry, AgentStatus.BUSY)
        return True

    async def deliver(
        self,
        agent_member_id: str,
        channel_id: str,
        messages: list[dict[str, Any]],
        thread_root_id: str | None,
    ) -> bool:
        entry = self._agents.get(agent_member_id)
        if entry is None or not messages:
            return False
        max_id = max(m["id"] for m in messages)
        prev = entry.last_delivered.get(channel_id)
        if prev is not None and max_id <= prev:
            return False  # 该频道已喂过的最大 message_id → noop 去重（§5.2，按频道）
        entry.last_delivered[channel_id] = max_id
        entry.process.set_turn_context(channel_id, thread_root_id)
        await self._emit(entry, AgentStatus.BUSY)
        # 渲染运行时无关正文（纪律 8）；载体封装归各 Process（claude / codex 各自特化）。
        await entry.process.feed(
            encoding.render_deliver(messages, thread_root_id=thread_root_id)
        )
        return True

    async def inject(
        self, agent_member_id: str, body: str, source: dict[str, Any], diagnostic_type: str
    ) -> None:
        entry = self._agents.get(agent_member_id)
        if entry is None:
            return
        self._require_sink().on_diagnostic(
            DiagnosticEventIn(
                agent_member_id=agent_member_id,
                type=diagnostic_type,
                payload={"direction": "sent", "source": source},
                at=self._now(),
            )
        )
        entry.process.set_turn_context(None, None)
        await self._emit(entry, AgentStatus.BUSY)
        await entry.process.feed(encoding.render_inject(body, source))

    # -------------------------------------------------------- 进程表 / Home

    def process_table(self) -> list[DaemonAgentState]:
        return [
            DaemonAgentState(
                agent_member_id=aid,
                status=e.status,
                source_session=e.process.router.session_id,
            )
            for aid, e in sorted(self._agents.items())
        ]

    def home_path(self, agent_member_id: str) -> str | None:
        entry = self._agents.get(agent_member_id)
        return entry.boot.home_path if entry else None

    # -------------------------------------------------------- 崩溃熔断（§5）

    async def _on_process_exit(self, agent_member_id: str, returncode: int | None) -> None:
        entry = self._agents.get(agent_member_id)
        if entry is None:
            return
        self._require_sink().on_diagnostic(
            DiagnosticEventIn(
                agent_member_id=agent_member_id,
                type="agent.process_exited",
                payload={"exit_code": returncode},
                at=self._now(),
            )
        )
        if entry.stopping:
            return  # 主动 stop/reset → 不拉起
        entry.restart_task = asyncio.create_task(self._supervise_restart(entry, returncode))

    async def _supervise_restart(self, entry: _AgentEntry, returncode: int | None) -> None:
        aid = entry.boot.agent_member_id
        loop = asyncio.get_running_loop()
        now = loop.time()
        entry.crash_times.append(now)
        while entry.crash_times and now - entry.crash_times[0] > CRASH_WINDOW_SEC:
            entry.crash_times.popleft()
        attempt = len(entry.crash_times)
        if attempt > CRASH_MAX:
            await self._emit(entry, AgentStatus.ERROR, "crash_loop_giveup")
            return
        delay = CRASH_BACKOFF[min(attempt - 1, len(CRASH_BACKOFF) - 1)]
        # resume 损坏降级：用了 resume 却从未确认会话（无 init/result）→ 冷启 + session_lost（§4）
        resume = True
        if entry.resume_used and not entry.process.router.confirmed:
            self._require_sink().on_diagnostic(
                DiagnosticEventIn(
                    agent_member_id=aid,
                    type="agent.session_lost",
                    payload={"attempt": attempt},
                    at=self._now(),
                )
            )
            self.paths.clear_session(aid)
            resume = False
        self._require_sink().on_diagnostic(
            DiagnosticEventIn(
                agent_member_id=aid,
                type="agent.crash_restarted",
                payload={"attempt": attempt, "backoff_sec": delay, "exit_code": returncode},
                at=self._now(),
            )
        )
        await asyncio.sleep(delay)
        if entry.stopping or self._agents.get(aid) is not entry:
            return
        await self._launch(entry.boot, resume=resume)

    # -------------------------------------------------------- 底座

    def _require_sink(self) -> AdapterSink:
        if self._sink is None:
            raise RuntimeError("adapter not bound to sink")
        return self._sink

    async def _emit(
        self, entry: _AgentEntry, status: AgentStatus, error_detail: str | None = None
    ) -> None:
        entry.status = status
        if status == AgentStatus.IDLE:
            entry.reached_idle = True
        await self._require_sink().on_status_changed(
            entry.boot.agent_member_id, status, error_detail
        )


# 历史名向后兼容（cli / 测试曾以 ClaudeCodeAdapter 引用管理器；M5 泛化为 RuntimeManager）。
ClaudeCodeAdapter = RuntimeManager


# ------------------------------------------------------------ 小工具


def _placeholder_sink() -> AdapterSink:
    return _NullSink()


class _NullSink:
    async def on_status_changed(self, *a: Any, **k: Any) -> None: ...
    async def on_activity(self, *a: Any, **k: Any) -> None: ...
    def on_usage(self, *a: Any, **k: Any) -> None: ...
    def on_diagnostic(self, *a: Any, **k: Any) -> None: ...


async def _safe_wait(proc: ProcLike) -> int | None:
    with contextlib.suppress(Exception):
        return await proc.wait()
    return proc.returncode


def _json_loads(text: str) -> Any:
    import json

    return json.loads(text)


def _json_dumps(obj: Any) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)
