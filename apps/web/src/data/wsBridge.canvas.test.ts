// wsBridge 画布 handler 回归:canvas.* 事件 data 载 canvas_id(非 channel_id),桥接按 canvas.id
// 反查频道快照(qk.canvas)并 setQueryData patch。removed 事件只带 node_id/edge_id,按内容命中反查。
// 运行:pnpm -F @coagentia/web test
import { QueryClient } from '@tanstack/react-query';
import { describe, expect, it } from 'vitest';

import type { CanvasDetail, CanvasEdgePublic, CanvasNodePublic, Envelope } from '@coagentia/contracts-ts';

import { qk } from '../lib/queryKeys';
import { applyEnvelope } from './wsBridge';

const CH = 'ch_build';
const CV = 'cv_1';

function node(id: string, over: Partial<CanvasNodePublic> = {}): CanvasNodePublic {
  return { id, canvas_id: CV, kind: 'agent', pos_x: 0, pos_y: 0, created_at: '2026-07-10T00:00:00Z', ...over };
}
function edge(id: string, from: string, to: string): CanvasEdgePublic {
  return { id, canvas_id: CV, from_node_id: from, to_node_id: to };
}

function seed(detail?: Partial<CanvasDetail>): QueryClient {
  const qc = new QueryClient();
  const base: CanvasDetail = {
    canvas: {
      id: CV, channel_id: CH, workspace_id: 'ws_1',
      baseline_hash: 'h0', baseline_version: 1, updated_at: '2026-07-10T00:00:00Z',
    },
    nodes: [node('n_1')],
    edges: [],
  };
  qc.setQueryData<CanvasDetail>(qk.canvas(CH), { ...base, ...detail });
  return qc;
}

function env(type: string, data: unknown): Envelope {
  // channel_id 故意省略:canvas.* 事件仅载 canvas_id,验证桥接的反查路径。
  return { type, workspace_id: 'ws_1', seq: 1, key: 'k1', at: '2026-07-10T01:00:00Z', data } as Envelope;
}

const read = (qc: QueryClient) => qc.getQueryData<CanvasDetail>(qk.canvas(CH))!;

describe('wsBridge canvas.* handler(按 canvas_id 反查频道快照)', () => {
  it('node_added:按 node.canvas_id 命中快照并 upsert 节点', () => {
    const qc = seed();
    applyEnvelope(qc, env('canvas.node_added', { node: node('n_2') }));
    expect(read(qc).nodes!.map((n) => n.id)).toEqual(['n_1', 'n_2']);
  });

  it('node_updated:同 id 替换(load-state,重复应用无害)', () => {
    const qc = seed();
    applyEnvelope(qc, env('canvas.node_updated', { node: node('n_1', { pos_x: 99, pos_y: 88 }) }));
    const n1 = read(qc).nodes!.find((n) => n.id === 'n_1')!;
    expect([n1.pos_x, n1.pos_y]).toEqual([99, 88]);
    expect(read(qc).nodes).toHaveLength(1);
  });

  it('node_removed:仅带 node_id → 按内容命中,删节点并清理其悬挂边', () => {
    const qc = seed({ nodes: [node('n_1'), node('n_2')], edges: [edge('e_1', 'n_1', 'n_2')] });
    applyEnvelope(qc, env('canvas.node_removed', { node_id: 'n_2' }));
    expect(read(qc).nodes!.map((n) => n.id)).toEqual(['n_1']);
    expect(read(qc).edges).toHaveLength(0);
  });

  it('edge_added:按 edge.canvas_id 命中并 upsert;幂等去重', () => {
    const qc = seed({ nodes: [node('n_1'), node('n_2')] });
    applyEnvelope(qc, env('canvas.edge_added', { edge: edge('e_1', 'n_1', 'n_2') }));
    applyEnvelope(qc, env('canvas.edge_added', { edge: edge('e_1', 'n_1', 'n_2') }));
    expect(read(qc).edges).toHaveLength(1);
  });

  it('edge_removed:仅带 edge_id → 按内容命中删除', () => {
    const qc = seed({ nodes: [node('n_1'), node('n_2')], edges: [edge('e_1', 'n_1', 'n_2')] });
    applyEnvelope(qc, env('canvas.edge_removed', { edge_id: 'e_1' }));
    expect(read(qc).edges).toHaveLength(0);
  });

  it('layout_updated:按 canvas_id 命中,整批坐标覆盖', () => {
    const qc = seed({ nodes: [node('n_1'), node('n_2')] });
    applyEnvelope(qc, env('canvas.layout_updated', {
      canvas_id: CV,
      positions: [{ node_id: 'n_1', x: 12, y: 34 }],
    }));
    const n1 = read(qc).nodes!.find((n) => n.id === 'n_1')!;
    expect([n1.pos_x, n1.pos_y]).toEqual([12, 34]);
  });

  it('baseline_advanced:更新画布头基线版本/指纹', () => {
    const qc = seed();
    applyEnvelope(qc, env('canvas.baseline_advanced', {
      canvas_id: CV, baseline_version: 7, baseline_hash: 'hZ',
    }));
    expect(read(qc).canvas.baseline_version).toBe(7);
    expect(read(qc).canvas.baseline_hash).toBe('hZ');
  });

  it('canvas_id 不匹配任何缓存快照 → 安全忽略(不抛错)', () => {
    const qc = seed();
    expect(() =>
      applyEnvelope(qc, env('canvas.node_added', { node: node('n_x', { canvas_id: 'cv_other' }) })),
    ).not.toThrow();
    expect(read(qc).nodes!.map((n) => n.id)).toEqual(['n_1']); // 原快照不受影响
  });
});
