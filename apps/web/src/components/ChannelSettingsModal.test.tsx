// P12 频道级设置弹窗:基础四组 + M6a Project 组 + 通知 mode 即点即存。
// 照 RemindersTab.test.tsx 的 QueryClient seed + vi.mock('../api') 范式。
// 运行:pnpm -F @coagentia/web test
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    api: {
      ...actual.api,
      patchChannel: vi.fn(), putNotificationSetting: vi.fn(), projects: vi.fn(), computers: vi.fn(),
    },
  };
});

import type { ChannelPublic } from '@coagentia/contracts-ts';

import { api } from '../api';
import { ToastProvider, Toaster } from '../components/Toast';
import { ChannelSettingsModal } from './ChannelSettingsModal';

function channelOf(over: Partial<ChannelPublic> = {}): ChannelPublic {
  return {
    id: 'ch1',
    kind: 'channel',
    name: 'build',
    workspace_id: 'ws1',
    created_at: '2026-07-11T00:00:00Z',
    description: 'orig desc',
    is_private: false,
    remind_todo_h: 24,
    remind_inprog_h: 12,
    remind_review_h: 24,
    remind_escalation: false,
    held_reeval_min: 5,
    held_escalate_n: 3,
    ...over,
  };
}

function renderModal(opts: { channel?: ChannelPublic; currentMode?: 'all' | 'mentions' | 'mute' } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  const onClose = vi.fn();
  render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <ChannelSettingsModal
          channel={opts.channel ?? channelOf()}
          meId="mem_owner"
          currentMode={opts.currentMode ?? 'all'}
          onClose={onClose}
        />
        <Toaster />
      </ToastProvider>
    </QueryClientProvider>,
  );
  return { qc, onClose };
}

describe('ChannelSettingsModal 五组', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.projects).mockResolvedValue([]);
    vi.mocked(api.computers).mockResolvedValue([]);
  });

  it('渲染基本/通知/Project/提醒/护栏五组', () => {
    renderModal();
    expect(screen.getByText('基本')).toBeInTheDocument();
    expect(screen.getByText('通知')).toBeInTheDocument();
    expect(screen.getByText('Project')).toBeInTheDocument();
    expect(screen.getByText('提醒阈值')).toBeInTheDocument();
    expect(screen.getByText('护栏阈值')).toBeInTheDocument();
  });

  it('DM 频道:无通知组(DM 必达,裁决 #5)', () => {
    renderModal({ channel: channelOf({ kind: 'dm', name: 'Hank' }) });
    expect(screen.getByText('基本')).toBeInTheDocument();
    expect(screen.queryByText('通知')).not.toBeInTheDocument();
    expect(screen.queryByText('Project')).not.toBeInTheDocument();
  });

  it('通知 mode 即点即存(putNotificationSetting)', async () => {
    vi.mocked(api.putNotificationSetting).mockResolvedValue({ channel_id: 'ch1', member_id: 'mem_owner', mode: 'mute' });
    renderModal({ currentMode: 'all' });
    fireEvent.click(screen.getByRole('radio', { name: '静音' }));
    await waitFor(() => expect(api.putNotificationSetting).toHaveBeenCalledWith('ch1', 'mute'));
  });

  it('阈值改动 → 保存仅提交差异字段', async () => {
    vi.mocked(api.patchChannel).mockResolvedValue(channelOf());
    const { onClose } = renderModal();
    // 改重评估等待 5 → 10
    fireEvent.change(screen.getByLabelText('重评估等待'), { target: { value: '10' } });
    fireEvent.click(screen.getByRole('button', { name: /保存/ }));
    await waitFor(() => expect(api.patchChannel).toHaveBeenCalledWith('ch1', { held_reeval_min: 10 }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('无改动 → 保存不发 PATCH,直接关闭', async () => {
    const { onClose } = renderModal();
    fireEvent.click(screen.getByRole('button', { name: /保存/ }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(api.patchChannel).not.toHaveBeenCalled();
  });

  it('可见性切私有 → 保存提交 is_private', async () => {
    vi.mocked(api.patchChannel).mockResolvedValue(channelOf());
    renderModal();
    fireEvent.click(screen.getByRole('button', { name: '私有' }));
    fireEvent.click(screen.getByRole('button', { name: /保存/ }));
    await waitFor(() => expect(api.patchChannel).toHaveBeenCalledWith('ch1', { is_private: true }));
  });

  // ---- B-M6-2：编排组（decomp_mode / decomp_node_limit / orch_escalation）
  it('渲染编排组 + 三控件', () => {
    renderModal();
    expect(screen.getByTestId('cs-orchestration')).toBeInTheDocument();
    expect(screen.getByRole('radiogroup', { name: '拆解模式' })).toBeInTheDocument();
    expect(screen.getByLabelText('单次提案节点上限')).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: 'Orchestrator 升级接线' })).toBeInTheDocument();
  });

  it('DM 频道：无编排组（DM 不承载任务/拆解）', () => {
    renderModal({ channel: channelOf({ kind: 'dm', name: 'Hank' }) });
    expect(screen.queryByTestId('cs-orchestration')).not.toBeInTheDocument();
  });

  it('切直落 + 改节点上限 → 保存提交 decomp_mode/decomp_node_limit', async () => {
    vi.mocked(api.patchChannel).mockResolvedValue(channelOf());
    renderModal();
    fireEvent.click(screen.getByRole('radio', { name: '直落' }));
    fireEvent.change(screen.getByLabelText('单次提案节点上限'), { target: { value: '20' } });
    fireEvent.click(screen.getByRole('button', { name: /保存/ }));
    await waitFor(() =>
      expect(api.patchChannel).toHaveBeenCalledWith('ch1', { decomp_mode: 'direct', decomp_node_limit: 20 }),
    );
  });

  it('节点上限越界（>50）→ 不提交该字段', async () => {
    vi.mocked(api.patchChannel).mockResolvedValue(channelOf());
    const { onClose } = renderModal();
    fireEvent.change(screen.getByLabelText('单次提案节点上限'), { target: { value: '99' } });
    fireEvent.click(screen.getByRole('button', { name: /保存/ }));
    // 越界字段不进 patch；无其它改动 → 直接关闭不发 PATCH
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(api.patchChannel).not.toHaveBeenCalled();
  });
});
