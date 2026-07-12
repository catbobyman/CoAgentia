// P13 创建 Agent 弹窗（M6b 角色模板段）：预选预填/提交携 role_template_key/不选模板零变化/
// NAME_TAKEN 就地报错不关窗。数据源 = contracts 生成三常量（纪律 7 单源）。
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    api: { ...actual.api, computers: vi.fn(), createAgent: vi.fn() },
  };
});

import type { AgentPublic, ComputerPublic } from '@coagentia/contracts-ts';
import {
  ORCHESTRATOR_ROLE_TEMPLATE_DESCRIPTION_PREFILL,
  ORCHESTRATOR_ROLE_TEMPLATE_KEY,
} from '@coagentia/contracts-ts';

import { api, ApiError } from '../api';
import { ToastProvider } from './Toast';
import { CreateAgentModal } from './CreateAgentModal';

const COMPUTER: ComputerPublic = {
  id: 'computer_1', workspace_id: 'ws_1', name: '本机', created_at: '2026-07-12T00:00:00Z',
};
const AGENT: AgentPublic = {
  member_id: 'mem_new', computer_id: COMPUTER.id, runtime: 'claude_code', model: 'sonnet',
  home_path: '~/.coagentia/agents/mem_new', created_by_member_id: 'mem_owner',
  role_template_key: ORCHESTRATOR_ROLE_TEMPLATE_KEY,
};

function renderModal(props?: { preselectRoleKey?: string; onCreated?: (a: AgentPublic) => void }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <CreateAgentModal onClose={() => {}} {...props} />
      </ToastProvider>
    </QueryClientProvider>,
  );
  return qc;
}

describe('CreateAgentModal 角色模板段', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.computers).mockResolvedValue([COMPUTER]);
    vi.mocked(api.createAgent).mockResolvedValue(AGENT);
  });

  it('preselectRoleKey=orchestrator：模板预选 + description 预填模板话术（可改）', async () => {
    renderModal({ preselectRoleKey: ORCHESTRATOR_ROLE_TEMPLATE_KEY });
    expect(await screen.findByTestId('role-template-select')).toHaveValue(
      ORCHESTRATOR_ROLE_TEMPLATE_KEY,
    );
    expect(screen.getByLabelText('成员说明（description）')).toHaveValue(
      ORCHESTRATOR_ROLE_TEMPLATE_DESCRIPTION_PREFILL,
    );
  });

  it('手动选中模板 → 预填 description；提交携 role_template_key 与可改后的文本', async () => {
    const onCreated = vi.fn();
    renderModal({ onCreated });
    await screen.findByLabelText('所在机器');
    fireEvent.change(screen.getByTestId('role-template-select'), {
      target: { value: ORCHESTRATOR_ROLE_TEMPLATE_KEY },
    });
    const desc = screen.getByLabelText('成员说明（description）');
    expect(desc).toHaveValue(ORCHESTRATOR_ROLE_TEMPLATE_DESCRIPTION_PREFILL);
    fireEvent.change(desc, { target: { value: '改过的说明' } }); // 用户可改
    fireEvent.change(screen.getByLabelText('名字'), { target: { value: 'Orchestrator' } });
    fireEvent.change(screen.getByLabelText('模型'), { target: { value: 'sonnet' } });
    fireEvent.click(screen.getByRole('button', { name: '创建 Agent' }));
    await waitFor(() => expect(api.createAgent).toHaveBeenCalledWith({
      name: 'Orchestrator', runtime: 'claude_code', model: 'sonnet',
      computer_id: COMPUTER.id, description: '改过的说明',
      role_template_key: ORCHESTRATOR_ROLE_TEMPLATE_KEY,
    }));
    expect(onCreated).toHaveBeenCalledWith(AGENT);
  });

  it('不选模板：请求体不含 role_template_key（现行为零变化）', async () => {
    renderModal();
    await screen.findByLabelText('所在机器');
    fireEvent.change(screen.getByLabelText('名字'), { target: { value: 'Rin' } });
    fireEvent.change(screen.getByLabelText('模型'), { target: { value: 'sonnet' } });
    fireEvent.click(screen.getByRole('button', { name: '创建 Agent' }));
    await waitFor(() => expect(api.createAgent).toHaveBeenCalledTimes(1));
    const body = vi.mocked(api.createAgent).mock.calls[0]![0];
    expect(body).not.toHaveProperty('role_template_key');
    expect(body.computer_id).toBe(COMPUTER.id); // MVP 单机预选唯一 computer
  });

  it('NAME_TAKEN 就地报错不关窗', async () => {
    vi.mocked(api.createAgent).mockRejectedValue(
      new ApiError(409, 'NAME_TAKEN', '成员名 Orchestrator 已被占用'),
    );
    renderModal();
    await screen.findByLabelText('所在机器');
    fireEvent.change(screen.getByLabelText('名字'), { target: { value: 'Orchestrator' } });
    fireEvent.change(screen.getByLabelText('模型'), { target: { value: 'sonnet' } });
    fireEvent.click(screen.getByRole('button', { name: '创建 Agent' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('已被占用');
    expect(screen.getByRole('dialog', { name: '创建 Agent' })).toBeInTheDocument();
  });
});
