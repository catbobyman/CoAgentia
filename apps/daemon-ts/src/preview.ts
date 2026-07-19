/**
 * M7 K2 daemon 预览长驻 dev server 进程域（契约 D §5.3 / §7；对等基准 = apps/daemon preview.py）。
 *
 * `check.run` 是"跑完即止"的短命令；预览 dev server 是**长驻**：起进程 → 健康检查轮询 vs
 * 存活监控**并行竞速** → 可达上报 running 携 port / 夭折或超时上报 failed 携 log_tail（≤2KB）。
 * 自然键 = preview_session_id：已在跑重发 → noop + 补报现状端口；已停/未知 → noop。
 *
 * **端口唯一性靠进程内注册表，不靠 OS**（校准条款 5 / K2-cal 最关键坑）：win32 的 SO_REUSEADDR
 * 让同端口双绑不被 OS 拒绝，故 daemon 必须自持端口唯一性（assigned 集合 + Lock，撞则重取；
 * bind :0 取埠即弃）。**判定归 server、执行归 daemon**：回收/超时阈值判定在 server 或契约默认，
 * daemon 收指令即执行、只上报事实（port / log_tail / status）。win32 进程树终止 =
 * `taskkill /F /T`（复用 checks.ts killProcessTree，校准条款 3）。
 *
 * 与 py 版的接口差异（逐条登记，非行为改进）：
 * - py 起进程失败 = create_subprocess_shell 同步抛 OSError；node 的创建期失败（如 cwd 非目录）
 *   经 'error' 事件**异步**到达 → start() 内以 'spawn'/'error' 事件竞速一次收敛创建判定
 *   （语义同：起进程即失败 → (true, 预生成 failed)，端口不泄漏）。
 * - py stderr=STDOUT 是 fd 级合流；node 无 fd 级合流 → stdout/stderr 双管道按到达序并入
 *   同一 2KB 尾缓冲（cal6：spawn 当拍同步挂消费者，stderr 永不裸放）。
 * - py 的 reader task（_read_tail 拉式排空 + cancel 收尾）在 TS 是 push 式 'data' 监听——
 *   监听随流关闭自然终结，无需 cancel；_finalize_tail 的"等 reader 排空 ≤3s"对应等
 *   'close' 事件（流定稿，cal6）≤3s。
 * - py _await_ready = asyncio.wait FIRST_COMPLETED + task cancel；TS Promise.race 后无法
 *   取消底层等待 → 手工停止标志（校准条款），健康轮询循环在 ≤ connect_timeout+poll_interval
 *   一拍内自行退出，tcp 探测 socket/timer 逐次显式清理防泄漏。
 * - py _pick_free_port 同步 socket bind；TS net.createServer().listen 异步（cal5：
 *   显式 host 127.0.0.1 + exclusive:true，EADDRINUSE 走 error 事件异步判）。
 */

import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import * as net from 'node:net';

import type { PreviewStartData, PreviewStatusData } from '@coagentia/contracts-ts';

import { Lock, sleep, withTimeout } from './aio.ts';
import { killProcessTree } from './checks.ts';

// 参数默认（实现默认，非协议形状；出处见 PREVIEW-CALIBRATION §3.6 / 契约 D §5.3）。
export const HEALTH_TIMEOUT_SEC = 120.0; // 健康检查超时（契约 D §5.3）
const HEALTH_POLL_SEC = 0.5; // TCP 连通轮询间隔
const TCP_CONNECT_TIMEOUT_SEC = 0.5; // 单次探测连接超时
export const PREVIEW_LOG_TAIL_BYTES = 2 * 1024; // failed 携进程输出尾上限（契约 A v1.0.11 / D preview.status）
const _ACQUIRE_TRIES = 50;

export type PreviewStatus = 'starting' | 'running' | 'recycled' | 'failed';
export type PreviewStatusCallback = (data: PreviewStatusData) => Promise<void>;

