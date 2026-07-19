/**
 * RuntimeAdapter 接口（契约 E §9 的 M1 接缝）+ AdapterSink 回调 + FakeAdapter 占位。
 *
 * A6 只到指令的**自然键幂等消费**与**状态/遥测回传**为止；Agent 进程的真实驱动（命令行拼装、
 * stream-json 解析、崩溃拉起）归契约 E 的 claude_code 适配器（A7）。FakeAdapter 满足 A6 全部
 * 契约义务：按自然键幂等（重复 start/deliver → noop 且无第二次副作用）、经 AdapterSink 上报
 * status_changed（starting→idle）/activity/usage/diagnostics。A7 以真适配器替换本类，client 不变。
 *
 * 对等基准 = apps/daemon adapter.py；py Protocol → TS interface（结构类型等价）。
 */

import type { AgentBoot, AgentStatus, DaemonAgentState, DiagnosticEventIn, TokenUsageEventIn, WakeReason, WakeRefs } from '@coagentia/contracts-ts';

/** 适配器 → daemon client 的回传口（契约 D §7 上报帧的来源）。 */
export interface AdapterSink {
  onStatusChanged(agentMemberId: string, status: AgentStatus, errorDetail?: string | null): Promise<void>;

  onActivity(agentMemberId: string, detail: string): Promise<void>;

  onUsage(event: TokenUsageEventIn): void;

  onDiagnostic(event: DiagnosticEventIn): void;
}

/**
 * 契约 E §9 RuntimeAdapter 接口的 M1 子集（生命周期 + 投递 + 进程表 + Home 定位）。
 *
 * 幂等契约（契约 D §5，「自然键」）：start/stop/wake/deliver 返回 boolean = 「状态是否发生变更」
 * （true=真动作；false=已是目标态 → client ack noop，无第二次副作用）。
 *
 * 注：adapters/base.ts 另有同名 RuntimeAdapter（每进程驱动接口）——py 侧同名不消歧（挂账
 * TS-③，裁决 #12 原样直译），TS 侧靠 import 路径文件隔离，不冲突。
 */
export interface RuntimeAdapter {
  bind(sink: AdapterSink): void;

  start(boot: AgentBoot): Promise<boolean>;

  stop(agentMemberId: string): Promise<boolean>;

  restart(boot: AgentBoot): Promise<void>;

  resetSession(boot: AgentBoot): Promise<void>;

  resetFull(boot: AgentBoot): Promise<void>;

  wake(agentMemberId: string, reason: WakeReason, refs: WakeRefs): Promise<boolean>;

  deliver(
    agentMemberId: string,
    channelId: string,
    messages: Array<Record<string, unknown>>,
    threadRootId: string | null,
  ): Promise<boolean>;

  inject(
    agentMemberId: string,
    body: string,
    source: Record<string, unknown>,
    diagnosticType: string,
  ): Promise<void>;

  processTable(): DaemonAgentState[];

  homePath(agentMemberId: string): string | null;
}

class _AgentState {
  boot: AgentBoot;
  status: AgentStatus = 'starting';
  lastDelivered: string | null = null; // 已喂过的最大 message_id（deliver 去重）
  awake = false;
  sourceSession: string | null = null;

  constructor(boot: AgentBoot) {
    this.boot = boot;
  }
}

/** A7 前的假适配器：记录调用、模拟 status 上报、按自然键幂等（无真实进程）。 */
export class FakeAdapter implements RuntimeAdapter {
  private agents = new Map<string, _AgentState>();
  private sink: AdapterSink | null = null;
  // 可观测调用记录（测试断言副作用次数）。
  starts: string[] = [];
  stops: string[] = [];
  restarts: string[] = [];
  resetSessions: string[] = [];
  resetFulls: string[] = [];
  injects: Array<[string, string]> = [];
  delivers: Array<[string, string]> = []; // (agent_id, max_message_id)

