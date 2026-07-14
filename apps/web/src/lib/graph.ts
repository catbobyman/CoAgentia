// 画布图内核的 TS 镜像:环检测 + 阻塞派生（契约 A §6 / 内核 packages/contracts kernel/graph.py）。
// 与 server 同一组黄金判例对照（packages/fixtures/golden/graph.json）逐条断言,防前后端双实现漂移
// （纪律 8）。纯结构函数:无 IO、无时钟、无随机;对输入排序保证跨语言确定性（同图同解）。
import type { CanvasEdgePublic, CanvasNodePublic, TaskPublic } from '@coagentia/contracts-ts';

const WHITE = 0;
const GRAY = 1;
const BLACK = 2;

// 码点升序比较（对齐 Python sorted;画布节点 id 全 ASCII[字母/ULID] → 与 UTF-16 序一致）。
function cmp(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

// 构建邻接表:节点集 = nodeIds ∪ 边端点;每个邻接按码点升序（确定性）。
function adjacency(nodeIds: string[], edges: Array<[string, string]>): Map<string, string[]> {
  const adj = new Map<string, string[]>();
  for (const n of nodeIds) if (!adj.has(n)) adj.set(n, []);
  for (const [a, b] of edges) {
    if (!adj.has(a)) adj.set(a, []);
    if (!adj.has(b)) adj.set(b, []);
  }
  for (const [a, b] of edges) adj.get(a)!.push(b);
  for (const list of adj.values()) list.sort(cmp);
  return adj;
}

/**
 * 有向图三色 DFS 检测环:返回构成环的节点 id 路径（从环入口到回边源,自环含单节点）,无环返回 null。
 * 起点/邻接按码点升序遍历 → 同一图返回路径唯一确定（与 server 镜像逐字可比）。
 */
export function detectCycle(nodeIds: string[], edges: Array<[string, string]>): string[] | null {
  const adj = adjacency(nodeIds, edges);
  const color = new Map<string, number>();
  for (const k of adj.keys()) color.set(k, WHITE);

  for (const start of [...adj.keys()].sort(cmp)) {
    if (color.get(start) !== WHITE) continue;
    const stack: Array<[string, number]> = [[start, 0]];
    const path: string[] = [start];
    color.set(start, GRAY);
    while (stack.length > 0) {
      const top = stack[stack.length - 1]!;
      const node = top[0];
      const i = top[1];
      const neighbors = adj.get(node)!;
      if (i < neighbors.length) {
        top[1] = i + 1;
        const nxt = neighbors[i]!;
        if (color.get(nxt) === GRAY) return path.slice(path.indexOf(nxt)); // 回边命中环
        if (color.get(nxt) === WHITE) {
          color.set(nxt, GRAY);
          stack.push([nxt, 0]);
          path.push(nxt);
        }
      } else {
        color.set(node, BLACK); // 邻接耗尽,出栈染黑
        stack.pop();
        path.pop();
      }
    }
  }
  return null;
}

// W9 partial 档字面量（= UpstreamPolicy 'partial';镜像 kernel/graph.py._PARTIAL）。
const PARTIAL = 'partial';

/**
 * 派生 blocked 节点集(W9 双档 satisfied,M8b L7):节点 blocked ⇔ 至少一个直接前驱不在该节点适用的
 * satisfied 集中;无前驱的根永不 blocked。纯结构函数——上游"完成"语义与节点 policy 由 caller 折进入参。
 * - doneSatisfied:strict 判据集(上游 Done/success,现状语义)。
 * - terminalSatisfied:partial 判据集(上游到达终态,含 closed/failed);省略 ≡ doneSatisfied(纯 strict 回归)。
 * - policy:{nodeId:'strict'|'partial'},缺席默认 strict;放行档是被评估节点(下游)的属性。
 */
export function deriveBlocked(
  nodeIds: string[],
  edges: Array<[string, string]>,
  doneSatisfied: Set<string>,
  terminalSatisfied?: Set<string>,
  policy?: Record<string, string>,
): Set<string> {
  const terminal = terminalSatisfied ?? doneSatisfied;
  const pol = policy ?? {};
  const preds = new Map<string, string[]>();
  for (const n of nodeIds) preds.set(n, []);
  for (const [a, b] of edges) {
    const list = preds.get(b);
    if (list) list.push(a);
  }
  const blocked = new Set<string>();
  for (const [n, ps] of preds) {
    const sat = pol[n] === PARTIAL ? terminal : doneSatisfied;
    if (ps.some((p) => !sat.has(p))) blocked.add(n);
  }
  return blocked;
}

/**
 * 画布「就绪/阻塞」派生（纪律 8 单源）——画布着色(satisfied+blocked)与看板 blocked 徽标共用此一处,
 * 避免 satisfied 组装规则在 CanvasTab / BoardTab 两处漂移。**W9 双档(M8b L7)**：组装
 * doneSatisfied(agent done / system success——现状语义,用于着色的"已完成"集)与 terminalSatisfied
 * (agent∈{done,closed} / system∈{success,failed}——partial 节点放行判据),按各节点 upstream_policy
 * 交 deriveBlocked 选档。返回的 satisfied = doneSatisfied(着色语义不变:partial 放行但未 Done 的汇总
 * 节点不着"已完成"色)。此处只做双档组装 + 调用,detectCycle/deriveBlocked 算法本体不动(与 server 平价)。
 */
export function deriveCanvasBlocked(
  nodes: CanvasNodePublic[],
  edges: CanvasEdgePublic[],
  taskById: Record<string, TaskPublic>,
): { satisfied: Set<string>; blocked: Set<string> } {
  const doneSatisfied = new Set<string>();
  const terminalSatisfied = new Set<string>();
  const policy: Record<string, string> = {};
  for (const n of nodes) {
    if (n.upstream_policy === PARTIAL) policy[n.id] = PARTIAL;
    if (n.kind === 'agent') {
      const st = taskById[n.task_id ?? '']?.status;
      if (st === 'done') {
        doneSatisfied.add(n.id);
        terminalSatisfied.add(n.id);
      } else if (st === 'closed') {
        terminalSatisfied.add(n.id);
      }
    } else if (n.system_status === 'success') {
      doneSatisfied.add(n.id);
      terminalSatisfied.add(n.id);
    } else if (n.system_status === 'failed') {
      terminalSatisfied.add(n.id);
    }
  }
  const blocked = deriveBlocked(
    nodes.map((n) => n.id),
    edges.map((e) => [e.from_node_id, e.to_node_id]),
    doneSatisfied,
    terminalSatisfied,
    policy,
  );
  return { satisfied: doneSatisfied, blocked };
}

/**
 * 连边预判:把待加边 source→target 并入现有边跑 detectCycle,成环返回 true。
 * onConnect 前置闸——成环则前端红色反馈、不发请求（server GRAPH_CYCLE 仍作兜底）。
 */
export function wouldCreateCycle(
  edges: Array<[string, string]>,
  source: string,
  target: string,
): boolean {
  if (source === target) return true;
  const nodeIds = new Set<string>([source, target]);
  for (const [a, b] of edges) {
    nodeIds.add(a);
    nodeIds.add(b);
  }
  return detectCycle([...nodeIds], [...edges, [source, target]]) !== null;
}
