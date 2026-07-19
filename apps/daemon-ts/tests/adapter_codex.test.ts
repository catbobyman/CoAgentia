/**
 * Codex 适配器单测（契约 E2 §5/§8）：JSON-RPC 帧映射 + 进程握手/turn/审批 + 会话簿记。
 *
 * 桩 spawn（FakeProc/SpawnRecorder，复用 adapter_helpers），无真 codex——真机留 verify 阶段。
 * 对等基准 = apps/daemon tests/test_adapter_codex.py（25 例）。
 *
 * py→TS 移植登记（非行为改进）：
 * - py 经 RuntimeManager（claude_code.py）驱动的用例——W3 并行期曾降档为 CodexProcess 面
 *   检查点（保留在中段）；claude_code.ts 落地后，管理器面（runtime 分派/STARTING→IDLE/
 *   process_table/deliver 渲染+BUSY 先行+去重游标/三档 wiring 含 clear_session/熔断降级）
 *   在文末 RuntimeManager 组逐条真移植（W4 收尾清账，原 it.todo 挂账销账）。
 * - py test_spawn_uses_large_stream_limit monkeypatch create_subprocess_exec 校验 limit 传参；
 *   node spawn 无 limit 参数（校准条款 2）→ 检查点改为：STREAM_LINE_LIMIT 常量远大于 64KB +
 *   defaultCodexSpawn 真进程冒烟（>64KB 单行完整读回，B-4 反例免疫）；claude _default_spawn
 *   半部 = claude_code.STREAM_LINE_LIMIT 常量同值检查点（defaultSpawn 未导出且 argv 固定真
 *   claude，无法脱离真 claude 起真进程冒烟——登记差异，见对应用例注释）。
 * - py caplog → TS 无 logger 捕获注入：改经 logconfig 文件落盘（setupFileLogging 到 tmp）后
 *   读 daemon.log 断言（同一 INFO 通路，观测点等价）。
 * - py json.dumps 出帧含空格（`"id": 99`）；JSON.stringify 紧凑（`"id":99`）——断言按 TS 形状。
 * - py mgr.deliver 渲染正文 + set_turn_context；降档用 encoding.renderDeliver + 显式
 *   setTurnContext 直喂 CodexProcess.feed（渲染面已由 adapter_encoding_cmdline.test.ts 守门）。
 */

import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import type { AgentBoot } from '@coagentia/contracts-ts';

import { withTimeout } from '../src/aio.ts';
import {
  CRASH_BACKOFF,
  RuntimeManager,
  STREAM_LINE_LIMIT as CLAUDE_STREAM_LINE_LIMIT,
} from '../src/adapters/claude_code.ts';
import {
  CodexFrameRouter,
  CodexProcess,
  STREAM_LINE_LIMIT,
  defaultCodexSpawn,
} from '../src/adapters/codex.ts';
import type { CodexFrameRouterOptions } from '../src/adapters/codex.ts';
import { materializeCredentials } from '../src/adapters/codex_cmdline.ts';
import { renderDeliver } from '../src/adapters/encoding.ts';
import { resetFileLogging, setupFileLogging } from '../src/logconfig.ts';
import { DataPaths } from '../src/paths.ts';
import type { JsonObject } from '../src/protocol.ts';
import { FakeProc, RecordingSink, SpawnRecorder, seqUlid } from './adapter_helpers.ts';
import { until } from './helpers.ts';

const AID = '01K5CMPT00000000000000000A';
const CH = '01K5CHAN00000000000000000A';

function now(): string {
  return '2026-07-11T00:00:00.000Z';
}

function router(sink: RecordingSink, opts: CodexFrameRouterOptions = {}): CodexFrameRouter {
  return new CodexFrameRouter(AID, sink, { ulid: seqUlid, now, ...opts });
}

/** codex 通知帧（method + params，无 id）。 */
function n(method: string, params: JsonObject = {}): JsonObject {
  return { method, params };
}

function itemStarted(itype: string, iid = 'i1', extra: JsonObject = {}): JsonObject {
  return n('item/started', {
    item: { type: itype, id: iid, ...extra },
    threadId: 'c',
    turnId: 't',
    startedAtMs: 0,
  });
}

function itemDone(itype: string, iid = 'i1', extra: JsonObject = {}): JsonObject {
  return n('item/completed', {
    item: { type: itype, id: iid, ...extra },
    threadId: 'c',
    turnId: 't',
    completedAtMs: 0,
  });
}

function turnDone(status = 'completed', extra: JsonObject = {}): JsonObject {
  return n('turn/completed', { threadId: 'c', turn: { id: 't1', items: [], status, ...extra } });
}

function tokenUsage(inp = 111, out = 22, cached = 7): JsonObject {
  const last = {
    inputTokens: inp,
    outputTokens: out,
    cachedInputTokens: cached,
    reasoningOutputTokens: 0,
    totalTokens: inp + out,
  };
  return n('thread/tokenUsage/updated', {
    threadId: 'c',
    turnId: 't',
    tokenUsage: { last, total: { ...last } },
  });
}

