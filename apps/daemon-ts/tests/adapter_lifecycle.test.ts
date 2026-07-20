/**
 * ClaudeCodeAdapter 生命周期单测（E §4/§5/§6）：桩 spawn，无真 claude。
 *
 * 覆盖：start→idle、幂等、deliver→busy→result→idle+usage 恰一条、三档重置、
 * 崩溃拉起退避、resume 损坏降级 session_lost、崩溃熔断放弃。
 *
 * 对等基准 = apps/daemon tests/test_adapter_lifecycle.py（13 用例逐条对应，零行为改进）。
 * py/TS 测试侧差异（登记）：
 * - py monkeypatch 模块常量 CRASH_BACKOFF/AUTH_RECOVERY_DELAYS → TS 就地 splice 替换导出数组
 *   （afterEach 还原）；
 * - py monkeypatch cmdline.default_config_dir → TS vi.mock 包装 cmdline 模块（materializeCredentials
 *   的 source 显式改指 machine 目录，行为等价）；
 * - py loop.time() → performance.now()/1000（与 src 熔断时钟同源）。
 */

import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { Writable } from 'node:stream';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { AgentBoot } from '@coagentia/contracts-ts';

import type { ClaudeCodeProcess } from '../src/adapters/claude_code.ts';
import {
  AUTH_RECOVERY_DELAYS,
  CRASH_BACKOFF,
  ClaudeCodeAdapter,
  NodeStdin,
  defaultSpawn,
} from '../src/adapters/claude_code.ts';
import { sleep, withTimeout } from '../src/aio.ts';
import { DataPaths } from '../src/paths.ts';
import { RecordingSink, SpawnRecorder, fBlockStart, fInit, fResult, seqUlid } from './adapter_helpers.ts';
import { until } from './helpers.ts';

// py monkeypatch cmdline.default_config_dir 对等：vi.mock 包装（machineDir=null 时行为不变）。
const mockState = vi.hoisted(() => ({ machineDir: null as string | null }));

vi.mock('../src/adapters/cmdline.ts', async (importOriginal) => {
  const real = await importOriginal<typeof import('../src/adapters/cmdline.ts')>();
  return {
    ...real,
    defaultConfigDir: (): string => mockState.machineDir ?? real.defaultConfigDir(),
    materializeCredentials: (configDir: string, source?: string | null): string[] =>
      real.materializeCredentials(configDir, source ?? mockState.machineDir),
  };
});

const AID = '01K5CMPT00000000000000000A';

const ORIG_CRASH_BACKOFF = [...CRASH_BACKOFF];
const ORIG_AUTH_DELAYS = [...AUTH_RECOVERY_DELAYS];

let tmp: string;

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'coagentia-adapter-lifecycle-'));
});

afterEach(() => {
  CRASH_BACKOFF.splice(0, CRASH_BACKOFF.length, ...ORIG_CRASH_BACKOFF);
  AUTH_RECOVERY_DELAYS.splice(0, AUTH_RECOVERY_DELAYS.length, ...ORIG_AUTH_DELAYS);
  mockState.machineDir = null;
  fs.rmSync(tmp, { recursive: true, force: true });
});

function boot(home: string): AgentBoot {
  return {
    agent_member_id: AID,
    name: 'Pat',
    runtime: 'claude_code',
    model: 'claude-opus-4-8',
    home_path: home,
    skills: [],
  };
}

function make(): [ClaudeCodeAdapter, RecordingSink, SpawnRecorder, DataPaths] {
  const paths = new DataPaths(path.join(tmp, 'root'));
  paths.ensureDirs();
  const spawnRec = new SpawnRecorder();
  const adapter = new ClaudeCodeAdapter(paths, {
    serverUrl: 'http://s',
    apiKey: 'cak_x',
    spawn: spawnRec.spawn,
    ulid: seqUlid,
  });
  const sink = new RecordingSink();
  adapter.bind(sink);
  return [adapter, sink, spawnRec, paths];
}

