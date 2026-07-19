/**
 * DaemonClient：契约 D daemon 侧本体（连接生命周期 / 握手 / 指令幂等消费 / 上报 / 缓冲重传）。
 *
 * 一条连接的生命：connect → hello → hello_ack → 并发跑 {reader, heartbeat, flush} → 断连 →
 * 指数退避重连（1s→2s→…→30s 封顶，无限重试）。指令按自然键幂等（委托 adapter），frame_id 短窗
 * 去重为加速器；需 ack 类遥测经 TelemetryBuffer 落盘、断连后重传（ULID 不虚增）。
 *
 * 对等基准 = apps/daemon client.py。py↔TS 结构差异（登记）：
 * - reader/heartbeat/flush 三并发 task 的取消 → 每连接 connClosed 事件旗标 + 循环自查退出
 *   （py task.cancel 注入；TS 无取消注入，等价义务=reader 终结后两循环有界退出）；
 * - shutdown 对在飞 worktree 任务：py cancel 注入（git.py 取消恢复回滚 HEAD）；TS 等其自然
 *   完成（git op 自带 60s 超时上界，超时路径同样回滚），不强杀；
 * - fs.tree 扫描面拆至 src/fsscan.ts（py 内联本文件，零行为变化）。
 */

import * as fs from 'node:fs';
import * as path from 'node:path';

import type {
  AckResult,
  AgentStatus,
  BufferedCounts,
  CheckFinishedData,
  DaemonAgentState,
  DaemonHelloAckData,
  DeployFinishedData,
  DeployLogReportData,
  DetectedRuntime,
  DiagnosticEventIn,
  FrameError,
  GitDiffQuery,
  InstrType,
  PreviewStatusData,
  ReportType,
  TokenUsageEventIn,
  WorktreeScanQuery,
  WorktreeStatusData,
} from '@coagentia/contracts-ts';

import type { AdapterSink, RuntimeAdapter } from './adapter.ts';
import { AsyncEvent, Lock, TimeoutError, sleep, withTimeout } from './aio.ts';
import { TelemetryBuffer } from './buffer.ts';
import { CheckRunner } from './checks.ts';
import { DeployRunner } from './deploy.ts';
import { FS_TREE_MAX, fsDirEntries, fsRootEntries } from './fsscan.ts';
import { DAEMON_PROTOCOL_V } from './generated/constants.ts';
import { GitWorktreeManager, resolvePath } from './git.ts';
import { HANDLERS } from './handlers.ts';
import { getLogger } from './logconfig.ts';
import type { DataPaths } from './paths.ts';
import { PreviewRunner } from './preview.ts';
import type { CommandRunner } from './probe.ts';
import { probeRuntimes } from './probe.ts';
import {
  FRAME_KIND,
  ackFrame,
  pongFrame,
  reportFrame,
} from './protocol.ts';
import type { Transport } from './transport.ts';
import { TransportClosed, wsConnect } from './transport.ts';
import { newUlid } from './util.ts';
import { DAEMON_VERSION } from './version.ts';

const log = getLogger('coagentia_daemon.client');

export const BACKOFF_START = 1.0;
export const BACKOFF_CAP = 30.0;
const DIAG_BATCH = 50; // 契约 D §7：diagnostics ≤50 条/批
const USAGE_BATCH = 500;
const HOME_FILE_MAX = 1024 * 1024; // 契约 D §6：home.file 文本上限 1MB（码点计）
const ACTIVITY_PRETHROTTLE_MS = 250; // 契约 D §7：daemon 可 ≥250ms 预节流省带宽
const FRAME_DEDUP_WINDOW = 2048;
// CHECK_RUN 仍走同步 handler + buffer.findCheck 重放（终态缓冲重传），须留在重放白名单。
const STATUS_REPLAY_INSTRS: ReadonlySet<string> = new Set(['check.run']);
// worktree ensure/merge/cleanup 走后台通道（handleInstr 分流），不经同步短窗去重门；
// 其重放语义由后台任务的自然键幂等重跑提供（重连原帧重发 → 后台重跑 git op 补报终态）。
const BACKGROUND_INSTRS: ReadonlySet<string> = new Set([
  'worktree.ensure',
  'worktree.merge',
  'worktree.cleanup',
]);

