// M3b force-start 二次确认弹层:确认 → POST force-start → 成功 toast「已强制启动,已留痕」;
// 403(非人类 owner)→ 专属错误文案;取消 → onClose,不发请求。
// 运行:pnpm -F @coagentia/web test
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    api: { ...actual.api, forceStart: vi.fn() },
  };
});

import type { TaskPublic } from '@coagentia/contracts-ts';

import { api, ApiError } from '../api';
import { ToastProvider, Toaster } from './Toast';
import { ForceStartModal } from './ForceStartModal';

const TASK: TaskPublic = {
  id: 'task_1', number: 7, title: '被阻塞的部署任务',
  channel_id: 'ch_1', workspace_id: 'ws_1', root_message_id: 'msg_root',
  created_by_member_id: 'mem_owner', owner_member_id: 'mem_rin',
  status: 'todo', created_at: '2026-07-10T00:00:00Z', status_changed_at: '2026-07-10T00:00:00Z',
};

function renderModal(onClose = vi.fn()) {
  render(
    <ToastProvider>
      <ForceStartModal task={TASK} onClose={onClose} />
      <Toaster />
    </ToastProvider>,
  );
  return { onClose };
}

describe('ForceStartModal 二次确认', () => {
  it('展示目标任务 + 红色留痕警告,二次确认按钮存在', () => {
    renderModal();
    expect(screen.getByTestId('force-start-confirm')).toBeInTheDocument();
    expect(screen.getByText('#7')).toBeInTheDocument();
    expect(screen.getByText('被阻塞的部署任务')).toBeInTheDocument();
    // 红色校验条(role=alert)提示留痕 + 越过 gating。
    expect(screen.getByRole('alert')).toHaveTextContent(/force_start|留痕/);
    expect(screen.getByTestId('force-start-confirm-btn')).toBeInTheDocument();
  });

  it('确认 → api.forceStart(taskId) 且成功 toast「已强制启动,已留痕」', async () => {
    vi.mocked(api.forceStart).mockResolvedValue(TASK);
    const { onClose } = renderModal();

    fireEvent.click(screen.getByTestId('force-start-confirm-btn'));

    await waitFor(() => expect(api.forceStart).toHaveBeenCalledWith('task_1'));
    await waitFor(() => {
      expect(screen.getByText('已强制启动,已留痕')).toBeInTheDocument();
    });
    expect(onClose).toHaveBeenCalled();
  });

  it('403 → 专属错误文案(仅人类 owner 可越过 gating),不关闭', async () => {
    vi.mocked(api.forceStart).mockRejectedValue(
      new ApiError(403, 'FORBIDDEN', '禁止', undefined),
    );
    const { onClose } = renderModal();

    fireEvent.click(screen.getByTestId('force-start-confirm-btn'));

    await waitFor(() => {
      expect(screen.getByText(/仅人类 owner 可越过 gating/)).toBeInTheDocument();
    });
    expect(onClose).not.toHaveBeenCalled();
  });

  it('取消 → onClose,不发起 force-start', () => {
    vi.mocked(api.forceStart).mockClear();
    const { onClose } = renderModal();
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(onClose).toHaveBeenCalled();
    expect(api.forceStart).not.toHaveBeenCalled();
  });
});
