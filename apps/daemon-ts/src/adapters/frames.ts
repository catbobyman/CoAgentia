/**
 * stream-json 帧 → 上报回调映射（契约 E §7/§8；锚定 CLI 2.1.205 真机帧）。
 *
 * 纯逻辑，无子进程依赖——可用桩帧全量单测（防腐层 / 相位聚合 / usage 提取 / 诊断映射）。
 *
 * **帧防腐层（铁律 4）**：未知 top-level `type` / 未知 `system.subtype` / 契约外帧
 * （rate_limit_event、system/status|api_retry|notification）一律忽略并计数；每种未知类型**首现**
 * 写一条低频 `agent.unknown_frame` 诊断（后续同类型静默累加 `unknownCounts`）——CLI 升级不崩。
 *
 * **相位聚合（§7.2/§7.3）**：仅在相位切换时回调一帧 activity；同相位 delta 帧不产生上报。
 * **usage 提取（§7.4）**：唯一提取点 = result 帧；id=适配器 ULID（exactly-once 去重根基），
 * source_session=init 帧的 session_id（UUID）；忽略 message_delta 中间 usage 与 total_cost_usd。
 *
 * 对等基准 = apps/daemon adapters/frames.py。
 */

import type { AgentStatus, TokenUsageEventIn } from '@coagentia/contracts-ts';

import type { AdapterSink } from '../adapter.ts';
import type { JsonObject } from '../protocol.ts';
import { newUlid, nowIso } from '../util.ts';

// 相位文案（ACTIVITY_PHRASES 值域，契约 E §7.2 / C §6.2）。
// py 侧为模块私有 `_P_*`，但 codex.py 跨模块消费 → TS 侧导出（W3 codex.ts 消费面）。
export const P_THINKING = 'Thinking…';
export const P_REPLYING = 'Replying…';
export const P_COMMAND = 'Running command…';
export const P_WRITING = 'Writing file…';
export const P_READING = 'Reading files…';
export const P_BROWSING = 'Browsing…';
export const P_SUBAGENT = 'Subagent started';
export const P_USING = 'Using {tool}…'; // ACTIVITY_PHRASES 模板

// 工具名 → 相位分类（其余工具落 "Using {tool}…"）
const TOOLS_COMMAND = new Set(['Bash', 'BashOutput', 'KillShell', 'KillBash']);
const TOOLS_WRITING = new Set(['Edit', 'Write', 'MultiEdit', 'NotebookEdit']);
const TOOLS_READING = new Set(['Read', 'Glob', 'Grep', 'LS', 'NotebookRead']);
const TOOLS_BROWSING = new Set(['WebFetch', 'WebSearch']);
const TOOLS_SUBAGENT = new Set(['Task']);

const TURN_OUTPUT_PREVIEW_MAX = 500; // §8 agent.turn_output 正文预览上限
const COMMAND_PREVIEW_MAX = 500;

/** MCP 工具名 mcp__coagentia__send_message → send_message（活动文案可读性）。 */
export function shortTool(name: string): string {
  if (name.startsWith('mcp__')) {
    const idx = name.lastIndexOf('__');
    return name.slice(idx + 2) || name;
  }
  return name;
}

/** 工具名 → activity 相位（契约 E §7.2）。 */
export function phaseForTool(name: string): string {
  if (TOOLS_COMMAND.has(name)) return P_COMMAND;
  if (TOOLS_WRITING.has(name)) return P_WRITING;
  if (TOOLS_READING.has(name)) return P_READING;
  if (TOOLS_BROWSING.has(name)) return P_BROWSING;
  if (TOOLS_SUBAGENT.has(name)) return P_SUBAGENT;
  return P_USING.replace('{tool}', shortTool(name));
}