export type ConnectFn = (serverUrl: string, apiKey: string) => Promise<Transport>;
export type JsonObject = Record<string, unknown>;

/** 指数退避下一值（契约 D §2：1→2→4→…→30 封顶）。 */
export function nextBackoff(current: number, cap: number = BACKOFF_CAP): number {
  return Math.min(current * 2, cap);
}

export interface DaemonClientOptions {
  serverUrl: string;
  apiKey: string;
  adapter: RuntimeAdapter;
  buffer: TelemetryBuffer;
  paths: DataPaths;
  osName: string;
  arch: string;
  daemonVersion?: string;
  connectFn?: ConnectFn;
  runner?: CommandRunner | null;
  heartbeatSec?: number;
  pongTimeout?: number;
  ackTimeout?: number;
  backoffStart?: number;
  backoffCap?: number;
}

interface PendingAck {
  promise: Promise<JsonObject>;
  resolve: (frame: JsonObject) => void;
  reject: (err: Error) => void;
}

export class DaemonClient implements AdapterSink {
  readonly serverUrl: string;
  readonly apiKey: string;
  readonly adapter: RuntimeAdapter;
  readonly buffer: TelemetryBuffer;
  readonly paths: DataPaths;
  readonly osName: string;
  readonly arch: string;
  readonly daemonVersion: string;
  heartbeatSec: number;
  pongTimeout: number;
  ackTimeout: number;

  readonly boot_nonce: string;
  readonly git: GitWorktreeManager;
  readonly checks: CheckRunner;
  readonly previews: PreviewRunner;
  readonly deploys: DeployRunner;

  readonly connected = new AsyncEvent();
  helloAck: DaemonHelloAckData | null = null;

  _transport: Transport | null = null; // 测试可直接注入（py 侧 client._transport 同款）

  private readonly connectFn: ConnectFn;
  private readonly runner: CommandRunner | null;
  private readonly backoffStart: number;
  private readonly backoffCap: number;
  private readonly sendLock = new Lock();
  private detectedRuntimes: DetectedRuntime[] = [];

  private readonly pongEvent = new AsyncEvent();
  private readonly flushEvent = new AsyncEvent();
  private connClosed = new AsyncEvent(); // 每连接重建：reader 终结 → 心跳/flush 有界退出
  private readonly reportAcks = new Map<string, PendingAck>();
  private readonly recentFrames: string[] = [];
  private readonly recentFrameSet = new Set<string>();
  private readonly activityLast = new Map<string, number>();
  private stopped = false;
  private wasConnected = false;

  // worktree ensure/merge/cleanup 后台通道（#1：解放 reader，避免大 merge 阻塞 PONG 误重连）。
  // 键=frame_id 用于在飞去重 + 生命周期；单车道锁串行执行杜绝同仓并发 git。
  private readonly worktreeTasks = new Map<string, Promise<void>>();
  private readonly worktreeLane = new Lock();
  private worktreeClosing = false;

  constructor(opts: DaemonClientOptions) {
    this.serverUrl = opts.serverUrl;
    this.apiKey = opts.apiKey;
    this.adapter = opts.adapter;
    this.buffer = opts.buffer;
    this.paths = opts.paths;
    this.osName = opts.osName;
    this.arch = opts.arch;
    this.daemonVersion = opts.daemonVersion ?? DAEMON_VERSION;
    this.connectFn = opts.connectFn ?? wsConnect;
    this.runner = opts.runner ?? null;
    this.heartbeatSec = opts.heartbeatSec ?? 25.0;
    this.pongTimeout = opts.pongTimeout ?? 10.0;
    this.ackTimeout = opts.ackTimeout ?? 10.0;
    this.backoffStart = opts.backoffStart ?? BACKOFF_START;
    this.backoffCap = opts.backoffCap ?? BACKOFF_CAP;

    this.adapter.bind(this); // AdapterSink = this
    // 进程级 boot nonce（契约 D §4.1 v1.0.5）：每 DaemonClient 实例（=daemon 进程）一次性生成，
    // 重连不变、重启必变——server 借此区分 WS jitter 与真重启（对账 #9）。
    this.boot_nonce = newUlid();
    this.git = new GitWorktreeManager(opts.paths);
    this.checks = new CheckRunner();
    this.previews = new PreviewRunner();
    this.deploys = new DeployRunner();
  }

