// M6b 消息流提案渲染（B-M6-2 ③）：card_kind==='proposal' → 正文剥离 <control> 只显散文 +
// 渲染提案卡；无 card_kind 不触发卡片（结构化 marker 而非 body 嗅探,同 #6 范式），但机读体
// 仍一律剥离（M6 review F10：修复循环首发的无效提案无 card_ref，原样渲染=JSON 泄漏进会话）。
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return { ...actual, api: { ...actual.api, proposal: vi.fn() } };
});

import type { MessagePublic, ProposalPublic } from '@coagentia/contracts-ts';

import { api } from '../api';
import { MessageFlow } from './MessageFlow';

const PROPOSAL: ProposalPublic = {
  id: 'prop_1', workspace_id: 'ws_1', channel_id: 'ch_1', source_task_id: 'task_1',
  kind: 'full', revision: 1, status: 'awaiting_confirm',
  body: {
    version: 'coagentia.decomposition.v1', mode: 'single_task', summary: '一步交付',
    nodes: [{ temp_id: 'a' }], edges: [],
  },
  proposal_hash: '0123456789abcdef'.repeat(4), proposed_by_member_id: 'mem_orch',
  created_at: '2026-07-12T00:00:00Z', updated_at: '2026-07-12T00:00:00Z',
};

const CONTROL_JSON = '{"version":"coagentia.decomposition.v1","mode":"single_task"}';
const MESSAGE: MessagePublic = {
  id: 'msg_prop', workspace_id: 'ws_1', channel_id: 'ch_1', kind: 'user',
  author_member_id: 'mem_orch', card_kind: 'proposal', card_ref: PROPOSAL.id,
  body: `拆解思路:单任务直达。\n\n<control>${CONTROL_JSON}</control>`,
  created_at: '2026-07-12T00:00:00Z',
};

function renderFlow(message: MessagePublic) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MessageFlow
        messages={[message]}
        memberById={{ mem_orch: { id: 'mem_orch', workspace_id: 'ws_1', kind: 'agent', name: 'Orchestrator', created_at: '2026-07-12T00:00:00Z' } }}
        memberNames={['Orchestrator']} meName="Memcyo"
        presenceOf={() => undefined} taskByRoot={{}} usageByTask={{}}
        onReviewProposal={() => {}}
      />
    </QueryClientProvider>,
  );
}

describe('消息流提案卡（card_kind=proposal）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.proposal).mockResolvedValue(PROPOSAL);
  });

  it('正文剥离 <control> 块只渲染散文；提案卡按 card_ref 拉取渲染', async () => {
    renderFlow(MESSAGE);
    expect(screen.getByText(/拆解思路:单任务直达/)).toBeInTheDocument();
    // 机读 JSON 不得泄漏进消息流正文。
    expect(document.body.textContent).not.toContain('coagentia.decomposition.v1"');
    expect(document.body.textContent).not.toContain('<control>');
    // findBy 等待 query 结算（loading 占位与实体卡共用 data-testid=proposal-card）。
    expect(await screen.findByTestId('proposal-mode')).toHaveTextContent('单任务');
    expect(vi.mocked(api.proposal)).toHaveBeenCalledWith(PROPOSAL.id);
    expect(screen.getByTestId('proposal-card')).toBeInTheDocument();
  });

  it('无 card_kind 的控制块文本（修复循环首发）→ 不触发卡片,但机读体仍剥离（F10）', () => {
    const fake: MessagePublic = { ...MESSAGE, card_kind: undefined, card_ref: undefined };
    renderFlow(fake);
    expect(screen.queryByTestId('proposal-card')).not.toBeInTheDocument();
    expect(vi.mocked(api.proposal)).not.toHaveBeenCalled();
    expect(screen.getByText(/拆解思路:单任务直达/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('<control>');
    expect(document.body.textContent).not.toContain('coagentia.decomposition.v1"');
  });

  it('正文只有控制块（剥空）→ 占位说明,不空白不泄漏（F10）', () => {
    const bare: MessagePublic = {
      ...MESSAGE, card_kind: undefined, card_ref: undefined,
      body: `<control>${CONTROL_JSON}</control>`,
    };
    renderFlow(bare);
    expect(screen.getByText(/机读控制块已交系统处理/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('decomposition.v1');
  });
});
