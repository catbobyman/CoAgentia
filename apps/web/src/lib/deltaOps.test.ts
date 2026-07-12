// M6b delta 操作解析 + 客户端结构重验（防呆）单测。
import { describe, expect, it } from 'vitest';

import { readDeltaOps, revalidateDelta } from './deltaOps';

describe('readDeltaOps（防御式字段读取）', () => {
  it('解析 add_node/remove_node/add_edge/remove_edge 并保留原始下标 + kind', () => {
    const body = {
      version: 'coagentia.decomposition-delta.v1',
      operations: [
        { op: 'add_node', node: { temp_id: 'n5', title: '单测' } },
        { op: 'remove_node', temp_id: 'node_old' },
        { op: 'add_edge', from: 'n2', to: 'n5' },
        { op: 'remove_edge', from: 'n2', to: 'node_old' },
      ],
    };
    const ops = readDeltaOps(body);
    expect(ops.map((o) => o.index)).toEqual([0, 1, 2, 3]);
    expect(ops[0]!.kind).toBe('add');
    expect(ops[0]!.nodeRef).toBe('n5');
    expect(ops[1]!.kind).toBe('remove');
    expect(ops[1]!.nodeRef).toBe('node_old');
    expect(ops[2]!.from).toBe('n2');
    expect(ops[2]!.to).toBe('n5');
  });

  it('边 op 兼容裹在 edge 对象 / 备用字段名', () => {
    const ops = readDeltaOps({
      operations: [
        { op: 'add_edge', edge: { from: 'a', to: 'b' } },
        { op: 'remove_edge', source: 'a', target: 'c' },
      ],
    });
    expect(ops[0]!.from).toBe('a');
    expect(ops[0]!.to).toBe('b');
    expect(ops[1]!.from).toBe('a');
    expect(ops[1]!.to).toBe('c');
  });

  it('守空：无 operations → 空数组', () => {
    expect(readDeltaOps(undefined)).toEqual([]);
    expect(readDeltaOps({})).toEqual([]);
  });
});

describe('revalidateDelta（剩余 op 集结构重验）', () => {
  const base = {
    nodeLimit: 12,
    currentNodeIds: ['n1', 'n2', 'n3'],
    currentEdges: [['n1', 'n2'], ['n2', 'n3']] as Array<[string, string]>,
    activeNodeIds: new Set<string>(),
  };

  it('无问题的增量 → 空错误', () => {
    const ops = readDeltaOps({ operations: [{ op: 'add_node', node: { temp_id: 'n5' } }, { op: 'add_edge', from: 'n3', to: 'n5' }] });
    expect(revalidateDelta(ops, base)).toEqual([]);
  });

  it('引用悬空：新增边引用不存在节点 → DANGLING_REF', () => {
    const ops = readDeltaOps({ operations: [{ op: 'add_edge', from: 'n3', to: 'ghost' }] });
    expect(revalidateDelta(ops, base).some((e) => e.code === 'DANGLING_REF')).toBe(true);
  });

  it('成环 → GRAPH_CYCLE', () => {
    const ops = readDeltaOps({ operations: [{ op: 'add_edge', from: 'n3', to: 'n1' }] });
    expect(revalidateDelta(ops, base).some((e) => e.code === 'GRAPH_CYCLE')).toBe(true);
  });

  it('超上限 → NODE_LIMIT', () => {
    const ops = readDeltaOps({ operations: [{ op: 'add_node', node: { temp_id: 'n5' } }] });
    expect(revalidateDelta(ops, { ...base, nodeLimit: 3 }).some((e) => e.code === 'NODE_LIMIT')).toBe(true);
  });

  it('删除进行中/评审中节点 → NODE_ACTIVE', () => {
    const ops = readDeltaOps({ operations: [{ op: 'remove_node', temp_id: 'n2' }] });
    const env = { ...base, activeNodeIds: new Set(['n2']) };
    expect(revalidateDelta(ops, env).some((e) => e.code === 'NODE_ACTIVE')).toBe(true);
  });

  it('删节点级联去边后不误报悬空', () => {
    const ops = readDeltaOps({ operations: [{ op: 'remove_node', temp_id: 'n3' }] });
    // n2→n3 因 n3 删除而被级联去除，不应报 DANGLING_REF
    expect(revalidateDelta(ops, base)).toEqual([]);
  });
});
