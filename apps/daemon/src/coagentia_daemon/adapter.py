"""RuntimeAdapter 接口（契约 E §9 的 M1 接缝）+ AdapterSink 回调 + FakeAdapter 占位。

A6 只到指令的**自然键幂等消费**与**状态/遥测回传**为止；Agent 进程的真实驱动（命令行拼装、
stream-json 解析、崩溃拉起）归契约 E 的 claude_code 适配器（A7）。FakeAdapter 满足 A6 全部
契约义务：按自然键幂等（重复 start/deliver → noop 且无第二次副作用）、经 AdapterSink 上报
status_changed（starting→idle）/activity/usage/diagnostics。A7 以真适配器替换本类，client 不变。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from coagentia_contracts.daemon import (
    AgentBoot,
    DaemonAgentState,
    DiagnosticEventIn,
    TokenUsageEventIn,
    WakeRefs,
)
from coagentia_contracts.enums import AgentStatus, WakeReason


class AdapterSink(Protocol):
    """适配器 → daemon client 的回传口（契约 D §7 上报帧的来源）。"""

    async def on_status_changed(
        self, agent_member_id: str, status: AgentStatus, error_detail: str | None = None
    ) -> None: ...

    async def on_activity(self, agent_member_id: str, detail: str) -> None: ...

    def on_usage(self, event: TokenUsageEventIn) -> None: ...

    def on_diagnostic(self, event: DiagnosticEventIn) -> None: ...


@runtime_checkable
class RuntimeAdapter(Protocol):
    """契约 E §9 RuntimeAdapter 接口的 M1 子集（生命周期 + 投递 + 进程表 + Home 定位）。

    幂等契约（契约 D §5，"自然键"）：start/stop/wake/deliver 返回 bool = "状态是否发生变更"
    （True=真动作；False=已是目标态 → client ack noop，无第二次副作用）。
    """

    def bind(self, sink: AdapterSink) -> None: ...

    async def start(self, boot: AgentBoot) -> bool: ...

    async def stop(self, agent_member_id: str) -> bool: ...

    async def restart(self, boot: AgentBoot) -> None: ...

    async def reset_session(self, boot: AgentBoot) -> None: ...

    async def reset_full(self, boot: AgentBoot) -> None: ...

    async def wake(
        self, agent_member_id: str, reason: WakeReason, refs: WakeRefs
    ) -> bool: ...

    async def deliver(
        self,
        agent_member_id: str,
        channel_id: str,
        messages: list[dict[str, Any]],
        thread_root_id: str | None,
    ) -> bool: ...

    async def inject(
        self, agent_member_id: str, body: str, source: dict[str, Any], diagnostic_type: str
    ) -> None: ...

    def process_table(self) -> list[DaemonAgentState]: ...

    def home_path(self, agent_member_id: str) -> str | None: ...


class _AgentState:
    __slots__ = ("boot", "status", "last_delivered", "awake", "source_session")

    def __init__(self, boot: AgentBoot) -> None:
        self.boot = boot
        self.status = AgentStatus.STARTING
        self.last_delivered: str | None = None  # 已喂过的最大 message_id（deliver 去重）
        self.awake = False
        self.source_session: str | None = None


class FakeAdapter:
    """A7 前的假适配器：记录调用、模拟 status 上报、按自然键幂等（无真实进程）。"""

    def __init__(self) -> None:
        self._agents: dict[str, _AgentState] = {}
        self._sink: AdapterSink | None = None
        # 可观测调用记录（测试断言副作用次数）。
        self.starts: list[str] = []
        self.stops: list[str] = []
        self.restarts: list[str] = []
        self.reset_sessions: list[str] = []
        self.reset_fulls: list[str] = []
        self.injects: list[tuple[str, str]] = []
        self.delivers: list[tuple[str, str]] = []  # (agent_id, max_message_id)

    def bind(self, sink: AdapterSink) -> None:
        self._sink = sink

    # ---- 生命周期（自然键 = agent_member_id）----

    async def start(self, boot: AgentBoot) -> bool:
        aid = boot.agent_member_id
        if aid in self._agents:
            return False  # 已在跑 → noop（自然键幂等，无第二次副作用）
        state = _AgentState(boot)
        state.source_session = f"fake-session-{aid}"
        self._agents[aid] = state
        self.starts.append(aid)
        await self._emit(aid, AgentStatus.STARTING)
        state.status = AgentStatus.IDLE
        await self._emit(aid, AgentStatus.IDLE)
        return True

    async def stop(self, agent_member_id: str) -> bool:
        if agent_member_id not in self._agents:
            return False  # 已停 → noop
        self._agents.pop(agent_member_id, None)
        self.stops.append(agent_member_id)
        await self._emit(agent_member_id, AgentStatus.OFFLINE)
        return True

    async def restart(self, boot: AgentBoot) -> None:
        # 三档第一档：保 session 保 Home，等价 stop+start，配置以本帧快照为准。
        self.restarts.append(boot.agent_member_id)
        self._agents.pop(boot.agent_member_id, None)
        await self.start(boot)

    async def reset_session(self, boot: AgentBoot) -> None:
        self.reset_sessions.append(boot.agent_member_id)
        self._agents.pop(boot.agent_member_id, None)
        await self.start(boot)  # 清 session（假：清 last_delivered 由新 state 天然完成）

    async def reset_full(self, boot: AgentBoot) -> None:
        self.reset_fulls.append(boot.agent_member_id)
        self._agents.pop(boot.agent_member_id, None)
        await self.start(boot)  # Home 清空由 client 侧 DataPaths.clear_agent_home 执行

    async def wake(self, agent_member_id: str, reason: WakeReason, refs: WakeRefs) -> bool:
        state = self._agents.get(agent_member_id)
        if state is None or state.awake:
            return False  # 未在跑 / 已清醒 → noop（deliver 照常）
        state.awake = True
        state.status = AgentStatus.BUSY
        await self._emit(agent_member_id, AgentStatus.BUSY)
        return True

    async def deliver(
        self,
        agent_member_id: str,
        channel_id: str,
        messages: list[dict[str, Any]],
        thread_root_id: str | None,
    ) -> bool:
        state = self._agents.get(agent_member_id)
        if state is None or not messages:
            return False
        max_id = max(m["id"] for m in messages)
        if state.last_delivered is not None and max_id <= state.last_delivered:
            return False  # 已喂过的最大 message_id → noop 去重（契约 D §5.2）
        state.last_delivered = max_id
        self.delivers.append((agent_member_id, max_id))
        return True

    async def inject(
        self, agent_member_id: str, body: str, source: dict[str, Any], diagnostic_type: str
    ) -> None:
        self.injects.append((agent_member_id, body))

    # ---- 进程表 / Home ----

    def process_table(self) -> list[DaemonAgentState]:
        return [
            DaemonAgentState(
                agent_member_id=aid, status=s.status, source_session=s.source_session
            )
            for aid, s in sorted(self._agents.items())
        ]

    def home_path(self, agent_member_id: str) -> str | None:
        state = self._agents.get(agent_member_id)
        return state.boot.home_path if state else None

    # ---- 内部 ----

    async def _emit(self, agent_member_id: str, status: AgentStatus) -> None:
        if self._sink is not None:
            await self._sink.on_status_changed(agent_member_id, status)