let tmp: string;

// py monkeypatch 模块常量 → TS 就地 splice 替换导出数组 + afterEach 还原（adapter_lifecycle 先例）。
const ORIG_CRASH_BACKOFF = [...CRASH_BACKOFF];

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'coagentia-codex-'));
});

afterEach(() => {
  CRASH_BACKOFF.splice(0, CRASH_BACKOFF.length, ...ORIG_CRASH_BACKOFF);
  resetFileLogging(); // 防泄漏：本文件仅日志用例装配落盘器
  fs.rmSync(tmp, { recursive: true, force: true });
});

// ========================================================= FrameRouter（纯逻辑）

describe('CodexFrameRouter（契约 E2 §5/§7）', () => {
  it('spawn 底座大帧免疫（test_spawn_uses_large_stream_limit 的 codex 半部；B-4 根因回归）', async () => {
    // codex thread/resume 重放会话历史、大工具输出/大 reasoning 的单条 JSON-RPC 帧可超 64KB；
    // py asyncio 默认 limit 下 readline() 抛 LimitOverrunError 杀读循环 → agent「挂死无诊断」。
    // TS 检查点：① 自设行上限常量远大于 64KB；② 真进程冒烟——>64KB 单行经 defaultCodexSpawn
    // 的行读层完整读回（node readline 无 64KB 天花板由校准条款 2 行读法保证）。
    expect(STREAM_LINE_LIMIT).toBe(32 * 1024 * 1024);
    expect(STREAM_LINE_LIMIT).toBeGreaterThanOrEqual(8 * 1024 * 1024); // 远大于 64KB asyncio 默认

    const big = 'x'.repeat(200_000); // > 64KB：py 默认 limit 即死的尺寸
    const script = "process.stdout.write('x'.repeat(200000) + '\\n'); console.log('中文尾');";
    const proc = await defaultCodexSpawn(
      [process.execPath, '-e', script],
      tmp,
      { ...process.env } as Record<string, string>,
    );
    try {
      const line1 = await withTimeout(proc.stdout.readline(), 15_000);
      expect(line1.length).toBeGreaterThan(64 * 1024);
      expect(line1.toString('utf-8').trim()).toBe(big);
      const line2 = await withTimeout(proc.stdout.readline(), 15_000);
      expect(line2.toString('utf-8').trim()).toBe('中文尾'); // UTF-8 跨管道完好（校准条款 1）
      const eofLine = await withTimeout(proc.stdout.readline(), 15_000);
      expect(eofLine.length).toBe(0); // EOF = 空 Buffer（py b"" 对等）
      expect(await withTimeout(proc.wait(), 15_000)).toBe(0);
    } finally {
      try {
        proc.kill(); // 收尾必杀（正常路径已退出，此处幂等兜底）
      } catch {
        // 已退出
      }
    }
  });

  it('claude _default_spawn 同用大行上限（test_spawn_uses_large_stream_limit 的 claude 半部）', () => {
    // py 断言 claude _default_spawn 给 create_subprocess_exec 传同一 cc_mod.STREAM_LINE_LIMIT；
    // node spawn 无 limit 参数（校准条款 2）→ claude 侧行读层（claude_code 内 NodeLineReader）
    // 以模块常量为自设上限，检查点 = 常量与 codex 同值且远大于 64KB（py 两半部同断言点）。
    // 真进程 >64KB 冒烟由上例 codex 半部覆盖（同款校准条款 2 行读法）；claude defaultSpawn
    // 未导出且 buildArgv 固定真 claude，无法脱离真 claude 起真进程冒烟（登记差异，非行为改进）。
    expect(CLAUDE_STREAM_LINE_LIMIT).toBe(32 * 1024 * 1024);
    expect(CLAUDE_STREAM_LINE_LIMIT).toBe(STREAM_LINE_LIMIT); // 两 runtime 共用同值（py 同一常量）
    expect(CLAUDE_STREAM_LINE_LIMIT).toBeGreaterThanOrEqual(8 * 1024 * 1024);
  });

  it('router 处理 item 与 turn 生命周期发 INFO 日志（test_router_logs_item_and_turn_lifecycle）', async () => {
    // B-4 可观测性：挂死症状 = 首个 tool call（item/completed）后哑火——item 序列日志即定位
    // 「卡在哪一 item」。py caplog → TS 经 logconfig 文件落盘观测（同一 INFO 通路）。
    const paths = new DataPaths(path.join(tmp, 'logroot'));
    paths.ensureDirs();
    resetFileLogging();
    setupFileLogging(paths, 'INFO');
    const sink = new RecordingSink();
    const r = router(sink);
    await r.process(itemStarted('mcpToolCall', 'i1', { tool: 'claim_task', server: 'coagentia' }));
    await r.process(itemDone('mcpToolCall', 'i1', { tool: 'claim_task', status: 'ok' }));
    await r.process(turnDone());
    const msgs = fs.readFileSync(paths.logPath, 'utf-8');
    expect(msgs).toContain('item/started type=mcpToolCall');
    expect(msgs).toContain('item/completed type=mcpToolCall');
    expect(msgs).toContain('turn/completed status=completed');
  });

  it('thread/started 记 conversation + confirmed（test_thread_started_sets_conversation_and_confirmed）', async () => {
    const sink = new RecordingSink();
    const captured: string[] = [];
    const r = router(sink, { onSession: (c) => captured.push(c) });
    await r.process(n('thread/started', { thread: { id: 'conv-1' } }));
    expect(r.sessionId).toBe('conv-1');
    expect(r.confirmed).toBe(true);
    expect(captured).toEqual(['conv-1']);
    expect(sink.statuses()).toEqual([]); // 就绪 idle 由管理器发，router 不在 thread/started 发状态
  });

  it('turn/started→busy、turn/completed→idle（test_turn_started_busy_completed_idle）', async () => {
    const sink = new RecordingSink();
    const r = router(sink);
    await r.process(n('turn/started', { threadId: 'c', turn: {} }));
    expect(sink.statuses().at(-1)).toBe('busy');
    await r.process(turnDone());
    expect(sink.statuses().at(-1)).toBe('idle');
  });

  it('usage 于 turn/completed 提取恰一条（test_usage_extracted_once_on_turn_completed）', async () => {
    // usage（E2 §7）：tokenUsage/updated 缓存 → turn/completed 提取恰一条；cache_write 恒 0。
    const sink = new RecordingSink();
    const r = router(sink);
    r.setConversation('conv-1');
    r.setTurnContext(CH, '01K5THRD00000000000000000A');
    r.beginTurn();
    await r.process(tokenUsage());
    expect(sink.usage).toHaveLength(0); // 未在 update 时上报（防多次 update 重复计）
    await r.process(turnDone());
    expect(sink.usage).toHaveLength(1);
    const ev = sink.usage[0]!;
    expect(ev.input_tokens).toBe(111);
    expect(ev.output_tokens).toBe(22);
    expect(ev.cache_read_tokens).toBe(7); // cachedInputTokens → cache_read_tokens
    expect(ev.cache_write_tokens).toBe(0); // codex 无独立 cache creation 字段
    expect(ev.source_session).toBe('conv-1');
    expect(ev.channel_id).toBe(CH);
    expect(ev.id).toBeTruthy();
  });

  it('多次 token update 仍只一条 usage（test_multiple_token_updates_still_one_usage）', async () => {
    const sink = new RecordingSink();
    const r = router(sink);
    r.beginTurn();
    await r.process(tokenUsage(10, 1));
    await r.process(tokenUsage(50, 8));
    await r.process(turnDone());
    expect(sink.usage).toHaveLength(1);
    expect(sink.usage[0]!.input_tokens).toBe(50); // 提取最新增量（权威 last）
  });

  it('相位聚合只在切换时上报（test_phase_aggregation_only_on_switch）', async () => {
    // 相位聚合（E2 §5）：item/started + delta → activity 帧数=相位切换数。
    const sink = new RecordingSink();
    const r = router(sink);
    await r.process(itemStarted('reasoning'));
    for (let i = 0; i < 20; i += 1) {
      // 同相位 delta 不上报
      await r.process(n('item/reasoning/textDelta', { delta: 'x', itemId: 'i1' }));
    }
    await r.process(n('item/agentMessage/delta', { delta: 'hi', itemId: 'i2' }));
    await r.process(itemStarted('commandExecution', 'i3'));
    await r.process(itemStarted('fileChange', 'i4'));
    await r.process(itemStarted('mcpToolCall', 'i5', { tool: 'send_message', server: 'coagentia' }));
    expect(sink.activity.map(([, d]) => d)).toEqual([
      'Thinking…',
      'Replying…',
      'Running command…',
      'Writing file…',
      'Using send_message…',
    ]);
  });

  it('item/completed 三类诊断（test_item_completed_diagnostics）', async () => {
    const sink = new RecordingSink();
    const r = router(sink);
    await r.process(itemDone('commandExecution', 'i1', { command: ['pytest'], exitCode: 0, status: 'ok' }));
    await r.process(itemDone('fileChange', 'i1', { changes: [{ path: 'notes.md' }], status: 'ok' }));
    await r.process(itemDone('mcpToolCall', 'i1', { tool: 'send_message', status: 'failed' }));
    const byType = new Map<string, JsonObject>();
    for (const d of sink.diagnostics) {
      if (d.type.startsWith('agent.')) byType.set(d.type, d.payload as JsonObject);
    }
    expect(byType.get('agent.command')!['command']).toBe('pytest');
    expect(byType.get('agent.command')!['is_error']).toBe(false);
    expect(byType.get('agent.file_edit')!['path']).toBe('notes.md');
    expect(byType.get('agent.tool_call')!['tool']).toBe('send_message');
    expect(byType.get('agent.tool_call')!['ok']).toBe(false);
  });

  it('非零 exitCode 判 is_error（test_command_failed_exit_code_is_error）', async () => {
    const sink = new RecordingSink();
    const r = router(sink);
    await r.process(itemDone('commandExecution', 'i1', { command: 'bad', exitCode: 2, status: 'completed' }));
    const diag = sink.diagnostics.find((d) => d.type === 'agent.command')!;
    expect((diag.payload as JsonObject)['is_error']).toBe(true);
  });

  it('turn failed 映射 error 状态（test_turn_completed_failed_maps_error）', async () => {
    const sink = new RecordingSink();
    const r = router(sink);
    await r.process(turnDone('failed', { error: { code: 'badRequest' } }));
    expect(sink.statuses().at(-1)).toBe('error');
    expect(sink.status.at(-1)![2]).toBe('badRequest');
  });

  it('error 通知瞬态/终态分流（test_error_notification_terminal_and_transient）', async () => {
    const sink = new RecordingSink();
    const r = router(sink);
    await r.process(n('error', { error: 'serverOverloaded', threadId: 'c', turnId: 't', willRetry: true }));
    expect(sink.statuses()).toEqual([]); // 瞬态：codex 内部重试，不改状态
    await r.process(n('error', { error: 'badRequest', threadId: 'c', turnId: 't', willRetry: false }));
    expect(sink.statuses().at(-1)).toBe('error');
  });

  it('未知通知计数、已知噪声静默（test_unknown_notification_counted_ignored_silent）', async () => {
    // 防腐（铁律 4）：未知通知计数 + 首现诊断；契约内已知噪声静默不计。
    const sink = new RecordingSink();
    const r = router(sink);
    await r.process(n('totally/new/method', { foo: 1 }));
    await r.process(n('totally/new/method', { foo: 2 })); // 同类型第二次 → 静默累加
    await r.process(n('thread/status/changed', { status: 'idle' })); // 已知噪声 → 静默忽略
    await r.process(n('account/updated'));
    expect(r.unknownCounts['totally/new/method']).toBe(2);
    expect(r.unknownCounts).not.toHaveProperty('thread/status/changed');
    const unknownDiags = sink.diagnostics.filter((d) => d.type === 'agent.unknown_frame');
    expect(unknownDiags).toHaveLength(1);
    expect(sink.statuses()).toEqual([]);
  });

  it('畸形帧不外抛（test_malformed_frames_do_not_raise）', async () => {
    const sink = new RecordingSink();
    const r = router(sink);
    await r.process(n('turn/completed', { threadId: 'c' })); // 无 turn
    await r.process(n('item/started', { item: 'not-a-dict' }));
    await r.process(itemDone('commandExecution')); // 缺字段
    await r.process({ method: 'thread/tokenUsage/updated', params: 'bad' });
    await r.process(n('thread/started', { thread: 'bad' }));
    expect(true).toBe(true); // 未抛即通过
  });

  it('turn 结束回调触发（test_release_turn_callback_fires）', async () => {
    const calls: number[] = [];
    const sink = new RecordingSink();
    const r = router(sink, {
      onTurnEnd: async () => {
        calls.push(1);
      },
    });
    await r.process(turnDone());
    expect(calls).toEqual([1]);
  });
});