  // ---------------------------------------------------------------- 主循环（重连）

  /** 无限重连主循环（契约 D §2：无人值守，永不放弃）。 */
  async run(): Promise<void> {
    let backoff = this.backoffStart;
    while (!this.stopped) {
      let transport: Transport;
      try {
        transport = await this.connectFn(this.serverUrl, this.apiKey);
      } catch (exc) {
        this.logWarn(`connect failed: ${String(exc)}`);
        await sleep(backoff * 1000);
        backoff = nextBackoff(backoff, this.backoffCap);
        continue;
      }
      this.wasConnected = false;
      try {
        await this.serve(transport);
      } catch (exc) {
        if (!(exc instanceof TransportClosed)) this.logWarn(`serve error: ${String(exc)}`);
      } finally {
        await this.safeClose(transport);
        this.connected.clear();
        this._transport = null;
        this.failPending('connection closed');
        // 断连**不杀**活跃预览（契约 D §4.2 v1.0.5，与 Agent 进程同款保持存活）：重连 hello
        // 携 boot_nonce + 预览进程表快照，server 对账 #9 逐会话判活。
      }
      if (this.wasConnected) {
        backoff = this.backoffStart; // 成功连过一轮 → 退避复位
      } else {
        await sleep(backoff * 1000);
        backoff = nextBackoff(backoff, this.backoffCap);
      }
    }
  }

  stop(): void {
    this.stopped = true;
    this.checks.cancel();
    this.previews.cancel();
    this.deploys.cancel();
    this.worktreeClosing = true;
    // py 靠 KeyboardInterrupt 打断 run；TS 主动关传输促使 reader 尽快终结（登记差异）。
    const t = this._transport;
    if (t !== null) void this.safeClose(t);
  }

  /** 停全部 Runner 并等在飞 worktree 任务收尾后再退出（TS 侧不注入取消，等自然完成——git op 自带超时上界）。 */
  async shutdown(): Promise<void> {
    this.stop();
    await this.checks.waitClosed();
    await this.previews.waitClosed();
    await this.deploys.waitClosed();
    await this.waitWorktreesClosed();
  }

  // ---------------------------------------------------------------- 一条连接的服务

  async serve(transport: Transport): Promise<void> {
    this._transport = transport;
    if (this.detectedRuntimes.length === 0) {
      this.detectedRuntimes = await probeRuntimes(this.runner);
    }
    const helloFrameId = await this.sendHello();
    const ack = await transport.recv(); // 握手第 3 步：hello_ack（借 ack 信封，契约 D §4.1）
    this.applyHelloAck(ack, helloFrameId);
    this.wasConnected = true;
    this.connected.set();
    log.info(`connected: hello_ack ok, heartbeat=${this.heartbeatSec}s, boot_nonce=${this.boot_nonce}`);
    this.flushEvent.set(); // 重连即重传离线期缓冲（契约 D §4.1 第 5 步）

    this.connClosed = new AsyncEvent();
    const heartbeat = this.heartbeatLoop(transport);
    const flush = this.flushLoop();
    try {
      await this.readerLoop(transport); // reader 结束/抛 TransportClosed = 连接终结
    } finally {
      this.connClosed.set();
      this.flushEvent.set(); // 唤醒 flush 循环观察 connClosed
      this.pongEvent.set(); // 唤醒心跳等待
      await Promise.allSettled([heartbeat, flush]);
    }
  }

