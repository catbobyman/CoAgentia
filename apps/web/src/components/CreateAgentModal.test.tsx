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

// #1/#2 修复：消费所选机器的 detected_runtimes（FR-2.3）驱动 model 候选池 + runtime 置灰。
describe('CreateAgentModal runtime/model 探测消费', () => {
  const computerWith = (rts: ComputerPublic['detected_runtimes']): ComputerPublic => ({
    ...COMPUTER, detected_runtimes: rts,
  });

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.createAgent).mockResolvedValue(AGENT);
  });

  it('该机探测到 models → 模型输入提供候选（datalist），且仍可自由输入', async () => {
    vi.mocked(api.computers).mockResolvedValue([computerWith([
      { runtime: 'claude_code', installed: true, models: ['opus', 'sonnet', 'haiku'] },
      { runtime: 'codex', installed: false, models: [] },
    ])]);
    renderModal();
    // 探测消费依赖 computers 查询到位——锚定 computer option 渲染后再断言（label 是静态的，过早）。
    await screen.findByRole('option', { name: '本机' });
    expect(screen.getByLabelText('模型')).toHaveAttribute('list', 'ca-model-list');
    const list = document.getElementById('ca-model-list');
    const values = Array.from(list?.querySelectorAll('option') ?? []).map((o) => o.getAttribute('value'));
    expect(values).toEqual(['opus', 'sonnet', 'haiku']);
  });

  it('该机未安装 codex → runtime 置灰不可选（claude_code 正常）', async () => {
    vi.mocked(api.computers).mockResolvedValue([computerWith([
      { runtime: 'claude_code', installed: true, models: ['sonnet'] },
      { runtime: 'codex', installed: false, models: [] },
    ])]);
    renderModal();
    await screen.findByRole('option', { name: '本机' });
    expect(screen.getByRole('radio', { name: /Codex/ })).toBeDisabled();
    expect(screen.getByRole('radio', { name: /Claude Code/ })).toBeEnabled();
  });

  it('默认 runtime 在该机未安装 → 警示 + 阻断创建；切到已安装 runtime 恢复', async () => {
    vi.mocked(api.computers).mockResolvedValue([computerWith([
      { runtime: 'claude_code', installed: false, models: [] },
      { runtime: 'codex', installed: true, models: ['gpt-5-codex'] },
    ])]);
    renderModal();
    await screen.findByRole('option', { name: '本机' });
    fireEvent.change(screen.getByLabelText('名字'), { target: { value: 'Rin' } });
    fireEvent.change(screen.getByLabelText('模型'), { target: { value: 'gpt-5-codex' } });
    expect(screen.getByText(/未安装 Claude Code/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '创建 Agent' })).toBeDisabled();
    fireEvent.click(screen.getByRole('radio', { name: /Codex/ }));
    expect(screen.getByRole('button', { name: '创建 Agent' })).toBeEnabled();
  });

  it('无探测数据（机器未探测）→ 不阻塞，两 runtime 均可选、自由输入模型', async () => {
    vi.mocked(api.computers).mockResolvedValue([COMPUTER]); // 无 detected_runtimes
    renderModal();
    await screen.findByLabelText('所在机器');
    expect(screen.getByRole('radio', { name: 'Claude Code' })).toBeEnabled();
    expect(screen.getByRole('radio', { name: 'Codex' })).toBeEnabled();
    fireEvent.change(screen.getByLabelText('名字'), { target: { value: 'Rin' } });
    fireEvent.change(screen.getByLabelText('模型'), { target: { value: 'sonnet' } });
    expect(screen.getByRole('button', { name: '创建 Agent' })).toBeEnabled();
  });
});
