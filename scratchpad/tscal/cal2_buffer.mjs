// cal2_buffer.mjs — 读法 B：手写 Buffer 累积 + 按 \n 字节切分（对照组）
// data 事件里 chunk.indexOf(0x0a) 找换行；跨 chunk 残段入 acc 数组，行完整时 Buffer.concat
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { performance } from 'node:perf_hooks';
import { makeFrame } from './cal2_gen.mjs';

const specs = process.argv.slice(2);
if (!specs.length) {
  console.error('usage: node cal2_buffer.mjs <spec...>');
  process.exit(2);
}
const dir = path.dirname(fileURLToPath(import.meta.url));

const expected = specs.map((spec) => {
  const buf = makeFrame(spec);
  return { spec, len: buf.length, sha: createHash('sha256').update(buf).digest('hex') };
});
if (globalThis.gc) globalThis.gc();

const peak = { rss: 0, heapUsed: 0, external: 0, arrayBuffers: 0 };
const sampler = setInterval(() => {
  const m = process.memoryUsage();
  for (const k of Object.keys(peak)) if (m[k] > peak[k]) peak[k] = m[k];
}, 5);
sampler.unref();

const fmt = (m) => {
  const o = {};
  for (const k of ['rss', 'heapUsed', 'external', 'arrayBuffers']) o[k] = +((m[k] || 0) / 1048576).toFixed(1);
  return o;
};
const baseline = process.memoryUsage();

const t0 = performance.now();
const child = spawn(process.execPath, [path.join(dir, 'cal2_child.mjs'), ...specs], {
  stdio: ['ignore', 'pipe', 'inherit'],
});

let i = 0;
let prevT = 0;
const results = [];
let chunkCount = 0;
let maxChunk = 0;

function onLine(lineBuf) {
  const tLine = performance.now() - t0;
  const exp = expected[i] ?? { spec: '<extra>', len: -1, sha: '' };
  i++;
  const tv = performance.now();
  const sha = createHash('sha256').update(lineBuf).digest('hex'); // 直接哈希，无拷贝
  const verifyMs = performance.now() - tv;
  let decodeMs = -1;
  let parseMs = -1;
  let parseOk = false;
  try {
    const td = performance.now();
    const str = lineBuf.toString('utf8'); // JSON.parse 需要 string：解码计一次
    decodeMs = performance.now() - td;
    const tp = performance.now();
    const obj = JSON.parse(str);
    parseMs = performance.now() - tp;
    parseOk = obj.type === 'frame' && obj.spec === exp.spec;
  } catch {
    /* parseOk=false */
  }
  results.push({
    spec: exp.spec,
    bytes: lineBuf.length,
    expBytes: exp.len,
    lenMatch: lineBuf.length === exp.len,
    shaMatch: sha === exp.sha,
    msSinceSpawn: +tLine.toFixed(1),
    msDelta: +(tLine - prevT).toFixed(1),
    verifyMs: +verifyMs.toFixed(1),
    utf8DecodeMs: +decodeMs.toFixed(1),
    jsonParseMs: +parseMs.toFixed(1),
    parseOk,
  });
  prevT = tLine;
}

let acc = []; // 跨 chunk 残段
child.stdout.on('data', (chunk) => {
  chunkCount++;
  if (chunk.length > maxChunk) maxChunk = chunk.length;
  let start = 0;
  for (;;) {
    const idx = chunk.indexOf(0x0a, start);
    if (idx === -1) {
      if (start < chunk.length) acc.push(chunk.subarray(start));
      return;
    }
    let line;
    if (acc.length) {
      acc.push(chunk.subarray(start, idx));
      line = Buffer.concat(acc);
      acc = [];
    } else {
      line = chunk.subarray(start, idx);
    }
    onLine(line);
    start = idx + 1;
  }
});

const stdoutEnd = new Promise((r) => child.stdout.once('end', r));
const exitCode = await new Promise((r) => child.once('close', r));
await stdoutEnd;
clearInterval(sampler);
const totalMs = performance.now() - t0;

console.log(
  JSON.stringify(
    {
      method: 'manual Buffer accumulate + \\n split',
      node: process.version,
      platform: process.platform,
      specs,
      childExitCode: exitCode,
      linesReceived: results.length,
      results,
      totalMs: +totalMs.toFixed(1),
      pipeChunks: { count: chunkCount, maxChunkBytes: maxChunk },
      trailingBytesWithoutNewline: acc.reduce((s, b) => s + b.length, 0),
      memMB: { baseline: fmt(baseline), peak: fmt(peak), final: fmt(process.memoryUsage()) },
      note: '校验 sha256 直接对行 Buffer；utf8DecodeMs 为 toString 供 JSON.parse 的解码耗时',
    },
    null,
    1,
  ),
);
process.exit(results.length === specs.length && results.every((r) => r.lenMatch && r.shaMatch) ? 0 : 1);
