// P2 画布单测:图模型 join + blocked 派生、节点卡 blocked 徽标渲染、连边成环红色预判。
// 不整体渲染 ReactFlow(happy-dom 无布局测量)——测纯函数 buildCanvasModel/planEdgeConnect 与
// 脱离 RF context 的展示卡 TaskNodeCard/SystemNodeCard。运行:pnpm -F @coagentia/web test
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return { ...actual, api: { ...actual.api, createCanvasNode: vi.fn() } };
});

import type {
  CanvasDetail, CanvasNodePublic, MemberPublic, NodeCreate, PresenceEntry, TaskPublic,
} from '@coagentia/contracts-ts';

import { api } from '../api';

import {
  SystemNodeModal,
  SystemNodeCard,
  TaskNodeCard,
  buildCanvasModel,
  planEdgeConnect,
  resolveActingMember,
  type SystemNodeData,
  type TaskNodeData,
} from './CanvasTab';

function task(id: string, number: number, status: TaskPublic['status'], title: string): TaskPublic {
  return {
    id, number, title, status,
    channel_id: 'ch_1', workspace_id: 'ws_1', root_message_id: `msg_${id}`,
    created_by_member_id: 'mem_owner', owner_member_id: 'mem_rin',
    created_at: '2026-07-10T00:00:00Z', status_changed_at: '2026-07-10T00:00:00Z',
  };
}
const RIN: MemberPublic = {
  id: 'mem_rin', kind: 'agent', name: 'Rin', workspace_id: 'ws_1', created_at: '2026-07-10T00:00:00Z',
};

function detailOf(upstreamStatus: TaskPublic['status']): CanvasDetail {
  return {
    canvas: {
      id: 'cv_1', channel_id: 'ch_1', workspace_id: 'ws_1',
      baseline_hash: 'h0', baseline_version: 1, updated_at: '2026-07-10T00:00:00Z',
    },
    nodes: [
      { id: 'n_1', canvas_id: 'cv_1', kind: 'agent', task_id: 't_1', pos_x: 0, pos_y: 0, created_at: '2026-07-10T00:00:00Z' },
      { id: 'n_2', canvas_id: 'cv_1', kind: 'agent', task_id: 't_2', pos_x: 300, pos_y: 0, created_at: '2026-07-10T00:00:00Z' },
    ],
    edges: [{ id: 'e_1', canvas_id: 'cv_1', from_node_id: 'n_1', to_node_id: 'n_2' }],
  };
}

const memberById = { [RIN.id]: RIN } as Record<string, MemberPublic>;
const presenceById = {} as Record<string, PresenceEntry>;

describe('buildCanvasModel 节点 join + blocked 派生', () => {
  it('上游任务未 done → 下游节点 blocked;上游节点自身不 blocked', () => {
    const taskById = {
      t_1: task('t_1', 1, 'in_progress', '上游任务'),
      t_2: task('t_2', 2, 'todo', '下游任务'),
    };
    const { rfNodes } = buildCanvasModel(detailOf('in_progress'), taskById, memberById, presenceById, undefined);
    const n1 = rfNodes.find((n) => n.id === 'n_1')!.data as TaskNodeData;
    const n2 = rfNodes.find((n) => n.id === 'n_2')!.data as TaskNodeData;
    // join:number/title/status 来自 tasks 缓存。
    expect(n2.number).toBe(2);
    expect(n2.title).toBe('下游任务');
    expect(n2.status).toBe('todo');
    // blocked:n_2 前驱 n_1 未 satisfied → blocked;n_1 无前驱 → 不 blocked。
    expect(n1.blocked).toBe(false);
    expect(n2.blocked).toBe(true);
  });

  it('上游任务 done → 下游节点解除 blocked', () => {
    const taskById = {
      t_1: task('t_1', 1, 'done', '上游任务'),
      t_2: task('t_2', 2, 'todo', '下游任务'),
    };
    const { rfNodes } = buildCanvasModel(detailOf('done'), taskById, memberById, presenceById, undefined);
    const n2 = rfNodes.find((n) => n.id === 'n_2')!.data as TaskNodeData;
    expect(n2.blocked).toBe(false);
  });

  it('选中 id 命中 → 该节点 data.selected=true', () => {
    const taskById = { t_1: task('t_1', 1, 'todo', 'A'), t_2: task('t_2', 2, 'todo', 'B') };
    const { rfNodes } = buildCanvasModel(detailOf('todo'), taskById, memberById, presenceById, 'n_2');
    expect((rfNodes.find((n) => n.id === 'n_2')!.data as TaskNodeData).selected).toBe(true);
    expect((rfNodes.find((n) => n.id === 'n_1')!.data as TaskNodeData).selected).toBe(false);
  });
});

