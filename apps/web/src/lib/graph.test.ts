// graph.ts 与 server 图内核(packages/contracts kernel/graph.py)平价:加载同一组黄金判例
// (packages/fixtures/golden/graph.json)逐条断言,防前后端双实现漂移(纪律 8)。
// 运行:pnpm -F @coagentia/web test
import { describe, expect, it } from 'vitest';

import type { CanvasEdgePublic, CanvasNodePublic, TaskPublic } from '@coagentia/contracts-ts';

import golden from '../../../../packages/fixtures/golden/graph.json';
import { deriveCanvasBlocked, detectCycle, deriveBlocked, wouldCreateCycle } from './graph';

interface GraphCase {
  fn: 'detect_cycle' | 'derive_blocked';
  name: string;
  node_ids: string[];
  edges: Array<[string, string]>;
  cycle?: string[] | null;
  satisfied?: string[];
  blocked?: string[];
}

const cases = golden as unknown as GraphCase[];
const detectCases = cases.filter((c) => c.fn === 'detect_cycle');
const blockedCases = cases.filter((c) => c.fn === 'derive_blocked');

describe('detectCycle 黄金判例平价', () => {
  // 判例集非空自证(golden 被移动/清空时立即失败,而非静默跳过)。
  it('golden 含 detect_cycle 判例', () => {
    expect(detectCases.length).toBeGreaterThan(0);
  });
  for (const c of detectCases) {
    it(`detect_cycle: ${c.name}`, () => {
      // 返回构成环的有序路径(与 server 逐字可比),无环 null。
      expect(detectCycle(c.node_ids, c.edges)).toEqual(c.cycle ?? null);
    });
  }
});

describe('deriveBlocked 黄金判例平价', () => {
  it('golden 含 derive_blocked 判例', () => {
    expect(blockedCases.length).toBeGreaterThan(0);
  });
  for (const c of blockedCases) {
    it(`derive_blocked: ${c.name}`, () => {
      const got = [...deriveBlocked(c.node_ids, c.edges, new Set(c.satisfied ?? []))].sort();
      expect(got).toEqual([...(c.blocked ?? [])].sort());
    });
  }
});

describe('wouldCreateCycle 连边预判', () => {
  it('自环判定为环', () => {
    expect(wouldCreateCycle([], 'A', 'A')).toBe(true);
  });
  it('回边(C→A 于链 A→B→C)成环', () => {
    expect(wouldCreateCycle([['A', 'B'], ['B', 'C']], 'C', 'A')).toBe(true);
  });
  it('前向边(A→C 于链 A→B→C)不成环', () => {
    expect(wouldCreateCycle([['A', 'B'], ['B', 'C']], 'A', 'C')).toBe(false);
  });
});

// deriveCanvasBlocked = satisfied 组装(agent done / system success)+ deriveBlocked 的单源(纪律 8):
// CanvasTab 着色与 BoardTab 徽标共用,故此处锁其行为(算法本体由上方黄金判例保证)。
function tnode(id: string, task_id: string): CanvasNodePublic {
  return { id, canvas_id: 'cv_1', kind: 'agent', task_id, pos_x: 0, pos_y: 0, created_at: '2026-07-10T00:00:00Z' };
}
function snode(id: string, status: CanvasNodePublic['system_status']): CanvasNodePublic {
  return { id, canvas_id: 'cv_1', kind: 'system', system_status: status, pos_x: 0, pos_y: 0, created_at: '2026-07-10T00:00:00Z' };
}
function task(id: string, status: TaskPublic['status']): TaskPublic {
  return {
    id, number: 1, title: id, status,
    channel_id: 'ch_1', workspace_id: 'ws_1', root_message_id: `msg_${id}`,
    created_by_member_id: 'mem_owner', owner_member_id: 'mem_rin',
    created_at: '2026-07-10T00:00:00Z', status_changed_at: '2026-07-10T00:00:00Z',
  };
}
const edge = (from: string, to: string): CanvasEdgePublic => ({ id: `e_${from}_${to}`, canvas_id: 'cv_1', from_node_id: from, to_node_id: to });

describe('deriveCanvasBlocked satisfied+blocked 单源', () => {
  it('上游任务 done → satisfied,下游不 blocked(happy path)', () => {
    const nodes = [tnode('n_1', 't_1'), tnode('n_2', 't_2')];
    const taskById = { t_1: task('t_1', 'done'), t_2: task('t_2', 'todo') };
    const { satisfied, blocked } = deriveCanvasBlocked(nodes, [edge('n_1', 'n_2')], taskById);
    expect([...satisfied]).toEqual(['n_1']);
    expect(blocked.size).toBe(0);
  });

  it('上游任务未 done → 下游 blocked;上游根节点不 blocked', () => {
    const nodes = [tnode('n_1', 't_1'), tnode('n_2', 't_2')];
    const taskById = { t_1: task('t_1', 'in_progress'), t_2: task('t_2', 'todo') };
    const { satisfied, blocked } = deriveCanvasBlocked(nodes, [edge('n_1', 'n_2')], taskById);
    expect(satisfied.size).toBe(0);
    expect(blocked.has('n_1')).toBe(false);
    expect(blocked.has('n_2')).toBe(true);
  });

  it('system 节点 success 计入 satisfied,解除其下游 blocked', () => {
    const nodes = [snode('s_1', 'success'), tnode('n_2', 't_2')];
    const taskById = { t_2: task('t_2', 'todo') };
    const { satisfied, blocked } = deriveCanvasBlocked(nodes, [edge('s_1', 'n_2')], taskById);
    expect([...satisfied]).toEqual(['s_1']);
    expect(blocked.size).toBe(0);
  });
});
