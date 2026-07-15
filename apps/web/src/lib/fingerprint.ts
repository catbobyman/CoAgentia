// 规范化序列化与指纹的 TS 镜像（契约 A §2 / 内核 packages/contracts kernel/fingerprint.py）。
// 与 server 同一规范：JSON 子集值域、null 剔除、键按 Unicode 码点升序、无空白、非 ASCII 不转义、
// SHA-256 → 64 位小写 hex。纯函数、零依赖、同构（浏览器 + Node/vitest 皆可，故内置 SHA-256）。
// 双跑验收标准 = packages/fixtures/golden/（fingerprint.json 与 decomposition.json 的 proposal_fingerprint 例）。

type JsonSubset =
  | { [k: string]: JsonSubset }
  | JsonSubset[]
  | string
  | number
  | boolean
  | null;

// 码点升序比较（对齐 Python 默认字符串比较 = 按 Unicode 码点）——处理代理对（astral）。
export function cmpCodepoint(a: string, b: string): number {
  const ca = Array.from(a);
  const cb = Array.from(b);
  const n = Math.min(ca.length, cb.length);
  for (let i = 0; i < n; i++) {
    const pa = ca[i]!.codePointAt(0)!;
    const pb = cb[i]!.codePointAt(0)!;
    if (pa !== pb) return pa - pb;
  }
  return ca.length - cb.length;
}

// 递归序列化：键按码点升序、null 剔除（对象内）、数组内禁 null、禁 float（非整数）；
// 叶子字符串/键用 JSON.stringify（转义集与 Python json 一致：" \ 与控制字符，非 ASCII 原样）。
function serialize(value: JsonSubset): string {
  if (value === null) {
    // 顶层/对象值的 null 已在对象层剔除；数组内 null 在数组分支抛错。此处兜底抛错。
    throw new Error('null is forbidden here (contract A section 2.2)');
  }
  const t = typeof value;
  if (t === 'boolean') return value ? 'true' : 'false';
  if (t === 'number') {
    if (!Number.isInteger(value)) {
      throw new Error('float is forbidden in fingerprinted content (contract A section 2.1)');
    }
    return String(value);
  }
  if (t === 'string') return JSON.stringify(value);
  if (Array.isArray(value)) {
    return `[${value
      .map((v) => {
        if (v === null) throw new Error('null is forbidden inside arrays (contract A section 2.2)');
        return serialize(v);
      })
      .join(',')}]`;
  }
  if (t === 'object') {
    const obj = value as { [k: string]: JsonSubset };
    const keys = Object.keys(obj)
      .filter((k) => obj[k] !== null) // null 剔除（缺席 ≡ null）
      .sort(cmpCodepoint);
    return `{${keys.map((k) => `${JSON.stringify(k)}:${serialize(obj[k]!)}`).join(',')}}`;
  }
  throw new Error(`unsupported type in fingerprinted content: ${t}`);
}

/** 规范化序列化：键按码点升序、无空白、非 ASCII 原样（等价 Python json.dumps(sort_keys,ensure_ascii=False)）。 */
export function canonicalize(value: JsonSubset): string {
  return serialize(value);
}

// UTF-8 编码（同构：TextEncoder 在浏览器与 Node 均可用）。
function utf8Bytes(str: string): Uint8Array {
  return new TextEncoder().encode(str);
}

const _K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

// 纯 JS SHA-256（over UTF-8 bytes → 64 位小写 hex）——golden 的 sha256 值（Python hashlib 产）双跑对照。
function sha256Hex(bytes: Uint8Array): string {
  let h0 = 0x6a09e667;
  let h1 = 0xbb67ae85;
  let h2 = 0x3c6ef372;
  let h3 = 0xa54ff53a;
  let h4 = 0x510e527f;
  let h5 = 0x9b05688c;
  let h6 = 0x1f83d9ab;
  let h7 = 0x5be0cd19;

  const l = bytes.length;
  const bitLen = l * 8;
  const withOne = l + 1;
  const pad = ((56 - (withOne % 64)) + 64) % 64;
  const total = withOne + pad + 8;
  const m = new Uint8Array(total);
  m.set(bytes);
  m[l] = 0x80;
  const hi = Math.floor(bitLen / 0x100000000);
  const lo = bitLen >>> 0;
  m[total - 8] = (hi >>> 24) & 0xff;
  m[total - 7] = (hi >>> 16) & 0xff;
  m[total - 6] = (hi >>> 8) & 0xff;
  m[total - 5] = hi & 0xff;
  m[total - 4] = (lo >>> 24) & 0xff;
  m[total - 3] = (lo >>> 16) & 0xff;
  m[total - 2] = (lo >>> 8) & 0xff;
  m[total - 1] = lo & 0xff;

  const w = new Uint32Array(64);
  const rotr = (x: number, n: number): number => (x >>> n) | (x << (32 - n));

  for (let off = 0; off < total; off += 64) {
    for (let i = 0; i < 16; i++) {
      const j = off + i * 4;
      w[i] = ((m[j]! << 24) | (m[j + 1]! << 16) | (m[j + 2]! << 8) | m[j + 3]!) >>> 0;
    }
    for (let i = 16; i < 64; i++) {
      const x15 = w[i - 15]!;
      const x2 = w[i - 2]!;
      const s0 = rotr(x15, 7) ^ rotr(x15, 18) ^ (x15 >>> 3);
      const s1 = rotr(x2, 17) ^ rotr(x2, 19) ^ (x2 >>> 10);
      w[i] = (w[i - 16]! + s0 + w[i - 7]! + s1) >>> 0;
    }
    let a = h0;
    let b = h1;
    let c = h2;
    let d = h3;
    let e = h4;
    let f = h5;
    let g = h6;
    let h = h7;
    for (let i = 0; i < 64; i++) {
      const s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      const ch = (e & f) ^ (~e & g);
      const t1 = (h + s1 + ch + _K[i]! + w[i]!) >>> 0;
      const s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const t2 = (s0 + maj) >>> 0;
      h = g;
      g = f;
      f = e;
      e = (d + t1) >>> 0;
      d = c;
      c = b;
      b = a;
      a = (t1 + t2) >>> 0;
    }
    h0 = (h0 + a) >>> 0;
    h1 = (h1 + b) >>> 0;
    h2 = (h2 + c) >>> 0;
    h3 = (h3 + d) >>> 0;
    h4 = (h4 + e) >>> 0;
    h5 = (h5 + f) >>> 0;
    h6 = (h6 + g) >>> 0;
    h7 = (h7 + h) >>> 0;
  }
  const hex = (x: number): string => (x >>> 0).toString(16).padStart(8, '0');
  return [h0, h1, h2, h3, h4, h5, h6, h7].map(hex).join('');
}

/** SHA-256(canonicalize(value)) → 64 位小写十六进制（契约 A §2）。 */
export function fingerprint(value: JsonSubset): string {
  return sha256Hex(utf8Bytes(canonicalize(value)));
}

/** UI 展示短码 = 前 6 位（契约 A §2 / ids.SHORT_HASH_LEN）。 */
export function shortHash(fullHash: string): string {
  return fullHash.slice(0, 6);
}
