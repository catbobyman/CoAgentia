// M7（B-M7-1）预览面板行为测试：[预览] 按钮亮灭条件 + onOpen、回收倒计时推导、失败日志尾 20 行、
// 顶条三态（starting/running iframe/failed）、心跳 60s 重发 POST、[重试] 链路。
// 照 HeldDraftCard.test.tsx 的 vi.mock('../api') + QueryClientProvider + fake timers 体例。
// 运行:pnpm -F @coagentia/web test
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    api: { ...actual.api, startPreview: vi.fn(), getPreview: vi.fn(), stopPreview: vi.fn() },
  };
});

import type { PreviewSessionPublic } from '@coagentia/contracts-ts';

import { api } from '../api';
import { qk } from '../lib/queryKeys';
import { ToastProvider } from './Toast';
import {
  PreviewButton, PreviewPanel, canPreview, lastLines, previewCountdownSeconds,
} from './PreviewPanel';

const TASK_ID = 'task_1';

function session(over: Partial<PreviewSessionPublic> = {}): PreviewSessionPublic {
  return {
    id: 'prev_1', workspace_id: 'ws_1', task_id: TASK_ID, worktree_id: 'wt_1',
    status: 'starting', started_at: '2026-07-13T00:00:00Z',
    ...over,
  };
}

function makeQc() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
}

function renderPanel(seed: PreviewSessionPublic, idleMin = 30) {
  const qc = makeQc();
  // 播种缓存直接驱动目标态（模拟 POST/WS 反流后的稳态）；mock startPreview 回同态使 mount POST 不改态。
  qc.setQueryData(qk.preview(TASK_ID), seed);
  vi.mocked(api.startPreview).mockResolvedValue(seed);
  render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <PreviewPanel target={{ taskId: TASK_ID, taskNumber: 7, idleMin }} onClose={() => {}} />
      </ToastProvider>
    </QueryClientProvider>,
  );
  return { qc };
}

describe('canPreview（[预览] 按钮亮灭条件）', () => {
  it('有 worktree 且 dev_command 非空才亮', () => {
    expect(canPreview(true, 'npm run dev')).toBe(true);
    expect(canPreview(false, 'npm run dev')).toBe(false); // 无 worktree
    expect(canPreview(true, undefined)).toBe(false); // 未配 dev_command
    expect(canPreview(true, null)).toBe(false);
    expect(canPreview(true, '   ')).toBe(false); // 空白视同未配
  });
});

describe('PreviewButton', () => {
  it('亮时点击触发 onOpen；灭时置灰不触发', () => {
    const onOpen = vi.fn();
    const { rerender } = render(
      <PreviewButton hasWorktree devCommand="npm run dev" onOpen={onOpen} />,
    );
    const btn = screen.getByRole('button', { name: /预览/ });
    expect(btn).not.toBeDisabled();
    fireEvent.click(btn);
    expect(onOpen).toHaveBeenCalledTimes(1);

    rerender(<PreviewButton hasWorktree={false} devCommand="npm run dev" onOpen={onOpen} />);
    expect(screen.getByRole('button', { name: /预览/ })).toBeDisabled();
  });
});

describe('previewCountdownSeconds（回收倒计时客户端推导）', () => {
  const NOW = new Date('2026-07-13T00:00:00Z').getTime();
  it('running：deadline = last_active_at + idleMin 分钟，剩余 = deadline - now', () => {
    const s = session({ status: 'running', last_active_at: '2026-07-13T00:00:00Z' });
    expect(previewCountdownSeconds(s, 30, NOW)).toBe(1800); // 30min
    expect(previewCountdownSeconds(s, 30, NOW + 1_770_000)).toBe(30); // 剩 30s
    expect(previewCountdownSeconds(s, 30, NOW + 2_000_000)).toBe(0); // 已过期夹到 0
  });
  it('无 last_active_at 或非活跃态 → null（不显示）', () => {
    expect(previewCountdownSeconds(session({ status: 'running' }), 30, NOW)).toBeNull();
    expect(previewCountdownSeconds(
      session({ status: 'failed', last_active_at: '2026-07-13T00:00:00Z' }), 30, NOW,
    )).toBeNull();
  });
});

describe('lastLines（失败日志尾 20 行）', () => {
  it('只保留尾 n 行', () => {
    const text = Array.from({ length: 25 }, (_, i) => `line ${i + 1}`).join('\n');
    const tail = lastLines(text, 20);
    expect(tail.split('\n')).toHaveLength(20);
    expect(tail).toContain('line 25');
    expect(tail).not.toContain('line 5\n'); // 前 5 行被裁
  });
  it('空/未定义 → 空串', () => {
    expect(lastLines(null, 20)).toBe('');
    expect(lastLines(undefined, 20)).toBe('');
  });
});

