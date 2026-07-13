// 拆解提案同构校验内核的 TS 镜像（契约 B §12.2 / 拆解设计 §5–§6；py 权威 = packages/contracts
// kernel/decomposition.py）。行为与 py 逐字节一致：错误 code/path/message/hint 文本、遍历顺序、排序、
// 指纹全一致。与 server 同一组黄金判例对照（packages/fixtures/golden/decomposition.json）双跑（纪律 8）。
// 用途：草稿画布实时校验、确认按钮防呆（前端不复制判定逻辑，只复制内核）。纯函数、无 IO。
//
// env 约定（同 py docstring）：{node_limit, member_ids, bound_project_ids}；ref 语义 = id 精确匹配。
// 注：V3 未知字段遍历用 Object.keys（插入序），与 py dict 一致——前提是字段名非纯整数（schema 字段名
// 皆标识符，天然满足；JSON 数字键在两语言解析后顺序本就不同，不在 schema 值域内）。
import { detectCycle } from './graph';
import { cmpCodepoint, fingerprint } from './fingerprint';

const SCHEMA_DECOMPOSITION_V1 = 'coagentia.decomposition.v1';
const SCHEMA_TASK_PLAN_V1 = 'coagentia.task-plan.v1';

export interface DecompError {
  code: string;
  path: string;
  message: string;
  hint?: string;
}

export interface DecompEnv {
  node_limit: number;
  member_ids: string[];
  bound_project_ids: string[];
}

// -------- 安全子集值域（**全部层级** §5.1；与 py 常量对齐） --------
// 深层允许集须 ≥ 落地消费严格度（TaskPlanBody/AcceptanceCriterion extra="forbid"）——
// 内核放行的提案落地时 model_validate 不得爆炸（F7/F8 域不变量）。
const SYSTEM_INJECTED = new Set(['revision', 'proposed_by', 'proposed_at']);
const TOP_ALLOWED = new Set(['version', 'source', 'mode', 'summary', 'nodes', 'edges', 'merge_plan']);
const NODE_ALLOWED = new Set([
  'temp_id', 'title', 'kind', 'system_action', 'command',
  'task_plan', 'suggested_owner', 'project', 'writes_code',
]);
const PLAN_ALLOWED = new Set([
  'version', 'goal', 'acceptance_criteria', 'defaults_decided', 'out_of_scope',
]);
const AC_ALLOWED = new Set(['id', 'statement', 'verify_by', 'verify_ref']);
const EDGE_ALLOWED = new Set(['from', 'to']);
const TOP_ALIASES: Record<string, string> = { tasks: 'nodes', dependencies: 'edges', deps: 'edges' };
const NODE_ALIASES: Record<string, string> = {
  id: 'temp_id', name: 'title', owner: 'suggested_owner', assignee: 'suggested_owner',
};
const MODES = new Set(['decompose', 'single_task']);
const KINDS = new Set(['agent', 'system']);
const SYSTEM_ACTIONS = new Set(['merge', 'check']);
const VERIFY_BY = new Set(['command', 'inspect', 'manual']);
const TEMP_ID_RE = /^[A-Za-z0-9_-]{1,32}$/;

const CONTROL_OPEN = '<control>';
const CONTROL_CLOSE = '</control>';

// -------- 类型守卫 --------
type Dict = Record<string, unknown>;
function isStr(v: unknown): v is string {
  return typeof v === 'string';
}
function isBool(v: unknown): v is boolean {
  return typeof v === 'boolean';
}
function isDict(v: unknown): v is Dict {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}
function has(obj: Dict, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(obj, key);
}
function err(code: string, path: string, message: string, hint?: string): DecompError {
  return hint === undefined ? { code, path, message } : { code, path, message, hint };
}
// 长度语义钉死为 Unicode 码点（对齐 py len()）——.length 是 UTF-16 码元，增补平面字符（emoji 等）
// 会两侧分歧，禁止用于 title/summary 的边界判定。
function cpLen(s: string): number {
  let n = 0;
  for (const _ of s) n++;
  return n;
}

// ---------------------------------------------------------------- parse_control（V1）

