// cal6 探针 a：长驻 python 回声子进程，1000 轮 JSON 行往返。
// 阶段1: 串行往返(逐轮测延迟)；阶段2: 1000 条一次性灌入(流水线)验保序/零丢。
// 载荷含中文+emoji，逐字段核对回声完整性。
import { spawn } from "node:child_process";
import readline from "node:readline";

const PY = "D:/Project4work/Agenthub_7_8/coagentia/.venv/Scripts/python.exe";
const SCRIPT = "D:/Project4work/Agenthub_7_8/coagentia/scratchpad/tscal/cal6_echo.py";
const N = 1000;
const PAYLOAD = "中文校验✓🚀-";

const child = spawn(PY, ["-X", "utf8", SCRIPT], { stdio: ["pipe", "pipe", "pipe"] });
const watchdog = setTimeout(() => {
  console.error("WATCHDOG: 60s 超时，杀子进程");
  try { child.kill(); } catch {}
  process.exit(2);
}, 60_000);

let stderrBuf = "";
child.stderr.setEncoding("utf8");
child.stderr.on("data", (d) => { stderrBuf += d; });

const rl = readline.createInterface({ input: child.stdout });
const queue = [];
let pendingResolve = null;
rl.on("line", (line) => {
  if (pendingResolve) { const r = pendingResolve; pendingResolve = null; r(line); }
  else queue.push(line);
});
function nextLine() {
  if (queue.length) return Promise.resolve(queue.shift());
  return new Promise((res) => { pendingResolve = res; });
}

// ---- 阶段1：串行 1000 轮往返 ----
const lat = [];
let seqErr = 0, textErr = 0;
for (let i = 0; i < N; i++) {
  const msg = { seq: i, text: PAYLOAD + i };
  const t0 = process.hrtime.bigint();
  child.stdin.write(JSON.stringify(msg) + "\n");
  const line = await nextLine();
  const t1 = process.hrtime.bigint();
  const obj = JSON.parse(line);
  if (obj.seq !== i) seqErr++;
  if (obj.text !== msg.text) textErr++;
  lat.push(Number(t1 - t0) / 1e6);
}
lat.sort((a, b) => a - b);
const avg = lat.reduce((s, x) => s + x, 0) / lat.length;
const pct = (p) => lat[Math.min(lat.length - 1, Math.floor(lat.length * p))];

// ---- 阶段2：1000 条流水线灌入 ----
const tB0 = Date.now();
for (let i = 0; i < N; i++) {
  child.stdin.write(JSON.stringify({ seq: i, text: PAYLOAD + i }) + "\n");
}
let burstSeqErr = 0, burstTextErr = 0, burstGot = 0;
for (let i = 0; i < N; i++) {
  const obj = JSON.parse(await nextLine());
  if (obj.seq !== i) burstSeqErr++;
  if (obj.text !== PAYLOAD + i) burstTextErr++;
  burstGot++;
}
const tB1 = Date.now();

child.stdin.end();
const exitInfo = await new Promise((res) => child.on("exit", (code, signal) => res({ code, signal })));
clearTimeout(watchdog);

console.log(JSON.stringify({
  probe: "cal6_a",
  serial: {
    rounds: N, seq_errors: seqErr, text_errors: textErr,
    latency_ms: {
      avg: +avg.toFixed(3), min: +lat[0].toFixed(3), p50: +pct(0.5).toFixed(3),
      p95: +pct(0.95).toFixed(3), p99: +pct(0.99).toFixed(3), max: +lat[lat.length - 1].toFixed(3),
    },
  },
  burst: { sent: N, got: burstGot, seq_errors: burstSeqErr, text_errors: burstTextErr, total_ms: tB1 - tB0 },
  child_exit: exitInfo,
  stderr_len: stderrBuf.length,
}, null, 2));
