// F4 主题应用：workspace.ui_theme → documentElement[data-theme]（afterglow-tokens.css 的选择器）。
// 事实源 = workspace 查询缓存（PATCH 落库 + WS workspace.updated 反流）；此处只负责把值落到 DOM。
// 'system' 依赖 prefers-color-scheme 解析为 dark/light（token 只定义 dark/light 两套）。
import type { UiTheme } from '@coagentia/contracts-ts';

/** 把契约主题值解析为 CSS token 主题（'system' 走 prefers-color-scheme，非浏览器环境兜底 dark）。 */
export function resolveTheme(theme: UiTheme): 'dark' | 'light' {
  if (theme === 'system') {
    if (typeof window !== 'undefined' && window.matchMedia) {
      return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
    }
    return 'dark';
  }
  return theme;
}

/** 落 data-theme 到根节点（无 document 的环境静默跳过——SSR/测试）。 */
export function applyTheme(theme: UiTheme): void {
  if (typeof document === 'undefined') return;
  document.documentElement.setAttribute('data-theme', resolveTheme(theme));
}