// ========================================================= CodexProcess（桩 spawn）

function bootOf(home: string): AgentBoot {
  return {
    agent_member_id: AID,
    name: 'Codex-Pat',
    runtime: 'codex',
    model: 'gpt-5-codex',
    home_path: home,
    skills: [],
  };
}

interface ProcRig {
  proc: CodexProcess;
  sink: RecordingSink;
  spawn: SpawnRecorder;
  paths: DataPaths;
}

/** py _make_manager 的 Process 面降档：直建 CodexProcess（RuntimeManager 归 claude_code.ts）。 */
function makeProcess(): ProcRig {
  const paths = new DataPaths(path.join(tmp, 'root'));
  paths.ensureDirs();
  const spawn = new SpawnRecorder();
  const sink = new RecordingSink();
  const proc = new CodexProcess(AID, sink, paths, {
    serverUrl: 'http://s',
    apiKey: 'cak_x',
    spawn: spawn.spawn,
    ulid: seqUlid,
    now,
  });
  return { proc, sink, spawn, paths };
}

function hasLine(fake: FakeProc, needle: string): boolean {
  return fake.stdin.lines().some((ln) => ln.includes(needle));
}

function deliverMsg(mid: string, body: string): JsonObject {
  return {
    id: mid,
    channel_id: CH,
    author_member_id: '01K5AUTH00000000000000000A',
    created_at: now(),
    body,
  };
}

