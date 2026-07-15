// P3 看板 blocked 徽标(M3b):由频道画布快照派生 blocked task_id 集 → blocked 卡出锁标 + 「blocked」
// 徽标 + 强制启动按钮(点击进二次确认弹层)。blockedTaskIdsFromCanvas 纯函数单测 + 组件渲染集成测。
// 运行:pnpm -F @coagentia/web test
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    api: { ...actual.api, canvasSnapshot: vi.fn(), forceStart: vi.fn() },
  };
});

import type { CanvasDetail, MemberPublic, TaskPublic } from '@coagentia/contracts-ts';

import { qk } from '../lib/queryKeys';
import { ToastProvider } from './Toast';
import { BoardTab, blockedTaskIdsFromCanvas } from './BoardTab';

function task(id: string, number: number, status: TaskPublic['status']): TaskPublic {
  return {
    id, number, title: `任务 ${number}`, status,
    channel_id: 'ch_1', workspace_id: 'ws_1', root_message_id: `msg_${id}`,
    created_by_member_id: 'mem_owner', owner_member_id: 'mem_rin',
    created_at: '2026-07-10T00:00:00Z', status_changed_at: '2026-07-10T00:00:00Z',
  };
}
const RIN: MemberPublic = {
  id: 'mem_rin', kind: 'agent', name: 'Rin', workspace_id: 'ws_1', created_at: '2026-07-10T00:00:00Z',
};
const memberById: Record<string, MemberPublic> = { [RIN.id]: RIN };

// n1(t_1)→ n2(t_2):t_1 未 done ⇒ n2 blocked ⇒ t_2 ∈ blocked。
function canvasDetail(): CanvasDetail {
  return {
    canvas: {
      id: 'cv_1', channel_id: 'ch_1', workspace_id: 'ws_1',
      baseline_hash: 'h0', baseline_version: 1, updated_at: '2026-07-10T00:00:00Z',
    },
    nodes: [
      { id: 'n_1', canvas_id: 'cv_1', kind: 'agent', task_id: 't_1', pos_x: 0, pos_y: 0, created_at: '2026-07-10T00:00:00Z' },
      { id: 'n_2', canvas_id: 'cv_1', kind: 'agent', task_id: 't_2', pos_x: 300, pos_y: 0, created_at: '2026-07-10T00:00:00Z' },
    ],
    edges: [{ id: 'e_1', canvas_id: 'cv_1', from_node_id: 'n_1', to_node_id: 'n_2' }],
  };
}

describe('blockedTaskIdsFromCanvas 派生', () => {
  it('上游未 done → 下游 task_id 入 blocked 集;上游 done → 空集', () => {
    const detail = canvasDetail();
    const notDone = { t_1: task('t_1', 1, 'in_progress'), t_2: task('t_2', 2, 'todo') };
    expect([...blockedTaskIdsFromCanvas(detail, notDone)]).toEqual(['t_2']);

    const done = { t_1: task('t_1', 1, 'done'), t_2: task('t_2', 2, 'todo') };
    expect(blockedTaskIdsFromCanvas(detail, done).size).toBe(0);
  });

  it('无画布 → 空集', () => {
    expect(blockedTaskIdsFromCanvas(undefined, {}).size).toBe(0);
  });
});

function renderBoard(tasks: TaskPublic[], detail?: CanvasDetail) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  if (detail) qc.setQueryData(qk.canvas('ch_1'), detail);
  render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <BoardTab tasks={tasks} memberById={memberById} presenceOf={() => undefined} />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe('BoardTab blocked 徽标 + force-start', () => {
  const tasks = [task('t_1', 1, 'in_progress'), task('t_2', 2, 'todo')];

  it('blocked 任务卡显示 lock+blocked 徽标与强制启动按钮;非 blocked 卡不显示', () => {
    renderBoard(tasks, canvasDetail());
    // t_2 blocked、t_1 不 blocked → 各恰一枚徽标/按钮。
    expect(screen.getAllByTestId('board-blocked')).toHaveLength(1);
    expect(screen.getAllByTestId('board-force-start')).toHaveLength(1);
    expect(screen.getByText('blocked')).toBeInTheDocument();
  });

  it('无画布快照 → 无 blocked 徽标', () => {
    renderBoard(tasks);
    expect(screen.queryByTestId('board-blocked')).not.toBeInTheDocument();
  });

  it('点强制启动 → 弹二次确认弹层(不误触选牌)', () => {
    const onSelect = vi.fn();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    qc.setQueryData(qk.canvas('ch_1'), canvasDetail());
    render(
      <QueryClientProvider client={qc}>
        <ToastProvider>
          <BoardTab tasks={tasks} memberById={memberById} presenceOf={() => undefined} onSelectTask={onSelect} />
        </ToastProvider>
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByTestId('board-force-start'));
    expect(screen.getByTestId('force-start-confirm')).toBeInTheDocument();
    // stopPropagation:强制启动不应触发选牌回调。
    expect(onSelect).not.toHaveBeenCalled();
  });
});
