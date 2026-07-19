/**
 * 重连 / 握手 / 缓冲重传（契约 D §2 退避、§4.1 hello 进程表、§7/§11.5 重传不虚增）。
 * 对等基准 = py test_reconnect.py（6 用例逐条对应）。
 *
 * py→TS 移植登记（非行为改进）：
 * - py asyncio.create_task(client.run()) + 收尾 task.cancel()/suppress(CancelledError)；TS 无
 *   任务取消注入（client.ts 头部登记差异）→ client.stop() + 关传输促 run 自然退出，
 *   withTimeout 兜上界（run 正常 resolve，无需 suppress）。
 * - py 直构 preview 模块 _Preview dataclass 种进程域记录；TS _Preview 为模块私有类 →
 *   以结构等价桩对象写入 PreviewRunner 私有注册表（hello 快照 statusOf 只读
 *   sessionId/status/port/logTail 四字段，桩覆盖该面）。
 * - py client._flush_usage/_resolve_report_ack 私有面直调；TS 同名 flushUsage/resolveReportAck
 *   为 private 且无公开驱动面 → (client as ...) 结构断言直调（任务书授权的最后手段）。
 */

import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import type { DaemonAgentState, PreviewStatusData, TokenUsageEventIn } from '@coagentia/contracts-ts';

import { sleep, withTimeout } from '../src/aio.ts';
import { BACKOFF_CAP, nextBackoff } from '../src/client.ts';
import type { JsonObject } from '../src/transport.ts';
import { newUlid } from '../src/util.ts';
import {
  AutoAckTransport,
  RecordingTransport,
  bootData,
  fakeRunner,
  instr,
  makeClient,
  until,
  usageEvent,
} from './helpers.ts';

let tmp: string;

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'coagentia-reconnect-'));
});

afterEach(() => {
  fs.rmSync(tmp, { recursive: true, force: true });
});

/** 私有 flush/ack 面的结构视图（对等 py client._flush_usage / _resolve_report_ack 直调）。 */
interface FlushFaces {
  flushUsage(): Promise<void>;
  resolveReportAck(frame: JsonObject): void;
}

function lastReport(tr: RecordingTransport, rtype: string): JsonObject {
  const reports = tr.reports(rtype);
  if (reports.length === 0) throw new Error(`no ${rtype} reports recorded`);
  return reports[reports.length - 1]!;
}

