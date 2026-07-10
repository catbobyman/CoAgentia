"""指令处理器注册表（契约 D §5；每条按自然键幂等 → ack done/noop/failed）。

处理器委托 RuntimeAdapter（A7 前用 FakeAdapter）：适配器返回"状态是否变更"的 bool，
处理器据此回 done / noop。M6/M7 指令（worktree/preview/deploy）在 M1 仅登记目录 → 回
failed(UNSUPPORTED_IN_M1)，不发明实现。frame_id 短窗去重是加速器；正确性押在自然键幂等。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from coagentia_contracts.daemon import (
    AckResult,
    AgentBoot,
    AgentRefData,
    AgentWakeData,
    FrameError,
    InstrType,
    MessageDeliverData,
    MessageInjectData,
)

if TYPE_CHECKING:
    from coagentia_daemon.client import DaemonClient

HandlerResult = tuple[AckResult, FrameError | None]
Handler = Callable[["DaemonClient", dict[str, Any]], Awaitable[HandlerResult]]

_OK: FrameError | None = None


async def _agent_start(client: DaemonClient, data: dict[str, Any]) -> HandlerResult:
    boot = AgentBoot.model_validate(data["agent"])
    client.paths.ensure_agent_home(boot.agent_member_id)
    started = await client.adapter.start(boot)
    return (AckResult.DONE if started else AckResult.NOOP, _OK)


async def _agent_stop(client: DaemonClient, data: dict[str, Any]) -> HandlerResult:
    ref = AgentRefData.model_validate(data)
    stopped = await client.adapter.stop(ref.agent_member_id)
    return (AckResult.DONE if stopped else AckResult.NOOP, _OK)


async def _agent_restart(client: DaemonClient, data: dict[str, Any]) -> HandlerResult:
    boot = AgentBoot.model_validate(data["agent"])
    await client.adapter.restart(boot)
    return (AckResult.DONE, _OK)


async def _agent_reset_session(client: DaemonClient, data: dict[str, Any]) -> HandlerResult:
    boot = AgentBoot.model_validate(data["agent"])
    await client.adapter.reset_session(boot)
    return (AckResult.DONE, _OK)


async def _agent_reset_full(client: DaemonClient, data: dict[str, Any]) -> HandlerResult:
    boot = AgentBoot.model_validate(data["agent"])
    # 第三档：清空 Home 目录内容（目录保留）+ 清 session；诊断历史在 server，不清。
    client.paths.clear_agent_home(boot.agent_member_id)
    client.paths.clear_session(boot.agent_member_id)
    await client.adapter.reset_full(boot)
    return (AckResult.DONE, _OK)


async def _agent_wake(client: DaemonClient, data: dict[str, Any]) -> HandlerResult:
    d = AgentWakeData.model_validate(data)
    woke = await client.adapter.wake(d.agent_member_id, d.reason, d.refs)
    return (AckResult.DONE if woke else AckResult.NOOP, _OK)


async def _agent_sleep(client: DaemonClient, data: dict[str, Any]) -> HandlerResult:
    # 登记不使用（契约 D §5.1；M1 无服务端触发方）→ 幂等 noop。
    return (AckResult.NOOP, _OK)


async def _message_deliver(client: DaemonClient, data: dict[str, Any]) -> HandlerResult:
    d = MessageDeliverData.model_validate(data)
    messages = [m.model_dump(mode="json") for m in d.messages]
    delivered = await client.adapter.deliver(
        d.agent_member_id, d.channel_id, messages, d.thread_root_id
    )
    # 键 = agent + 批内最大 message_id；重复投递同批 → 已喂过 → noop（契约 D §5.2）。
    return (AckResult.DONE if delivered else AckResult.NOOP, _OK)


async def _message_inject(client: DaemonClient, data: dict[str, Any]) -> HandlerResult:
    d = MessageInjectData.model_validate(data)
    await client.adapter.inject(
        d.agent_member_id, d.body, d.source.model_dump(mode="json"), d.diagnostic_type
    )
    return (AckResult.DONE, _OK)


async def _runtime_rescan(client: DaemonClient, data: dict[str, Any]) -> HandlerResult:
    # 无状态、重复无害；结果走 runtimes.detected 上报（契约 D §5.3 / §7）。
    await client.rescan_runtimes()
    return (AckResult.DONE, _OK)


async def _unsupported(client: DaemonClient, data: dict[str, Any]) -> HandlerResult:
    return (
        AckResult.FAILED,
        FrameError(code="UNSUPPORTED_IN_M1", message="交付类指令归 M6/M7（契约 D §5.3 登记目录）"),
    )


HANDLERS: dict[InstrType, Handler] = {
    InstrType.AGENT_START: _agent_start,
    InstrType.AGENT_STOP: _agent_stop,
    InstrType.AGENT_RESTART: _agent_restart,
    InstrType.AGENT_RESET_SESSION: _agent_reset_session,
    InstrType.AGENT_RESET_FULL: _agent_reset_full,
    InstrType.AGENT_WAKE: _agent_wake,
    InstrType.AGENT_SLEEP: _agent_sleep,
    InstrType.MESSAGE_DELIVER: _message_deliver,
    InstrType.MESSAGE_INJECT: _message_inject,
    InstrType.RUNTIME_RESCAN: _runtime_rescan,
    # M6/M7 登记目录（M1 未实现）：
    InstrType.WORKTREE_ENSURE: _unsupported,
    InstrType.WORKTREE_MERGE: _unsupported,
    InstrType.WORKTREE_CLEANUP: _unsupported,
    InstrType.PREVIEW_START: _unsupported,
    InstrType.PREVIEW_STOP: _unsupported,
    InstrType.DEPLOY_RUN: _unsupported,
}
