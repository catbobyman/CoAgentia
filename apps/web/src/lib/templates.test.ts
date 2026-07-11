// M5b 模板域纯函数单测(判定单点：存为模板 gating / 角色占位提取 / role_mapping 覆盖 / 跨 runtime 互审)。
// 运行:pnpm -F @coagentia/web test
import { describe, expect, it } from 'vitest';

import type { CanvasNodePublic, MemberPublic, TaskPublic, TemplateRole } from '@coagentia/contracts-ts';

import {
  buildRolePlaceholders,
  classifyRole,
  extractRolePlaceholders,
  formalTaskNodes,
  hasSameRuntimeReview,
  missingRoleMappings,
  saveTemplateGate,
} from './templates';

function node(over: Partial<CanvasNodePublic> = {}): CanvasNodePublic {
  return { id: 'n', canvas_id: 'c', kind: 'agent', created_at: '2026-07-11T00:00:00Z', ...over };
}
function task(over: Partial<TaskPublic> = {}): TaskPublic {
  return {
    id: 't', channel_id: 'ch', workspace_id: 'ws', number: 1, title: 'T',
    created_at: '2026-07-11T00:00:00Z', created_by_member_id: 'm', root_message_id: 'r',
    status_changed_at: '2026-07-11T00:00:00Z', ...over,
  };
}
function member(id: string, name: string): MemberPublic {
  return { id, name, kind: 'agent', workspace_id: 'ws', created_at: '2026-07-11T00:00:00Z' };
}
function role(placeholder: string, description?: string): TemplateRole {
  return description ? { placeholder, description } : { placeholder };
}

describe('saveTemplateGate', () => {
  it('无正式节点 → disabled + 先加正式节点', () => {
    expect(saveTemplateGate(0, false)).toEqual({ enabled: false, hint: '先加正式节点' });
  });
  it('有草稿层 → disabled + 先处理草稿(优先于节点数)', () => {
    expect(saveTemplateGate(0, true)).toEqual({ enabled: false, hint: '先处理画布草稿层' });
    expect(saveTemplateGate(3, true)).toEqual({ enabled: false, hint: '先处理画布草稿层' });
  });
  it('≥1 正式节点且无草稿 → enabled', () => {
    expect(saveTemplateGate(1, false)).toEqual({ enabled: true });
  });
});

describe('formalTaskNodes / extractRolePlaceholders', () => {
  const nodes = [
    node({ id: 'n1', kind: 'agent', task_id: 't1' }),
    node({ id: 'n2', kind: 'agent', task_id: 't2' }),
    node({ id: 'n3', kind: 'agent', task_id: 't3' }), // 无 owner → 待认领
    node({ id: 'ns', kind: 'system', system_action: 'merge' }), // 系统节点排除
    node({ id: 'n0', kind: 'agent' }), // 无 task_id 排除
  ];
  const taskById = {
    t1: task({ id: 't1', owner_member_id: 'a' }),
    t2: task({ id: 't2', owner_member_id: 'b' }),
    t3: task({ id: 't3' }),
  };
  const memberById = { a: member('a', 'Alice'), b: member('b', 'Bob') };

  it('formalTaskNodes 只留含 task_id 的 agent 节点', () => {
    expect(formalTaskNodes(nodes).map((n) => n.id)).toEqual(['n1', 'n2', 'n3']);
  });

  it('按 owner 去重 + 无 owner 归待认领(默认占位名 = owner 名)', () => {
    const rows = extractRolePlaceholders(nodes, taskById, memberById);
    expect(rows).toEqual([
      { ownerId: 'a', ownerName: 'Alice', placeholder: 'Alice' },
      { ownerId: 'b', ownerName: 'Bob', placeholder: 'Bob' },
      { ownerId: null, ownerName: '待认领', placeholder: '待认领' },
    ]);
  });

  it('同 owner 多节点只出一行', () => {
    const dup = [node({ id: 'n1', task_id: 't1' }), node({ id: 'n2', task_id: 't1' })];
    const rows = extractRolePlaceholders(dup, { t1: task({ id: 't1', owner_member_id: 'a' }) }, memberById);
    expect(rows).toHaveLength(1);
  });

  it('buildRolePlaceholders 仅含有 owner 的行、trim、待认领不入', () => {
    const rows = extractRolePlaceholders(nodes, taskById, memberById);
    rows[0].placeholder = '  契约工程师  ';
    expect(buildRolePlaceholders(rows)).toEqual({ a: '契约工程师', b: 'Bob' });
  });

  it('buildRolePlaceholders 全空 → undefined(省字段)', () => {
    expect(buildRolePlaceholders([{ ownerId: null, ownerName: '待认领', placeholder: '待认领' }])).toBeUndefined();
  });
});

describe('classifyRole', () => {
  it('评审/验收/checker → review', () => {
    expect(classifyRole(role('评审工程师', '独立评审（checker ≠ doer）'))).toBe('review');
    expect(classifyRole(role('Reviewer', 'independent review'))).toBe('review');
  });
  it('实现/doer → implement', () => {
    expect(classifyRole(role('实现工程师', '落地实现（doer）'))).toBe('implement');
    expect(classifyRole(role('Impl', 'the doer'))).toBe('implement');
  });
  it('其它 → other', () => {
    expect(classifyRole(role('产品经理', '框定需求'))).toBe('other');
  });
});

describe('hasSameRuntimeReview (FR-7.3)', () => {
  const roles = [role('实现工程师', 'doer'), role('评审工程师', 'checker')];
  it('评审+实现同 runtime → true(warning)', () => {
    const rt: Record<string, string> = { a: 'claude_code', b: 'claude_code' };
    const mapping = { 实现工程师: 'a', 评审工程师: 'b' };
    expect(hasSameRuntimeReview(roles, mapping, (id) => rt[id])).toBe(true);
  });
  it('评审+实现异 runtime → false', () => {
    const rt: Record<string, string> = { a: 'codex', b: 'claude_code' };
    const mapping = { 实现工程师: 'a', 评审工程师: 'b' };
    expect(hasSameRuntimeReview(roles, mapping, (id) => rt[id])).toBe(false);
  });
  it('待认领(null)/未选 → 无 runtime，不触发', () => {
    const rt: Record<string, string> = { a: 'claude_code' };
    const mapping = { 实现工程师: 'a', 评审工程师: null };
    expect(hasSameRuntimeReview(roles, mapping, (id) => rt[id])).toBe(false);
  });
});

describe('missingRoleMappings (§11.2 #1)', () => {
  const roles = [role('实现工程师'), role('评审工程师')];
  it('未做选择的占位 = 未覆盖', () => {
    expect(missingRoleMappings(roles, { 实现工程师: 'a' })).toEqual(['评审工程师']);
  });
  it('null(待认领)算已覆盖', () => {
    expect(missingRoleMappings(roles, { 实现工程师: 'a', 评审工程师: null })).toEqual([]);
  });
});
