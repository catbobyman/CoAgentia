// M7b 画布页签成本汇总 chip：GET /usage?level=canvas&ref=<channel_id> → Σ token 合计 + 覆盖率 N/M；
// 永不折算货币；未就绪不占位（不闪烁）。
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return { ...actual, api: { ...actual.api, usage: vi.fn() } };
});

import type { UsageReport } from '@coagentia/contracts-ts';

import { api } from '../api';
import { CanvasUsageChip } from './UsageChip';

function renderChip() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <CanvasUsageChip channelId="ch_1" />
    </QueryClientProvider>,
  );
}

describe('CanvasUsageChip', () => {
  beforeEach(() => vi.clearAllMocks());

  it('拉取 canvas 层汇总 → Σ 合计 + 覆盖率；ref=channel_id', async () => {
    const report: UsageReport = {
      level: 'canvas', ref: 'ch_1',
      usage: { input_tokens: 3000, output_tokens: 1000, cache_read_tokens: 0, cache_write_tokens: 0 },
      tasks_reporting: { reporting: 4, total: 5 },
    };
    vi.mocked(api.usage).mockResolvedValue(report);
    renderChip();
    await waitFor(() => expect(screen.getByTestId('canvas-usage-chip')).toBeInTheDocument());
    expect(screen.getByTestId('canvas-usage-chip')).toHaveTextContent('Σ 4.0k tok');
    expect(screen.getByTestId('canvas-usage-coverage')).toHaveTextContent('4/5 上报');
    expect(api.usage).toHaveBeenCalledWith('canvas', 'ch_1', false);
    // 永不货币。
    expect(screen.getByTestId('canvas-usage-chip').textContent).not.toMatch(/[$￥€]/);
  });

  it('未就绪（数据未到）不渲染占位（避免闪烁）', () => {
    vi.mocked(api.usage).mockReturnValue(new Promise(() => {})); // 永挂起
    renderChip();
    expect(screen.queryByTestId('canvas-usage-chip')).not.toBeInTheDocument();
  });

  it('空集 usage 全 0 → Σ 0 tok，0/0 上报（诚实）', async () => {
    vi.mocked(api.usage).mockResolvedValue({
      level: 'canvas', ref: 'ch_1',
      usage: { input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0 },
      tasks_reporting: { reporting: 0, total: 0 },
    });
    renderChip();
    await waitFor(() => expect(screen.getByTestId('canvas-usage-chip')).toHaveTextContent('Σ 0 tok'));
    expect(screen.getByTestId('canvas-usage-coverage')).toHaveTextContent('0/0 上报');
  });
});
