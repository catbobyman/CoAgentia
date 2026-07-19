/**
 * A7 适配器测试底座：RecordingSink + FakeProc/spawn 注入 + 帧构造。
 *
 * 对等基准 = apps/daemon tests/adapter_helpers.py；帧构造器锚定 CLI 2.1.205 真机形状逐字段保留。
 * W3 适配器波（claude_code/codex）依赖本文件的接口面——py 逐一对应，勿另行发明。
 */

import type { AgentStatus, DiagnosticEventIn, TokenUsageEventIn } from '@coagentia/contracts-ts';

import type { AdapterSink } from '../src/adapter.ts';
import { AsyncEvent, AsyncQueue } from '../src/aio.ts';
import type { JsonObject } from '../src/transport.ts';

let ulidSeq = 0;

/** 确定性 ULID 桩（测试内去重/断言用；26 位 Crockford 合法形状）。 */
export function seqUlid(): string {
  ulidSeq += 1;
  return '01K5TEST' + String(ulidSeq).padStart(18, '0').slice(-18);
}

/** AdapterSink 记录器：四类回调全量收集，供断言。 */
export class RecordingSink implements AdapterSink {
  status: Array<[string, AgentStatus, string | null]> = [];
  activity: Array<[string, string]> = [];
  usage: TokenUsageEventIn[] = [];
  diagnostics: DiagnosticEventIn[] = [];

  async onStatusChanged(
    agentMemberId: string,
    status: AgentStatus,
    errorDetail: string | null = null,
  ): Promise<void> {
    this.status.push([agentMemberId, status, errorDetail]);
  }

  async onActivity(agentMemberId: string, detail: string): Promise<void> {
    this.activity.push([agentMemberId, detail]);
  }

  onUsage(event: TokenUsageEventIn): void {
    this.usage.push(event);
  }

  onDiagnostic(event: DiagnosticEventIn): void {
    this.diagnostics.push(event);
  }

  // ---- 便捷视图 ----
  statuses(): AgentStatus[] {
    return this.status.map(([, s]) => s);
  }

  diagTypes(): string[] {
    return this.diagnostics.map((d) => d.type);
  }
}

export class FakeStdin {
  chunks: Buffer[] = [];
  closed = false;

  write(data: Buffer | string): void {
    this.chunks.push(typeof data === 'string' ? Buffer.from(data, 'utf8') : data);
  }

  async drain(): Promise<void> {
    return;
  }

  close(): void {
    this.closed = true;
  }

  lines(): string[] {
    // py bytes.decode+splitlines 对等；容忍 CRLF（校准条款 cal6：win32 文本模式 +1 字节 \r）
    const parts = Buffer.concat(this.chunks).toString('utf8').split(/\r?\n/);
    if (parts.length > 0 && parts[parts.length - 1] === '') {
      parts.pop();
    }
    return parts;
  }
}

export class FakeStdout {
  private q = new AsyncQueue<Buffer>();

  async readline(): Promise<Buffer> {
    return this.q.get();
  }

  push(obj: JsonObject): void {
    this.q.put(Buffer.from(JSON.stringify(obj) + '\n', 'utf8'));
  }

  pushRaw(line: Buffer): void {
    this.q.put(line);
  }

  eof(): void {
    this.q.put(Buffer.alloc(0)); // 空 Buffer = EOF（py b"" 对等）
  }
}

/** 子进程桩：测试推 stdout 帧、控制退出（py asyncio 子进程桩对等）。 */
export class FakeProc {
  stdin = new FakeStdin();
  stdout = new FakeStdout();
  returncode: number | null = null;
  pid = 4242;
  private exited = new AsyncEvent();
  argv: string[] = [];
  cwd = '';
  env: Record<string, string> = {};

  terminate(): void {
    if (this.returncode === null) {
      this.returncode = -15;
    }
    this.exited.set();
  }

  kill(): void {
    this.returncode = -9;
    this.exited.set();
  }

  async wait(): Promise<number> {
    await this.exited.wait();
    return this.returncode ?? 0;
  }

  /** 进程退出：EOF stdout + 置 returncode（触发读循环结束 → on_exit）。 */
  finish(code = 0): void {
    this.returncode = code;
    this.stdout.eof();
    this.exited.set();
  }
}

/** spawn 注入：记录每次拉起的 FakeProc（py `__call__` 对等 = spawn 箭头属性，可直接注入）。 */
export class SpawnRecorder {
  procs: FakeProc[] = [];

  spawn = async (argv: string[], cwd: string, env: Record<string, string>): Promise<FakeProc> => {
    const p = new FakeProc();
    p.argv = argv;
    p.cwd = cwd;
    p.env = env;
    this.procs.push(p);
    return p;
  };
}

// ---- stream-json 帧构造（锚定 CLI 2.1.205 真机形状） ----

export function fInit(
  sessionId = '11111111-2222-3333-4444-555555555555',
  model = 'claude-opus-4-8',
): JsonObject {
  return {
    type: 'system',
    subtype: 'init',
    session_id: sessionId,
    model,
    tools: ['Bash', 'Read'],
    mcp_servers: [{ name: 'coagentia', status: 'connected' }],
  };
}

export function fStream(eventType: string, event: JsonObject = {}): JsonObject {
  return { type: 'stream_event', event: { type: eventType, ...event }, session_id: 's' };
}

export function fBlockStart(
  blockType: string,
  opts: { name?: string; blockId?: string; toolInput?: JsonObject } = {},
): JsonObject {
  const cb: JsonObject = { type: blockType };
  if (opts.name !== undefined) {
    cb['name'] = opts.name;
  }
  if (opts.blockId !== undefined) {
    cb['id'] = opts.blockId;
  }
  if (opts.toolInput !== undefined) {
    cb['input'] = opts.toolInput;
  }
  return fStream('content_block_start', { content_block: cb });
}

export function fAssistant(
  opts: { text?: string; toolUses?: JsonObject[]; stopReason?: string } = {},
): JsonObject {
  const { text = '', toolUses = [], stopReason = 'end_turn' } = opts;
  const content: JsonObject[] = [];
  if (text) {
    content.push({ type: 'text', text });
  }
  for (const tu of toolUses) {
    content.push({ type: 'tool_use', ...tu });
  }
  return {
    type: 'assistant',
    message: { content, stop_reason: stopReason, model: 'm', id: 'msg_1' },
    session_id: 's',
  };
}

export function fResult(
  opts: {
    subtype?: string;
    isError?: boolean;
    inputTokens?: number;
    outputTokens?: number;
    cacheRead?: number;
    cacheWrite?: number;
  } = {},
): JsonObject {
  const {
    subtype = 'success',
    isError = false,
    inputTokens = 100,
    outputTokens = 20,
    cacheRead = 5,
    cacheWrite = 3,
  } = opts;
  return {
    type: 'result',
    subtype,
    is_error: isError,
    result: 'done',
    session_id: 's',
    usage: {
      input_tokens: inputTokens,
      output_tokens: outputTokens,
      cache_read_input_tokens: cacheRead,
      cache_creation_input_tokens: cacheWrite,
    },
    total_cost_usd: 0.01,
  };
}