/** 绑 127.0.0.1:0 取内核分配端口后立即释放（K2-cal §3.1 取端口法；cal5 异步 error 判）。 */
function pickFreePort(): Promise<number> {
  return new Promise<number>((resolve, reject) => {
    const srv = net.createServer();
    srv.once('error', (err) => reject(err));
    srv.listen({ port: 0, host: '127.0.0.1', exclusive: true }, () => {
      const addr = srv.address();
      const port = addr !== null && typeof addr === 'object' ? addr.port : 0;
      srv.close(() => resolve(port));
    });
  });
}

/** 把原始尾部收敛为编码后 ≤2KB 的合法 UTF-8 文本（镜像 checks.boundedUtf8Tail 但 2KB）。 */
function boundedPreviewTail(raw: Buffer): string {
  let bytes = raw.subarray(Math.max(0, raw.length - PREVIEW_LOG_TAIL_BYTES));
  let start = 0;
  while (start < bytes.length && (bytes[start]! & 0xc0) === 0x80) start += 1; // 去掉截断处的 UTF-8 续字节
  bytes = bytes.subarray(start);
  let text = bytes.toString('utf-8');
  while (Buffer.byteLength(text, 'utf-8') > PREVIEW_LOG_TAIL_BYTES) {
    // py text[1:] 按码点掐头；JS 需跳过代理对防拆半个字符。
    const first = text.codePointAt(0)!;
    text = text.slice(first > 0xffff ? 2 : 1);
  }
  return text;
}

/**
 * daemon 进程内端口唯一性缓解手段：已分配端口集合 + 锁（K2-cal §3.2 权威）。
 *
 * Windows 不拒绝重复绑定（SO_REUSEADDR），故 daemon 不能靠 OS 保证端口唯一；用进程内注册表在
 * 并发 preview.start 间串行分配，pickFreePort 结果撞注册表则重取；stop/failed/recycled 释放。
 */
export class _PortRegistry {
  private readonly assigned = new Set<number>();
  private readonly lock = new Lock();

  async acquire(): Promise<number> {
    return this.lock.runExclusive(async () => {
      for (let i = 0; i < _ACQUIRE_TRIES; i += 1) {
        const port = await pickFreePort();
        if (!this.assigned.has(port)) {
          this.assigned.add(port);
          return port;
        }
      }
      throw new Error('无法取得未占用的空闲端口');
    });
  }

  release(port: number): void {
    this.assigned.delete(port);
  }
}

/** 一个预览会话的进程域状态（自然键 = session_id；py _Preview dataclass 对应）。 */
class _Preview {
  status: PreviewStatus = 'starting';
  port: number | null = null;
  proc: ChildProcess | null = null;
  tail: Buffer = Buffer.alloc(0);
  /** py proc.wait() 对应：'exit' 事件（进程退出，管道未必定稿）。 */
  exited: Promise<void> | null = null;
  exitedFlag = false;
  /** py reader task 排空定稿对应：'close' 事件（管道残余输出已收，cal6 定稿只挂 'close'）。 */
  closed: Promise<void> | null = null;
  monitor: Promise<void> | null = null;
  stopping = false; // stop/shutdown 抢先标记：置位后 monitor 静默退出不上报 failed
  logTail: string | null = null;

  readonly sessionId: string;

  constructor(sessionId: string) {
    this.sessionId = sessionId;
  }

  /** 有界尾追加（对等 py _read_tail + del 前缀：仅保留末尾 2KB，防管道写满阻塞长驻进程）。 */
  appendTail(chunk: Buffer): void {
    this.tail = this.tail.length === 0 ? chunk : Buffer.concat([this.tail, chunk]);
    if (this.tail.length > PREVIEW_LOG_TAIL_BYTES) {
      this.tail = Buffer.from(this.tail.subarray(this.tail.length - PREVIEW_LOG_TAIL_BYTES));
    }
  }
}

/**
 * preview_session_id 自然键的长驻 dev server 管理器（CheckRunner 长驻变体）。
 *
 * - `start`：起进程后立即返回让 handler ack DONE，健康检查/存活监控在后台 monitor 上报；
 * - `stop`：杀树 + 上报 recycled；
 * - `waitClosed`：shutdown 逐个杀活跃子进程（清洁关闭无孤儿，K2-cal §3.4）。
 */
export class PreviewRunner {
  private readonly healthTimeout: number;
  private readonly pollInterval: number;
  private readonly connectTimeout: number;
  private readonly registry = new _PortRegistry();
  private readonly previews = new Map<string, _Preview>();
  private closing = false;

