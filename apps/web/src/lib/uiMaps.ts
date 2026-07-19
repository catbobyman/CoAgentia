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
