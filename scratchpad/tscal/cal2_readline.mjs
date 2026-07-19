// cal2_readline.mjs — 读法 A：readline.createInterface 逐行读子进程 stdout 的大帧
// 校验：长度 + sha256（期望值在 spawn 前预计算并释放大 Buffer）；计时 + 内存峰值采样
import { spawn } from 'node:child_process';
import readline from 'node:readline';
import { createHash } from 'node:crypto';
import { constants as bufConstants } from 'node:buffer';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { performance } from 'node:perf_hooks';
import { makeFrame } from './cal2_gen.mjs';

const specs = process.argv.slice(2);
if (!specs.length) {
  console.error('usage: node cal2_readline.mjs <spec...>   e.g. 1 8 32 / u1 / 64');
  process.exit(2);
}
const dir = path.dirname(fileURLToPath(import.meta.url));

// 1) 预计算期望值（长度 + sha256），随后释放大 Buffer
const expected = specs.map((spec) => {
  const buf = makeFrame(spec);
  return { spec, len: buf.length, sha: createHash('sha256').update(buf).digest('hex') };
});
if (globalThis.gc) globalThis.gc();

// 2) 内存峰值采样（5ms）
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

// 3) spawn + readline 逐行读
const t0 = performance.now();
const child = spawn(process.execPath, [path.join(dir, 'cal2_child.mjs'), ...specs], {
  stdio: ['ignore', 'pipe', 'inherit'],
});
const rl = readline.createInterface({ input: child.stdout, crlfDelay: Infinity });
const rlClosed = new Promise((r) => rl.once('close', r));

let i = 0;
let prevT = 0;
const results = [];
rl.on('line', (line) => {
  const tLine = performance.now() - t0; // 收到完整行的时刻（校验开销不计入）
  const exp = expected[i] ?? { spec: '<extra>', len: -1, sha: '' };
  i++;
  const tv = performance.now();
  const buf = Buffer.from(line, 'utf8'); // 校验用重编码拷贝（内存峰值含此开销，报告注明）
  const sha = createHash('sha256').update(buf).digest('hex');
  const verifyMs = performance.now() - tv;
  let parseMs = -1;
  let parseOk = false;
  try {
    const tp = performance.now();
    const obj = JSON.parse(line);
    parseMs = performance.now() - tp;
    parseOk = obj.type === 'frame' && obj.spec === exp.spec;
  } catch {
    /* parseOk=false */
  }
  results.push({
    spec: exp.spec,
    bytes: buf.length,
    expBytes: exp.len,
    lenMatch: buf.length === exp.len,
    shaMatch: sha === exp.sha,
    msSinceSpawn: +tLine.toFixed(1),
    msDelta: +(tLine - prevT).toFixed(1),
    verifyMs: +verifyMs.toFixed(1),
    jsonParseMs: +parseMs.toFixed(1),
    parseOk,
  });
  prevT = tLine;
});

const exitCode = await new Promise((r) => child.once('close', r));
await rlClosed;
clearInterval(sampler);
const totalMs = performance.now() - t0;

console.log(
  JSON.stringify(
    {
      method: 'readline.createInterface',
      node: process.version,
      platform: process.platform,
      specs,
      childExitCode: exitCode,
      linesReceived: results.length,
      results,
      totalMs: +totalMs.toFixed(1),
      v8MaxStringLength: bufConstants.MAX_STRING_LENGTH,
      memMB: { baseline: fmt(baseline), peak: fmt(peak), final: fmt(process.memoryUsage()) },
      note: 'peak 内存含校验用 Buffer.from(line) 一次 32MB 级拷贝与 sha256',
    },
    null,
    1,
  ),
);
process.exit(results.length === specs.length && results.every((r) => r.lenMatch && r.shaMatch) ? 0 : 1);
