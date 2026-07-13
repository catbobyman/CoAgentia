"""指令处理器注册表（契约 D §5；每条按自然键幂等 → ack done/noop/failed）。

处理器委托 RuntimeAdapter（A7 前用 FakeAdapter）：适配器返回"状态是否变更"的 bool，
处理器据此回 done / noop。M6 worktree 指令委托 GitWorktreeManager；M7 preview/deploy
仍回 failed。frame_id 短窗去重是加速器；正确性押在自然键幂等。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from coagentia_contracts.daemon import (
    AckResult,
    AgentBoot,
    AgentRefData,
    AgentWakeData,
    CheckRunData,
    DeployRunData,
    FrameError,
    InstrType,
    MessageDeliverData,
    MessageInjectData,
    PreviewStartData,
    PreviewStopData,
    WorktreeCleanupData,
    WorktreeEnsureData,
    WorktreeMergeData,
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


async def _worktree_ensure(client: DaemonClient, data: dict[str, Any]) -> HandlerResult:
    operation = await client.git.ensure(WorktreeEnsureData.model_validate(data))
    if operation.status is not None:
        await client.report_worktree_status(operation.status)
    return (AckResult.DONE if operation.changed else AckResult.NOOP, _OK)


async def _worktree_cleanup(client: DaemonClient, data: dict[str, Any]) -> HandlerResult:
    operation = await client.git.cleanup(WorktreeCleanupData.model_validate(data))
    if operation.status is not None:
        await client.report_worktree_status(operation.status)
    return (AckResult.DONE if operation.changed else AckResult.NOOP, _OK)


async def _worktree_merge(client: DaemonClient, data: dict[str, Any]) -> HandlerResult:
    operation = await client.git.merge(WorktreeMergeData.model_validate(data))
    if operation.status is not None:
        await client.report_worktree_status(operation.status)
    return (AckResult.DONE if operation.changed else AckResult.NOOP, _OK)


async def _check_run(client: DaemonClient, data: dict[str, Any]) -> HandlerResult:
    run = CheckRunData.model_validate(data)
    buffered = client.buffer.find_check(run.run_id)
    if buffered is not None:
        await client.report_check_finished(buffered)
        return (AckResult.NOOP, _OK)
    started, known = client.checks.start(run, client.report_check_finished)
    if known is not None:
        await client.report_check_finished(known)
    return (AckResult.DONE if started else AckResult.NOOP, _OK)


async def _preview_start(client: DaemonClient, data: dict[str, Any]) -> HandlerResult:
    d = PreviewStartData.model_validate(data)
    # 起进程后立即 ack（健康检查异步经 report_cb 上报，同 check.start 后台 task 立即 ack 思想）。
    started, status = await client.previews.start(d, client.report_preview_status)
    if status is not None:
        # 已在跑 → 补报现状端口；起进程即失败 → 补报预生成 failed（契约 D §5.3）。
        await client.report_preview_status(status)
    return (AckResult.DONE if started else AckResult.NOOP, _OK)


async def _preview_stop(client: DaemonClient, data: dict[str, Any]) -> HandlerResult:
    d = PreviewStopData.model_validate(data)
    stopped, status = await client.previews.stop(d.preview_session_id)
    if status is not None:
        # recycled；回收判定在 server，daemon 只执行并上报事实。
        await client.report_preview_status(status)
    return (AckResult.DONE if stopped else AckResult.NOOP, _OK)


async def _deploy_run(client: DaemonClient, data: dict[str, Any]) -> HandlerResult:
    d = DeployRunData.model_validate(data)
    # 已终态重发（缓冲留痕）→ 重报终态，不重跑（副作用不可重放，铁律 3）。
    buffered = client.buffer.find_deploy_finished(d.deployment_id)
    if buffered is not None:
        await client.report_deploy_finished(buffered)
        return (AckResult.NOOP, _OK)
    started, known = client.deploys.start(
        d, client.report_deploy_log, client.report_deploy_finished
    )
    if not started and known is not None:
        # 内存态已终态（同进程重发）→ 重报终态供 server CAS 幂等落库。
        await client.report_deploy_finished(known)
    return (AckResult.DONE if started else AckResult.NOOP, _OK)


async def _unsupported(client: DaemonClient, data: dict[str, Any]) -> HandlerResult:
    return (
        AckResult.FAILED,
        FrameError(code="UNSUPPORTED_IN_M1", message="该交付类指令尚未进入当前实现波次"),
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
    InstrType.WORKTREE_ENSURE: _worktree_ensure,
    InstrType.WORKTREE_MERGE: _worktree_merge,
    InstrType.WORKTREE_CLEANUP: _worktree_cleanup,
    InstrType.CHECK_RUN: _check_run,
    InstrType.PREVIEW_START: _preview_start,
    InstrType.PREVIEW_STOP: _preview_stop,
    InstrType.DEPLOY_RUN: _deploy_run,
}
