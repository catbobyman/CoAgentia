// B-M8-3 成员页「创建 Agent」入口：ms-head 右侧按钮 → 打开 CreateAgentModal（role=dialog）。
// react-router 的 useNavigate 与 lib/store 的 useUiStore 用 vi.hoisted spy 替换（本屏独立于主壳，
// 无真 RouterProvider/无需真 zustand）；'../api' 按 SetupChecklistScreen.test 范式部分替换。
// 运行:pnpm -F @coagentia/web test
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const { navigateMock, setActiveChannelMock } = vi.hoisted(() => ({
  navigateMock: vi.fn(),
  setActiveChannelMock: vi.fn(),
}));

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigateMock,
}));

vi.mock('../lib/store', () => ({
  useUiStore: (selector: (s: { setActiveChannel: typeof setActiveChannelMock }) => unknown) =>
    selector({ setActiveChannel: setActiveChannelMock }),
}));

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    api: {
      ...actual.api,
      members: vi.fn(), presence: vi.fn(), computers: vi.fn(), agent: vi.fn(), createAgent: vi.fn(),
    },
  };
});

import type { ComputerPublic, MemberPublic } from '@coagentia/contracts-ts';

import { api } from '../api';
import { ToastProvider } from '../components/Toast';
import { MembersScreen } from './MembersScreen';

const OWNER: MemberPublic = {
  id: 'mem_owner', name: 'Owner', kind: 'human', role: 'owner',
  workspace_id: 'ws_1', created_at: '2026-07-14T00:00:00Z',
};
const COMPUTER: ComputerPublic = {
  id: 'computer_1', workspace_id: 'ws_1', name: '本机', created_at: '2026-07-14T00:00:00Z',
};

function renderScreen() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <MembersScreen />
      </ToastProvider>
    </QueryClientProvider>,
  );
  return qc;
}

describe('MembersScreen 创建 Agent 入口', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.members).mockResolvedValue([OWNER]);
    vi.mocked(api.presence).mockResolvedValue({ items: [] });
    vi.mocked(api.computers).mockResolvedValue([COMPUTER]);
  });

  it('ms-head 有「创建 Agent」按钮，点击后打开 CreateAgentModal', async () => {
    renderScreen();
    const btn = await screen.findByRole('button', { name: '创建 Agent' });
    expect(btn).toBeInTheDocument();

    expect(screen.queryByRole('dialog', { name: '创建 Agent' })).not.toBeInTheDocument();
    fireEvent.click(btn);
    expect(await screen.findByRole('dialog', { name: '创建 Agent' })).toBeInTheDocument();
    // 弹窗字段就位（名字输入）。
    expect(screen.getByLabelText('名字')).toBeInTheDocument();
  });
});
