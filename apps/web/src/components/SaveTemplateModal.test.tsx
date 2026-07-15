// 存为模板弹窗(M5 B-M5-2 ①):占位改名 / 包含节点勾选 / 名称必填 disabled / 提交体形状。
// 照 ChannelSettingsModal.test.tsx 的 QueryClient + vi.mock('../api') 范式。
// 运行:pnpm -F @coagentia/web test
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return { ...actual, api: { ...actual.api, createTemplate: vi.fn() } };
});

import type { CanvasNodePublic, MemberPublic, TaskPublic, TemplatePublic } from '@coagentia/contracts-ts';

import { api } from '../api';
import { ToastProvider, Toaster } from './Toast';
import { SaveTemplateModal } from './SaveTemplateModal';

function node(id: string, taskId?: string, kind: 'agent' | 'system' = 'agent'): CanvasNodePublic {
  return { id, canvas_id: 'c', kind, created_at: '2026-07-11T00:00:00Z', task_id: taskId };
}
function task(id: string, title: string, owner?: string): TaskPublic {
  return {
    id, channel_id: 'ch', workspace_id: 'ws', number: 1, title,
    created_at: '2026-07-11T00:00:00Z', created_by_member_id: 'm', root_message_id: 'r',
    status_changed_at: '2026-07-11T00:00:00Z', owner_member_id: owner,
  };
}
function member(id: string, name: string): MemberPublic {
  return { id, name, kind: 'agent', workspace_id: 'ws', created_at: '2026-07-11T00:00:00Z' };
}
function templateOf(): TemplatePublic {
  return {
    id: 'tpl_new', workspace_id: 'ws', name: 'X', created_by_member_id: 'm',
    created_at: '2026-07-11T00:00:00Z', body: { nodes: [], edges: [], roles: [], briefing: '' },
  };
}

const NODES = [
  node('n1', 't1'),
  node('n2', 't2'),
  node('ns', undefined, 'system'), // 系统节点不入
];
const TASKS = [task('t1', '实现登录', 'a'), task('t2', '评审登录', 'b')];
const MEMBERS = [member('a', 'Alice'), member('b', 'Bob')];

function renderModal() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  const onClose = vi.fn();
  render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <SaveTemplateModal channelId="ch" nodes={NODES} tasks={TASKS} members={MEMBERS} onClose={onClose} />
        <Toaster />
      </ToastProvider>
    </QueryClientProvider>,
  );
  return { onClose };
}

describe('SaveTemplateModal', () => {
  beforeEach(() => vi.clearAllMocks());

  it('名称空 → 保存 disabled；填名 → 可保存', () => {
    renderModal();
    expect(screen.getByTestId('save-template-submit')).toBeDisabled();
    fireEvent.change(screen.getByLabelText('模板名称'), { target: { value: '工程三角' } });
    expect(screen.getByTestId('save-template-submit')).not.toBeDisabled();
  });

  it('占位提取表按 owner 去重(仅 task 节点)', () => {
    renderModal();
    const rows = screen.getAllByTestId('role-row');
    expect(rows).toHaveLength(2); // Alice / Bob(系统节点不产生行)
    expect(screen.getByLabelText('占位名 Alice')).toHaveValue('Alice');
  });

  it('占位改名 → 提交 role_placeholders 用新名', async () => {
    vi.mocked(api.createTemplate).mockResolvedValue(templateOf());
    const { onClose } = renderModal();
    fireEvent.change(screen.getByLabelText('模板名称'), { target: { value: '工程三角' } });
    fireEvent.change(screen.getByLabelText('占位名 Alice'), { target: { value: '实现工程师' } });
    fireEvent.click(screen.getByTestId('save-template-submit'));
    await waitFor(() => expect(api.createTemplate).toHaveBeenCalledWith(
      expect.objectContaining({ channel_id: 'ch', name: '工程三角', role_placeholders: { a: '实现工程师', b: 'Bob' } }),
    ));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('全选节点 → 省 include_node_ids；取消一个 → 提交子集', async () => {
    vi.mocked(api.createTemplate).mockResolvedValue(templateOf());
    renderModal();
    fireEvent.change(screen.getByLabelText('模板名称'), { target: { value: 'T' } });
    // 默认全选:先验证省字段
    fireEvent.click(screen.getByTestId('save-template-submit'));
    await waitFor(() => expect(api.createTemplate).toHaveBeenCalled());
    expect(vi.mocked(api.createTemplate).mock.calls[0][0]).not.toHaveProperty('include_node_ids');
  });

  it('取消勾选一个节点 → include_node_ids 为剩余子集', async () => {
    vi.mocked(api.createTemplate).mockResolvedValue(templateOf());
    renderModal();
    fireEvent.change(screen.getByLabelText('模板名称'), { target: { value: 'T' } });
    fireEvent.click(screen.getByRole('checkbox', { name: '评审登录' })); // 取消 n2
    fireEvent.click(screen.getByTestId('save-template-submit'));
    await waitFor(() => expect(api.createTemplate).toHaveBeenCalledWith(
      expect.objectContaining({ include_node_ids: ['n1'] }),
    ));
  });
});
