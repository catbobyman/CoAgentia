// B-M8-3 新建频道弹窗：字段渲染 / 提交携 ChannelCreate 体（member_ids 空 + is_private）/ 空名禁提交 /
// NAME_TAKEN 就地报错不关窗。照 CreateAgentModal.test.tsx 的 QueryClient + vi.mock('../api') 范式。
// 运行:pnpm -F @coagentia/web test
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    api: { ...actual.api, createChannel: vi.fn() },
  };
});

import type { ChannelPublic } from '@coagentia/contracts-ts';

import { api, ApiError } from '../api';
import { ToastProvider } from './Toast';
import { NewChannelModal } from './NewChannelModal';

const CHANNEL: ChannelPublic = {
  id: 'ch_new', kind: 'channel', name: 'design', workspace_id: 'ws_1',
  created_at: '2026-07-14T00:00:00Z', is_private: false,
};

function renderModal(props?: { onClose?: () => void; onCreated?: (ch: ChannelPublic) => void }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <NewChannelModal onClose={props?.onClose ?? (() => {})} onCreated={props?.onCreated} />
      </ToastProvider>
    </QueryClientProvider>,
  );
  return qc;
}

describe('NewChannelModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.createChannel).mockResolvedValue(CHANNEL);
  });

  it('渲染名字 / 说明 / 私有频道字段', () => {
    renderModal();
    expect(screen.getByRole('dialog', { name: '新建频道' })).toBeInTheDocument();
    expect(screen.getByLabelText('名字')).toBeInTheDocument();
    expect(screen.getByLabelText('说明（可选）')).toBeInTheDocument();
    expect(screen.getByLabelText('私有频道')).toBeInTheDocument();
  });

  it('空名 → 创建按钮 disabled', () => {
    renderModal();
    expect(screen.getByRole('button', { name: '创建频道' })).toBeDisabled();
  });

  it('填名后提交 → POST /channels 携 name + is_private + 空 member_ids；onCreated 回传新频道', async () => {
    const onCreated = vi.fn();
    const onClose = vi.fn();
    renderModal({ onCreated, onClose });
    fireEvent.change(screen.getByLabelText('名字'), { target: { value: 'design' } });
    fireEvent.click(screen.getByLabelText('私有频道'));
    const submit = screen.getByRole('button', { name: '创建频道' });
    expect(submit).not.toBeDisabled();
    fireEvent.click(submit);
    await waitFor(() => expect(api.createChannel).toHaveBeenCalledWith({
      name: 'design', is_private: true, member_ids: [],
    }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(CHANNEL));
    expect(onClose).toHaveBeenCalled();
  });

  it('有说明时携 description；空说明不下发该字段', async () => {
    renderModal();
    fireEvent.change(screen.getByLabelText('名字'), { target: { value: 'design' } });
    fireEvent.change(screen.getByLabelText('说明（可选）'), { target: { value: '设计讨论' } });
    fireEvent.click(screen.getByRole('button', { name: '创建频道' }));
    await waitFor(() => expect(api.createChannel).toHaveBeenCalledWith({
      name: 'design', description: '设计讨论', is_private: false, member_ids: [],
    }));
  });

  it('NAME_TAKEN 就地报错不关窗', async () => {
    vi.mocked(api.createChannel).mockRejectedValue(
      new ApiError(409, 'NAME_TAKEN', '频道名 design 已被占用'),
    );
    const onClose = vi.fn();
    renderModal({ onClose });
    fireEvent.change(screen.getByLabelText('名字'), { target: { value: 'design' } });
    fireEvent.click(screen.getByRole('button', { name: '创建频道' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('已被占用');
    expect(screen.getByRole('dialog', { name: '新建频道' })).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });
});
