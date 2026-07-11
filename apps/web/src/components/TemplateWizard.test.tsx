// 模板向导三步(M5 B-M5-2 ②):选模板 / 角色映射 / 同 runtime warning / 异 runtime 无 warning /
// 步③预览(简报 + 映射摘要 + DAG) / 实例化跳转(携 Idempotency-Key)。runtime 经 qk.agent 缓存 seed
// (staleTime Infinity 免 refetch)。步②下拉无「新建 Agent」选项(M5 前端无建 Agent 流程，见 FIX 1)。
// 运行:pnpm -F @coagentia/web test
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    api: { ...actual.api, templates: vi.fn(), instantiateTemplate: vi.fn(), agent: vi.fn() },
  };
});

import type { AgentPublic, MemberPublic, TemplatePublic } from '@coagentia/contracts-ts';

import { api } from '../api';
import { qk } from '../lib/queryKeys';
import { ToastProvider, Toaster } from './Toast';
import { TemplateWizard } from './TemplateWizard';

function member(id: string, name: string): MemberPublic {
  return { id, name, kind: 'agent', workspace_id: 'ws', created_at: '2026-07-11T00:00:00Z' };
}
function agentOf(id: string, runtime: 'claude_code' | 'codex'): AgentPublic {
  return { member_id: id, computer_id: 'c', created_by_member_id: 'm', home_path: '/h', model: 'x', runtime };
}
function triangle(): TemplatePublic {
  return {
    id: 'tpl_tri', workspace_id: 'ws', name: '工程三角', builtin: true,
    description: '实现 → 评审', created_by_member_id: 'm', created_at: '2026-07-11T00:00:00Z',
    body: {
      nodes: [
        { key: 'impl', title: '实现', role: '实现工程师', plan_skeleton: null },
        { key: 'review', title: '独立验收', role: '评审工程师', plan_skeleton: null },
      ],
      edges: [{ from_key: 'impl', to_key: 'review' }],
      roles: [
        { placeholder: '实现工程师', description: '落地实现（doer）' },
        { placeholder: '评审工程师', description: '独立评审（checker ≠ doer）' },
      ],
      briefing: '本频道由工程三角实例化：实现方交付、评审方复核。',
    },
  };
}

const RT: Record<string, 'claude_code' | 'codex'> = { a: 'claude_code', b: 'claude_code', c: 'codex', d: 'claude_code' };

function renderWizard(members: MemberPublic[]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  // seed runtime(useQueries 读 qk.agent，fresh → 不 refetch)。
  members.forEach((m) => qc.setQueryData<AgentPublic>(qk.agent(m.id), agentOf(m.id, RT[m.id] ?? 'claude_code')));
  const onClose = vi.fn();
  const onInstantiated = vi.fn();
  const tree = (ms: MemberPublic[]) => (
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <TemplateWizard
          channelId="ch"
          members={ms}
          onClose={onClose}
          onInstantiated={onInstantiated}
        />
        <Toaster />
      </ToastProvider>
    </QueryClientProvider>
  );
  const utils = render(tree(members));
  const rerender = (ms: MemberPublic[]) => {
    ms.forEach((m) => qc.setQueryData<AgentPublic>(qk.agent(m.id), agentOf(m.id, RT[m.id] ?? 'claude_code')));
    utils.rerender(tree(ms));
  };
  return { qc, onClose, onInstantiated, rerender };
}

async function gotoStep2() {
  const card = await screen.findByTestId('template-card');
  fireEvent.click(card);
  fireEvent.click(screen.getByRole('button', { name: /下一步/ }));
  await screen.findByTestId('wizard-step-2');
}

