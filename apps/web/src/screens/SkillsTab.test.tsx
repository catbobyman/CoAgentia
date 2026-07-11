// P6 技能页签编辑态(M5 B §11.3):候选池 ∪ 已授予勾选、池外已授予可移除、自由输入、权限位、
// codex 引导文案。照 RemindersTab.test.tsx 的 QueryClient seed + vi.mock('../api') 范式。
// 运行:pnpm -F @coagentia/web test
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    api: { ...actual.api, putAgentSkills: vi.fn() },
  };
});

import type { AgentPublic, AgentSkillPublic, ComputerPublic } from '@coagentia/contracts-ts';

import { api } from '../api';
import { qk } from '../lib/queryKeys';
import { ToastProvider, Toaster } from '../components/Toast';
import { SkillsTab } from './AgentDetailScreen';

const AGENT = 'mem_agent';

function agentOf(over: Partial<AgentPublic> = {}): AgentPublic {
  return {
    member_id: AGENT,
    computer_id: 'cmp_1',
    created_by_member_id: 'mem_owner',
    home_path: '/h',
    model: 'sonnet',
    runtime: 'claude_code',
    ...over,
  };
}

function computerOf(skills: string[]): ComputerPublic {
  return {
    id: 'cmp_1',
    name: 'PC',
    workspace_id: 'ws1',
    created_at: '2026-07-11T00:00:00Z',
    detected_runtimes: [
      { runtime: 'claude_code', installed: true, skills },
      { runtime: 'codex', installed: true, skills: [] },
    ],
  };
}

function skill(name: string): AgentSkillPublic {
  return { agent_member_id: AGENT, skill: name, granted_at: '2026-07-11T00:00:00Z', granted_by_member_id: 'mem_owner' };
}

function renderTab(opts: {
  granted: AgentSkillPublic[];
  agent?: AgentPublic;
  computer?: ComputerPublic;
  editable?: boolean;
}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  qc.setQueryData<AgentSkillPublic[]>(qk.agentSkills(AGENT), opts.granted);
  render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <SkillsTab
          memberId={AGENT}
          agent={opts.agent ?? agentOf()}
          computer={opts.computer}
          editable={opts.editable ?? true}
        />
        <Toaster />
      </ToastProvider>
    </QueryClientProvider>,
  );
  return { qc };
}

describe('SkillsTab 编辑态', () => {
  beforeEach(() => vi.clearAllMocks());

  it('候选池 ∪ 已授予:池内已授予勾选,池内未授予未勾选', () => {
    renderTab({ granted: [skill('git')], computer: computerOf(['git', 'docker']) });
    const git = screen.getByRole('checkbox', { name: 'git' });
    const docker = screen.getByRole('checkbox', { name: 'docker' });
    expect(git).toHaveAttribute('aria-checked', 'true');
    expect(docker).toHaveAttribute('aria-checked', 'false');
  });

  it('池外已授予项仍显示并标「池外」,可移除', async () => {
    vi.mocked(api.putAgentSkills).mockResolvedValue([]);
    renderTab({ granted: [skill('legacy')], computer: computerOf(['git']) });
    expect(screen.getByText('池外')).toBeInTheDocument();
    // 点击已勾选的 legacy → 从授予集移除(PUT 不含 legacy)
    fireEvent.click(screen.getByRole('checkbox', { name: 'legacy' }));
    await waitFor(() => expect(api.putAgentSkills).toHaveBeenCalledWith(AGENT, []));
  });

  it('勾选池内未授予项 → PUT 追加', async () => {
    vi.mocked(api.putAgentSkills).mockResolvedValue([]);
    renderTab({ granted: [skill('git')], computer: computerOf(['git', 'docker']) });
    fireEvent.click(screen.getByRole('checkbox', { name: 'docker' }));
    await waitFor(() => expect(api.putAgentSkills).toHaveBeenCalledWith(AGENT, ['git', 'docker']));
  });

  it('自由输入回车 → PUT 追加任意串', async () => {
    vi.mocked(api.putAgentSkills).mockResolvedValue([]);
    renderTab({ granted: [], computer: computerOf(['git']) });
    const input = screen.getByPlaceholderText('自由输入技能名后回车授予');
    fireEvent.change(input, { target: { value: 'custom-skill' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(api.putAgentSkills).toHaveBeenCalledWith(AGENT, ['custom-skill']));
  });

  it('codex 池空 → 引导文案而非空表(无勾选/无自由输入)', () => {
    renderTab({
      granted: [],
      agent: agentOf({ runtime: 'codex' }),
      computer: computerOf([]),
    });
    expect(screen.getByText(/Codex.*暂无技能机制/)).toBeInTheDocument();
    expect(screen.queryByPlaceholderText('自由输入技能名后回车授予')).not.toBeInTheDocument();
  });

  it('无编辑权限(R3):只读已授予,无勾选/无自由输入', () => {
    renderTab({ granted: [skill('git')], computer: computerOf(['git', 'docker']), editable: false });
    expect(screen.getByText('git')).toBeInTheDocument();
    expect(screen.getByText('granted')).toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText('自由输入技能名后回车授予')).not.toBeInTheDocument();
  });
});
