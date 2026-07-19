/**
 * daemon 侧 ULID / 时间戳生成（与 server ledger.new_ulid / now_iso 同算法，两端字典序一致）。
 *
 * 契约 A §1：26 字符 Crockford Base32 大写 ULID（48-bit 毫秒 + 80-bit 随机；天然排除 I/L/O/U）；
 * 时间戳 ISO-8601 UTC 毫秒 Z。与 py 侧 util.py 同构，golden 判例逐字节守门（tests/util.test.ts）。
 */

import { randomBytes } from 'node:crypto';

const CROCKFORD = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'; // Crockford Base32（排除 I/L/O/U）

export function encodeUlid(timestampMs: number, random10: Uint8Array): string {
  let rand = 0n;
  for (const b of random10) rand = (rand << 8n) | BigInt(b);
  let value = (BigInt(timestampMs) << 80n) | rand;
  const chars: string[] = [];
  for (let i = 0; i < 26; i += 1) {
    chars.push(CROCKFORD[Number(value & 0x1fn)]!);
    value >>= 5n;
  }
  return chars.reverse().join('');
}

export function newUlid(): string {
  return encodeUlid(Date.now(), randomBytes(10));
}

export function nowIso(): string {
  // Date.toISOString() 恒为 UTC 毫秒 Z（YYYY-MM-DDTHH:mm:ss.sssZ），与 py strftime 格式一致
  return new Date().toISOString();
}
