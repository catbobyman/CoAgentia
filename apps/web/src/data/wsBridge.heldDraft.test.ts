// held_draft.* 桥接回归(M4b):held_draft.created 去重 append 到 qk.heldDrafts(channel) 档、
// held_draft.updated 按 id 替换(含 released/discarded/resolved 终态反流),该频道未加载则放行不建。
// 照 wsBridge.reminder.test.ts 的 seedClient/构造 Envelope/applyEnvelope 范式。
// 运行:pnpm -F @coagentia/web test
import { QueryClient } from '@tanstack/react-query';
import { describe, expect, it } from 'vitest';

import type { Envelope, HeldDraftPublic } from '@coagentia/contracts-ts';

import { qk } from '../lib/queryKeys';
import { applyEnvelope } from './wsBridge';

const CH = 'ch_build';

function draft(id: string, over: Partial<HeldDraftPublic> = {}): HeldDraftPublic {
  return {
    id,
    agent_member_id: 'mem_agent',
    channel_id: CH,
    created_at: '2026-07-10T00:00:00Z',
    draft_body: '草稿正文',
    next_reeval_at: '2026-07-10T01:00:00Z',
    reasons: { total_unread: 2, unread_message_ids: ['msg_a', 'msg_b'] },
    status: 'held',
    workspace_id: 'ws_1',
    ...over,
  };
}

function env(type: 'held_draft.created' | 'held_draft.updated', d: HeldDraftPublic): Envelope {
  return {
    type,
    workspace_id: 'ws_1',
    seq: 1,
    key: 'k1',
    at: '2026-07-10T00:30:00Z',
    data: { draft: d },
  } as Envelope;
}

describe('wsBridge held_draft.created', () => {
  it('去重后追加到该频道的 heldDrafts 档(末尾)', () => {
    const qc = new QueryClient();
    qc.setQueryData<HeldDraftPublic[]>(qk.heldDrafts(CH), [draft('hd_1')]);

    applyEnvelope(qc, env('held_draft.created', draft('hd_2')));

    const list = qc.getQueryData<HeldDraftPublic[]>(qk.heldDrafts(CH));
    expect(list).toHaveLength(2);
    expect(list![0]!.id).toBe('hd_1');
    expect(list![1]!.id).toBe('hd_2');
  });

  it('幂等:同 id 重放不重复(按 id 替换而非追加)', () => {
    const qc = new QueryClient();
    qc.setQueryData<HeldDraftPublic[]>(qk.heldDrafts(CH), [draft('hd_1')]);

    applyEnvelope(qc, env('held_draft.created', draft('hd_1', { draft_body: '改过' })));

    const list = qc.getQueryData<HeldDraftPublic[]>(qk.heldDrafts(CH));
    expect(list).toHaveLength(1);
    expect(list![0]!.draft_body).toBe('改过');
  });

  it('该频道的 heldDrafts 未加载(缓存缺失)则放行不建', () => {
    const qc = new QueryClient();
    applyEnvelope(qc, env('held_draft.created', draft('hd_9')));
    expect(qc.getQueryData<HeldDraftPublic[]>(qk.heldDrafts(CH))).toBeUndefined();
  });
});

describe('wsBridge held_draft.updated', () => {
  it('按 id 替换(released 终态反流)', () => {
    const qc = new QueryClient();
    qc.setQueryData<HeldDraftPublic[]>(qk.heldDrafts(CH), [draft('hd_1'), draft('hd_2')]);

    applyEnvelope(
      qc,
      env('held_draft.updated', draft('hd_1', { status: 'released', resolution: 'released' })),
    );

    const list = qc.getQueryData<HeldDraftPublic[]>(qk.heldDrafts(CH));
    expect(list).toHaveLength(2);
    expect(list!.find((d) => d.id === 'hd_1')!.status).toBe('released');
    expect(list!.find((d) => d.id === 'hd_2')!.status).toBe('held'); // 其它不动
  });

  it('id 不在列表则安全追加(不抛错)', () => {
    const qc = new QueryClient();
    qc.setQueryData<HeldDraftPublic[]>(qk.heldDrafts(CH), [draft('hd_1')]);
    expect(() => applyEnvelope(qc, env('held_draft.updated', draft('hd_x')))).not.toThrow();
    expect(qc.getQueryData<HeldDraftPublic[]>(qk.heldDrafts(CH))).toHaveLength(2);
  });

  it('未加载则放行不建', () => {
    const qc = new QueryClient();
    applyEnvelope(qc, env('held_draft.updated', draft('hd_1', { status: 'discarded' })));
    expect(qc.getQueryData<HeldDraftPublic[]>(qk.heldDrafts(CH))).toBeUndefined();
  });
});