  bind(sink: AdapterSink): void {
    this.sink = sink;
  }

  // ---- 生命周期（自然键 = agent_member_id）----

  async start(boot: AgentBoot): Promise<boolean> {
    const aid = boot.agent_member_id;
    if (this.agents.has(aid)) {
      return false; // 已在跑 → noop（自然键幂等，无第二次副作用）
    }
    const state = new _AgentState(boot);
    state.sourceSession = `fake-session-${aid}`;
    this.agents.set(aid, state);
    this.starts.push(aid);
    await this.emit(aid, 'starting');
    state.status = 'idle';
    await this.emit(aid, 'idle');
    return true;
  }

  async stop(agentMemberId: string): Promise<boolean> {
    if (!this.agents.has(agentMemberId)) {
      return false; // 已停 → noop
    }
    this.agents.delete(agentMemberId);
    this.stops.push(agentMemberId);
    await this.emit(agentMemberId, 'offline');
    return true;
  }

  async restart(boot: AgentBoot): Promise<void> {
    // 三档第一档：保 session 保 Home，等价 stop+start，配置以本帧快照为准。
    this.restarts.push(boot.agent_member_id);
    this.agents.delete(boot.agent_member_id);
    await this.start(boot);
  }

  async resetSession(boot: AgentBoot): Promise<void> {
    this.resetSessions.push(boot.agent_member_id);
    this.agents.delete(boot.agent_member_id);
    await this.start(boot); // 清 session（假：清 lastDelivered 由新 state 天然完成）
  }

  async resetFull(boot: AgentBoot): Promise<void> {
    this.resetFulls.push(boot.agent_member_id);
    this.agents.delete(boot.agent_member_id);
    await this.start(boot); // Home 清空由 client 侧 DataPaths.clearAgentHome 执行
  }

  async wake(agentMemberId: string, reason: WakeReason, refs: WakeRefs): Promise<boolean> {
    void reason;
    void refs;
    const state = this.agents.get(agentMemberId);
    if (state === undefined || state.awake) {
      return false; // 未在跑 / 已清醒 → noop（deliver 照常）
    }
    state.awake = true;
    state.status = 'busy';
    await this.emit(agentMemberId, 'busy');
    return true;
  }

  async deliver(
    agentMemberId: string,
    channelId: string,
    messages: Array<Record<string, unknown>>,
    threadRootId: string | null,
  ): Promise<boolean> {
    void channelId;
    void threadRootId;
    const state = this.agents.get(agentMemberId);
    if (state === undefined || messages.length === 0) {
      return false;
    }
    let maxId = String(messages[0]['id']);
    for (const m of messages) {
      const mid = String(m['id']);
      if (mid > maxId) maxId = mid;
    }
    if (state.lastDelivered !== null && maxId <= state.lastDelivered) {
      return false; // 已喂过的最大 message_id → noop 去重（契约 D §5.2）
    }
    state.lastDelivered = maxId;
    this.delivers.push([agentMemberId, maxId]);
    return true;
  }

  async inject(
    agentMemberId: string,
    body: string,
    source: Record<string, unknown>,
    diagnosticType: string,
  ): Promise<void> {
    void source;
    void diagnosticType;
    this.injects.push([agentMemberId, body]);
  }

  // ---- 进程表 / Home ----

  processTable(): DaemonAgentState[] {
    return [...this.agents.entries()]
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
      .map(([aid, s]) => ({
        agent_member_id: aid,
        status: s.status,
        source_session: s.sourceSession,
      }));
  }

  homePath(agentMemberId: string): string | null {
    const state = this.agents.get(agentMemberId);
    return state !== undefined ? state.boot.home_path : null;
  }

  // ---- 内部 ----

  private async emit(agentMemberId: string, status: AgentStatus): Promise<void> {
    if (this.sink !== null) {
      await this.sink.onStatusChanged(agentMemberId, status);
    }
  }
}
