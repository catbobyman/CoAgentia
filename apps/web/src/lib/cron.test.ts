// cron cadence 解析/人读预览(M5 B §11.5)。运行:pnpm -F @coagentia/web test
import { describe, expect, it } from 'vitest';

import { cronPreview, isCronCadence } from './cron';

describe('isCronCadence', () => {
  it('五段式纯 cron 字符 → true', () => {
    expect(isCronCadence('0 9 * * *')).toBe(true);
    expect(isCronCadence('30 8 1 * *')).toBe(true);
    expect(isCronCadence('*/5 * * * *')).toBe(true);
    expect(isCronCadence('0 9 * * 1-5')).toBe(true);
  });

  it('非五段 / 含字母的 interval 写法 → false', () => {
    expect(isCronCadence('daily 09:00')).toBe(false);
    expect(isCronCadence('every 30m')).toBe(false);
    expect(isCronCadence('0 9 * *')).toBe(false); // 四段
    expect(isCronCadence('0 9 * * * *')).toBe(false); // 六段
    expect(isCronCadence('')).toBe(false);
  });
});

describe('cronPreview', () => {
  it('每天定点', () => {
    expect(cronPreview('0 9 * * *')).toBe('每天 09:00');
    expect(cronPreview('30 18 * * *')).toBe('每天 18:30');
  });

  it('每周 X 定点(0/7 皆周日)', () => {
    expect(cronPreview('0 9 * * 5')).toBe('每周五 09:00');
    expect(cronPreview('0 9 * * 0')).toBe('每周日 09:00');
    expect(cronPreview('0 9 * * 7')).toBe('每周日 09:00');
  });

  it('周范围 / 周列表', () => {
    expect(cronPreview('0 9 * * 1-5')).toBe('每周一至周五 09:00');
    expect(cronPreview('0 9 * * 1,3,5')).toBe('每周一、周三、周五 09:00');
  });

  it('每月 N 号定点', () => {
    expect(cronPreview('30 8 1 * *')).toBe('每月 1 号 08:30');
  });

  it('步进(每 N 分钟 / 每 N 小时)', () => {
    expect(cronPreview('*/5 * * * *')).toBe('每 5 分钟');
    expect(cronPreview('0 */2 * * *')).toBe('每 2 小时');
  });

  it('非 cron / 无法识别 → undefined(调用方仅显示原串)', () => {
    expect(cronPreview('daily 09:00')).toBeUndefined();
    expect(cronPreview('*/10 9 * * *')).toBeUndefined(); // 分步进但时段非 * → 不定点
    expect(cronPreview('0 9 * 2 *')).toBe('每天 09:00'); // 月限定不改口径(时刻仍可读)
  });

  it('非法时刻(时>23/分>59) → undefined', () => {
    expect(cronPreview('70 9 * * *')).toBeUndefined();
    expect(cronPreview('0 26 * * *')).toBeUndefined();
  });
});
