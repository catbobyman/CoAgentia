import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    api: {
      ...actual.api,
      projects: vi.fn(), computers: vi.fn(), createProject: vi.fn(), patchProject: vi.fn(),
      deleteProject: vi.fn(), bindProject: vi.fn(), unbindProject: vi.fn(),
    },
  };
});

import type { ComputerPublic, ProjectPublic } from '@coagentia/contracts-ts';

import { api, ApiError } from '../api';
import { ToastProvider } from './Toast';
import { ProjectSettingsSection } from './ProjectSettingsSection';

const COMPUTER: ComputerPublic = {
  id: 'computer_1', workspace_id: 'ws_1', name: '本机',
  created_at: '2026-07-11T00:00:00Z',
};
const PROJECT: ProjectPublic = {
  id: 'project_1', workspace_id: 'ws_1', computer_id: COMPUTER.id, name: 'Alpha',
  repo_path: 'D:/repos/alpha', channel_ids: [], created_at: '2026-07-11T00:00:00Z',
  worktree_keep_days: 7, preview_idle_min: 30,
};

function renderSection(canManage = true) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <ToastProvider><ProjectSettingsSection channelId="ch_1" canManage={canManage} /></ToastProvider>
    </QueryClientProvider>,
  );
  return qc;
}

describe('ProjectSettingsSection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.projects).mockResolvedValue([PROJECT]);
    vi.mocked(api.computers).mockResolvedValue([COMPUTER]);
  });

  it('管理员可把未绑定 Project 绑定当前频道', async () => {
    vi.mocked(api.bindProject).mockResolvedValue({ channel_id: 'ch_1', project_id: PROJECT.id });
    renderSection();
    expect(await screen.findByText('Alpha')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '绑定 Alpha' }));
    await waitFor(() => expect(api.bindProject).toHaveBeenCalledWith('ch_1', PROJECT.id));
  });

  it('新建 Project 预选唯一 Computer，并提交完整交付配置', async () => {
    vi.mocked(api.createProject).mockResolvedValue(PROJECT);
    renderSection();
    await screen.findByText('Alpha');
    fireEvent.click(screen.getByRole('button', { name: '新建 Project' }));
    fireEvent.change(screen.getByLabelText('Project 名称'), { target: { value: 'Beta' } });
    fireEvent.change(screen.getByLabelText('仓库路径'), { target: { value: 'D:/repos/beta' } });
    fireEvent.change(screen.getByLabelText('开发命令'), { target: { value: 'pnpm dev' } });
    fireEvent.change(screen.getByLabelText('保留天数'), { target: { value: '14' } });
    fireEvent.click(screen.getByRole('button', { name: '创建 Project' }));
    await waitFor(() => expect(api.createProject).toHaveBeenCalledWith({
      name: 'Beta', repo_path: 'D:/repos/beta', computer_id: COMPUTER.id,
      dev_command: 'pnpm dev', worktree_keep_days: 14, preview_idle_min: 30,
    }));
  });

  it('repo_path 422 在表单内显示，不关闭弹窗', async () => {
    vi.mocked(api.createProject).mockRejectedValue(
      new ApiError(422, 'VALIDATION_FAILED', 'repo_path 不是 Git 仓库'),
    );
    renderSection();
    await screen.findByText('Alpha');
    fireEvent.click(screen.getByRole('button', { name: '新建 Project' }));
    fireEvent.change(screen.getByLabelText('Project 名称'), { target: { value: 'Bad' } });
    fireEvent.change(screen.getByLabelText('仓库路径'), { target: { value: 'D:/bad' } });
    fireEvent.click(screen.getByRole('button', { name: '创建 Project' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('repo_path 不是 Git 仓库');
    expect(screen.getByRole('dialog', { name: 'Project 编辑器' })).toBeInTheDocument();
  });

  it('无管理权限时不发 Project 查询也不渲染管理面', () => {
    renderSection(false);
    expect(screen.queryByText('Project')).not.toBeInTheDocument();
    expect(api.projects).not.toHaveBeenCalled();
  });
});
