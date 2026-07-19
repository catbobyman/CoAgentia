/**
 * daemon-ts 单测辅助：内存传输（RecordingTransport）+ 帧构造（对等基准 = apps/daemon tests/helpers.py）。
 *
 * makeClient 装配器随 client 波补齐（W4）；本文件先提供传输桩与帧构造器（W0–W3 共用）。
 */

import { FakeAdapter } from '../src/adapter.ts';
import { TelemetryBuffer } from '../src/buffer.ts';
import { DaemonClient } from '../src/client.ts';
import type { DaemonClientOptions } from '../src/client.ts';
import { DataPaths } from '../src/paths.ts';
import { TransportClosed } from '../src/transport.ts';
import type { JsonObject } from '../src/transport.ts';
import { newUlid, nowIso } from '../src/util.ts';

// 集成/握手用固定合法 ULID（与 py conftest.IntegrationEnv 对齐）。
export const HELLO_ACK_COMPUTER = '01K5CMPT00000000000000000A';
export const HELLO_ACK_WORKSPACE = '01K5WKSP00000000000000000A';

const CLOSE = Symbol('close');

export async function until(pred: () => boolean, timeout = 5000): Promise<void> {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (pred()) return;
    await new Promise((r) => setTimeout(r, 10));
  }
  throw new Error('condition not met within timeout');
}

/** 内存传输：记录 daemon 上行帧 + 可注入下行帧（无需真 socket / server）。 */
export class RecordingTransport {
  sent: JsonObject[] = [];
  closed = false;
  private incoming: Array<JsonObject | typeof CLOSE> = [];
  private waiter: { resolve: (f: JsonObject) => void; reject: (e: Error) => void } | null = null;

  async send(frame: JsonObject): Promise<void> {
    if (this.closed) throw new TransportClosed('closed');
    this.sent.push(frame);
  }

  async recv(): Promise<JsonObject> {
    const item = this.incoming.shift();
    if (item === CLOSE) throw new TransportClosed('closed');
    if (item !== undefined) return item;
    return new Promise<JsonObject>((resolve, reject) => {
      this.waiter = { resolve, reject };
    });
  }

  async close(): Promise<void> {
    this.closed = true;
    this.push(CLOSE);
  }

  // ---- 注入下行 / 过滤上行 ----
  feed(frame: JsonObject): void {
    this.push(frame);
  }

  private push(item: JsonObject | typeof CLOSE): void {
    if (this.waiter !== null) {
      const w = this.waiter;
      this.waiter = null;
      if (item === CLOSE) w.reject(new TransportClosed('closed'));
      else w.resolve(item);
      return;
    }
    this.incoming.push(item);
  }

  acks(): JsonObject[] {
    return this.sent.filter((f) => f['kind'] === 'ack');
  }

  reports(rtype?: string): JsonObject[] {
    const out = this.sent.filter((f) => f['kind'] === 'report');
    return rtype ? out.filter((f) => f['type'] === rtype) : out;
  }

  lastAck(): JsonObject {
    const acks = this.acks();
    if (acks.length === 0) throw new Error('no acks recorded');
    return acks[acks.length - 1]!;
  }
}

/** RecordingTransport + 自动应答 hello→hello_ack、ping→pong（免真 server 的握手驱动）。 */
export class AutoAckTransport extends RecordingTransport {
  constructor(readonly heartbeatSec: number = 25) {
    super();
  }

  override async send(frame: JsonObject): Promise<void> {
    await super.send(frame);
    if (frame['kind'] === 'report' && frame['type'] === 'hello') {
      this.feed({
        v: 1,
        kind: 'ack',
        ref: frame['frame_id'],
        result: 'done',
        data: {
          protocol_v: 1,
          server_version: 'test',
          computer_id: HELLO_ACK_COMPUTER,
          workspace_id: HELLO_ACK_WORKSPACE,
          heartbeat_sec: this.heartbeatSec,
        },
      });
    } else if (frame['kind'] === 'ping') {
      this.feed({ v: 1, kind: 'pong' });
    }
  }
}

// ---- client 装配（对等 py helpers.make_client）----

export interface MakeClientResult {
  client: DaemonClient;
  adapter: FakeAdapter;
  transport: RecordingTransport | null;
}

export function makeClient(
  tmpPath: string,
  opts: {
    adapter?: FakeAdapter;
    transport?: RecordingTransport | null;
    runner?: DaemonClientOptions['runner'];
  } & Partial<
    Pick<
      DaemonClientOptions,
      'connectFn' | 'heartbeatSec' | 'pongTimeout' | 'ackTimeout' | 'backoffStart' | 'backoffCap'
    >
  > = {},
): MakeClientResult {
  const adapter = opts.adapter ?? new FakeAdapter();
  const paths = new DataPaths(`${tmpPath}/root`);
  paths.ensureDirs();
  const buffer = new TelemetryBuffer(paths);
  const client = new DaemonClient({
    serverUrl: 'http://127.0.0.1:0',
    apiKey: 'cak_test',
    adapter,
    buffer,
    paths,
    osName: 'linux',
    arch: 'x64',
    runner: opts.runner ?? (async () => [0, '2.1.205 (Claude Code)', '']),
    ...(opts.connectFn !== undefined ? { connectFn: opts.connectFn } : {}),
    ...(opts.heartbeatSec !== undefined ? { heartbeatSec: opts.heartbeatSec } : {}),
    ...(opts.pongTimeout !== undefined ? { pongTimeout: opts.pongTimeout } : {}),
    ...(opts.ackTimeout !== undefined ? { ackTimeout: opts.ackTimeout } : {}),
    ...(opts.backoffStart !== undefined ? { backoffStart: opts.backoffStart } : {}),
    ...(opts.backoffCap !== undefined ? { backoffCap: opts.backoffCap } : {}),
  });
  const transport = opts.transport ?? null;
  if (transport !== null) client._transport = transport;
  return { client, adapter, transport };
}

/** 探测桩：免真 claude 子进程（对等 py helpers.fake_runner）。 */
export async function fakeRunner(_argv: string[]): Promise<[number, string, string]> {
  return [0, '2.1.205 (Claude Code)', ''];
}

// ---- 帧构造 ----

export function bootData(tmpPath: string, agentId?: string, name = 'A'): JsonObject {
  const aid = agentId ?? newUlid();
  return {
    agent_member_id: aid,
    name,
    runtime: 'claude_code',
    model: 'claude-opus-4-8',
    home_path: `${tmpPath}/home/${aid}`,
    skills: [],
  };
}

export function instr(itype: string, data: JsonObject, frameId?: string): JsonObject {
  return {
    v: 1,
    kind: 'instr',
    frame_id: frameId ?? newUlid(),
    type: itype,
    at: nowIso(),
    data,
  };
}

export function messagePublic(channelId: string, workspaceId?: string, body = 'hi'): JsonObject {
  return {
    id: newUlid(),
    workspace_id: workspaceId ?? newUlid(),
    channel_id: channelId,
    thread_root_id: null,
    author_member_id: null,
    kind: 'user',
    card_kind: null,
    card_ref: null,
    body,
    created_at: nowIso(),
  };
}

export function usageEvent(agentId: string, eventId?: string): JsonObject {
  return {
    id: eventId ?? newUlid(),
    agent_member_id: agentId,
    channel_id: null,
    thread_root_id: null,
    input_tokens: 10,
    output_tokens: 5,
    cache_read_tokens: 0,
    cache_write_tokens: 0,
    source_session: 'sess-1',
    reported_at: nowIso(),
  };
}