describe('节点卡渲染', () => {
  it('blocked 任务节点显示 lock 徽标 + 状态词', () => {
    const data: TaskNodeData = {
      kind: 'agent', number: 7, title: '被阻塞的任务', status: 'in_progress',
      ownerName: 'Rin', blocked: true, selected: false,
    };
    render(<TaskNodeCard data={data} />);
    expect(screen.getByTestId('node-blocked')).toBeInTheDocument();
    expect(screen.getByText('In Progress')).toBeInTheDocument();
    expect(screen.getByText('#7')).toBeInTheDocument();
  });

  it('非 blocked 节点不显示 lock 徽标', () => {
    const data: TaskNodeData = {
      kind: 'agent', number: 1, title: '正常任务', status: 'todo', blocked: false, selected: false,
    };
    render(<TaskNodeCard data={data} />);
    expect(screen.queryByTestId('node-blocked')).not.toBeInTheDocument();
  });

  it('系统节点菱形卡显示动作标题与状态', () => {
    const data: SystemNodeData = {
      kind: 'system', action: 'merge', status: 'running', title: 'Merge', blocked: false, selected: false,
    };
    render(<SystemNodeCard data={data} />);
    expect(screen.getByTestId('canvas-snode')).toBeInTheDocument();
    expect(screen.getByText('Merge')).toBeInTheDocument();
    expect(screen.getByText('running')).toBeInTheDocument();
  });

  it('仅 failed 系统节点显示 Retry；check 终态提供输出入口', () => {
    const retry = vi.fn();
    const output = vi.fn();
    const { rerender } = render(
      <SystemNodeCard
        data={{ kind: 'system', action: 'check', status: 'failed', title: 'Check', blocked: false, selected: false }}
        onRetry={retry} onShowOutput={output}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '重试 Check' }));
    fireEvent.click(screen.getByRole('button', { name: '查看 Check 输出' }));
    expect(retry).toHaveBeenCalledOnce();
    expect(output).toHaveBeenCalledOnce();

    rerender(
      <SystemNodeCard
        data={{ kind: 'system', action: 'check', status: 'success', title: 'Check', blocked: false, selected: false }}
        onRetry={retry} onShowOutput={output}
      />,
    );
    expect(screen.queryByRole('button', { name: /重试/ })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '查看 Check 输出' })).toBeInTheDocument();
  });
});

