/**
 * M7 K2 daemon 预览长驻进程域（PreviewRunner）：真子进程实机测试（对等基准 = py test_preview.py）。
 *
 * dev_command 用零依赖命令起长驻 HTTP server；每测 finally 经 `runner.waitClosed()` 逐个 taskkill
 * 活跃子进程收尾（无孤儿）。win32 专属探针（netstat 反查孙 PID / taskkill 杀树 / 存活监控）以
 * it.runIf 门控，跨平台不变量（端口注入/幂等/失败日志尾/端口注册表）通吃。
 *
 * py→TS 移植登记（非行为改进）：
 * - py 用 sys.executable(python -m http.server %PORT%) 起 dev server；TS 用 process.execPath(node)
 *   + 落盘 devserver.cjs 等价替换（checks.test.ts 同款：避免测试依赖 python 解释器在位），
 *   PORT 仍经平台 shell 展开（win32 %PORT% / posix $PORT）传参，语义检查点不变。
 * - 坏命令探针 py = `python -m coagentia_no_such_module_xyz`（"No module named"）；TS = node 跑
 *   不存在脚本（"Cannot find module"），同为"进程速死 + stderr 进 2KB 尾"。
 * - py urllib 校验 HTTP 200；TS 用全局 fetch（node ≥22 内置）。
 * - netstat/tasklist/taskkill 探针输出按校准条款 3 只判退出码与 ASCII 字段（GBK 不解码判文本）。
 */

import { spawnSync } from 'node:child_process';
import * as fs from 'node:fs';
import * as net from 'node:net';
import * as os from 'node:os';
import * as path from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import type { PreviewStartData, PreviewStatusData } from '@coagentia/contracts-ts';

import { sleep } from '../src/aio.ts';
import { PreviewRunner, _PortRegistry } from '../src/preview.ts';
import { newUlid } from '../src/util.ts';
import { until } from './helpers.ts';

let tmp: string;

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'coagentia-preview-'));
});

