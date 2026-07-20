/**
 * Claude Code 适配器（契约 E 全文落地；A7 替换 A6 FakeAdapter）。
 *
 * 两层：
 * - `ClaudeCodeProcess`：**每进程**驱动（base.RuntimeAdapter / E §9）——命令行拼装、子进程、
 *   stdout stream-json 逐行解析 → FrameRouter → 四回调；start/stop/feed/resetSessionArgs。
 * - `RuntimeManager`（历史名 `ClaudeCodeAdapter`）：daemon 侧**管理器**（A6 RuntimeAdapter 接口，
 *   DaemonClient 不变）——每 Agent 一个进程驱动，管会话簿记 / 三档重置 / 崩溃熔断（§4/§5）/
 *   输入编码（§6）；按 boot.runtime 分派 claude / codex 进程类（E2 §1）。
 *
 * 对等基准 = apps/daemon adapters/claude_code.py。`spawn` 可注入 → 单测/冒烟脱离真 claude。
 *
 * py/TS 差异（逐条登记，非行为改进；README 体例 5 / 任务书 §4）：
 * - 取消语义：py reader/stderr 任务 task.cancel（CancelledError 注入 await 点）→ TS 读循环逐行
 *   经 CancelSignal **退订式** race（命中 ReaderCancelled → 不触发退出回调，对等 py
 *   CancelledError re-raise；禁止回退成逐行 race 单个长命 promise，见 CancelSignal 注释）；
 *   restart_task.cancel → 取消标志 + stopping/身份判等（===）双守卫
 *   （退避睡眠不被打断，但睡后复核阻止重拉起——副作用等价）。
 * - monkeypatch 面：py 测试 monkeypatch 模块常量 CRASH_BACKOFF / AUTH_RECOVERY_DELAYS → TS 导出
 *   **可就地替换数组**（测试 splice 归零 + afterEach 还原），代码读取点保持动态取值。
 * - sink 重绑：py 双点重绑（entry.process._sink 与 entry.process.router._sink 各赋值）→ TS 管理器
 *   仍写两席位（codex router 的 `_sink` 是活席位）；claude 侧 FrameRouter（W1 底座）sink 为
 *   private readonly，行为经 Process 构造时注入的**转发 sink**（恒指向 process._sink）跟随，
 *   router 上的 `_sink` 写入对 claude 是无副作用存根。
 * - codex 分派：py 同步惰性 import → TS 异步动态 import('./codex.ts')（_new_process → 异步）。
 * - 32MB 行上限：py StreamReader limit 超限抛 LimitOverrunError **终结读循环**（视作进程终结）→
 *   TS 按校准条款 2「超限=丢帧不崩读循环」：超限行整行丢弃 + log 告警 + 继续读（stderr 同理
 *   继续排空）；两 runtime 常量并行自持（codex.ts 同值），W4/W5 收敛单点挂账。
 * - 子进程失败：py 创建点同步 OSError → node 'error' 事件异步到达，defaultSpawn 内 await
 *   spawn/error 收敛回创建点抛出（checks/preview 先例）。
 * - 单调时钟：py loop.time() → performance.now()/1000（秒），熔断窗口数值不变。
 * - 命名：py `_on_line` → 公有 `onLine`（测试直调面）；py `_agents` → 公有 `agents` Map
 *   （测试/管理器分派用例直读面）。
 */