describe('PreviewPanel 顶条三态', () => {
  afterEach(() => cleanup());

  it('starting：顶条显「启动中…（健康检查）」', async () => {
    renderPanel(session({ status: 'starting' }));
    expect((await screen.findAllByText(/启动中…（健康检查）/)).length).toBeGreaterThan(0);
    expect(screen.queryByTitle('任务 #7 预览')).not.toBeInTheDocument(); // 未就绪不挂 iframe
  });

  it('running：加载 iframe，src = http://127.0.0.1:{port}', async () => {
    renderPanel(session({ status: 'running', port: 5173 }));
    const frame = await screen.findByTitle('任务 #7 预览');
    expect(frame).toHaveAttribute('src', 'http://127.0.0.1:5173');
    expect(screen.getByText(/运行中/)).toBeInTheDocument();
  });

  it('failed：显 fail_log_tail 尾 20 行 + [重试]', async () => {
    const log = Array.from({ length: 30 }, (_, i) => `err ${i + 1}`).join('\n');
    renderPanel(session({ status: 'failed', fail_log_tail: log }));
    expect(await screen.findByText(/启动失败/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /重试/ })).toBeInTheDocument();
    const pre = screen.getByRole('log');
    expect(pre.textContent?.split('\n')).toHaveLength(20); // 尾 20 行
    expect(pre.textContent).toContain('err 30');
    expect(pre.textContent).not.toContain('err 5\n');
  });

  it('[重试] = 再次 POST（ensure 重建）', async () => {
    renderPanel(session({ status: 'failed', fail_log_tail: 'boom' }));
    await screen.findByText(/启动失败/);
    const before = vi.mocked(api.startPreview).mock.calls.length;
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /重试/ }));
    });
    const calls = vi.mocked(api.startPreview).mock.calls;
    expect(calls.length).toBeGreaterThan(before);
    expect(calls[calls.length - 1]![0]).toBe(TASK_ID);
  });
});

describe('PreviewPanel 心跳（60s 重发 POST）', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-07-13T00:00:00Z'));
  });
  afterEach(() => {
    vi.useRealTimers();
    cleanup();
  });

  it('running 面板每 60s 重发 POST 推进 last_active_at；关闭即停', () => {
    const qc = makeQc();
    qc.setQueryData(qk.preview(TASK_ID), session({ status: 'running', port: 5173 }));
    vi.mocked(api.startPreview).mockResolvedValue(session({ status: 'running', port: 5173 }));
    const { unmount } = render(
      <QueryClientProvider client={qc}>
        <ToastProvider>
          <PreviewPanel target={{ taskId: TASK_ID, taskNumber: 7, idleMin: 30 }} onClose={() => {}} />
        </ToastProvider>
      </QueryClientProvider>,
    );

    const before = vi.mocked(api.startPreview).mock.calls.length; // mount POST 已发
    act(() => void vi.advanceTimersByTime(60_000));
    expect(vi.mocked(api.startPreview).mock.calls.length).toBe(before + 1); // 心跳一拍

    act(() => void vi.advanceTimersByTime(60_000));
    expect(vi.mocked(api.startPreview).mock.calls.length).toBe(before + 2);

    // 关闭 → 定时器清理，不再重发。
    const stopped = vi.mocked(api.startPreview).mock.calls.length;
    unmount();
    act(() => void vi.advanceTimersByTime(180_000));
    expect(vi.mocked(api.startPreview).mock.calls.length).toBe(stopped);
  });

  it('failed 非活跃态不心跳（避免自动重建）', () => {
    const qc = makeQc();
    qc.setQueryData(qk.preview(TASK_ID), session({ status: 'failed', fail_log_tail: 'boom' }));
    vi.mocked(api.startPreview).mockResolvedValue(session({ status: 'failed', fail_log_tail: 'boom' }));
    render(
      <QueryClientProvider client={qc}>
        <ToastProvider>
          <PreviewPanel target={{ taskId: TASK_ID, taskNumber: 7, idleMin: 30 }} onClose={() => {}} />
        </ToastProvider>
      </QueryClientProvider>,
    );
    const before = vi.mocked(api.startPreview).mock.calls.length;
    act(() => void vi.advanceTimersByTime(180_000));
    expect(vi.mocked(api.startPreview).mock.calls.length).toBe(before); // 无心跳
  });
});

describe('PreviewPanel 回收倒计时显示', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-07-13T00:00:00Z'));
  });
  afterEach(() => {
    vi.useRealTimers();
    cleanup();
  });

  it('running 显示剩余（idleMin=1 → 1:00 后回收），每秒递减', () => {
    const qc = makeQc();
    const s = session({ status: 'running', port: 5173, last_active_at: '2026-07-13T00:00:00Z' });
    qc.setQueryData(qk.preview(TASK_ID), s);
    vi.mocked(api.startPreview).mockResolvedValue(s);
    render(
      <QueryClientProvider client={qc}>
        <ToastProvider>
          <PreviewPanel target={{ taskId: TASK_ID, taskNumber: 7, idleMin: 1 }} onClose={() => {}} />
        </ToastProvider>
      </QueryClientProvider>,
    );
    expect(screen.getByTestId('preview-countdown')).toHaveTextContent('1:00 后回收');
    act(() => void vi.advanceTimersByTime(2000));
    expect(screen.getByTestId('preview-countdown')).toHaveTextContent('0:58 后回收');
  });
});
