// O8 画布可见面（M8b B-M8-2 ②③）：汇总节点 badge / partial 放行档 badge、节点检视器 upstream_policy
// 展示与人类改档（patch_node）。运行:pnpm -F @coagentia/web test
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return { ...actual, api: { ...actual.api, patchCanvasNode: vi.fn() } };
});

import type { CanvasNodePublic } from '@coagentia/contracts-ts';

import { api } from '../api';
import { NodeInspector, SystemNodeCard, TaskNodeCard, type SystemNodeData, type TaskNodeData } from './CanvasTab';

describe('O8 节点 badge（②）', () => {
  it('汇总节点 → 「汇总」badge；partial 档 → 「partial」badge', () => {
    const data: TaskNodeData = {
      kind: 'agent', number: 9, title: '汇总交付', status: 'in_progress',
      blocked: false, selected: false, isSummary: true, upstreamPolicy: 'partial',
    };
    render(<TaskNodeCard data={data} />);
    expect(screen.getByTestId('node-summary-badge')).toHaveTextContent('汇总');
    expect(screen.getByTestId('node-partial-badge')).toHaveTextContent('partial');
  });

  it('strict 档普通节点 → 无 badge', () => {
    const data: TaskNodeData = {
      kind: 'agent', number: 3, title: '实现', status: 'todo',
      blocked: false, selected: false, upstreamPolicy: 'strict',
    };
    render(<TaskNodeCard data={data} />);
    expect(screen.queryByTestId('node-summary-badge')).toBeNull();
    expect(screen.queryByTestId('node-partial-badge')).toBeNull();
  });

  it('系统节点 partial 档 → 「partial」badge', () => {
    const data: SystemNodeData = {
      kind: 'system', action: 'merge', status: 'idle', title: 'Merge',
      blocked: false, selected: false, upstreamPolicy: 'partial',
    };
    render(<SystemNodeCard data={data} />);
    expect(screen.getByTestId('node-partial-badge')).toBeInTheDocument();
  });
});

const NODE: CanvasNodePublic = {
  id: 'n_sum', canvas_id: 'cv_1', kind: 'agent', task_id: 't_sum',
  is_summary: true, upstream_policy: 'strict',
  pos_x: 0, pos_y: 0, created_at: '2026-07-10T00:00:00Z',
};

describe('NodeInspector 改档（③）', () => {
  it('展示当前档 + 汇总标识；点 partial → patch_node(upstream_policy=partial)', async () => {
    vi.mocked(api.patchCanvasNode).mockResolvedValue({} as never);
    render(
      <NodeInspector
        node={NODE} title="#9 汇总交付" canvasId="cv_1"
        onClose={vi.fn()} onError={vi.fn()}
      />,
    );
    expect(screen.getByTestId('node-inspector')).toBeInTheDocument();
    expect(screen.getByText('#9 汇总交付')).toBeInTheDocument();
    // strict 当前档高亮。
    expect(screen.getByTestId('policy-strict')).toHaveClass('active');
    // 改 partial → 走 patch_node。
    fireEvent.click(screen.getByTestId('policy-partial'));
    await waitFor(() =>
      expect(api.patchCanvasNode).toHaveBeenCalledWith('cv_1', 'n_sum', { upstream_policy: 'partial' }),
    );
  });

  it('点当前档（strict）→ 不重复请求', () => {
    vi.mocked(api.patchCanvasNode).mockClear();
    render(
      <NodeInspector
        node={NODE} title="#9 汇总交付" canvasId="cv_1"
        onClose={vi.fn()} onError={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId('policy-strict'));
    expect(api.patchCanvasNode).not.toHaveBeenCalled();
  });

  it('改档失败 → onError 组 toast（403 rule=O9 等）', async () => {
    const onError = vi.fn();
    vi.mocked(api.patchCanvasNode).mockRejectedValue(new Error('boom'));
    render(
      <NodeInspector
        node={NODE} title="#9 汇总交付" canvasId="cv_1"
        onClose={vi.fn()} onError={onError}
      />,
    );
    fireEvent.click(screen.getByTestId('policy-partial'));
    await waitFor(() => expect(onError).toHaveBeenCalled());
  });
});