import { spawn as nodeSpawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';
import type { Readable, Writable } from 'node:stream';

import type { AgentBoot, AgentStatus, DaemonAgentState, DiagnosticEventIn, Runtime, WakeReason, WakeRefs } from '@coagentia/contracts-ts';

import type { AdapterSink, RuntimeAdapter as ManagerContract } from '../adapter.ts';
import { AsyncEvent, AsyncQueue, sleep, withTimeout } from '../aio.ts';
import { getLogger } from '../logconfig.ts';
import type { DataPaths } from '../paths.ts';
import type { JsonObject } from '../protocol.ts';
import { newUlid, nowIso } from '../util.ts';
import type { RuntimeAdapter as ProcessContract } from './base.ts';
import { buildArgv, buildEnv, materializeCredentials, materializeMcpConfig } from './cmdline.ts';
import { renderDeliver, renderInject, userFrameLine } from './encoding.ts';
import { FrameRouter } from './frames.ts';

// daemon 文件日志（B-4 可观测性，对照 codex）：claude stream-json 帧收发/生命周期落 daemon.log。
const log = getLogger('coagentia_daemon.adapters.claude_code');
const FRAME_PREVIEW = 600; // 帧原文 DEBUG 预览截断

// 崩溃拉起退避（§5：1s → 5s → 15s；5 分钟窗 ≥3 次 → 放弃 error）。
// py monkeypatch 模块常量 → TS 可就地替换数组（测试 splice 归零 + 还原），读取点动态取值。
export const CRASH_BACKOFF: number[] = [1.0, 5.0, 15.0];
export const CRASH_WINDOW_SEC = 300.0;
export const CRASH_MAX = 3;
const STOP_GRACE_SEC = 5.0; // §5：关 stdin → 等 5s 优雅退出 → terminate/kill
export const AUTH_RECOVERY_DELAYS: number[] = [0.0, 0.5, 1.0, 2.0];
const AUTH_ERROR_MARKERS = [
  'failed to authenticate',
  'oauth session expired',
  'authentication_error',
] as const;
const STDERR_TAIL_MAX = 50; // py deque(maxlen=50)

// 会话续接判据的簿记键（按 runtime）：claude=session_id（--resume），codex=conversation_id
// （thread/resume）。管理器骨架 runtime 无关，仅此处按 runtime 取键（适配器边界内特化，纪律 8）。
const RESUME_KEY: Record<Runtime, string> = {
  claude_code: 'session_id',
  codex: 'conversation_id',
};

/** 子进程 stdin 的最小接口（可注入桩；对齐 tests/adapter_helpers.ts FakeStdin 结构面）。 */
export interface ProcStdin {
  write(data: Buffer | string): void;
  drain?(): Promise<void>;
  close(): void;
}

/** 子进程 stdout/stderr 的最小行读接口（readline 含行尾 \n；空 Buffer = EOF，py b"" 对等）。 */
export interface ProcStdout {
  readline(): Promise<Buffer>;
}

/** asyncio 子进程的最小接口（可注入桩，py ProcLike Protocol 对等）。 */
export interface ProcLike {
  stdin: ProcStdin | null;
  stdout: ProcStdout;
  stderr?: ProcStdout | null;
  returncode: number | null;
  pid?: number | null;
  terminate(): void;
  kill(): void;
  wait(): Promise<number>;
}

export type SpawnFn = (argv: string[], cwd: string, env: Record<string, string>) => Promise<ProcLike>;

// 逐行缓冲上限（B-4 根因）：py asyncio StreamReader 默认 64KB 太小——单条 stream-json 帧可远超
// （大工具结果/大 reasoning），超限杀读循环 → agent「挂死无诊断」。py 放宽 limit=32MB；TS 无
// limit 参数，校准条款 2 的手写行读法以本常量为自设上限（超限=丢帧告警不崩读循环，防 OOM）。
export const STREAM_LINE_LIMIT = 32 * 1024 * 1024;

/** stop() 主动取消读循环的穿透位（对等 py CancelledError：不触发退出回调）。 */
class ReaderCancelled extends Error {}

/**
 * 取消信号（py task.cancel 的 TS 线程化）：race() 任一落定即退订等待者。
 * 禁止回退成「逐行 race 单个长命 cancelled promise」——每次 race 都在该 promise 的 reaction
 * 列表追加**永久**记录并经胜果钉住行 Buffer（读循环热路径 → 会话内无界增长）。
 */
class CancelSignal {
  private err: Error | null = null;
  private readonly waiters = new Set<(e: Error) => void>();

  cancel(err: Error): void {
    if (this.err !== null) return;
    this.err = err;
    const ws = [...this.waiters];
    this.waiters.clear();
    for (const w of ws) w(err);
  }

  /** p 与取消信号竞速：已取消立即抛；任一落定即退订，不留逐行残留。 */
  async race<T>(p: Promise<T>): Promise<T> {
    if (this.err !== null) {
      p.catch(() => {}); // 仍消费 p（对齐原 race 全路径订阅语义，杜绝潜在 unhandledRejection）
      throw this.err;
    }
    let unsubscribe!: () => void;
    const cancelP = new Promise<never>((_, reject) => {
      const w = (e: Error): void => reject(e);
      this.waiters.add(w);
      unsubscribe = (): void => {
        this.waiters.delete(w);
      };
    });
    try {
      return await Promise.race([p, cancelP]);
    } finally {
      unsubscribe();
    }
  }
}

function isDict(v: unknown): v is JsonObject {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

/** py `_safe_wait` 对等：wait 异常时回退 returncode。 */
async function safeWait(proc: ProcLike): Promise<number | null> {
  try {
    return await proc.wait();
  } catch {
    return proc.returncode ?? null;
  }
}

// ------------------------------------------------------------ 真子进程包装（defaultSpawn）

/**
 * 行读器（校准条款 2）：Buffer 累积按 \n 字节切分（严禁逐 chunk toString，cal1 规则 4）；
 * 行含行尾 \n（py readline 对等，空行 b"\n" 不与 EOF 混淆）；EOF = 空 Buffer。
 * 超 32MB 行整行丢弃 + onOversize 告警，不崩读循环（校准条款 2，py LimitOverrunError 差异已登记）。
 */
class NodeLineReader implements ProcStdout {
  private parts: Buffer[] = [];
  private partBytes = 0;
  private dropping = false;
  private eof = false;
  private readonly lines = new AsyncQueue<Buffer>();

  private readonly limit: number;
  private readonly onOversize: (bytes: number) => void;

  constructor(stream: Readable, limit: number, onOversize: (bytes: number) => void) {
    this.limit = limit;
    this.onOversize = onOversize;
    // cal6：spawn 当拍同步挂消费者（否则子进程死亡残留数据静默丢）。
    stream.on('data', (chunk: Buffer) => this.push(chunk));
    const finish = (): void => this.pushEof();
    stream.on('close', finish);
    stream.on('error', finish);
  }

  private push(chunk: Buffer): void {
    let start = 0;
    for (let i = 0; i < chunk.length; i += 1) {
      if (chunk[i] === 0x0a) {
        this.completeLine(chunk.subarray(start, i + 1));
        start = i + 1;
      }
    }
    if (start < chunk.length) this.addPartial(chunk.subarray(start));
  }

  private completeLine(piece: Buffer): void {
    if (this.dropping) {
      this.dropping = false; // 超限行到此终结：整行已弃，恢复正常切行
      return;
    }
    const total = this.partBytes + piece.length;
    if (total > this.limit) {
      this.parts = [];
      this.partBytes = 0;
      this.onOversize(total);
      return;
    }
    const line = this.parts.length === 0 ? piece : Buffer.concat([...this.parts, piece]);
    this.parts = [];
    this.partBytes = 0;
    this.lines.put(line);
  }

  private addPartial(part: Buffer): void {
    if (this.dropping) return; // 超限行内：整块丢弃（防 OOM）
    const total = this.partBytes + part.length;
    if (total > this.limit) {
      this.parts = [];
      this.partBytes = 0;
      this.dropping = true;
      this.onOversize(total);
      return;
    }
    this.parts.push(part);
    this.partBytes = total;
  }

  private pushEof(): void {
    if (this.eof) return;
    this.eof = true;
    if (!this.dropping && this.parts.length > 0) {
      // py readline 在 EOF 前返回无行尾残段（对等）
      this.lines.put(Buffer.concat(this.parts));
    }
    this.parts = [];
    this.partBytes = 0;
    this.lines.put(Buffer.alloc(0));
  }

  async readline(): Promise<Buffer> {
    if (this.eof && this.lines.size === 0) return Buffer.alloc(0);
    return this.lines.get();
  }
}

/** stdin 包装（测试面导出）：write 背压 → drain 等 'drain'/'close'/'error' 先至者（cal6）。 */
export class NodeStdin implements ProcStdin {
  private needDrain = false;

  private readonly stream: Writable;

  constructor(stream: Writable) {
    this.stream = stream;
    // EPIPE 等写侧错误必须有 handler（未挂 'error' 的流错误 = uncaughtException 崩进程）。
    stream.on('error', () => {});
  }

  write(data: Buffer | string): void {
    if (!this.stream.write(data)) this.needDrain = true;
  }

  async drain(): Promise<void> {
    if (!this.needDrain) return;
    this.needDrain = false;
    if (!this.stream.writableNeedDrain) return;
    // 背压中 'drain' 可能永不来（流关闭/出错）→ close/error 兜底收敛不悬挂；error 胜出吞掉
    //（写失败已由构造挂的 'error' handler 免于崩进程，经后续写/进程退出路径浮出）。
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
      // 已关闭 / 已销毁：幂等
    }
  }
}

/** node ChildProcess → ProcLike（生命周期定稿只挂 'close'，cal6：end/exit 顺序不定）。 */
class NodeProcess implements ProcLike {
  readonly stdin: ProcStdin | null;
  readonly stdout: ProcStdout;
  readonly stderr: ProcStdout | null;
  returncode: number | null = null;
  readonly pid: number | null;
  private readonly closed = new AsyncEvent();

  private readonly child: ChildProcess;

  constructor(child: ChildProcess) {
    this.child = child;
    this.pid = child.pid ?? null;
    this.stdin = child.stdin !== null ? new NodeStdin(child.stdin) : null;
    const oversize = (label: string) => (bytes: number) =>
      log.warn(`claude ${label} line over ${STREAM_LINE_LIMIT} bytes dropped (got ~${bytes})`);
    this.stdout =
      child.stdout !== null
        ? new NodeLineReader(child.stdout, STREAM_LINE_LIMIT, oversize('stdout'))
        : { readline: async () => Buffer.alloc(0) };
    this.stderr =
      child.stderr !== null
        ? new NodeLineReader(child.stderr, STREAM_LINE_LIMIT, oversize('stderr'))
        : null;
    child.on('close', (code) => {
      // 信号终止 code=null → 统一 -1（py 负信号值的近似；仅诊断展示位）
      this.returncode = code ?? -1;
      this.closed.set();
    });
    child.on('error', () => {
      if (this.returncode === null) this.returncode = -1;
      this.closed.set();
    });
  }

  terminate(): void {
    try {
      this.child.kill();
    } catch {
      // 已退出：幂等
    }
  }

  kill(): void {
    try {
      this.child.kill('SIGKILL');
    } catch {
      // 已退出：幂等
    }
  }

  async wait(): Promise<number> {
    await this.closed.wait();
    return this.returncode ?? 0;
  }
}

/** win32 cmd.exe 拉起时的参数引用（路径含空格；校准条款 3 shell:true 通道）。 */
// codex.ts 同名同实现的文件内自持副本（W3 文件域隔离，W4/W5 收敛单点挂账；勿另建共享模块）。
function quoteForShell(s: string): string {
  return /[\s"]/.test(s) ? `"${s.replace(/"/g, '\\"')}"` : s;
}

/**
 * 默认 spawn（测试面导出；argv 由调用方给定）：win32 `.cmd`/`.bat`（npm shim claude）必须
 * shell:true（node 22 裸 spawn EINVAL，校准条款 3）；spawn 失败 race 'spawn'/'error' 还原
 * py 创建点同步 OSError 语义。
 */
export async function defaultSpawn(argv: string[], cwd: string, env: Record<string, string>): Promise<ProcLike> {
  const [cmd, ...args] = argv;
  const needsShell = process.platform === 'win32' && /\.(cmd|bat)$/i.test(cmd ?? '');
  const child = needsShell
    ? nodeSpawn([quoteForShell(cmd ?? ''), ...args.map(quoteForShell)].join(' '), {
        cwd,
        env,
        shell: true,
        stdio: ['pipe', 'pipe', 'pipe'],
      })
    : nodeSpawn(cmd ?? '', args, { cwd, env, stdio: ['pipe', 'pipe', 'pipe'] });
  const proc = new NodeProcess(child); // cal6：当拍同步挂 stdout/stderr 消费者
  await new Promise<void>((resolve, reject) => {
    child.once('spawn', () => resolve());
    // py 创建点同步 OSError 对等：spawn 失败收敛回创建点抛出
    child.once('error', (err) => reject(err));
  });
  return proc;
}

// ============================================================ 每进程驱动（E §9）

/** 进程构造选项（py 关键字参数 → TS options 对象；CodexProcessOptions 同形）。 */
export interface ClaudeCodeProcessOptions {
  serverUrl: string;
  apiKey: string;
  spawn?: SpawnFn | null;
  onExit?: ((agentMemberId: string, returncode: number | null) => Promise<void>) | null;
  ulid?: () => string;
  now?: () => string;
}

/** 单 Agent 的 claude 子进程驱动（base.RuntimeAdapter / E §9）。 */
export class ClaudeCodeProcess implements ProcessContract {
  readonly agentMemberId: string;
  // 管理器重绑席位（py entry.process._sink = _AgentSink 同席位）——public 可写；
  // router 经构造时注入的转发 sink 恒指向本席位（frames.ts sink 为 private readonly 的适配）。
  _sink: AdapterSink;
  readonly router: FrameRouter;
  stderrTail: string[] = []; // py deque(maxlen=50)
  pid: number | null = null;

  private readonly paths: DataPaths;
  private readonly serverUrl: string;
  private readonly apiKey: string;
  private readonly spawnFn: SpawnFn;
  private readonly onExitCb: ((agentMemberId: string, returncode: number | null) => Promise<void>) | null;
  private readonly nowFn: () => string;
  private proc: ProcLike | null = null;
  private readerTask: Promise<void> | null = null;
  private stderrTask: Promise<void> | null = null;
  // stop() 主动取消位（py task.cancel 的 TS 线程化：读循环/排空在 await 点经退订式 race 收敛）
  private cancelSignal: CancelSignal | null = null;
  private resumeArgs: string[] = [];
  private configDir: string | null = null;
  private lastInput: string | null = null;
  private authRetryUsed = false;

  constructor(agentMemberId: string, sink: AdapterSink, paths: DataPaths, opts: ClaudeCodeProcessOptions) {
    this.agentMemberId = agentMemberId;
    this._sink = sink;
    this.paths = paths;
    this.serverUrl = opts.serverUrl;
    this.apiKey = opts.apiKey;
    this.spawnFn = opts.spawn ?? defaultSpawn;
    this.onExitCb = opts.onExit ?? null;
    this.nowFn = opts.now ?? nowIso;
    // 转发 sink：管理器重绑 process._sink 后 router 自动跟随（py router._sink 直改的等价物）。
    const forward: AdapterSink = {
      onStatusChanged: (aid, status, errorDetail) => this._sink.onStatusChanged(aid, status, errorDetail),
      onActivity: (aid, detail) => this._sink.onActivity(aid, detail),
      onUsage: (event) => {
        this._sink.onUsage(event);
      },
      onDiagnostic: (event) => {
        this._sink.onDiagnostic(event);
      },
    };
    this.router = new FrameRouter(agentMemberId, forward, {
      ulid: opts.ulid ?? newUlid,
      now: opts.now ?? nowIso,
      onSession: (sid) => this.persistSession(sid),
    });
  }

  // -------------------------------------------------------- 会话簿记

  private persistSession(sessionId: string): void {
    this.paths.writeSession(this.agentMemberId, { session_id: sessionId });
  }

  private resumeSessionId(): string | null {
    const sid = this.paths.readSession(this.agentMemberId)['session_id'];
    return typeof sid === 'string' && sid ? sid : null;
  }

  /** 三档重置的会话层命令行差异（§4）：当前进程的 --resume 参数（空 = 新会话）。 */
  resetSessionArgs(): string[] {
    return [...this.resumeArgs];
  }

  // -------------------------------------------------------- 生命周期（E §9）

  async start(boot: AgentBoot, resume: boolean): Promise<void> {
    const home = this.paths.ensureAgentHome(this.agentMemberId);
    const configDir = buildEnv(home)['CLAUDE_CONFIG_DIR']!;
    this.configDir = configDir;
    const mcpPath = materializeMcpConfig(configDir, {
      agentMemberId: this.agentMemberId,
      serverUrl: this.serverUrl,
      apiKey: this.apiKey,
    });
    materializeCredentials(configDir); // 凭证物化（§2/FR-2.3）
    this.materializeSkills(configDir, boot.skills ?? []);
    const resumeId = resume ? this.resumeSessionId() : null;
    this.resumeArgs = resumeId ? ['--resume', resumeId] : [];
    const argv = buildArgv(boot, { mcpConfigPath: mcpPath, resumeSessionId: resumeId });
    const env = buildEnv(home);
    this.router.resetRun(); // 复位本次 spawn 的运行态（confirmed/turn/phase）
    this.proc = await this.spawnFn(argv, home, env);
    this.pid = this.proc.pid ?? null;
    log.info(`claude[${this.agentMemberId}] spawned pid=${String(this.pid)} resume=${String(Boolean(resumeId))}`);
    this._sink.onDiagnostic(
      this.diag('agent.process_started', { pid: this.pid, resume: Boolean(resumeId) }),
    );
    this.cancelSignal = new CancelSignal();
    this.readerTask = this.readLoop();
    this.readerTask.catch(() => {}); // 读循环内部已兜底；双保险
    this.stderrTask = null;
    // stderr 必须持续排空：--verbose 会灌满 stderr 管道 → 否则子进程写阻塞死锁（cal6）。
    if (this.proc.stderr !== undefined && this.proc.stderr !== null) {
      this.stderrTask = this.drainStderr();
      this.stderrTask.catch(() => {});
    }
  }

  /**
   * 技能白名单物化占位（§2/R6）：白名单外技能不可见。
   *
   * M1 仅落地隔离目录 + 白名单清单文件；真实技能复制/链接随技能库落地（open_issue）。
   */
  private materializeSkills(configDir: string, skills: string[]): void {
    try {
      fs.mkdirSync(path.join(configDir, 'skills'), { recursive: true });
      fs.writeFileSync(
        path.join(configDir, 'coagentia-skills.json'),
        JSON.stringify({ allowed: [...skills] }),
        'utf-8',
      );
    } catch {
      // py contextlib.suppress(OSError) 对齐
    }
  }

  private async readLoop(): Promise<void> {
    const proc = this.proc!;
    const stdout = proc.stdout;
    try {
      for (;;) {
        const line = await this.cancelSignal!.race(stdout.readline());
        if (line.length === 0) break;
        await this.onLine(line);
      }
    } catch (err) {
      if (err instanceof ReaderCancelled) {
        return; // 对等 py CancelledError re-raise：stop() 主动取消 → 不触发退出回调
      }
      // 读循环内任何异常都不外抛，视作进程终结（py except Exception 对齐）
      log.warn(`claude[${this.agentMemberId}] read loop error: ${String(err)}`);
    }
    const returncode = await safeWait(proc);
    log.info(
      `claude[${this.agentMemberId}] read loop ended (stdout EOF), returncode=${String(returncode)}; ` +
        `stderr_tail=${JSON.stringify(this.stderrTail.slice(-5))}`,
    );
    if (this.onExitCb !== null) {
      await this.onExitCb(this.agentMemberId, returncode);
    }
  }

  /** 持续排空 stderr（保留末尾若干行供崩溃诊断），防止管道满导致子进程阻塞。 */
  private async drainStderr(): Promise<void> {
    const proc = this.proc;
    const stderr = proc?.stderr;
    if (stderr === undefined || stderr === null) return;
    try {
      for (;;) {
        const line = await this.cancelSignal!.race(stderr.readline());
        if (line.length === 0) break;
        const text = line.toString('utf-8').trimEnd(); // decode('utf-8','replace') 对等（U+FFFD）
        if (text) {
          this.stderrTail.push(text);
          if (this.stderrTail.length > STDERR_TAIL_MAX) this.stderrTail.shift();
          log.info(`claude[${this.agentMemberId}] stderr: ${text}`);
        }
      }
    } catch {
      return; // py except (CancelledError, Exception) → return 对齐
    }
  }

  /** 处理一行 stream-json（py `_on_line`；测试直调面 → 公有）。 */
  async onLine(line: Buffer | string): Promise<void> {
    let text = typeof line === 'string' ? line : line.toString('utf-8');
    text = text.trim();
    if (!text) return;
    log.debug(`claude[${this.agentMemberId}] recv: ${text.slice(0, FRAME_PREVIEW)}`);
    let frame: unknown;
    try {
      frame = JSON.parse(text);
    } catch {
      log.warn(`claude[${this.agentMemberId}] recv non-json: ${text.slice(0, 200)}`);
      this.router.unknownCounts['<non-json>'] = (this.router.unknownCounts['<non-json>'] ?? 0) + 1;
      return;
    }
    if (isDict(frame)) {
      await this.router.process(frame);
      if (ClaudeCodeProcess.isAuthFailure(frame)) {
        await this.retryAfterAuthFailure();
      }
    }
  }

  /**
   * 写入一个 turn 的输入（§6.4：写 stdin 即 ack）。
   *
   * text = 管理器渲染的**运行时无关正文**（encoding.render*，纪律 8）；载体（stream-json
   * user 帧封装）是 claude 侧特化，落在本 Process（区别于 codex 的 turn/start input）。
   */
  async feed(text: string): Promise<void> {
    if (this.configDir !== null) {
      materializeCredentials(this.configDir);
    }
    this.lastInput = text;
    this.authRetryUsed = false;
    await this.writeInput(text);
  }

  private async writeInput(text: string): Promise<void> {
    const proc = this.proc;
    if (proc === null || proc.stdin === null) {
      throw new Error('process not running');
    }
    log.info(`claude[${this.agentMemberId}] feed turn input (len=${text.length})`);
    this.router.beginTurn(); // 抑制随后的 init→idle 误报（init 帧在首输入后到）
    const data = Buffer.from(userFrameLine(text) + '\n', 'utf-8'); // 载体封装（claude 特化）
    proc.stdin.write(data);
    if (proc.stdin.drain !== undefined) {
      await proc.stdin.drain(); // 背压（cal6）
    }
  }

  private static isAuthFailure(frame: JsonObject): boolean {
    if (frame['type'] !== 'result') return false;
    if (!frame['is_error'] && !frame['api_error_status']) return false;
    const text = JSON.stringify(frame).toLowerCase(); // py casefold 对等（marker 全 ASCII）
    return AUTH_ERROR_MARKERS.some((marker) => text.includes(marker));
  }

  /** 等待并吸收其他 Agent 刷新的 OAuth 凭证，然后把失败 turn 自动重投一次。 */
  private async retryAfterAuthFailure(): Promise<void> {
    if (this.authRetryUsed || this.lastInput === null || this.configDir === null) return;
    this.authRetryUsed = true;
    for (const delay of AUTH_RECOVERY_DELAYS) {
      await sleep(delay * 1000);
      if (materializeCredentials(this.configDir).length > 0) {
        await this.writeInput(this.lastInput);
        return;
      }
    }
  }

  /** 关 stdin → 等 5s 优雅退出 → terminate → kill（§5）。 */
  async stop(): Promise<void> {
    const proc = this.proc;
    if (proc === null) return;
    try {
      if (proc.stdin !== null) proc.stdin.close();
    } catch {
      // py contextlib.suppress(Exception) 对齐
    }
    try {
      await withTimeout(proc.wait(), STOP_GRACE_SEC * 1000);
    } catch {
      try {
        proc.terminate();
      } catch {
        // suppress 对齐
      }
      try {
        await withTimeout(proc.wait(), 2000);
      } catch {
        try {
          proc.kill();
        } catch {
          // suppress 对齐
        }
      }
    }
    // py for task in (reader, stderr): task.cancel(); await task（suppress）
    if (this.cancelSignal !== null) {
      this.cancelSignal.cancel(new ReaderCancelled('stopped'));
    }
    for (const task of [this.readerTask, this.stderrTask]) {
      if (task !== null) {
        try {
          await task;
        } catch {
          // suppress(CancelledError, Exception) 对齐
        }
      }
    }
    this.proc = null;
  }

  isRunning(): boolean {
    return this.proc !== null && this.proc.returncode === null;
  }

  setTurnContext(channelId: string | null, threadRootId: string | null): void {
    this.router.setTurnContext(channelId, threadRootId);
  }

  private diag(dtype: string, payload: JsonObject): DiagnosticEventIn {
    return {
      agent_member_id: this.agentMemberId,
      type: dtype,
      // py pydantic 默认字段（channel_id/task_id/batch_id=None）显式兑现（JSONL 形状对等）。
      channel_id: null,
      task_id: null,
      batch_id: null,
      payload,
      at: this.nowFn(),
    };
  }
}

// ============================================================ 管理器（A6 接口）

/** 管理器所需的进程侧最小面（ClaudeCodeProcess / CodexProcess 共同结构，E2 §1）。 */
export interface ManagedRouter {
  sessionId: string | null;
  confirmed: boolean;
  // codex router 的活重绑席位；claude router（frames.ts）无此席（行为经 process._sink 转发跟随）。
  _sink?: AdapterSink;
}

export interface ManagedProcess {
  _sink: AdapterSink;
  readonly router: ManagedRouter;
  start(boot: AgentBoot, resume: boolean): Promise<void>;
  stop(): Promise<void>;
  feed(text: string): Promise<void>;
  resetSessionArgs(): string[];
  isRunning(): boolean;
  setTurnContext(channelId: string | null, threadRootId: string | null): void;
}

type ManagedProcessCtor = new (
  agentMemberId: string,
  sink: AdapterSink,
  paths: DataPaths,
  opts: ClaudeCodeProcessOptions,
) => ManagedProcess;

/** 崩溃重拉起任务句柄（py restart_task: asyncio.Task；cancel → 取消标志 + 睡后复核）。 */
export interface RestartHandle {
  promise: Promise<void>;
  cancelled: boolean;
}

export class AgentEntry {
  boot: AgentBoot;
  readonly process: ManagedProcess;
  status: AgentStatus = 'starting';
  // 去重游标**按 channel_id 维度**：不同频道各自「已喂过的最大 message_id」，避免频道 A 的较大
  // message_id 误压制频道 B 较早消息的投递（契约 D §5.2；跨频道乱序丢消息 #2）。
  readonly lastDelivered = new Map<string, string>();
  stopping = false;
  resumeUsed = false;
  reachedIdle = false;
  readonly crashTimes: number[] = []; // py deque[float]（单调秒）
  restartTask: RestartHandle | null = null;

  constructor(boot: AgentBoot, process: ManagedProcess) {
    this.boot = boot;
    this.process = process;
  }
}

/** 每 Agent 的 sink 代理：透传真 sink + 记 status（进程表）/ reachedIdle（降级判定）。 */
class AgentSink implements AdapterSink {
  private readonly entry: AgentEntry;
  private readonly real: AdapterSink;

  constructor(entry: AgentEntry, real: AdapterSink) {
    this.entry = entry;
    this.real = real;
  }

  async onStatusChanged(agentMemberId: string, status: AgentStatus, errorDetail: string | null = null): Promise<void> {
    this.entry.status = status;
    if (status === 'idle') this.entry.reachedIdle = true;
    await this.real.onStatusChanged(agentMemberId, status, errorDetail);
  }

  async onActivity(agentMemberId: string, detail: string): Promise<void> {
    await this.real.onActivity(agentMemberId, detail);
  }

  onUsage(event: Parameters<AdapterSink['onUsage']>[0]): void {
    this.real.onUsage(event);
  }

  onDiagnostic(event: DiagnosticEventIn): void {
    this.real.onDiagnostic(event);
  }
}

export interface RuntimeManagerOptions {
  serverUrl: string;
  apiKey: string;
  spawn?: SpawnFn | null;
  ulid?: () => string;
  now?: () => string;
}

/**
 * daemon 侧 runtime 管理器（A6 RuntimeAdapter 接口；DaemonClient / handlers 不变）。
 *
 * 按 `boot.runtime` 分派进程类（ClaudeCodeProcess / CodexProcess，E2 §1）——会话簿记 / 三档
 * 重置 / 崩溃熔断 / 去重游标骨架 runtime 无关原样复用。历史名 `ClaudeCodeAdapter` 保留为别名。
 */
export class RuntimeManager implements ManagerContract {
  readonly paths: DataPaths;
  // py `_agents`：测试/管理器分派用例直读面 → TS 公有 Map（登记差异）。
  readonly agents = new Map<string, AgentEntry>();

  private readonly serverUrl: string;
  private readonly apiKey: string;
  private readonly spawnFn: SpawnFn | null;
  private readonly ulidFn: () => string;
  private readonly nowFn: () => string;
  private sink: AdapterSink | null = null;

  constructor(paths: DataPaths, opts: RuntimeManagerOptions) {
    this.paths = paths;
    this.serverUrl = opts.serverUrl;
    this.apiKey = opts.apiKey;
    this.spawnFn = opts.spawn ?? null;
    this.ulidFn = opts.ulid ?? newUlid;
    this.nowFn = opts.now ?? nowIso;
  }

  bind(sink: AdapterSink): void {
    this.sink = sink;
  }

  // -------------------------------------------------------- 生命周期

  async start(boot: AgentBoot): Promise<boolean> {
    const aid = boot.agent_member_id;
    const existing = this.agents.get(aid);
    if (existing !== undefined && existing.process.isRunning()) {
      return false; // 已在跑 → noop（自然键幂等）
    }
    const resume = this.hasResumableSession(boot);
    await this.launch(boot, resume);
    return true;
  }

  private hasResumableSession(boot: AgentBoot): boolean {
    const key = RESUME_KEY[boot.runtime] ?? 'session_id';
    return Boolean(this.paths.readSession(boot.agent_member_id)[key]);
  }

  /** 按 runtime 分派进程类（E2 §1；codex 动态 import 对等 py 惰性 import）。 */
  private async newProcess(aid: string, runtime: Runtime): Promise<ManagedProcess> {
    let cls: ManagedProcessCtor;
    if (runtime === 'codex') {
      const mod = await import('./codex.ts');
      cls = mod.CodexProcess;
    } else {
      cls = ClaudeCodeProcess;
    }
    return new cls(
      aid,
      placeholderSink(), // 占位，launch 内即刻替换为 AgentSink
      this.paths,
      {
        serverUrl: this.serverUrl,
        apiKey: this.apiKey,
        spawn: this.spawnFn,
        onExit: (a, rc) => this.onProcessExit(a, rc),
        ulid: this.ulidFn,
        now: this.nowFn,
      },
    );
  }

  private async launch(boot: AgentBoot, resume: boolean): Promise<AgentEntry> {
    const aid = boot.agent_member_id;
    let entry = this.agents.get(aid);
    if (entry === undefined) {
      const process = await this.newProcess(aid, boot.runtime);
      entry = new AgentEntry(boot, process);
      this.agents.set(aid, entry);
    }
    entry.boot = boot;
    entry.stopping = false;
    entry.reachedIdle = false;
    entry.resumeUsed = resume;
    // 绑定每 Agent sink（含 status 记录）：py 双席位重绑对等（claude router 经转发跟随，
    // codex router 的 _sink 为活席位）。
    const agentSink = new AgentSink(entry, this.requireSink());
    entry.process._sink = agentSink;
    entry.process.router._sink = agentSink;
    await this.emit(entry, 'starting');
    await entry.process.start(boot, resume);
    // 就绪 idle（§5）：spawn 成功即可接收输入（stdin 缓冲）。实测本 CLI 的 init 帧在
    // 首个 stdin 输入后才到（E §11.3），就绪解耦于 init；会话确认另由 router.confirmed 记。
    await this.emit(entry, 'idle');
    return entry;
  }

  async stop(agentMemberId: string): Promise<boolean> {
    const entry = this.agents.get(agentMemberId);
    if (entry === undefined) {
      return false;
    }
    this.agents.delete(agentMemberId);
    entry.stopping = true;
    if (entry.restartTask !== null) {
      entry.restartTask.cancelled = true; // py restart_task.cancel() 对等（睡后复核阻止重拉起）
    }
    await entry.process.stop();
    await this.emit(entry, 'offline');
    return true;
  }

  async restart(boot: AgentBoot): Promise<void> {
    // 一档：保 session 保 Home。
    await this.respawn(boot, true);
  }

  async resetSession(boot: AgentBoot): Promise<void> {
    // 二档：新会话（清 session 簿记，保 Home）。
    this.paths.clearSession(boot.agent_member_id);
    await this.respawn(boot, false);
  }

  async resetFull(boot: AgentBoot): Promise<void> {
    // 三档：Home 已由 handler 清空；清 session + 新会话。
    this.paths.clearSession(boot.agent_member_id);
    await this.respawn(boot, false);
  }

  private async respawn(boot: AgentBoot, resume: boolean): Promise<void> {
    const aid = boot.agent_member_id;
    const entry = this.agents.get(aid);
    if (entry !== undefined) {
      entry.stopping = true;
      if (entry.restartTask !== null) {
        entry.restartTask.cancelled = true;
      }
      await entry.process.stop();
    }
    await this.launch(boot, resume);
  }

  async wake(agentMemberId: string, reason: WakeReason, refs: WakeRefs | null): Promise<boolean> {
    void reason;
    void refs;
    const entry = this.agents.get(agentMemberId);
    if (entry === undefined || entry.status === 'busy') {
      return false; // 未在跑 / 已清醒 → noop（deliver 照常）
    }
    await this.emit(entry, 'busy');
    return true;
  }

  async deliver(
    agentMemberId: string,
    channelId: string,
    messages: Array<Record<string, unknown>>,
    threadRootId: string | null,
  ): Promise<boolean> {
    const entry = this.agents.get(agentMemberId);
    if (entry === undefined || messages.length === 0) {
      return false;
    }
    // 契约 MessagePublic.id 必填，fail-close 对等 py pydantic（FAILED ack、游标不动）：缺 id 时
    // String(undefined)="undefined" 会入游标，且 'u' 高于全部 Crockford ULID 字符 → 该频道此后
    // 投递永久 noop。整批抛错 → handleInstr 捕获转 ack failed。
    for (const m of messages) {
      if (typeof m['id'] !== 'string' || m['id'] === '') {
        throw new Error('message.deliver 批含缺 id 消息（契约 MessagePublic.id 必填）');
      }
    }
    let maxId = String(messages[0]!['id']);
    for (const m of messages) {
      const mid = String(m['id']);
      if (mid > maxId) maxId = mid;
    }
    const prev = entry.lastDelivered.get(channelId);
    if (prev !== undefined && maxId <= prev) {
      return false; // 该频道已喂过的最大 message_id → noop 去重（§5.2，按频道）
    }
    entry.lastDelivered.set(channelId, maxId);
    entry.process.setTurnContext(channelId, threadRootId);
    await this.emit(entry, 'busy');
    // 渲染运行时无关正文（纪律 8）；载体封装归各 Process（claude / codex 各自特化）。
    await entry.process.feed(renderDeliver(messages, { threadRootId }));
    return true;
  }

  async inject(
    agentMemberId: string,
    body: string,
    source: Record<string, unknown>,
    diagnosticType: string,
  ): Promise<void> {
    const entry = this.agents.get(agentMemberId);
    if (entry === undefined) {
      return;
    }
    this.requireSink().onDiagnostic(
      this.managerDiag(agentMemberId, diagnosticType, { direction: 'sent', source }),
    );
    entry.process.setTurnContext(null, null);
    await this.emit(entry, 'busy');
    await entry.process.feed(renderInject(body, source));
  }

  // -------------------------------------------------------- 进程表 / Home

  processTable(): DaemonAgentState[] {
    return [...this.agents.entries()]
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
      .map(([aid, e]) => ({
        agent_member_id: aid,
        status: e.status,
        source_session: e.process.router.sessionId,
      }));
  }

  homePath(agentMemberId: string): string | null {
    const entry = this.agents.get(agentMemberId);
    return entry !== undefined ? entry.boot.home_path : null;
  }

  // -------------------------------------------------------- 崩溃熔断（§5）

  private async onProcessExit(agentMemberId: string, returncode: number | null): Promise<void> {
    const entry = this.agents.get(agentMemberId);
    if (entry === undefined) {
      return;
    }
    this.requireSink().onDiagnostic(
      this.managerDiag(agentMemberId, 'agent.process_exited', { exit_code: returncode }),
    );
    if (entry.stopping) {
      return; // 主动 stop/reset → 不拉起
    }
    const handle: RestartHandle = { promise: Promise.resolve(), cancelled: false };
    handle.promise = this.superviseRestart(entry, returncode, handle);
    handle.promise.catch(() => {}); // py create_task 未观测异常对等（不外抛）
    entry.restartTask = handle;
  }

  private async superviseRestart(entry: AgentEntry, returncode: number | null, handle: RestartHandle): Promise<void> {
    const aid = entry.boot.agent_member_id;
    const now = performance.now() / 1000; // py loop.time() 对等（单调秒）
    entry.crashTimes.push(now);
    while (entry.crashTimes.length > 0 && now - entry.crashTimes[0]! > CRASH_WINDOW_SEC) {
      entry.crashTimes.shift();
    }
    const attempt = entry.crashTimes.length;
    if (attempt > CRASH_MAX) {
      await this.emit(entry, 'error', 'crash_loop_giveup');
      return;
    }
    const delay = CRASH_BACKOFF[Math.min(attempt - 1, CRASH_BACKOFF.length - 1)] ?? 0;
    // resume 损坏降级：用了 resume 却从未确认会话（无 init/result）→ 冷启 + session_lost（§4）
    let resume = true;
    if (entry.resumeUsed && !entry.process.router.confirmed) {
      this.requireSink().onDiagnostic(this.managerDiag(aid, 'agent.session_lost', { attempt }));
      this.paths.clearSession(aid);
      resume = false;
    }
    this.requireSink().onDiagnostic(
      this.managerDiag(aid, 'agent.crash_restarted', {
        attempt,
        backoff_sec: delay,
        exit_code: returncode,
      }),
    );
    await sleep(delay * 1000);
    // py cancel 注入 → 取消标志 + stopping/身份判等（is not → ===）睡后复核
    if (handle.cancelled || entry.stopping || this.agents.get(aid) !== entry) {
      return;
    }
    await this.launch(entry.boot, resume);
  }

  // -------------------------------------------------------- 底座

  private requireSink(): AdapterSink {
    if (this.sink === null) {
      throw new Error('adapter not bound to sink');
    }
    return this.sink;
  }

  private async emit(entry: AgentEntry, status: AgentStatus, errorDetail: string | null = null): Promise<void> {
    entry.status = status;
    if (status === 'idle') entry.reachedIdle = true;
    await this.requireSink().onStatusChanged(entry.boot.agent_member_id, status, errorDetail);
  }

  private managerDiag(agentMemberId: string, dtype: string, payload: JsonObject): DiagnosticEventIn {
    return {
      agent_member_id: agentMemberId,
      type: dtype,
      channel_id: null,
      task_id: null,
      batch_id: null,
      payload,
      at: this.nowFn(),
    };
  }
}

// 历史名向后兼容（cli / 测试曾以 ClaudeCodeAdapter 引用管理器；M5 泛化为 RuntimeManager）。
export { RuntimeManager as ClaudeCodeAdapter };

// ------------------------------------------------------------ 小工具

function placeholderSink(): AdapterSink {
  return new NullSink();
}

class NullSink implements AdapterSink {
  async onStatusChanged(): Promise<void> {}
  async onActivity(): Promise<void> {}
  onUsage(): void {}
  onDiagnostic(): void {}
}