/** 推 initialize 响应，等到 thread/start|resume 请求写出（不推 thread 响应）。 */
async function reachThreadRequest(fake: FakeProc, opts: { resume?: boolean } = {}): Promise<void> {
  await until(() => hasLine(fake, '"initialize"'));
  fake.stdout.push({ id: 1, result: { codexHome: '/iso/.codex' } });
  const method = opts.resume ? '"thread/resume"' : '"thread/start"';
  await until(() => hasLine(fake, method));
}

/** 推 initialize / thread.* 响应，完成握手。 */
async function driveHandshake(
  fake: FakeProc,
  cid = 'conv-1',
  opts: { resume?: boolean } = {},
): Promise<void> {
  await reachThreadRequest(fake, opts);
  fake.stdout.push({ id: 2, result: { thread: { id: cid } } });
}

describe('CodexProcess（契约 E2 §1–§4；桩 spawn）', () => {
  it('start 拉起 codex app-server + process_started 诊断（test_dispatch_picks_codex_process 的 Process 面）', async () => {
    // py 原例经 RuntimeManager 断言分派 CodexProcess + STARTING→就绪 IDLE（管理器面见文末 RuntimeManager 组）。
    const { proc, sink, spawn } = makeProcess();
    await proc.start(bootOf(path.join(tmp, 'home')), false);
    try {
      expect(spawn.procs).toHaveLength(1);
      expect(spawn.procs[0]!.argv.at(-1)).toBe('app-server');
      expect(sink.diagTypes()).toContain('agent.process_started');
    } finally {
      await proc.stop();
    }
  });

  it('握手落 conversation_id + initialized 通知（test_handshake_persists_conversation 的 Process 面）', async () => {
    const { proc, spawn, paths } = makeProcess();
    await proc.start(bootOf(path.join(tmp, 'home')), false);
    try {
      const fake = spawn.procs[0]!;
      await driveHandshake(fake, 'conv-xyz');
      await until(() => paths.readSession(AID)['conversation_id'] === 'conv-xyz');
      expect(proc.router.confirmed).toBe(true);
      expect(proc.router.sessionId).toBe('conv-xyz'); // process_table source_session 的数据源
      expect(hasLine(fake, '"initialized"')).toBe(true); // 通知已发
    } finally {
      await proc.stop();
    }
  });

  it('投递→turn/start→busy+相位→usage 恰一条→idle（test_full_turn_deliver_busy_activity_idle_usage 的 Process 面）', async () => {
    // py 用例 2 经 mgr.deliver（渲染 + BUSY 先行归管理器面）；降档 = renderDeliver + feed 直喂。
    const { proc, sink, spawn } = makeProcess();
    await proc.start(bootOf(path.join(tmp, 'home')), false);
    try {
      const fake = spawn.procs[0]!;
      await driveHandshake(fake, 'conv-1');
      await until(() => proc.router.confirmed);

      proc.setTurnContext(CH, null);
      await proc.feed(renderDeliver([deliverMsg('01K5MSG100000000000000000A', 'hi codex')]));
      await until(() => hasLine(fake, '"turn/start"'));
      const tsLine = fake.stdin.lines().find((ln) => ln.includes('"turn/start"'))!;
      expect(tsLine).toContain('hi codex'); // 渲染正文进 turn/start input
      expect(tsLine).not.toMatch(/"type":\s*"user"/); // 非 claude stream-json 封装（纪律 8：载体各自特化）

      fake.stdout.push(n('turn/started', { threadId: 'conv-1', turn: {} }));
      fake.stdout.push(itemStarted('agentMessage', 'a1'));
      fake.stdout.push(tokenUsage(42, 8));
      fake.stdout.push(turnDone());
      await until(() => sink.usage.length === 1);
      expect(sink.statuses()).toContain('busy');
      expect(sink.statuses().at(-1)).toBe('idle');
      expect(sink.activity.map(([, d]) => d)).toEqual(['Replying…']);
      const ev = sink.usage[0]!;
      expect(ev.input_tokens).toBe(42);
      expect(ev.output_tokens).toBe(8);
      expect(ev.channel_id).toBe(CH);
      expect(ev.source_session).toBe('conv-1');
    } finally {
      await proc.stop();
    }
  });

  it('thread 未就绪投递入队，握手后排空（test_feed_before_ready_queues_then_drains）', async () => {
    const { proc, spawn } = makeProcess();
    await proc.start(bootOf(path.join(tmp, 'home')), false);
    try {
      const fake = spawn.procs[0]!;
      proc.setTurnContext(CH, null);
      await proc.feed(renderDeliver([deliverMsg('01K5MSG100000000000000000A', 'early')]));
      expect(hasLine(fake, '"turn/start"')).toBe(false); // 未就绪 → 未提交
      await driveHandshake(fake, 'conv-1');
      await until(() => hasLine(fake, '"turn/start"')); // 就绪后排空
      expect(hasLine(fake, 'early')).toBe(true);
    } finally {
      await proc.stop();
    }
  });

  it('turn 串行队列（test_serial_turn_queue）', async () => {
    // 两连投递（同频道递增 id）→ 串行提交：turn2 待 turn1 completed 后才发。
    const { proc, spawn } = makeProcess();
    await proc.start(bootOf(path.join(tmp, 'home')), false);
    try {
      const fake = spawn.procs[0]!;
      await driveHandshake(fake, 'conv-1');
      await until(() => proc.router.confirmed);
      proc.setTurnContext(CH, null);
      await proc.feed(renderDeliver([deliverMsg('01K5MSG100000000000000000A', 'one')]));
      await until(() => hasLine(fake, 'one'));
      await proc.feed(renderDeliver([deliverMsg('01K5MSG200000000000000000A', 'two')]));
      expect(hasLine(fake, 'two')).toBe(false); // turn1 未完成 → turn2 入队不发
      fake.stdout.push(turnDone());
      await until(() => hasLine(fake, 'two')); // turn1 完成 → turn2 提交
    } finally {
      await proc.stop();
    }
  });

  it('ServerRequest 审批自动应答（test_server_request_auto_approved）', async () => {
    // NFR5：即使 approvalPolicy=never 也可能来。
    const { proc, spawn } = makeProcess();
    await proc.start(bootOf(path.join(tmp, 'home')), false);
    try {
      const fake = spawn.procs[0]!;
      await driveHandshake(fake, 'conv-1');
      const params = { callId: 'x', command: ['ls'], conversationId: 'c', cwd: '/', parsedCmd: [] };
      fake.stdout.push({ id: 99, method: 'execCommandApproval', params });
      await until(() => hasLine(fake, '"decision"'));
      const line = fake.stdin.lines().find((ln) => ln.includes('"decision"'))!;
      expect(line).toContain('"id":99'); // JSON.stringify 紧凑（py json.dumps 为 `"id": 99`）
      expect(line).toContain('"approved"');
    } finally {
      await proc.stop();
    }
  });

  it('未知 ServerRequest 保守回 error（test_unknown_server_request_conservative_error）', async () => {
    const { proc, spawn } = makeProcess();
    await proc.start(bootOf(path.join(tmp, 'home')), false);
    try {
      const fake = spawn.procs[0]!;
      await driveHandshake(fake, 'conv-1');
      fake.stdout.push({ id: 77, method: 'attestation/generate', params: {} });
      await until(() => hasLine(fake, '"error"'));
      const line = fake.stdin
        .lines()
        .find((ln) => ln.includes('"error"') && ln.includes('"id":77'))!;
      expect(line).toContain('-32601');
    } finally {
      await proc.stop();
    }
  });

  it('resume→thread/resume 保 conversation；清簿记→thread/start（test_restart_resumes_reset_starts_new 的 Process 面）', async () => {
    // py 三档经 mgr.restart/reset_session（含 clear_session wiring，管理器面见文末 RuntimeManager 组）；
    // 降档 = 同一 Process 实例 stop 后再 start（py 管理器 _launch 复用 entry.process 同席位）。
    const { proc, spawn, paths } = makeProcess();
    const b = bootOf(path.join(tmp, 'home'));
    await proc.start(b, false);
    await driveHandshake(spawn.procs[0]!, 'conv-keep');
    await until(() => paths.readSession(AID)['conversation_id'] === 'conv-keep');
    await proc.stop();

    // 一档 restart 面：resume=true → thread/resume（保 conversation）
    await proc.start(b, true);
    expect(spawn.procs).toHaveLength(2);
    await reachThreadRequest(spawn.procs[1]!, { resume: true });
    expect(hasLine(spawn.procs[1]!, 'conv-keep')).toBe(true);
    expect(paths.readSession(AID)['conversation_id']).toBe('conv-keep');
    await proc.stop();

    // 二档 reset_session 面：清簿记（管理器职责，此处显式）+ resume=false → thread/start
    paths.clearSession(AID);
    await proc.start(b, false);
    expect(spawn.procs).toHaveLength(3);
    await reachThreadRequest(spawn.procs[2]!, { resume: false });
    expect(paths.readSession(AID)).toEqual({});
    await proc.stop();
  });

  it('reset_session_args 恒空（test_reset_session_args_empty）', async () => {
    const paths = new DataPaths(path.join(tmp, 'root'));
    paths.ensureDirs();
    const sink = new RecordingSink();
    const proc = new CodexProcess(AID, sink, paths, { serverUrl: 'http://s', apiKey: 'k' });
    expect(proc.resetSessionArgs()).toEqual([]);
  });

  it('握手失败（thread 无 id）→ 杀进程（test_handshake_failure_kills_process 的 Process 面）', async () => {
    // py 原例经管理器接熔断降级（CRASH_BACKOFF monkeypatch）；Process 面检查点 = kill 已触发。
    const { proc, spawn } = makeProcess();
    await proc.start(bootOf(path.join(tmp, 'home')), false);
    try {
      const fake = spawn.procs[0]!;
      await until(() => hasLine(fake, '"initialize"'));
      fake.stdout.push({ id: 1, result: {} });
      await until(() => hasLine(fake, '"thread/start"'));
      fake.stdout.push({ id: 2, result: { thread: {} } }); // 无 id → 握手失败
      await until(() => fake.returncode !== null, 5000); // 被 kill
      expect(fake.returncode).toBe(-9);
    } finally {
      await proc.stop();
    }
  });

  it('隔离 auth.json 较新则保留、机器源更新才复制（test_codex_materialize_credentials_preserves_refreshed）', () => {
    // review #5：隔离 auth.json 比机器源新（codex 刷新 OAuth）→ 保留不覆写；机器源更新才复制。
    const machine = path.join(tmp, 'machine');
    fs.mkdirSync(machine);
    const target = path.join(tmp, 'isolated');
    fs.mkdirSync(target);
    fs.writeFileSync(path.join(machine, 'auth.json'), '{"v":"machine-old"}', 'utf-8');
    fs.writeFileSync(path.join(target, 'auth.json'), '{"v":"codex-refreshed"}', 'utf-8');
    // 隔离目标更新（codex 运行时刷新）——机器源置旧。
    fs.utimesSync(path.join(machine, 'auth.json'), 1000, 1000);
    fs.utimesSync(path.join(target, 'auth.json'), 5000, 5000);

    const copied = materializeCredentials(target, machine);
    expect(copied).toEqual([]); // 保留刷新态，未覆写
    expect(fs.readFileSync(path.join(target, 'auth.json'), 'utf-8')).toBe('{"v":"codex-refreshed"}');

    // 机器源更新（用户重登）→ 复制覆盖。
    fs.utimesSync(path.join(machine, 'auth.json'), 9000, 9000);
    const copied2 = materializeCredentials(target, machine);
    expect(copied2).toEqual(['auth.json']);
    expect(fs.readFileSync(path.join(target, 'auth.json'), 'utf-8')).toBe('{"v":"machine-old"}');
  });

});