// 按 <control>/</control> 标签定界扫描全部完整块（围栏字符不参与定界，故天然容忍围栏）；
// 手写扫描保证跨语言逐字节一致：每个 <control> 配其后最近 </control>；残缺开标签不计块。
function extractControlBlocks(text: string): string[] {
  const blocks: string[] = [];
  let i = 0;
  for (;;) {
    const start = text.indexOf(CONTROL_OPEN, i);
    if (start === -1) break;
    const contentStart = start + CONTROL_OPEN.length;
    const end = text.indexOf(CONTROL_CLOSE, contentStart);
    if (end === -1) break;
    blocks.push(text.slice(contentStart, end));
    i = end + CONTROL_CLOSE.length;
  }
  return blocks;
}

/** 提取恰一个 <control> 块并解析为 JSON 对象。成功 → {body, error:null}；失败 → {body:null, error}。 */
export function parseControl(text: string): { body: Dict | null; error: DecompError | null } {
  const blocks = extractControlBlocks(text);
  if (blocks.length === 0) {
    return {
      body: null,
      error: err('CONTROL_PARSE', '$',
        '未找到 <control> 控制块；提案正文须恰含一个 <control>{…}</control> 块'),
    };
  }
  if (blocks.length > 1) {
    return {
      body: null,
      error: err('CONTROL_PARSE', '$',
        `发现 ${blocks.length} 个 <control> 控制块；恰需一个（围栏内外重复也算多块）`),
    };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(blocks[0]!);
  } catch {
    return { body: null, error: err('CONTROL_PARSE', '$', '<control> 控制块内不是合法 JSON') };
  }
  if (!isDict(parsed)) {
    return { body: null, error: err('CONTROL_PARSE', '$', '<control> 控制块必须是 JSON 对象') };
  }
  return { body: parsed, error: null };
}

/**
 * 剥离全部 `<control>…</control>` 块，返回给人读的散文（提案消息正文渲染用）。
 * **非校验判定、不参与 golden 双跑**——仅复用 §5.3 的 `<control>` 定界（同 extractControlBlocks 的
 * 标签扫描），供提案卡在消息流剥离控制块后渲染散文。残缺开标签（无配对闭标签）之后一律丢弃，
 * 与 extractControlBlocks「残缺不计块」一致。
 */
export function stripControl(text: string): string {
  let out = '';
  let i = 0;
  for (;;) {
    const start = text.indexOf(CONTROL_OPEN, i);
    if (start === -1) {
      out += text.slice(i);
      break;
    }
    out += text.slice(i, start);
    const end = text.indexOf(CONTROL_CLOSE, start + CONTROL_OPEN.length);
    if (end === -1) break; // 残缺开标签：其后（含标签）不当散文渲染
    i = end + CONTROL_CLOSE.length;
  }
  return out.trim();
}

// ---------------------------------------------------------------- 近似匹配（EDGE_UNKNOWN_NODE hint）

function levenshtein(a: string, b: string): number {
  if (a === b) return 0;
  // 距离语义 = Unicode 码点（对齐 py str 索引）——.length/下标是 UTF-16 码元，
  // 增补平面字符会与 py 权威算出不同 nearest，hint 文本双侧分歧。
  const ca = Array.from(a);
  const cb = Array.from(b);
  const la = ca.length;
  const lb = cb.length;
  if (la === 0) return lb;
  if (lb === 0) return la;
  let prev = Array.from({ length: lb + 1 }, (_, i) => i);
  for (let i = 1; i <= la; i++) {
    const cur = [i, ...Array<number>(lb).fill(0)];
    for (let j = 1; j <= lb; j++) {
      const cost = ca[i - 1] === cb[j - 1] ? 0 : 1;
      cur[j] = Math.min(prev[j]! + 1, cur[j - 1]! + 1, prev[j - 1]! + cost);
    }
    prev = cur;
  }
  return prev[lb]!;
}

function nearest(missing: string, candidates: string[]): string | null {
  let best: string | null = null;
  let bestD = -1;
  for (const cand of [...candidates].sort(cmpCodepoint)) {
    const d = levenshtein(missing, cand);
    if (best === null || d < bestD) {
      best = cand;
      bestD = d;
    }
  }
  return best;
}

