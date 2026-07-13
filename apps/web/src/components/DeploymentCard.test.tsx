// M7b 部署卡：token 小结行（Σ 四字段 + 覆盖率 N/M，永不货币）/ 结果行（URL·耗时·退出码，失败态色）/
// 日志实时跟随 + 向上滚动暂停 + 「↓ 跟随」胶囊 / deploy_log 订阅时机（打开 sub、卸载 unsub）/ 历史翻页。
// 运行:pnpm -F @coagentia/web test
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return { ...actual, api: { ...actual.api, getDeployment: vi.fn(), deploymentLog: vi.fn() } };
});
vi.mock('../data/wsUplink', () => ({
  subscribeDeployLog: vi.fn(),
  unsubscribeDeployLog: vi.fn(),
}));

import type { DeploymentLogPage, DeploymentPublic } from '@coagentia/contracts-ts';

import { api } from '../api';
import { qk } from '../lib/queryKeys';
import { EMPTY_DEPLOY_LOG, type DeployLogState } from '../data/deployLog';
import { subscribeDeployLog, unsubscribeDeployLog } from '../data/wsUplink';
import {
  DeploymentCard,
  deployDurationSec,
  fmtDuration,
  fmtTokens,
  isTerminal,
  scrolledToBottom,
  sumTokens,
} from './DeploymentCard';

const DEP_ID = 'deploy_1';

function deployment(over: Partial<DeploymentPublic> = {}): DeploymentPublic {
  return {
    id: DEP_ID, workspace_id: 'ws_1', project_id: 'project_1',
    triggered_by_member_id: 'mem_owner', branch: 'main', command: 'npm run deploy',
    status: 'running', ...over,
  };
}

function makeQc() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
}

function renderCard(dep: DeploymentPublic, seedLog?: DeployLogState) {
  const qc = makeQc();
  qc.setQueryData(qk.deployment(DEP_ID), dep);
  vi.mocked(api.getDeployment).mockResolvedValue(dep);
  if (seedLog) qc.setQueryData(qk.deploymentLog(DEP_ID), seedLog);
  render(
    <QueryClientProvider client={qc}>
      <DeploymentCard deploymentId={DEP_ID} />
    </QueryClientProvider>,
  );
  return { qc };
}

describe('DeploymentCard 纯函数', () => {
  it('sumTokens 四字段合计；fmtTokens ≥1000 缩略 k', () => {
    expect(sumTokens({ input_tokens: 100, output_tokens: 200, cache_read_tokens: 30, cache_write_tokens: 70 })).toBe(400);
    expect(sumTokens(undefined)).toBe(0);
    expect(fmtTokens(999)).toBe('999');
    expect(fmtTokens(1500)).toBe('1.5k');
  });
  it('deployDurationSec / fmtDuration', () => {
    expect(deployDurationSec('2026-07-13T00:00:00Z', '2026-07-13T00:01:05Z')).toBe(65);
    expect(deployDurationSec(undefined, '2026-07-13T00:01:05Z')).toBeNull();
    expect(deployDurationSec('2026-07-13T00:01:05Z', '2026-07-13T00:00:00Z')).toBeNull(); // 负值夹掉
    expect(fmtDuration(65)).toBe('1m05s');
    expect(fmtDuration(42)).toBe('42s');
  });
  it('scrolledToBottom 阈值内视同贴底 / isTerminal', () => {
    expect(scrolledToBottom({ scrollTop: 800, scrollHeight: 1000, clientHeight: 200 })).toBe(true);
    expect(scrolledToBottom({ scrollTop: 0, scrollHeight: 1000, clientHeight: 200 })).toBe(false);
    expect(isTerminal('success')).toBe(true);
    expect(isTerminal('running')).toBe(false);
  });
});

describe('DeploymentCard token 小结行 + 结果行', () => {
  afterEach(() => cleanup());

  it('token_summary → Σ 合计 + 覆盖率 N/M（诚实标注，永不货币）', () => {
    renderCard(deployment({
      status: 'success', exit_code: 0,
      token_summary: {
        usage: { input_tokens: 1200, output_tokens: 800, cache_read_tokens: 0, cache_write_tokens: 0 },
        tasks_reporting: { reporting: 2, total: 3 },
      },
    }));
    const row = screen.getByTestId('deployment-token-summary');
    expect(row).toHaveTextContent('Σ 2.0k tok');
    expect(screen.getByTestId('deployment-tasks-reporting')).toHaveTextContent('上报 2/3');
    // 永不出现货币符号。
    expect(row.textContent).not.toMatch(/[$￥€]/);
  });

  it('终态结果行：URL 链接 + 耗时 + 退出码；失败态标 data-status', () => {
    renderCard(deployment({
      status: 'failed', exit_code: 1, url: null,
      started_at: '2026-07-13T00:00:00Z', finished_at: '2026-07-13T00:00:30Z',
    }));
    const result = screen.getByTestId('deployment-result');
    expect(result).toHaveAttribute('data-status', 'failed');
    expect(result).toHaveTextContent('耗时 30s');
    expect(screen.getByTestId('deployment-exit-code')).toHaveTextContent('退出码 1');
  });

  it('success 带 URL → 渲染外链', () => {
    renderCard(deployment({
      status: 'success', exit_code: 0, url: 'https://app.example.com',
      started_at: '2026-07-13T00:00:00Z', finished_at: '2026-07-13T00:00:30Z',
    }));
    const link = screen.getByRole('link', { name: /app\.example\.com/ });
    expect(link).toHaveAttribute('href', 'https://app.example.com');
  });
});