  private async sendHello(): Promise<string> {
    const frame = reportFrame('hello', this.buildHello());
    await this.send(frame);
    return frame['frame_id'] as string;
  }

  /** hello 载荷：真实进程表（adapter）+ 探测 runtime + 缓冲计数 + boot nonce 与预览进程表快照。 */
  buildHello(): JsonObject {
    return {
      daemon_version: this.daemonVersion,
      os: this.osName,
      arch: this.arch,
      detected_runtimes: this.detectedRuntimes,
      agents: this.adapter.processTable() as DaemonAgentState[],
      buffered: this.buffer.counts() as BufferedCounts,
      boot_nonce: this.boot_nonce,
      previews: this.previews.processTable() as PreviewStatusData[],
    };
  }

  private applyHelloAck(ack: JsonObject, helloFrameId: string): void {
    if (ack['kind'] !== FRAME_KIND.ACK || ack['ref'] !== helloFrameId) {
      throw new TransportClosed(`unexpected first frame (expected hello_ack): ${JSON.stringify(ack)}`);
    }
    const data = ack['data'] as DaemonHelloAckData;
    this.helloAck = data;
    if (data.protocol_v !== DAEMON_PROTOCOL_V) {
      throw new TransportClosed(`protocol mismatch: ${data.protocol_v}`);
    }
    this.heartbeatSec = Number(data.heartbeat_sec); // 记 heartbeat_sec（契约 D §2）
  }

  // ---------------------------------------------------------------- 收帧循环 + 分发

  private async readerLoop(transport: Transport): Promise<void> {
    for (;;) {
      const frame = await transport.recv(); // TransportClosed 抛出 = 断连
      await this.dispatch(frame);
    }
  }

  private async dispatch(frame: JsonObject): Promise<void> {
    const kind = frame['kind'];
    if (kind === FRAME_KIND.INSTR) {
      await this.handleInstr(frame);
    } else if (kind === FRAME_KIND.QUERY) {
      await this.handleQuery(frame);
    } else if (kind === FRAME_KIND.ACK) {
      this.resolveReportAck(frame);
    } else if (kind === FRAME_KIND.PONG) {
      this.pongEvent.set();
    } else if (kind === FRAME_KIND.PING) {
      try {
        await this.send(pongFrame());
      } catch (exc) {
        if (!(exc instanceof TransportClosed)) throw exc;
      }
    }
    // reply / report: server→daemon 不发；忽略
  }

  // ---------------------------------------------------------------- 指令消费（契约 D §5）

  async handleInstr(frame: JsonObject): Promise<void> {
    const frameId = frame['frame_id'] as string;
    const itype = frame['type'] as InstrType;
    log.info(`instr recv: ${itype} frame=${frameId}`); // 关联 codex/claude 生命周期
    if (BACKGROUND_INSTRS.has(itype)) {
      // worktree 帧后台化：立即返回让 reader 继续处理 PONG 等帧；ack 仍在 op 完成后由后台任务
      // 发出（保序：handler 先报 worktree.status 再返回 → 任务末尾发 ack），server 零改动。
      this.spawnWorktreeInstr(frame, itype);
      return;
    }
    if (this.recentFrameSet.has(frameId) && !STATUS_REPLAY_INSTRS.has(itype)) {
      // frame_id 短窗去重加速器：原帧重发 → 直接 noop（自然键幂等已保证无副作用）。
      await this.sendAck(frameId, 'noop', null);
      return;
    }
    const handler = HANDLERS[itype];
    if (handler === undefined) {
      await this.sendAck(frameId, 'failed', null);
      return;
    }
    let result: AckResult;
    let error: FrameError | null;
    try {
      [result, error] = await handler(this, (frame['data'] as JsonObject) ?? {});
    } catch (exc) {
      this.logWarn(`instr ${itype} failed: ${String(exc)}`);
      result = 'failed';
      error = { code: 'HANDLER_ERROR', message: exc instanceof Error ? exc.message : String(exc) };
    }
    await this.sendAck(frameId, result, error);
    // 只有 ack 成功写入传输后才做短窗记忆；否则重连重发必须重新走自然键处理器，
    // 让 worktree 等带状态指令能补报终态，而不是只回一个失真的 noop。
    this.rememberFrame(frameId);
  }

