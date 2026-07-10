// M3b 升格补契约(既有 L1 任务,辅路):PATCH level=l2 升格按钮 + 「让 @Agent 起草」定向直投两路。
// 升格按钮仅 L1(level≠l2)出;起草 202 成功 toast,DAEMON_OFFLINE 专属文案。
// 运行:pnpm -F @coagentia/web test
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    api: { ...actual.api, promoteTask: vi.fn(), requestContractDraft: vi.fn() },
  };
});

import type { MemberPublic, TaskDetail, TaskPublic } from '@coagentia/contracts-ts';

import { api, ApiError } from '../api';
import { qk } from '../lib/queryKeys';
import { ToastProvider, Toaster } from '../components/Toast';
import { ThreadPanel } from './ThreadPanel';

const OWNER: MemberPublic = {
  id: 'mem_owner', kind: 'human', role: 'owner', name: 'Memcyo',
  workspace_id: 'ws_1', created_at: '2026-07-09T00:00:00Z',
};
const HANK: MemberPublic = {
  id: 'mem_hank', kind: 'agent', name: 'Hank', workspace_id: 'ws_1', created_at: '2026-07-09T00:00:00Z',
};
const MEMBER_BY_ID: Record<string, MemberPublic> = { [OWNER.id]: OWNER, [HANK.id]: HANK };

function makeTask(level?: TaskPublic['level']): TaskPublic {
  return {
    id: 'task_1', channel_id: 'ch_build', created_at: '2026-07-09T04:15:00Z',
    created_by_member_id: OWNER.id, number: 1, owner_member_id: HANK.id,
    root_message_id: 'msg_root', status: 'in_progress', status_changed_at: '2026-07-09T04:20:00Z',
    title: '单文件番茄钟', workspace_id: 'ws_1', level,
  };
}

function renderPanel(task: TaskPublic) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  const detail: TaskDetail = { task, usage: {}, contracts: [] };
  qc.setQueryData(qk.taskDetail(task.id), detail);
  qc.setQueryData(qk.thread(task.root_message_id), []);
  render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <ThreadPanel
          task={task}
          rootMessageId={task.root_message_id}
          memberById={MEMBER_BY_ID}
          memberNames={Object.values(MEMBER_BY_ID).map((m) => m.name)}
          meName={OWNER.name}
          meId={OWNER.id}
          presenceOf={() => undefined}
          onClose={() => {}}
          onSend={() => {}}
        />
        <Toaster />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe('ThreadPanel L1→L2 升格', () => {
  it('L1 任务(level=l1)显示升格按钮 → 点击 PATCH level=l2', () => {
    vi.mocked(api.promoteTask).mockResolvedValue(makeTask('l2'));
    renderPanel(makeTask('l1'));
    const btn = screen.getByTestId('promote-l2');
    expect(btn).toBeInTheDocument();
    fireEvent.click(btn);
    expect(api.promoteTask).toHaveBeenCalledWith('task_1');
  });

  it('level 未设(默认 L1)也显示升格按钮', () => {
    renderPanel(makeTask(undefined));
    expect(screen.getByTestId('promote-l2')).toBeInTheDocument();
  });

  it('L2 任务不显示升格按钮', () => {
    renderPanel(makeTask('l2'));
    expect(screen.queryByTestId('promote-l2')).not.toBeInTheDocument();
  });
});

describe('ThreadPanel 让 @Agent 起草(补契约 Agent 路)', () => {
  it('选 Agent+kind → requestContractDraft(202)成功 toast', async () => {
    vi.mocked(api.requestContractDraft).mockResolvedValue(undefined);
    renderPanel(makeTask('l1'));

    fireEvent.click(screen.getByRole('button', { name: /让 @Agent 起草/ }));
    fireEvent.click(screen.getByText('@Hank · TaskPlan'));

    await waitFor(() => {
      expect(api.requestContractDraft).toHaveBeenCalledWith('task_1', { agent_member_id: 'mem_hank', kind: 'task_plan' });
    });
    await waitFor(() => {
      expect(screen.getByText(/已请求 @Hank 起草 TaskPlan/)).toBeInTheDocument();
    });
  });

  it('DAEMON_OFFLINE(503)→ 专属离线文案', async () => {
    vi.mocked(api.requestContractDraft).mockRejectedValue(
      new ApiError(503, 'DAEMON_OFFLINE', 'daemon 离线', undefined),
    );
    renderPanel(makeTask('l1'));

    fireEvent.click(screen.getByRole('button', { name: /让 @Agent 起草/ }));
    fireEvent.click(screen.getByText('@Hank · TaskHandoff'));

    await waitFor(() => {
      expect(screen.getByText(/@Hank 的 daemon 离线/)).toBeInTheDocument();
    });
  });
});