function unknownNodeHint(missing: string, tempIds: string[]): string {
  if (tempIds.length === 0) return '提案未声明任何节点 temp_id';
  const n = nearest(missing, tempIds);
  return `现有 temp_id：${tempIds.join('、')}；是否想引用 '${n}'？`;
}

// ---------------------------------------------------------------- validate_proposal（V2–V14）

/** V2–V14 全量收集校验。返回错误列表（空 = 通过）。纯函数。 */
export function validateProposal(body: unknown, env: DecompEnv): DecompError[] {
  const errors: DecompError[] = [];
  if (!isDict(body)) {
    errors.push(err('FIELD_INVALID', '$', '提案必须为 JSON 对象'));
    return errors;
  }

  const nodeLimit = env.node_limit ?? 12;
  const memberIds = new Set(env.member_ids ?? []);
  const boundProjectIds = new Set(env.bound_project_ids ?? []);

  // -- V2 version const
  if (body['version'] !== SCHEMA_DECOMPOSITION_V1) {
    errors.push(err('BAD_VERSION', '$.version', `version 必须为 '${SCHEMA_DECOMPOSITION_V1}'`));
  }

  // -- V3 top-level 未知字段（插入序遍历，跳过允许集与系统注入字段）
  for (const key of Object.keys(body)) {
    if (TOP_ALLOWED.has(key) || SYSTEM_INJECTED.has(key)) continue;
    const alias = TOP_ALIASES[key];
    errors.push(err('UNKNOWN_FIELD', `$.${key}`,
      `未知字段 '${key}'（提案 schema 不接受额外字段）`,
      alias ? `是否想用 '${alias}'？` : undefined));
  }

  // -- V4 top-level 必填 / 类型 / 边界
  if (!has(body, 'source')) {
    errors.push(err('FIELD_INVALID', '$.source', 'source 为必填项（本频道 source 任务引用）'));
  } else if (!isStr(body['source']) || body['source'] === '') {
    errors.push(err('FIELD_INVALID', '$.source', 'source 必须为非空字符串'));
  }

  const mode = body['mode'];
  if (!has(body, 'mode')) {
    errors.push(err('FIELD_INVALID', '$.mode', 'mode 为必填项（decompose 或 single_task）'));
  } else if (!isStr(mode) || !MODES.has(mode)) {
    errors.push(err('FIELD_INVALID', '$.mode', "mode 必须为 'decompose' 或 'single_task'"));
  }

  // 长度语义 = Unicode 码点（cpLen，对齐 py len()；非 UTF-16 码元）。
  if (!has(body, 'summary')) {
    errors.push(err('FIELD_INVALID', '$.summary', 'summary 为必填项'));
  } else if (!isStr(body['summary'])) {
    errors.push(err('FIELD_INVALID', '$.summary', 'summary 必须为字符串'));
  } else if (cpLen(body['summary']) > 200) {
    errors.push(err('FIELD_INVALID', '$.summary', 'summary 不得超过 200 字'));
  }

  const nodes = body['nodes'];
  const nodesIsList = Array.isArray(nodes);
  if (!has(body, 'nodes')) {
    errors.push(err('FIELD_INVALID', '$.nodes', 'nodes 为必填数组'));
  } else if (!nodesIsList) {
    errors.push(err('FIELD_INVALID', '$.nodes', 'nodes 必须为数组'));
  }

  const edges = body['edges'];
  const edgesIsList = Array.isArray(edges);
  if (has(body, 'edges') && !edgesIsList) {
    errors.push(err('FIELD_INVALID', '$.edges', 'edges 必须为数组'));
  }
  const edgesEff: unknown[] = edgesIsList ? edges : [];
  const nodeList: unknown[] = nodesIsList ? nodes : [];

  // -- V6 节点数（decompose 2..node_limit；single_task 恰 1 且 edges 必空）
  if (nodesIsList && isStr(mode) && MODES.has(mode)) {
    const n = nodeList.length;
    if (mode === 'single_task') {
      if (n !== 1) {
        errors.push(err('NODE_COUNT', '$.nodes', `single_task 模式须恰好 1 个节点，实际 ${n} 个`));
      }
      if (edgesEff.length > 0) {
        errors.push(err('NODE_COUNT', '$.edges', 'single_task 模式不得声明依赖边'));
      }
    } else {
      if (n < 2 || n > nodeLimit) {
        errors.push(err('NODE_COUNT', '$.nodes',
          `decompose 模式节点数须为 2..${nodeLimit}，实际 ${n} 个`));
      }
    }
  }

  // -- 逐节点：V3(node)/V4(node)/V10/V14/V11/V12；收集 temp_id/title 供 V7/V8
  const tempIdOrder: string[] = [];
  const tempIdSet = new Set<string>();
  const seenTemp = new Set<string>();
  const seenTitle = new Set<string>();
  let anyWritesCode = false;

  for (let i = 0; i < nodeList.length; i++) {
    const node = nodeList[i];
    const base = `$.nodes[${i}]`;
    if (!isDict(node)) {
      errors.push(err('FIELD_INVALID', base, '节点必须为对象'));
      continue;
    }

    // V3 node 未知字段
    for (const key of Object.keys(node)) {
      if (NODE_ALLOWED.has(key)) continue;
      const alias = NODE_ALIASES[key];
      errors.push(err('UNKNOWN_FIELD', `${base}.${key}`,
        `未知字段 '${key}'（提案 schema 不接受额外字段）`,
        alias ? `是否想用 '${alias}'？` : undefined));
    }

    // V4 temp_id
    const tid = node['temp_id'];
    if (!has(node, 'temp_id') || !isStr(tid)) {
      errors.push(err('FIELD_INVALID', `${base}.temp_id`, 'temp_id 为必填字符串'));
    } else if (!TEMP_ID_RE.test(tid)) {
      errors.push(err('FIELD_INVALID', `${base}.temp_id`, 'temp_id 须匹配 ^[A-Za-z0-9_-]{1,32}$'));
    }
    if (isStr(tid)) {
      if (!tempIdSet.has(tid)) {
        tempIdSet.add(tid);
        tempIdOrder.push(tid);
      }
      if (seenTemp.has(tid)) {
        errors.push(err('DUP_ID', `${base}.temp_id`, `temp_id '${tid}' 重复（提案内须唯一）`));
      } else {
        seenTemp.add(tid);
      }
    }

    // V4 title（长度 = Unicode 码点，同 summary 注记）
    const title = node['title'];
    if (!has(node, 'title') || !isStr(title)) {
      errors.push(err('FIELD_INVALID', `${base}.title`, 'title 为必填字符串'));
    } else if (cpLen(title) < 1 || cpLen(title) > 80) {
      errors.push(err('FIELD_INVALID', `${base}.title`, 'title 长度须为 1..80 字'));
    }
    if (isStr(title)) {
      if (seenTitle.has(title)) {
        errors.push(err('DUP_TITLE', `${base}.title`, `title '${title}' 重复（提案内须唯一）`));
      } else {
        seenTitle.add(title);
      }
    }

    // V4 kind（默认 agent）
    const kind = has(node, 'kind') ? node['kind'] : 'agent';
    const kindValid = isStr(kind) && KINDS.has(kind);
    if (has(node, 'kind') && !kindValid) {
      errors.push(err('FIELD_INVALID', `${base}.kind`, "kind 必须为 'agent' 或 'system'"));
    }
    const effKind = kindValid ? (kind as string) : null;

    // V4 writes_code（默认 false）
    const wc = has(node, 'writes_code') ? node['writes_code'] : false;
    if (has(node, 'writes_code') && !isBool(wc)) {
      errors.push(err('FIELD_INVALID', `${base}.writes_code`, 'writes_code 必须为布尔值'));
    }
    const writesCode = wc === true;
    if (writesCode) anyWritesCode = true;

    // V4 suggested_owner 类型（member_ref | null）
    const owner = node['suggested_owner'];
    const ownerPresent = has(node, 'suggested_owner') && owner !== null;
    if (ownerPresent && !isStr(owner)) {
      errors.push(err('FIELD_INVALID', `${base}.suggested_owner`,
        'suggested_owner 必须为成员引用字符串或 null'));
    }

    // V4 project 类型（project_ref | null）
    const project = node['project'];
    const projectPresent = has(node, 'project') && project !== null;
    if (projectPresent && !isStr(project)) {
      errors.push(err('FIELD_INVALID', `${base}.project`, 'project 必须为项目引用字符串或 null'));
    }

    // V4 command 类型（string | null；check 节点的非空必填由 V14 另判）——与 py 同步：
    // 不校验则非 str 值零错误直达 proposalFingerprint（float/数组内 null 违反 A §2 前置）
    const commandVal = node['command'];
    if (has(node, 'command') && commandVal !== null && !isStr(commandVal)) {
      errors.push(err('FIELD_INVALID', `${base}.command`, 'command 必须为字符串或 null'));
    }

    // -- kind 相关：V10（agent）/ V14（system + agent 禁 system_action）
    if (effKind === 'agent') {
      validateAgentPlan(node, base, errors);
      if (node['system_action'] !== null && node['system_action'] !== undefined) {
        errors.push(err('SYSTEM_NODE_INVALID', `${base}.system_action`,
          'agent 节点不得声明 system_action'));
      }
    } else if (effKind === 'system') {
      validateSystemNode(node, base, errors);
    }

    // -- V11 suggested_owner 成员校验（system 节点已由 V14 禁止，故仅 agent/未知 kind）
    if (ownerPresent && isStr(owner) && effKind !== 'system') {
      if (!memberIds.has(owner)) {
        errors.push(err('OWNER_NOT_MEMBER', `${base}.suggested_owner`,
          `suggested_owner '${owner}' 不是本频道成员`,
          '可置为 null 留待认领，或先邀请该成员入频道'));
      }
    }

    // -- V12 writes_code → project 必填且已绑定
    if (writesCode) {
      if (!projectPresent) {
        errors.push(err('PROJECT_REQUIRED', `${base}.project`,
          'writes_code=true 的节点必须指定 project'));
      } else if (isStr(project) && !boundProjectIds.has(project)) {
        errors.push(err('PROJECT_UNBOUND', `${base}.project`, `project '${project}' 未绑定本频道`,
          '先将该 Project 绑定到本频道，或改用已绑定的 Project'));
      }
    }
  }

  // -- V8 edges 引用存在 + 禁自环（逐边声明序）；V3 未知字段执法到 edge 层（无别名清单）
  if (edgesIsList) {
    for (let i = 0; i < edgesEff.length; i++) {
      const edge = edgesEff[i];
      const ebase = `$.edges[${i}]`;
      if (!isDict(edge)) {
        errors.push(err('FIELD_INVALID', ebase, '边必须为对象'));
        continue;
      }
      for (const key of Object.keys(edge)) {
        if (!EDGE_ALLOWED.has(key)) {
          errors.push(err('UNKNOWN_FIELD', `${ebase}.${key}`,
            `未知字段 '${key}'（提案 schema 不接受额外字段）`));
        }
      }
      const frm = edge['from'];
      const to = edge['to'];
      const frmOk = isStr(frm);
      const toOk = isStr(to);
      if (!has(edge, 'from') || !frmOk) {
        errors.push(err('FIELD_INVALID', `${ebase}.from`, 'from 为必填字符串'));
      }
      if (!has(edge, 'to') || !toOk) {
        errors.push(err('FIELD_INVALID', `${ebase}.to`, 'to 为必填字符串'));
      }
      if (frmOk && toOk && frm === to) {
        errors.push(err('EDGE_SELF', ebase, `禁止自环（from 与 to 同为 '${frm}'）`));
        continue;
      }
      if (frmOk && !tempIdSet.has(frm)) {
        errors.push(err('EDGE_UNKNOWN_NODE', `${ebase}.from`, `边引用了不存在的节点 '${frm}'`,
          unknownNodeHint(frm, tempIdOrder)));
      }
      if (toOk && !tempIdSet.has(to)) {
        errors.push(err('EDGE_UNKNOWN_NODE', `${ebase}.to`, `边引用了不存在的节点 '${to}'`,
          unknownNodeHint(to, tempIdOrder)));
      }
    }
  }

  // -- V9 全图无环（复用 lib/graph.detectCycle，勿重写）
  const cycleEdges: Array<[string, string]> = [];
  const cycleNodes = [...tempIdOrder];
  for (const e of edgesEff) {
    if (isDict(e) && isStr(e['from']) && isStr(e['to'])) {
      cycleEdges.push([e['from'], e['to']]);
      if (!tempIdSet.has(e['from'])) cycleNodes.push(e['from']);
      if (!tempIdSet.has(e['to'])) cycleNodes.push(e['to']);
    }
  }
  const cycle = detectCycle(cycleNodes, cycleEdges);
  if (cycle !== null) {
    errors.push(err('GRAPH_CYCLE', '$.edges', `提案图存在环：${cycle.join(' → ')}`));
  }

  // -- V13 存在 writes_code 节点时 merge_plan 必填
  const mergePlan = body['merge_plan'];
  if (has(body, 'merge_plan') && mergePlan !== null && !isStr(mergePlan)) {
    errors.push(err('FIELD_INVALID', '$.merge_plan', 'merge_plan 必须为字符串或 null'));
  }
  if (anyWritesCode && (mergePlan === null || mergePlan === undefined || mergePlan === '')) {
    errors.push(err('MERGE_PLAN_MISSING', '$.merge_plan', '存在 writes_code 节点时 merge_plan 必填'));
  }

  return errors;
}

