// P0c 起步清单(首跑态)覆盖(测试缺口 A):003「打开模板向导」在依赖达成时可点 → 开真实
// TemplateWizard;向导 onInstantiated(channelId) 冒泡到本屏 → 关窗 + setActiveChannel + 导航画布。
// 照 TemplateWizard.test.tsx 的 QueryClient seed + vi.mock('../api') 范式；'@tanstack/react-router'
// 的 useNavigate/useRouterState 与 '../lib/store' 的 useUiStore 用 vi.hoisted 的 spy 全量替换
// (SetupChecklistScreen 独立于主壳渲染，无真 RouterProvider/无需真 zustand 状态)。
// 运行:pnpm -F @coagentia/web test
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const { navigateMock, setActiveChannelMock } = vi.hoisted(() => ({
  navigateMock: vi.fn(),
  setActiveChannelMock: vi.fn(),
}));

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigateMock,
  // Rail 用它高亮当前 rail item(pathname 前缀匹配)，本屏测试无关，给个稳定假值即可。
  useRouterState: () => '/setup',
}));

vi.mock('../lib/store', () => ({
  useUiStore: (selector: (s: { setActiveChannel: typeof setActiveChannelMock; setSearchOpen: () => void }) => unknown) =>
    selector({ setActiveChannel: setActiveChannelMock, setSearchOpen: () => {} }),
}));

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    api: {
      ...actual.api,
      templates: vi.fn(), instantiateTemplate: vi.fn(), agent: vi.fn(),
      computers: vi.fn(), createAgent: vi.fn(),
    },
  };
});

import type {
  AgentPublic, ChannelPublic, ChannelsSnapshot, ComputerPublic, MemberPublic, TemplatePublic,
  WorkspacePublic,
} from '@coagentia/contracts-ts';

import { api } from '../api';
import { qk } from '../lib/queryKeys';
import { SetupChecklistScreen } from './SetupChecklistScreen';

const WS = 'ws_1';

function workspaceOf(setupState: Record<string, boolean>): WorkspacePublic {
  return {
    id: WS, name: 'WS', slug: 'ws', created_at: '2026-07-11T00:00:00Z',
    setup_state: setupState,
  };
}

function allChannel(): ChannelPublic {
  return { id: 'ch_all', kind: 'channel', name: 'all', workspace_id: WS, created_at: '2026-07-11T00:00:00Z' };
}

function channelsSnap(items: ChannelPublic[]): ChannelsSnapshot {
  return { items, read_positions: [] };
}

function member(id: string, name: string): MemberPublic {
  return { id, name, kind: 'agent', workspace_id: WS, created_at: '2026-07-11T00:00:00Z' };
}

function agentOf(id: string): AgentPublic {
  return { member_id: id, computer_id: 'c', created_by_member_id: 'm', home_path: '/h', model: 'x', runtime: 'claude_code' };
}

function computerOf(): ComputerPublic {
  return { id: 'computer_1', workspace_id: WS, name: '本机', created_at: '2026-07-11T00:00:00Z' };
}

// 单角色模板(简化驱动到实例化提交，聚焦本屏 onInstantiated 冒泡而非向导内部映射逻辑——
// 向导内部行为已在 TemplateWizard.test.tsx 全覆盖)。
function soloTemplate(): TemplatePublic {
  return {
    id: 'tpl_solo', workspace_id: WS, name: '单角色模板', builtin: true,
    created_by_member_id: 'm', created_at: '2026-07-11T00:00:00Z',
    body: {
      nodes: [{ key: 'n1', title: '唯一节点', role: '角色A', plan_skeleton: null }],
      edges: [],
      roles: [{ placeholder: '角色A' }],
    },
  };
}

function renderScreen(setupState: Record<string, boolean>, members: MemberPublic[] = [member('a', 'Alice')]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  qc.setQueryData<WorkspacePublic>(qk.workspace(), workspaceOf(setupState));
  qc.setQueryData<ChannelsSnapshot>(qk.channels(), channelsSnap([allChannel()]));
  qc.setQueryData<MemberPublic[]>(qk.members(), members);
  members.forEach((m) => qc.setQueryData<AgentPublic>(qk.agent(m.id), agentOf(m.id)));
  render(
    <QueryClientProvider client={qc}>
      <SetupChecklistScreen />
    </QueryClientProvider>,
  );
  return { qc };
}