describe('SystemNodeModal', () => {
  it('check 未填 command 禁止提交，填写后创建 system/check 节点', async () => {
    vi.mocked(api.createCanvasNode).mockResolvedValue({} as never);
    render(<SystemNodeModal canvasId="cv_1" onClose={vi.fn()} onError={vi.fn()} />);
    fireEvent.click(screen.getByRole('radio', { name: 'Check' }));
    const create = screen.getByRole('button', { name: '创建系统节点' });
    expect(create).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Check 命令'), { target: { value: 'pnpm test' } });
    expect(create).toBeEnabled();
    fireEvent.click(create);
    await waitFor(() => expect(api.createCanvasNode).toHaveBeenCalledWith('cv_1', {
      kind: 'system', title: 'Check', system_action: 'check', command: 'pnpm test',
    }));
  });

  // ①(M8a 加固批 L1 原子建边)：上游多选——建边随建节点原子完成,不再靠人事后手连(消解空成功竞态 K1)。
  const UP_TASK = task('t_up', 3, 'todo', '上游任务');
  const UP_NODES: CanvasNodePublic[] = [
    { id: 'n_up1', canvas_id: 'cv_1', kind: 'agent', task_id: 't_up', pos_x: 0, pos_y: 0, created_at: '2026-07-10T00:00:00Z' },
    { id: 'n_up2', canvas_id: 'cv_1', kind: 'system', system_action: 'check', command: 'pnpm lint', pos_x: 300, pos_y: 0, created_at: '2026-07-10T00:00:00Z' },
  ];

  it('列出画布既有节点供上游多选;勾选后 POST body 携 upstream_node_ids', async () => {
    vi.mocked(api.createCanvasNode).mockReset().mockResolvedValue({} as never);
    render(
      <SystemNodeModal canvasId="cv_1" nodes={UP_NODES} tasks={[UP_TASK]} onClose={vi.fn()} onError={vi.fn()} />,
    );
    // 任务节点标注 #编号+标题;系统节点复用 systemTitle(Check · 命令)。
    expect(screen.getByLabelText('#3 上游任务')).toBeInTheDocument();
    expect(screen.getByLabelText('Check · pnpm lint')).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText('#3 上游任务'));
    fireEvent.click(screen.getByRole('button', { name: '创建系统节点' }));
    await waitFor(() => expect(api.createCanvasNode).toHaveBeenCalledWith('cv_1', {
      kind: 'system', title: 'Merge', system_action: 'merge', upstream_node_ids: ['n_up1'],
    }));
  });

  it('不勾选任何上游 → POST body 不携 upstream_node_ids 字段', async () => {
    vi.mocked(api.createCanvasNode).mockReset().mockResolvedValue({} as never);
    render(
      <SystemNodeModal canvasId="cv_1" nodes={UP_NODES} tasks={[UP_TASK]} onClose={vi.fn()} onError={vi.fn()} />,
    );
    fireEvent.click(screen.getByRole('button', { name: '创建系统节点' }));
    await waitFor(() => expect(api.createCanvasNode).toHaveBeenCalledTimes(1));
    const [, body] = vi.mocked(api.createCanvasNode).mock.calls[0] as [string, NodeCreate];
    expect(body).not.toHaveProperty('upstream_node_ids');
  });

  it('画布无既有节点 → 上游多选区显示空态提示', () => {
    render(<SystemNodeModal canvasId="cv_1" onClose={vi.fn()} onError={vi.fn()} />);
    expect(screen.getByText('画布暂无可选上游节点')).toBeInTheDocument();
  });
});

// ④(M8a 加固批 R-13)：部署确认弹窗触发者取"当前 acting member"——与全局既有 me 惯用式
// (kind=human && role=owner)对齐,不再读成"频道 owner"语义漂移。
describe('resolveActingMember(R-13 acting member 惯用式)', () => {
  const OWNER: MemberPublic = {
    id: 'mem_owner', kind: 'human', role: 'owner', name: 'Aster', workspace_id: 'ws_1',
    created_at: '2026-07-10T00:00:00Z',
  };

  it('取 kind=human && role=owner 的成员', () => {
    expect(resolveActingMember([RIN, OWNER])?.name).toBe('Aster');
  });

  it('无匹配的人类 owner 成员 → undefined', () => {
    expect(resolveActingMember([RIN])).toBeUndefined();
  });
});

describe('planEdgeConnect 连边成环红色预判', () => {
  const chain: Array<[string, string]> = [['A', 'B'], ['B', 'C']];
  it('回边 C→A 成环 → 拒绝(reason=cycle),不发请求', () => {
    expect(planEdgeConnect(chain, 'C', 'A')).toEqual({ ok: false, reason: 'cycle' });
  });
  it('前向边 A→C 合法 → 放行', () => {
    expect(planEdgeConnect(chain, 'A', 'C')).toEqual({ ok: true, from: 'A', to: 'C' });
  });
  it('缺端点 → incomplete', () => {
    expect(planEdgeConnect(chain, null, 'C')).toEqual({ ok: false, reason: 'incomplete' });
  });
});
