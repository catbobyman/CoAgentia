// M6b delta 面板行为测试：ops 渲染 / 逐项剔除重验（NODE_ACTIVE、全剔除）/ base 横幅 / confirm removed_ops
// / DELTA_BASE_MISMATCH。照 DraftLayer.test 的 QueryClient seed + vi.mock('../api') 范式。
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

import type {
  CanvasEdgePublic, CanvasNodePublic, CanvasPublic, ChannelsSnapshot, ProposalPublic, TaskPublic,
} from '@coagentia/contracts-ts';

import { api, ApiError } from '../api';
import { qk } from '../lib/queryKeys';
import { ToastProvider, Toaster } from './Toast';
import { DeltaPanel } from './DeltaPanel';

const CANVAS: CanvasPublic = {
  id: 'canvas_1', channel_id: 'ch_1', workspace_id: 'ws_1',
  baseline_version: 5, baseline_hash: 'basehash', updated_at: 't',
};
const NODES = [
  { id: 'n1', canvas_id: 'canvas_1', created_at: 't', kind: 'agent', task_id: 't1' },
  { id: 'n2', canvas_id: 'canvas_1', created_at: 't', kind: 'agent', task_id: 't2' },
  { id: 'n3', canvas_id: 'canvas_1', created_at: 't', kind: 'agent', task_id: 't3' },
] as unknown as CanvasNodePublic[];
const EDGES = [
  { id: 'e1', canvas_id: 'canvas_1', from_node_id: 'n1', to_node_id: 'n2' },
  { id: 'e2', canvas_id: 'canvas_1', from_node_id: 'n2', to_node_id: 'n3' },
] as unknown as CanvasEdgePublic[];
const task = (id: string, status: string, title: string): TaskPublic =>
  ({ id, status, title, channel_id: 'ch_1', workspace_id: 'ws_1', number: 1, created_at: 't', root_message_id: 'm', created_by_member_id: 'x' }) as unknown as TaskPublic;

const OPS = [
  { op: 'add_node', node: { temp_id: 'n5', title: '自动化单测' } },
  { op: 'add_edge', from: 'n3', to: 'n5' },
  { op: 'remove_node', temp_id: 'n2' },
];
const DELTA: ProposalPublic = {
  id: 'prop_d', workspace_id: 'ws_1', channel_id: 'ch_1', source_task_id: 'task_1',
  kind: 'delta', revision: 1, status: 'awaiting_confirm', base_hash: 'basehash',
  body: { version: 'coagentia.decomposition-delta.v1', base: 'basehash', operations: OPS },
  proposal_hash: 'abcdef0123456789'.repeat(4), proposed_by_member_id: 'mem_orch',
  created_at: 't', updated_at: 't',
};
const SNAP = { items: [{ id: 'ch_1', kind: 'channel', name: 'build', workspace_id: 'ws_1', created_at: 't', decomp_node_limit: 12 }], read_positions: [] } as unknown as ChannelsSnapshot;

function renderPanel(opts: { proposal?: Partial<ProposalPublic>; tasks?: TaskPublic[] } = {}) {
  const proposal = { ...DELTA, ...opts.proposal };
  const tasks = opts.tasks ?? [task('t1', 'done', '需求'), task('t2', 'done', '实现'), task('t3', 'done', '评审')];
  vi.mocked(api.proposal).mockResolvedValue(proposal);
  vi.mocked(api.channels).mockResolvedValue(SNAP);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  qc.setQueryData(qk.proposal(proposal.id), proposal);
  qc.setQueryData(qk.channels(), SNAP);
  const onClose = vi.fn();
  render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <DeltaPanel channelId="ch_1" proposalId={proposal.id} canvas={CANVAS} nodes={NODES} edges={EDGES} tasks={tasks} members={[]} onClose={onClose} />
        <Toaster />
      </ToastProvider>
    </QueryClientProvider>,
  );
  return { qc, onClose };
}

