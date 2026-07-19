// cal1_pipe_encoding.mjs — win32 node 子进程管道编码校准探针
// 探针 a) node spawn python stdout（不带/带 PYTHONIOENCODING）字节级对比
//      b) node spawn git log 中文提交信息 UTF-8 完好性
//      c) node 写 stdin 中文给 python 反向验证
//      d)（附加）多字节 UTF-8 被 chunk 边界拆分时逐 chunk 解码 vs 整段解码
// 只用 node 内置模块。python 助手脚本由本文件运行时写入同目录。
import { spawn, spawnSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const DIR = path.dirname(fileURLToPath(import.meta.url));
const COAGENTIA = 'D:/Project4work/Agenthub_7_8/coagentia';
const PAYLOAD_FULL = '中文测试：你好，世界！🎉🚀 mixed-ASCII';
const PAYLOAD_CN = '中文测试：你好，世界！';
const STDIN_PAYLOAD = '回环测试：中文→节点🚀🎉 loopback';

// ---------- python 助手脚本（运行时写入，UTF-8） ----------
const HELPER_STDOUT = path.join(DIR, 'cal1_helper_stdout.py');
writeFileSync(HELPER_STDOUT, `import os, sys
print("PYVER=" + sys.version.split()[0])
print("ENC=" + str(sys.stdout.encoding))
print("ENV_PIO=" + repr(os.environ.get("PYTHONIOENCODING")))
print("ENV_PU8=" + repr(os.environ.get("PYTHONUTF8")))
sys.stdout.flush()
mode = sys.argv[1]
if mode == "cn":
    print("PAYLOAD=" + ${JSON.stringify(PAYLOAD_CN)})
elif mode == "full":
    print("PAYLOAD=" + ${JSON.stringify(PAYLOAD_FULL)})
elif mode == "split":
    import time
    b = "\\U0001F680".encode("utf-8")  # 🚀 4 字节
    sys.stdout.buffer.write(b[:2]); sys.stdout.buffer.flush()
    time.sleep(0.4)
    sys.stdout.buffer.write(b[2:] + b"\\n"); sys.stdout.buffer.flush()
sys.stdout.flush()
`, 'utf8');

const HELPER_STDIN = path.join(DIR, 'cal1_helper_stdin.py');
writeFileSync(HELPER_STDIN, `import sys
try:
    data = sys.stdin.read()
    print("STDIN_ENC=" + str(sys.stdin.encoding))
    print("HEX=" + data.encode("utf-8").hex())
except Exception as e:
    print("STDIN_ENC=" + str(getattr(sys.stdin, "encoding", None)))
    print("ERROR=" + (type(e).__name__ + ": " + str(e)).encode("ascii", "backslashreplace").decode("ascii"))
`, 'utf8');

// ---------- 工具函数 ----------
function bareEnv() {
  const e = { ...process.env };
  delete e.PYTHONIOENCODING;
  delete e.PYTHONUTF8;
  delete e.PYTHONLEGACYWINDOWSSTDIO;
  return e;
}

function run(cmd, args, { env, cwd, input, timeoutMs = 90000 } = {}) {
  return new Promise((resolve) => {
    const t0 = Date.now();
    const child = spawn(cmd, args, { cwd, env, windowsHide: true });
    const out = [];
    const err = [];
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      try { spawnSync('taskkill', ['/F', '/T', '/PID', String(child.pid)]); } catch {}
    }, timeoutMs);
    child.stdout.on('data', (d) => out.push(d));
    child.stderr.on('data', (d) => err.push(d));
    child.on('error', (e) => { clearTimeout(timer); resolve({ spawnError: String(e), ms: Date.now() - t0 }); });
    child.on('close', (code) => {
      clearTimeout(timer);
      resolve({ code, timedOut, chunks: out.map((b) => Buffer.from(b)), stdout: Buffer.concat(out), stderr: Buffer.concat(err), ms: Date.now() - t0 });
    });
    child.stdin.on('error', () => {});
    if (input !== undefined) child.stdin.write(input);
    child.stdin.end();
  });
}

const dec = (b) => b.toString('utf8');
function line(buf, prefix) {
  for (const l of dec(buf).split(/\r?\n/)) if (l.startsWith(prefix)) return l.slice(prefix.length);
  return null;
}
// 从原始字节里取 marker 之后到行尾的字节（marker 是 ASCII，与子进程编码无关，可直接字节定位）
function bytesAfter(buf, marker) {
  const m = Buffer.from(marker, 'ascii');
  const i = buf.indexOf(m);
  if (i < 0) return null;
  let k = i + m.length;
  const j = k;
  while (k < buf.length && buf[k] !== 0x0a && buf[k] !== 0x0d) k++;
  return buf.slice(j, k);
}
const countRepl = (s) => (s.match(/�/g) || []).length;

// ---------- 主流程 ----------
const report = {
  node: process.version,
  parentEnv: {
    PYTHONIOENCODING: process.env.PYTHONIOENCODING ?? null,
    PYTHONUTF8: process.env.PYTHONUTF8 ?? null,
    LANG: process.env.LANG ?? null,
    LC_ALL: process.env.LC_ALL ?? null,
  },
};