  constructor(
    opts: { healthTimeout?: number; pollInterval?: number; connectTimeout?: number } = {},
  ) {
    this.healthTimeout = opts.healthTimeout ?? HEALTH_TIMEOUT_SEC;
    this.pollInterval = opts.pollInterval ?? HEALTH_POLL_SEC;
    this.connectTimeout = opts.connectTimeout ?? TCP_CONNECT_TIMEOUT_SEC;
  }

  // ---------------------------------------------------------------- start（自然键幂等）

  /**
   * 起 dev server；同 session_id 已存在 → noop + 返回现状（含端口）供 handler 补报。
   *
   * 返回 [started, status]：started=true → ack DONE（健康检查异步经 monitor 上报）；
   * started=false → ack NOOP。status 非空则由 handler 补报（现状端口 / 预生成 failed）。
   */
  async start(
    data: PreviewStartData,
    reportCb: PreviewStatusCallback,
  ): Promise<[boolean, PreviewStatusData | null]> {
    const sessionId = data.preview_session_id;
    if (this.closing) return [false, null];
    const existing = this.previews.get(sessionId);
    if (existing !== undefined) {
      // 已在跑/已终态 → noop + 补报现状（"已在跑 → 上报端口"，契约 D §5.3）。
      return [false, this.statusOf(existing)];
    }

    const port = await this.registry.acquire();
    // 必须用 shell（非 exec）：%PORT%/$PORT 由 shell 展开，npm run dev 类命令本就是 shell 串
    // （K2-cal §3.1）。PORT 注入子进程环境，dev server 亦可读 process.env.PORT（约定优于配置）。
    const env = {
      ...process.env,
      PORT: String(port),
      PYTHONUTF8: '1',
      PYTHONIOENCODING: 'utf-8',
    };
    let proc: ChildProcess | null = null;
    let spawnError: Error | null = null;
    try {
      proc = spawn(data.dev_command, {
        cwd: data.worktree_path,
        shell: true,
        env,
        stdio: ['ignore', 'pipe', 'pipe'],
      });
    } catch (err) {
      spawnError = err instanceof Error ? err : new Error(String(err));
    }

    const pv = new _Preview(sessionId);
    if (proc !== null) {
      // cal6：spawn 当拍同步挂 stdout/stderr 消费者（否则子进程死亡残留数据静默丢、stderr 积压死锁）。
      const onData = (chunk: Buffer): void => pv.appendTail(chunk);
      proc.stdout?.on('data', onData);
      proc.stderr?.on('data', onData);
      pv.exited = new Promise<void>((resolve) => {
        proc!.once('exit', () => {
          pv.exitedFlag = true;
          resolve();
        });
      });
      pv.closed = new Promise<void>((resolve) => {
        proc!.on('close', () => resolve());
        proc!.on('error', () => resolve());
      });
      // py 在创建点同步 except OSError；node 创建失败经 'error' 事件异步到达 →
      // 'spawn'/'error' 竞速一次收敛创建判定（'error' 监听留驻兼防 unhandled 'error' 崩溃）。
      spawnError = await new Promise<Error | null>((resolve) => {
        proc!.once('spawn', () => resolve(null));
        proc!.once('error', (err) => resolve(err));
      });
    }
    if (spawnError !== null) {
      // 起进程即失败（如 worktree_path 非目录）→ 释放端口 + 记终态 + 预生成 failed 供补报。
      this.registry.release(port);
      pv.status = 'failed';
      pv.port = null;
      pv.proc = null;
      pv.logTail = boundedPreviewTail(Buffer.from(String(spawnError.message), 'utf-8'));
      this.previews.set(sessionId, pv);
      return [true, this.statusOf(pv)];
    }

    pv.port = port;
    pv.proc = proc;
    this.previews.set(sessionId, pv);
    // 对等 py _finish_monitor（add_done_callback 取走异常）：catch 收敛为已决 Promise。
    pv.monitor = this.runMonitor(pv, reportCb).catch(() => {});
    return [true, null];
  }

  // ------------------------------------------------------------ 后台 monitor（健康 vs 存活竞速）

