// M5b 模板域纯函数(判定单点,组件与测试共用；纪律 7 前端不复制 server 判定，仅承载 UI 责任规则)。
// 覆盖:存为模板入口可用性、角色占位提取(前端预填，server 按 A §4.10 重序列化真值)、
// role_mapping 全覆盖校验(§11.2 #1)、跨 runtime 互审 warning(FR-7.3/§11.2 #6，UI 责任 server 零校验)。
import type {
  CanvasNodePublic,
  MemberPublic,
  TaskPublic,
  TemplateRole,
} from '@coagentia/contracts-ts';

// ---- 存为模板入口可用性(§11.1 #2):画布 ≥1 正式(task)节点、无草稿层，否则 disabled + tooltip。
export type SaveTemplateGate = { enabled: true } | { enabled: false; hint: string };

export function saveTemplateGate(formalNodeCount: number, hasDraft: boolean): SaveTemplateGate {
  if (hasDraft) return { enabled: false, hint: '先处理画布草稿层' };
  if (formalNodeCount < 1) return { enabled: false, hint: '先加正式节点' };
  return { enabled: true };
}

// 画布正式节点 = 含 task_id 的 agent 节点(序列化源，§11.1 #1；pos/system 节点不入模板)。
export function formalTaskNodes(nodes: CanvasNodePublic[]): CanvasNodePublic[] {
  return nodes.filter((n) => n.kind === 'agent' && !!n.task_id);
}

// ---- 角色占位提取表(§11.1 #1 A §4.10):按节点 owner 去重、无 owner 归"待认领"。
// 前端预填供改名；server 存为模板时会按快照重新序列化真值(此表仅驱动 role_placeholders 覆盖)。
export interface RolePlaceholderRow {
  ownerId: string | null; // null = 待认领(无 member_id，不能作为 role_placeholders 覆盖键)
  ownerName: string; // 展示名
  placeholder: string; // 可改占位名(默认 = owner 名 / "待认领")
}

export function extractRolePlaceholders(
  nodes: CanvasNodePublic[],
  taskById: Record<string, TaskPublic>,
  memberById: Record<string, MemberPublic>,
): RolePlaceholderRow[] {
  const rows: RolePlaceholderRow[] = [];
  const seen = new Set<string>();
  let hasUnowned = false;
  for (const n of formalTaskNodes(nodes)) {
    const task = n.task_id ? taskById[n.task_id] : undefined;
    const ownerId = task?.owner_member_id ?? null;
    if (!ownerId) {
      hasUnowned = true;
      continue;
    }
    if (seen.has(ownerId)) continue;
    seen.add(ownerId);
    const name = memberById[ownerId]?.name ?? ownerId;
    rows.push({ ownerId, ownerName: name, placeholder: name });
  }
  if (hasUnowned) rows.push({ ownerId: null, ownerName: '待认领', placeholder: '待认领' });
  return rows;
}

// role_placeholders 覆盖体(仅有 owner 的行；待认领无 member_id 键，交 server 默认)。空 → undefined 省字段。
export function buildRolePlaceholders(
  rows: RolePlaceholderRow[],
): Record<string, string> | undefined {
  const out: Record<string, string> = {};
  for (const r of rows) {
    if (r.ownerId && r.placeholder.trim()) out[r.ownerId] = r.placeholder.trim();
  }
  return Object.keys(out).length ? out : undefined;
}

// ---- 角色类别(FR-7.3 checker ≠ doer)：话术承载(builtin roles.description 标注)，关键字派生。
export type RoleKind = 'implement' | 'review' | 'other';

export function classifyRole(role: TemplateRole): RoleKind {
  // 仅按占位名判定：占位名是角色身份的权威信号。description 常提及下游步骤(如"实现工程师"的
  // description 含"交独立验收")，若并入判定会把 doer 误归 review——进而使 FR-7.3 同 runtime
  // 互审警示对 builtin 工程三角(其唯一用武之地)失效。用户模板占位=成员名(无关键字)→ 'other'，
  // 警示自然不触发（用户未声明 checker/doer 语义角色）。
  const p = role.placeholder.toLowerCase();
  if (/评审|验收|审查|复核|checker|review/.test(p)) return 'review';
  if (/实现|落地|开发|编码|doer|impl/.test(p)) return 'implement';
  return 'other';
}

// FR-7.3(§11.2 #6):评审角色与实现角色映射到「同 runtime」成员 → warning(不阻塞，UI 责任)。
export function hasSameRuntimeReview(
  roles: TemplateRole[],
  mapping: Record<string, string | null | undefined>,
  runtimeOf: (memberId: string) => string | undefined,
): boolean {
  const reviewRt = new Set<string>();
  const implRt = new Set<string>();
  for (const r of roles) {
    const mid = mapping[r.placeholder];
    if (!mid) continue; // null(待认领)/未选 → 无 runtime
    const rt = runtimeOf(mid);
    if (!rt) continue;
    const kind = classifyRole(r);
    if (kind === 'review') reviewRt.add(rt);
    else if (kind === 'implement') implRt.add(rt);
  }
  for (const rt of reviewRt) if (implRt.has(rt)) return true;
  return false;
}

// role_mapping 全覆盖校验(§11.2 #1):未做出选择的占位(mapping 无该键) = 未覆盖 → 阻塞实例化，
// 避免 server 422 VALIDATION_FAILED(details.missing)。null=待认领算已覆盖。
export function missingRoleMappings(
  roles: TemplateRole[],
  mapping: Record<string, string | null | undefined>,
): string[] {
  return roles
    .filter((r) => !(r.placeholder in mapping))
    .map((r) => r.placeholder);
}