describe('DeploymentCard 日志订阅时机 + 翻页', () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => cleanup());

  it('running 自动展开日志 → 打开即 subscribeDeployLog；卸载 → unsubscribeDeployLog', async () => {
    vi.mocked(api.deploymentLog).mockResolvedValue({ lines: [], next_after: null, truncated: false });
    const { qc } = renderCard(deployment({ status: 'running' }));
    await waitFor(() => expect(subscribeDeployLog).toHaveBeenCalledWith(DEP_ID));
    cleanup();
    expect(unsubscribeDeployLog).toHaveBeenCalledWith(DEP_ID);
    void qc;
  });

  it('打开日志拉首页历史；next_after → 「加载更多」按 after 游标续翻并追加', async () => {
    vi.mocked(api.deploymentLog)
      .mockResolvedValueOnce({ lines: ['h1', 'h2'], next_after: 2, truncated: false } as DeploymentLogPage)
      .mockResolvedValueOnce({ lines: ['h3'], next_after: null, truncated: false } as DeploymentLogPage);
    renderCard(deployment({ status: 'running' }));

    // 首页无 after。
    await waitFor(() => expect(api.deploymentLog).toHaveBeenCalledWith(DEP_ID, undefined));
    await screen.findByText(/h1/);
    const more = await screen.findByTestId('deployment-log-more');
    await act(async () => { fireEvent.click(more); });

    await waitFor(() => expect(api.deploymentLog).toHaveBeenLastCalledWith(DEP_ID, 2));
    await waitFor(() => expect(screen.getByTestId('deployment-log').textContent).toContain('h3'));
    // 全部翻完 → 按钮消失。
    expect(screen.queryByTestId('deployment-log-more')).not.toBeInTheDocument();
  });

  it('终态部署默认折叠日志（不逐卡订阅），展开后才订阅', async () => {
    vi.mocked(api.deploymentLog).mockResolvedValue({ lines: [], next_after: null, truncated: false });
    renderCard(deployment({ status: 'success', exit_code: 0 }));
    expect(subscribeDeployLog).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('deployment-log-toggle'));
    await waitFor(() => expect(subscribeDeployLog).toHaveBeenCalledWith(DEP_ID));
  });
});

describe('DeploymentCard 日志跟随与暂停', () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => cleanup());

  it('向上滚动 → 暂停跟随，出现「↓ 跟随」胶囊；点胶囊/滚回底 → 恢复跟随', async () => {
    vi.mocked(api.deploymentLog).mockResolvedValue({ lines: [], next_after: null, truncated: false });
    renderCard(
      deployment({ status: 'running' }),
      { ...EMPTY_DEPLOY_LOG, lines: ['a', 'b', 'c'] },
    );
    const logEl = await screen.findByTestId('deployment-log');
    Object.defineProperty(logEl, 'scrollHeight', { value: 1000, configurable: true });
    Object.defineProperty(logEl, 'clientHeight', { value: 200, configurable: true });
    Object.defineProperty(logEl, 'scrollTop', { value: 0, writable: true, configurable: true });

    // 默认跟随中：无胶囊。
    expect(screen.queryByTestId('deployment-follow-pill')).not.toBeInTheDocument();

    // 向上滚（scrollTop=0 远离底）→ 暂停 + 胶囊出现。
    act(() => { fireEvent.scroll(logEl); });
    expect(screen.getByTestId('deployment-follow-pill')).toBeInTheDocument();

    // 点胶囊 → 恢复跟随（滚到底）+ 胶囊消失。
    act(() => { fireEvent.click(screen.getByTestId('deployment-follow-pill')); });
    expect(screen.queryByTestId('deployment-follow-pill')).not.toBeInTheDocument();

    // 手动滚回底部再触发 scroll → 保持跟随（无胶囊）。
    (logEl as unknown as { scrollTop: number }).scrollTop = 800;
    act(() => { fireEvent.scroll(logEl); });
    expect(screen.queryByTestId('deployment-follow-pill')).not.toBeInTheDocument();
  });
});