  private rememberFrame(frameId: string): void {
    if (this.recentFrames.length === FRAME_DEDUP_WINDOW) {
      this.recentFrameSet.delete(this.recentFrames.shift()!);
    }
    this.recentFrames.push(frameId);
    this.recentFrameSet.add(frameId);
  }

  private async sendAck(ref: string, result: AckResult, error: FrameError | null): Promise<void> {
    const payload = ackFrame(ref, result, error);
    if (error === null) delete payload['error']; // py 仅 error 非空才带键
    await this.send(payload);
  }

  // -------------------------------------------------------- worktree 后台通道（契约 D §5.3；#1）

  /** 后台起 worktree 指令；同 frame_id 在飞则丢弃不补 ack（保序：完成时唯一一次 ack）。 */
  private spawnWorktreeInstr(frame: JsonObject, itype: InstrType): void {
    const frameId = frame['frame_id'] as string;
    if (this.worktreeClosing || this.worktreeTasks.has(frameId)) return;
    const task = this.runWorktreeInstr(frame, itype).finally(() => {
      this.worktreeTasks.delete(frameId);
    });
    task.catch(() => {}); // 后台任务兜底（对等 py done_callback 取异常防 warning）
    this.worktreeTasks.set(frameId, task);
  }

  private async runWorktreeInstr(frame: JsonObject, itype: InstrType): Promise<void> {
    const frameId = frame['frame_id'] as string;
    await this.worktreeLane.runExclusive(async () => {
      // 单车道串行，防同仓并发 git 抢锁
      const handler = HANDLERS[itype]!;
      let result: AckResult;
      let error: FrameError | null;
      try {
        [result, error] = await handler(this, (frame['data'] as JsonObject) ?? {});
      } catch (exc) {
        this.logWarn(`worktree instr ${itype} failed: ${String(exc)}`);
        result = 'failed';
        error = { code: 'HANDLER_ERROR', message: exc instanceof Error ? exc.message : String(exc) };
      }
      // handler 内已先发 worktree.status，返回后此处再发 ack —— status→ack 保序不变。
      try {
        await this.sendAck(frameId, result, error);
        this.rememberFrame(frameId);
      } catch (exc) {
        if (!(exc instanceof TransportClosed)) throw exc;
      }
    });
  }

  private async waitWorktreesClosed(): Promise<void> {
    this.worktreeClosing = true;
    await Promise.allSettled([...this.worktreeTasks.values()]);
  }

  // ---------------------------------------------------------------- 查询代理（契约 D §6）

  async handleQuery(frame: JsonObject): Promise<void> {
    const qtype = frame['type'] as string;
    const data = (frame['data'] as JsonObject) ?? {};
    let reply: unknown;
    try {
      if (qtype === 'home.tree') {
        reply = this.homeTree(data);
      } else if (qtype === 'home.file') {
        reply = this.homeFile(data);
      } else if (qtype === 'git.diff') {
        reply = await this.git.diff(data as unknown as GitDiffQuery);
      } else if (qtype === 'fs.tree') {
        reply = this.fsTree(data);
      } else if (qtype === 'worktree.scan') {
        reply = await this.git.scan(data as unknown as WorktreeScanQuery);
      } else {
        reply = { error: 'unsupported' };
      }
    } catch (exc) {
      reply = { error: exc instanceof Error ? exc.message : String(exc) };
    }
    await this.send({
      v: DAEMON_PROTOCOL_V,
      kind: FRAME_KIND.REPLY,
      ref: frame['frame_id'],
      data: reply,
    });
  }

