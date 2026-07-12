// 设计稿视觉映射常量(非契约数据):头像色号/状态词/状态色变量/presence 色变量。
// 全部取自设计稿 P1,值只引用 afterglow token 变量名(零发明)。
export const AVATARS: Record<string, { v: number; human?: boolean }> = {
  Memcyo: { v: 3, human: true },
  Pat: { v: 1 },
  Hank: { v: 7 },
  Rin: { v: 5 },
  Orchestrator: { v: 4 },
};

export const STATUS_WORD: Record<string, string> = {
  todo: 'Todo', in_progress: 'In Progress', in_review: 'In Review',
  done: 'Done', closed: 'Closed',
};

export const STATUS_VAR: Record<string, string> = {
  todo: '--st-todo', in_progress: '--st-progress', in_review: '--st-review',
  done: '--st-done', closed: '--st-closed',
};

export const PRESENCE_VAR: Record<string, string> = {
  online: '--success', idle: '--success', busy: '--warning',
  error: '--danger', offline: '--border-strong',
};

// ---- M6b 拆解提案生命周期态（ProposalStatus 全态：契约 models.ts ProposalStatus 9 值 = 拆解设计
// §3 状态机全态）。徽标文案 + 色变量（零发明，只引用 afterglow token）；uiMaps 为展示单源，提案卡
// 与草稿层（后半）共用，防漂移。进行中态用 accent/warning，终态成功 done、失败/拒绝 danger，
// superseded 归静默 muted。
export const PROPOSAL_STATUS_WORD: Record<string, string> = {
  drafting: '起草中', validating: '校验中', repairing: '修复中',
  awaiting_confirm: '待确认', landing: '落地中', landed: '已落地',
  superseded: '已被取代', rejected: '已拒绝', failed: '已失败',
};

export const PROPOSAL_STATUS_VAR: Record<string, string> = {
  drafting: '--text-muted', validating: '--accent', repairing: '--warning',
  awaiting_confirm: '--accent', landing: '--accent', landed: '--success',
  superseded: '--text-muted', rejected: '--danger', failed: '--danger',
};

export function avatarCfg(name: string): { v: number; human?: boolean } {
  return AVATARS[name] ?? { v: (name.charCodeAt(0) % 8) + 1 };
}

// 像素 logo「A」的 4×4 位图(复发点 1:·██· / █··█ / ████ / █··█)。
export const LOGO_A_BITS: readonly number[] = [
  0, 1, 1, 0,
  1, 0, 0, 1,
  1, 1, 1, 1,
  1, 0, 0, 1,
];
