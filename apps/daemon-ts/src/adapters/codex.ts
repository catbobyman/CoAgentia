/**
 * Codex 适配器（契约 E2 全文落地；第二 runtime，与 claude_code 双实现同构）。
 *
 * - `CodexFrameRouter`：codex app-server **JSON-RPC 通知** → 四类回调映射（防腐层 / 相位聚合 /
 *   usage 提取 / 诊断映射，E2 §5）。纯逻辑、无子进程依赖——可用桩帧全量单测。
 * - `CodexProcess`：**每进程**驱动（base.RuntimeAdapter / E §9）——`codex app-server` 长驻子进程、
 *   JSON-RPC 逐行读写、握手（initialize→initialized→thread/start|resume）、turn/start 提交、
 *   ServerRequest 审批自动应答、CODEX_HOME 隔离 + config.toml MCP 注入。start/stop/feed/
 *   resetSessionArgs。
 *
 * 管理器（`RuntimeManager`，claude_code.ts）按 `boot.runtime` 分派本类 / `ClaudeCodeProcess`——
 * 会话簿记 / 三档重置 / 崩溃熔断 / 去重游标骨架 runtime 无关原样复用（E2 §1.1）。
 *
 * **帧名权威 = 0.144.0 实测校准**（CODEX-CALIBRATION）；冻结的是映射语义与不变量，非方法名。
 * 相位文案单源自 claude `frames.ts`（ACTIVITY_PHRASES 值域，runtime 无关）。
 *
 * 对等基准 = apps/daemon adapters/codex.py。与 py 版的接口差异（逐条登记，非行为改进）：
 * - py 从 claude_code.py import STREAM_LINE_LIMIT / ProcLike / SpawnFn / _safe_wait；TS W3 并行波
 *   文件域隔离（claude_code.ts 由并行代理落地）→ 本模块自持等价定义，W4/W5 收敛单点（挂账）。
 * - py asyncio task.cancel 注入取消 → TS 显式线程化：读循环 / stderr 逐行经 StopSignal 退订式
 *   race 退出（禁止回退成逐行 race 单个长命 stopP，见 StopSignal 注释）；握手 request 仍 race
 *   停止事件（checks.ts/deploy.ts AbortSignal 同族先例）。
 * - py `isinstance(proc, asyncio.subprocess.Process)` 判真进程决定 taskkill → TS 判据 =
 *   `proc instanceof SpawnedCodexProc`（真 spawn 包装类标记；FakeProc 走 terminate() 桩路径）。
 * - py `_run_taskkill`（codex_cmdline.taskkill_argv 自起子进程）→ TS 复用 checks.killProcessTree
 *   单点（体例：杀树单点禁止重发明；code 0/128=幂等成功，其余抛错 → 本处 suppress 对齐 py）。
 * - py asyncio StreamReader limit=STREAM_LINE_LIMIT（超限抛 LimitOverrunError 杀读循环）→ TS
 *   校准条款 2：Buffer 累积字节切行 + CRLF 容忍 + 32MB 自设上限，超限强制切出为一行继续读
 *   （该行必非 JSON → 防腐层 `<non-json>` 计数，不崩读循环）。
 * - py 子进程创建点同步 OSError → node 'error' 事件异步收敛（defaultCodexSpawn 内 race
 *   'spawn'/'error' 还原同步抛出语义）；win32 `.cmd` 拉起 shell:true（校准条款 3，node 22
 *   裸 spawn EINVAL），杀树以壳 pid 为根。
 * - py json.dumps 出帧分隔符含空格（`", "` / `": "`），JSON.stringify 紧凑无空格——消费端按
 *   JSON 解析，语义等价（encoding.ts 同款登记）。
 */

import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';

import type { AgentBoot, AgentStatus, DiagnosticEventIn, TokenUsageEventIn } from '@coagentia/contracts-ts';

import type { AdapterSink } from '../adapter.ts';
import { AsyncEvent, withTimeout } from '../aio.ts';
import { killProcessTree } from '../checks.ts';
import { getLogger } from '../logconfig.ts';
import type { JsonObject } from '../protocol.ts';
import { newUlid, nowIso } from '../util.ts';
import { DAEMON_VERSION } from '../version.ts';
import * as cmdline from './cmdline.ts';
import * as codexCmdline from './codex_cmdline.ts';
import {
  P_BROWSING,
  P_COMMAND,
  P_READING,
  P_REPLYING,
  P_SUBAGENT,
  P_THINKING,
  P_USING,
  P_WRITING,
  shortTool,
} from './frames.ts';
import type { DataPaths } from '../paths.ts';

const HANDSHAKE_TIMEOUT_MS = 60_000; // initialize / thread.* 应答上限（慢启不误杀，坏进程走熔断）
const STOP_GRACE_MS = 5_000; // 杀树后等待退出上限

// daemon 文件日志（B-4 可观测性）：codex app-server JSON-RPC 帧收发/握手/生命周期落 daemon.log。
// 帧原文在 DEBUG（挂死排查设 COAGENTIA_DAEMON_LOG_LEVEL=DEBUG）；生命周期里程碑在 INFO。
const log = getLogger('coagentia_daemon.adapters.codex');
const FRAME_PREVIEW = 600; // 帧原文 DEBUG 预览截断（避免超长 turn 输入/输出灌爆日志）

// asyncio StreamReader 逐行缓冲上限的 TS 对应物（B-4 根因）：py 侧 claude_code.STREAM_LINE_LIMIT
// 两 runtime 共用；TS 侧 node 无 limit 参数，校准条款 2 的行读法以本常量为自设上限（32MB 覆盖
// 现实帧尺寸：codex thread/resume 重放会话历史、大工具输出/大 reasoning 单帧可远超 64KB）。
// W3 并行波文件域隔离 → 本模块自持；claude_code.ts 落地后 W4/W5 收敛单点（挂账，见文件头）。
export const STREAM_LINE_LIMIT = 32 * 1024 * 1024;

/** 收帧的紧凑摘要（method/id/类别）——即使 DEBUG 关，INFO 也能看清帧序。 */
// py `_frame_summary` 同席位（py 侧当前无调用点，原样保留不外销）。
// eslint-disable-next-line @typescript-eslint/no-unused-vars
function frameSummary(frame: JsonObject): string {
  if ('method' in frame) {
    const kind = frame['id'] !== null && frame['id'] !== undefined ? 'srvreq' : 'notif';
    return `${kind} ${String(frame['method'])} id=${String(frame['id'])}`;
  }
  return `resp id=${String(frame['id'])} ${'error' in frame ? 'error' : 'ok'}`;
}