  private homeTree(data: JsonObject): JsonObject {
    const target = this.safeJoin(data['agent_member_id'] as string, data['path'] as string);
    const entries: JsonObject[] = [];
    if (target !== null && fs.existsSync(target) && fs.statSync(target).isDirectory()) {
      const names = fs.readdirSync(target).sort();
      for (const name of names) {
        const st = fs.statSync(path.join(target, name));
        entries.push({
          name,
          kind: st.isDirectory() ? 'dir' : 'file',
          size_bytes: st.size,
          mtime: isoFromMtime(st.mtimeMs),
        });
      }
    }
    return { entries };
  }

  private homeFile(data: JsonObject): JsonObject {
    const target = this.safeJoin(data['agent_member_id'] as string, data['path'] as string);
    if (target === null || !fs.existsSync(target) || !fs.statSync(target).isFile()) {
      return { kind: 'binary', size_bytes: 0, mime: null };
    }
    const raw = fs.readFileSync(target);
    let text: string;
    try {
      text = new TextDecoder('utf-8', { fatal: true }).decode(raw); // 对等 py 严格 decode
    } catch {
      return { kind: 'binary', size_bytes: raw.length, mime: null };
    }
    // 截断按码点计（对等 py len(text)/text[:max]；UTF-16 长度 ≤ max 必不超码点上限）。
    if (text.length <= HOME_FILE_MAX) {
      return { kind: 'text', content: text, truncated: false };
    }
    const points = Array.from(text);
    const truncated = points.length > HOME_FILE_MAX;
    return {
      kind: 'text',
      content: truncated ? points.slice(0, HOME_FILE_MAX).join('') : text,
      truncated,
    };
  }

  /** PS-WT fs.tree：computer 级只读目录浏览。path=null → 根视图；否则列该目录仅子目录。 */
  private fsTree(data: JsonObject): JsonObject {
    const p = (data['path'] as string | null | undefined) ?? null;
    if (p === null) {
      return { entries: fsRootEntries(), truncated: false };
    }
    const [entries, truncated] = fsDirEntries(p);
    return { entries, truncated };
  }

  /** path 规范化后必须在该 Agent home 之内（防 ../ 逃逸，契约 D §6）。 */
  private safeJoin(agentMemberId: string, p: string): string | null {
    const homeStr = this.adapter.homePath(agentMemberId);
    const rootRaw = homeStr !== null && homeStr !== '' ? expandHome(homeStr) : this.paths.agentHome(agentMemberId);
    const root = resolvePath(rootRaw);
    const candidate = resolvePath(path.join(root, p.replace(/^[/\\]+/, '')));
    const fold = (s: string) => (process.platform === 'win32' ? s.toLowerCase() : s);
    if (fold(candidate) === fold(root) || fold(candidate).startsWith(fold(root) + path.sep)) {
      return candidate;
    }
    return null;
  }

  // ---------------------------------------------------------- AdapterSink 上报（契约 D §7）

  async onStatusChanged(agentMemberId: string, status: AgentStatus, errorDetail?: string | null): Promise<void> {
    await this.reportBestEffort('agent.status_changed', {
      agent_member_id: agentMemberId,
      status,
      error_detail: errorDetail ?? null,
    });
  }

  async onActivity(agentMemberId: string, detail: string): Promise<void> {
    const now = performance.now();
    if (now - (this.activityLast.get(agentMemberId) ?? -Infinity) < ACTIVITY_PRETHROTTLE_MS) {
      return;
    }
    this.activityLast.set(agentMemberId, now);
    await this.reportBestEffort('agent.activity', { agent_member_id: agentMemberId, detail });
  }

  onUsage(event: TokenUsageEventIn): void {
    this.buffer.appendUsage(event);
    this.flushEvent.set();
  }

  onDiagnostic(event: DiagnosticEventIn): void {
    this.buffer.appendDiagnostic(event);
    this.flushEvent.set();
  }