afterEach(() => {
  fs.rmSync(tmp, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
});

const DEVSERVER_JS = [
  "const http = require('node:http');",
  'const port = Number(process.argv[2]);',
  "const server = http.createServer((req, res) => { res.statusCode = 200; res.end('ok'); });",
  "server.listen(port, '127.0.0.1');",
  '',
].join('\n');

/** 健康的零依赖长驻命令；PORT 引用由平台 shell 展开（win32 %PORT% / posix $PORT）。 */
function devCommand(): string {
  const script = path.join(tmp, 'devserver.cjs');
  fs.writeFileSync(script, DEVSERVER_JS, 'utf-8');
  const portRef = process.platform === 'win32' ? '%PORT%' : '$PORT';
  return `"${process.execPath}" "${script}" ${portRef}`;
}

function startData(worktree: string, command?: string): PreviewStartData {
  return {
    preview_session_id: newUlid(),
    task_id: newUlid(),
    worktree_path: worktree,
    dev_command: command ?? devCommand(),
  };
}

/** 收集 report_cb 上报的 preview.status 帧。 */
class Reports {
  items: PreviewStatusData[] = [];

  cb = async (data: PreviewStatusData): Promise<void> => {
    this.items.push(data);
  };

  byStatus(status: string): PreviewStatusData[] {
    return this.items.filter((d) => d.status === status);
  }
}

async function httpStatus(port: number): Promise<number> {
  const res = await fetch(`http://127.0.0.1:${port}/`, { signal: AbortSignal.timeout(3000) });
  await res.arrayBuffer(); // 排空 body 防连接悬挂
  return res.status;
}

function tcpReachable(port: number): Promise<boolean> {
  return new Promise<boolean>((resolve) => {
    const sock = net.connect({ port, host: '127.0.0.1' });
    const timer = setTimeout(() => {
      sock.destroy();
      resolve(false);
    }, 500);
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

/** netstat -ano 反查监听 127.0.0.1:<port> 的孙进程 PID（win32；只判 ASCII 字段）。 */
function findListeningPid(port: number): number | null {
  const r = spawnSync('netstat', ['-ano', '-p', 'TCP'], { encoding: 'utf-8' });
  for (const line of (r.stdout ?? '').split(/\r?\n/)) {
    const parts = line.trim().split(/\s+/);
    if (
      parts.length >= 5 &&
      parts[0] === 'TCP' &&
      parts[3] === 'LISTENING' &&
      parts[1]!.endsWith(`:${port}`)
    ) {
      const pid = Number(parts[4]);
      return Number.isInteger(pid) ? pid : null;
    }
  }
  return null;
}

function pidAlive(pid: number): boolean {
  const r = spawnSync('tasklist', ['/FI', `PID eq ${pid}`, '/NH'], { encoding: 'utf-8' });
  return (r.stdout ?? '').includes(String(pid));
}

function taskkillPid(pid: number): void {
  spawnSync('taskkill', ['/F', '/T', '/PID', String(pid)], { encoding: 'utf-8' });
}

describe('preview 长驻进程域（契约 D §5.3/§7）', () => {
  // ------------------------------------------------------------------------- 跨平台

  it('start 注入 PORT 且上报 running 后 HTTP 200（test_start_injects_port_and_reports_running_http_200）', async () => {
    const runner = new PreviewRunner({ healthTimeout: 15.0, pollInterval: 0.1 });
    const reports = new Reports();
    const data = startData(tmp);
    try {
      const [started, status] = await runner.start(data, reports.cb);
      expect(started).toBe(true); // 起进程即 ack DONE，健康检查异步上报
      expect(status).toBeNull();
      await until(() => reports.byStatus('running').length > 0, 15_000);
      const running = reports.byStatus('running').at(-1)!;
      expect(running.port).not.toBeNull();
      expect(running.preview_session_id).toBe(data.preview_session_id);
      // 健康检查可达 + HTTP 200 = PORT 确注入且 dev server 绑到分配端口
      expect(await httpStatus(running.port!)).toBe(200);
    } finally {
      await runner.waitClosed();
    }
  });

  it('同自然键二次 start 补报现状端口（test_start_idempotent_reports_current_port）', async () => {
    const runner = new PreviewRunner({ healthTimeout: 15.0, pollInterval: 0.1 });
    const reports = new Reports();
    const data = startData(tmp);
    try {
      await runner.start(data, reports.cb);
      await until(() => reports.byStatus('running').length > 0, 15_000);
      const port = reports.byStatus('running').at(-1)!.port;
      // 同 preview_session_id 二次 start → noop + 补报现状端口，不重开进程
      const [started2, status2] = await runner.start(data, reports.cb);
      expect(started2).toBe(false);
      expect(status2).not.toBeNull();
      expect(status2!.status).toBe('running');
      expect(status2!.port).toBe(port);
    } finally {
      await runner.waitClosed();
    }
  });

  it('未知/重复 stop 均 noop（test_stop_unknown_and_repeated_is_noop）', async () => {
    const runner = new PreviewRunner({ healthTimeout: 15.0, pollInterval: 0.1 });
    const reports = new Reports();
    try {
      // 未知 session → noop
      const [stopped, status] = await runner.stop(newUlid());
      expect(stopped).toBe(false);
      expect(status).toBeNull();
      // 起 → 停（recycled）→ 再停（noop）
      const data = startData(tmp);
      await runner.start(data, reports.cb);
      await until(() => reports.byStatus('running').length > 0, 15_000);
      const [stopped1, st1] = await runner.stop(data.preview_session_id);
      expect(stopped1).toBe(true);
      expect(st1).not.toBeNull();
      expect(st1!.status).toBe('recycled');
      const [stopped2, st2] = await runner.stop(data.preview_session_id);
      expect(stopped2).toBe(false);
      expect(st2).toBeNull();
    } finally {
      await runner.waitClosed();
    }
  });

  it('坏命令速死上报 failed 携 log_tail（test_bad_command_reports_failed_with_log_tail）', async () => {
    const runner = new PreviewRunner({ healthTimeout: 30.0, pollInterval: 0.1 }); // 长超时证明"不空等"
    const reports = new Reports();
    // py = python -m coagentia_no_such_module_xyz（"No module named"）；TS = node 跑不存在脚本。
    const bad = `"${process.execPath}" "${path.join(tmp, 'coagentia_no_such_script_xyz.cjs')}"`;
    const data = startData(tmp, bad);
    try {
      const [started] = await runner.start(data, reports.cb);
      expect(started).toBe(true);
      // 进程先退出（存活监控竞速胜出）→ 立即 failed，不等 30s 健康超时
      await until(() => reports.byStatus('failed').length > 0, 8000);
      const failed = reports.byStatus('failed').at(-1)!;
      expect(failed.log_tail).toBeTruthy();
      expect(failed.log_tail!).toContain('Cannot find module');
      expect(Buffer.byteLength(failed.log_tail!, 'utf-8')).toBeLessThanOrEqual(2 * 1024);
    } finally {
      await runner.waitClosed();
    }
  });

  it('健康检查超时上报 failed（test_health_timeout_reports_failed）', async () => {
    const runner = new PreviewRunner({ healthTimeout: 1.0, pollInterval: 0.1 });
    const reports = new Reports();
    // 进程存活但从不绑定端口 → 健康检查超时 → 杀树 + failed
    const cmd = `"${process.execPath}" -e "setTimeout(() => {}, 30000)"`;
    const data = startData(tmp, cmd);
    try {
      const [started] = await runner.start(data, reports.cb);
      expect(started).toBe(true);
      await until(() => reports.byStatus('failed').length > 0, 8000);
      expect(reports.byStatus('failed').length).toBeGreaterThan(0);
    } finally {
      await runner.waitClosed();
    }
  });

  it('worktree 非目录起进程即失败（test_invalid_worktree_reports_failed_immediately）', async () => {
    const runner = new PreviewRunner();
    const reports = new Reports();
    const data = startData(path.join(tmp, 'does_not_exist'));
    // 起进程即失败（cwd 非目录 → py 同步 OSError / node 'error' 事件）→ ack DONE + 预生成
    // failed，端口不泄漏
    const [started, status] = await runner.start(data, reports.cb);
    expect(started).toBe(true);
    expect(status).not.toBeNull();
    expect(status!.status).toBe('failed');
    await runner.waitClosed();
  });

  it('waitClosed 杀活跃预览无孤儿（test_wait_closed_kills_active_preview_no_orphan）', async () => {
    const runner = new PreviewRunner({ healthTimeout: 15.0, pollInterval: 0.1 });
    const reports = new Reports();
    const data = startData(tmp);
    const [started] = await runner.start(data, reports.cb);
    expect(started).toBe(true);
    await until(() => reports.byStatus('running').length > 0, 15_000);
    const port = reports.byStatus('running').at(-1)!.port;
    expect(port).not.toBeNull();
    expect(await tcpReachable(port!)).toBe(true);
    // shutdown 逐个杀子 → 无孤儿（端口不再可达）
    await runner.waitClosed();
    await sleep(500);
    expect(await tcpReachable(port!)).toBe(false);
  });

  it('processTable 快照含活跃与终态记录（test_process_table_snapshots_live_and_terminal_sessions）', async () => {
    // hello.previews 进程表快照（契约 D §4.1 v1.0.5）：含活跃与**终态**记录——running 携 port、
    // failed 携 log_tail（断连期 best-effort 丢失的终态上报靠重连 hello 快照恢复）。断连不再杀预览
    // （对账 #9 逐会话判活取代 v1.0.4 的对称杀），存活子进程跨重连原样保持。
    const runner = new PreviewRunner({ healthTimeout: 15.0, pollInterval: 0.1 });
    const reports = new Reports();
    const ok = startData(tmp);
    const bad = startData(path.join(tmp, 'does_not_exist')); // 起进程即失败 → 终态记录留存注册表
    try {
      const [started] = await runner.start(ok, reports.cb);
      expect(started).toBe(true);
      await runner.start(bad, reports.cb);
      await until(() => reports.byStatus('running').length > 0, 15_000);
      const table = new Map(runner.processTable().map((e) => [e.preview_session_id, e]));
      const live = table.get(ok.preview_session_id)!;
      expect(live.status).toBe('running');
      expect(live.port).not.toBeNull();
      expect(await tcpReachable(live.port!)).toBe(true); // 快照 ≠ 杀进程，仍存活
      const dead = table.get(bad.preview_session_id)!;
      expect(dead.status).toBe('failed');
      expect(dead.log_tail).toBeTruthy();
      expect(dead.port).toBeNull(); // 终态不再占端口
    } finally {
      await runner.waitClosed();
    }
  });

  it('running→stop 不误报 failed（test_stop_does_not_report_failed）', async () => {
    // 缺口 #7：stop 抢先置 stopping，monitor 的存活等待返回后见 stopping 静默退出——收集帧里
    // status=='failed' 恰 0（recycled 由 stop 直接返回，非 monitor 越权补 failed）。
    const runner = new PreviewRunner({ healthTimeout: 15.0, pollInterval: 0.1 });
    const reports = new Reports();
    const data = startData(tmp);
    try {
      await runner.start(data, reports.cb);
      await until(() => reports.byStatus('running').length > 0, 15_000);
      const [stopped, st] = await runner.stop(data.preview_session_id);
      expect(stopped).toBe(true);
      expect(st).not.toBeNull();
      expect(st!.status).toBe('recycled');
      // stop 内已 await monitor 收敛；再给一拍确保无迟到 failed 帧混入。
      await sleep(300);
      expect(reports.byStatus('failed')).toEqual([]); // 关键：stopping 抢占 → 无 failed 误报
      expect(reports.byStatus('running').length).toBeGreaterThan(0); // 正常态确曾上报（sanity）
    } finally {
      await runner.waitClosed();
    }
  });

  it('preview.start 起进程可立即 ack DONE、同自然键重发 noop 补报（test_preview_start_handler_acks_done_then_natural_key_noop 的 PreviewRunner 面）', async () => {
    // py 原例经 client.handle_instr 断言 transport ack done/noop 帧（client/handlers 面归 W4
    // client 波，届时按 handler 面复核）；此处对等其 PreviewRunner 检查点：start 同步判定
    // (true, null) = ack DONE 依据；running 上报携 port；同 preview_session_id 重发 →
    // (false, 现状 status 携 port) = ack NOOP + 补报依据。
    const runner = new PreviewRunner({ healthTimeout: 15.0, pollInterval: 0.1 });
    const reports = new Reports();
    const data = startData(tmp);
    try {
      const [started, status] = await runner.start(data, reports.cb);
      expect(started).toBe(true);
      expect(status).toBeNull();
      await until(() => reports.byStatus('running').length > 0, 15_000);
      expect(reports.byStatus('running').at(-1)!.port).toBeTruthy();
      // 同 preview_session_id、新 frame_id → 自然键 noop + 补报现状端口
      const [started2, status2] = await runner.start(data, reports.cb);
      expect(started2).toBe(false);
      expect(status2).not.toBeNull();
      expect(status2!.status).toBe('running');
      expect(status2!.port).toBeTruthy();
    } finally {
      await runner.waitClosed();
    }
  });

  it('preview.stop 可 ack DONE 上报 recycled、再停 noop（test_preview_stop_handler_acks_done_then_noop 的 PreviewRunner 面）', async () => {
    // py 原例经 client.handle_instr 断言 transport ack done/noop 帧与 preview.status 上报
    // （client/handlers 面归 W4 client 波）；此处对等其 PreviewRunner 检查点：stop →
    // (true, recycled) = ack DONE + recycled 上报依据；再停 → (false, null) = ack NOOP。
    const runner = new PreviewRunner({ healthTimeout: 15.0, pollInterval: 0.1 });
    const reports = new Reports();
    const data = startData(tmp);
    try {
      await runner.start(data, reports.cb);
      await until(() => reports.byStatus('running').length > 0, 15_000);
      const [stopped, st] = await runner.stop(data.preview_session_id);
      expect(stopped).toBe(true);
      expect(st).not.toBeNull();
      expect(st!.status).toBe('recycled');
      const [stopped2, st2] = await runner.stop(data.preview_session_id);
      expect(stopped2).toBe(false);
      expect(st2).toBeNull();
    } finally {
      await runner.waitClosed();
    }
  });

  it('端口注册表并发分配全互异（test_port_registry_concurrent_acquire_distinct）', async () => {
    const reg = new _PortRegistry();
    const ports = await Promise.all(Array.from({ length: 20 }, () => reg.acquire()));
    expect(new Set(ports).size).toBe(ports.length); // 并发分配全互异
    for (const p of ports) reg.release(p);
  });

  // ------------------------------------------------------------------------- win32 专属

  it.runIf(process.platform === 'win32')(
    '外力杀孙进程存活监控上报 failed（test_liveness_external_kill_grandchild_reports_failed）',
    async () => {
      const runner = new PreviewRunner({ healthTimeout: 15.0, pollInterval: 0.1 });
      const reports = new Reports();
      const data = startData(tmp);
      try {
        await runner.start(data, reports.cb);
        await until(() => reports.byStatus('running').length > 0, 15_000);
        const port = reports.byStatus('running').at(-1)!.port;
        expect(port).not.toBeNull();
        const gpid = findListeningPid(port!);
        expect(gpid).not.toBeNull();
        taskkillPid(gpid!); // 外力杀孙（模拟 dev server 自崩）→ 存活监控应捕获 → failed
        await until(() => reports.byStatus('failed').length > 0, 8000);
        expect(reports.byStatus('failed').length).toBeGreaterThan(0);
      } finally {
        await runner.waitClosed();
      }
    },
  );

  it.runIf(process.platform === 'win32')(
    'stop 杀树孙进程死净且端口释放（test_stop_kills_tree_grandchild_dead_and_port_released）',
    async () => {
      const runner = new PreviewRunner({ healthTimeout: 15.0, pollInterval: 0.1 });
      const reports = new Reports();
      const data = startData(tmp);
      try {
        await runner.start(data, reports.cb);
        await until(() => reports.byStatus('running').length > 0, 15_000);
        const port = reports.byStatus('running').at(-1)!.port;
        expect(port).not.toBeNull();
        const gpid = findListeningPid(port!);
        expect(gpid).not.toBeNull();
        expect(pidAlive(gpid!)).toBe(true);
        const [stopped, st] = await runner.stop(data.preview_session_id);
        expect(stopped).toBe(true);
        expect(st).not.toBeNull();
        expect(st!.status).toBe('recycled');
        await sleep(600);
        expect(pidAlive(gpid!)).toBe(false); // taskkill /F /T 连孙一并杀
        expect(await tcpReachable(port!)).toBe(false); // 端口释放
      } finally {
        await runner.waitClosed();
      }
    },
  );
});
