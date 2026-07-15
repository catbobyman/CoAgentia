// PS-WT ① 侧栏「项目」区：admin 门（canManage=false 不渲染、不发 projects 查询）/ 展开显示绑定频道
// 引用 / 点击频道引用 → onSelectChannel / 双入口（区头 ＋ = NewProjectModal、项目行 ＋ = NewChannelModal）。
// 照 ProjectSettingsSection.test 的 QueryClient + vi.mock('../api') 范式。运行:pnpm -F @coagentia/web test
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    api: {
      ...actual.api,
      projects: vi.fn(), computers: vi.fn(), createProject: vi.fn(),
      createChannel: vi.fn(), bindProject: vi.fn(),
    },
  };
});

import type { ChannelPublic, ComputerPublic, ProjectPublic } from '@coagentia/contracts-ts';

import { api } from '../api';
import { ToastProvider } from './Toast';
import { ProjectSidebarSection, type ProjectSidebarSectionProps } from './ProjectSidebarSection';

const CH_BUILD: ChannelPublic = { id: 'ch1', kind: 'channel', name: 'build', workspace_id: 'ws_1', created_at: '2026-07-14T00:00:00Z' };
const CH_DESIGN: ChannelPublic = { id: 'ch2', kind: 'channel', name: 'design', workspace_id: 'ws_1', created_at: '2026-07-14T00:00:00Z' };
const COMPUTER: ComputerPublic = { id: 'computer_1', workspace_id: 'ws_1', name: '本机', created_at: '2026-07-14T00:00:00Z' };
const PROJECT: ProjectPublic = {
  id: 'project_1', workspace_id: 'ws_1', computer_id: COMPUTER.id, name: 'Alpha',
  repo_path: 'D:/repos/alpha', channel_ids: ['ch1'], created_at: '2026-07-14T00:00:00Z',
  worktree_keep_days: 7, preview_idle_min: 30,
};

function renderSection(over: Partial<ProjectSidebarSectionProps> = {}) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const props: ProjectSidebarSectionProps = {
    channels: [CH_BUILD, CH_DESIGN],
    activeChannelId: undefined,
    onSelectChannel: vi.fn(),
    canManage: true,
    ...over,
  };
  render(
    <QueryClientProvider client={qc}>
      <ToastProvider><ProjectSidebarSection {...props} /></ToastProvider>
    </QueryClientProvider>,
  );
  return props;
}

describe('ProjectSidebarSection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.projects).mockResolvedValue([PROJECT]);
    vi.mocked(api.computers).mockResolvedValue([COMPUTER]);
  });

  it('非 admin（canManage=false）不渲染此区，也不发 projects 查询', () => {
    renderSection({ canManage: false });
    expect(screen.queryByText('项目')).not.toBeInTheDocument();
    expect(api.projects).not.toHaveBeenCalled();
  });

  it('admin：展开项目 → 显示绑定频道引用；点击引用触发 onSelectChannel', async () => {
    const props = renderSection();
    const projRow = await screen.findByRole('button', { name: '项目 Alpha' });
    // 展开前不显示绑定频道引用。
    expect(screen.queryByText('build')).not.toBeInTheDocument();
    fireEvent.click(projRow);
    const chanRef = await screen.findByText('build');
    fireEvent.click(chanRef);
    await waitFor(() => expect(props.onSelectChannel).toHaveBeenCalledWith(CH_BUILD));
  });

  it('双入口：区头 ＋ 打开 NewProjectModal；项目行 ＋ 打开 NewChannelModal', async () => {
    renderSection();
    await screen.findByRole('button', { name: '项目 Alpha' });

    fireEvent.click(screen.getByRole('button', { name: '新建 Project' }));
    expect(await screen.findByRole('dialog', { name: '新建 Project' })).toBeInTheDocument();
    // 关闭再测项目行入口（取消按钮关弹窗）。
    fireEvent.click(screen.getByRole('button', { name: '取消' }));

    fireEvent.click(screen.getByRole('button', { name: '在 Alpha 下新建频道' }));
    expect(await screen.findByRole('dialog', { name: '新建频道' })).toBeInTheDocument();
  });
});
