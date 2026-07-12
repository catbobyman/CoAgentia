// M6b 拆解入口 T2（拆解设计 §4）：任务卡「拆解」动作 → POST {task_id} 形状；202 toast；
// 409 弹创建引导（引导链细节归 DecomposeGuide.test）。
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// 503 离线引导子组件用 useNavigate（去 P7）——测试无真 RouterProvider，替换为 spy。
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => vi.fn() }));

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    api: { ...actual.api, decompose: vi.fn(), computers: vi.fn() },
  };
});

import type { MemberPublic, ProposalPublic, TaskDetail, TaskPublic } from '@coagentia/contracts-ts';

import { api, ApiError } from '../api';
import { qk } from '../lib/queryKeys';
import { ToastProvider, Toaster } from '../components/Toast';
import { ThreadPanel } from './ThreadPanel';

const OWNER: MemberPublic = {
  id: 'mem_owner', kind: 'human', role: 'owner', name: 'Memcyo',
  workspace_id: 'ws_1', created_at: '2026-07-09T00:00:00Z',
};
const TASK: TaskPublic = {
  id: 'task_1', channel_id: 'ch_build', created_at: '2026-07-12T00:00:00Z',
  created_by_member_id: OWNER.id, number: 7, root_message_id: 'msg_root',
  status: 'todo', status_changed_at: '2026-07-12T00:00:00Z',
  title: '做一个番茄钟', workspace_id: 'ws_1',
};
const PROPOSAL: ProposalPublic = {
  id: 'prop_1', workspace_id: 'ws_1', channel_id: 'ch_build', source_task_id: TASK.id,
  kind: 'full', revision: 1, status: 'drafting', body: {},
  proposal_hash: 'e'.repeat(64), proposed_by_member_id: 'mem_orch',
  created_at: '2026-07-12T00:00:00Z', updated_at: '2026-07-12T00:00:00Z',
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
          channelId={TASK.channel_id}
          memberById={{ [OWNER.id]: OWNER }}
          memberNames={[OWNER.name]}
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

describe('ThreadPanel 任务卡「拆解」动作（T2）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.computers).mockResolvedValue([]);
  });

  it('点击「拆解」→ POST {task_id}（该任务即 source）；202 成功 toast', async () => {
    vi.mocked(api.decompose).mockResolvedValue(PROPOSAL);
    renderPanel();
    fireEvent.click(screen.getByTestId('task-decompose'));
    await waitFor(() =>
      expect(api.decompose).toHaveBeenCalledWith(TASK.channel_id, { task_id: TASK.id }));
    expect(await screen.findByText(/拆解已发起，提案将出现在本线程/)).toBeInTheDocument();
  });

  it('409 NO_ORCHESTRATOR → 弹创建引导（与画布入口同一条链）', async () => {
    vi.mocked(api.decompose).mockRejectedValue(
      new ApiError(409, 'NO_ORCHESTRATOR', '本频道还没有协调 Agent'),
    );
    renderPanel();
    fireEvent.click(screen.getByTestId('task-decompose'));
    expect(await screen.findByText('本频道还没有协调 Agent')).toBeInTheDocument();
    expect(screen.getByTestId('create-orchestrator')).toBeInTheDocument();
  });

  it('503 DAEMON_OFFLINE → 离线提示文案', async () => {
    vi.mocked(api.decompose).mockRejectedValue(
      new ApiError(503, 'DAEMON_OFFLINE', 'daemon 离线'),
    );
    renderPanel();
    fireEvent.click(screen.getByTestId('task-decompose'));
    expect(await screen.findByText('@Orchestrator 当前离线（机器断连）')).toBeInTheDocument();
  });
});
