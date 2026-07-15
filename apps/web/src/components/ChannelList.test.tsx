// B-M8-3 侧栏「新建频道」死壳补齐：点击「新建频道」→ 打开 NewChannelModal（role=dialog）。
// ChannelList 纯 props 驱动；弹窗内 useCreateChannel/useToast 需 QueryClient + ToastProvider。
// 运行:pnpm -F @coagentia/web test
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { ChannelPublic } from '@coagentia/contracts-ts';

import { ToastProvider } from './Toast';
import { ChannelList, type ChannelListProps } from './ChannelList';

function renderList(over: Partial<ChannelListProps> = {}) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const props: ChannelListProps = {
    channels: [],
    activeChannelId: undefined,
    unreadCount: () => 0,
    presenceOf: () => undefined,
    dmPeer: () => undefined,
    onSelectChannel: vi.fn(),
    ...over,
  };
  render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <ChannelList {...props} />
      </ToastProvider>
    </QueryClientProvider>,
  );
  return props;
}

describe('ChannelList 新建频道入口', () => {
  it('点击「新建频道」打开 NewChannelModal', () => {
    renderList();
    expect(screen.queryByRole('dialog', { name: '新建频道' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '新建频道' }));
    expect(screen.getByRole('dialog', { name: '新建频道' })).toBeInTheDocument();
    expect(screen.getByLabelText('名字')).toBeInTheDocument();
  });

  it('渲染频道列表（回归：既有频道行不受影响）', () => {
    const ch: ChannelPublic = {
      id: 'ch1', kind: 'channel', name: 'build', workspace_id: 'ws1',
      created_at: '2026-07-14T00:00:00Z',
    };
    renderList({ channels: [ch] });
    expect(screen.getByText('build')).toBeInTheDocument();
  });
});
