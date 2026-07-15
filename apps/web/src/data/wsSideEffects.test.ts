// M6b WS 副作用桥单测：rev 替换切激活草稿 + landing.* → 信号 kind。
import { QueryClient } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';

import type { Envelope, ProposalPublic } from '@coagentia/contracts-ts';

import { qk } from '../lib/queryKeys';
import { landingSignalKind, reconcileActiveDraft } from './wsSideEffects';

function prop(over: Partial<ProposalPublic> = {}): ProposalPublic {
  return {
    id: 'prop_1', workspace_id: 'ws_1', channel_id: 'ch_1', source_task_id: 'task_1',
    kind: 'full', revision: 1, status: 'awaiting_confirm',
    body: {}, proposal_hash: 'a'.repeat(64), proposed_by_member_id: 'm',
    created_at: 't', updated_at: 't', ...over,
  };
}
function env(type: string, data: unknown, channel_id: string | null = 'ch_1'): Envelope {
  return { type, workspace_id: 'ws_1', channel_id, seq: 1, key: 'k', at: 't', data } as Envelope;
}

describe('reconcileActiveDraft（rev 替换）', () => {
  it('新 draft.presented 同 source 任务 → 切 activeDraft 到新提案 id', () => {
    const qc = new QueryClient();
    const old = prop({ id: 'prop_1', revision: 1 });
    qc.setQueryData(qk.proposal('prop_1'), old);
    const active: Record<string, string | null> = { ch_1: 'prop_1' };
    const store = {
      getActiveDraft: (c: string) => active[c],
      setActiveDraft: (c: string, id: string | null) => { active[c] = id; },
    };
    const next = prop({ id: 'prop_2', revision: 2, source_task_id: 'task_1' });
    reconcileActiveDraft(env('draft.presented', { proposal: next }), qc, store);
    expect(active['ch_1']).toBe('prop_2');
  });

  it('无激活草稿 → 不切换', () => {
    const qc = new QueryClient();
    const active: Record<string, string | null> = {};
    const store = {
      getActiveDraft: (c: string) => active[c],
      setActiveDraft: (c: string, id: string | null) => { active[c] = id; },
    };
    reconcileActiveDraft(env('draft.presented', { proposal: prop({ id: 'prop_2' }) }), qc, store);
    expect(active['ch_1']).toBeUndefined();
  });

  it('新提案不同 source 任务 → 不切换（非 rev 替换）', () => {
    const qc = new QueryClient();
    qc.setQueryData(qk.proposal('prop_1'), prop({ id: 'prop_1', source_task_id: 'task_1' }));
    const active: Record<string, string | null> = { ch_1: 'prop_1' };
    const store = {
      getActiveDraft: (c: string) => active[c],
      setActiveDraft: (c: string, id: string | null) => { active[c] = id; },
    };
    const next = prop({ id: 'prop_9', source_task_id: 'task_OTHER' });
    reconcileActiveDraft(env('draft.presented', { proposal: next }), qc, store);
    expect(active['ch_1']).toBe('prop_1');
  });

  it('非 draft.presented / delta 提案 → 忽略', () => {
    const qc = new QueryClient();
    const set = vi.fn();
    const store = { getActiveDraft: () => 'prop_1', setActiveDraft: set };
    reconcileActiveDraft(env('proposal.updated', { proposal: prop({ id: 'prop_2' }) }), qc, store);
    reconcileActiveDraft(env('draft.presented', { proposal: prop({ id: 'prop_2', kind: 'delta' }) }), qc, store);
    expect(set).not.toHaveBeenCalled();
  });
});

describe('landingSignalKind', () => {
  it('landing.* → kind；其余 → null', () => {
    expect(landingSignalKind(env('landing.started', {}))).toBe('started');
    expect(landingSignalKind(env('landing.completed', {}))).toBe('completed');
    expect(landingSignalKind(env('landing.fail_closed', {}))).toBe('fail_closed');
    expect(landingSignalKind(env('task.updated', {}))).toBeNull();
  });
});
