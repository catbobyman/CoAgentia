// M6b 草稿层行为测试：调整应用 + 防呆重验联动 / confirm 请求体构造（expected 三字段 + adjustments）/
// 409 STALE latest 刷新 / 拒绝。照 ChannelSettingsModal.test 的 QueryClient seed + vi.mock('../api') 范式。
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    api: { ...actual.api, proposal: vi.fn(), channels: vi.fn(), confirmProposal: vi.fn(), rejectProposal: vi.fn() },
  };
});

import type { CanvasPublic, ChannelsSnapshot, ProposalPublic } from '@coagentia/contracts-ts';

import { api, ApiError } from '../api';
import { qk } from '../lib/queryKeys';
import { ToastProvider, Toaster } from './Toast';
import { DraftLayer } from './DraftLayer';

const PLAN = {
  version: 'coagentia.task-plan.v1', goal: 'g',
  acceptance_criteria: [{ id: 'ac-1', statement: 's', verify_by: 'manual', verify_ref: '' }],
};
const BODY = {
  version: 'coagentia.decomposition.v1', source: 'task_1', mode: 'decompose', summary: '实现与评审',
  nodes: [
    { temp_id: 'a', title: '需求', kind: 'agent', task_plan: PLAN },
    { temp_id: 'b', title: '实现', kind: 'agent', task_plan: PLAN },
    { temp_id: 'c', title: '评审', kind: 'agent', task_plan: PLAN },
  ],
  edges: [{ from: 'a', to: 'b' }, { from: 'b', to: 'c' }],
};
const PROPOSAL: ProposalPublic = {
  id: 'prop_1', workspace_id: 'ws_1', channel_id: 'ch_1', source_task_id: 'task_1',
  kind: 'full', revision: 2, status: 'awaiting_confirm', body: BODY,
  proposal_hash: 'abcdef0123456789'.repeat(4), proposed_by_member_id: 'mem_orch',
  created_at: 't', updated_at: 't',
};
const CANVAS: CanvasPublic = {
  id: 'canvas_1', channel_id: 'ch_1', workspace_id: 'ws_1',
  baseline_version: 5, baseline_hash: 'basehash', updated_at: 't',
};
const SNAP = {
  items: [{ id: 'ch_1', kind: 'channel', name: 'build', workspace_id: 'ws_1', created_at: 't', decomp_node_limit: 12 }],
  read_positions: [],
} as unknown as ChannelsSnapshot;

function renderLayer(over: Partial<ProposalPublic> = {}) {
  const proposal = { ...PROPOSAL, ...over };
  vi.mocked(api.proposal).mockResolvedValue(proposal);
  vi.mocked(api.channels).mockResolvedValue(SNAP);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  qc.setQueryData(qk.proposal(proposal.id), proposal);
  qc.setQueryData(qk.channels(), SNAP);
  const onClose = vi.fn();
  render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <DraftLayer channelId="ch_1" proposalId={proposal.id} members={[]} boundProjects={[]} canvas={CANVAS} onClose={onClose} />
        <Toaster />
      </ToastProvider>
    </QueryClientProvider>,
  );
  return { qc, onClose };
}

describe('DraftLayer', () => {
  beforeEach(() => vi.clearAllMocks());

  it('渲染确认条：rev/节点数/依赖数/指纹短码 + 三节点', async () => {
    renderLayer();
    const bar = await screen.findByTestId('draft-confirm-bar');
    expect(bar).toHaveTextContent('rev.2');
    expect(bar).toHaveTextContent('3 节点');
    expect(bar).toHaveTextContent('2 依赖');
    expect(bar).toHaveTextContent('abcdef');
    expect(screen.getAllByTestId('draft-node')).toHaveLength(3);
    expect(screen.getByTestId('draft-confirm')).not.toBeDisabled();
  });

  it('remove_node 调整：节点减少 + 累积调整计数', async () => {
    renderLayer();
    await screen.findByTestId('draft-confirm-bar');
    fireEvent.click(screen.getByRole('button', { name: '移除节点 评审' }));
    await waitFor(() => expect(screen.getAllByTestId('draft-node')).toHaveLength(2));
    expect(screen.getByTestId('draft-confirm-bar')).toHaveTextContent('已调整 1 项');
    expect(screen.getByTestId('draft-confirm')).not.toBeDisabled(); // 2 节点仍合法
  });

  it('防呆重验联动：删到 1 节点 → NODE_COUNT 错误 → 确认 disabled + 就地清单', async () => {
    renderLayer();
    await screen.findByTestId('draft-confirm-bar');
    fireEvent.click(screen.getByRole('button', { name: '移除节点 实现' }));
    fireEvent.click(screen.getByRole('button', { name: '移除节点 评审' }));
    await waitFor(() => expect(screen.getByTestId('draft-error-count')).toBeInTheDocument());
    expect(screen.getByTestId('draft-confirm')).toBeDisabled();
    expect(screen.getByTestId('draft-error-list')).toBeInTheDocument();
  });

  it('确认落地：请求体 = expected 三字段 + adjustments + removed_ops:[]，成功关层', async () => {
    vi.mocked(api.confirmProposal).mockResolvedValue({ batch: {} as never, proposal: { ...PROPOSAL, status: 'landing' } });
    const { onClose } = renderLayer();
    await screen.findByTestId('draft-confirm-bar');
    // 先做一次 edit_node（title），随 confirm 一次性提交。
    fireEvent.click(screen.getByRole('button', { name: '移除节点 评审' }));
    fireEvent.click(screen.getByTestId('draft-confirm'));
    await waitFor(() => expect(api.confirmProposal).toHaveBeenCalledTimes(1));
    const [pid, body] = vi.mocked(api.confirmProposal).mock.calls[0]!;
    expect(pid).toBe('prop_1');
    expect(body.expected).toEqual({ proposal_hash: PROPOSAL.proposal_hash, baseline_version: 5, baseline_hash: 'basehash' });
    expect(body.removed_ops).toEqual([]);
    expect(body.adjustments).toEqual([{ op: 'remove_node', temp_id: 'c' }]);
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('409 STALE_CONFIRM：以 latest 刷新提案 + 不关层', async () => {
    const latestProp = { ...PROPOSAL, revision: 3, proposal_hash: 'f'.repeat(64) };
    vi.mocked(api.confirmProposal).mockRejectedValue(
      new ApiError(409, 'STALE_CONFIRM', 'stale', undefined, { proposal: latestProp, baseline_version: 8, baseline_hash: 'newhash' }),
    );
    const { qc, onClose } = renderLayer();
    await screen.findByTestId('draft-confirm-bar');
    fireEvent.click(screen.getByTestId('draft-confirm'));
    await waitFor(() =>
      expect(qc.getQueryData<ProposalPublic>(qk.proposal('prop_1'))?.proposal_hash).toBe('f'.repeat(64)),
    );
    expect(onClose).not.toHaveBeenCalled();
    expect(await screen.findByText('已刷新最新态，请重审')).toBeInTheDocument();
  });

  it('拒绝：填理由 → rejectProposal → 关层', async () => {
    vi.mocked(api.rejectProposal).mockResolvedValue({ ...PROPOSAL, status: 'rejected' });
    const { onClose } = renderLayer();
    await screen.findByTestId('draft-confirm-bar');
    fireEvent.click(screen.getByTestId('draft-reject'));
    fireEvent.change(screen.getByLabelText('拒绝理由'), { target: { value: '换自动化门' } });
    fireEvent.click(screen.getByTestId('proposal-reject-submit'));
    await waitFor(() => expect(api.rejectProposal).toHaveBeenCalledWith('prop_1', '换自动化门'));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });
});