// V10：agent 节点必有合法 task_plan（goal 非空、AC≥1、每条 verify_by 合法）。
// 校验严格度 ≥ 落地消费（TaskPlanBody extra="forbid"、AC 四字段全必填）——presence+类型即可，
// 空串不禁。检查顺序（与 py 逐字节同序）：未知字段 → version → goal → AC（逐条：未知字段 →
// id → statement → verify_by → verify_ref）→ defaults_decided → out_of_scope。
function validateAgentPlan(node: Dict, base: string, errors: DecompError[]): void {
  const plan = node['task_plan'];
  if (!has(node, 'task_plan') || !isDict(plan)) {
    errors.push(err('PLAN_MISSING', `${base}.task_plan`, 'agent 节点必须包含 task_plan'));
    return;
  }
  const ppath = `${base}.task_plan`;
  // V3 task_plan 层未知字段（allowed = TaskPlanBody 字段集；深层无别名清单）
  for (const key of Object.keys(plan)) {
    if (!PLAN_ALLOWED.has(key)) {
      errors.push(err('UNKNOWN_FIELD', `${ppath}.${key}`,
        `未知字段 '${key}'（提案 schema 不接受额外字段）`));
    }
  }
  // version 若出现必须为 task-plan v1 常量（TaskPlanBody 带默认 Literal：缺席合法、错值必炸）
  if (has(plan, 'version') && plan['version'] !== SCHEMA_TASK_PLAN_V1) {
    errors.push(err('FIELD_INVALID', `${ppath}.version`,
      `version 必须为 '${SCHEMA_TASK_PLAN_V1}'`));
  }
  const goal = plan['goal'];
  if (!isStr(goal) || goal === '') {
    errors.push(err('PLAN_MISSING', `${ppath}.goal`, 'task_plan.goal 不得为空'));
  }
  const acs = plan['acceptance_criteria'];
  if (!Array.isArray(acs) || acs.length === 0) {
    errors.push(err('AC_INVALID', `${ppath}.acceptance_criteria`,
      'task_plan 须至少包含 1 条验收标准'));
  } else {
    for (let j = 0; j < acs.length; j++) {
      const ac = acs[j];
      const apath = `${ppath}.acceptance_criteria[${j}]`;
      if (!isDict(ac)) {
        errors.push(err('AC_INVALID', apath, '验收标准必须为对象'));
        continue;
      }
      // V3 AC 层未知字段（allowed = AcceptanceCriterion 字段集）
      for (const key of Object.keys(ac)) {
        if (!AC_ALLOWED.has(key)) {
          errors.push(err('UNKNOWN_FIELD', `${apath}.${key}`,
            `未知字段 '${key}'（提案 schema 不接受额外字段）`));
        }
      }
      // AcceptanceCriterion 四字段全必填（presence + 类型）
      if (!has(ac, 'id') || !isStr(ac['id'])) {
        errors.push(err('AC_INVALID', `${apath}.id`, 'id 为必填字符串'));
      }
      if (!has(ac, 'statement') || !isStr(ac['statement'])) {
        errors.push(err('AC_INVALID', `${apath}.statement`, 'statement 为必填字符串'));
      }
      const vb = ac['verify_by'];
      if (!isStr(vb) || !VERIFY_BY.has(vb)) {
        errors.push(err('AC_INVALID', `${apath}.verify_by`,
          'verify_by 必须为 command / inspect / manual 之一'));
      }
      if (!has(ac, 'verify_ref') || !isStr(ac['verify_ref'])) {
        errors.push(err('AC_INVALID', `${apath}.verify_ref`, 'verify_ref 为必填字符串'));
      }
    }
  }
  // defaults_decided / out_of_scope 若出现必须为字符串数组（TaskPlanBody list[str]，null 也炸）
  for (const fld of ['defaults_decided', 'out_of_scope'] as const) {
    if (!has(plan, fld)) continue;
    const val = plan[fld];
    if (!Array.isArray(val)) {
      errors.push(err('FIELD_INVALID', `${ppath}.${fld}`, `${fld} 必须为字符串数组`));
    } else {
      for (let j = 0; j < val.length; j++) {
        if (!isStr(val[j])) {
          errors.push(err('FIELD_INVALID', `${ppath}.${fld}[${j}]`, `${fld} 的元素必须为字符串`));
        }
      }
    }
  }
}