  /** runtime.rescan：重探测 → 更新缓存 + runtimes.detected 上报（契约 D §5.3/§7）。 */
  async rescanRuntimes(): Promise<void> {
    this.detectedRuntimes = await probeRuntimes(this.runner);
    await this.reportBestEffort('runtimes.detected', { runtimes: this.detectedRuntimes });
  }

  /** worktree 指令先上报现状，再由 handleInstr 发 ack（同 WS 内有序）。 */
  async reportWorktreeStatus(data: WorktreeStatusData): Promise<void> {
    await this.reportBestEffort('worktree.status', data);
  }

  /** preview.status 载状态直发（无 ack）；断连丢弃靠对账兜底。 */
  async reportPreviewStatus(data: PreviewStatusData): Promise<void> {
    await this.reportBestEffort('preview.status', data);
  }

  /** check.finished 先持久入缓冲；flush 获 server ack 后才删除。 */
  async reportCheckFinished(data: CheckFinishedData): Promise<void> {
    this.buffer.appendCheck(data);
    this.flushEvent.set();
  }

  /** deploy.log 先持久入缓冲（需 ack）；断连重启原样重传（chunk_seq 单调，server 按已收 max 去重）。 */
  async reportDeployLog(data: DeployLogReportData): Promise<void> {
    this.buffer.appendDeployLog(data);
    this.flushEvent.set();
  }

  /** deploy.finished 先持久入缓冲（需 ack）；flush 获 server ack 后按 deployment_id 删除。 */
  async reportDeployFinished(data: DeployFinishedData): Promise<void> {
    this.buffer.appendDeployFinished(data);
    this.flushEvent.set();
  }

  /** 载状态类上报（无 ack）：连接可用即发，断连忽略（重连 hello 全量重报兜底）。 */
  private async reportBestEffort(rtype: ReportType, data: unknown): Promise<void> {
    try {
      await this.send(reportFrame(rtype, data));
    } catch (exc) {
      if (!(exc instanceof TransportClosed)) throw exc;
    }
  }

  // ---------------------------------------------------------------- 缓冲重传（契约 D §7）

  private async flushLoop(): Promise<void> {
    for (;;) {
      await this.flushEvent.wait();
      if (this.connClosed.isSet()) return;
      this.flushEvent.clear();
      try {
        await this.flushUsage();
        await this.flushDiagnostics();
        await this.flushChecks();
        // deploy.log 先于 deploy.finished flush（保序：日志全排空再报终态）。
        await this.flushDeployLogs();
        await this.flushDeployFinished();
      } catch (exc) {
        if (!(exc instanceof TransportClosed)) throw exc;
      }
    }
  }

  private async flushUsage(): Promise<void> {
    while (this.buffer.hasUsage()) {
      const batch = this.buffer.peekUsage(USAGE_BATCH);
      const ok = await this.reportAwaited('usage.batch', { events: batch });
      if (!ok) return; // 未 ack → 保留待重传（不虚增：ULID 不变，server 去重）
      this.buffer.ackUsage(batch.map((e) => e.id));
    }
  }

  private async flushDiagnostics(): Promise<void> {
    while (this.buffer.hasDiagnostics()) {
      const batch = this.buffer.peekDiagnostics(DIAG_BATCH);
      const ok = await this.reportAwaited('diagnostics.batch', { events: batch });
      if (!ok) return;
      this.buffer.ackDiagnostics(batch.length);
    }
  }

  private async flushChecks(): Promise<void> {
    while (this.buffer.hasChecks()) {
      const [first] = this.buffer.peekChecks(1);
      const ok = await this.reportAwaited('check.finished', first!);
      if (!ok) return;
      this.buffer.ackChecks([first!.run_id]);
    }
  }

