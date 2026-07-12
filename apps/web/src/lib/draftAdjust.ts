// M6b 草稿层调整应用 + 分层布局（纯函数，无 IO、无时钟）。
// applyAdjustments = 服务端 orchestration/draft.apply_adjustments 的 TS 镜像：AwaitingConfirm 期间
// 人类调整在客户端累积（契约 B §4.10 设计决策——不设逐笔端点），随 confirm 一次性提交；此处按序
// 应用到 proposal.body 得「调整后 body」供 lib/decomposition.validateProposal 实时防呆 + 草稿层渲染。
// 语义与服务端逐条对齐：按序应用 / remove_node 级联删关联边 / add_edge 幂等 / edit_node 整键替换 /
// 原 body 无 edges 键且调整后仍无边 → 还原缺席（指纹不因过程性补键漂移）。**不参与 golden 双跑**
// （golden 只覆盖 decomposition 校验内核）；此镜像以行为测试对齐服务端。
//
// layerize = 客户端 Kahn 最长路径分层（草稿节点无 pos_x/pos_y——proposals.body 不落画布表），
// 供草稿层把节点铺成有向 DAG（col=层、row=层内序）。

// ---- 调整 op 形状（与服务端 _OP_KEYS 对齐；confirm.adjustments 逐项即此形状）
export type Adjustment =
  | { op: 'add_node'; node: Record<string, unknown> }
  | { op: 'remove_node'; temp_id: string }
  | { op: 'add_edge'; from: string; to: string }
  | { op: 'remove_edge'; from: string; to: string }
  | { op: 'edit_node'; temp_id: string; changes: Record<string, unknown> }
  | { op: 'edit_merge_plan'; merge_plan: string | null };

type Dict = Record<string, unknown>;
function isDict(v: unknown): v is Dict {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}
function clone<T>(v: T): T {
  // body 恒为 JSON 值（proposals.body），JSON 深拷贝足够且跨环境稳定。
  return JSON.parse(JSON.stringify(v)) as T;
}

/** applyAdjustments 遇非法 op 时抛（与服务端 422 对齐）——UI 仅构造合法 op，调用方仍应 try/catch 兜底。 */
export class AdjustmentError extends Error {}

/**
 * 确定性应用调整清单 → 调整后 body（op 语义见文件头）。纯函数：同 (body, adjustments) 恒得同结果。
 * 零调整 = 恒等（逐键不动——edges 缺席的 body 不得被补出空数组改变指纹）。
 */
export function applyAdjustments(body: unknown, adjustments: Adjustment[]): Dict {
  const result: Dict = isDict(body) ? clone(body) : {};
  if (adjustments.length === 0) return result;

  const hadEdges = Array.isArray(result['edges']);
  let nodes = Array.isArray(result['nodes']) ? (result['nodes'] as unknown[]) : [];
  result['nodes'] = nodes;
  let edges = Array.isArray(result['edges']) ? (result['edges'] as unknown[]) : [];
  result['edges'] = edges;

  const findNode = (tempId: string): Dict | null => {
    for (const n of nodes) if (isDict(n) && n['temp_id'] === tempId) return n;
    return null;
  };

  adjustments.forEach((adj, idx) => {
    switch (adj.op) {
      case 'add_node': {
        if (!isDict(adj.node) || typeof adj.node['temp_id'] !== 'string') {
          throw new AdjustmentError(`调整项 [${idx}] add_node 须携完整节点对象（含 temp_id）`);
        }
        if (findNode(adj.node['temp_id'] as string) !== null) {
          throw new AdjustmentError(`调整项 [${idx}] temp_id '${adj.node['temp_id']}' 已存在`);
        }
        nodes.push(clone(adj.node));
        break;
      }
      case 'remove_node': {
        if (findNode(adj.temp_id) === null) {
          throw new AdjustmentError(`调整项 [${idx}] remove_node 目标 '${adj.temp_id}' 不存在`);
        }
        nodes = nodes.filter((n) => !(isDict(n) && n['temp_id'] === adj.temp_id));
        result['nodes'] = nodes;
        // 连带删除关联边（画布删节点同语义——确定性，客户端无需补 remove_edge）。
        edges = edges.filter(
          (e) => !(isDict(e) && (e['from'] === adj.temp_id || e['to'] === adj.temp_id)),
        );
        result['edges'] = edges;
        break;
      }
      case 'add_edge': {
        const exists = edges.some(
          (e) => isDict(e) && e['from'] === adj.from && e['to'] === adj.to,
        );
        if (!exists) edges.push({ from: adj.from, to: adj.to }); // 幂等：同 (from,to) 已存在则忽略
        break;
      }
      case 'remove_edge': {
        const before = edges.length;
        edges = edges.filter(
          (e) => !(isDict(e) && e['from'] === adj.from && e['to'] === adj.to),
        );
        if (edges.length === before) {
          throw new AdjustmentError(
            `调整项 [${idx}] remove_edge 目标 (${adj.from} → ${adj.to}) 不存在`,
          );
        }
        result['edges'] = edges;
        break;
      }
      case 'edit_node': {
        const target = findNode(adj.temp_id);
        if (target === null) {
          throw new AdjustmentError(`调整项 [${idx}] edit_node 目标 '${adj.temp_id}' 不存在`);
        }
        for (const [k, v] of Object.entries(adj.changes)) target[k] = clone(v); // 整键替换
        break;
      }
      case 'edit_merge_plan': {
        result['merge_plan'] = adj.merge_plan;
        break;
      }
    }
  });

  // 原 body 无 edges 键且调整后仍无边 → 还原缺席（缺席 ≢ 空数组：指纹不因过程性补键漂移）。
  if (!hadEdges && Array.isArray(result['edges']) && (result['edges'] as unknown[]).length === 0) {
    delete result['edges'];
  }
  return result;
}