// V14：system 节点 system_action 合法、check 必有 command、禁 task_plan/suggested_owner。
function validateSystemNode(node: Dict, base: string, errors: DecompError[]): void {
  const action = node['system_action'];
  if (!isStr(action) || !SYSTEM_ACTIONS.has(action)) {
    errors.push(err('SYSTEM_NODE_INVALID', `${base}.system_action`,
      "system 节点的 system_action 必须为 'merge' 或 'check'"));
  }
  if (action === 'check') {
    const command = node['command'];
    if (!isStr(command) || command === '') {
      errors.push(err('SYSTEM_NODE_INVALID', `${base}.command`,
        'system_action=check 的节点必须提供 command'));
    }
  }
  if (has(node, 'task_plan') && node['task_plan'] !== null) {
    errors.push(err('SYSTEM_NODE_INVALID', `${base}.task_plan`, 'system 节点不得包含 task_plan'));
  }
  if (has(node, 'suggested_owner') && node['suggested_owner'] !== null) {
    errors.push(err('SYSTEM_NODE_INVALID', `${base}.suggested_owner`,
      'system 节点不得指定 suggested_owner'));
  }
}

// ---------------------------------------------------------------- proposal_fingerprint

/**
 * 规范化提案指纹（拆解设计 §5.2）：剔系统注入字段 → nodes 按 temp_id、edges 按 (from,to) 排序 →
 * 复用 A §2 指纹（键排序 / null 剔除 / SHA-256）。故"同内容不同书写序"指纹相同。
 */
export function proposalFingerprint(body: Dict): string {
  const cleaned: Dict = {};
  for (const k of Object.keys(body)) {
    if (!SYSTEM_INJECTED.has(k)) cleaned[k] = body[k];
  }
  const nodes = cleaned['nodes'];
  if (Array.isArray(nodes)) {
    cleaned['nodes'] = [...nodes].sort((a, b) =>
      cmpCodepoint(
        isDict(a) && isStr(a['temp_id']) ? a['temp_id'] : '',
        isDict(b) && isStr(b['temp_id']) ? b['temp_id'] : '',
      ),
    );
  }
  const edges = cleaned['edges'];
  if (Array.isArray(edges)) {
    cleaned['edges'] = [...edges].sort((a, b) => {
      const af = isDict(a) && isStr(a['from']) ? a['from'] : '';
      const bf = isDict(b) && isStr(b['from']) ? b['from'] : '';
      if (af !== bf) return cmpCodepoint(af, bf);
      const at = isDict(a) && isStr(a['to']) ? a['to'] : '';
      const bt = isDict(b) && isStr(b['to']) ? b['to'] : '';
      return cmpCodepoint(at, bt);
    });
  }
  return fingerprint(cleaned as never);
}
