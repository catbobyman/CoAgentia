/**
 * 指令处理器注册表（契约 D §5；每条按自然键幂等 → ack done/noop/failed）。
 *
 * 处理器委托 RuntimeAdapter（A7 前用 FakeAdapter）：适配器返回「状态是否变更」的 bool，
 * 处理器据此回 done / noop。worktree 指令委托 GitWorktreeManager；check/preview/deploy
 * 委托各 Runner。frame_id 短窗去重是加速器；正确性押在自然键幂等。
 *
 * 对等基准 = apps/daemon handlers.py。py 侧 pydantic model_validate 的入参校验在 TS 为
 * 结构断言（protocol.ts 口径：按需取字段，畸形帧由 handler 异常收敛为 failed）。
 */

import type {
  AgentBoot,
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
} from '@coagentia/contracts-ts';

import type { DaemonClient } from './client.ts';

export type AckResultValue = 'done' | 'noop' | 'failed';
export type HandlerResult = [AckResultValue, FrameError | null];
export type Handler = (client: DaemonClient, data: Record<string, unknown>) => Promise<HandlerResult>;

const OK: FrameError | null = null;

async function agentStart(client: DaemonClient, data: Record<string, unknown>): Promise<HandlerResult> {
  const boot = data['agent'] as AgentBoot;
  client.paths.ensureAgentHome(boot.agent_member_id);
  const started = await client.adapter.start(boot);
  return [started ? 'done' : 'noop', OK];
}

async function agentStop(client: DaemonClient, data: Record<string, unknown>): Promise<HandlerResult> {
  const stopped = await client.adapter.stop(data['agent_member_id'] as string);
  return [stopped ? 'done' : 'noop', OK];
}

async function agentRestart(client: DaemonClient, data: Record<string, unknown>): Promise<HandlerResult> {
  await client.adapter.restart(data['agent'] as AgentBoot);
  return ['done', OK];
}

async function agentResetSession(client: DaemonClient, data: Record<string, unknown>): Promise<HandlerResult> {
  await client.adapter.resetSession(data['agent'] as AgentBoot);
  return ['done', OK];
}

async function agentResetFull(client: DaemonClient, data: Record<string, unknown>): Promise<HandlerResult> {
  const boot = data['agent'] as AgentBoot;
  // 第三档：清空 Home 目录内容（目录保留）+ 清 session；诊断历史在 server，不清。
  client.paths.clearAgentHome(boot.agent_member_id);
  client.paths.clearSession(boot.agent_member_id);
  await client.adapter.resetFull(boot);
  return ['done', OK];
}

async function agentWake(client: DaemonClient, data: Record<string, unknown>): Promise<HandlerResult> {
  const d = data as unknown as AgentWakeData;
  const woke = await client.adapter.wake(d.agent_member_id, d.reason, d.refs);
  return [woke ? 'done' : 'noop', OK];
}

async function agentSleep(_client: DaemonClient, _data: Record<string, unknown>): Promise<HandlerResult> {
  // 登记不使用（契约 D §5.1；M1 无服务端触发方）→ 幂等 noop。
  return ['noop', OK];
}

async function messageDeliver(client: DaemonClient, data: Record<string, unknown>): Promise<HandlerResult> {
  const d = data as unknown as MessageDeliverData;
  const messages = d.messages as unknown as Array<Record<string, unknown>>;
  const delivered = await client.adapter.deliver(d.agent_member_id, d.channel_id, messages, d.thread_root_id ?? null);
  // 键 = agent + 批内最大 message_id；重复投递同批 → 已喂过 → noop（契约 D §5.2）。
  return [delivered ? 'done' : 'noop', OK];
}

async function messageInject(client: DaemonClient, data: Record<string, unknown>): Promise<HandlerResult> {
  const d = data as unknown as MessageInjectData;
  await client.adapter.inject(
    d.agent_member_id,
    d.body,
    d.source as unknown as Record<string, unknown>,
    d.diagnostic_type,
  );
  return ['done', OK];
}

async function runtimeRescan(client: DaemonClient, _data: Record<string, unknown>): Promise<HandlerResult> {
  // 无状态、重复无害；结果走 runtimes.detected 上报（契约 D §5.3 / §7）。
  await client.rescanRuntimes();
  return ['done', OK];
}

