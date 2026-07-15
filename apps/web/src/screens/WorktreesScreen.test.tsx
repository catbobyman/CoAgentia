// PS-WT ② 工作树管理台：按项目分组 / 派生态徽标（活跃·已合并·孤儿·丢失）/ 清理按钮资格
// （merged/conflicted + 孤儿；active 永不给）/ 清理确认弹窗明列目录绝对路径 + 分支名 / 登记树走
// cleanupWorktree、孤儿走 cleanupOrphan / 该机离线 → 清理禁用 / 任务链接跳频道。
// 照 MembersScreen.test：router useNavigate + lib/store useUiStore 用 vi.hoisted，'../api' 部分替换。
// 运行:pnpm -F @coagentia/web test
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
      worktreesConsole: vi.fn(), computers: vi.fn(),
      cleanupWorktree: vi.fn(), cleanupOrphan: vi.fn(),
    },
  };
});

import type { ComputerPublic, WorktreeConsoleItem, WorktreeConsoleReply } from '@coagentia/contracts-ts';

import { api } from '../api';
import { ToastProvider } from '../components/Toast';
import { WorktreesScreen } from './WorktreesScreen';

const C1: ComputerPublic = { id: 'c1', workspace_id: 'ws_1', name: '本机', created_at: '2026-07-14T00:00:00Z' };
const C2: ComputerPublic = { id: 'c2', workspace_id: 'ws_1', name: '远程', created_at: '2026-07-14T00:00:00Z' };

const ITEMS: WorktreeConsoleItem[] = [
  { id: 'wt_a', project_id: 'p1', project_name: 'Alpha', computer_id: 'c1', task_id: 't_a', task_title: '活跃任务', channel_id: 'ch1', branch: 'coagentia/a', path: 'D:/wt/p1/t_a', status: 'active', derived: 'ok', live: { dirty: true, behind: 2, ahead: 0, head_commit: 'abc' } },
  { id: 'wt_m', project_id: 'p1', project_name: 'Alpha', computer_id: 'c1', task_id: 't_m', task_title: '已合并任务', channel_id: 'ch1', branch: 'coagentia/m', path: 'D:/wt/p1/t_m', status: 'merged', derived: 'ok', live: { dirty: false, behind: 0, ahead: 0 } },
  { id: null, project_id: 'p1', project_name: 'Alpha', computer_id: 'c1', task_id: 't_orphan', task_title: null, channel_id: null, branch: 'coagentia/orphan', path: 'D:/wt/p1/t_orphan', status: null, derived: 'orphan', live: { dirty: false, behind: 0, ahead: 0 } },
  { id: 'wt_off', project_id: 'p2', project_name: 'Beta', computer_id: 'c2', task_id: 't_off', task_title: '离线合并', channel_id: 'ch2', branch: 'coagentia/off', path: 'D:/wt/p2/t_off', status: 'merged', derived: 'ok', live: null },
  { id: 'wt_miss', project_id: 'p2', project_name: 'Beta', computer_id: 'c2', task_id: 't_miss', task_title: '丢失任务', channel_id: 'ch2', branch: 'coagentia/miss', path: 'D:/wt/p2/t_miss', status: 'active', derived: 'missing', live: null },
];
const REPLY: WorktreeConsoleReply = {
  items: ITEMS,
  scans: [{ computer_id: 'c1', status: 'ok' }, { computer_id: 'c2', status: 'offline' }],
};

function renderScreen() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={qc}>
      <ToastProvider><WorktreesScreen /></ToastProvider>
    </QueryClientProvider>,
  );
  return qc;
}

describe('WorktreesScreen', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.worktreesConsole).mockResolvedValue(REPLY);
    vi.mocked(api.computers).mockResolvedValue([C1, C2]);
    vi.mocked(api.cleanupWorktree).mockResolvedValue({
      id: 'wt_m', workspace_id: 'ws_1', project_id: 'p1', task_id: 't_m',
      branch: 'coagentia/m', path: 'D:/wt/p1/t_m', status: 'cleaned', created_at: '2026-07-14T00:00:00Z',
    });
    vi.mocked(api.cleanupOrphan).mockResolvedValue({ project_id: 'p1', task_id: 't_orphan', removed: true });
  });

  it('按项目分组，派生态徽标（活跃/已合并/孤儿/丢失）', async () => {
    renderScreen();
    expect(await screen.findByText('Alpha')).toBeInTheDocument();
    expect(screen.getByText('Beta')).toBeInTheDocument();
    expect(screen.getByText('活跃')).toBeInTheDocument();
    // merged 徽标出现在 Alpha(wt_m) 与 Beta(wt_off) 两行。
    expect(screen.getAllByText('已合并')).toHaveLength(2);
    expect(screen.getByText('孤儿')).toBeInTheDocument();
    expect(screen.getByText('丢失')).toBeInTheDocument();
  });

  it('active 行无清理按钮；merged 行有', async () => {
    renderScreen();
    await screen.findByText('Alpha');
    expect(screen.queryByRole('button', { name: '清理 coagentia/a' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '清理 coagentia/m' })).toBeInTheDocument();
  });

  it('清理 merged 行 → 确认弹窗明列目录绝对路径 + 分支；确认走 cleanupWorktree', async () => {
    renderScreen();
    await screen.findByText('Alpha');
    fireEvent.click(screen.getByRole('button', { name: '清理 coagentia/m' }));
    // 确认弹窗含绝对路径与分支名（分支同名也出现在行内，故 scope 到 dialog 内断言）。
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('D:/wt/p1/t_m')).toBeInTheDocument();
    expect(within(dialog).getByText('coagentia/m')).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: '清理' }));
    await waitFor(() => expect(api.cleanupWorktree).toHaveBeenCalledWith('wt_m'));
  });

  it('清理孤儿行 → 走 cleanupOrphan（ids-only）', async () => {
    renderScreen();
    await screen.findByText('Alpha');
    fireEvent.click(screen.getByRole('button', { name: '清理 coagentia/orphan' }));
    fireEvent.click(await screen.findByRole('button', { name: '清理' }));
    await waitFor(() => expect(api.cleanupOrphan).toHaveBeenCalledWith('c1', {
      project_id: 'p1', task_id: 't_orphan',
    }));
  });

  it('该机离线 → 清理按钮禁用', async () => {
    renderScreen();
    await screen.findByText('Beta');
    expect(screen.getByRole('button', { name: '清理 coagentia/off' })).toBeDisabled();
  });

  it('任务标题链接点击 → 切频道并跳转', async () => {
    renderScreen();
    await screen.findByText('Alpha');
    fireEvent.click(screen.getByRole('button', { name: '活跃任务' }));
    expect(setActiveChannelMock).toHaveBeenCalledWith('ch1');
    expect(navigateMock).toHaveBeenCalled();
  });
});
