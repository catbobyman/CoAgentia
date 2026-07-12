// M6b 提案卡行为测试（拆解设计 §8.1）：摘要区（模式/节点数/依赖缩略/指纹短码）/生命周期态
// 徽标全态配色/failed 态错误清单入口/「在画布中审阅」入口/proposal.updated 驱动刷新。
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return { ...actual, api: { ...actual.api, proposal: vi.fn() } };
});

import type { ProposalPublic, ProposalStatus } from '@coagentia/contracts-ts';

import { api } from '../api';
import { qk } from '../lib/queryKeys';
import { PROPOSAL_STATUS_WORD } from '../lib/uiMaps';
import { applyEnvelope } from '../data/wsBridge';
import { ProposalCard } from './ProposalCard';

const PROPOSAL: ProposalPublic = {
  id: 'prop_1', workspace_id: 'ws_1', channel_id: 'ch_1', source_task_id: 'task_1',
  kind: 'full', revision: 2, status: 'awaiting_confirm',
  body: {
    version: 'coagentia.decomposition.v1', mode: 'decompose', summary: '实现与评审两线并行',
    nodes: [{ temp_id: 'a' }, { temp_id: 'b' }, { temp_id: 'c' }],
    edges: [{ from: 'a', to: 'c' }, { from: 'b', to: 'c' }],
  },
  proposal_hash: 'abcdef0123456789'.repeat(4), proposed_by_member_id: 'mem_orch',
  created_at: '2026-07-12T00:00:00Z', updated_at: '2026-07-12T00:00:00Z',
};

// ProposalStatus 全态（契约 ProposalStatus 9 值 = 拆解设计 §3 状态机全态）。
const ALL_STATUSES: ProposalStatus[] = [
  'drafting', 'validating', 'repairing', 'awaiting_confirm', 'landing',
  'landed', 'superseded', 'rejected', 'failed',
];

function renderCard(
  proposal: ProposalPublic,
  handlers?: { onReviewInCanvas?: () => void; onViewThread?: () => void },
) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <ProposalCard proposalId={proposal.id} {...handlers} />
    </QueryClientProvider>,
  );
  return qc;
}

describe('ProposalCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.proposal).mockResolvedValue(PROPOSAL);
  });

  it('渲染摘要区：模式/节点数/依赖缩略/指纹短码（前 6 位）/rev 徽标', async () => {
    renderCard(PROPOSAL, { onReviewInCanvas: () => {} });
    expect(await screen.findByTestId('proposal-mode')).toHaveTextContent('拆解');
    expect(screen.getByText('3 节点')).toBeInTheDocument();
    expect(screen.getByText('依赖 2')).toBeInTheDocument();
    expect(screen.getByText('#abcdef')).toBeInTheDocument(); // proposal_hash 前 6 位
    expect(screen.getByText('rev 2')).toBeInTheDocument();
    expect(screen.getByText('实现与评审两线并行')).toBeInTheDocument();
  });

  it.each(ALL_STATUSES)('生命周期态徽标 %s：文案与 data-status 全态覆盖', async (status) => {
    vi.mocked(api.proposal).mockResolvedValue({ ...PROPOSAL, status });
    renderCard({ ...PROPOSAL, status });
    const badge = await screen.findByTestId('proposal-status');
    expect(badge).toHaveTextContent(PROPOSAL_STATUS_WORD[status]!);
    expect(screen.getByTestId('proposal-card')).toHaveAttribute('data-status', status);
  });

  it('failed 态：显示错误清单引导 + 「查看线程」入口；隐藏「在画布中审阅」', async () => {
    vi.mocked(api.proposal).mockResolvedValue({ ...PROPOSAL, status: 'failed' });
    const onViewThread = vi.fn();
    renderCard({ ...PROPOSAL, status: 'failed' }, { onReviewInCanvas: () => {}, onViewThread });
    expect(await screen.findByRole('alert')).toHaveTextContent('错误清单见 source 线程');
    fireEvent.click(screen.getByTestId('proposal-view-thread'));
    expect(onViewThread).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId('proposal-review')).not.toBeInTheDocument();
  });

  it('非终态显示「在画布中审阅」入口（草稿层归后半，先挂跳画布）', async () => {
    const onReview = vi.fn();
    renderCard(PROPOSAL, { onReviewInCanvas: onReview });
    fireEvent.click(await screen.findByTestId('proposal-review'));
    expect(onReview).toHaveBeenCalledTimes(1);
  });

  it('proposal.updated WS 信封驱动卡片态实时刷新（wsBridge patch 同一 query）', async () => {
    const qc = renderCard(PROPOSAL);
    await screen.findByTestId('proposal-status');
    applyEnvelope(qc, {
      type: 'proposal.updated', workspace_id: 'ws_1', channel_id: 'ch_1', seq: 9,
      key: 'proposal:prop_1', at: '2026-07-12T00:00:02Z',
      data: { proposal: { ...PROPOSAL, status: 'landed' } },
    } as never);
    expect(await screen.findByText(PROPOSAL_STATUS_WORD['landed']!)).toBeInTheDocument();
    expect(screen.getByTestId('proposal-card')).toHaveAttribute('data-status', 'landed');
  });

  // ---- B-M6-2：入口按 kind/status 分派（full 待确认「查看草稿」/ delta 待确认「审查增量」）
  it('full 待确认：审阅按钮文案 = 「查看草稿」，点击带 proposalId 回调', async () => {
    const onReview = vi.fn();
    renderCard(PROPOSAL, { onReviewInCanvas: onReview });
    const btn = await screen.findByTestId('proposal-review');
    expect(btn).toHaveTextContent('查看草稿');
    fireEvent.click(btn);
    expect(onReview).toHaveBeenCalledWith('prop_1');
  });

  it('delta 待确认：显示「审查增量」（proposal-review-delta），点击调 onReviewDelta', async () => {
    const delta = { ...PROPOSAL, kind: 'delta' as const };
    vi.mocked(api.proposal).mockResolvedValue(delta);
    const onDelta = vi.fn();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <ProposalCard proposalId={delta.id} onReviewDelta={onDelta} onReviewInCanvas={() => {}} />
      </QueryClientProvider>,
    );
    const btn = await screen.findByTestId('proposal-review-delta');
    fireEvent.click(btn);
    expect(onDelta).toHaveBeenCalledWith('prop_1');
    expect(screen.queryByTestId('proposal-review')).not.toBeInTheDocument();
  });
});
