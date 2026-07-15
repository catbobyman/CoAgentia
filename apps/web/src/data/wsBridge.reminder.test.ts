// reminder.* 桥接回归(M4a):reminder.created 去重 append 到 qk.agentReminders 档、
// reminder.updated 按 id 替换(含 cancel 反流 status=cancelled),该 agent 未加载则放行不建。
// 照 wsBridge.activity.test.ts 的 seedClient/构造 Envelope/applyEnvelope 范式。
// 运行:pnpm -F @coagentia/web test
import { QueryClient } from '@tanstack/react-query';
import { describe, expect, it } from 'vitest';

import type { Envelope, ReminderPublic } from '@coagentia/contracts-ts';

import { qk } from '../lib/queryKeys';
import { applyEnvelope } from './wsBridge';

const AGENT = 'mem_agent';

function reminder(id: string, over: Partial<ReminderPublic> = {}): ReminderPublic {
  return {
    id,
    agent_member_id: AGENT,
    anchor_channel_id: 'ch_build',
    cadence: 'daily 09:00',
    created_at: '2026-07-10T00:00:00Z',
    kind: 'recurring',
    next_fire_at: '2026-07-11T09:00:00Z',
    status: 'active',
    workspace_id: 'ws_1',
    ...over,
  };
}

function env(type: 'reminder.created' | 'reminder.updated', r: ReminderPublic): Envelope {
  return {
    type,
    workspace_id: 'ws_1',
    seq: 1,
    key: 'k1',
    at: '2026-07-10T01:00:00Z',
    data: { reminder: r },
  } as Envelope;
}

describe('wsBridge reminder.created', () => {
  it('去重后追加到该 agent 的 reminders 档(末尾,升序对齐 REST)', () => {
    const qc = new QueryClient();
    qc.setQueryData<ReminderPublic[]>(qk.agentReminders(AGENT), [reminder('rem_1')]);

    applyEnvelope(qc, env('reminder.created', reminder('rem_2')));

    const list = qc.getQueryData<ReminderPublic[]>(qk.agentReminders(AGENT));
    expect(list).toHaveLength(2);
    expect(list![0]!.id).toBe('rem_1');
    expect(list![1]!.id).toBe('rem_2');
  });

  it('幂等:同 id 重放不重复(按 id 替换而非追加)', () => {
    const qc = new QueryClient();
    qc.setQueryData<ReminderPublic[]>(qk.agentReminders(AGENT), [reminder('rem_1')]);

    applyEnvelope(qc, env('reminder.created', reminder('rem_1', { cadence: 'weekly' })));

    const list = qc.getQueryData<ReminderPublic[]>(qk.agentReminders(AGENT));
    expect(list).toHaveLength(1);
    expect(list![0]!.cadence).toBe('weekly');
  });

  it('该 agent 的 reminders 未加载(缓存缺失)则放行不建', () => {
    const qc = new QueryClient();
    applyEnvelope(qc, env('reminder.created', reminder('rem_9')));
    expect(qc.getQueryData<ReminderPublic[]>(qk.agentReminders(AGENT))).toBeUndefined();
  });
});

describe('wsBridge reminder.updated', () => {
  it('按 id 替换(cancel 反流 status=cancelled)', () => {
    const qc = new QueryClient();
    qc.setQueryData<ReminderPublic[]>(qk.agentReminders(AGENT), [
      reminder('rem_1'),
      reminder('rem_2'),
    ]);

    applyEnvelope(
      qc,
      env('reminder.updated', reminder('rem_1', { status: 'cancelled', cancelled_by_member_id: 'mem_owner' })),
    );

    const list = qc.getQueryData<ReminderPublic[]>(qk.agentReminders(AGENT));
    expect(list).toHaveLength(2);
    expect(list!.find((r) => r.id === 'rem_1')!.status).toBe('cancelled');
    expect(list!.find((r) => r.id === 'rem_2')!.status).toBe('active'); // 其它不动
  });

  it('id 不在列表则安全追加(不抛错)', () => {
    const qc = new QueryClient();
    qc.setQueryData<ReminderPublic[]>(qk.agentReminders(AGENT), [reminder('rem_1')]);
    expect(() => applyEnvelope(qc, env('reminder.updated', reminder('rem_x')))).not.toThrow();
    expect(qc.getQueryData<ReminderPublic[]>(qk.agentReminders(AGENT))).toHaveLength(2);
  });

  it('未加载则放行不建', () => {
    const qc = new QueryClient();
    applyEnvelope(qc, env('reminder.updated', reminder('rem_1', { status: 'cancelled' })));
    expect(qc.getQueryData<ReminderPublic[]>(qk.agentReminders(AGENT))).toBeUndefined();
  });
});