  private async runMonitor(pv: _Preview, reportCb: PreviewStatusCallback): Promise<void> {
    // py _run_monitor finally 处 cancel reader task；TS 的 'data' 监听随流关闭自然终结，无需收尾。
    await this.monitor(pv, reportCb);
  }

  private async monitor(pv: _Preview, reportCb: PreviewStatusCallback): Promise<void> {
    const reachable = await this.awaitReady(pv);
    if (pv.stopping) {
      return; // stop/shutdown 抢先杀进程 → 由其上报 recycled，monitor 不再上报。
    }
    if (reachable) {
      pv.status = 'running';
      await reportCb({
        preview_session_id: pv.sessionId,
        status: 'running',
        port: pv.port,
        log_tail: null,
      });
      // 就绪后续挂存活监控：进程退出（dev server 自行崩溃/被外力杀）→ failed 携 log_tail。
      await pv.exited!;
      if (pv.stopping) return;
      await this.reportFailed(pv, reportCb);
      return;
    }
    // 未就绪（健康超时或进程夭折）→ 杀树（进程若还活）+ failed 携 log_tail。
    await this.killPreviewTree(pv);
    if (pv.stopping) return;
    await this.reportFailed(pv, reportCb);
  }

  private async reportFailed(pv: _Preview, reportCb: PreviewStatusCallback): Promise<void> {
    pv.status = 'failed';
    pv.logTail = await this.finalizeTail(pv);
    await reportCb({
      preview_session_id: pv.sessionId,
      status: 'failed',
      port: null,
      log_tail: pv.logTail,
    });
    if (pv.port !== null) {
      this.registry.release(pv.port);
    }
  }

  /**
   * 健康检查（TCP 轮询）与存活监控（'exit'）**并行竞速** FIRST_COMPLETED。
   *
   * 坏命令/夭折进程先退出 → 立即判未就绪，不空等 120s 健康超时（K2-cal §3.5）。
   * py 竞速后 cancel 败者任务；TS race 后无法取消底层等待 → 手工停止标志 + 回收等待
   * （健康循环 ≤ connect_timeout+poll_interval 一拍内自行退出，socket/timer 已逐次清理）。
   */
  private async awaitReady(pv: _Preview): Promise<boolean> {
    const stop = { flag: false };
    let healthResult = false;
    const healthP = this.healthCheck(pv.port, stop).then((ok) => {
      healthResult = ok;
      return 'health' as const;
    });
    const exitP = pv.exited!.then(() => 'exit' as const);
    const winner = await Promise.race([healthP, exitP]);
    stop.flag = true;
    await healthP; // 等健康循环真正退出（有界残余），防 timer/socket 泄漏。
    return winner === 'health' && healthResult;
  }

  private async healthCheck(port: number | null, stop: { flag: boolean }): Promise<boolean> {
    if (port === null) return false;
    const deadline = Date.now() + this.healthTimeout * 1000;
    while (Date.now() < deadline && !stop.flag) {
      if (await this.tcpReachable(port)) return true;
      if (stop.flag) return false;
      await sleep(this.pollInterval * 1000);
    }
    return false;
  }

  private tcpReachable(port: number): Promise<boolean> {
    return new Promise<boolean>((resolve) => {
      const sock = net.connect({ port, host: '127.0.0.1' });
      const timer = setTimeout(() => {
        sock.destroy();
        resolve(false);
      }, this.connectTimeout * 1000);
      sock.once('connect', () => {
        clearTimeout(timer);
        sock.destroy();
        resolve(true);
      });
      sock.once('error', () => {
        clearTimeout(timer);
        sock.destroy();
        resolve(false);
      });
    });
  }

  /** 收尾 tail：等管道定稿（'close'）≤3s 后再有界收敛（进程已死 → 定稿很快；py reader 排空对应）。 */
  private async finalizeTail(pv: _Preview): Promise<string> {
    if (pv.closed !== null) {
      try {
        await withTimeout(pv.closed, 3000);
      } catch {
        // py suppress(wait_for(shield(reader), 3.0))：排空超时不外抛。
      }
    }
    return boundedPreviewTail(pv.tail);
  }

