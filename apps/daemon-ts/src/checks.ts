/**
 * M6 check 系统节点的本机命令执行器（契约 D §5.3 / §7；对等基准 = apps/daemon checks.py）。
 *
 * 命令在 Project 主工作区执行，stdout/stderr 合流后仅保留 UTF-8 尾部 4KB。超时在 Windows
 * 使用 `taskkill /F /T` 清理整棵进程树（校准条款 3：数组形式 spawn、code 0/128=成功、
 * GBK 输出不解析文本）；其它平台终止本次启动的 shell 进程。
 *
 * 与 py 版的接口差异（逐条登记，非行为改进）：
 * - py 取消 = asyncio task cancel（CancelledError 注入 await 点）；TS 无任务取消注入，
 *   取消经 AbortSignal 显式线程化（CheckProcessRunner 第三参），命中时抛 CheckCancelledError
 *   （对应 py CancelledError 不被 `except Exception` 捕获的穿透语义）。
 * - killProcessTree 单点 export 按 pid 收杀（签名 `(pid: number) => Promise<void>`），
 *   供 git/preview/deploy 复用（py 侧它们 import 私有 _kill_process_tree(proc)）。
 */

import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import type { CheckFinishedData, CheckRunData } from '@coagentia/contracts-ts';

import { TimeoutError, withTimeout } from './aio.ts';

export const CHECK_TIMEOUT_SEC = 30 * 60;
export const OUTPUT_TAIL_BYTES = 4 * 1024;

export interface CheckProcessResult {
  readonly exitCode: number;
  readonly outputTail: string;
}

/** 对应 py CancelledError 穿透位：runner 被取消时抛出，CheckRunner.run 不收敛为 127。 */
export class CheckCancelledError extends Error {}

export type CheckProcessRunner = (
  data: CheckRunData,
  timeoutSec: number,
  signal?: AbortSignal,
) => Promise<CheckProcessResult>;
export type CheckFinishedCallback = (data: CheckFinishedData) => Promise<void>;

function expanduser(p: string): string {
  if (p === '~') return os.homedir();
  if (p.startsWith('~/') || p.startsWith('~\\')) return path.join(os.homedir(), p.slice(2));
  return p;
}

/** 有界字节尾（对等 py _append_tail：超 4KB 即掐头）。 */
class TailBuffer {
  private buf: Buffer = Buffer.alloc(0);

  append(chunk: Buffer): void {
    this.buf = this.buf.length === 0 ? chunk : Buffer.concat([this.buf, chunk]);
    if (this.buf.length > OUTPUT_TAIL_BYTES) {
      this.buf = Buffer.from(this.buf.subarray(this.buf.length - OUTPUT_TAIL_BYTES));
    }
  }

  bytes(): Buffer {
    return this.buf;
  }
}

/** 把原始尾部收敛为编码后不超过 4KB 的合法 UTF-8 文本（先剥 0x80 续字节再 decode）。 */
function boundedUtf8Tail(raw: Buffer): string {
  let bytes = raw.subarray(Math.max(0, raw.length - OUTPUT_TAIL_BYTES));
  let start = 0;
  while (start < bytes.length && (bytes[start]! & 0xc0) === 0x80) start += 1;
  bytes = bytes.subarray(start);
  let text = bytes.toString('utf-8');
  while (Buffer.byteLength(text, 'utf-8') > OUTPUT_TAIL_BYTES) {
    // py text[1:] 按码点掐头；JS 需跳过代理对防拆半个字符。
    const first = text.codePointAt(0)!;
    text = text.slice(first > 0xffff ? 2 : 1);
  }
  return text;
}

/**
 * win32 杀整棵进程树单点（校准条款 3；git/preview/deploy 复用）。
 *
 * - `spawn('taskkill', ['/F','/T','/PID', pid])` 数组形式；code 0=杀净、code 128=进程已死
 *   （幂等成功）；其余退出码抛错（调用方按 py 语义可回落 proc.kill()）。
 * - taskkill 输出是 GBK：只排空管道防积压，不解析文本、不解码判断。
 * - 非 win32：SIGKILL 单进程（对等 py「其它平台终止本次启动的 shell 进程」），ESRCH 幂等成功。
 */
