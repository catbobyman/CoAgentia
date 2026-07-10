// P6 Reminders 页签强化(M4a):每行展示 kind/cadence/next_fire_at/锚点/循环契约角标/status,
// active 行有取消钮(DELETE /reminders/{id});cancelled/done 行无取消钮。
// 照 ThreadPanel.promote.test.tsx 的 QueryClient seed + vi.mock('../api') 范式。
// 运行:pnpm -F @coagentia/web test
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    api: { ...actual.api, cancelReminder: vi.fn() },
  };
});

import type { ReminderPublic } from '@coagentia/contracts-ts';

import { api, ApiError } from '../api';
import { qk } from '../lib/queryKeys';
import { ToastProvider, Toaster } from '../components/Toast';
import { RemindersTab } from './AgentDetailScreen';

const AGENT = 'mem_agent';

function reminder(id: string, over: Partial<ReminderPublic> = {}): ReminderPublic {
  return {
    id,
    agent_member_id: AGENT,
    anchor_channel_id: 'ch_abc123build',
    cadence: 'daily 09:00',
    created_at: '2026-07-10T00:00:00Z',
    kind: 'recurring',
    next_fire_at: '2026-07-11T09:00:00Z',
    status: 'active',
    workspace_id: 'ws_1',
    ...over,
  };
}

function renderTab(reminders: ReminderPublic[]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  qc.setQueryData<ReminderPublic[]>(qk.agentReminders(AGENT), reminders);
  render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <RemindersTab memberId={AGENT} />
        <Toaster />
      </ToastProvider>
    </QueryClientProvider>,
  );
  return { qc };
}

describe('RemindersTab 展示', () => {
  it('展示 kind / cadence / status,以及循环契约角标(loop_contract_id 非空)', () => {
    renderTab([reminder('rem_1', { loop_contract_id: 'lc_1' })]);
    expect(screen.getByText('recurring')).toBeInTheDocument();
    expect(screen.getByText('daily 09:00')).toBeInTheDocument();
    expect(screen.getByText('active')).toBeInTheDocument();
    expect(screen.getByText('循环 · 契约')).toBeInTheDocument();
  });

  it('无 loop_contract_id 则不显示循环契约角标', () => {
    renderTab([reminder('rem_1', { loop_contract_id: null })]);
    expect(screen.queryByText('循环 · 契约')).not.toBeInTheDocument();
  });

  it('锚点:频道恒标注,task/message 择有标注(尾 6 位)', () => {
    renderTab([
      reminder('rem_1', {
        anchor_channel_id: 'chbld1',
        anchor_task_id: 'task_xyz789',
        anchor_message_id: 'msg_qrs456',
      }),
    ]);
    expect(screen.getByText(/#chbld1/)).toBeInTheDocument();
    expect(screen.getByText(/task xyz789/)).toBeInTheDocument();
    expect(screen.getByText(/msg qrs456/)).toBeInTheDocument();
  });

  it('空列表 → 空态文案', () => {
    renderTab([]);
    expect(screen.getByText('尚无 reminder。')).toBeInTheDocument();
  });
});

describe('RemindersTab 取消交互', () => {
  it('active 行:点击取消 → api.cancelReminder(id)', async () => {
    vi.mocked(api.cancelReminder).mockResolvedValue(undefined);
    renderTab([reminder('rem_1')]);

    fireEvent.click(screen.getByRole('button', { name: '取消' }));

    await waitFor(() => expect(api.cancelReminder).toHaveBeenCalledWith('rem_1'));
  });

  it('cancelled / done 行:不显示取消钮', () => {
    renderTab([
      reminder('rem_c', { status: 'cancelled' }),
      reminder('rem_d', { status: 'done' }),
    ]);
    expect(screen.queryByRole('button', { name: '取消' })).not.toBeInTheDocument();
    expect(screen.getByText('cancelled')).toBeInTheDocument();
    expect(screen.getByText('done')).toBeInTheDocument();
  });

  it('取消失败(403)→ 错误 toast', async () => {
    vi.mocked(api.cancelReminder).mockRejectedValue(
      new ApiError(403, 'FORBIDDEN', '无权取消该提醒', undefined),
    );
    renderTab([reminder('rem_1')]);

    fireEvent.click(screen.getByRole('button', { name: '取消' }));

    await waitFor(() => expect(screen.getByText('无权取消该提醒')).toBeInTheDocument());
  });
});