function isDict(v: unknown): v is JsonObject {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

/** py `int(x or 0)` 对等：None/缺失/假值 → 0；数值截断取整（非法值 TS 侧落 0 不抛，防腐同族）。 */
function asInt(v: unknown): number {
  const n = Number(v ?? 0);
  return Number.isFinite(n) ? Math.trunc(n) : 0;
}

export interface FrameRouterOptions {
  ulid?: () => string;
  now?: () => string;
  onSession?: ((sessionId: string) => void) | null;
}

/**
 * 单 Agent 的 stream-json 帧解析器 → AdapterSink 四回调。
 *
 * 每进程一实例；turn 上下文（channelId/threadRootId）由管理器在 feed 前置入，
 * result 帧据此为 usage 事件打归属提示（server 富化为 task_id）。
 */
export class FrameRouter {
  readonly agentMemberId: string;
  private readonly sink: AdapterSink;
  private readonly ulid: () => string;
  private readonly now: () => string;
  private readonly onSession: ((sessionId: string) => void) | null;

  // 会话簿记
  sessionId: string | null = null;
  model: string | null = null;
  // 相位聚合
  private phase: string | null = null;
  // turn 进行中标记（beginTurn 置位；result 复位）——防止 init 帧在 turn 中途误报 idle。
  // 实测本 CLI stream-json 输入模式：init 帧**首个 stdin 输入后**才到（E §11.3），
  // 故 init→idle 需避开正在进行的 turn。
  private turnActive = false;
  // 会话已确认（见过 init 或 result）——resume 是否真正生效的判据（管理器降级用）。
  confirmed = false;
  // 防腐层计数（lifetime）
  unknownCounts: Record<string, number> = {};
  // tool_use_id → [name, input] 关联（assistant/stream_event 登记，user tool_result 回查）
  private toolUses = new Map<string, [string, JsonObject]>();
  // turn 归属提示
  channelId: string | null = null;
  threadRootId: string | null = null;
  // 最近一次上报状态（管理器读，避免重复 emit）
  lastStatus: AgentStatus | null = null;

  constructor(agentMemberId: string, sink: AdapterSink, opts: FrameRouterOptions = {}) {
    this.agentMemberId = agentMemberId;
    this.sink = sink;
    this.ulid = opts.ulid ?? newUlid;
    this.now = opts.now ?? nowIso;
    this.onSession = opts.onSession ?? null;
  }

  setTurnContext(channelId: string | null, threadRootId: string | null): void {
    this.channelId = channelId;
    this.threadRootId = threadRootId;
  }

  resetPhase(): void {
    this.phase = null;
  }

  /** 管理器喂入一个 turn 前调用：标记 turn 进行中（init→idle 抑制窗口）。 */
  beginTurn(): void {
    this.turnActive = true;
  }

  /** 一次 spawn 的运行态复位（confirmed/turn 标记；sessionId 保留）。 */
  resetRun(): void {
    this.confirmed = false;
    this.turnActive = false;
    this.phase = null;
  }

  // ------------------------------------------------------------ 分发

  /** 路由一帧（防腐：任何未知/畸形帧不得抛出到调用方）。 */
  async process(frame: JsonObject): Promise<void> {
    const ftype = frame['type'];
    if (ftype === 'system') {
      await this.onSystem(frame);
    } else if (ftype === 'stream_event') {
      await this.onStreamEvent(frame);
    } else if (ftype === 'assistant') {
      await this.onAssistant(frame);
    } else if (ftype === 'user') {
      await this.onUser(frame);
    } else if (ftype === 'result') {
      await this.onResult(frame);
    } else {
      // py str(None)="None" ↔ TS String(undefined)="undefined"：未知键的字面拼写随宿主语言（登记差异）
      this.countUnknown(String(ftype));
    }
  }

  // ------------------------------------------------------------ system（init → idle）

  private async onSystem(frame: JsonObject): Promise<void> {
    const subtype = frame['subtype'];
    if (subtype === 'init') {
      const sid = frame['session_id'] as string | null | undefined;
      if (sid && sid !== this.sessionId) {
        this.sessionId = sid;
        if (this.onSession !== null) {
          this.onSession(sid); // 会话簿记（daemon/state/<id>.json，§4）
        }
      }
      this.model = (frame['model'] as string | null | undefined) || this.model;
      this.confirmed = true; // resume 生效 / 会话就绪的权威凭据
      if (!this.turnActive) {
        // turn 未在跑（罕见：某些模式 init 先于输入）→ 就绪 idle。
        await this.status('idle');
      }
    } else {
      // system/status | api_retry | notification（契约外噪声）→ 防腐层
      this.countUnknown(`system/${String(subtype)}`);
    }
  }

  // ------------------------------------------------------------ stream_event（相位聚合）

  private async onStreamEvent(frame: JsonObject): Promise<void> {
    const event = (frame['event'] || {}) as JsonObject;
    const etype = event['type'];
    if (etype === 'content_block_start') {
      const block = (event['content_block'] || {}) as JsonObject;
      const btype = block['type'];
      if (btype === 'thinking') {
        await this.switchPhase(P_THINKING);
      } else if (btype === 'text') {
        await this.switchPhase(P_REPLYING);
      } else if (btype === 'tool_use') {
        const name = (block['name'] as string | undefined) || '';
        const bid = block['id'] as string | undefined;
        if (bid) {
          this.toolUses.set(bid, [name, (block['input'] || {}) as JsonObject]);
        }
        await this.switchPhase(phaseForTool(name));
      }
    }
    // message_start / content_block_delta / content_block_stop / message_delta /
    // message_stop：同相位或无相位 → 不上报（§7.2）
  }

  private async switchPhase(phase: string): Promise<void> {
    if (phase === this.phase) {
      return;
    }
    this.phase = phase;
    await this.sink.onActivity(this.agentMemberId, phase);
  }

  // ------------------------------------------------------------ assistant（turn_output 诊断）

  private async onAssistant(frame: JsonObject): Promise<void> {
    const msg = (frame['message'] || {}) as JsonObject;
    const content = (msg['content'] || []) as JsonObject[];
    const textParts: string[] = [];
    let toolCalls = 0;
    for (const block of content) {
      const btype = block['type'];
      if (btype === 'text') {
        textParts.push((block['text'] as string | undefined) || '');
      } else if (btype === 'tool_use') {
        toolCalls += 1;
        const bid = block['id'] as string | undefined;
        if (bid) {
          this.toolUses.set(bid, [
            (block['name'] as string | undefined) || '',
            (block['input'] || {}) as JsonObject,
          ]);
        }
      }
    }
    const preview = textParts.join('').slice(0, TURN_OUTPUT_PREVIEW_MAX);
    this.diag('agent.turn_output', {
      preview, // 正文**不外发**，仅截断留痕（铁律 5）
      tool_calls: toolCalls,
      stop_reason: msg['stop_reason'] ?? null,
    });
  }

  // ------------------------------------------------------------ user（tool_result → 诊断）

  private async onUser(frame: JsonObject): Promise<void> {
    const msg = (frame['message'] || {}) as JsonObject;
    const content = msg['content'] || [];
    if (!Array.isArray(content)) {
      return;
    }
    for (const block of content as unknown[]) {
      if (!isDict(block) || block['type'] !== 'tool_result') {
        continue;
      }
      const tid = String(block['tool_use_id'] || ''); // 缺失/非法 → "" 查默认（帧防腐）
      const [name, toolInput] = this.toolUses.get(tid) ?? ['', {} as JsonObject];
      const isError = Boolean(block['is_error']);
      if (TOOLS_COMMAND.has(name)) {
        const cmd = String(toolInput['command'] ?? '').slice(0, COMMAND_PREVIEW_MAX);
        this.diag('agent.command', { command: cmd, is_error: isError });
      } else if (TOOLS_WRITING.has(name)) {
        const kind = name === 'Write' ? 'create' : 'edit';
        this.diag('agent.file_edit', {
          path: toolInput['file_path'] ?? null,
          kind,
          is_error: isError,
        });
      } else {
        this.diag('agent.tool_call', { tool: name || 'unknown', ok: !isError });
      }
    }
  }

  // ------------------------------------------------------------ result（usage + idle/error）

  private async onResult(frame: JsonObject): Promise<void> {
    const usage = (frame['usage'] || {}) as JsonObject;
    const event: TokenUsageEventIn = {
      id: this.ulid(), // 每 result 帧一个 ULID（exactly-once 去重根基，§7.4）
      agent_member_id: this.agentMemberId,
      channel_id: this.channelId,
      thread_root_id: this.threadRootId,
      input_tokens: asInt(usage['input_tokens']),
      output_tokens: asInt(usage['output_tokens']),
      cache_read_tokens: asInt(usage['cache_read_input_tokens']),
      cache_write_tokens: asInt(usage['cache_creation_input_tokens']),
      source_session: this.sessionId,
      reported_at: this.now(),
    };
    this.sink.onUsage(event); // total_cost_usd / modelUsage.costUSD 忽略（永不折算货币）

    this.confirmed = true;
    this.turnActive = false;
    this.resetPhase();
    this.toolUses.clear();
    const subtype = frame['subtype'];
    const isError = Boolean(frame['is_error']) || Boolean(frame['api_error_status']);
    if (subtype === 'success' && !isError) {
      await this.status('idle');
    } else {
      const detail = String(subtype || frame['api_error_status'] || 'result_error');
      await this.status('error', detail);
    }
  }

  // ------------------------------------------------------------ 防腐层

  private countUnknown(key: string): void {
    const seen = this.unknownCounts[key] ?? 0;
    this.unknownCounts[key] = seen + 1;
    if (seen === 0) {
      // 每种未知类型首现写一条低频诊断，后续静默累加
      this.diag('agent.unknown_frame', { type: key, count: 1 });
    }
  }

  // ------------------------------------------------------------ 回调底座

  private async status(status: AgentStatus, errorDetail: string | null = null): Promise<void> {
    this.lastStatus = status;
    await this.sink.onStatusChanged(this.agentMemberId, status, errorDetail);
  }

  private diag(dtype: string, payload: JsonObject): void {
    this.sink.onDiagnostic({
      agent_member_id: this.agentMemberId,
      type: dtype,
      channel_id: this.channelId,
      // py 侧 pydantic 默认字段（task_id/batch_id=None）显式兑现——model_dump(mode="json")
      // 序列化含 null 键，TS 对象与之逐键对齐（W1 buffer JSONL 形状对等）。
      task_id: null,
      batch_id: null,
      payload,
      at: this.now(),
    });
  }
}