describe('ClaudeCodeAdapter 生命周期（契约 E §4/§5/§6）', () => {
  it('start 就绪 idle + init 会话簿记（test_start_reaches_idle_on_init）', async () => {
    const [adapter, sink, spawnRec, paths] = make();
    const b = boot(path.join(tmp, 'home'));
    expect(await adapter.start(b)).toBe(true);
    expect(sink.statuses()[0]).toBe('starting');
    expect(spawnRec.procs).toHaveLength(1);
    // 就绪 idle 解耦于 init（实测本 CLI init 首输入后才到，E §11.3）
    await until(() => sink.statuses().includes('idle'));
    // init 帧 → 会话簿记（session_id 持久化 + process_table 反映）
    spawnRec.procs[0]!.stdout.push(fInit('uuid-xyz'));
    await until(() => paths.readSession(AID)['session_id'] === 'uuid-xyz');
    expect(adapter.processTable()[0]!.source_session).toBe('uuid-xyz');
    // process_started 诊断
    expect(sink.diagTypes()).toContain('agent.process_started');
  });

  it('已在跑重复 start → noop（test_start_idempotent_while_running）', async () => {
    const [adapter, , spawnRec] = make();
    const b = boot(path.join(tmp, 'home'));
    expect(await adapter.start(b)).toBe(true);
    expect(await adapter.start(b)).toBe(false); // 已在跑 → noop
    expect(spawnRec.procs).toHaveLength(1);
  });

  it('用例 2 桩版：喂输入→busy+activity→result→idle + usage 恰一条 ULID（test_full_turn_deliver_busy_activity_result_idle_usage）', async () => {
    const [adapter, sink, spawnRec] = make();
    await adapter.start(boot(path.join(tmp, 'home')));
    const proc = spawnRec.procs[0]!;
    proc.stdout.push(fInit('uuid-1'));
    await until(() => sink.statuses().includes('idle'));

    const msg = {
      id: '01K5MSG100000000000000000A',
      channel_id: '01K5CHAN00000000000000000A',
      author_member_id: '01K5AUTH00000000000000000A',
      created_at: '2026-07-09T00:00:00.000Z',
      body: 'hi',
    };
    expect(await adapter.deliver(AID, '01K5CHAN00000000000000000A', [msg], null)).toBe(true);
    expect(sink.statuses()).toContain('busy');
    expect(proc.stdin.lines().length).toBeGreaterThan(0); // 写 stdin 即 ack
    expect(proc.stdin.lines()[0]).toContain('hi');

    proc.stdout.push(fBlockStart('thinking'));
    proc.stdout.push(fResult({ inputTokens: 42, outputTokens: 8 }));
    await until(() => sink.usage.length === 1);
    expect(sink.statuses().at(-1)).toBe('idle');
    expect(sink.activity.map(([, d]) => d)).toEqual(['Thinking…']);
    const ev = sink.usage[0]!;
    expect(ev.input_tokens).toBe(42);
    expect(ev.output_tokens).toBe(8);
    expect(ev.channel_id).toBe('01K5CHAN00000000000000000A');
    expect(ev.source_session).toBe('uuid-1');
  });

  it('同批重投按最大 message_id 去重（test_deliver_dedup_by_max_message_id）', async () => {
    const [adapter] = make();
    await adapter.start(boot(path.join(tmp, 'home')));
    const msg = { id: '01K5MSG100000000000000000A', channel_id: 'C', body: 'a' };
    expect(await adapter.deliver(AID, 'C', [msg], null)).toBe(true);
    expect(await adapter.deliver(AID, 'C', [msg], null)).toBe(false); // 同批 → noop
  });

  it('#2：去重游标按 channel_id 维度（test_deliver_dedup_is_per_channel）', async () => {
    // 频道 A 的较大 id 不压制频道 B 较早消息的投递。
    const [adapter] = make();
    await adapter.start(boot(path.join(tmp, 'home')));
    // 频道 A 先投较大 message_id。
    const msgA = { id: '01K5MSG900000000000000000A', channel_id: 'A', body: 'a' };
    expect(await adapter.deliver(AID, 'A', [msgA], null)).toBe(true);
    // 频道 B 投较早（更小）message_id：跨频道独立游标 → 不被 A 误判 noop。
    const msgB = { id: '01K5MSG100000000000000000A', channel_id: 'B', body: 'b' };
    expect(await adapter.deliver(AID, 'B', [msgB], null)).toBe(true);
    // 同频道 B 重投同批 → 按频道去重仍 noop。
    expect(await adapter.deliver(AID, 'B', [msgB], null)).toBe(false);
  });

  it('stop → offline（test_stop_emits_offline）', async () => {
    const [adapter, sink] = make();
    await adapter.start(boot(path.join(tmp, 'home')));
    expect(await adapter.stop(AID)).toBe(true);
    expect(sink.statuses().at(-1)).toBe('offline');
    expect(await adapter.stop(AID)).toBe(false); // 已停 → noop
  });

  it('restart 保 session；reset_session 清簿记（test_restart_keeps_session_reset_clears）', async () => {
    const [adapter, , spawnRec, paths] = make();
    const b = boot(path.join(tmp, 'home'));
    await adapter.start(b);
    spawnRec.procs[0]!.stdout.push(fInit('uuid-keep'));
    await until(() => paths.readSession(AID)['session_id'] === 'uuid-keep');

    await adapter.restart(b); // 一档：保 session → 新进程带 --resume
    expect(spawnRec.procs).toHaveLength(2);
    expect(spawnRec.procs[1]!.argv).toContain('--resume');
    expect(paths.readSession(AID)['session_id']).toBe('uuid-keep');

    await adapter.resetSession(b); // 二档：新会话 → 清簿记、无 --resume
    expect(spawnRec.procs).toHaveLength(3);
    expect(spawnRec.procs[2]!.argv).not.toContain('--resume');
    expect(paths.readSession(AID)).toEqual({});
  });

  it('崩溃拉起（§5）：进程意外退出 → --resume 拉起 + crash_restarted 诊断（test_crash_restart_backoff）', async () => {
    CRASH_BACKOFF.splice(0, CRASH_BACKOFF.length, 0, 0, 0);
    const [adapter, sink, spawnRec] = make();
    await adapter.start(boot(path.join(tmp, 'home')));
    spawnRec.procs[0]!.stdout.push(fInit('uuid-c'));
    await until(() => sink.statuses().includes('idle'));
    // 意外退出（非 stop）
    spawnRec.procs[0]!.finish(1);
    await until(() => spawnRec.procs.length === 2, 5000);
    expect(sink.diagTypes()).toContain('agent.process_exited');
    expect(sink.diagTypes()).toContain('agent.crash_restarted');
    expect(spawnRec.procs[1]!.argv).toContain('--resume'); // 保上下文
  });

  it('resume 损坏降级（§4）：resume 启动从未就绪即退 → session_lost + 冷启（test_resume_corruption_degrades_session_lost）', async () => {
    CRASH_BACKOFF.splice(0, CRASH_BACKOFF.length, 0, 0, 0);
    const [adapter, sink, spawnRec, paths] = make();
    paths.writeSession(AID, { session_id: 'corrupt-old' });
    await adapter.start(boot(path.join(tmp, 'home'))); // resume=true
    expect(spawnRec.procs[0]!.argv).toContain('--resume');
    // 从未 init 就崩
    spawnRec.procs[0]!.finish(1);
    await until(() => sink.diagTypes().includes('agent.session_lost'), 5000);
    await until(() => spawnRec.procs.length === 2, 5000);
    expect(paths.readSession(AID)).toEqual({}); // 会话簿记已清
    expect(spawnRec.procs[1]!.argv).not.toContain('--resume'); // 降级冷启
  });

  it('崩溃熔断（§5/用例 6）：5 分钟窗 ≥3 次 → error 放弃拉起（test_crash_loop_giveup_error）', async () => {
    CRASH_BACKOFF.splice(0, CRASH_BACKOFF.length, 0, 0, 0);
    const [adapter, sink, spawnRec] = make();
    await adapter.start(boot(path.join(tmp, 'home')));
    const entry = adapter.agents.get(AID)!;
    const t = performance.now() / 1000; // py loop.time() 对等（与 src 熔断时钟同源）
    entry.crashTimes.push(t, t, t); // 预置 3 次近期崩溃
    spawnRec.procs[0]!.finish(1); // 第 4 次 → 超阈
    await until(() => sink.statuses().includes('error'), 5000);
    expect(spawnRec.procs).toHaveLength(1); // 放弃拉起，无新进程
  });

  it('wake 翻 busy；已 busy → noop（test_wake_flips_busy）', async () => {
    const [adapter, sink, spawnRec] = make();
    await adapter.start(boot(path.join(tmp, 'home')));
    spawnRec.procs[0]!.stdout.push(fInit());
    // py 协程 await 不让位事件循环（init 帧在断言后才被消化）；TS 每 await 让位微任务 →
    // 需等 init 消化完（confirmed）再 wake，否则 init 的 idle 与 wake 的 busy 交错（登记差异）。
    const proc = adapter.agents.get(AID)!.process;
    await until(() => sink.statuses().includes('idle') && proc.router.confirmed);
    expect(await adapter.wake(AID, 'mention', null)).toBe(true);
    expect(sink.statuses().at(-1)).toBe('busy');
    expect(await adapter.wake(AID, 'mention', null)).toBe(false); // 已 busy → noop
  });

  it('inject 写 stdin + 诊断留痕（test_inject_writes_stdin_and_diagnostic）', async () => {
    const [adapter, sink, spawnRec] = make();
    await adapter.start(boot(path.join(tmp, 'home')));
    await adapter.inject(AID, '看这里', { kind: 'guard_feedback' }, 'guard.reevaluate_requested');
    expect(sink.diagTypes()).toContain('guard.reevaluate_requested');
    expect(spawnRec.procs[0]!.stdin.lines().some((ln) => ln.includes('看这里'))).toBe(true);
  });

  it('auth 失败吸收同侪凭证并自动重投一次（test_auth_failure_absorbs_peer_credentials_and_retries_turn）', async () => {
    const machine = path.join(tmp, 'machine');
    writeCredentials(path.join(machine, '.credentials.json'), 0);
    mockState.machineDir = machine; // py monkeypatch cmdline.default_config_dir 对等
    AUTH_RECOVERY_DELAYS.splice(0, AUTH_RECOVERY_DELAYS.length, 0);

    const [adapter, , spawnRec, paths] = make();
    await adapter.start(boot(path.join(tmp, 'home')));
    const channelId = '01K5CHAN00000000000000000A';
    await adapter.deliver(
      AID,
      channelId,
      [{ id: '01K5MSG100000000000000000A', channel_id: channelId, body: 'retry me' }],
      null,
    );
    expect(spawnRec.procs[0]!.stdin.lines()).toHaveLength(1);

    writeCredentials(path.join(paths.agentsDir, 'peer', '.claude', '.credentials.json'), 5000);
    const authError = fResult({ subtype: 'error_during_execution', isError: true });
    authError['result'] = 'Failed to authenticate: OAuth session expired';
    const entry = adapter.agents.get(AID)!;
    await (entry.process as ClaudeCodeProcess).onLine(JSON.stringify(authError));

    expect(spawnRec.procs[0]!.stdin.lines()).toHaveLength(2);
    expect(spawnRec.procs[0]!.stdin.lines()[0]).toBe(spawnRec.procs[0]!.stdin.lines()[1]);
  });

  it('deliver 批含缺 id 消息 → fail-close 抛错且游标不动（契约 MessagePublic.id 必填；无 py 对等：pydantic 上游拦截）', async () => {
    // 若缺 id 走 String(undefined)="undefined" 入游标：'u' 高于全部 Crockford ULID 字符 →
    // 该频道此后投递永久 noop。修复 = 整批抛错（handleInstr 转 ack failed），游标不动。
    const [adapter, sink, spawnRec] = make();
    await adapter.start(boot(path.join(tmp, 'home')));
    const bad = [
      { id: '01K5MSG900000000000000000A', channel_id: 'C', body: 'valid' },
      { channel_id: 'C', body: 'no-id' }, // 缺 id
    ];
    await expect(adapter.deliver(AID, 'C', bad, null)).rejects.toThrow('MessagePublic.id 必填');
    expect(spawnRec.procs[0]!.stdin.lines()).toHaveLength(0); // 整批未投
    expect(sink.statuses()).not.toContain('busy'); // 抛错先于 BUSY 先行
    // 游标未动：比失败批合法 id 更小的后续批仍送达（曾记 maxId/"undefined" 则此处 noop）。
    const ok = { id: '01K5MSG100000000000000000A', channel_id: 'C', body: 'later-ok' };
    expect(await adapter.deliver(AID, 'C', [ok], null)).toBe(true);
    expect(spawnRec.procs[0]!.stdin.lines().some((ln) => ln.includes('later-ok'))).toBe(true);
  });
});

