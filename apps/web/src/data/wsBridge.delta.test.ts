// M6b wsBridge delta.* / landing.* 事件族（契约 C §7 预留）：delta.* 载 {proposal} 按 id patch
// qk.proposal；landing.completed/fail_closed 载 {batch} → 失效该频道画布/任务/主流（画布刷新）。
import { QueryClient } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';

import type { Envelope, LandingBatchPublic, ProposalPublic } from '@coagentia/contracts-ts';

import { qk } from '../lib/queryKeys';
import { applyEnvelope } from './wsBridge';

const PROPOSAL: ProposalPublic = {
  id: 'prop_d', workspace_id: 'ws_1', channel_id: 'ch_1', source_task_id: 'task_1',
  kind: 'delta', revision: 1, status: 'awaiting_confirm', base_hash: 'b'.repeat(64),
  body: { version: 'coagentia.decomposition-delta.v1', operations: [] },
  proposal_hash: 'a'.repeat(64), proposed_by_member_id: 'm',
  created_at: 't', updated_at: 't',
};

const BATCH: LandingBatchPublic = {
  id: 'batch_1', workspace_id: 'ws_1', channel_id: 'ch_1', kind: 'decomp',
  source_ref: 'decomp:batch_1', content_hash: 'c'.repeat(64), confirmed_by: 'mem_owner',
  created_at: 't',
};

function envelope(type: string, data: unknown): Envelope {
  return { type, workspace_id: 'ws_1', channel_id: 'ch_1', seq: 1, key: 'k', at: 't', data } as Envelope;
}

describe('wsBridge delta.*', () => {
  it.each(['delta.proposed', 'delta.adjusted', 'delta.confirmed', 'delta.rejected'])(
    '%s 按 id 整体替换已加载的提案缓存',
    (type) => {
      const qc = new QueryClient();
      qc.setQueryData(qk.proposal(PROPOSAL.id), PROPOSAL);
      applyEnvelope(qc, envelope(type, { proposal: { ...PROPOSAL, status: 'landed' } }));
      expect(qc.getQueryData<ProposalPublic>(qk.proposal(PROPOSAL.id))?.status).toBe('landed');
    },
  );

  it('提案未加载时不凭 WS 造缓存', () => {
    const qc = new QueryClient();
    applyEnvelope(qc, envelope('delta.proposed', { proposal: PROPOSAL }));
    expect(qc.getQueryData(qk.proposal(PROPOSAL.id))).toBeUndefined();
  });
});

describe('wsBridge landing.*', () => {
  it('landing.completed → 失效该频道画布/任务/主流', () => {
    const qc = new QueryClient();
    const spy = vi.spyOn(qc, 'invalidateQueries');
    applyEnvelope(qc, envelope('landing.completed', { batch: BATCH }));
    const keys = spy.mock.calls.map((c) => JSON.stringify(c[0]?.queryKey));
    expect(keys).toContain(JSON.stringify(qk.canvas('ch_1')));
    expect(keys).toContain(JSON.stringify(qk.tasks('ch_1')));
    expect(keys).toContain(JSON.stringify(qk.messages('ch_1')));
  });

  it('landing.fail_closed → 同样失效（画布/任务/主流）', () => {
    const qc = new QueryClient();
    const spy = vi.spyOn(qc, 'invalidateQueries');
    applyEnvelope(qc, envelope('landing.fail_closed', { batch: BATCH }));
    expect(spy).toHaveBeenCalled();
  });

  it('landing.started → 无结构变更，不失效', () => {
    const qc = new QueryClient();
    const spy = vi.spyOn(qc, 'invalidateQueries');
    applyEnvelope(qc, envelope('landing.started', { batch: BATCH }));
    expect(spy).not.toHaveBeenCalled();
  });
});