function isDict(v: unknown): v is JsonObject {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

/** py `int(x or 0)` 对等：None/缺失/假值 → 0；数值截断取整（非法值落 0 不抛，帧防腐同族）。 */
function asInt(v: unknown): number {
  const n = Number(v ?? 0);
  return Number.isFinite(n) ? Math.trunc(n) : 0;
}

// ThreadItem.type → activity 相位（E2 §5；文案值域 = claude frames 单源）。
const ITEM_PHASE: Record<string, string> = {
  reasoning: P_THINKING,
  plan: P_THINKING,
  agentMessage: P_REPLYING,
  commandExecution: P_COMMAND,
  fileChange: P_WRITING,
  webSearch: P_BROWSING,
  subAgentActivity: P_SUBAGENT,
  // imageView 等只读类归 Reading（本地工作相位聚合，不逐帧）
  imageView: P_READING,
};

// 增量通知 method → 相位（delta 不逐帧上报，相位聚合 ≤6/turn；E2 §5）。
const DELTA_PHASE: Record<string, string> = {
  'item/agentMessage/delta': P_REPLYING,
  'item/plan/delta': P_THINKING,
  'item/reasoning/textDelta': P_THINKING,
  'item/reasoning/summaryTextDelta': P_THINKING,
  'item/commandExecution/outputDelta': P_COMMAND,
  'item/fileChange/outputDelta': P_WRITING,
  'item/fileChange/patchUpdated': P_WRITING,
};

// ServerRequest（server→client，需回应）自动批准载荷（NFR5 无交互式审批；CALIBRATION §6）。
// 即使 approvalPolicy=never 仍可能来 → 保守放行；未知 ServerRequest 回 JSON-RPC error（无法伪造
// 合法 approval 载荷，保守拒绝好过挂死）。
const APPROVAL_RESULTS: Record<string, JsonObject> = {
  execCommandApproval: { decision: 'approved' }, // ReviewDecision
  applyPatchApproval: { decision: 'approved' },
  'item/commandExecution/requestApproval': { decision: 'accept' }, // *ApprovalDecision
  'item/fileChange/requestApproval': { decision: 'accept' },
};

// 契约内已知但无回调映射的生命周期通知（静默忽略，不计防腐——避免例行帧刷屏计数）。
const IGNORED_NOTIFICATIONS: ReadonlySet<string> = new Set([
  'thread/status/changed',
  'thread/name/updated',
  'thread/goal/updated',
  'thread/goal/cleared',
  'thread/settings/updated',
  'turn/diff/updated',
  'turn/plan/updated',
  'item/commandExecution/terminalInteraction',
  'item/mcpToolCall/progress',
  'item/reasoning/summaryPartAdded',
  'mcpServer/startupStatus/updated',
  'mcpServer/oauthLogin/completed',
  'serverRequest/resolved',
  'account/updated',
  'account/rateLimits/updated',
  'remoteControl/status/changed', // 实测 0.144.0 握手/idle 期例行发（非 turn 相关噪声）
  'thread/compacted',
  'warning',
]);

/** JSON-RPC error 响应（握手请求失败 → 触发熔断降级）。 */
export class CodexRpcError extends Error {
  readonly rpcError: unknown;

  constructor(error: unknown) {
    super(String(error));
    this.rpcError = error;
  }
}

/** TurnError / CodexErrorInfo → 简短 error_detail 字符串。 */
export function errorDetail(err: unknown): string {
  if (typeof err === 'string') return err;
  if (isDict(err)) {
    for (const key of ['code', 'message', 'type', 'codexErrorInfo']) {
      const val = err[key];
      if (typeof val === 'string' && val) return val;
      if (isDict(val)) return errorDetail(val);
    }
    // py next(iter(err), "codex_error")：取首键兜底
    const first = Object.keys(err)[0];
    return first !== undefined ? first : 'codex_error';
  }
  return 'codex_error';
}

/** commandExecution.command（string 或 array）→ 截断预览。 */
function commandText(command: unknown): string {
  const text = Array.isArray(command) ? command.map((c) => String(c)).join(' ') : command;
  // py `str(command or "")`：falsy → ''（str(None) 不落入——or 短路）
  return String(text || '').slice(0, 500);
}

function firstChangePath(changes: unknown): unknown {
  if (Array.isArray(changes) && changes.length > 0) {
    const first: unknown = changes[0];
    if (isDict(first)) return first['path'] ?? null; // py first.get("path")
  }
  if (isDict(changes)) {
    // py next(iter(changes), None)：首键兜底
    const first = Object.keys(changes)[0];
    return first !== undefined ? first : null;
  }
  return null;
}

/** mcpToolCall/dynamicToolCall item → 可读工具名（去 mcp__ 前缀，同 claude 文案）。 */
function toolLabel(item: JsonObject): string {
  return shortTool(String(item['tool'] || 'tool'));
}

export interface CodexFrameRouterOptions {
  ulid?: () => string;
  now?: () => string;
  onSession?: ((conversationId: string) => void) | null;
  onTurnEnd?: (() => Promise<void>) | null;
}

/**
 * 单 Agent 的 codex JSON-RPC 通知路由器 → AdapterSink 四回调（E2 §5）。
 *
 * 只处理**通知**（method + params，无 id）；响应与 ServerRequest 由 `CodexProcess` 前置处理。
 * sessionId = conversationId(thread.id)——与 claude router 同名字段，管理器骨架原样复用。
 */
export class CodexFrameRouter {
  readonly agentMemberId: string;
  // 管理器重绑席位（py entry.process.router._sink = _AgentSink 同席位）——public 可写。
  _sink: AdapterSink;
  private readonly ulid: () => string;
  private readonly now: () => string;
  private readonly onSession: ((conversationId: string) => void) | null;
  private readonly onTurnEnd: (() => Promise<void>) | null;

  // 会话簿记（sessionId = conversationId）
  sessionId: string | null = null;
  // 相位聚合
  private phase: string | null = null;
  // 会话已确认（thread 就绪）——resume 是否生效的判据（管理器降级用）。
  confirmed = false;
  // 本 turn 最近一次 token 用量增量（turn/completed 时提取恰一条，防多次 update 重复计）。
  private lastUsage: JsonObject | null = null;
  // 防腐层计数（lifetime）
  unknownCounts: Record<string, number> = {};
  // turn 归属提示
  channelId: string | null = null;
  threadRootId: string | null = null;
  lastStatus: AgentStatus | null = null;

  constructor(agentMemberId: string, sink: AdapterSink, opts: CodexFrameRouterOptions = {}) {
    this.agentMemberId = agentMemberId;
    this._sink = sink;
    this.ulid = opts.ulid ?? newUlid;
    this.now = opts.now ?? nowIso;
    this.onSession = opts.onSession ?? null;
    this.onTurnEnd = opts.onTurnEnd ?? null;
  }

  // -------------------------------------------------------- 管理器接口（claude router 同构）

  setTurnContext(channelId: string | null, threadRootId: string | null): void {
    this.channelId = channelId;
    this.threadRootId = threadRootId;
  }

  resetPhase(): void {
    this.phase = null;
  }

  /** 提交 turn 前调用（接口对齐 claude router；codex 无 init→idle 竞态需抑制）。 */
  beginTurn(): void {
    this.lastUsage = null;
  }

  /** 一次 spawn 的运行态复位（confirmed/phase/usage；sessionId 保留）。 */
  resetRun(): void {
    this.confirmed = false;
    this.phase = null;
    this.lastUsage = null;
  }

  /** thread/started（响应或通知）→ 记 conversationId + 确认就绪。 */
  setConversation(conversationId: string | null): void {
    if (conversationId && conversationId !== this.sessionId) {
      this.sessionId = conversationId;
      if (this.onSession !== null) {
        this.onSession(conversationId);
      }
    }
    this.confirmed = true;
  }

  // -------------------------------------------------------- 分发（防腐：任何畸形帧不外抛）

  async process(frame: JsonObject): Promise<void> {
    const method = frame['method'];
    const rawParams = frame['params'];
    const params: JsonObject = isDict(rawParams) ? rawParams : {};
    if (method === 'thread/started') {
      this.onThreadStarted(params);
    } else if (method === 'turn/started') {
      await this.status('busy');
    } else if (method === 'turn/completed') {
      await this.onTurnCompleted(params);
    } else if (method === 'item/started') {
      await this.onItemStarted(params);
    } else if (method === 'item/completed') {
      await this.onItemCompleted(params);
    } else if (typeof method === 'string' && method in DELTA_PHASE) {
      await this.switchPhase(DELTA_PHASE[method]!);
    } else if (method === 'thread/tokenUsage/updated') {
      this.onTokenUsage(params);
    } else if (method === 'error') {
      await this.onError(params);
    } else if (typeof method === 'string' && IGNORED_NOTIFICATIONS.has(method)) {
      return; // 契约内已知但无映射的生命周期噪声 → 静默忽略（不计防腐）
    } else {
      // py str(None)="None" ↔ TS String(undefined)="undefined"：未知键字面拼写随宿主语言（登记差异）
      this.countUnknown(String(method));
    }
  }

  // -------------------------------------------------------- thread / turn

  private onThreadStarted(params: JsonObject): void {
    const thread = params['thread'];
    const cid = isDict(thread) ? thread['id'] : null;
    this.setConversation(typeof cid === 'string' ? cid : null);
  }

  private async onTurnCompleted(params: JsonObject): Promise<void> {
    const rawTurn = params['turn'];
    const turn: JsonObject = isDict(rawTurn) ? rawTurn : {};
    this.emitUsage(); // 本 turn 提取恰一条 usage（若有增量）
    this.resetPhase();
    this.confirmed = true;
    const status = turn['status'];
    log.info(`codex[${this.agentMemberId}] turn/completed status=${String(status)}`);
    if (status === 'failed') {
      await this.status('error', errorDetail(turn['error']));
    } else {
      await this.status('idle'); // completed / interrupted
    }
    await this.releaseTurn();
  }

  private async onError(params: JsonObject): Promise<void> {
    if (params['willRetry']) {
      log.info(
        `codex[${this.agentMemberId}] error willRetry (transient): ${errorDetail(params['error'])}`,
      );
      return; // 瞬态：codex 内部重试，turn 未终结
    }
    log.warn(`codex[${this.agentMemberId}] error (turn failed): ${errorDetail(params['error'])}`);
    await this.status('error', errorDetail(params['error']));
    this.resetPhase();
    await this.releaseTurn();
  }

  private async releaseTurn(): Promise<void> {
    if (this.onTurnEnd !== null) {
      await this.onTurnEnd();
    }
  }

  // -------------------------------------------------------- item（相位 + 诊断）

  private async onItemStarted(params: JsonObject): Promise<void> {
    const item = params['item'];
    if (!isDict(item)) {
      return;
    }
    // 挂死排查：turn 内的 item 序列是核心线索（末个 started 无 completed = 卡在该 item）。
    log.info(`codex[${this.agentMemberId}] item/started type=${String(item['type'])}`);
    const phase = this.phaseForItem(item);
    if (phase !== null) {
      await this.switchPhase(phase);
    }
  }

  private async onItemCompleted(params: JsonObject): Promise<void> {
    const item = params['item'];
    if (!isDict(item)) {
      return;
    }
    const itype = item['type'];
    log.info(`codex[${this.agentMemberId}] item/completed type=${String(itype)}`);
    if (itype === 'commandExecution') {
      const exitCode = item['exitCode'];
      const isError =
        item['status'] === 'failed' ||
        (exitCode !== null && exitCode !== undefined && asInt(exitCode) !== 0);
      this.diag('agent.command', { command: commandText(item['command']), is_error: isError });
    } else if (itype === 'fileChange') {
      this.diag('agent.file_edit', {
        path: firstChangePath(item['changes']),
        kind: 'edit',
        is_error: item['status'] === 'failed',
      });
    } else if (itype === 'mcpToolCall' || itype === 'dynamicToolCall') {
      this.diag('agent.tool_call', { tool: toolLabel(item), ok: item['status'] !== 'failed' });
    }
  }

  private phaseForItem(item: JsonObject): string | null {
    const itype = item['type'];
    if (itype === 'mcpToolCall' || itype === 'dynamicToolCall') {
      return P_USING.replace('{tool}', toolLabel(item));
    }
    return ITEM_PHASE[String(itype)] ?? null;
  }

  private async switchPhase(phase: string): Promise<void> {
    if (phase === this.phase) {
      return; // 同相位 delta 不上报（聚合，§5）
    }
    this.phase = phase;
    await this._sink.onActivity(this.agentMemberId, phase);
  }

  // -------------------------------------------------------- usage（E2 §7）

  private onTokenUsage(params: JsonObject): void {
    const usage = params['tokenUsage'];
    const last = isDict(usage) ? usage['last'] : null;
    if (isDict(last)) {
      this.lastUsage = last; // 缓存本 turn 最新增量，turn/completed 时提取恰一条
    }
  }

  private emitUsage(): void {
    const last = this.lastUsage;
    if (!isDict(last)) {
      return;
    }
    this.lastUsage = null;
    const event: TokenUsageEventIn = {
      id: this.ulid(), // 适配器 ULID（exactly-once 去重根基）
      agent_member_id: this.agentMemberId,
      channel_id: this.channelId,
      thread_root_id: this.threadRootId,
      input_tokens: asInt(last['inputTokens']),
      output_tokens: asInt(last['outputTokens']),
      cache_read_tokens: asInt(last['cachedInputTokens']),
      cache_write_tokens: 0, // codex 无独立 cache creation 字段（E2 §7）
      source_session: this.sessionId, // conversationId
      reported_at: this.now(),
    };
    this._sink.onUsage(event);
  }

  // -------------------------------------------------------- 防腐 / 回调底座

  /** 未知帧计数（CodexProcess 的 non-json/non-dict 分支复用；py router._count_unknown 对等）。 */
  countUnknown(key: string): void {
    const seen = this.unknownCounts[key] ?? 0;
    this.unknownCounts[key] = seen + 1;
    if (seen === 0) {
      // 每种未知类型首现一条低频诊断，后续静默累加
      log.info(`codex[${this.agentMemberId}] unknown frame type=${key}`);
      this.diag('agent.unknown_frame', { type: key, count: 1 });
    }
  }

  private async status(status: AgentStatus, errorDetailText: string | null = null): Promise<void> {
    this.lastStatus = status;
    await this._sink.onStatusChanged(this.agentMemberId, status, errorDetailText);
  }

  private diag(dtype: string, payload: JsonObject): void {
    this._sink.onDiagnostic({
      agent_member_id: this.agentMemberId,
      type: dtype,
      channel_id: this.channelId,
      // py pydantic 默认字段（task_id/batch_id=None）显式兑现（frames.ts 同款，JSONL 形状对等）。
      task_id: null,
      batch_id: null,
      payload,
      at: this.now(),
    });
  }
}

// ============================================================ 子进程接口（py claude_code.ProcLike 对等）

export interface CodexProcStdin {
  write(data: Buffer | string): void;
  drain?(): Promise<void>;
  close(): void;
}

export interface CodexProcStdout {
  readline(): Promise<Buffer>;
}

/** asyncio 子进程的最小接口（可注入桩；对齐 tests/adapter_helpers.ts FakeProc 结构面）。 */
export interface ProcLike {
  stdin: CodexProcStdin | null;
  stdout: CodexProcStdout;
  stderr?: CodexProcStdout | null;
  returncode: number | null;
  pid?: number | null;
  terminate(): void;
  kill(): void;
  wait(): Promise<number>;
}

export type SpawnFn = (argv: string[], cwd: string, env: Record<string, string>) => Promise<ProcLike>;

/** py `_safe_wait` 对等：wait 异常时回退 returncode。 */
async function safeWait(proc: ProcLike): Promise<number | null> {
  try {
    return await proc.wait();
  } catch {
    return proc.returncode;
  }
}

/** JSON-RPC 请求 Future（py asyncio.Future 对等：done 判据 + set_result/set_exception 幂等守卫）。 */
class PendingRequest {
  settled = false;
  readonly promise: Promise<JsonObject>;
  private resolveFn!: (v: JsonObject) => void;
  private rejectFn!: (e: Error) => void;

  constructor() {
    this.promise = new Promise<JsonObject>((res, rej) => {
      this.resolveFn = res;
      this.rejectFn = rej;
    });
    // 预挂接消费（教训：detached promise 必挂 catch 防 unhandledRejection）；
    // 后续 await 已 reject 的 promise 仍会正常抛出。
    this.promise.catch(() => {});
  }

  resolve(v: JsonObject): void {
    if (!this.settled) {
      this.settled = true;
      this.resolveFn(v);
    }
  }

  reject(e: Error): void {
    if (!this.settled) {
      this.settled = true;
      this.rejectFn(e);
    }
  }
}

export interface CodexProcessOptions {
  serverUrl: string;
  apiKey: string;
  spawn?: SpawnFn | null;
  onExit?: ((agentMemberId: string, returncode: number | null) => Promise<void>) | null;
  ulid?: () => string;
  now?: () => string;
}

/** 读循环 race 停止事件的哨兵（py task.cancel 的 TS 线程化）。 */
const STOPPED = Symbol('codex-stopped');

/**
 * 停止信号（读循环/stderr 专用）：race() 停止先至返回 STOPPED 哨兵，任一落定即退订等待者。
 * 禁止回退成「逐行 race 单个长命 stopP」——每次 race 都在该 promise 的 reaction 列表追加
 * **永久**记录并经胜果钉住行 Buffer（读循环热路径 → 会话内无界增长；claude CancelSignal 同款）。
 */
class StopSignal {
  private fired = false;
  private readonly waiters = new Set<() => void>();

  set(): void {
    if (this.fired) return;
    this.fired = true;
    const ws = [...this.waiters];
    this.waiters.clear();
    for (const w of ws) w();
  }

  /** p 与停止信号竞速：已停/停止先至 → STOPPED；任一落定即退订，不留逐行残留。 */
  async race<T>(p: Promise<T>): Promise<T | typeof STOPPED> {
    if (this.fired) {
      p.catch(() => {}); // 仍消费 p（对齐原 race 全路径订阅语义，杜绝潜在 unhandledRejection）
      return STOPPED;
    }
    let unsubscribe!: () => void;
    const stopP = new Promise<typeof STOPPED>((resolve) => {
      const w = (): void => resolve(STOPPED);
      this.waiters.add(w);
      unsubscribe = (): void => {
        this.waiters.delete(w);
      };
    });
    try {
      return await Promise.race([p, stopP]);
    } finally {
      unsubscribe();
    }
  }
}

/** 单 Agent 的 codex app-server 子进程驱动（base.RuntimeAdapter / E §9）。 */
export class CodexProcess {
  readonly agentMemberId: string;
  // 管理器重绑席位（py entry.process._sink = _AgentSink 同席位）——public 可写。
  _sink: AdapterSink;
  private readonly _paths: DataPaths;
  private readonly _serverUrl: string;
  private readonly _apiKey: string;
  private readonly _spawn: SpawnFn;
  private readonly _onExit:
    | ((agentMemberId: string, returncode: number | null) => Promise<void>)
    | null;
  private readonly _now: () => string;
  readonly router: CodexFrameRouter;

  private _proc: ProcLike | null = null;
  private _readerTask: Promise<void> | null = null;
  private _stderrTask: Promise<void> | null = null;
  private _handshakeTask: Promise<void> | null = null;
  stderrTail: string[] = []; // py deque(maxlen=50)
  pid: number | null = null;
  private _codexHome: string | null = null;
  // JSON-RPC 请求簿记
  private _pending = new Map<number, PendingRequest>();
  private _nextId = 1;
  // turn 串行队列（app-server 并发未定 → 内部串行提交兜底，E2 §4）
  private _threadId: string | null = null;
  private _turnQueue: Array<[string, string | null, string | null]> = [];
  private _turnInFlight = false;
  // 停止事件（py task.cancel 的 TS 对应物：握手 request 在 await 点 race 退出）
  private _stopping = false;
  private _stopEvent = new AsyncEvent();
  // 读循环/stderr 的停止信号（退订式 race；与 _stopEvent 同时机置位/换新）
  private _stopSignal = new StopSignal();

  constructor(agentMemberId: string, sink: AdapterSink, paths: DataPaths, opts: CodexProcessOptions) {
    this.agentMemberId = agentMemberId;
    this._sink = sink;
    this._paths = paths;
    this._serverUrl = opts.serverUrl;
    this._apiKey = opts.apiKey;
    this._spawn = opts.spawn ?? defaultCodexSpawn;
    this._onExit = opts.onExit ?? null;
    this._now = opts.now ?? nowIso;
    this.router = new CodexFrameRouter(agentMemberId, sink, {
      ulid: opts.ulid ?? newUlid,
      now: opts.now ?? nowIso,
      onSession: (cid) => this.persistSession(cid),
      onTurnEnd: () => this.onTurnFinished(),
    });
  }

  // -------------------------------------------------------- 会话簿记（conversation_id）

  private persistSession(conversationId: string): void {
    // 与 claude session_id 并存不互污（同文件同布局，E2 §3）。
    const data = this._paths.readSession(this.agentMemberId);
    data['conversation_id'] = conversationId;
    this._paths.writeSession(this.agentMemberId, data);
  }

  private resumeConversationId(): string | null {
    const cid = this._paths.readSession(this.agentMemberId)['conversation_id'];
    return typeof cid === 'string' && cid ? cid : null;
  }

  /** codex 无 argv 级会话差异（resume 走 thread/resume 方法，非命令行）——恒空。 */
  resetSessionArgs(): string[] {
    return [];
  }

  // -------------------------------------------------------- 生命周期（E §9）

  async start(boot: AgentBoot, resume: boolean): Promise<void> {
    const home = this._paths.ensureAgentHome(this.agentMemberId);
    const codexHome = codexCmdline.isolatedCodexHome(home);
    this._codexHome = codexHome;
    codexCmdline.materializeCredentials(codexHome); // ChatGPT 登录态物化（E2 §2.2）
    codexCmdline.materializeConfig(codexHome, {
      agentMemberId: this.agentMemberId,
      serverUrl: this._serverUrl,
      apiKey: this._apiKey,
    });
    // 复位本次 spawn 运行态
    this.resetRunState();
    const argv = codexCmdline.buildAppServerArgv();
    const env = codexCmdline.buildEnv(home);
    this._proc = await this._spawn(argv, home, env);
    this.pid = this._proc.pid ?? null;
    log.info(`codex[${this.agentMemberId}] spawned pid=${String(this.pid)} resume=${Boolean(resume)}`);
    this._sink.onDiagnostic(this.diag('agent.process_started', { pid: this.pid, resume: Boolean(resume) }));
    this._readerTask = this.readLoop();
    this._readerTask.catch(() => {}); // 读循环内部已兜底；双保险防 unhandledRejection
    if (this._proc.stderr !== null && this._proc.stderr !== undefined) {
      this._stderrTask = this.drainStderr();
      this._stderrTask.catch(() => {});
    }
    // 握手异步进行（不阻塞 start）：initialize→initialized→thread/start|resume。
    // 就绪 idle 由管理器在 start 返回后发（同 claude），confirmed 由 thread 就绪置位。
    this._handshakeTask = this.handshake(boot, resume);
    this._handshakeTask.catch(() => {}); // detached promise 必挂 catch（失败 kill 走熔断，内部已兜底）
  }

  private resetRunState(): void {
    this.router.resetRun();
    this._pending.clear();
    this._nextId = 1;
    this._threadId = null;
    this._turnQueue = [];
    this._turnInFlight = false;
    // stop 后同实例可再 start（py 管理器 _launch 复用 entry.process）→ 停止态复位、事件换新。
    this._stopping = false;
    this._stopEvent = new AsyncEvent();
    this._stopSignal = new StopSignal();
  }

  private async handshake(boot: AgentBoot, resume: boolean): Promise<void> {
    const aid = this.agentMemberId;
    try {
      log.info(`codex[${aid}] handshake: initialize`);
      await this.request('initialize', {
        clientInfo: { name: 'coagentia', version: DAEMON_VERSION },
      });
      await this.notify('initialized');
      const cid = resume ? this.resumeConversationId() : null;
      const opts = this.threadOpts(boot);
      let resp: JsonObject;
      if (cid) {
        log.info(`codex[${aid}] handshake: thread/resume threadId=${cid}`);
        resp = await this.request('thread/resume', { threadId: cid, ...opts });
      } else {
        log.info(`codex[${aid}] handshake: thread/start`);
        resp = await this.request('thread/start', opts);
      }
      const thread = resp['thread'];
      const tid = isDict(thread) ? thread['id'] : null;
      if (typeof tid === 'string' && tid) {
        log.info(`codex[${aid}] handshake ready: thread=${tid}`);
        this.router.setConversation(tid);
        this._threadId = tid;
        await this.maybeSubmitNextTurn(); // 排空握手前排队的投递
      } else {
        log.warn(`codex[${aid}] handshake: no thread id in response → abort`);
        await this.abortProcess(); // 无 thread → 视作握手失败，走熔断
      }
    } catch (exc) {
      if (this._stopping) {
        return; // 对等 py CancelledError → raise（stop 主动取消，静默退出不触发熔断）
      }
      // 握手任何失败 → 杀进程触发 on_exit + 熔断降级
      log.warn(`codex[${aid}] handshake failed: ${String(exc)} → abort`);
      await this.abortProcess();
    }
  }

  /** thread/start|resume 公共参数（NFR5 权限姿态 + 身份注入，E2 §1.3/§2.4）。 */
  private threadOpts(boot: AgentBoot): JsonObject {
    return {
      cwd: boot.home_path,
      sandbox: 'danger-full-access',
      approvalPolicy: 'never',
      model: boot.model,
      // 身份注入文案 = claude --append-system-prompt 同源（contracts 单点，E2 §2.4）。
      developerInstructions: cmdline.buildIdentityPrompt(boot),
    };
  }

  private async abortProcess(): Promise<void> {
    const proc = this._proc;
    if (proc === null) {
      return;
    }
    try {
      proc.kill(); // → 读循环 EOF → on_exit → 熔断降级（resume 未确认则 session_lost 冷启）
    } catch {
      // py contextlib.suppress(Exception) 对齐
    }
  }

  // -------------------------------------------------------- 读循环 / JSON-RPC

  private async readLoop(): Promise<void> {
    const proc = this._proc;
    if (proc === null) {
      return;
    }
    const stdout = proc.stdout;
    const stopSignal = this._stopSignal; // 本次 spawn 的信号（restart 换新，同 stopP 绑定时机）
    try {
      while (true) {
        const line = await stopSignal.race(stdout.readline());
        if (line === STOPPED) {
          return; // 对等 py CancelledError re-raise：stop() 主动取消 → 不触发退出回调
        }
        if (line.length === 0) {
          break;
        }
        await this.onLine(line);
      }
    } catch (exc) {
      // 读循环内异常视作进程终结
      log.warn(`codex[${this.agentMemberId}] read loop error: ${String(exc)}`);
    }
    this.failPending();
    const returncode = await safeWait(proc);
    // 挂死排查关键：stdout EOF = codex 自己退了；若 turn 期间无端 EOF，末尾 stderr 是线索。
    log.info(
      `codex[${this.agentMemberId}] read loop ended (stdout EOF), returncode=${String(returncode)}; ` +
        `stderr_tail=${JSON.stringify(this.stderrTail.slice(-5))}`,
    );
    if (this._onExit !== null) {
      await this._onExit(this.agentMemberId, returncode);
    }
  }

  private async drainStderr(): Promise<void> {
    const stderr = this._proc?.stderr;
    if (stderr === null || stderr === undefined) {
      return;
    }
    const stopSignal = this._stopSignal;
    try {
      while (true) {
        const line = await stopSignal.race(stderr.readline());
        if (line === STOPPED || line.length === 0) {
          return;
        }
        const text = line.toString('utf-8').trimEnd(); // py decode(replace).rstrip() 对等
        if (text) {
          this.stderrTail.push(text);
          if (this.stderrTail.length > 50) {
            this.stderrTail.shift(); // py deque(maxlen=50)
          }
          // codex app-server 的 stderr 是挂死排查金矿（node 报错/栈/超时都在此）。
          log.info(`codex[${this.agentMemberId}] stderr: ${text}`);
        }
      }
    } catch {
      return; // py except (CancelledError, Exception): return 对等
    }
  }

  private async onLine(line: Buffer | string): Promise<void> {
    const text = (typeof line === 'string' ? line : line.toString('utf-8')).trim();
    if (!text) {
      return;
    }
    log.debug(`codex[${this.agentMemberId}] recv: ${text.slice(0, FRAME_PREVIEW)}`);
    let frame: unknown;
    try {
      frame = JSON.parse(text);
    } catch {
      log.warn(`codex[${this.agentMemberId}] recv non-json: ${text.slice(0, 200)}`);
      this.router.countUnknown('<non-json>');
      return;
    }
    if (!isDict(frame)) {
      this.router.countUnknown('<non-dict>');
      return;
    }
    if ('method' in frame) {
      if (frame['id'] !== null && frame['id'] !== undefined) {
        await this.handleServerRequest(frame); // id + method = ServerRequest
      } else {
        await this.router.process(frame); // 通知
      }
    } else if (frame['id'] !== null && frame['id'] !== undefined) {
      this.resolveResponse(frame); // id + result/error = 请求响应
    } else {
      this.router.countUnknown('<no-method-no-id>');
    }
  }

  private resolveResponse(frame: JsonObject): void {
    const rid = frame['id'];
    const fut =
      typeof rid === 'number' && Number.isInteger(rid) ? this._pending.get(rid) : undefined;
    if (fut === undefined || fut.settled) {
      return; // 无主响应（如 turn/start 的 fire-and-forget）→ 忽略
    }
    if ('error' in frame) {
      fut.reject(new CodexRpcError(frame['error']));
    } else {
      const result = frame['result'];
      fut.resolve(isDict(result) ? result : {});
    }
  }

  private async handleServerRequest(frame: JsonObject): Promise<void> {
    const rid = frame['id'];
    const method = String(frame['method']);
    const result = APPROVAL_RESULTS[method];
    if (result !== undefined) {
      log.info(
        `codex[${this.agentMemberId}] serverRequest ${method} id=${String(rid)} → auto-approve`,
      );
      await this.writeMessage({ id: rid, result });
      return;
    }
    // 未知 ServerRequest：无法伪造合法 approval 载荷 → 保守回 error（好过挂死）。
    // 挂死疑点：codex 若在等一个我们回了 error 的 serverRequest，此处即线索。
    log.warn(
      `codex[${this.agentMemberId}] serverRequest ${method} id=${String(rid)} → reject (unsupported)`,
    );
    await this.writeMessage({
      id: rid,
      error: { code: -32601, message: 'unsupported server request' },
    });
    this.router.countUnknown(`serverRequest/${method}`);
  }

  private failPending(): void {
    for (const fut of this._pending.values()) {
      fut.reject(new CodexRpcError('process exited'));
    }
    this._pending.clear();
  }

  private async request(
    method: string,
    params: JsonObject,
    timeoutMs: number = HANDSHAKE_TIMEOUT_MS,
  ): Promise<JsonObject> {
    const rid = this._nextId;
    this._nextId += 1;
    const fut = new PendingRequest();
    this._pending.set(rid, fut);
    // py task.cancel 在任意 await 点注入；TS 对应 = 停止事件 race（stop() 期间的握手请求立即失败）。
    const stopP: Promise<never> = this._stopEvent.wait().then(() => {
      throw new CodexRpcError('process stopped');
    });
    stopP.catch(() => {});
    try {
      await this.writeMessage({ id: rid, method, params });
      return await withTimeout(Promise.race([fut.promise, stopP]), timeoutMs);
    } finally {
      this._pending.delete(rid);
    }
  }

  private async notify(method: string, params?: JsonObject): Promise<void> {
    const msg: JsonObject = { method };
    if (params !== undefined) {
      msg['params'] = params;
    }
    await this.writeMessage(msg);
  }

  private async writeMessage(obj: JsonObject): Promise<void> {
    const proc = this._proc;
    if (proc === null || proc.stdin === null) {
      throw new Error('process not running');
    }
    const payload = JSON.stringify(obj); // 非 ASCII 不转义（≡ py ensure_ascii=False）
    log.debug(`codex[${this.agentMemberId}] send: ${payload.slice(0, FRAME_PREVIEW)}`);
    proc.stdin.write(Buffer.from(payload + '\n', 'utf-8'));
    if (proc.stdin.drain !== undefined) {
      await proc.stdin.drain();
    }
  }

  // -------------------------------------------------------- turn 提交（E §6 / E2 §4）

  /** 写入一个 turn（§6.4：发出即 ack）。thread 未就绪则入队，握手后排空。 */
  async feed(text: string): Promise<void> {
    if (this._codexHome !== null) {
      codexCmdline.materializeCredentials(this._codexHome); // 投递前刷新凭证自愈
    }
    this._turnQueue.push([text, this.router.channelId, this.router.threadRootId]);
    await this.maybeSubmitNextTurn();
  }

  private async maybeSubmitNextTurn(): Promise<void> {
    if (this._threadId === null || this._turnInFlight || this._turnQueue.length === 0) {
      return;
    }
    const [text, channelId, threadRootId] = this._turnQueue.shift()!;
    this.router.setTurnContext(channelId, threadRootId);
    this._turnInFlight = true;
    this.router.beginTurn();
    // turn/start 是 fire-and-forget 请求：响应可能延至 turn 结束，ack=发出即 ack（E2 §4）。
    const rid = this._nextId;
    this._nextId += 1;
    log.info(
      `codex[${this.agentMemberId}] turn/start submitted (input_len=${text.length}, ` +
        `queue_left=${this._turnQueue.length})`,
    );
    await this.writeMessage({
      id: rid,
      method: 'turn/start',
      params: {
        threadId: this._threadId,
        input: [{ type: 'text', text }],
      },
    });
  }

  private async onTurnFinished(): Promise<void> {
    this._turnInFlight = false;
    await this.maybeSubmitNextTurn();
  }

  setTurnContext(channelId: string | null, threadRootId: string | null): void {
    this.router.setTurnContext(channelId, threadRootId);
  }

  // -------------------------------------------------------- stop

  /** 关 stdin → 杀进程树（win32 taskkill /F /T；app-server 不响应 stdin 关闭，E2 §1.2）。 */
  async stop(): Promise<void> {
    const proc = this._proc;
    if (proc === null) {
      return;
    }
    // 对等 py 三任务 task.cancel()：停止事件线程化，读循环/stderr/握手在 await 点退出（先停任务）。
    this._stopping = true;
    this._stopEvent.set();
    this._stopSignal.set();
    try {
      if (proc.stdin !== null) {
        proc.stdin.close();
      }
    } catch {
      // py contextlib.suppress(Exception) 对齐
    }
    await this.terminateTree(proc);
    for (const task of [this._handshakeTask, this._readerTask, this._stderrTask]) {
      if (task !== null) {
        try {
          await task; // 停止事件已触发 → 各任务在 race 点收敛，不死等
        } catch {
          // py suppress(CancelledError, Exception) 对齐
        }
      }
    }
    this.failPending();
    this._proc = null;
  }

  private async terminateTree(proc: ProcLike): Promise<void> {
    if (proc.returncode !== null) {
      return;
    }
    // py isinstance(proc, asyncio.subprocess.Process) 判真进程 → TS 判据 = SpawnedCodexProc 标记
    //（FakeProc 桩走 terminate()；登记差异，见文件头）。
    if (codexCmdline.isWin32() && proc instanceof SpawnedCodexProc && typeof proc.pid === 'number' && proc.pid) {
      try {
        await killProcessTree(proc.pid); // terminate 杀不掉底层 node（E2 §1.2；checks 杀树单点）
      } catch {
        // py _run_taskkill 全程 suppress 对齐（code 0/128 之外的失败走下方 grace/kill 兜底）
      }
    } else {
      try {
        proc.terminate();
      } catch {
        // py contextlib.suppress(Exception) 对齐
      }
    }
    try {
      await withTimeout(proc.wait(), STOP_GRACE_MS);
    } catch {
      // py except (TimeoutError, Exception) 对齐：等不到退出 → kill 兜底
      try {
        proc.kill();
      } catch {
        // py contextlib.suppress(Exception) 对齐
      }
    }
  }

  isRunning(): boolean {
    return this._proc !== null && this._proc.returncode === null;
  }

  private diag(dtype: string, payload: JsonObject): DiagnosticEventIn {
    return {
      agent_member_id: this.agentMemberId,
      type: dtype,
      channel_id: null,
      task_id: null,
      batch_id: null,
      payload,
      at: this._now(),
    };
  }
}

// ============================================================ 真 spawn（py _default_codex_spawn 对等）

/**
 * 校准条款 2 行读法（readline 视图；测试面导出）：Buffer 累积按 `\n` 字节切行（含换行符吐出，
 * py readline 语义）；EOF 残段作末行；EOF 后 readline 恒返回空 Buffer（py b"" 对等）；未终止行
 * 累积超 STREAM_LINE_LIMIT 强制切出为一行继续读（超限帧必非合法 JSON → 防腐层计数，不崩读循环；
 * 区别于 claude 侧丢帧语义，两处各自登记）。未终止段 = chunk 引用数组 + 字节计数，每行恰一次
 * Buffer.concat（claude NodeLineReader 同款；逐 chunk concat 是跨 chunk O(n²) 拷贝——
 * 32MB 上限 ⇒ ~8GB memcpy，禁止回退）。
 */
export class LineReader implements CodexProcStdout {
  private lines: Buffer[] = [];
  private waiters: Array<(b: Buffer) => void> = [];
  private parts: Buffer[] = [];
  private partBytes = 0;
  private closed = false;

  private readonly maxLineBytes: number;

  constructor(maxLineBytes: number = STREAM_LINE_LIMIT) {
    this.maxLineBytes = maxLineBytes;
  }

  push(chunk: Buffer): void {
    let start = 0;
    for (let i = 0; i < chunk.length; i += 1) {
      if (chunk[i] === 0x0a) {
        this.completeLine(chunk.subarray(start, i + 1));
        start = i + 1;
      }
    }
    if (start < chunk.length) {
      this.parts.push(chunk.subarray(start));
      this.partBytes += chunk.length - start;
      if (this.partBytes > this.maxLineBytes) {
        this.deliver(this.takeParts()); // 校准条款 2：超限强制切出，不崩读循环、不无界累积
      }
    }
  }

  /** 完整行收口（含行尾 \n）：无累积段直投视图，有则恰一次 concat。 */
  private completeLine(piece: Buffer): void {
    if (this.parts.length === 0) {
      this.deliver(piece);
      return;
    }
    this.parts.push(piece);
    this.partBytes += piece.length;
    this.deliver(this.takeParts());
  }

  /** 累积段收口：恰一次 Buffer.concat 并清空计数。 */
  private takeParts(): Buffer {
    const line = Buffer.concat(this.parts);
    this.parts = [];
    this.partBytes = 0;
    return line;
  }

  end(): void {
    if (this.closed) {
      return;
    }
    if (this.parts.length > 0) {
      this.deliver(this.takeParts()); // EOF 残段作末行
    }
    this.closed = true;
    for (const w of this.waiters.splice(0)) {
      w(Buffer.alloc(0)); // EOF：唤醒全部挂起读者
    }
  }

  private deliver(line: Buffer): void {
    const w = this.waiters.shift();
    if (w !== undefined) {
      w(line);
    } else {
      this.lines.push(line);
    }
  }

  async readline(): Promise<Buffer> {
    const item = this.lines.shift();
    if (item !== undefined) {
      return item;
    }
    if (this.closed) {
      return Buffer.alloc(0);
    }
    return new Promise<Buffer>((r) => this.waiters.push(r));
  }
}

/** stdin 包装：write 背压 `write()===false → drain 等 'drain'`（校准条款 6）。 */
class RealStdin implements CodexProcStdin {
  private needsDrain = false;

  private readonly stream: NodeJS.WritableStream;

  constructor(stream: NodeJS.WritableStream) {
    this.stream = stream;
    // EPIPE 等写侧错误必须有 handler（未挂 'error' 的流错误 = uncaughtException 崩进程）。
    stream.on('error', () => {});
  }

  write(data: Buffer | string): void {
    this.needsDrain = !this.stream.write(data);
  }

  async drain(): Promise<void> {
    if (!this.needsDrain) {
      return;
    }
    this.needsDrain = false;
    await new Promise<void>((resolve) => {
      const done = (): void => {
        this.stream.removeListener('drain', done);
        this.stream.removeListener('close', done);
        this.stream.removeListener('error', done);
        resolve();
      };
      this.stream.once('drain', done);
      this.stream.once('close', done);
      this.stream.once('error', done);
    });
  }

  close(): void {
    try {
      this.stream.end();
    } catch {
      // 已关闭等：suppress（py stdin.close 同款吞错席位）
    }
  }
}

/**
 * 真 codex app-server 子进程包装（ProcLike 实现）。
 *
 * 校准条款 6：spawn 当拍同步挂 stdout/stderr 消费者（构造函数内），stderr 永不裸放；
 * 生命周期定稿只挂 'close'（end/exit 顺序不定）。本类也是 terminateTree 的「真进程」判据标记
 * （py isinstance(proc, asyncio.subprocess.Process) 对应位）。
 */
export class SpawnedCodexProc implements ProcLike {
  readonly stdin: CodexProcStdin | null;
  readonly stdout: LineReader;
  readonly stderr: LineReader;
  readonly pid: number | null;
  private rc: number | null = null;
  private readonly closedEvent = new AsyncEvent();
  private readonly spawnP: Promise<void>;

  private readonly child: ChildProcess;

  constructor(child: ChildProcess) {
    this.child = child;
    this.stdout = new LineReader();
    this.stderr = new LineReader();
    // 当拍同步挂消费者（cal6：否则子进程死亡残留数据静默丢）。
    child.stdout?.on('data', (chunk: Buffer) => this.stdout.push(chunk));
    child.stderr?.on('data', (chunk: Buffer) => this.stderr.push(chunk));
    this.stdin = child.stdin !== null ? new RealStdin(child.stdin) : null;
    this.pid = child.pid ?? null;
    child.on('exit', (code) => {
      this.rc = code ?? -1; // 信号终止 py 为负值；统一 -1（登记差异）
    });
    child.on('close', () => {
      // 定稿只挂 'close'（cal6）：全部管道收尾后才吐 EOF / 放行 wait。
      this.stdout.end();
      this.stderr.end();
      this.closedEvent.set();
    });
    this.spawnP = new Promise<void>((resolve, reject) => {
      child.once('spawn', () => resolve());
      child.once('error', (err) => reject(err));
    });
    this.spawnP.catch(() => {});
    // 'spawn' 之后的 error（如 kill 失败）不得变成 uncaughtException。
    child.on('error', () => {
      this.stdout.end();
      this.stderr.end();
      this.closedEvent.set();
    });
  }

  get returncode(): number | null {
    return this.rc;
  }

  /** py create_subprocess_exec 同步 OSError 语义还原：spawn 失败在创建点抛出。 */
  async spawned(): Promise<void> {
    await this.spawnP;
  }

  terminate(): void {
    this.child.kill('SIGTERM');
  }

  kill(): void {
    this.child.kill('SIGKILL');
  }

  async wait(): Promise<number> {
    await this.closedEvent.wait();
    return this.rc ?? 0;
  }
}

/** win32 cmd.exe 拉起时的参数引用（路径含空格；校准条款 3 shell:true 通道）。 */
function quoteForShell(s: string): string {
  return /[\s"]/.test(s) ? `"${s.replace(/"/g, '\\"')}"` : s;
}

/**
 * 默认 spawn（py _default_codex_spawn 对等）。
 *
 * - 行缓冲上限：py limit=STREAM_LINE_LIMIT（B-4 根因：默认 64KB 太小，大帧杀读循环致挂死）→
 *   TS 由 LineReader 以同常量为自设上限（node 无 limit 参数，校准条款 2）。
 * - win32 `.cmd`/`.bat`（npm shim）必须 shell:true（node 22 裸 spawn EINVAL，校准条款 3）；
 *   杀树以壳 pid 为根（taskkill /T 覆盖全树）。
 * - spawn 失败：race 'spawn'/'error' 还原 py 创建点同步 OSError 语义。
 */
export async function defaultCodexSpawn(
  argv: string[],
  cwd: string,
  env: Record<string, string>,
): Promise<ProcLike> {
  const [cmd, ...args] = argv;
  const needsShell =
    process.platform === 'win32' && /\.(cmd|bat)$/i.test(cmd ?? '');
  const child = needsShell
    ? spawn([quoteForShell(cmd ?? ''), ...args.map(quoteForShell)].join(' '), {
        cwd,
        env,
        shell: true,
        stdio: ['pipe', 'pipe', 'pipe'],
      })
    : spawn(cmd ?? '', args, { cwd, env, stdio: ['pipe', 'pipe', 'pipe'] });
  const proc = new SpawnedCodexProc(child);
  await proc.spawned();
  return proc;
}
