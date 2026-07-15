"""stream-json 帧 → 上报回调映射（契约 E §7/§8；锚定 CLI 2.1.205 真机帧）。

纯逻辑，无子进程依赖——可用桩帧全量单测（防腐层 / 相位聚合 / usage 提取 / 诊断映射）。

**帧防腐层（铁律 4）**：未知 top-level `type` / 未知 `system.subtype` / 契约外帧
（rate_limit_event、system/status|api_retry|notification）一律忽略并计数；每种未知类型**首现**
写一条低频 `agent.unknown_frame` 诊断（后续同类型静默累加 `unknown_counts`）——CLI 升级不崩。

**相位聚合（§7.2/§7.3）**：仅在相位切换时回调一帧 activity；同相位 delta 帧不产生上报。
**usage 提取（§7.4）**：唯一提取点 = result 帧；id=适配器 ULID（exactly-once 去重根基），
source_session=init 帧的 session_id（UUID）；忽略 message_delta 中间 usage 与 total_cost_usd。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from coagentia_contracts.daemon import DiagnosticEventIn, TokenUsageEventIn
from coagentia_contracts.enums import AgentStatus

from coagentia_daemon.adapter import AdapterSink
from coagentia_daemon.util import new_ulid, now_iso

# 相位文案（ACTIVITY_PHRASES 值域，契约 E §7.2 / C §6.2）
_P_THINKING = "Thinking…"
_P_REPLYING = "Replying…"
_P_COMMAND = "Running command…"
_P_WRITING = "Writing file…"
_P_READING = "Reading files…"
_P_BROWSING = "Browsing…"
_P_SUBAGENT = "Subagent started"
_P_USING = "Using {tool}…"  # ACTIVITY_PHRASES 模板

# 工具名 → 相位分类（其余工具落 "Using {tool}…"）
_TOOLS_COMMAND = frozenset({"Bash", "BashOutput", "KillShell", "KillBash"})
_TOOLS_WRITING = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
_TOOLS_READING = frozenset({"Read", "Glob", "Grep", "LS", "NotebookRead"})
_TOOLS_BROWSING = frozenset({"WebFetch", "WebSearch"})
_TOOLS_SUBAGENT = frozenset({"Task"})

_TURN_OUTPUT_PREVIEW_MAX = 500  # §8 agent.turn_output 正文预览上限
_COMMAND_PREVIEW_MAX = 500


def _short_tool(name: str) -> str:
    """MCP 工具名 mcp__coagentia__send_message → send_message（活动文案可读性）。"""
    if name.startswith("mcp__"):
        return name.rsplit("__", 1)[-1] or name
    return name


def phase_for_tool(name: str) -> str:
    """工具名 → activity 相位（契约 E §7.2）。"""
    if name in _TOOLS_COMMAND:
        return _P_COMMAND
    if name in _TOOLS_WRITING:
        return _P_WRITING
    if name in _TOOLS_READING:
        return _P_READING
    if name in _TOOLS_BROWSING:
        return _P_BROWSING
    if name in _TOOLS_SUBAGENT:
        return _P_SUBAGENT
    return _P_USING.format(tool=_short_tool(name))


class FrameRouter:
    """单 Agent 的 stream-json 帧解析器 → AdapterSink 四回调。

    每进程一实例；turn 上下文（channel_id/thread_root_id）由管理器在 feed 前置入，
    result 帧据此为 usage 事件打归属提示（server 富化为 task_id）。
    """

    def __init__(
        self,
        agent_member_id: str,
        sink: AdapterSink,
        *,
        ulid: Callable[[], str] = new_ulid,
        now: Callable[[], str] = now_iso,
        on_session: Callable[[str], None] | None = None,
    ) -> None:
        self.agent_member_id = agent_member_id
        self._sink = sink
        self._ulid = ulid
        self._now = now
        self._on_session = on_session
        # 会话簿记
        self.session_id: str | None = None
        self.model: str | None = None
        # 相位聚合
        self._phase: str | None = None
        # turn 进行中标记（feed 置位；result 复位）——防止 init 帧在 turn 中途误报 idle。
        # 实测本 CLI stream-json 输入模式：init 帧**首个 stdin 输入后**才到（E §11.3），
        # 故 init→idle 需避开正在进行的 turn。
        self._turn_active = False
        # 会话已确认（见过 init 或 result）——resume 是否真正生效的判据（管理器降级用）。
        self.confirmed = False
        # 防腐层计数（lifetime）
        self.unknown_counts: dict[str, int] = {}
        # tool_use_id → (name, input) 关联（assistant/stream_event 登记，user tool_result 回查）
        self._tool_uses: dict[str, tuple[str, dict[str, Any]]] = {}
        # turn 归属提示
        self.channel_id: str | None = None
        self.thread_root_id: str | None = None
        # 最近一次上报状态（管理器读，避免重复 emit）
        self.last_status: AgentStatus | None = None

    def set_turn_context(self, channel_id: str | None, thread_root_id: str | None) -> None:
        self.channel_id = channel_id
        self.thread_root_id = thread_root_id

    def reset_phase(self) -> None:
        self._phase = None

    def begin_turn(self) -> None:
        """管理器喂入一个 turn 前调用：标记 turn 进行中（init→idle 抑制窗口）。"""
        self._turn_active = True

    def reset_run(self) -> None:
        """一次 spawn 的运行态复位（confirmed/turn 标记；session_id 保留）。"""
        self.confirmed = False
        self._turn_active = False
        self._phase = None

    # ------------------------------------------------------------ 分发

    async def process(self, frame: dict[str, Any]) -> None:
        """路由一帧（防腐：任何未知/畸形帧不得抛出到调用方）。"""
        ftype = frame.get("type")
        if ftype == "system":
            await self._on_system(frame)
        elif ftype == "stream_event":
            await self._on_stream_event(frame)
        elif ftype == "assistant":
            await self._on_assistant(frame)
        elif ftype == "user":
            await self._on_user(frame)
        elif ftype == "result":
            await self._on_result(frame)
        else:
            self._count_unknown(str(ftype))

    # ------------------------------------------------------------ system（init → idle）

    async def _on_system(self, frame: dict[str, Any]) -> None:
        subtype = frame.get("subtype")
        if subtype == "init":
            sid = frame.get("session_id")
            if sid and sid != self.session_id:
                self.session_id = sid
                if self._on_session is not None:
                    self._on_session(sid)  # 会话簿记（daemon/state/<id>.json，§4）
            self.model = frame.get("model") or self.model
            self.confirmed = True  # resume 生效 / 会话就绪的权威凭据
            if not self._turn_active:
                # turn 未在跑（罕见：某些模式 init 先于输入）→ 就绪 idle。
                await self._status(AgentStatus.IDLE)
        else:
            # system/status | api_retry | notification（契约外噪声）→ 防腐层
            self._count_unknown(f"system/{subtype}")

    # ------------------------------------------------------------ stream_event（相位聚合）

    async def _on_stream_event(self, frame: dict[str, Any]) -> None:
        event = frame.get("event") or {}
        etype = event.get("type")
        if etype == "content_block_start":
            block = event.get("content_block") or {}
            btype = block.get("type")
            if btype == "thinking":
                await self._switch_phase(_P_THINKING)
            elif btype == "text":
                await self._switch_phase(_P_REPLYING)
            elif btype == "tool_use":
                name = block.get("name") or ""
                bid = block.get("id")
                if bid:
                    self._tool_uses[bid] = (name, block.get("input") or {})
                await self._switch_phase(phase_for_tool(name))
        # message_start / content_block_delta / content_block_stop / message_delta /
        # message_stop：同相位或无相位 → 不上报（§7.2）

    async def _switch_phase(self, phase: str) -> None:
        if phase == self._phase:
            return
        self._phase = phase
        await self._sink.on_activity(self.agent_member_id, phase)

    # ------------------------------------------------------------ assistant（turn_output 诊断）

    async def _on_assistant(self, frame: dict[str, Any]) -> None:
        msg = frame.get("message") or {}
        content = msg.get("content") or []
        text_parts: list[str] = []
        tool_calls = 0
        for block in content:
            btype = block.get("type")
            if btype == "text":
                text_parts.append(block.get("text") or "")
            elif btype == "tool_use":
                tool_calls += 1
                bid = block.get("id")
                if bid:
                    self._tool_uses[bid] = (block.get("name") or "", block.get("input") or {})
        preview = "".join(text_parts)[:_TURN_OUTPUT_PREVIEW_MAX]
        self._diag(
            "agent.turn_output",
            {
                "preview": preview,  # 正文**不外发**，仅截断留痕（铁律 5）
                "tool_calls": tool_calls,
                "stop_reason": msg.get("stop_reason"),
            },
        )

    # ------------------------------------------------------------ user（tool_result → 诊断）

    async def _on_user(self, frame: dict[str, Any]) -> None:
        msg = frame.get("message") or {}
        content = msg.get("content") or []
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tid = str(block.get("tool_use_id") or "")  # 缺失/非法 → "" 查默认（帧防腐）
            name, tool_input = self._tool_uses.get(tid, ("", {}))
            is_error = bool(block.get("is_error"))
            if name in _TOOLS_COMMAND:
                cmd = str(tool_input.get("command", ""))[:_COMMAND_PREVIEW_MAX]
                self._diag("agent.command", {"command": cmd, "is_error": is_error})
            elif name in _TOOLS_WRITING:
                kind = "create" if name == "Write" else "edit"
                self._diag(
                    "agent.file_edit",
                    {"path": tool_input.get("file_path"), "kind": kind, "is_error": is_error},
                )
            else:
                self._diag("agent.tool_call", {"tool": name or "unknown", "ok": not is_error})

    # ------------------------------------------------------------ result（usage + idle/error）

    async def _on_result(self, frame: dict[str, Any]) -> None:
        usage = frame.get("usage") or {}
        event = TokenUsageEventIn(
            id=self._ulid(),  # 每 result 帧一个 ULID（exactly-once 去重根基，§7.4）
            agent_member_id=self.agent_member_id,
            channel_id=self.channel_id,
            thread_root_id=self.thread_root_id,
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            cache_read_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
            cache_write_tokens=int(usage.get("cache_creation_input_tokens", 0) or 0),
            source_session=self.session_id,
            reported_at=self._now(),
        )
        self._sink.on_usage(event)  # total_cost_usd / modelUsage.costUSD 忽略（永不折算货币）

        self.confirmed = True
        self._turn_active = False
        self.reset_phase()
        self._tool_uses.clear()
        subtype = frame.get("subtype")
        is_error = bool(frame.get("is_error")) or bool(frame.get("api_error_status"))
        if subtype == "success" and not is_error:
            await self._status(AgentStatus.IDLE)
        else:
            detail = str(subtype or frame.get("api_error_status") or "result_error")
            await self._status(AgentStatus.ERROR, error_detail=detail)

    # ------------------------------------------------------------ 防腐层

    def _count_unknown(self, key: str) -> None:
        seen = self.unknown_counts.get(key, 0)
        self.unknown_counts[key] = seen + 1
        if seen == 0:  # 每种未知类型首现写一条低频诊断，后续静默累加
            self._diag("agent.unknown_frame", {"type": key, "count": 1})

    # ------------------------------------------------------------ 回调底座

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
