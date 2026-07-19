/**
 * M7b K4 daemon 部署命令执行器（契约 D §5.3 / §7；对等基准 = apps/daemon deploy.py）。
 *
 * `check.run` 是「跑完即止」且只回终态尾；部署命令须**流式**回日志（deploy.log 逐批携单调
 * chunk_seq）+ 结束回 deploy.finished（status/exit_code/url）。判定归 server、执行归 daemon：
 * 30min 超时/杀树阈值走契约默认，daemon 收 deploy.run 即在 repo 主工作区跑命令、逐批上报日志、
 * 结束上报终态。自然键 = deployment_id：已在跑重发 → noop；已终态重发 → 重报终态（不重跑，
 * 副作用不可重放，铁律 3）。win32 进程树终止 = `taskkill /F /T`（复用 checks.killProcessTree）。
 *
 * URL 提取约定：部署工具（Vercel/Netlify 等）惯例把最终 URL 打在输出末尾——取**最后一个**匹配到
 * 的 `https?://\S+`；仅 success 吐 url，failed/超时吐 null。
 *
 * 与 py 版的接口差异（逐条登记，非行为改进）：
 * - 流式三触发重构：py 用 `wait_for(stream.readline(), 0.5)` 超时即 flush（每次 readline 重开
 *   0.5s 静默窗）；TS 无带超时的 readline → 行事件 + 静默计时器实现（每行到达重置 0.5s 计时器、
 *   满 20 行立即 flush、EOF flush 残批）——满批/静默/EOF 三触发语义逐条保住，on_log 批经串行
 *   Promise 链保序（py 靠 await on_log 天然串行）。
 * - 合流：py `stderr=STDOUT` fd 级合流单读；node 无 fd 级合流 → stdout/stderr 双管道各自按
 *   校准条款 2 切行后按到达序汇入同一批器（cal6：spawn 当拍同步挂消费者，stderr 永不裸放）。
 * - 行读法 = 校准条款 2：Buffer 累积按 `\n` 字节切分 + CRLF 容忍 + **32MB 行上限**（py 侧
 *   readline 默认 64KB limit 超限抛错崩 streamer；TS 侧超限把已累积段强制切出为一行继续读，
 *   不崩读循环、不无界累积）。
 * - 取消：py = asyncio task cancel（CancelledError 注入 await 点）；TS 无任务取消注入，取消经
 *   AbortSignal 显式线程化（DeployProcessRunner 第四参），命中时杀树后抛 DeployCancelledError
 *   （对应 py CancelledError 不被 `except Exception` 捕获的穿透语义，不落终态）。
 * - py 启动点 `except OSError` 同步捕获 → 127；node spawn 失败经 'error' 事件异步到达，
 *   收敛为同一条「部署进程启动失败」日志 + 127。
 * - 超时标记行 py 用 `{timeout_sec:g}` 格式化；TS 用模板字符串数字默认字符串化（常用量纲
 *   1800/0.2 等输出一致，仅极端数量级的科学计数法边界不同）。
 */

import { spawn } from 'node:child_process';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import type { DeployFinishedData, DeployLogReportData, DeployRunData } from '@coagentia/contracts-ts';

import { TimeoutError, withTimeout } from './aio.ts';
import { killProcessTree } from './checks.ts';

export const DEPLOY_TIMEOUT_SEC = 30 * 60; // 契约 D §5.3：部署超时 30min（同 check 上限口径）
const LOG_BATCH_LINES = 20; // 每批最多行数（流式响应性；满批或 0.5s 静默或 EOF 即 flush）
const LOG_BATCH_IDLE_SEC = 0.5; // 静默 flush 间隔
const MAX_LINE_BYTES = 32 * 1024 * 1024; // 校准条款 2：行累积上限（node 累积无界须自设）
const URL_RE = /https?:\/\/\S+/g;

export interface DeployProcessResult {
  readonly exitCode: number | null; // 超时 = null（契约 D §5.3 deploy.finished.exit_code null）
  readonly url: string | null;
}

/** 对应 py CancelledError 穿透位：runner 被取消时抛出，DeployRunner 不收敛为终态。 */
export class DeployCancelledError extends Error {}

// onLog(lines)：DeployRunner 注入闭包，累加 chunk_seq 后转 report_deploy_log。
export type LogBatchCallback = (lines: string[]) => Promise<void>;
export type DeployLogCallback = (data: DeployLogReportData) => Promise<void>;
export type DeployFinishedCallback = (data: DeployFinishedData) => Promise<void>;