export async function killProcessTree(pid: number): Promise<void> {
  if (process.platform !== 'win32') {
    try {
      process.kill(pid, 'SIGKILL');
    } catch {
      // 进程已不存在 = 幂等成功（对齐 taskkill code 128 语义）。
    }
    return;
  }
  const code = await new Promise<number | null>((resolve, reject) => {
    const killer = spawn('taskkill', ['/F', '/T', '/PID', String(pid)], {
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    killer.stdout?.resume();
    killer.stderr?.resume();
    killer.on('error', (err) => reject(err));
    killer.on('close', (c) => resolve(c));
  });
  if (code !== 0 && code !== 128) {
    throw new Error(`taskkill /F /T /PID ${pid} exit code ${code}`);
  }
}

type ProcSettle = { kind: 'close'; code: number | null } | { kind: 'error'; message: string };

/** 在 repo 主工作区经平台 shell 执行既有 command，并有界采集输出尾。 */
export async function runCheckProcess(
  data: CheckRunData,
  timeoutSec: number = CHECK_TIMEOUT_SEC,
  signal?: AbortSignal,
): Promise<CheckProcessResult> {
  const repo = path.resolve(expanduser(data.repo_path));
  let isDir = false;
  try {
    isDir = fs.statSync(repo).isDirectory();
  } catch {
    isDir = false;
  }
  if (!isDir) {
    return { exitCode: 127, outputTail: `repo_path 不存在或不是目录：${repo}` };
  }

  const env = {
    ...process.env,
    LC_ALL: 'C.UTF-8',
    LANG: 'C.UTF-8',
    PYTHONUTF8: '1',
    PYTHONIOENCODING: 'utf-8',
  };
  // py 用 create_subprocess_shell（win32 = cmd.exe）+ stderr=STDOUT 合流；node 无 fd 级合流，
  // stdout/stderr 双管道按到达序并入同一尾缓冲（cal6：spawn 当拍同步挂消费者，stderr 永不裸放）。
  const proc = spawn(data.command, {
    cwd: repo,
    shell: true,
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const tail = new TailBuffer();
  proc.stdout?.on('data', (chunk: Buffer) => tail.append(chunk));
  proc.stderr?.on('data', (chunk: Buffer) => tail.append(chunk));

  let exitedFlag = false;
  // 超时时钟对齐 py wait_for(proc.wait())=进程退出（'exit'）；管道排空另按 py reader 3s 上限
  // 在 finally 收口（closedP='close'=流已定稿，cal6：定稿不挂 'exit'）。
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
        abortListener = () => reject(new CheckCancelledError('check run cancelled'));
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
    } else if (err instanceof CheckCancelledError) {
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
    // 对等 py finally suppress(wait_for(reader, 3.0))：给管道残余输出 ≤3s 定稿窗（含取消/超时路径）。
    try {
      await withTimeout(closedP, 3000);
    } catch {
      // py suppress：排空超时不外抛。
    }
  }

  if (timedOut) {
    tail.append(Buffer.from(`\n[check timeout after ${timeoutSec}s]\n`, 'utf-8'));
    return { exitCode: 124, outputTail: boundedUtf8Tail(tail.bytes()) };
  }
  if (outcome !== null && outcome.kind === 'error') {
    return { exitCode: 127, outputTail: boundedUtf8Tail(Buffer.from(outcome.message, 'utf-8')) };
  }
  const exitCode = outcome !== null && outcome.code !== null ? outcome.code : 1;
  return { exitCode, outputTail: boundedUtf8Tail(tail.bytes()) };
}

/** run_id 自然键执行器；同进程重发只重报终态，不重复跑命令。 */
export class CheckRunner {
  private readonly runner: CheckProcessRunner;
  private readonly timeoutSec: number;
  private readonly finished = new Map<string, CheckFinishedData>();
  private readonly running = new Map<string, { promise: Promise<void>; controller: AbortController }>();
  private closing = false;

  constructor(opts: { runner?: CheckProcessRunner; timeoutSec?: number } = {}) {
    this.runner = opts.runner ?? runCheckProcess;
    this.timeoutSec = opts.timeoutSec ?? CHECK_TIMEOUT_SEC;
  }

  async run(data: CheckRunData, signal?: AbortSignal): Promise<[boolean, CheckFinishedData]> {
    const known = this.finished.get(data.run_id);
    if (known !== undefined) return [false, known];
    let result: CheckProcessResult;
    try {
      result = await this.runner(data, this.timeoutSec, signal);
    } catch (err) {
      // py: CancelledError 是 BaseException 不被 except Exception 吞——取消穿透，不落终态。
      if (err instanceof CheckCancelledError) throw err;
      // 执行边界统一收敛为 check.failed（对等 py except Exception → 127）。
      const message = err instanceof Error ? err.message : String(err);
      result = { exitCode: 127, outputTail: boundedUtf8Tail(Buffer.from(message, 'utf-8')) };
    }
    const finishedData: CheckFinishedData = {
      run_id: data.run_id,
      node_id: data.node_id,
      status: result.exitCode === 0 ? 'success' : 'failed',
      exit_code: result.exitCode,
      output_tail: result.outputTail,
    };
    this.finished.set(data.run_id, finishedData);
    return [true, finishedData];
  }

  /** 后台启动长命令，使 instr 可立即 ack；同 run_id 在跑/终态均自然键 noop。 */
  start(data: CheckRunData, onFinished: CheckFinishedCallback): [boolean, CheckFinishedData | null] {
    if (this.closing) return [false, null];
    if (this.running.has(data.run_id)) return [false, null];
    const known = this.finished.get(data.run_id);
    if (known !== undefined) return [false, known];
    const controller = new AbortController();
    const promise = this.execute(data, onFinished, controller.signal);
    this.running.set(data.run_id, { promise, controller });
    // 对等 py add_done_callback：清 running 记忆 + 取走回调/落盘异常避免 unhandled rejection。
    void promise
      .catch(() => {})
      .finally(() => {
        this.running.delete(data.run_id);
      });
    return [true, null];
  }

  private async execute(
    data: CheckRunData,
    onFinished: CheckFinishedCallback,
    signal: AbortSignal,
  ): Promise<void> {
    const [, finishedData] = await this.run(data, signal);
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