describe('DeltaPanel', () => {
  beforeEach(() => vi.clearAllMocks());

  it('渲染 ops 列表 + 增删计数 + base 短码', async () => {
    renderPanel();
    await screen.findByTestId('delta-panel');
    expect(screen.getAllByTestId('delta-op')).toHaveLength(3);
    expect(screen.getByText('+2 新增')).toBeInTheDocument();
    expect(screen.getByText('−1 删除')).toBeInTheDocument();
    // remove_node 目标 title 从画布反查（n2 = 实现）
    expect(screen.getByText('实现')).toBeInTheDocument();
    expect(screen.getByTestId('delta-confirm')).not.toBeDisabled();
  });

  it('NODE_ACTIVE：删除 in_progress 节点 → 重验错误 + 确认 disabled；剔除后恢复', async () => {
    renderPanel({ tasks: [task('t1', 'done', '需求'), task('t2', 'in_progress', '实现'), task('t3', 'done', '评审')] });
    await screen.findByTestId('delta-panel');
    expect(screen.getByTestId('delta-reval-errors')).toHaveTextContent('进行中');
    expect(screen.getByTestId('delta-confirm')).toBeDisabled();
    // 剔除 remove_node 那条（第三个 checkbox）→ 错误消失
    fireEvent.click(screen.getByRole('checkbox', { name: '剔除操作 remove_node' }));
    await waitFor(() => expect(screen.queryByTestId('delta-reval-errors')).not.toBeInTheDocument());
    expect(screen.getByTestId('delta-confirm')).not.toBeDisabled();
  });

  it('NODE_ACTIVE：删除 running 系统节点同判（并行审计镜像修复——服务端 _node_is_active 口径）', async () => {
    // 系统节点状态不进结构基线（base 横幅拦不住），漏判会让按钮可点而服务端 422 突袭。
    const nodesWithSystem = [
      ...NODES,
      { id: 'sys1', canvas_id: 'canvas_1', created_at: 't', kind: 'system', system_action: 'merge', system_status: 'running' },
    ] as unknown as CanvasNodePublic[];
    const proposal = {
      ...DELTA,
      body: { version: 'coagentia.decomposition-delta.v1', base: 'basehash', operations: [{ op: 'remove_node', node_id: 'sys1' }] },
    };
    vi.mocked(api.proposal).mockResolvedValue(proposal as ProposalPublic);
    vi.mocked(api.channels).mockResolvedValue(SNAP);
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    qc.setQueryData(qk.proposal(proposal.id), proposal);
    qc.setQueryData(qk.channels(), SNAP);
    render(
      <QueryClientProvider client={qc}>
        <ToastProvider>
          <DeltaPanel channelId="ch_1" proposalId={proposal.id} canvas={CANVAS} nodes={nodesWithSystem} edges={EDGES} tasks={[task('t1', 'done', '需求'), task('t2', 'done', '实现'), task('t3', 'done', '评审')]} members={[]} onClose={vi.fn()} />
          <Toaster />
        </ToastProvider>
      </QueryClientProvider>,
    );
    await screen.findByTestId('delta-panel');
    expect(screen.getByTestId('delta-reval-errors')).toHaveTextContent('进行中');
    expect(screen.getByTestId('delta-confirm')).toBeDisabled();
  });

  it('全部剔除 → 提示改用拒绝 + 确认 disabled', async () => {
    renderPanel();
    await screen.findByTestId('delta-panel');
    for (const cb of screen.getAllByRole('checkbox')) fireEvent.click(cb);
    await waitFor(() => expect(screen.getByTestId('delta-all-removed')).toBeInTheDocument());
    expect(screen.getByTestId('delta-confirm')).toBeDisabled();
  });

  it('base 过期横幅：proposal.base_hash ≠ 画布基线 → 横幅 + 确认 disabled', async () => {
    renderPanel({ proposal: { base_hash: 'stalehash' } });
    await screen.findByTestId('delta-panel');
    expect(screen.getByTestId('delta-base-banner')).toBeInTheDocument();
    expect(screen.getByTestId('delta-confirm')).toBeDisabled();
  });

  it('确认落地：removed_ops = 剔除的原始下标（升序）+ adjustments:[] + expected 三字段', async () => {
    vi.mocked(api.confirmProposal).mockResolvedValue({ batch: {} as never, proposal: { ...DELTA, status: 'landing' } });
    const { onClose } = renderPanel();
    await screen.findByTestId('delta-panel');
    // 剔除第 2 个 op（add_edge，index 1）
    fireEvent.click(screen.getByRole('checkbox', { name: '剔除操作 add_edge' }));
    fireEvent.click(screen.getByTestId('delta-confirm'));
    await waitFor(() => expect(api.confirmProposal).toHaveBeenCalledTimes(1));
    const [pid, body] = vi.mocked(api.confirmProposal).mock.calls[0]!;
    expect(pid).toBe('prop_d');
    expect(body.expected).toEqual({ proposal_hash: DELTA.proposal_hash, baseline_version: 5, baseline_hash: 'basehash' });
    expect(body.adjustments).toEqual([]);
    expect(body.removed_ops).toEqual([1]);
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('409 DELTA_BASE_MISMATCH → 横幅 + 不关层', async () => {
    vi.mocked(api.confirmProposal).mockRejectedValue(new ApiError(409, 'DELTA_BASE_MISMATCH', '基线变了'));
    const { onClose } = renderPanel();
    await screen.findByTestId('delta-panel');
    fireEvent.click(screen.getByTestId('delta-confirm'));
    await waitFor(() => expect(screen.getByTestId('delta-base-banner')).toBeInTheDocument());
    expect(onClose).not.toHaveBeenCalled();
  });
});
