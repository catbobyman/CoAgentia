// PS-WT ① 新建 Project 弹窗：字段渲染 / 空值禁提交 / 未选 Computer 时「浏览」禁用 / 选机后「浏览」
// 打开 FolderPickerModal / 提交携 ProjectCreate 体 / VALIDATION_FAILED 就地报错不关窗。
// 照 ProjectSettingsSection.test 的 QueryClient + vi.mock('../api') 范式。运行:pnpm -F @coagentia/web test
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    api: { ...actual.api, computers: vi.fn(), createProject: vi.fn(), browseFs: vi.fn() },
  };
});

import type { ComputerPublic, ProjectPublic } from '@coagentia/contracts-ts';

import { api, ApiError } from '../api';
import { ToastProvider } from './Toast';
import { NewProjectModal } from './NewProjectModal';

const C1: ComputerPublic = { id: 'computer_1', workspace_id: 'ws_1', name: '本机', created_at: '2026-07-14T00:00:00Z' };
const C2: ComputerPublic = { id: 'computer_2', workspace_id: 'ws_1', name: '远程', created_at: '2026-07-14T00:00:00Z' };
const PROJECT: ProjectPublic = {
  id: 'project_1', workspace_id: 'ws_1', computer_id: C1.id, name: 'Alpha',
  repo_path: 'D:/repos/alpha', channel_ids: [], created_at: '2026-07-14T00:00:00Z',
  worktree_keep_days: 7, preview_idle_min: 30,
};

function renderModal(over?: { onCreated?: (p: ProjectPublic) => void; onClose?: () => void }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <NewProjectModal onClose={over?.onClose ?? (() => {})} onCreated={over?.onCreated} />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe('NewProjectModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.computers).mockResolvedValue([C1, C2]); // 两台 → 不预选，可测未选态
    vi.mocked(api.createProject).mockResolvedValue(PROJECT);
    vi.mocked(api.browseFs).mockResolvedValue({ entries: [], truncated: false });
  });

  it('渲染 名称 / Computer / 仓库路径 + 浏览 字段', async () => {
    renderModal();
    expect(await screen.findByRole('dialog', { name: '新建 Project' })).toBeInTheDocument();
    expect(screen.getByLabelText('名称')).toBeInTheDocument();
    expect(screen.getByLabelText('Computer')).toBeInTheDocument();
    expect(screen.getByLabelText('仓库路径')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '浏览' })).toBeInTheDocument();
  });

  it('未选 Computer → 浏览禁用；创建按钮禁用', async () => {
    renderModal();
    await screen.findByRole('option', { name: '本机' });
    expect(screen.getByRole('button', { name: '浏览' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '创建 Project' })).toBeDisabled();
  });

  it('选机后浏览可用，点击打开 FolderPickerModal', async () => {
    renderModal();
    await screen.findByRole('option', { name: '本机' });
    fireEvent.change(screen.getByLabelText('Computer'), { target: { value: C1.id } });
    const browse = screen.getByRole('button', { name: '浏览' });
    expect(browse).not.toBeDisabled();
    fireEvent.click(browse);
    expect(await screen.findByRole('dialog', { name: '选择文件夹' })).toBeInTheDocument();
  });

  it('填全字段后提交 → POST /projects 携 name/repo_path/computer_id', async () => {
    const onCreated = vi.fn();
    const onClose = vi.fn();
    renderModal({ onCreated, onClose });
    await screen.findByRole('option', { name: '本机' });
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: 'Alpha' } });
    fireEvent.change(screen.getByLabelText('Computer'), { target: { value: C1.id } });
    fireEvent.change(screen.getByLabelText('仓库路径'), { target: { value: 'D:/repos/alpha' } });
    const submit = screen.getByRole('button', { name: '创建 Project' });
    expect(submit).not.toBeDisabled();
    fireEvent.click(submit);
    await waitFor(() => expect(api.createProject).toHaveBeenCalledWith({
      name: 'Alpha', repo_path: 'D:/repos/alpha', computer_id: C1.id,
    }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(PROJECT));
    expect(onClose).toHaveBeenCalled();
  });

  it('VALIDATION_FAILED 就地报错不关窗', async () => {
    vi.mocked(api.createProject).mockRejectedValue(
      new ApiError(422, 'VALIDATION_FAILED', 'repo_path 不是 Git 仓库'),
    );
    const onClose = vi.fn();
    renderModal({ onClose });
    await screen.findByRole('option', { name: '本机' });
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: 'Bad' } });
    fireEvent.change(screen.getByLabelText('Computer'), { target: { value: C1.id } });
    fireEvent.change(screen.getByLabelText('仓库路径'), { target: { value: 'D:/bad' } });
    fireEvent.click(screen.getByRole('button', { name: '创建 Project' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('不是 Git 仓库');
    expect(screen.getByRole('dialog', { name: '新建 Project' })).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });
});
