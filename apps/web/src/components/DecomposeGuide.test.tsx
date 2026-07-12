// M6b 拆解入口（交互 §6.8 / 拆解设计 §4 T3）：请求形状 {text}、NO_ORCHESTRATOR → 引导弹窗 →
// 创建弹窗预选+预填 → 创建后拉入频道并回聚焦、503 离线文案与去 P7 引导。T2 {task_id} 形状归
// ThreadPanel.decompose.test。router 依赖用 vi.mock 替换（同 SetupChecklistScreen 体例）。
import { useState } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const navigateMock = vi.hoisted(() => vi.fn());
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigateMock,
}));

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    api: {
      ...actual.api,
      decompose: vi.fn(), computers: vi.fn(), createAgent: vi.fn(), addChannelMember: vi.fn(),
    },
  };
});

import type { AgentPublic, ComputerPublic, ProposalPublic } from '@coagentia/contracts-ts';
import {
  ORCHESTRATOR_ROLE_TEMPLATE_DESCRIPTION_PREFILL,
  ORCHESTRATOR_ROLE_TEMPLATE_KEY,
} from '@coagentia/contracts-ts';

import { api, ApiError } from '../api';
import { ToastProvider, Toaster } from './Toast';
import { DecomposeGuideModals, DecomposeTextModal, useDecompose } from './DecomposeGuide';

const COMPUTER: ComputerPublic = {
  id: 'computer_1', workspace_id: 'ws_1', name: '本机', created_at: '2026-07-12T00:00:00Z',
};
const AGENT: AgentPublic = {
  member_id: 'mem_orch', computer_id: COMPUTER.id, runtime: 'claude_code', model: 'sonnet',
  home_path: '~/.coagentia/agents/mem_orch', created_by_member_id: 'mem_owner',
  role_template_key: ORCHESTRATOR_ROLE_TEMPLATE_KEY,
};
const PROPOSAL: ProposalPublic = {
  id: 'prop_1', workspace_id: 'ws_1', channel_id: 'ch_1', source_task_id: 'task_src',
  kind: 'full', revision: 1, status: 'drafting', body: {},
  proposal_hash: 'f'.repeat(64), proposed_by_member_id: 'mem_orch',
  created_at: '2026-07-12T00:00:00Z', updated_at: '2026-07-12T00:00:00Z',
};

/** 画布 T3 入口的最小 harness：文本弹窗常开（409/503 时引导层压其上,文本不丢——CanvasTab 同构）。 */
function Harness({ onProposal, onRefocus }: {
  onProposal: (p: ProposalPublic) => void;
  onRefocus: () => void;
}) {
  const d = useDecompose('ch_1', onProposal);
  const [open, setOpen] = useState(true);
  return (
    <>
      {open && (
        <DecomposeTextModal
          busy={d.busy}
          onClose={() => setOpen(false)}
          onSubmit={(text) => { void d.request({ text }); }}
        />
      )}
      <DecomposeGuideModals
        guide={d.guide}
        channelId="ch_1"
        onClose={() => d.setGuide(null)}
        onOrchestratorCreated={onRefocus}
      />
    </>
  );
}

function renderHarness(onProposal = vi.fn(), onRefocus = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <Harness onProposal={onProposal} onRefocus={onRefocus} />
        <Toaster />
      </ToastProvider>
    </QueryClientProvider>,
  );
  return { onProposal, onRefocus };
}

function submitText(text: string) {
  fireEvent.change(screen.getByLabelText('需求描述'), { target: { value: text } });
  fireEvent.click(screen.getByTestId('decompose-submit'));
}