// ========================================================= RuntimeManager 管理器面（W4 收尾清账）

interface MgrRig {
  mgr: RuntimeManager;
  sink: RecordingSink;
  spawn: SpawnRecorder;
  paths: DataPaths;
}

/** py _make_manager 对等：真 RuntimeManager（claude_code.ts）+ 注入 spawn/ulid + bind sink。 */
function makeManager(): MgrRig {
  const paths = new DataPaths(path.join(tmp, 'root'));
  paths.ensureDirs();
  const spawn = new SpawnRecorder();
  const mgr = new RuntimeManager(paths, {
    serverUrl: 'http://s',
    apiKey: 'cak_x',
    spawn: spawn.spawn,
    ulid: seqUlid,
    now,
  });
  const sink = new RecordingSink();
  mgr.bind(sink);
  return { mgr, sink, spawn, paths };
}

describe('RuntimeManager × codex（契约 E2 §1；py 经 mgr 驱动用例的管理器面）', () => {
  it('管理器按 boot.runtime 分派 CodexProcess + STARTING→就绪 IDLE（test_dispatch_picks_codex_process 的 RuntimeManager 面）', async () => {
    const { mgr, sink, spawn } = makeManager();
    // codex 分派是异步动态 import（claude_code.ts 登记差异）——mgr.start 已 await 到 spawn 完成。
    expect(await mgr.start(bootOf(path.join(tmp, 'home')))).toBe(true);
    try {
      expect(mgr.agents.get(AID)!.process).toBeInstanceOf(CodexProcess);
      expect(spawn.procs).toHaveLength(1);
      expect(spawn.procs[0]!.argv.at(-1)).toBe('app-server');
      expect(sink.statuses()[0]).toBe('starting');
      await until(() => sink.statuses().includes('idle')); // 握手前就绪 idle（同 claude）
      expect(sink.diagTypes()).toContain('agent.process_started');
    } finally {
      await mgr.stop(AID); // 收尾：停止事件收敛握手/读循环任务（桩进程 terminate）
    }
  });

  it('process_table[0].source_session = conversationId（test_handshake_persists_conversation 的管理器面）', async () => {
    const { mgr, spawn, paths } = makeManager();
    await mgr.start(bootOf(path.join(tmp, 'home')));
    try {
      const fake = spawn.procs[0]!;
      await driveHandshake(fake, 'conv-xyz');
      await until(() => paths.readSession(AID)['conversation_id'] === 'conv-xyz');
      expect(mgr.agents.get(AID)!.process.router.confirmed).toBe(true);
      expect(mgr.processTable()[0]!.source_session).toBe('conv-xyz');
      expect(hasLine(fake, '"initialized"')).toBe(true); // 通知已发
    } finally {
      await mgr.stop(AID);
    }
  });

  it('mgr.deliver 渲染 + BUSY 先行 → usage 恰一条 → idle（test_full_turn_deliver_busy_activity_idle_usage 的管理器面）', async () => {
    const { mgr, sink, spawn } = makeManager();
    await mgr.start(bootOf(path.join(tmp, 'home')));
    try {
      const fake = spawn.procs[0]!;
      await driveHandshake(fake, 'conv-1');
      await until(() => mgr.agents.get(AID)!.process.router.confirmed);

      const msg = deliverMsg('01K5MSG100000000000000000A', 'hi codex');
      expect(await mgr.deliver(AID, CH, [msg], null)).toBe(true);
      expect(sink.statuses()).toContain('busy'); // BUSY 先行：管理器 emit 先于 feed 提交
      await until(() => hasLine(fake, '"turn/start"'));
      const tsLine = fake.stdin.lines().find((ln) => ln.includes('"turn/start"'))!;
      expect(tsLine).toContain('hi codex'); // mgr 渲染正文进 turn/start input
      expect(tsLine).not.toMatch(/"type":\s*"user"/); // 非 claude stream-json 封装（纪律 8：载体各自特化）

      fake.stdout.push(n('turn/started', { threadId: 'conv-1', turn: {} }));
      fake.stdout.push(itemStarted('agentMessage', 'a1'));
      fake.stdout.push(tokenUsage(42, 8));
      fake.stdout.push(turnDone());
      await until(() => sink.usage.length === 1);
      expect(sink.statuses().at(-1)).toBe('idle');
      expect(sink.activity.map(([, d]) => d)).toEqual(['Replying…']);
      const ev = sink.usage[0]!;
      expect(ev.input_tokens).toBe(42);
      expect(ev.output_tokens).toBe(8);
      expect(ev.channel_id).toBe(CH);
      expect(ev.source_session).toBe('conv-1');
    } finally {
      await mgr.stop(AID);
    }
  });

  it('turn 串行队列 + 去重游标（test_serial_turn_queue 的管理器面）', async () => {
    const { mgr, spawn } = makeManager();
    await mgr.start(bootOf(path.join(tmp, 'home')));
    try {
      const fake = spawn.procs[0]!;
      await driveHandshake(fake, 'conv-1');
      await until(() => mgr.agents.get(AID)!.process.router.confirmed);
      const one = deliverMsg('01K5MSG100000000000000000A', 'one');
      expect(await mgr.deliver(AID, CH, [one], null)).toBe(true);
      await until(() => hasLine(fake, 'one'));
      // 去重游标（契约 D §5.2）：同批重投（≤ 该频道已喂最大 message_id）→ noop 不再排 turn。
      // py test_serial_turn_queue 无此断言（lifecycle 面已覆盖 claude 路径）；挂账文本点名
      // 「去重游标」→ 此处补管理器游标经 codex 路径的检查点（测试面增补，非行为改进）。
      expect(await mgr.deliver(AID, CH, [one], null)).toBe(false);
      expect(await mgr.deliver(AID, CH, [deliverMsg('01K5MSG200000000000000000A', 'two')], null)).toBe(true);
      expect(hasLine(fake, 'two')).toBe(false); // turn1 未完成 → turn2 入队不发
      fake.stdout.push(turnDone());
      await until(() => hasLine(fake, 'two')); // turn1 完成 → turn2 提交
    } finally {
      await mgr.stop(AID);
    }
  });

  it('mgr.restart→thread/resume 保 conversation；reset_session 清簿记→thread/start（test_restart_resumes_reset_starts_new 的管理器面）', async () => {
    const { mgr, spawn, paths } = makeManager();
    const b = bootOf(path.join(tmp, 'home'));
    await mgr.start(b);
    try {
      await driveHandshake(spawn.procs[0]!, 'conv-keep');
      await until(() => paths.readSession(AID)['conversation_id'] === 'conv-keep');

      await mgr.restart(b); // 一档：保 conversation → thread/resume
      expect(spawn.procs).toHaveLength(2);
      await reachThreadRequest(spawn.procs[1]!, { resume: true });
      expect(hasLine(spawn.procs[1]!, 'conv-keep')).toBe(true);
      expect(paths.readSession(AID)['conversation_id']).toBe('conv-keep');

      await mgr.resetSession(b); // 二档：清簿记（clear_session wiring）→ thread/start
      expect(spawn.procs).toHaveLength(3);
      await reachThreadRequest(spawn.procs[2]!, { resume: false });
      expect(paths.readSession(AID)).toEqual({});
    } finally {
      await mgr.stop(AID);
    }
  });

  it('握手失败 kill → on_exit → 熔断退避重拉起（test_handshake_failure_kills_process 的管理器面；CRASH_BACKOFF 归零）', async () => {
    CRASH_BACKOFF.splice(0, CRASH_BACKOFF.length, 0, 0, 0); // afterEach 还原（lifecycle 先例）
    const { mgr, sink, spawn } = makeManager();
    await mgr.start(bootOf(path.join(tmp, 'home')));
    try {
      const fake = spawn.procs[0]!;
      await until(() => hasLine(fake, '"initialize"'));
      fake.stdout.push({ id: 1, result: {} });
      await until(() => hasLine(fake, '"thread/start"'));
      fake.stdout.push({ id: 2, result: { thread: {} } }); // 无 id → 握手失败
      await until(() => fake.returncode !== null, 5000); // 被 kill（py 原例断言止于此）
      expect(fake.returncode).toBe(-9);
      // TS 增补（验挂账语句的 on_exit → 熔断降级全链；测试面增补，非行为改进）：
      // FakeProc.kill 不 EOF stdout（py 同款）→ 显式 EOF 驱动读循环观察进程终结 → on_exit
      // → 熔断监督（退避 0）→ 重拉起。
      fake.stdout.eof();
      await until(() => spawn.procs.length === 2, 5000);
      expect(sink.diagTypes()).toContain('agent.process_exited');
      expect(sink.diagTypes()).toContain('agent.crash_restarted');
    } finally {
      await mgr.stop(AID);
    }
  });
});
