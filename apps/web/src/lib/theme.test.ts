// F4 主题解析：dark/light 直通；system 依 prefers-color-scheme（无 matchMedia 环境兜底 dark）。
import { afterEach, describe, expect, it, vi } from 'vitest';

import { applyTheme, resolveTheme } from './theme';

afterEach(() => { vi.unstubAllGlobals(); document.documentElement.removeAttribute('data-theme'); });

describe('resolveTheme', () => {
  it('dark/light 直通', () => {
    expect(resolveTheme('dark')).toBe('dark');
    expect(resolveTheme('light')).toBe('light');
  });

  it('system + prefers light → light', () => {
    vi.stubGlobal('matchMedia', (q: string) => ({ matches: q.includes('light') }));
    expect(resolveTheme('system')).toBe('light');
  });

  it('system + 无 light 偏好 → dark', () => {
    vi.stubGlobal('matchMedia', () => ({ matches: false }));
    expect(resolveTheme('system')).toBe('dark');
  });
});

describe('applyTheme', () => {
  it('落 data-theme 到根节点', () => {
    applyTheme('light');
    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
    applyTheme('dark');
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
  });
});