export type DeployProcessRunner = (
  data: DeployRunData,
  onLog: LogBatchCallback,
  timeoutSec: number,
  signal?: AbortSignal,
) => Promise<DeployProcessResult>;

function expanduser(p: string): string {
  if (p === '~') return os.homedir();
  if (p.startsWith('~/') || p.startsWith('~\\')) return path.join(os.homedir(), p.slice(2));
  return p;
}

/**
 * 校准条款 2 行读法：Buffer 累积按 `\n` 字节切行；行文本 UTF-8 decode（无效序列 → U+FFFD，
 * 对等 py errors="replace"）+ rstrip("\r\n")（CRLF 容忍）；EOF 残段作末行吐出（对等 py
 * readline 在 EOF 返回无换行残段）；未终止行累积超 32MB 即强制切出为一行继续（不崩读循环）。
 */
class LineSplitter {
  private pending: Buffer = Buffer.alloc(0);

  private readonly onLine: (text: string) => void;

  constructor(onLine: (text: string) => void) {
    this.onLine = onLine;
  }

  push(chunk: Buffer): void {
    let buf = this.pending.length === 0 ? chunk : Buffer.concat([this.pending, chunk]);
    let idx: number;
    while ((idx = buf.indexOf(0x0a)) >= 0) {
      this.emit(buf.subarray(0, idx));
      buf = buf.subarray(idx + 1);
    }
    if (buf.length > MAX_LINE_BYTES) {
      this.emit(buf);
      buf = Buffer.alloc(0);
    }
    this.pending = buf;
  }

  eof(): void {
    if (this.pending.length > 0) {
      this.emit(this.pending);
      this.pending = Buffer.alloc(0);
    }
  }

  private emit(bytes: Buffer): void {
    this.onLine(bytes.toString('utf-8').replace(/[\r\n]+$/u, ''));
  }
}

/**
 * 行 → 批（对等 py _stream_output 的批量语义）：满 LOG_BATCH_LINES 立即 flush、静默
 * LOG_BATCH_IDLE_SEC 无新行 flush、EOF（drain）flush 残批；onLog 经串行 Promise 链保序
 * （py 靠 `await on_log(batch)` 天然串行）。onLog 抛错后停止后续投递（对等 py streamer
 * task 因 on_log 异常终止、finally suppress）。
 */
class LogBatcher {
  private batch: string[] = [];
  private idleTimer: ReturnType<typeof setTimeout> | undefined;
  private chain: Promise<void> = Promise.resolve();
  private dead = false;

  private readonly onLog: LogBatchCallback;

  constructor(onLog: LogBatchCallback) {
    this.onLog = onLog;
  }

  pushLine(text: string): void {
    if (this.dead) return; // on_log 已异常终止投递（对等 py streamer 已死不再读行）。
    this.batch.push(text);
    if (this.batch.length >= LOG_BATCH_LINES) {
      this.flush();
      return;
    }
    if (this.idleTimer !== undefined) clearTimeout(this.idleTimer);
    this.idleTimer = setTimeout(() => this.flush(), LOG_BATCH_IDLE_SEC * 1000);
  }

  private flush(): void {
    if (this.idleTimer !== undefined) {
      clearTimeout(this.idleTimer);
      this.idleTimer = undefined;
    }
    if (this.dead || this.batch.length === 0) return;
    const lines = this.batch;
    this.batch = [];
    this.chain = this.chain
      .then(() => this.onLog(lines))
      .catch(() => {
        this.dead = true; // 对等 py：on_log 异常杀 streamer，后续批不再投递。
      });
  }

  /** EOF 收口：flush 残批并等串行链排空（对等 py streamer 自然跑完）。 */
  async drain(): Promise<void> {
    this.flush();
    await this.chain;
  }
}

type ProcSettle = { kind: 'close'; code: number | null } | { kind: 'error'; message: string };