describe('reconnect（契约 D §2/§4.1/§7）', () => {
  it('退避序列 1→2→4→…→30 封顶（test_backoff_schedule）', () => {
    let b = 1.0;
    const seq = [b];
    for (let i = 0; i < 6; i += 1) {
      b = nextBackoff(b);
      seq.push(b);
    }
    expect(seq).toEqual([1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0]);
    expect(nextBackoff(30.0)).toBe(BACKOFF_CAP);
  });

  it('build_hello 反映进程表（test_build_hello_reflects_process_table）', async () => {
    const tr = new RecordingTransport();
    const { client } = makeClient(tmp, { transport: tr });
    const data = bootData(tmp);
    const aid = data['agent_member_id'] as string;
    await client.handleInstr(instr('agent.start', { agent: data }));
    const hello = client.buildHello();
    const agents = hello['agents'] as DaemonAgentState[];
    expect(agents.map((a) => a.agent_member_id)).toEqual([aid]);
    expect(['starting', 'idle', 'busy']).toContain(agents[0]!.status);
    expect((hello['buffered'] as { usage: number }).usage).toBe(0);
    // 停掉后进程表清空。
    await client.handleInstr(instr('agent.stop', { agent_member_id: aid }));
    expect(client.buildHello()['agents']).toEqual([]);
  });

  it('连接失败按退避重试直至成功（test_run_retries_connect_until_success）', async () => {
    let calls = 0;
    const tr = new AutoAckTransport();

    const connectFn = async (_url: string, _key: string): Promise<AutoAckTransport> => {
      calls += 1;
      if (calls < 3) throw new Error('connection refused');
      return tr;
    };

    const { client } = makeClient(tmp, {
      connectFn,
      runner: fakeRunner,
      backoffStart: 0.01,
      backoffCap: 0.02,
    });
    const task = client.run();
    task.catch(() => {});
    try {
      await withTimeout(client.connected.wait(), 5000);
      expect(calls).toBe(3); // 两次失败退避后第三次连上
      expect(client.helloAck).not.toBeNull();
      expect(client.helloAck!.heartbeat_sec).toBe(25);
    } finally {
      client.stop();
      await tr.close();
      await withTimeout(task, 5000);
    }
  });

  it('断连≠Agent 死亡：重连后 hello 进程表仍含存活 Agent（test_reconnect_keeps_agents_and_rehellos）', async () => {
    // 契约 D §4.2。
    const transports: AutoAckTransport[] = [];

    const connectFn = async (_url: string, _key: string): Promise<AutoAckTransport> => {
      const t = new AutoAckTransport();
      transports.push(t);
      return t;
    };

    const { client, adapter } = makeClient(tmp, {
      connectFn,
      runner: fakeRunner,
      backoffStart: 0.01,
      backoffCap: 0.02,
    });
    const task = client.run();
    task.catch(() => {});
    try {
      await withTimeout(client.connected.wait(), 5000);
      const t1 = transports[transports.length - 1]!;
      const data = bootData(tmp);
      const aid = data['agent_member_id'] as string;
      t1.feed(instr('agent.start', { agent: data }));
      await until(() => adapter.processTable().some((a) => a.agent_member_id === aid));
      // 杀连接 → 重连。
      await t1.close();
      await until(() => transports.length >= 2 && client.connected.isSet());
      const t2 = transports[transports.length - 1]!;
      const hellos = t2.sent.filter((f) => f['type'] === 'hello');
      expect(hellos.length, '重连应重发 hello').toBeGreaterThan(0);
      const table = ((hellos[hellos.length - 1]!['data'] as JsonObject)['agents'] as DaemonAgentState[]).map(
        (a) => a.agent_member_id,
      );
      expect(table).toContain(aid); // 存活进程仍在进程表
    } finally {
      client.stop();
      if (transports.length > 0) await transports[transports.length - 1]!.close();
      await withTimeout(task, 5000);
    }
  });

  it('重连 hello 携同一 boot_nonce 与预览进程表快照（test_reconnect_hello_carries_boot_nonce_and_previews）', async () => {
    // 契约 D §4.1/§4.2 v1.0.5：断连**不杀**预览——重连 hello 携**同一** boot_nonce 与预览进程表
    // 快照（server 对账 #9 以此逐会话判活，存活预览 survive WS jitter）。
    const transports: AutoAckTransport[] = [];

    const connectFn = async (_url: string, _key: string): Promise<AutoAckTransport> => {
      const t = new AutoAckTransport();
      transports.push(t);
      return t;
    };

    const { client } = makeClient(tmp, {
      connectFn,
      runner: fakeRunner,
      backoffStart: 0.01,
      backoffCap: 0.02,
    });
    // 直接在进程域种 running 记录（不起真子进程；PreviewRunner 注册表即 hello 快照事实源）。
    // py 直构 _Preview；TS 该类模块私有 → 结构等价桩（statusOf 只读下列字段面）。
    const sessionId = newUlid();
    (client.previews as unknown as { previews: Map<string, unknown> }).previews.set(sessionId, {
      sessionId,
      status: 'running',
      port: 4321,
      proc: null,
      monitor: null,
      stopping: false,
      logTail: null,
    });
    const task = client.run();
    task.catch(() => {});
    try {
      await withTimeout(client.connected.wait(), 5000);
      const t1 = transports[transports.length - 1]!;
      const hellos1 = t1.sent.filter((f) => f['type'] === 'hello');
      const hello1 = hellos1[hellos1.length - 1]!;
      await t1.close();
      await until(() => transports.length >= 2 && client.connected.isSet());
      const t2 = transports[transports.length - 1]!;
      const hellos2 = t2.sent.filter((f) => f['type'] === 'hello');
      const hello2 = hellos2[hellos2.length - 1]!;
      // boot nonce：进程级一次性——重连不变（jitter 与真重启的区分信号）。
      expect((hello1['data'] as JsonObject)['boot_nonce']).toBe(client.boot_nonce);
      expect((hello2['data'] as JsonObject)['boot_nonce']).toBe(client.boot_nonce);
      // 断连未杀：重连快照仍含存活预览（携 port）。
      const previews = (hello2['data'] as JsonObject)['previews'] as PreviewStatusData[];
      const entry = previews.find((p) => p.preview_session_id === sessionId)!;
      expect(entry.status).toBe('running');
      expect(entry.port).toBe(4321);
    } finally {
      client.stop();
      if (transports.length > 0) await transports[transports.length - 1]!.close();
      await withTimeout(task, 5000);
    }
  });

  it('§11.5 usage 未 ack 同 ULID 批重传、ack 后清空（test_usage_retransmit_no_inflation）', async () => {
    // daemon 侧半边：未 ack → 同 ULID 批重传；ack 后清空（server 按 ULID 去重）。
    const tr = new RecordingTransport();
    const { client } = makeClient(tmp, { transport: tr, ackTimeout: 0.05 });
    const aid = '01K5AGENT0000000000000000A';
    for (let i = 0; i < 10; i += 1) {
      client.onUsage(usageEvent(aid) as unknown as TokenUsageEventIn);
    }
    const ids = client.buffer.peekUsage(500).map((e) => e.id);
    expect(ids).toHaveLength(10);

    // 第一次 flush：无 ack → 超时 → 全量保留。
    await (client as unknown as FlushFaces).flushUsage();
    expect(client.buffer.counts().usage).toBe(10);
    const rep1 = lastReport(tr, 'usage.batch');
    expect(((rep1['data'] as JsonObject)['events'] as Array<{ id: string }>).map((e) => e.id)).toEqual(ids);

    // 第二次 flush：并发解析 ack → 落库确认 → 缓冲清空；ULID 与首发一致（不虚增）。
    const flush = (client as unknown as FlushFaces).flushUsage();
    await sleep(10);
    const rep2 = lastReport(tr, 'usage.batch');
    (client as unknown as FlushFaces).resolveReportAck({
      kind: 'ack',
      ref: rep2['frame_id'],
      result: 'done',
    });
    await withTimeout(flush, 2000);
    expect(client.buffer.counts().usage).toBe(0);
    expect(((rep2['data'] as JsonObject)['events'] as Array<{ id: string }>).map((e) => e.id)).toEqual(ids);
  });
});