describe('SetupChecklistScreen 003 打开模板向导', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.templates).mockResolvedValue([soloTemplate()]);
    vi.mocked(api.agent).mockImplementation((id: string) => Promise.resolve(agentOf(id)));
  });

  it('依赖(002)达成、003 未完成 → 按钮 actionable，点击后打开真实 TemplateWizard', async () => {
    renderScreen({ add_computer: true, create_agent: true, first_task: false });
    const btn = screen.getByRole('button', { name: '打开模板向导' });
    expect(btn).not.toBeDisabled();

    expect(screen.queryByTestId('template-wizard')).not.toBeInTheDocument();
    fireEvent.click(btn);
    expect(await screen.findByTestId('template-wizard')).toBeInTheDocument();
  });

  it('依赖(002)未达成 → 按钮 disabled，不开向导', () => {
    renderScreen({ add_computer: true, create_agent: false, first_task: false });
    const btn = screen.getByRole('button', { name: '打开模板向导' });
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(screen.queryByTestId('template-wizard')).not.toBeInTheDocument();
  });

  it('向导走完实例化 → onInstantiated 冒泡:关窗 + setActiveChannel(目标频道) + 导航到画布', async () => {
    vi.mocked(api.instantiateTemplate).mockResolvedValue({
      batch: {
        id: 'b', workspace_id: WS, channel_id: 'ch_all', kind: 'tmpl', content_hash: 'h',
        source_ref: 'tpl_solo', confirmed_by: 'm', status: 'done',
        created_at: '2026-07-11T00:00:00Z', done_at: '2026-07-11T00:00:00Z',
      },
      tasks: [],
    });
    renderScreen({ add_computer: true, create_agent: true, first_task: false });
    fireEvent.click(screen.getByRole('button', { name: '打开模板向导' }));
    await screen.findByTestId('template-wizard');

    // 步①选模板 → 步②映射唯一占位 → 步③实例化。
    const card = await screen.findByTestId('template-card');
    fireEvent.click(card);
    fireEvent.click(screen.getByRole('button', { name: /下一步/ }));
    await screen.findByTestId('wizard-step-2');
    fireEvent.change(screen.getByLabelText('映射 角色A'), { target: { value: 'a' } });
    fireEvent.click(screen.getByRole('button', { name: /下一步/ }));
    await screen.findByTestId('wizard-step-3');
    fireEvent.click(screen.getByTestId('instantiate-submit'));

    await waitFor(() => expect(screen.queryByTestId('template-wizard')).not.toBeInTheDocument());
    expect(setActiveChannelMock).toHaveBeenCalledWith('ch_all');
    expect(navigateMock).toHaveBeenCalledWith({ to: '/', search: { tab: 'canvas' } });
  });
});

// B-M8-3 步骤 002「创建第一个 Agent」死壳补齐：依赖(001)达成、002 未完成 → 按钮 actionable，
// 点击打开 CreateAgentModal。
describe('SetupChecklistScreen 002 创建 Agent', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.templates).mockResolvedValue([soloTemplate()]);
    vi.mocked(api.agent).mockImplementation((id: string) => Promise.resolve(agentOf(id)));
    vi.mocked(api.computers).mockResolvedValue([computerOf()]);
  });

  it('依赖(001)达成、002 未完成 → 按钮 actionable，点击打开 CreateAgentModal', async () => {
    renderScreen({ add_computer: true, create_agent: false, first_task: false });
    const btn = screen.getByRole('button', { name: '创建 Agent' });
    expect(btn).not.toBeDisabled();

    expect(screen.queryByRole('dialog', { name: '创建 Agent' })).not.toBeInTheDocument();
    fireEvent.click(btn);
    expect(await screen.findByRole('dialog', { name: '创建 Agent' })).toBeInTheDocument();
  });

  it('依赖(001)未达成 → 002 按钮 disabled，不开弹窗', () => {
    renderScreen({ add_computer: false, create_agent: false, first_task: false });
    const btn = screen.getByRole('button', { name: '创建 Agent' });
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(screen.queryByRole('dialog', { name: '创建 Agent' })).not.toBeInTheDocument();
  });
});
