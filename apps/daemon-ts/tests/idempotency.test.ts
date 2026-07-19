/**
 * 指令幂等消费（契约 D §5：自然键幂等 + frame_id 短窗去重加速器）。
 * 对等基准 = py test_idempotency.py（9 用例逐条对应）。
 *
 * 驱动方式：内存传输 + 直接调用 client.handleInstr（免 server）。断言 ack 结果与假适配器副作用次数。
 *
 * py→TS 移植登记（非行为改进）：
 * - py client.deploys = DeployRunner(...) 猴补；TS deploys 为 readonly 面 → 结构剥离后赋值
 *   （(client as { deploys }) 等价 monkeypatch，任务书授权的最后手段）。
 * - test_deploy_run_now_supported_acks_done：TS afterEach 即时删 tmp（py tmp_path 生命周期到
 *   session 末），补一行等后台终态落盘再返回，避免与清理竞态——非断言面差异。
 */

import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import type { JsonObject } from '../src/transport.ts';
import { DeployRunner } from '../src/deploy.ts';
import type { DeployProcessRunner } from '../src/deploy.ts';
import { newUlid } from '../src/util.ts';
import {
  RecordingTransport,
  bootData,
  instr,
  makeClient,
  messagePublic,
  until,
} from './helpers.ts';

let tmp: string;

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'coagentia-idem-'));
});

afterEach(() => {
  fs.rmSync(tmp, { recursive: true, force: true });
});