// ---- 布局：Kahn 最长路径分层（确定性，同图同解）
export interface LayerPos {
  col: number;
  row: number;
}
export function layerize(
  nodeIds: string[],
  edges: Array<[string, string]>,
): Map<string, LayerPos> {
  const nodes = [...new Set(nodeIds)];
  const known = new Set(nodes);
  const preds = new Map<string, string[]>();
  const succ = new Map<string, string[]>();
  const indeg = new Map<string, number>();
  for (const n of nodes) {
    preds.set(n, []);
    succ.set(n, []);
    indeg.set(n, 0);
  }
  for (const [a, b] of edges) {
    if (!known.has(a) || !known.has(b)) continue; // 悬空端点不参与布局
    succ.get(a)!.push(b);
    preds.get(b)!.push(a);
    indeg.set(b, indeg.get(b)! + 1);
  }
  // Kahn 拓扑序（indeg 0 入队，保持 nodes 原序）
  const ind = new Map(indeg);
  const queue = nodes.filter((n) => ind.get(n) === 0);
  const order: string[] = [];
  while (queue.length > 0) {
    const n = queue.shift()!;
    order.push(n);
    for (const m of succ.get(n)!) {
      ind.set(m, ind.get(m)! - 1);
      if (ind.get(m) === 0) queue.push(m);
    }
  }
  for (const n of nodes) if (!order.includes(n)) order.push(n); // 环残留兜底（不应出现于合法提案）

  const col = new Map<string, number>();
  for (const n of order) {
    let c = 0;
    for (const p of preds.get(n)!) c = Math.max(c, (col.get(p) ?? 0) + 1);
    col.set(n, c);
  }
  const rowByCol = new Map<number, number>();
  const pos = new Map<string, LayerPos>();
  for (const n of nodes) {
    const c = col.get(n) ?? 0;
    const r = rowByCol.get(c) ?? 0;
    pos.set(n, { col: c, row: r });
    rowByCol.set(c, r + 1);
  }
  return pos;
}

// ---- 从 body 节点安全读出展示字段（body 形状由服务端提交期校验，此处守空防崩）
export interface DraftNodeView {
  temp_id: string;
  title: string;
  kind: 'agent' | 'system';
  system_action?: string;
  suggested_owner: string | null;
  writes_code: boolean;
}
export function readNodeView(node: unknown): DraftNodeView | null {
  if (!isDict(node) || typeof node['temp_id'] !== 'string') return null;
  const kind = node['kind'] === 'system' ? 'system' : 'agent';
  return {
    temp_id: node['temp_id'],
    title: typeof node['title'] === 'string' ? node['title'] : '(未命名)',
    kind,
    system_action: typeof node['system_action'] === 'string' ? node['system_action'] : undefined,
    suggested_owner: typeof node['suggested_owner'] === 'string' ? node['suggested_owner'] : null,
    writes_code: node['writes_code'] === true,
  };
}

/** 从 body 读节点/边数组（守空）。 */
export function readNodes(body: unknown): unknown[] {
  return isDict(body) && Array.isArray(body['nodes']) ? (body['nodes'] as unknown[]) : [];
}
export function readEdges(body: unknown): Array<{ from: string; to: string }> {
  const raw = isDict(body) && Array.isArray(body['edges']) ? (body['edges'] as unknown[]) : [];
  const out: Array<{ from: string; to: string }> = [];
  for (const e of raw) {
    if (isDict(e) && typeof e['from'] === 'string' && typeof e['to'] === 'string') {
      out.push({ from: e['from'], to: e['to'] });
    }
  }
  return out;
}
