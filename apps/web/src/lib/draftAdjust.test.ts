// M6b 草稿调整应用（服务端 draft.apply_adjustments 镜像）+ 分层布局单测。
import { describe, expect, it } from 'vitest';

import type { Adjustment } from './draftAdjust';
import { applyAdjustments, layerize, readEdges, readNodeView } from './draftAdjust';

const BODY = {
  version: 'coagentia.decomposition.v1',
  mode: 'decompose',
  summary: 's',
  nodes: [
    { temp_id: 'a', title: '需求', kind: 'agent', suggested_owner: null },
    { temp_id: 'b', title: '实现', kind: 'agent', suggested_owner: 'mem_1' },
    { temp_id: 'c', title: '评审', kind: 'agent', suggested_owner: null },
  ],
  edges: [{ from: 'a', to: 'b' }, { from: 'b', to: 'c' }],
};

describe('applyAdjustments', () => {
  it('零调整 = 恒等（逐键不动）', () => {
    expect(applyAdjustments(BODY, [])).toEqual(BODY);
  });

  it('remove_node 级联删除关联边（画布删节点同语义）', () => {
    const out = applyAdjustments(BODY, [{ op: 'remove_node', temp_id: 'b' }]);
    expect((out['nodes'] as unknown[]).length).toBe(2);
    expect(out['edges']).toEqual([]); // a→b、b→c 双删
  });

  it('edit_node 整键替换（title/suggested_owner）', () => {
    const adj: Adjustment[] = [
      { op: 'edit_node', temp_id: 'a', changes: { title: '需求框定', suggested_owner: 'mem_9' } },
    ];
    const out = applyAdjustments(BODY, adj);
    const a = (out['nodes'] as Array<Record<string, unknown>>).find((n) => n['temp_id'] === 'a');
    expect(a?.['title']).toBe('需求框定');
    expect(a?.['suggested_owner']).toBe('mem_9');
  });

  it('add_edge 幂等（同 from,to 已存在则忽略）', () => {
    const out = applyAdjustments(BODY, [{ op: 'add_edge', from: 'a', to: 'b' }]);
    expect((out['edges'] as unknown[]).length).toBe(2);
    const out2 = applyAdjustments(BODY, [{ op: 'add_edge', from: 'a', to: 'c' }]);
    expect((out2['edges'] as unknown[]).length).toBe(3);
  });

  it('remove_edge 删除指定边', () => {
    const out = applyAdjustments(BODY, [{ op: 'remove_edge', from: 'a', to: 'b' }]);
    expect(out['edges']).toEqual([{ from: 'b', to: 'c' }]);
  });

  it('按序应用（多 op 组合）', () => {
    const out = applyAdjustments(BODY, [
      { op: 'remove_node', temp_id: 'c' },
      { op: 'add_edge', from: 'a', to: 'b' },
      { op: 'edit_node', temp_id: 'b', changes: { title: 'X' } },
    ]);
    expect((out['nodes'] as unknown[]).length).toBe(2);
    const b = (out['nodes'] as Array<Record<string, unknown>>).find((n) => n['temp_id'] === 'b');
    expect(b?.['title']).toBe('X');
  });

  it('原 body 无 edges 键且调整后仍无边 → 还原缺席（指纹不因补键漂移）', () => {
    const single = { version: 'coagentia.decomposition.v1', mode: 'single_task', nodes: [{ temp_id: 'x', title: 'x' }] };
    const out = applyAdjustments(single, [{ op: 'edit_node', temp_id: 'x', changes: { title: 'y' } }]);
    expect('edges' in out).toBe(false);
  });

  it('remove_node 目标不存在 → 抛（与服务端 422 对齐）', () => {
    expect(() => applyAdjustments(BODY, [{ op: 'remove_node', temp_id: 'zzz' }])).toThrow();
  });
});

describe('layerize', () => {
  it('Kahn 最长路径分层：线性链 a→b→c 得 col 0/1/2', () => {
    const pos = layerize(['a', 'b', 'c'], [['a', 'b'], ['b', 'c']]);
    expect(pos.get('a')?.col).toBe(0);
    expect(pos.get('b')?.col).toBe(1);
    expect(pos.get('c')?.col).toBe(2);
  });

  it('并行分支同层不同 row', () => {
    // a→c、b→c：a、b 均根（col 0，row 0/1），c col 1
    const pos = layerize(['a', 'b', 'c'], [['a', 'c'], ['b', 'c']]);
    expect(pos.get('a')?.col).toBe(0);
    expect(pos.get('b')?.col).toBe(0);
    expect(pos.get('a')?.row).not.toBe(pos.get('b')?.row);
    expect(pos.get('c')?.col).toBe(1);
  });
});

describe('readNodeView / readEdges', () => {
  it('readNodeView 守空 + 读 kind/owner/writes_code', () => {
    expect(readNodeView({})).toBeNull();
    const v = readNodeView({ temp_id: 'a', title: 'T', kind: 'system', system_action: 'merge' });
    expect(v?.kind).toBe('system');
    expect(v?.system_action).toBe('merge');
  });
  it('readEdges 只保留 from/to 均字符串的边', () => {
    expect(readEdges({ edges: [{ from: 'a', to: 'b' }, { from: 1 }] })).toEqual([{ from: 'a', to: 'b' }]);
  });
});