describe('idempotency（契约 D §5）', () => {
  it('agent.start 同帧重发 done→noop（test_agent_start_done_then_noop_same_frame）', async () => {
    const tr = new RecordingTransport();
    const { client, adapter } = makeClient(tmp, { transport: tr });
    const data = bootData(tmp);
    const frame = instr('agent.start', { agent: data });

    await client.handleInstr(frame);
    expect(adapter.starts).toEqual([data['agent_member_id']]);
    expect(tr.lastAck()['result']).toBe('done');

    // 同 frame_id 重发（ack 丢失场景）→ noop，假适配器不产生第二次副作用。
    await client.handleInstr(frame);
    expect(adapter.starts).toEqual([data['agent_member_id']]); // 仍只 1 次
    expect(tr.lastAck()['result']).toBe('noop');
  });

  it('agent.start 自然键去重不依赖 frame（test_agent_start_noop_by_natural_key_diff_frame）', async () => {
    // 正确性押在自然键：不同 frame_id、同 agent 已在跑 → noop（不依赖 frame 去重）。
    const tr = new RecordingTransport();
    const { client, adapter } = makeClient(tmp, { transport: tr });
    const data = bootData(tmp);
    await client.handleInstr(instr('agent.start', { agent: data }));
    await client.handleInstr(instr('agent.start', { agent: data })); // 新 frame_id
    expect(adapter.starts).toEqual([data['agent_member_id']]);
    expect(tr.lastAck()['result']).toBe('noop');
  });

  it('agent.start 上报 starting→idle（test_agent_start_emits_status_reports）', async () => {
    const tr = new RecordingTransport();
    const { client } = makeClient(tmp, { transport: tr });
    const data = bootData(tmp);
    await client.handleInstr(instr('agent.start', { agent: data }));
    const statuses = tr.reports('agent.status_changed').map((r) => (r['data'] as JsonObject)['status']);
    expect(statuses).toEqual(['starting', 'idle']); // 契约 D §7：starting→idle 上报
  });

  it('agent.stop done→noop（test_agent_stop_done_then_noop）', async () => {
    const tr = new RecordingTransport();
    const { client } = makeClient(tmp, { transport: tr });
    const data = bootData(tmp);
    const aid = data['agent_member_id'] as string;
    await client.handleInstr(instr('agent.start', { agent: data }));
    await client.handleInstr(instr('agent.stop', { agent_member_id: aid }));
    expect(tr.lastAck()['result']).toBe('done');
    await client.handleInstr(instr('agent.stop', { agent_member_id: aid })); // 已停
    expect(tr.lastAck()['result']).toBe('noop');
  });

  it('message.deliver 按批内最大 message_id 去重（test_message_deliver_dedup_by_max_id）', async () => {
    const tr = new RecordingTransport();
    const { client, adapter } = makeClient(tmp, { transport: tr });
    const data = bootData(tmp);
    const aid = data['agent_member_id'] as string;
    const ch = '01K5CHAN00000000000000000A';
    await client.handleInstr(instr('agent.start', { agent: data }));
    const msg = messagePublic(ch);
    const deliver = instr('message.deliver', {
      agent_member_id: aid,
      channel_id: ch,
      messages: [msg],
      thread_root_id: null,
    });
    await client.handleInstr(deliver);
    expect(tr.lastAck()['result']).toBe('done');
    expect(adapter.delivers).toEqual([[aid, msg['id']]]);
    // 重复投递同批（不同 frame_id）→ 已喂过的最大 message_id → noop 去重。
    const deliver2 = instr('message.deliver', {
      agent_member_id: aid,
      channel_id: ch,
      messages: [msg],
      thread_root_id: null,
    });
    await client.handleInstr(deliver2);
    expect(tr.lastAck()['result']).toBe('noop');
    expect(adapter.delivers).toHaveLength(1);
  });

  it('agent.wake 已清醒 → noop（test_wake_noop_when_already_awake）', async () => {
    const tr = new RecordingTransport();
    const { client } = makeClient(tmp, { transport: tr });
    const data = bootData(tmp);
    const aid = data['agent_member_id'] as string;
    await client.handleInstr(instr('agent.start', { agent: data }));
    const wake = instr('agent.wake', {
      agent_member_id: aid,
      reason: 'mention',
      refs: { message_ids: [messagePublic('c')['id']] },
    });
    await client.handleInstr(wake);
    expect(tr.lastAck()['result']).toBe('done');
    await client.handleInstr(instr('agent.wake', { agent_member_id: aid, reason: 'mention', refs: {} }));
    expect(tr.lastAck()['result']).toBe('noop');
  });

  it('runtime.rescan 上报 runtimes.detected（test_runtime_rescan_reports_detected）', async () => {
    const tr = new RecordingTransport();

    const runner = async (_argv: string[]): Promise<[number, string, string]> => [
      0,
      '2.1.205 (Claude Code)',
      '',
    ];

    const { client } = makeClient(tmp, { transport: tr, runner });
    await client.handleInstr(instr('runtime.rescan', {}));
    expect(tr.lastAck()['result']).toBe('done');
    expect(tr.reports('runtimes.detected').length, 'rescan 应上报 runtimes.detected').toBeGreaterThan(0);
  });

  it('deploy.run 已落地即 ack done（test_deploy_run_now_supported_acks_done）', async () => {
    // preview.start/stop 自 K2、deploy.run 自 M7b K4 起均已落地——M7 指令目录再无 _unsupported。
    const fake: DeployProcessRunner = async () => ({ exitCode: 0, url: 'https://demo.example.com' });

    const tr = new RecordingTransport();
    const { client } = makeClient(tmp, { transport: tr });
    (client as unknown as { deploys: DeployRunner }).deploys = new DeployRunner({ runner: fake });
    await client.handleInstr(
      instr('deploy.run', {
        deployment_id: newUlid(),
        repo_path: '/r',
        command: 'run',
        branch: 'main',
      }),
    );
    expect(tr.lastAck()['result']).toBe('done'); // 起后台 task 即 ack DONE（不再 UNSUPPORTED）
    // TS 侧收尾（登记）：等后台终态经回调落盘缓冲，再让 afterEach 删 tmp（py 无此竞态面）。
    await until(() => client.buffer.hasDeployFinished());
  });

  it('agent.reset_full 清 Home 与 session（test_reset_full_clears_home_and_session）', async () => {
    const tr = new RecordingTransport();
    const { client, adapter } = makeClient(tmp, { transport: tr });
    const data = bootData(tmp);
    const aid = data['agent_member_id'] as string;
    await client.handleInstr(instr('agent.start', { agent: data }));
    // 在 daemon 管理的 agent home 落点写一个文件 + 会话簿记。
    const home = client.paths.ensureAgentHome(aid);
    fs.writeFileSync(path.join(home, 'junk.txt'), 'x', 'utf-8');
    client.paths.writeSession(aid, { source_session: 's' });
    await client.handleInstr(instr('agent.reset_full', { agent: data }));
    expect(tr.lastAck()['result']).toBe('done');
    expect(fs.readdirSync(home)).toEqual([]); // Home 内容清空、目录保留
    expect(client.paths.readSession(aid)).toEqual({});
    expect(adapter.resetFulls).toContain(aid);
  });
});
