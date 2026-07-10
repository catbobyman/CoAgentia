// M3b 升格补契约(手填路):新建 L2 任务弹层构建 NodeCreate.task_plan —— 多条验收标准(可增删)、
// 可选 defaults_decided / out_of_scope,version=coagentia.task-plan.v1。校验 goal + ≥1 AC 才可提交。
// 运行:pnpm -F @coagentia/web test
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    api: { ...actual.api, createCanvasNode: vi.fn() },
  };
});

import type { NodeCreate, TaskPlanBody } from '@coagentia/contracts-ts';

import { api } from '../api';
import { NewNodeModal } from './CanvasTab';

describe('NewNodeModal 补契约(手填多 AC)', () => {
  it('创建按钮在 goal/AC 未填时禁用;齐备后放行', () => {
    render(<NewNodeModal canvasId="cv_1" onClose={vi.fn()} onError={vi.fn()} />);
    const create = screen.getByRole('button', { name: '创建' });
    expect(create).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText('任务标题'), { target: { value: '部署站点' } });
    fireEvent.change(screen.getByPlaceholderText('这个任务要达成什么'), { target: { value: '上线 v1' } });
    // 仍缺 AC → 禁用
    expect(create).toBeDisabled();
    fireEvent.change(screen.getByPlaceholderText('验收判据 1'), { target: { value: '首页 200' } });
    expect(create).toBeEnabled();
  });

  it('增加第二条 AC + defaults/out_of_scope → 提交完整 NodeCreate.task_plan', async () => {
    const onClose = vi.fn();
    vi.mocked(api.createCanvasNode).mockResolvedValue({} as never);
    render(<NewNodeModal canvasId="cv_1" onClose={onClose} onError={vi.fn()} />);

    fireEvent.change(screen.getByPlaceholderText('任务标题'), { target: { value: '部署站点' } });
    fireEvent.change(screen.getByPlaceholderText('这个任务要达成什么'), { target: { value: '上线 v1' } });

    // 初始 1 行 AC,增加第二行 → 共 2 行。
    fireEvent.change(screen.getByPlaceholderText('验收判据 1'), { target: { value: '首页返回 200' } });
    fireEvent.click(screen.getByRole('button', { name: /增加一条/ }));
    expect(screen.getAllByTestId('ac-row')).toHaveLength(2);
    fireEvent.change(screen.getByPlaceholderText('验收判据 2'), { target: { value: '构建无告警' } });

    // 第二条 AC 的 verify_by 设为 command + verify_ref。
    const selects = screen.getAllByRole('combobox');
    fireEvent.change(selects[1], { target: { value: 'command' } });
    const refs = screen.getAllByPlaceholderText('verify_ref(命令/引用,可空)');
    fireEvent.change(refs[1], { target: { value: 'pnpm build' } });

    fireEvent.change(screen.getByPlaceholderText('例:番茄时长默认 25/5'), { target: { value: '默认端口 8080\n默认区域 us' } });
    fireEvent.change(screen.getByPlaceholderText('例:多语言 UI'), { target: { value: '移动端适配' } });

    fireEvent.click(screen.getByRole('button', { name: '创建' }));

    await waitFor(() => expect(api.createCanvasNode).toHaveBeenCalledTimes(1));
    const [cvId, body] = vi.mocked(api.createCanvasNode).mock.calls[0] as [string, NodeCreate];
    expect(cvId).toBe('cv_1');
    expect(body.kind).toBe('agent');
    expect(body.title).toBe('部署站点');
    const plan = body.task_plan as TaskPlanBody;
    expect(plan.goal).toBe('上线 v1');
    expect(plan.version).toBe('coagentia.task-plan.v1');
    expect(plan.acceptance_criteria).toHaveLength(2);
    expect(plan.acceptance_criteria[0]).toMatchObject({ id: 'ac-1', statement: '首页返回 200', verify_by: 'manual' });
    expect(plan.acceptance_criteria[1]).toMatchObject({ id: 'ac-2', statement: '构建无告警', verify_by: 'command', verify_ref: 'pnpm build' });
    expect(plan.defaults_decided).toEqual(['默认端口 8080', '默认区域 us']);
    expect(plan.out_of_scope).toEqual(['移动端适配']);
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('空的 AC 行被过滤,只提交有 statement 的判据', async () => {
    vi.mocked(api.createCanvasNode).mockReset().mockResolvedValue({} as never);
    render(<NewNodeModal canvasId="cv_1" onClose={vi.fn()} onError={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText('任务标题'), { target: { value: 'T' } });
    fireEvent.change(screen.getByPlaceholderText('这个任务要达成什么'), { target: { value: 'G' } });
    fireEvent.change(screen.getByPlaceholderText('验收判据 1'), { target: { value: '唯一判据' } });
    // 增加一条空行(不填)→ 提交时应被过滤。
    fireEvent.click(screen.getByRole('button', { name: /增加一条/ }));
    fireEvent.click(screen.getByRole('button', { name: '创建' }));
    await waitFor(() => expect(api.createCanvasNode).toHaveBeenCalledTimes(1));
    const [, body] = vi.mocked(api.createCanvasNode).mock.calls[0] as [string, NodeCreate];
    expect((body.task_plan as TaskPlanBody).acceptance_criteria).toHaveLength(1);
  });
});
