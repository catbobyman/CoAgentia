// P5 T7 拒绝就地提示(交互 §5.4):置 in_review 命中 HANDOFF_INCOMPLETE(422)时,
// error.details.missing 要在契约卡区就地展示,而不是一闪而过的 toast。
// 运行:pnpm -F @coagentia/web test
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    api: {
      ...actual.api,
      setTaskStatus: vi.fn(),
    },
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
  id: 'mem_hank', kind: 'agent', name: 'Hank',
  workspace_id: 'ws_1', created_at: '2026-07-09T00:00:00Z',
};
const MEMBER_BY_ID: Record<string, MemberPublic> = { [OWNER.id]: OWNER, [HANK.id]: HANK };

// in_progress → 目标态含 in_review(TASK_TRANSITIONS,packages/contracts-ts 生成),用于触发状态下拉。
const TASK: TaskPublic = {
  id: 'task_1',
  channel_id: 'ch_build',
  created_at: '2026-07-09T04:15:00Z',
  created_by_member_id: OWNER.id,
  number: 1,
  owner_member_id: HANK.id,
  root_message_id: 'msg_root',
  status: 'in_progress',
  status_changed_at: '2026-07-09T04:20:00Z',
  title: '单文件番茄钟',
  workspace_id: 'ws_1',
};

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  const detail: TaskDetail = { task: TASK, usage: {}, contracts: [] };
  qc.setQueryData(qk.taskDetail(TASK.id), detail);
  qc.setQueryData(qk.thread(TASK.root_message_id), []);

  render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <ThreadPanel
          task={TASK}
          rootMessageId={TASK.root_message_id}
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

describe('ThreadPanel T7 拒绝就地提示(HANDOFF_INCOMPLETE)', () => {
  it('置 in_review 命中 HANDOFF_INCOMPLETE → 就地显示 missing 字段(不是 toast)', async () => {
    vi.mocked(api.setTaskStatus).mockRejectedValue(
      new ApiError(422, 'HANDOFF_INCOMPLETE', '交接契约不完整', { missing: ['deliverables', 'evidence'] }),
    );

    renderPanel();

    // 状态下拉:In Progress → 展开 → 点 In Review。
    fireEvent.click(screen.getByRole('button', { name: /In Progress/ }));
    fireEvent.click(screen.getByText('In Review'));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
    });
    expect(screen.getByText(/缺少:deliverables \/ evidence/)).toBeInTheDocument();

    // 不是 toast:toaster 容器里不应出现这条文案(就地提示常驻在契约卡区,不是自动消失的浮层)。
    const toaster = document.querySelector('.toaster');
    expect(toaster?.textContent ?? '').not.toMatch(/deliverables/);
  });

  it('DAEMON_OFFLINE 与 HANDOFF_INCOMPLETE 互不影响:非 HANDOFF_INCOMPLETE 错误不点亮就地提示', async () => {
    vi.mocked(api.setTaskStatus).mockRejectedValue(
      new ApiError(422, 'TASK_TRANSITION_INVALID', '非法流转', undefined),
    );

    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: /In Progress/ }));
    fireEvent.click(screen.getByText('In Review'));

    await waitFor(() => {
      expect(screen.getByText(/非法流转/)).toBeInTheDocument();
    });
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});
