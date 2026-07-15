"""Codex 适配器（契约 E2 全文落地；第二 runtime，与 claude_code 双实现同构）。

- `CodexFrameRouter`：codex app-server **JSON-RPC 通知** → 四类回调映射（防腐层 / 相位聚合 /
  usage 提取 / 诊断映射，E2 §5）。纯逻辑、无子进程依赖——可用桩帧全量单测。
- `CodexProcess`：**每进程**驱动（base.RuntimeAdapter / E §9）——`codex app-server` 长驻子进程、
  JSON-RPC 逐行读写、握手（initialize→initialized→thread/start|resume）、turn/start 提交、
  ServerRequest 审批自动应答、CODEX_HOME 隔离 + config.toml MCP 注入。start/stop/feed/
  reset_session_args。

管理器（`RuntimeManager`，claude_code.py）按 `boot.runtime` 分派本类 / `ClaudeCodeProcess`——
会话簿记 / 三档重置 / 崩溃熔断 / 去重游标骨架 runtime 无关原样复用（E2 §1.1）。

**帧名权威 = 0.144.0 实测校准**（CODEX-CALIBRATION）；冻结的是映射语义与不变量，非方法名。
相位文案单源自 claude `frames.py`（ACTIVITY_PHRASES 值域，runtime 无关）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections import deque
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from coagentia_contracts.daemon import AgentBoot, DiagnosticEventIn, TokenUsageEventIn
from coagentia_contracts.enums import AgentStatus

from coagentia_daemon import __version__
from coagentia_daemon.adapter import AdapterSink
from coagentia_daemon.adapters import cmdline, codex_cmdline
from coagentia_daemon.adapters.claude_code import ProcLike, SpawnFn, _safe_wait
from coagentia_daemon.adapters.frames import (
    _P_BROWSING,
    _P_COMMAND,
    _P_READING,
    _P_REPLYING,
    _P_SUBAGENT,
    _P_THINKING,
    _P_USING,
    _P_WRITING,
    _short_tool,
)
from coagentia_daemon.paths import DataPaths
from coagentia_daemon.util import new_ulid, now_iso

_HANDSHAKE_TIMEOUT = 60.0  # initialize / thread.* 应答上限（慢启不误杀，坏进程走熔断）
_STOP_GRACE_SEC = 5.0  # 杀树后等待退出上限

# ThreadItem.type → activity 相位（E2 §5；文案值域 = claude frames 单源）。
_ITEM_PHASE: dict[str, str] = {
    "reasoning": _P_THINKING,
    "plan": _P_THINKING,
    "agentMessage": _P_REPLYING,
    "commandExecution": _P_COMMAND,
    "fileChange": _P_WRITING,
    "webSearch": _P_BROWSING,
    "subAgentActivity": _P_SUBAGENT,
    # imageView 等只读类归 Reading（本地工作相位聚合，不逐帧）
    "imageView": _P_READING,
}

# 增量通知 method → 相位（delta 不逐帧上报，相位聚合 ≤6/turn；E2 §5）。
_DELTA_PHASE: dict[str, str] = {
    "item/agentMessage/delta": _P_REPLYING,
    "item/plan/delta": _P_THINKING,
    "item/reasoning/textDelta": _P_THINKING,
    "item/reasoning/summaryTextDelta": _P_THINKING,
    "item/commandExecution/outputDelta": _P_COMMAND,
    "item/fileChange/outputDelta": _P_WRITING,
    "item/fileChange/patchUpdated": _P_WRITING,
}

# ServerRequest（server→client，需回应）自动批准载荷（NFR5 无交互式审批；CALIBRATION §6）。
# 即使 approvalPolicy=never 仍可能来 → 保守放行；未知 ServerRequest 回 JSON-RPC error（无法伪造
# 合法 approval 载荷，保守拒绝好过挂死）。
_APPROVAL_RESULTS: dict[str, dict[str, Any]] = {
    "execCommandApproval": {"decision": "approved"},  # ReviewDecision
    "applyPatchApproval": {"decision": "approved"},
    "item/commandExecution/requestApproval": {"decision": "accept"},  # *ApprovalDecision
    "item/fileChange/requestApproval": {"decision": "accept"},
}


class CodexRpcError(Exception):
    """JSON-RPC error 响应（握手请求失败 → 触发熔断降级）。"""

    def __init__(self, error: Any) -> None:
        super().__init__(str(error))
        self.error = error


def _error_detail(err: Any) -> str:
    """TurnError / CodexErrorInfo → 简短 error_detail 字符串。"""
    if isinstance(err, str):
        return err
    if isinstance(err, dict):
        for key in ("code", "message", "type", "codexErrorInfo"):
            val = err.get(key)
            if isinstance(val, str) and val:
                return val
            if isinstance(val, dict):
                return _error_detail(val)
        return next((k for k in err), "codex_error")
    return "codex_error"


class CodexFrameRouter:
    """单 Agent 的 codex JSON-RPC 通知路由器 → AdapterSink 四回调（E2 §5）。

    只处理**通知**（method + params，无 id）；响应与 ServerRequest 由 `CodexProcess` 前置处理。
    session_id = conversationId(thread.id)——与 claude router 同名字段，管理器骨架原样复用。
    """

    def __init__(
        self,
        agent_member_id: str,
        sink: AdapterSink,
        *,
        ulid: Callable[[], str] = new_ulid,
        now: Callable[[], str] = now_iso,
        on_session: Callable[[str], None] | None = None,
        on_turn_end: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.agent_member_id = agent_member_id
        self._sink = sink
        self._ulid = ulid
        self._now = now
        self._on_session = on_session
        self._on_turn_end = on_turn_end
        # 会话簿记（session_id = conversationId）
        self.session_id: str | None = None
        # 相位聚合
        self._phase: str | None = None
        # 会话已确认（thread 就绪）——resume 是否生效的判据（管理器降级用）。
        self.confirmed = False
        # 本 turn 最近一次 token 用量增量（turn/completed 时提取恰一条，防多次 update 重复计）。
        self._last_usage: dict[str, Any] | None = None
        # 防腐层计数（lifetime）
        self.unknown_counts: dict[str, int] = {}
        # turn 归属提示
        self.channel_id: str | None = None
        self.thread_root_id: str | None = None
        self.last_status: AgentStatus | None = None

    # -------------------------------------------------------- 管理器接口（claude router 同构）

    def set_turn_context(self, channel_id: str | None, thread_root_id: str | None) -> None:
        self.channel_id = channel_id
        self.thread_root_id = thread_root_id

    def reset_phase(self) -> None:
        self._phase = None

    def begin_turn(self) -> None:
        """提交 turn 前调用（接口对齐 claude router；codex 无 init→idle 竞态需抑制）。"""
        self._last_usage = None

    def reset_run(self) -> None:
        """一次 spawn 的运行态复位（confirmed/phase/usage；session_id 保留）。"""
        self.confirmed = False
        self._phase = None
        self._last_usage = None

    def set_conversation(self, conversation_id: str | None) -> None:
        """thread/started（响应或通知）→ 记 conversationId + 确认就绪。"""
        if conversation_id and conversation_id != self.session_id:
            self.session_id = conversation_id
            if self._on_session is not None:
                self._on_session(conversation_id)
        self.confirmed = True

    # -------------------------------------------------------- 分发（防腐：任何畸形帧不外抛）

    async def process(self, frame: dict[str, Any]) -> None:
        method = frame.get("method")
        params = frame.get("params")
        if not isinstance(params, dict):
            params = {}
        if method == "thread/started":
            self._on_thread_started(params)
        elif method == "turn/started":
            await self._status(AgentStatus.BUSY)
        elif method == "turn/completed":
            await self._on_turn_completed(params)
        elif method == "item/started":
            await self._on_item_started(params)
        elif method == "item/completed":
            await self._on_item_completed(params)
        elif method in _DELTA_PHASE:
            await self._switch_phase(_DELTA_PHASE[method])
        elif method == "thread/tokenUsage/updated":
            self._on_token_usage(params)
        elif method == "error":
            await self._on_error(params)
        elif method in _IGNORED_NOTIFICATIONS:
            return  # 契约内已知但无映射的生命周期噪声 → 静默忽略（不计防腐）
        else:
            self._count_unknown(str(method))

    # -------------------------------------------------------- thread / turn

    def _on_thread_started(self, params: dict[str, Any]) -> None:
        thread = params.get("thread")
        cid = thread.get("id") if isinstance(thread, dict) else None
        self.set_conversation(cid)

    async def _on_turn_completed(self, params: dict[str, Any]) -> None:
        turn = params.get("turn")
        turn = turn if isinstance(turn, dict) else {}
        self._emit_usage()  # 本 turn 提取恰一条 usage（若有增量）
        self.reset_phase()
        self.confirmed = True
        status = turn.get("status")
        if status == "failed":
            await self._status(AgentStatus.ERROR, error_detail=_error_detail(turn.get("error")))
        else:
            await self._status(AgentStatus.IDLE)  # completed / interrupted
        await self._release_turn()

    async def _on_error(self, params: dict[str, Any]) -> None:
        if params.get("willRetry"):
            return  # 瞬态：codex 内部重试，turn 未终结
        await self._status(AgentStatus.ERROR, error_detail=_error_detail(params.get("error")))
        self.reset_phase()
        await self._release_turn()

    async def _release_turn(self) -> None:
        if self._on_turn_end is not None:
            await self._on_turn_end()

    # -------------------------------------------------------- item（相位 + 诊断）

    async def _on_item_started(self, params: dict[str, Any]) -> None:
        item = params.get("item")
        if not isinstance(item, dict):
            return
        phase = self._phase_for_item(item)
        if phase is not None:
            await self._switch_phase(phase)

    async def _on_item_completed(self, params: dict[str, Any]) -> None:
        item = params.get("item")
        if not isinstance(item, dict):
            return
        itype = item.get("type")
        if itype == "commandExecution":
            exit_code = item.get("exitCode")
            is_error = bool(item.get("status") == "failed") or (
                exit_code is not None and int(exit_code or 0) != 0
            )
            self._diag(
                "agent.command",
                {"command": _command_text(item.get("command")), "is_error": is_error},
            )
        elif itype == "fileChange":
            self._diag(
                "agent.file_edit",
                {
                    "path": _first_change_path(item.get("changes")),
                    "kind": "edit",
                    "is_error": bool(item.get("status") == "failed"),
                },
            )
        elif itype in ("mcpToolCall", "dynamicToolCall"):
            self._diag(
                "agent.tool_call",
                {"tool": _tool_label(item), "ok": item.get("status") != "failed"},
            )

    def _phase_for_item(self, item: dict[str, Any]) -> str | None:
        itype = item.get("type")
        if itype in ("mcpToolCall", "dynamicToolCall"):
            return _P_USING.format(tool=_tool_label(item))
        return _ITEM_PHASE.get(str(itype))

    async def _switch_phase(self, phase: str) -> None:
        if phase == self._phase:
            return  # 同相位 delta 不上报（聚合，§5）
        self._phase = phase
        await self._sink.on_activity(self.agent_member_id, phase)

    # -------------------------------------------------------- usage（E2 §7）

    def _on_token_usage(self, params: dict[str, Any]) -> None:
        usage = params.get("tokenUsage")
        last = usage.get("last") if isinstance(usage, dict) else None
        if isinstance(last, dict):
            self._last_usage = last  # 缓存本 turn 最新增量，turn/completed 时提取恰一条

    def _emit_usage(self) -> None:
        last = self._last_usage
        if not isinstance(last, dict):
            return
        self._last_usage = None
        self._sink.on_usage(
            TokenUsageEventIn(
                id=self._ulid(),  # 适配器 ULID（exactly-once 去重根基）
                agent_member_id=self.agent_member_id,
                channel_id=self.channel_id,
                thread_root_id=self.thread_root_id,
                input_tokens=int(last.get("inputTokens", 0) or 0),
                output_tokens=int(last.get("outputTokens", 0) or 0),
                cache_read_tokens=int(last.get("cachedInputTokens", 0) or 0),
                cache_write_tokens=0,  # codex 无独立 cache creation 字段（E2 §7）
                source_session=self.session_id,  # conversationId
                reported_at=self._now(),
            )
        )

    # -------------------------------------------------------- 防腐 / 回调底座

    def _count_unknown(self, key: str) -> None:
        seen = self.unknown_counts.get(key, 0)
        self.unknown_counts[key] = seen + 1
        if seen == 0:  # 每种未知类型首现一条低频诊断，后续静默累加
            self._diag("agent.unknown_frame", {"type": key, "count": 1})

    async def _status(self, status: AgentStatus, error_detail: str | None = None) -> None:
        self.last_status = status
        await self._sink.on_status_changed(self.agent_member_id, status, error_detail)

    def _diag(self, dtype: str, payload: dict[str, Any]) -> None:
        self._sink.on_diagnostic(
            DiagnosticEventIn(
                agent_member_id=self.agent_member_id,
                type=dtype,
                channel_id=self.channel_id,
                payload=payload,
                at=self._now(),
            )
        )


# 契约内已知但无回调映射的生命周期通知（静默忽略，不计防腐——避免例行帧刷屏计数）。
_IGNORED_NOTIFICATIONS: frozenset[str] = frozenset(
    {
        "thread/status/changed",
        "thread/name/updated",
        "thread/goal/updated",
        "thread/goal/cleared",
        "thread/settings/updated",
        "turn/diff/updated",
        "turn/plan/updated",
        "item/commandExecution/terminalInteraction",
        "item/mcpToolCall/progress",
        "item/reasoning/summaryPartAdded",
        "mcpServer/startupStatus/updated",
        "mcpServer/oauthLogin/completed",
        "serverRequest/resolved",
        "account/updated",
        "account/rateLimits/updated",
        "remoteControl/status/changed",  # 实测 0.144.0 握手/idle 期例行发（非 turn 相关噪声）
        "thread/compacted",
        "warning",
    }
)


def _command_text(command: Any) -> str:
    """commandExecution.command（string 或 array）→ 截断预览。"""
    if isinstance(command, list):
        command = " ".join(str(c) for c in command)
    return str(command or "")[:500]


def _first_change_path(changes: Any) -> str | None:
    if isinstance(changes, list) and changes:
        first = changes[0]
        if isinstance(first, dict):
            return first.get("path")
    if isinstance(changes, dict):
        return next(iter(changes), None)
    return None


def _tool_label(item: dict[str, Any]) -> str:
    """mcpToolCall/dynamicToolCall item → 可读工具名（去 mcp__ 前缀，同 claude 文案）。"""
    return _short_tool(str(item.get("tool") or "tool"))


class CodexProcess:
    """单 Agent 的 codex app-server 子进程驱动（base.RuntimeAdapter / E §9）。"""

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
        self._spawn = spawn or _default_codex_spawn
        self._on_exit = on_exit
        self._now = now
        self.router = CodexFrameRouter(
            agent_member_id,
            sink,
            ulid=ulid,
            now=now,
            on_session=self._persist_session,
            on_turn_end=self._on_turn_finished,
        )
        self._proc: ProcLike | None = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._handshake_task: asyncio.Task | None = None
        self.stderr_tail: deque[str] = deque(maxlen=50)
        self.pid: int | None = None
        self._codex_home: Path | None = None
        # JSON-RPC 请求簿记
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 1
        # turn 串行队列（app-server 并发未定 → 内部串行提交兜底，E2 §4）
        self._thread_id: str | None = None
        self._turn_queue: deque[tuple[str, str | None, str | None]] = deque()
        self._turn_in_flight = False

    # -------------------------------------------------------- 会话簿记（conversation_id）

    def _persist_session(self, conversation_id: str) -> None:
        # 与 claude session_id 并存不互污（同文件同布局，E2 §3）。
        data = self._paths.read_session(self.agent_member_id)
        data["conversation_id"] = conversation_id
        self._paths.write_session(self.agent_member_id, data)

    def _resume_conversation_id(self) -> str | None:
        return self._paths.read_session(self.agent_member_id).get("conversation_id")

    def reset_session_args(self) -> list[str]:
        """codex 无 argv 级会话差异（resume 走 thread/resume 方法，非命令行）——恒空。"""
        return []

    # -------------------------------------------------------- 生命周期（E §9）

    async def start(self, boot: AgentBoot, resume: bool) -> None:
        home = self._paths.ensure_agent_home(self.agent_member_id)
        codex_home = codex_cmdline.isolated_codex_home(str(home))
        self._codex_home = codex_home
        codex_cmdline.materialize_credentials(codex_home)  # ChatGPT 登录态物化（E2 §2.2）
        codex_cmdline.materialize_config(
            codex_home,
            agent_member_id=self.agent_member_id,
            server_url=self._server_url,
            api_key=self._api_key,
        )
        # 复位本次 spawn 运行态
        self._reset_run_state()
        argv = codex_cmdline.build_app_server_argv()
        env = codex_cmdline.build_env(str(home))
        self._proc = await self._spawn(argv, str(home), env)
        self.pid = getattr(self._proc, "pid", None)
        self._sink.on_diagnostic(
            self._diag("agent.process_started", {"pid": self.pid, "resume": bool(resume)})
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        if getattr(self._proc, "stderr", None) is not None:
            self._stderr_task = asyncio.create_task(self._drain_stderr())
        # 握手异步进行（不阻塞 start）：initialize→initialized→thread/start|resume。
        # 就绪 idle 由管理器在 start 返回后发（同 claude），confirmed 由 thread 就绪置位。
        self._handshake_task = asyncio.create_task(self._handshake(boot, resume))

    def _reset_run_state(self) -> None:
        self.router.reset_run()
        self._pending.clear()
        self._next_id = 1
        self._thread_id = None
        self._turn_queue.clear()
        self._turn_in_flight = False

    async def _handshake(self, boot: AgentBoot, resume: bool) -> None:
        try:
            await self._request(
                "initialize",
                {"clientInfo": {"name": "coagentia", "version": __version__}},
            )
            await self._notify("initialized")
            cid = self._resume_conversation_id() if resume else None
            opts = self._thread_opts(boot)
            if cid:
                resp = await self._request("thread/resume", {"threadId": cid, **opts})
            else:
                resp = await self._request("thread/start", opts)
            thread = resp.get("thread") if isinstance(resp, dict) else None
            tid = thread.get("id") if isinstance(thread, dict) else None
            if tid:
                self.router.set_conversation(tid)
                self._thread_id = tid
                await self._maybe_submit_next_turn()  # 排空握手前排队的投递
            else:
                await self._abort_process()  # 无 thread → 视作握手失败，走熔断
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — 握手任何失败 → 杀进程触发 on_exit + 熔断降级
            await self._abort_process()

    def _thread_opts(self, boot: AgentBoot) -> dict[str, Any]:
        """thread/start|resume 公共参数（NFR5 权限姿态 + 身份注入，E2 §1.3/§2.4）。"""
        return {
            "cwd": boot.home_path,
            "sandbox": "danger-full-access",
            "approvalPolicy": "never",
            "model": boot.model,
            # 身份注入文案 = claude --append-system-prompt 同源（contracts 单点，E2 §2.4）。
            "developerInstructions": cmdline.build_identity_prompt(boot),
        }

    async def _abort_process(self) -> None:
        proc = self._proc
        if proc is None:
            return
        with contextlib.suppress(Exception):
            proc.kill()  # → 读循环 EOF → on_exit → 熔断降级（resume 未确认则 session_lost 冷启）

    # -------------------------------------------------------- 读循环 / JSON-RPC

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
        except Exception:  # noqa: BLE001 — 读循环内异常视作进程终结
            pass
        self._fail_pending()
        returncode = await _safe_wait(proc)
        if self._on_exit is not None:
            await self._on_exit(self.agent_member_id, returncode)

    async def _drain_stderr(self) -> None:
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
            frame = json.loads(text)
        except ValueError:
            self.router._count_unknown("<non-json>")
            return
        if not isinstance(frame, dict):
            self.router._count_unknown("<non-dict>")
            return
        if "method" in frame:
            if frame.get("id") is not None:
                await self._handle_server_request(frame)  # id + method = ServerRequest
            else:
                await self.router.process(frame)  # 通知
        elif frame.get("id") is not None:
            self._resolve_response(frame)  # id + result/error = 请求响应
        else:
            self.router._count_unknown("<no-method-no-id>")

    def _resolve_response(self, frame: dict[str, Any]) -> None:
        rid = frame.get("id")
        fut = self._pending.get(rid) if isinstance(rid, int) else None
        if fut is None or fut.done():
            return  # 无主响应（如 turn/start 的 fire-and-forget）→ 忽略
        if "error" in frame:
            fut.set_exception(CodexRpcError(frame.get("error")))
        else:
            result = frame.get("result")
            fut.set_result(result if isinstance(result, dict) else {})

    async def _handle_server_request(self, frame: dict[str, Any]) -> None:
        rid = frame.get("id")
        method = str(frame.get("method"))
        result = _APPROVAL_RESULTS.get(method)
        if result is not None:
            await self._write_message({"id": rid, "result": result})
            return
        # 未知 ServerRequest：无法伪造合法 approval 载荷 → 保守回 error（好过挂死）。
        await self._write_message(
            {"id": rid, "error": {"code": -32601, "message": "unsupported server request"}}
        )
        self.router._count_unknown(f"serverRequest/{method}")

    def _fail_pending(self) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(CodexRpcError("process exited"))
        self._pending.clear()

    async def _request(
        self, method: str, params: dict[str, Any], *, timeout: float = _HANDSHAKE_TIMEOUT
    ) -> dict[str, Any]:
        rid = self._next_id
        self._next_id += 1
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            await self._write_message({"id": rid, "method": method, "params": params})
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(rid, None)

    async def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        msg: dict[str, Any] = {"method": method}
        if params is not None:
            msg["params"] = params
        await self._write_message(msg)

    async def _write_message(self, obj: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise RuntimeError("process not running")
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        proc.stdin.write(data)
        drain = getattr(proc.stdin, "drain", None)
        if drain is not None:
            await drain()

    # -------------------------------------------------------- turn 提交（E §6 / E2 §4）

    async def feed(self, text: str) -> None:
        """写入一个 turn（§6.4：发出即 ack）。thread 未就绪则入队，握手后排空。"""
        if self._codex_home is not None:
            codex_cmdline.materialize_credentials(self._codex_home)  # 投递前刷新凭证自愈
        self._turn_queue.append((text, self.router.channel_id, self.router.thread_root_id))
        await self._maybe_submit_next_turn()

    async def _maybe_submit_next_turn(self) -> None:
        if self._thread_id is None or self._turn_in_flight or not self._turn_queue:
            return
        text, channel_id, thread_root_id = self._turn_queue.popleft()
        self.router.set_turn_context(channel_id, thread_root_id)
        self._turn_in_flight = True
        self.router.begin_turn()
        # turn/start 是 fire-and-forget 请求：响应可能延至 turn 结束，ack=发出即 ack（E2 §4）。
        rid = self._next_id
        self._next_id += 1
        await self._write_message(
            {
                "id": rid,
                "method": "turn/start",
                "params": {
                    "threadId": self._thread_id,
                    "input": [{"type": "text", "text": text}],
                },
            }
        )

    async def _on_turn_finished(self) -> None:
        self._turn_in_flight = False
        await self._maybe_submit_next_turn()

    def set_turn_context(self, channel_id: str | None, thread_root_id: str | None) -> None:
        self.router.set_turn_context(channel_id, thread_root_id)

    # -------------------------------------------------------- stop

    async def stop(self) -> None:
        """关 stdin → 杀进程树（win32 taskkill /F /T；app-server 不响应 stdin 关闭，E2 §1.2）。"""
        proc = self._proc
        if proc is None:
            return
        for task in (self._handshake_task, self._reader_task, self._stderr_task):
            if task is not None:
                task.cancel()
        with contextlib.suppress(Exception):
            if proc.stdin is not None:
                proc.stdin.close()
        await self._terminate_tree(proc)
        for task in (self._handshake_task, self._reader_task, self._stderr_task):
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._fail_pending()
        self._proc = None

    async def _terminate_tree(self, proc: ProcLike) -> None:
        if proc.returncode is not None:
            return
        if (
            codex_cmdline.is_win32()
            and isinstance(proc, asyncio.subprocess.Process)
            and proc.pid
        ):
            await self._run_taskkill(proc.pid)  # terminate 杀不掉底层 node（E2 §1.2）
        else:
            with contextlib.suppress(Exception):
                proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=_STOP_GRACE_SEC)
        except (TimeoutError, Exception):  # noqa: BLE001
            with contextlib.suppress(Exception):
                proc.kill()

    async def _run_taskkill(self, pid: int) -> None:
        with contextlib.suppress(Exception):
            p = await asyncio.create_subprocess_exec(
                *codex_cmdline.taskkill_argv(pid),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await p.wait()

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    def _diag(self, dtype: str, payload: dict[str, Any]) -> DiagnosticEventIn:
        return DiagnosticEventIn(
            agent_member_id=self.agent_member_id, type=dtype, payload=payload, at=self._now()
        )


async def _default_codex_spawn(argv: list[str], cwd: str, env: dict[str, str]) -> ProcLike:
    return await asyncio.create_subprocess_exec(  # type: ignore[return-value]
        *argv,
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