function stdoutCase(r, expectedStr) {
  const expected = Buffer.from(expectedStr, 'utf8');
  const actual = r.stdout ? bytesAfter(r.stdout, 'PAYLOAD=') : null;
  const errLine = r.stderr ? (dec(r.stderr).match(/\w*Error[^\r\n]*/) || [null])[0] : null;
  return {
    ms: r.ms,
    exitCode: r.code ?? null,
    timedOut: r.timedOut ?? false,
    spawnError: r.spawnError ?? null,
    pyver: r.stdout ? line(r.stdout, 'PYVER=') : null,
    selfReportedStdoutEncoding: r.stdout ? line(r.stdout, 'ENC=') : null,
    childSeen_PYTHONIOENCODING: r.stdout ? line(r.stdout, 'ENV_PIO=') : null,
    childSeen_PYTHONUTF8: r.stdout ? line(r.stdout, 'ENV_PU8=') : null,
    expectedUtf8Hex: expected.toString('hex'),
    actualPayloadHex: actual ? actual.toString('hex') : null,
    byteExact: actual ? actual.equals(expected) : false,
    payloadDecodedByNodeUtf8: actual ? dec(actual) : null,
    replacementCharCount: actual ? countRepl(dec(actual)) : null,
    stderrErrorLine: errLine,
  };
}

const results = {};

// --- 探针 a：python stdout ---
// A1: 不带 PYTHONIOENCODING，中文+emoji 全量 payload
results.A1_bare_full = stdoutCase(
  await run('uv', ['run', 'python', HELPER_STDOUT, 'full'], { cwd: COAGENTIA, env: bareEnv() }),
  PAYLOAD_FULL,
);
// A1b: 不带 PYTHONIOENCODING，仅中文（GBK 可编码 → 观察 mojibake 而非崩溃）
results.A1b_bare_cnOnly = stdoutCase(
  await run('uv', ['run', 'python', HELPER_STDOUT, 'cn'], { cwd: COAGENTIA, env: bareEnv() }),
  PAYLOAD_CN,
);
// A2: PYTHONIOENCODING=utf-8
results.A2_pio_utf8_full = stdoutCase(
  await run('uv', ['run', 'python', HELPER_STDOUT, 'full'], { cwd: COAGENTIA, env: { ...bareEnv(), PYTHONIOENCODING: 'utf-8' } }),
  PAYLOAD_FULL,
);
// A3（附加）: PYTHONUTF8=1
results.A3_pyutf8_full = stdoutCase(
  await run('uv', ['run', 'python', HELPER_STDOUT, 'full'], { cwd: COAGENTIA, env: { ...bareEnv(), PYTHONUTF8: '1' } }),
  PAYLOAD_FULL,
);

// --- 探针 b：git log 中文提交信息 ---
{
  const r = await run('git', ['log', '--oneline', '-3'], { cwd: COAGENTIA, env: bareEnv() });
  let fatalUtf8Ok = true;
  let decoded = '';
  try {
    decoded = new TextDecoder('utf-8', { fatal: true }).decode(r.stdout);
  } catch (e) {
    fatalUtf8Ok = false;
    decoded = dec(r.stdout);
  }
  results.B_git_log = {
    ms: r.ms,
    exitCode: r.code ?? null,
    spawnError: r.spawnError ?? null,
    stdoutBytes: r.stdout ? r.stdout.length : 0,
    strictUtf8DecodeOk: fatalUtf8Ok,
    replacementCharCount: countRepl(decoded),
    containsCJK: /[一-鿿]/.test(decoded),
    lines: decoded.split(/\r?\n/).filter(Boolean),
    firstLineHex: r.stdout ? r.stdout.slice(0, Math.min(r.stdout.indexOf(0x0a) < 0 ? r.stdout.length : r.stdout.indexOf(0x0a), 120)).toString('hex') : null,
    stderr: r.stderr ? dec(r.stderr).slice(0, 300) : null,
  };
}

// --- 探针 c：node 写 stdin 中文给 python ---
function stdinCase(r) {
  const sentHex = Buffer.from(STDIN_PAYLOAD, 'utf8').toString('hex');
  const gotHex = r.stdout ? line(r.stdout, 'HEX=') : null;
  return {
    ms: r.ms,
    exitCode: r.code ?? null,
    spawnError: r.spawnError ?? null,
    selfReportedStdinEncoding: r.stdout ? line(r.stdout, 'STDIN_ENC=') : null,
    pyDecodeError: r.stdout ? line(r.stdout, 'ERROR=') : null,
    sentUtf8Hex: sentHex,
    receivedHexAsSeenByPython: gotHex,
    roundTripByteExact: gotHex === sentHex,
    stderr: r.stderr && r.stderr.length ? dec(r.stderr).slice(0, 300) : null,
  };
}
results.C1_stdin_bare = stdinCase(
  await run('uv', ['run', 'python', HELPER_STDIN], { cwd: COAGENTIA, env: bareEnv(), input: Buffer.from(STDIN_PAYLOAD, 'utf8') }),
);
results.C2_stdin_pio_utf8 = stdinCase(
  await run('uv', ['run', 'python', HELPER_STDIN], { cwd: COAGENTIA, env: { ...bareEnv(), PYTHONIOENCODING: 'utf-8' }, input: Buffer.from(STDIN_PAYLOAD, 'utf8') }),
);

// --- 探针 d（附加）：chunk 边界拆多字节 ---
{
  const r = await run('uv', ['run', 'python', HELPER_STDOUT, 'split'], { cwd: COAGENTIA, env: { ...bareEnv(), PYTHONIOENCODING: 'utf-8' } });
  const naive = (r.chunks || []).map((c) => c.toString('utf8')).join('');
  const proper = r.stdout ? dec(r.stdout) : '';
  results.D_chunk_split = {
    ms: r.ms,
    exitCode: r.code ?? null,
    chunkCount: r.chunks ? r.chunks.length : 0,
    chunkSizes: r.chunks ? r.chunks.map((c) => c.length) : [],
    naivePerChunkDecodeHasRocket: naive.includes('\u{1F680}'),
    naiveReplacementCount: countRepl(naive),
    properConcatDecodeHasRocket: proper.includes('\u{1F680}'),
    properReplacementCount: countRepl(proper),
  };
}

report.results = results;
console.log(JSON.stringify(report, null, 2));