  private async flushDeployLogs(): Promise<void> {
    while (this.buffer.hasDeployLogs()) {
      const [first] = this.buffer.peekDeployLogs(1);
      const ok = await this.reportAwaited('deploy.log', first!);
      if (!ok) return; // 未 ack → 保留待重传（chunk_seq 不变，server 去重）
      this.buffer.ackDeployLog(first!.deployment_id, first!.chunk_seq);
    }
  }

  private async flushDeployFinished(): Promise<void> {
    while (this.buffer.hasDeployFinished()) {
      const [first] = this.buffer.peekDeployFinished(1);
      const ok = await this.reportAwaited('deploy.finished', first!);
      if (!ok) return;
      this.buffer.ackDeployFinished([first!.deployment_id]);
    }
  }

  /** 缓冲重传类上报（需 ack）：发帧 → 等 server ack（超时/断连 → false）。 */
  private async reportAwaited(rtype: ReportType, data: unknown): Promise<boolean> {
    const frameId = newUlid();
    let resolve!: (f: JsonObject) => void;
    let reject!: (e: Error) => void;
    const promise = new Promise<JsonObject>((res, rej) => {
      resolve = res;
      reject = rej;
    });
    promise.catch(() => {}); // 防断连清账时无 awaiter 的孤儿 rejection
    this.reportAcks.set(frameId, { promise, resolve, reject });
    try {
      await this.send(reportFrame(rtype, data, frameId));
      const ack = await withTimeout(promise, this.ackTimeout * 1000);
      return ack['result'] === 'done' || ack['result'] === 'noop';
    } catch (exc) {
      if (exc instanceof TimeoutError || exc instanceof TransportClosed) return false;
      throw exc;
    } finally {
      this.reportAcks.delete(frameId);
    }
  }

  private resolveReportAck(frame: JsonObject): void {
    const pending = this.reportAcks.get((frame['ref'] as string) ?? '');
    pending?.resolve(frame);
  }

  private failPending(reason: string): void {
    for (const pending of this.reportAcks.values()) {
      pending.reject(new TransportClosed(reason));
    }
    this.reportAcks.clear();
  }

  // ---------------------------------------------------------------- 心跳（契约 D §2）

  private async heartbeatLoop(transport: Transport): Promise<void> {
    for (;;) {
      await Promise.race([sleep(this.heartbeatSec * 1000), this.connClosed.wait()]);
      if (this.connClosed.isSet()) return;
      this.pongEvent.clear();
      try {
        await this.send({ v: DAEMON_PROTOCOL_V, kind: FRAME_KIND.PING });
      } catch (exc) {
        if (!(exc instanceof TransportClosed)) throw exc;
      }
      try {
        await withTimeout(this.pongEvent.wait(), this.pongTimeout * 1000);
      } catch (exc) {
        if (exc instanceof TimeoutError) {
          this.logWarn('heartbeat: no pong → reconnect');
          await transport.close(); // 触发 reader TransportClosed → 重连
          return;
        }
        throw exc;
      }
      if (this.connClosed.isSet()) return;
    }
  }

  // ---------------------------------------------------------------- 底座

  private async send(payload: JsonObject): Promise<void> {
    const transport = this._transport;
    if (transport === null) throw new TransportClosed('no transport');
    await this.sendLock.runExclusive(() => transport.send(payload));
  }

  private async safeClose(transport: Transport): Promise<void> {
    try {
      await transport.close();
    } catch {
      // 关闭失败忽略（对等 py suppress(Exception)）
    }
  }

  private logWarn(message: string): void {
    // 经 logconfig → daemon.log；未装配时（如单测直建 DaemonClient）静默丢弃，不落盘。
    log.warn(message);
  }
}

function isoFromMtime(mtimeMs: number): string {
  return new Date(mtimeMs).toISOString();
}

function expandHome(p: string): string {
  if (p === '~' || p.startsWith('~/') || p.startsWith('~\\')) {
    return path.join(process.env['USERPROFILE'] ?? process.env['HOME'] ?? '', p.slice(1));
  }
  return p;
}

export { FS_TREE_MAX, HOME_FILE_MAX };
