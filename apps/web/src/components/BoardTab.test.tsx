// P3 频道看板页签:5 态分列渲染 + 任务卡点选回调（DEDAG:画布派生 blocked 徽标/强制启动已随画布域退役）。
// 运行:pnpm -F @coagentia/web test
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import type { MemberPublic, TaskPublic } from '@coagentia/contracts-ts';

import { ToastProvider } from './Toast';
import { BoardTab } from './BoardTab';

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

function renderBoard(tasks: TaskPublic[], onSelectTask?: (id: string) => void) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <BoardTab tasks={tasks} memberById={memberById} presenceOf={() => undefined} onSelectTask={onSelectTask} />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe('BoardTab 渲染与点选', () => {
  const tasks = [task('t_1', 1, 'in_progress'), task('t_2', 2, 'todo')];

  it('任务按状态列渲染(标题与编号可见)', () => {
    renderBoard(tasks);
    expect(screen.getByText('任务 1')).toBeInTheDocument();
    expect(screen.getByText('任务 2')).toBeInTheDocument();
    expect(screen.getByText('#1')).toBeInTheDocument();
    expect(screen.getByText('#2')).toBeInTheDocument();
  });

  it('点任务卡 → onSelectTask 回调该任务 id', () => {
    const onSelect = vi.fn();
    renderBoard(tasks, onSelect);
    fireEvent.click(screen.getByText('任务 2'));
    expect(onSelect).toHaveBeenCalledWith('t_2');
  });
});