describe('TemplateWizard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.templates).mockResolvedValue([triangle()]);
    vi.mocked(api.agent).mockImplementation((id: string) => Promise.resolve(agentOf(id, RT[id] ?? 'claude_code')));
  });

  it('步①列模板卡片(builtin 徽章 + DAG 缩略图)', async () => {
    renderWizard([member('a', 'Alice')]);
    expect(await screen.findByTestId('template-card')).toBeInTheDocument();
    expect(screen.getByText('工程三角')).toBeInTheDocument();
    expect(screen.getByText('builtin')).toBeInTheDocument();
    expect(screen.getByTestId('dag-thumb')).toBeInTheDocument();
  });

  it('步②每占位一个下拉，全覆盖前下一步 disabled', async () => {
    renderWizard([member('a', 'Alice'), member('b', 'Bob')]);
    await gotoStep2();
    expect(screen.getByLabelText('映射 实现工程师')).toBeInTheDocument();
    expect(screen.getByLabelText('映射 评审工程师')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /下一步/ })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('映射 实现工程师'), { target: { value: 'a' } });
    fireEvent.change(screen.getByLabelText('映射 评审工程师'), { target: { value: 'b' } });
    expect(screen.getByRole('button', { name: /下一步/ })).not.toBeDisabled();
  });

  it('评审+实现同 runtime → warning(不阻塞)', async () => {
    renderWizard([member('a', 'Alice'), member('b', 'Bob')]); // 都 claude_code
    await gotoStep2();
    fireEvent.change(screen.getByLabelText('映射 实现工程师'), { target: { value: 'a' } });
    fireEvent.change(screen.getByLabelText('映射 评审工程师'), { target: { value: 'b' } });
    expect(screen.getByTestId('same-runtime-warn')).toBeInTheDocument();
    // 仍可继续(不阻塞)
    expect(screen.getByRole('button', { name: /下一步/ })).not.toBeDisabled();
  });

  it('评审+实现异 runtime → 无 warning', async () => {
    renderWizard([member('a', 'Alice'), member('c', 'Cody')]); // a=claude, c=codex
    await gotoStep2();
    fireEvent.change(screen.getByLabelText('映射 实现工程师'), { target: { value: 'a' } });
    fireEvent.change(screen.getByLabelText('映射 评审工程师'), { target: { value: 'c' } });
    expect(screen.queryByTestId('same-runtime-warn')).not.toBeInTheDocument();
  });

  it('步②下拉仅 请选择成员…/待认领/agent 选项，无「新建 Agent」(FIX 1:死控件已移除)', async () => {
    renderWizard([member('a', 'Alice'), member('b', 'Bob')]);
    await gotoStep2();
    const sel = screen.getByLabelText('映射 实现工程师') as HTMLSelectElement;
    const optionTexts = Array.from(sel.options).map((o) => o.textContent);
    expect(optionTexts).toEqual(['请选择成员…', '待认领', 'Alice · Claude Code', 'Bob · Claude Code']);
    expect(optionTexts.some((t) => t?.includes('新建'))).toBe(false);
  });

  it('步③预览:简报文案 + 角色映射摘要(含待认领回退) + DAG 缩略图', async () => {
    renderWizard([member('a', 'Alice'), member('b', 'Bob')]);
    await gotoStep2();
    fireEvent.change(screen.getByLabelText('映射 实现工程师'), { target: { value: 'a' } });
    fireEvent.change(screen.getByLabelText('映射 评审工程师'), { target: { value: '__unassigned__' } });
    fireEvent.click(screen.getByRole('button', { name: /下一步/ }));
    await screen.findByTestId('wizard-step-3');

    const briefing = document.querySelector('.tw-briefing');
    expect(briefing?.textContent).toBe('本频道由工程三角实例化：实现方交付、评审方复核。');

    const rows = Array.from(document.querySelectorAll('.tw-summary .sr')).map((el) => el.textContent);
    expect(rows).toEqual(['实现工程师→Alice', '评审工程师→待认领']);

    expect(screen.getByTestId('dag-thumb')).toBeInTheDocument();
  });

  it('步③实例化 → 调 instantiateTemplate(携 Idempotency-Key)并跳转(onInstantiated)', async () => {
    vi.mocked(api.instantiateTemplate).mockResolvedValue({
      batch: {
        id: 'b', workspace_id: 'ws', channel_id: 'ch', kind: 'tmpl', content_hash: 'h',
        source_ref: 'tpl_tri', confirmed_by: 'm', status: 'done',
        created_at: '2026-07-11T00:00:00Z', done_at: '2026-07-11T00:00:00Z',
      },
      tasks: [],
    });
    const { onInstantiated } = renderWizard([member('a', 'Alice'), member('b', 'Bob')]);
    await gotoStep2();
    fireEvent.change(screen.getByLabelText('映射 实现工程师'), { target: { value: 'a' } });
    fireEvent.change(screen.getByLabelText('映射 评审工程师'), { target: { value: 'b' } });
    fireEvent.click(screen.getByRole('button', { name: /下一步/ }));
    await screen.findByTestId('wizard-step-3');
    fireEvent.click(screen.getByTestId('instantiate-submit'));
    await waitFor(() => expect(api.instantiateTemplate).toHaveBeenCalledWith(
      'tpl_tri',
      { channel_id: 'ch', role_mapping: { 实现工程师: 'a', 评审工程师: 'b' } },
      expect.any(String), // Idempotency-Key(crypto.randomUUID，值不固定，仅断言已传)
    ));
    await waitFor(() => expect(onInstantiated).toHaveBeenCalledWith('ch'));
  });
});