/** 在 repo 主工作区经平台 shell 执行部署命令，流式回日志 + 提取末行 URL。 */
export async function runDeployProcess(
  data: DeployRunData,
  onLog: LogBatchCallback,
  timeoutSec: number = DEPLOY_TIMEOUT_SEC,
  signal?: AbortSignal,
): Promise<DeployProcessResult> {
  const repo = path.resolve(expanduser(data.repo_path));
  let isDir = false;
  try {
    isDir = fs.statSync(repo).isDirectory();
  } catch {
    isDir = false;
  }
  if (!isDir) {
    await onLog([`repo_path 不存在或不是目录：${repo}`]);
    return { exitCode: 127, url: null };
  }

  const env = {
    ...process.env,
    LC_ALL: 'C.UTF-8',
    LANG: 'C.UTF-8',
    PYTHONUTF8: '1',
    PYTHONIOENCODING: 'utf-8',
  };
  // py 用 create_subprocess_shell（win32 = cmd.exe）+ stderr=STDOUT 合流；node 无 fd 级合流，
  // stdout/stderr 双管道各自切行后按到达序汇入同一批器（cal6：spawn 当拍同步挂消费者）。
  const proc = spawn(data.command, {
    cwd: repo,
    shell: true,
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  // 对等 py url_holder：逐行 finditer 保留最后一个匹配的 URL。
  let lastUrl: string | null = null;
  const batcher = new LogBatcher(onLog);
  const handleLine = (text: string): void => {
    for (const match of text.match(URL_RE) ?? []) lastUrl = match;
    batcher.pushLine(text);
  };
  const outSplitter = new LineSplitter(handleLine);
  const errSplitter = new LineSplitter(handleLine);
  proc.stdout?.on('data', (chunk: Buffer) => outSplitter.push(chunk));
  proc.stderr?.on('data', (chunk: Buffer) => errSplitter.push(chunk));

  let exitedFlag = false;
  // 超时时钟对齐 py wait_for(proc.wait())=进程退出（'exit'）；流定稿（'close'）另在 finally
  // 按 py suppress(wait_for(streamer, 5.0)) 收口（cal6：定稿只挂 'close'）。
  const settled = new Promise<ProcSettle>((resolve) => {
    proc.on('exit', (code) => {
      exitedFlag = true;
      resolve({ kind: 'close', code });
    });
    // py 在创建点 except OSError → 127；node 的 spawn 失败经 error 事件异步到达。
    proc.on('error', (err) => resolve({ kind: 'error', message: err.message }));
  });
  const closedP = new Promise<void>((resolve) => {
    proc.on('close', () => resolve());
    proc.on('error', () => resolve());
  });
  // 流定稿后吐 EOF 残行并排空批链（对等 py streamer task 跑到 EOF + 尾批 flush）。
  const streamerP = closedP.then(async () => {
    outSplitter.eof();
    errSplitter.eof();
    await batcher.drain();
  });

  /** 对等 py _kill_process_tree(proc)：win32 taskkill 树杀，失败回落 proc.kill，≤3s 等收敛。 */
  const killTreeLocal = async (): Promise<void> => {
    if (exitedFlag) return;
    let killedTree = false;
    if (process.platform === 'win32' && proc.pid !== undefined) {
      try {
        await killProcessTree(proc.pid);
        killedTree = true;
      } catch {
        killedTree = false;
      }
    }
    if (!killedTree) {
      try {
        proc.kill('SIGKILL');
      } catch {
        // 已不存在（py suppress ProcessLookupError 对应位）。
      }
    }
    try {
      await withTimeout(closedP, 3000);
    } catch {
      // py suppress：等收敛超时不外抛。
    }
  };

  let abortListener: (() => void) | undefined;
  const abortP: Promise<never> | null = signal
    ? new Promise<never>((_, reject) => {
        abortListener = () => reject(new DeployCancelledError('deploy run cancelled'));
        if (signal.aborted) abortListener();
        else signal.addEventListener('abort', abortListener, { once: true });
      })
    : null;
  abortP?.catch(() => {
    // 预挂接消费：abort 在 settled 之后到达时不产生 unhandled rejection。
  });

  let outcome: ProcSettle | null = null;
  let timedOut = false;
  try {
    const waited = abortP === null ? settled : Promise.race([settled, abortP]);
    outcome = await withTimeout(waited, timeoutSec * 1000);
  } catch (err) {
    if (err instanceof TimeoutError) {
      timedOut = true;
      await killTreeLocal();
    } else if (err instanceof DeployCancelledError) {
      // py: except CancelledError → shield(kill) 后 re-raise。
      await killTreeLocal();
      throw err;
    } else {
      throw err;
    }
  } finally {
    if (signal !== undefined && abortListener !== undefined) {
      signal.removeEventListener('abort', abortListener);
    }
    // 对等 py finally suppress(wait_for(streamer, 5.0))：给流残余输出 ≤5s 定稿窗（含取消/超时路径）。
    try {
      await withTimeout(streamerP, 5000);
    } catch {
      // py suppress：排空超时不外抛。
    }
  }

  if (timedOut) {
    await onLog([`[deploy timeout after ${timeoutSec}s]`]);
    return { exitCode: null, url: null }; // 超时 = failed，exit_code null
  }
  if (outcome !== null && outcome.kind === 'error') {
    await onLog([`部署进程启动失败：${outcome.message}`]);
    return { exitCode: 127, url: null };
  }
  const exitCode = outcome !== null && outcome.code !== null ? outcome.code : 1;
  const url = exitCode === 0 ? lastUrl : null; // 仅 success 吐 url
  return { exitCode, url };
}

/**
 * deployment_id 自然键的部署执行器（CheckRunner 流式变体）。
 *
 * - `start`：起后台 Promise 跑 `execute` 立即返回让 instr ack DONE；已在跑/已终态 → noop；
 * - chunk_seq 从 0 单调递增（per deployment_id）；
 * - `cancel/waitClosed`：shutdown 杀活跃部署树（不留孤儿；py = task.cancel，TS = AbortSignal）。
 */
export class DeployRunner {
  private readonly runner: DeployProcessRunner;
  private readonly timeoutSec: number;
  private readonly finished = new Map<string, DeployFinishedData>();
  private readonly running = new Map<
    string,
    { promise: Promise<void>; controller: AbortController }
  >();
  private readonly chunkSeq = new Map<string, number>();
  private closing = false;

  constructor(opts: { runner?: DeployProcessRunner; timeoutSec?: number } = {}) {
    this.runner = opts.runner ?? runDeployProcess;
    this.timeoutSec = opts.timeoutSec ?? DEPLOY_TIMEOUT_SEC;
  }

  /** 后台起部署命令使 instr 立即 ack；同 deployment_id 在跑 → noop、终态 → 返回终态供重报。 */
  start(
    data: DeployRunData,
    onLog: DeployLogCallback,
    onFinished: DeployFinishedCallback,
  ): [boolean, DeployFinishedData | null] {
    const did = data.deployment_id;
    if (this.closing) return [false, null];
    if (this.running.has(did)) return [false, null];
    const known = this.finished.get(did);
    if (known !== undefined) return [false, known];
    const controller = new AbortController();
    const promise = this.execute(data, onLog, onFinished, controller.signal);
    this.running.set(did, { promise, controller });
    // 对等 py add_done_callback：清 running 记忆 + 取走回调/落盘异常避免 unhandled rejection。
    void promise
      .catch(() => {})
      .finally(() => {
        this.running.delete(did);
      });
    return [true, null];
  }

  private async execute(
    data: DeployRunData,
    onLog: DeployLogCallback,
    onFinished: DeployFinishedCallback,
    signal: AbortSignal,
  ): Promise<void> {
    const did = data.deployment_id;

    const logCb: LogBatchCallback = async (lines) => {
      const seq = this.chunkSeq.get(did) ?? 0;
      this.chunkSeq.set(did, seq + 1);
      await onLog({ deployment_id: did, chunk_seq: seq, lines });
    };

    let result: DeployProcessResult;
    try {
      result = await this.runner(data, logCb, this.timeoutSec, signal);
    } catch (err) {
      // py: CancelledError 是 BaseException 不被 except Exception 吞——取消穿透，不落终态。
      if (err instanceof DeployCancelledError) throw err;
      result = { exitCode: 127, url: null }; // 执行边界统一收敛为 deploy.failed
    }
    const finishedData: DeployFinishedData = {
      deployment_id: did,
      status: result.exitCode === 0 ? 'success' : 'failed',
      exit_code: result.exitCode,
      url: result.url,
    };
    this.finished.set(did, finishedData);
    await onFinished(finishedData);
  }

  cancel(): void {
    this.closing = true;
    for (const entry of [...this.running.values()]) entry.controller.abort();
  }

  async waitClosed(): Promise<void> {
    this.cancel();
    if (this.running.size > 0) {
      await Promise.allSettled([...this.running.values()].map((entry) => entry.promise));
    }
  }
}
