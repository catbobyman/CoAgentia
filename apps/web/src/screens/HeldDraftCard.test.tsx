// M4b 被扣草稿卡(HeldDraftCard):三键触发正确 mutation、本地读秒倒计时、终态回执 + 三键隐藏、
// 升级横条、未读清单跳转 + 截断计数、409 HELD_DRAFT_RESOLVED 以最新态静默刷新。
// 照 RemindersTab.test.tsx 的 vi.mock('../api') + QueryClientProvider 范式。
// 运行:pnpm -F @coagentia/web test
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    api: {
      ...actual.api,
      heldDrafts: vi.fn(),
      releaseHeldDraft: vi.fn(),
      discardHeldDraft: vi.fn(),
      reevaluateHeldDraft: vi.fn(),
    },
  };
});

import type { HeldDraftPublic } from '@coagentia/contracts-ts';

import { api, ApiError } from '../api';
import { qk } from '../lib/queryKeys';
import { useHeldDrafts } from '../data/queries';
import { ToastProvider, Toaster } from '../components/Toast';
import { HeldDraftCard, HeldDraftList } from './HeldDraftCard';

const CH = 'ch_build';

function draft(over: Partial<HeldDraftPublic> = {}): HeldDraftPublic {
  return {
    id: 'hd_1',
    agent_member_id: 'mem_agent',
    channel_id: CH,
    created_at: '2026-07-10T00:00:00Z',
    draft_body: '这是一条被 freshness 门扣住的草稿正文。',
    next_reeval_at: '2026-07-10T00:01:00Z',
    reasons: { total_unread: 2, unread_message_ids: ['msg_aaa111', 'msg_bbb222'] },
    status: 'held',
    workspace_id: 'ws_1',
    ...over,
  };
}

function makeQc() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
}

function renderCard(
  d: HeldDraftPublic,
  opts: { canResolve?: boolean; onLocate?: (id: string) => void; qc?: QueryClient } = {},
) {
  const qc = opts.qc ?? makeQc();
  render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <HeldDraftCard
          draft={d}
          channelId={CH}
          memberById={{}}
          canResolve={opts.canResolve ?? true}
          onLocateMessage={opts.onLocate}
        />
      </ToastProvider>
    </QueryClientProvider>,
  );
  return { qc };
}

describe('HeldDraftCard 三键', () => {
  // 三键共享 pending → 首次点击即置灰其它;各键独立 render 逐一验证。
  it.each([
    ['放行', 'releaseHeldDraft'],
    ['重评估', 'reevaluateHeldDraft'],
    ['丢弃', 'discardHeldDraft'],
  ] as const)('点「%s」触发 api.%s(hd_1)', async (label, method) => {
    vi.mocked(api[method]).mockResolvedValue({} as never);
    renderCard(draft());

    fireEvent.click(screen.getByRole('button', { name: label }));

    await waitFor(() => expect(api[method]).toHaveBeenCalledWith('hd_1'));
    cleanup();
  });

  it('canResolve=false(非人类)则三键不渲染', () => {
    renderCard(draft(), { canResolve: false });
    expect(screen.queryByRole('button', { name: '放行' })).not.toBeInTheDocument();
  });
});

describe('HeldDraftCard 倒计时(本地读秒)', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-07-10T00:00:00Z'));
  });
  afterEach(() => vi.useRealTimers());

  it('每秒递减,到点显示「重评估中」', () => {
    renderCard(draft({ next_reeval_at: '2026-07-10T00:00:03Z' })); // 3s 后
    expect(screen.getByTestId('held-countdown')).toHaveTextContent('0:03 后重评估');

    act(() => void vi.advanceTimersByTime(2000));
    expect(screen.getByTestId('held-countdown')).toHaveTextContent('0:01 后重评估');

    act(() => void vi.advanceTimersByTime(2000));
    expect(screen.getByTestId('held-countdown')).toHaveTextContent('重评估中');
  });
});

