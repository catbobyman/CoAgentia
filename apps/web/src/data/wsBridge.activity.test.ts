// activity.* 桥接回归:activity.created 倒序 append 到 'all' 档、activity.done 标记 done_at。
// 照 wsBridge.test.ts 的 seedClient/构造 Envelope/applyEnvelope 范式。
// 运行:pnpm -F @coagentia/web test
import { QueryClient } from '@tanstack/react-query';
import { describe, expect, it } from 'vitest';

import type { ActivityItemPublic, Envelope } from '@coagentia/contracts-ts';

import { qk } from '../lib/queryKeys';
import { applyEnvelope } from './wsBridge';

function activityItem(
  id: string,
  doneAt?: string,
  kind: ActivityItemPublic['kind'] = 'mention',
): ActivityItemPublic {
  return {
    id,
    kind,
    member_id: 'mem_owner',
    workspace_id: 'ws_1',
    created_at: '2026-07-09T00:00:00Z',
    done_at: doneAt,
  };
}

function createdEnv(item: ActivityItemPublic): Envelope {
  return {
    type: 'activity.created',
    workspace_id: 'ws_1',
    seq: 1,
    key: 'k1',
    at: '2026-07-09T01:00:00Z',
    data: { item },
  } as Envelope;
}

function doneEnv(itemId: string): Envelope {
  return {
    type: 'activity.done',
    workspace_id: 'ws_1',
    seq: 2,
    key: 'k2',
    at: '2026-07-09T02:00:00Z',
    data: { item_id: itemId },
  } as Envelope;
}

describe('wsBridge activity.created', () => {
  it('倒序 append 到 all 档(新的在前)', () => {
    const qc = new QueryClient();
    qc.setQueryData<ActivityItemPublic[]>(qk.activity('all'), [activityItem('act_1')]);

    applyEnvelope(qc, createdEnv(activityItem('act_2')));

    const list = qc.getQueryData<ActivityItemPublic[]>(qk.activity('all'));
    expect(list).toHaveLength(2);
    expect(list![0]!.id).toBe('act_2'); // 新的在前
    expect(list![1]!.id).toBe('act_1');
  });

  it('幂等:同 id 重放不重复', () => {
    const qc = new QueryClient();
    qc.setQueryData<ActivityItemPublic[]>(qk.activity('all'), [activityItem('act_1')]);

    applyEnvelope(qc, createdEnv(activityItem('act_1')));

    expect(qc.getQueryData<ActivityItemPublic[]>(qk.activity('all'))).toHaveLength(1);
  });

  it('all 档缓存缺失时安全建档', () => {
    const qc = new QueryClient();
    applyEnvelope(qc, createdEnv(activityItem('act_9')));
    const list = qc.getQueryData<ActivityItemPublic[]>(qk.activity('all'));
    expect(list).toHaveLength(1);
    expect(list![0]!.id).toBe('act_9');
  });

  it('已挂载的 unread/mentions 档也实时 patch(停在这些 tab 时列表不再滞后于徽标)', () => {
    const qc = new QueryClient();
    qc.setQueryData<ActivityItemPublic[]>(qk.activity('all'), []);
    qc.setQueryData<ActivityItemPublic[]>(qk.activity('unread'), []);
    qc.setQueryData<ActivityItemPublic[]>(qk.activity('mentions'), []);

    applyEnvelope(qc, createdEnv(activityItem('act_m', undefined, 'mention')));

    expect(qc.getQueryData<ActivityItemPublic[]>(qk.activity('unread'))).toHaveLength(1); // 新项未读
    expect(qc.getQueryData<ActivityItemPublic[]>(qk.activity('mentions'))).toHaveLength(1); // kind=mention
  });

  it('归属过滤:dm 类不进 mentions 档;已读项不进 unread 档', () => {
    const qc = new QueryClient();
    qc.setQueryData<ActivityItemPublic[]>(qk.activity('unread'), []);
    qc.setQueryData<ActivityItemPublic[]>(qk.activity('mentions'), []);

    applyEnvelope(qc, createdEnv(activityItem('act_dm', undefined, 'dm')));
    expect(qc.getQueryData<ActivityItemPublic[]>(qk.activity('mentions'))).toHaveLength(0); // dm ∉ mentions
    expect(qc.getQueryData<ActivityItemPublic[]>(qk.activity('unread'))).toHaveLength(1); // dm 未读 ∈ unread
  });

  it('不凭空为未挂载的 unread 档建偏缓存', () => {
    const qc = new QueryClient();
    qc.setQueryData<ActivityItemPublic[]>(qk.activity('all'), []);
    applyEnvelope(qc, createdEnv(activityItem('act_x')));
    expect(qc.getQueryData<ActivityItemPublic[]>(qk.activity('unread'))).toBeUndefined();
  });
});

describe('wsBridge activity.done', () => {
  it('在所有 activity 档里把该 item 的 done_at 置为事件时间戳', () => {
    const qc = new QueryClient();
    qc.setQueryData<ActivityItemPublic[]>(qk.activity('all'), [
      activityItem('act_1'),
      activityItem('act_2'),
    ]);
    qc.setQueryData<ActivityItemPublic[]>(qk.activity('unread'), [activityItem('act_1')]);

    applyEnvelope(qc, doneEnv('act_1'));

    const all = qc.getQueryData<ActivityItemPublic[]>(qk.activity('all'));
    expect(all!.find((a) => a.id === 'act_1')!.done_at).toBe('2026-07-09T02:00:00Z');
    expect(all!.find((a) => a.id === 'act_2')!.done_at).toBeUndefined(); // 其它不动
    const unread = qc.getQueryData<ActivityItemPublic[]>(qk.activity('unread'));
    expect(unread!.find((a) => a.id === 'act_1')!.done_at).toBe('2026-07-09T02:00:00Z');
  });

  it('item 不在任何缓存时安全忽略(不抛错)', () => {
    const qc = new QueryClient();
    qc.setQueryData<ActivityItemPublic[]>(qk.activity('all'), [activityItem('act_1')]);
    expect(() => applyEnvelope(qc, doneEnv('act_missing'))).not.toThrow();
    expect(
      qc.getQueryData<ActivityItemPublic[]>(qk.activity('all'))!.find((a) => a.id === 'act_1')!
        .done_at,
    ).toBeUndefined();
  });
});
