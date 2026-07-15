// F10 死壳补齐：契约登记但 wsBridge 原零处理的实时缺口。computer.*/channel.*/member.* 失效对应
// 列表；task_contract.* 按 task_id 失效 taskDetail；draft.adjusted/confirmed/rejected 只载
// proposal_id → 失效（**不**整体替换，区别于 delta.*）；agent.updated/workspace.updated 整体替换。
import { QueryClient } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';

import type {
  AgentPublic, Envelope, WorkspacePublic,
} from '@coagentia/contracts-ts';

import { qk } from '../lib/queryKeys';
import { applyEnvelope } from './wsBridge';

function envelope(type: string, data: unknown): Envelope {
  return { type, workspace_id: 'ws_1', channel_id: 'ch_1', seq: 1, key: 'k', at: 't', data } as Envelope;
}
const keysOf = (spy: ReturnType<typeof vi.spyOn>) =>
  spy.mock.calls.map((c: unknown[]) => JSON.stringify((c[0] as { queryKey?: unknown })?.queryKey));

describe('F10a computer.*', () => {
  it.each(['computer.connected', 'computer.disconnected', 'computer.updated'])(
    '%s → 失效 computers',
    (type) => {
      const qc = new QueryClient();
      const spy = vi.spyOn(qc, 'invalidateQueries');
      applyEnvelope(qc, envelope(type, { computer: { id: 'c1' } }));
      expect(keysOf(spy)).toContain(JSON.stringify(qk.computers()));
    },
  );
});

describe('F10b task_contract.*', () => {
  it('task_contract.created 携 task_id → 失效对应 taskDetail', () => {
    const qc = new QueryClient();
    const spy = vi.spyOn(qc, 'invalidateQueries');
    applyEnvelope(qc, envelope('task_contract.created', { contract: { id: 'tc1', task_id: 'task_9' } }));
    expect(keysOf(spy)).toContain(JSON.stringify(qk.taskDetail('task_9')));
  });

  it('task_id 为空（loop_contract 挂 reminder）→ 不失效任何 taskDetail', () => {
    const qc = new QueryClient();
    const spy = vi.spyOn(qc, 'invalidateQueries');
    applyEnvelope(qc, envelope('task_contract.updated', { contract: { id: 'tc2', task_id: null } }));
    expect(spy).not.toHaveBeenCalled();
  });
});

describe('F10c draft full 生命周期（仅载 proposal_id）', () => {
  it.each(['draft.adjusted', 'draft.confirmed', 'draft.rejected', 'draft.superseded'])(
    '%s → 失效该提案 query（不整体替换）',
    (type) => {
      const qc = new QueryClient();
      const spy = vi.spyOn(qc, 'invalidateQueries');
      applyEnvelope(qc, envelope(type, { proposal_id: 'prop_x', adjustments: [] }));
      expect(keysOf(spy)).toContain(JSON.stringify(qk.proposal('prop_x')));
    },
  );

  it('draft.adjusted 不会把只含 proposal_id 的载荷当完整提案写进缓存', () => {
    const qc = new QueryClient();
    // 预置一个完整提案；draft.adjusted 只应失效、不应用残缺载荷覆盖它。
    qc.setQueryData(qk.proposal('prop_x'), { id: 'prop_x', status: 'awaiting_confirm' });
    applyEnvelope(qc, envelope('draft.adjusted', { proposal_id: 'prop_x', adjustments: [] }));
    const cached = qc.getQueryData<{ id: string; status: string }>(qk.proposal('prop_x'));
    // 仍是原完整对象（有 status），未被 { proposal_id } 覆盖。
    expect(cached?.status).toBe('awaiting_confirm');
  });
});

describe('F10d channel.* / member.*', () => {
  it.each([
    'channel.created', 'channel.updated', 'channel.deleted',
    'channel.member_added', 'channel.member_removed',
  ])('%s → 失效 channels', (type) => {
    const qc = new QueryClient();
    const spy = vi.spyOn(qc, 'invalidateQueries');
    applyEnvelope(qc, envelope(type, { channel: { id: 'ch_1' }, channel_id: 'ch_1', member_id: 'm1' }));
    expect(keysOf(spy)).toContain(JSON.stringify(qk.channels()));
  });

  it.each(['member.created', 'member.updated', 'member.removed'])('%s → 失效 members', (type) => {
    const qc = new QueryClient();
    const spy = vi.spyOn(qc, 'invalidateQueries');
    applyEnvelope(qc, envelope(type, { member: { id: 'm1' } }));
    expect(keysOf(spy)).toContain(JSON.stringify(qk.members()));
  });
});

describe('F10 agent.updated / workspace.updated 整体替换', () => {
  const AGENT = { member_id: 'mem_a', runtime: 'codex', model: 'gpt-5' } as unknown as AgentPublic;

  it('agent.updated 已加载 → 整体替换 qk.agent', () => {
    const qc = new QueryClient();
    qc.setQueryData(qk.agent('mem_a'), { member_id: 'mem_a', runtime: 'claude_code' });
    applyEnvelope(qc, envelope('agent.updated', { agent: AGENT }));
    expect(qc.getQueryData<AgentPublic>(qk.agent('mem_a'))?.runtime).toBe('codex');
  });

  it('agent.updated 未加载 → 放行不建（不凭 WS 造缓存）', () => {
    const qc = new QueryClient();
    applyEnvelope(qc, envelope('agent.updated', { agent: AGENT }));
    expect(qc.getQueryData(qk.agent('mem_a'))).toBeUndefined();
  });

  it('workspace.updated → 整体替换 workspace（单例恒替换）', () => {
    const qc = new QueryClient();
    const ws = { id: 'ws_1', ui_theme: 'light', notif_desktop: false } as unknown as WorkspacePublic;
    applyEnvelope(qc, envelope('workspace.updated', { workspace: ws }));
    expect(qc.getQueryData<WorkspacePublic>(qk.workspace())?.ui_theme).toBe('light');
  });
});