describe('拆解入口 T3（画布工具栏路径）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.computers).mockResolvedValue([COMPUTER]);
    vi.mocked(api.createAgent).mockResolvedValue(AGENT);
    vi.mocked(api.addChannelMember).mockResolvedValue(undefined);
  });

  it('提交需求文本 → POST {text} 形状；202 → onProposal 收 ProposalPublic', async () => {
    vi.mocked(api.decompose).mockResolvedValue(PROPOSAL);
    const { onProposal } = renderHarness();
    submitText('做一个番茄钟');
    await waitFor(() => expect(api.decompose).toHaveBeenCalledWith('ch_1', { text: '做一个番茄钟' }));
    expect(onProposal).toHaveBeenCalledWith(PROPOSAL);
  });

  it('409 NO_ORCHESTRATOR → 引导弹窗「本频道还没有协调 Agent」+ [创建 Orchestrator] + [取消]', async () => {
    vi.mocked(api.decompose).mockRejectedValue(
      new ApiError(409, 'NO_ORCHESTRATOR', '本频道还没有协调 Agent'),
    );
    renderHarness();
    submitText('做一个番茄钟');
    expect(await screen.findByText('本频道还没有协调 Agent')).toBeInTheDocument();
    const guideDialog = screen.getByRole('dialog', { name: '需要协调 Agent' });
    expect(within(guideDialog).getByTestId('create-orchestrator')).toBeInTheDocument();
    expect(within(guideDialog).getByRole('button', { name: '取消' })).toBeInTheDocument();
    // 文本弹窗仍在（引导层压其上）——引导链走完回画布即重新聚焦拆解入口,已输入文本不丢。
    expect(screen.getByLabelText('需求描述')).toHaveValue('做一个番茄钟');
  });

  it('引导链：创建弹窗预选 Orchestrator 模板+预填话术 → 创建后拉入频道 → 回聚焦', async () => {
    vi.mocked(api.decompose).mockRejectedValue(
      new ApiError(409, 'NO_ORCHESTRATOR', '本频道还没有协调 Agent'),
    );
    const { onRefocus } = renderHarness();
    submitText('做一个番茄钟');
    fireEvent.click(await screen.findByTestId('create-orchestrator'));

    // 创建 Agent 弹窗：角色模板预选 + description 预填（数据源 = 生成三常量）。
    expect(await screen.findByTestId('role-template-select')).toHaveValue(
      ORCHESTRATOR_ROLE_TEMPLATE_KEY,
    );
    expect(screen.getByLabelText('成员说明（description）')).toHaveValue(
      ORCHESTRATOR_ROLE_TEMPLATE_DESCRIPTION_PREFILL,
    );

    fireEvent.change(screen.getByLabelText('模型'), { target: { value: 'sonnet' } });
    fireEvent.click(screen.getByRole('button', { name: '创建 Agent' }));

    await waitFor(() => expect(api.createAgent).toHaveBeenCalledWith(
      expect.objectContaining({ role_template_key: ORCHESTRATOR_ROLE_TEMPLATE_KEY }),
    ));
    // find_orchestrator 按频道成员判定 → 创建后必须拉入本频道，然后回聚焦拆解入口。
    await waitFor(() => expect(api.addChannelMember).toHaveBeenCalledWith('ch_1', AGENT.member_id));
    await waitFor(() => expect(onRefocus).toHaveBeenCalledTimes(1));
  });

  it('503 DAEMON_OFFLINE → 「@Orchestrator 当前离线（机器断连）」+ 去机器页引导（P7）', async () => {
    vi.mocked(api.decompose).mockRejectedValue(
      new ApiError(503, 'DAEMON_OFFLINE', 'daemon 离线'),
    );
    renderHarness();
    submitText('做一个番茄钟');
    expect(await screen.findByText('@Orchestrator 当前离线（机器断连）')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('goto-computers'));
    expect(navigateMock).toHaveBeenCalledWith({ to: '/computers' });
  });

  it('其余结构化错误走 toast，不弹引导', async () => {
    vi.mocked(api.decompose).mockRejectedValue(
      new ApiError(404, 'NOT_FOUND', '频道不存在'),
    );
    renderHarness();
    submitText('做一个番茄钟');
    expect(await screen.findByText('频道不存在')).toBeInTheDocument();
    expect(screen.queryByText('本频道还没有协调 Agent')).not.toBeInTheDocument();
  });
});