async function worktreeEnsure(client: DaemonClient, data: Record<string, unknown>): Promise<HandlerResult> {
  const operation = await client.git.ensure(data as unknown as WorktreeEnsureData);
  if (operation.status !== null) await client.reportWorktreeStatus(operation.status);
  return [operation.changed ? 'done' : 'noop', OK];
}

async function worktreeCleanup(client: DaemonClient, data: Record<string, unknown>): Promise<HandlerResult> {
  const operation = await client.git.cleanup(data as unknown as WorktreeCleanupData);
  if (operation.status !== null) await client.reportWorktreeStatus(operation.status);
  return [operation.changed ? 'done' : 'noop', OK];
}

async function worktreeMerge(client: DaemonClient, data: Record<string, unknown>): Promise<HandlerResult> {
  const operation = await client.git.merge(data as unknown as WorktreeMergeData);
  if (operation.status !== null) await client.reportWorktreeStatus(operation.status);
  return [operation.changed ? 'done' : 'noop', OK];
}

async function checkRun(client: DaemonClient, data: Record<string, unknown>): Promise<HandlerResult> {
  const run = data as unknown as CheckRunData;
  const buffered = client.buffer.findCheck(run.run_id);
  if (buffered !== null) {
    await client.reportCheckFinished(buffered);
    return ['noop', OK];
  }
  const [started, known] = client.checks.start(run, (d) => client.reportCheckFinished(d));
  if (known !== null) await client.reportCheckFinished(known);
  return [started ? 'done' : 'noop', OK];
}

async function previewStart(client: DaemonClient, data: Record<string, unknown>): Promise<HandlerResult> {
  const d = data as unknown as PreviewStartData;
  // 起进程后立即 ack（健康检查异步经 report_cb 上报，同 check.start 后台 task 立即 ack 思想）。
  const [started, status] = await client.previews.start(d, (s) => client.reportPreviewStatus(s));
  if (status !== null) {
    // 已在跑 → 补报现状端口；起进程即失败 → 补报预生成 failed（契约 D §5.3）。
    await client.reportPreviewStatus(status);
  }
  return [started ? 'done' : 'noop', OK];
}

async function previewStop(client: DaemonClient, data: Record<string, unknown>): Promise<HandlerResult> {
  const d = data as unknown as PreviewStopData;
  const [stopped, status] = await client.previews.stop(d.preview_session_id);
  if (status !== null) {
    // recycled；回收判定在 server，daemon 只执行并上报事实。
    await client.reportPreviewStatus(status);
  }
  return [stopped ? 'done' : 'noop', OK];
}

async function deployRun(client: DaemonClient, data: Record<string, unknown>): Promise<HandlerResult> {
  const d = data as unknown as DeployRunData;
  // 已终态重发（缓冲留痕）→ 重报终态，不重跑（副作用不可重放，铁律 3）。
  const buffered = client.buffer.findDeployFinished(d.deployment_id);
  if (buffered !== null) {
    await client.reportDeployFinished(buffered);
    return ['noop', OK];
  }
  const [started, known] = client.deploys.start(
    d,
    (l) => client.reportDeployLog(l),
    (f) => client.reportDeployFinished(f),
  );
  if (!started && known !== null) {
    // 内存态已终态（同进程重发）→ 重报终态供 server CAS 幂等落库。
    await client.reportDeployFinished(known);
  }
  return [started ? 'done' : 'noop', OK];
}

export const HANDLERS: Partial<Record<InstrType, Handler>> = {
  'agent.start': agentStart,
  'agent.stop': agentStop,
  'agent.restart': agentRestart,
  'agent.reset_session': agentResetSession,
  'agent.reset_full': agentResetFull,
  'agent.wake': agentWake,
  'agent.sleep': agentSleep,
  'message.deliver': messageDeliver,
  'message.inject': messageInject,
  'runtime.rescan': runtimeRescan,
  'worktree.ensure': worktreeEnsure,
  'worktree.merge': worktreeMerge,
  'worktree.cleanup': worktreeCleanup,
  'check.run': checkRun,
  'preview.start': previewStart,
  'preview.stop': previewStop,
  'deploy.run': deployRun,
};
