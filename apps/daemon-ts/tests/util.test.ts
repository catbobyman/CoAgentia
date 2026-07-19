/** ULID/时间戳同构守门（任务书裁决 #8）：golden = py 权威实现注入固定输入生成（scratchpad/gen_util_golden.py）。 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import { encodeUlid, newUlid, nowIso } from '../src/util.ts';

interface Golden {
  ulid: Array<{ timestamp_ms: number; random_hex: string; expected: string }>;
  now_iso: Array<{ epoch_ms: number; expected: string }>;
}

const golden = JSON.parse(
  readFileSync(join(import.meta.dirname, 'fixtures', 'util_golden.json'), 'utf-8'),
) as Golden;

describe('encodeUlid golden（py 权威逐字节）', () => {
  for (const c of golden.ulid) {
    it(`ts=${c.timestamp_ms} rand=${c.random_hex}`, () => {
      const rand = Uint8Array.from(Buffer.from(c.random_hex, 'hex'));
      expect(encodeUlid(c.timestamp_ms, rand)).toBe(c.expected);
    });
  }
});

describe('nowIso 格式（py strftime 同构）', () => {
  for (const c of golden.now_iso) {
    it(`epoch=${c.epoch_ms}`, () => {
      expect(new Date(c.epoch_ms).toISOString()).toBe(c.expected);
    });
  }

  it('实时值符合 ISO-8601 UTC 毫秒 Z', () => {
    expect(nowIso()).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/);
  });
});

describe('newUlid', () => {
  it('26 字符 Crockford 大写、时间前缀字典序单调', () => {
    const a = newUlid();
    expect(a).toMatch(/^[0-9A-HJKMNP-TV-Z]{26}$/);
    const b = newUlid();
    // 同毫秒或后毫秒生成：时间前缀（10 字符）非降
    expect(b.slice(0, 10) >= a.slice(0, 10)).toBe(true);
  });
});
