// M6b wsBridge 提案事件（契约 C §7 M6 预留族）：proposal.updated / draft.presented 载
// ProposalPublic 按 id patch qk.proposal；draft.superseded 仅载 proposal_id → invalidate。
import { QueryClient } from '@tanstack/react-query';
import { describe, expect, it } from 'vitest';

import type { Envelope, ProposalPublic } from '@coagentia/contracts-ts';

import { qk } from '../lib/queryKeys';
import { applyEnvelope } from './wsBridge';

const PROPOSAL: ProposalPublic = {
  id: 'prop_1', workspace_id: 'ws_1', channel_id: 'ch_1', source_task_id: 'task_1',
  kind: 'full', revision: 1, status: 'validating',
  body: { version: 'coagentia.decomposition.v1', mode: 'decompose', nodes: [], edges: [] },
  proposal_hash: 'a'.repeat(64), proposed_by_member_id: 'mem_orch',
  created_at: '2026-07-12T00:00:00Z', updated_at: '2026-07-12T00:00:00Z',
};

function envelope(type: string, data: unknown, seq = 3): Envelope {
  return {
    type, workspace_id: 'ws_1', channel_id: 'ch_1', seq,
    key: 'proposal:prop_1', at: '2026-07-12T00:00:01Z', data,
  } as Envelope;
}

describe('wsBridge proposal.updated / draft.presented / draft.superseded', () => {
  it('proposal.updated 按 id 整体替换已加载的提案缓存（重复应用幂等）', () => {
    const qc = new QueryClient();
    qc.setQueryData(qk.proposal(PROPOSAL.id), PROPOSAL);
    const updated: ProposalPublic = { ...PROPOSAL, status: 'awaiting_confirm' };

    applyEnvelope(qc, envelope('proposal.updated', { proposal: updated }));
    applyEnvelope(qc, envelope('proposal.updated', { proposal: updated }, 4));

    expect(qc.getQueryData<ProposalPublic>(qk.proposal(PROPOSAL.id))?.status)
      .toBe('awaiting_confirm');
  });

  it('draft.presented 同载 ProposalPublic，走同一 patch（提案卡态刷新；草稿层渲染归后半）', () => {
    const qc = new QueryClient();
    qc.setQueryData(qk.proposal(PROPOSAL.id), PROPOSAL);
    const presented: ProposalPublic = { ...PROPOSAL, status: 'awaiting_confirm' };

    applyEnvelope(qc, envelope('draft.presented', { proposal: presented }));

    expect(qc.getQueryData<ProposalPublic>(qk.proposal(PROPOSAL.id))?.status)
      .toBe('awaiting_confirm');
  });

  it('提案未加载时不凭 WS 造缓存（卡片挂载时 REST 拉全，同 reminder/held_draft 范式）', () => {
    const qc = new QueryClient();
    applyEnvelope(qc, envelope('proposal.updated', { proposal: PROPOSAL }));
    expect(qc.getQueryData(qk.proposal(PROPOSAL.id))).toBeUndefined();
  });

  it('draft.superseded 仅载 proposal_id → 失效该提案 query（已挂载的卡 refetch 终态）', () => {
    const qc = new QueryClient();
    qc.setQueryData(qk.proposal(PROPOSAL.id), PROPOSAL);

    applyEnvelope(qc, envelope('draft.superseded', { proposal_id: PROPOSAL.id }));

    expect(qc.getQueryState(qk.proposal(PROPOSAL.id))?.isInvalidated).toBe(true);
  });
});
