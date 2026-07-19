// DEDAG [合并到主干] 按钮：显隐门（done 且 writes_code）+ 202 两态（accepted → pending 提示 /
// merged → 直呈已合并）+ 409 DEPLOY_IN_PROGRESS / 503 DAEMON_OFFLINE 专属文案。
// 运行:pnpm -F @coagentia/web test
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return { ...actual, api: { ...actual.api, mergeTask: vi.fn() } };
});

import type { TaskPublic, WorktreePublic } from '@coagentia/contracts-ts';

import { api, ApiError } from '../api';
import { ToastProvider, Toaster } from './Toast';
import { MergeButton } from './MergeButton';

const TASK: TaskPublic = {
  id: 'task_1', number: 7, title: '登录页交付', writes_code: true,
  channel_id: 'ch_1', workspace_id: 'ws_1', root_message_id: 'msg_root',
  created_by_member_id: 'mem_owner', owner_member_id: 'mem_rin',
  status: 'done', created_at: '2026-07-18T00:00:00Z', status_changed_at: '2026-07-18T00:00:00Z',
};

const WORKTREE: WorktreePublic = {
  id: 'wt_1', workspace_id: 'ws_1', project_id: 'project_1', task_id: 'task_1',
  branch: 'task/7-login', path: 'D:/repos/demo-wt/task-7', status: 'active',
  created_at: '2026-07-18T00:00:00Z',
};

function renderBtn(task: TaskPublic = TASK, worktree: WorktreePublic | null = WORKTREE) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <MergeButton task={task} worktree={worktree} />
        <Toaster />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe('MergeButton 显隐门', () => {
  beforeEach(() => vi.clearAllMocks());

  it('done 且 writes_code → 渲染 [合并到主干] 可点', () => {
    renderBtn();
    const btn = screen.getByTestId('merge-btn');
    expect(btn).toHaveTextContent('合并到主干');
    expect(btn).toBeEnabled();
  });

  it('非 done（in_progress）→ 不渲染', () => {
    renderBtn({ ...TASK, status: 'in_progress' });
    expect(screen.queryByTestId('merge-btn')).not.toBeInTheDocument();
  });

  it('done 但非 writes_code → 不渲染', () => {
    renderBtn({ ...TASK, writes_code: false });
    expect(screen.queryByTestId('merge-btn')).not.toBeInTheDocument();
  });

  it('worktree 已 merged → 直呈已合并禁用态（WS 反流后的稳态）', () => {
    renderBtn(TASK, { ...WORKTREE, status: 'merged' });
    const btn = screen.getByTestId('merge-btn');
    expect(btn).toHaveTextContent('已合并');
    expect(btn).toBeDisabled();
  });

  it('无可合并 worktree（null）→ 按钮禁用', () => {
    renderBtn(TASK, null);
    expect(screen.getByTestId('merge-btn')).toBeDisabled();
  });
});

describe('MergeButton 202 两态与错误文案', () => {
  beforeEach(() => vi.clearAllMocks());

  it('202 accepted → toast「已受理…系统消息回报」+ 按钮置 pending（合并中…禁用）', async () => {
    vi.mocked(api.mergeTask).mockResolvedValue({ task_id: 'task_1', status: 'accepted' });
    renderBtn();
    fireEvent.click(screen.getByTestId('merge-btn'));
    expect(screen.getByTestId('merge-confirm')).toBeInTheDocument();
    expect(screen.getByTestId('merge-branch')).toHaveTextContent('task/7-login');
    fireEvent.click(screen.getByTestId('merge-confirm-btn'));
    await waitFor(() => expect(api.mergeTask).toHaveBeenCalledWith('task_1'));
    await waitFor(() => {
      expect(screen.getByText('已受理，合并结果将以频道系统消息回报')).toBeInTheDocument();
    });
    // 弹窗关闭 + 按钮 pending 态（终态靠 worktree.updated 反流接管）。
    expect(screen.queryByTestId('merge-confirm')).not.toBeInTheDocument();
    const btn = screen.getByTestId('merge-btn');
    expect(btn).toHaveTextContent('合并中…');
    expect(btn).toBeDisabled();
  });

  it('202 merged（幂等命中）→ 直接呈已合并态', async () => {
    vi.mocked(api.mergeTask).mockResolvedValue({ task_id: 'task_1', status: 'merged' });
    renderBtn();
    fireEvent.click(screen.getByTestId('merge-btn'));
    fireEvent.click(screen.getByTestId('merge-confirm-btn'));
    await waitFor(() => {
      expect(screen.getByTestId('merge-btn')).toHaveTextContent('已合并');
    });
    expect(screen.getByTestId('merge-btn')).toBeDisabled();
    expect(screen.getByText('该任务已合并到主干')).toBeInTheDocument();
  });

  it('409 DEPLOY_IN_PROGRESS → toast「该项目已有合并在执行，稍后再试」', async () => {
    vi.mocked(api.mergeTask).mockRejectedValue(
      new ApiError(409, 'DEPLOY_IN_PROGRESS', '同 Project 串行'),
    );
    renderBtn();
    fireEvent.click(screen.getByTestId('merge-btn'));
    fireEvent.click(screen.getByTestId('merge-confirm-btn'));
    await waitFor(() => {
      expect(screen.getByText('该项目已有合并在执行，稍后再试')).toBeInTheDocument();
    });
    // 失败不置 pending：弹窗保留，可稍后重试或取消。
    expect(screen.getByTestId('merge-confirm')).toBeInTheDocument();
  });

  it('503 DAEMON_OFFLINE → toast「目标电脑 daemon 离线」', async () => {
    vi.mocked(api.mergeTask).mockRejectedValue(
      new ApiError(503, 'DAEMON_OFFLINE', 'Project 宿主 daemon 离线'),
    );
    renderBtn();
    fireEvent.click(screen.getByTestId('merge-btn'));
    fireEvent.click(screen.getByTestId('merge-confirm-btn'));
    await waitFor(() => {
      expect(screen.getByText('目标电脑 daemon 离线')).toBeInTheDocument();
    });
  });

  it('conflicted worktree → 可重试（冲突警示），确认再发 POST', async () => {
    vi.mocked(api.mergeTask).mockResolvedValue({ task_id: 'task_1', status: 'accepted' });
    renderBtn(TASK, { ...WORKTREE, status: 'conflicted' });
    const btn = screen.getByTestId('merge-btn');
    expect(btn).toBeEnabled();
    fireEvent.click(btn);
    expect(screen.getByRole('alert')).toHaveTextContent(/冲突/);
    fireEvent.click(screen.getByTestId('merge-confirm-btn'));
    await waitFor(() => expect(api.mergeTask).toHaveBeenCalledWith('task_1'));
  });
});