describe('HeldDraftCard 终态回执 / 升级 / 未读清单', () => {
  it('released 终态:显示回执并隐藏三键', () => {
    renderCard(draft({ status: 'released', resolution: 'released', resolved_at: '2026-07-10T00:05:00Z' }));
    expect(screen.getByText(/已放行/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '放行' })).not.toBeInTheDocument();
    expect(screen.queryByTestId('held-countdown')).not.toBeInTheDocument();
  });

  it('escalated_at 非空:显示升级横条', () => {
    renderCard(draft({ escalated_at: '2026-07-10T00:04:00Z' }));
    expect(screen.getByText(/已升级喊人/)).toBeInTheDocument();
  });

  it('未读清单可点跳转 + total_unread>展示数时显示「还有 N 条未读」', () => {
    const onLocate = vi.fn();
    renderCard(
      draft({ reasons: { total_unread: 5, unread_message_ids: ['msg_aaa111', 'msg_bbb222'] } }),
      { onLocate },
    );
    fireEvent.click(screen.getByRole('button', { name: /aaa111/ }));
    expect(onLocate).toHaveBeenCalledWith('msg_aaa111');
    expect(screen.getByText('还有 3 条未读')).toBeInTheDocument();
  });

  it('长草稿默认折叠,「展开全文」后显示全文', () => {
    const longBody = 'x'.repeat(400);
    renderCard(draft({ draft_body: longBody }));
    expect(screen.getByText(/x…$/)).toBeInTheDocument(); // 折叠省略号
    fireEvent.click(screen.getByRole('button', { name: '展开全文' }));
    expect(screen.getByRole('button', { name: '收起' })).toBeInTheDocument();
  });
});

describe('HeldDraftCard 409 静默收敛(经查询缓存刷新)', () => {
  function Harness({ onLocate }: { onLocate?: (id: string) => void }) {
    const q = useHeldDrafts(CH);
    return (
      <HeldDraftList
        drafts={q.data ?? []}
        channelId={CH}
        memberById={{}}
        canResolve
        onLocateMessage={onLocate}
      />
    );
  }

  it('release 撞 409 HELD_DRAFT_RESOLVED → 以 details.held_draft 收敛为终态回执(不弹错)', async () => {
    const qc = makeQc();
    vi.mocked(api.heldDrafts).mockResolvedValue([draft()]);
    qc.setQueryData<HeldDraftPublic[]>(qk.heldDrafts(CH), [draft()]);
    // 409:服务端已被别处终解,error.details 携带最新 held_draft(discarded 终态)。
    vi.mocked(api.releaseHeldDraft).mockRejectedValue(
      new ApiError(409, 'HELD_DRAFT_RESOLVED', '草稿已被终解', {
        held_draft: draft({ status: 'discarded', resolution: 'discarded' }),
      }),
    );

    render(
      <QueryClientProvider client={qc}>
        <ToastProvider>
          <Harness />
        </ToastProvider>
      </QueryClientProvider>,
    );

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '放行' }));
    });

    // 收敛为终态回执:三键消失,展示「已丢弃」。
    expect(await screen.findByText(/已丢弃/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '放行' })).not.toBeInTheDocument();
  });

  it('非 409 错误(503 daemon 离线)→ 弹 error toast(评审 #3)', async () => {
    const qc = makeQc();
    vi.mocked(api.heldDrafts).mockResolvedValue([draft()]);
    qc.setQueryData<HeldDraftPublic[]>(qk.heldDrafts(CH), [draft()]);
    vi.mocked(api.discardHeldDraft).mockRejectedValue(
      new ApiError(503, 'DAEMON_OFFLINE', 'daemon 离线'),
    );

    render(
      <QueryClientProvider client={qc}>
        <ToastProvider>
          <Harness />
          <Toaster />
        </ToastProvider>
      </QueryClientProvider>,
    );

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '丢弃' }));
    });

    // 弹 error toast(不再静默);卡片仍在(未终解)。
    expect(await screen.findByText(/daemon 离线/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '丢弃' })).toBeInTheDocument();
  });
});