  /** 对等 py checks._kill_process_tree(proc)：win32 taskkill 树杀，失败回落 proc.kill，≤3s 等收敛。 */
  private async killPreviewTree(pv: _Preview): Promise<void> {
    const proc = pv.proc;
    if (proc === null || pv.exitedFlag) return;
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
    if (pv.closed !== null) {
      try {
        await withTimeout(pv.closed, 3000);
      } catch {
        // py suppress：等收敛超时不外抛。
      }
    }
  }

  // ---------------------------------------------------------------- stop（自然键幂等）

  /** 杀树 + 上报 recycled；已停/未知/已终态 → noop（契约 D §5.3；回收判定在 server）。 */
  async stop(sessionId: string): Promise<[boolean, PreviewStatusData | null]> {
    const pv = this.previews.get(sessionId);
    if (pv === undefined || pv.status === 'recycled' || pv.status === 'failed') {
      return [false, null];
    }
    pv.stopping = true; // 抢先置位：monitor 见 stopping 静默退出，不抢报 failed。
    if (pv.proc !== null) {
      await this.killPreviewTree(pv);
    }
    if (pv.monitor !== null) {
      try {
        await withTimeout(pv.monitor, 5000);
      } catch {
        // py suppress(wait_for(shield(monitor), 5.0))：收敛超时不外抛。
      }
    }
    if (pv.port !== null) {
      this.registry.release(pv.port);
    }
    pv.status = 'recycled';
    return [
      true,
      { preview_session_id: sessionId, status: 'recycled', port: null, log_tail: null },
    ];
  }

  // ---------------------------------------------------------------- shutdown（清洁关闭无孤儿）

  cancel(): void {
    this.closing = true;
  }

  /**
   * 杀所有活跃（starting/running）预览子进程 + 释放端口，等 monitor 收敛（K2-cal §3.4）。
   *
   * stopping 标志抢先置位（monitor 见之静默退出不抢报 failed）；端口释放恰一次（终态检查兜底）。
   */
  private async killActive(): Promise<void> {
    const monitors: Array<Promise<void>> = [];
    for (const pv of [...this.previews.values()]) {
      if ((pv.status === 'starting' || pv.status === 'running') && !pv.stopping) {
        pv.stopping = true;
        if (pv.proc !== null) {
          try {
            await this.killPreviewTree(pv);
          } catch {
            // py suppress(Exception)：单个杀失败不阻断其余收尾。
          }
        }
        if (pv.port !== null) {
          this.registry.release(pv.port);
        }
        pv.status = 'recycled';
      }
      // py 判 not monitor.done() 再收；TS 已决 Promise 进 allSettled 即时归位，语义等价。
      if (pv.monitor !== null) monitors.push(pv.monitor);
    }
    if (monitors.length > 0) {
      await Promise.allSettled(monitors);
    }
  }

  /** shutdown：逐个 taskkill 所有活跃预览子进程，等 monitor 收敛（K2-cal §3.4）。 */
  async waitClosed(): Promise<void> {
    this.closing = true;
    await this.killActive();
  }

  // ---------------------------------------------------------------- 进程表快照（hello v1.0.5）

  /**
   * 预览会话进程表快照（hello.previews，契约 D §4.1 v1.0.5；与 adapter.processTable 同义）。
   *
   * **含终态记录**（failed/recycled 不剔除）：断连期上报 preview.status 是 best-effort 丢弃，
   * 重连 hello 携全量快照让 server 对账 #9 恢复丢失的终态（CAS 幂等，已一致则 noop）。
   */
  processTable(): PreviewStatusData[] {
    return [...this.previews.values()].map((pv) => this.statusOf(pv));
  }

  // ---------------------------------------------------------------- 内部

  /** 由当前进程域状态构造上报帧：running/starting 携 port，failed 携 log_tail。 */
  private statusOf(pv: _Preview): PreviewStatusData {
    const port = pv.status === 'starting' || pv.status === 'running' ? pv.port : null;
    const logTail = pv.status === 'failed' ? pv.logTail : null;
    return {
      preview_session_id: pv.sessionId,
      status: pv.status,
      port,
      log_tail: logTail,
    };
  }
}