// ========================================================= 真子进程底座（NodeStdin / defaultSpawn）

describe('NodeStdin / defaultSpawn（cal6/校准条款 3；无 py 对等：node 流侧特有面）', () => {
  it('NodeStdin：流 error 已挂 handler 不直抛；背压中流关闭 drain 兜底收敛', async () => {
    const stream = new Writable({
      highWaterMark: 1,
      write(_chunk, _enc, _cb) {
        // 扣住回调：持续背压（writableNeedDrain 恒 true，'drain' 永不来）
      },
    });
    const sin = new NodeStdin(stream);
    sin.write('x'); // hwm=1 → write()===false → needDrain
    // 无 'error' handler 时 emit('error') 直抛 ERR_UNHANDLED_ERROR（真机 = EPIPE 异步到达 →
    // uncaughtException 崩整个 daemon）。
    expect(() => stream.emit('error', new Error('EPIPE'))).not.toThrow();
    // 背压中流销毁（只来 'close' 不来 'drain'）→ drain() 不得永久悬挂。
    const drained = sin.drain();
    stream.destroy();
    await withTimeout(drained, 2000);
  });

  it('真子进程退出后写 stdin：异步写错误不成为 uncaughtException（EPIPE 家族真机面）', async () => {
    const proc = await defaultSpawn([process.execPath, '-e', 'process.exit(0)'], tmp, {
      ...process.env,
    } as Record<string, string>);
    expect(await withTimeout(proc.wait(), 15_000)).toBe(0);
    proc.stdin!.write('late\n'); // 死后写：错误异步到达 stream
    await proc.stdin!.drain?.();
    await sleep(100); // 给异步 'error' 浮出窗口——无 handler 版在此 uncaughtException 崩 worker
  });

  it.runIf(process.platform === 'win32')(
    'win32 .cmd（npm shim）经 shell:true 拉起 + 含空格路径引用（校准条款 3）',
    async () => {
      const cmdPath = path.join(tmp, 'claude shim.cmd'); // 文件名含空格：覆盖 quoteForShell
      fs.writeFileSync(cmdPath, '@echo off\r\necho shim-ok %1\r\n', 'utf-8');
      const proc = await defaultSpawn([cmdPath, 'arg1'], tmp, {
        ...process.env,
      } as Record<string, string>);
      try {
        const line = await withTimeout(proc.stdout.readline(), 15_000);
        expect(line.toString('utf-8').trim()).toBe('shim-ok arg1'); // 未修版 node 22 裸 spawn EINVAL
        expect(await withTimeout(proc.wait(), 15_000)).toBe(0);
      } finally {
        try {
          proc.kill(); // 收尾必杀（正常路径已退出，幂等兜底；README 体例 7）
        } catch {
          // 已退出
        }
      }
    },
  );
});

function writeCredentials(p: string, expiresAt: number): void {
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(
    p,
    JSON.stringify({
      claudeAiOauth: {
        accessToken: `access-${expiresAt}`,
        refreshToken: `refresh-${expiresAt}`,
        expiresAt,
        refreshTokenExpiresAt: expiresAt + 1000,
      },
    }),
    'utf-8',
  );
}
