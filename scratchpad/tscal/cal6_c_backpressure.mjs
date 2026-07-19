// cal6 探针 c：stdin.write 背压。向慢读 python 汇写 64MB。
// 变体1 firehose: 同步循环写 1024×64KB，无视 write()==false —— 验证后续写是否丢(sha256 对账)+内存峰值。
// 变体2 respect:  write()==false 时 await drain —— 验证 drain 行为+内存峰值对照。
// 变体3 single:   一次 write 整块 64MB —— 验证单次巨帧 write/drain 行为。
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import readline from "node:readline";

const PY = "D:/Project4work/Agenthub_7_8/coagentia/.venv/Scripts/python.exe";
const SCRIPT = "D:/Project4work/Agenthub_7_8/coagentia/scratchpad/tscal/cal6_sink.py";
const CHUNK_SIZE = 64 * 1024;
const N_CHUNKS = 1024; // 64MB

// 确定性数据：chunk i 全填 (i % 256)
function makeChunk(i) {
  return Buffer.alloc(CHUNK_SIZE, i % 256);
}
const expectHash = (() => {
  const h = createHash("sha256");
  for (let i = 0; i < N_CHUNKS; i++) h.update(makeChunk(i));
  return h.digest("hex");
})();

function runVariant(name) {
  return new Promise((resolve, reject) => {
    const child = spawn(PY, ["-X", "utf8", SCRIPT, "--slow"], { stdio: ["pipe", "pipe", "inherit"] });
    const watchdog = setTimeout(() => {
      try { child.kill(); } catch {}
      reject(new Error(`${name}: 120s 超时`));
    }, 120_000);
    const rl = readline.createInterface({ input: child.stdout });
    let report = null;
    rl.on("line", (line) => { try { report = JSON.parse(line); } catch {} });

    let falseCount = 0, drainCount = 0, peakBuffered = 0;
    child.stdin.on("drain", () => { drainCount++; });
    const t0 = Date.now();

    (async () => {
      if (name === "single") {
        const big = Buffer.concat(Array.from({ length: N_CHUNKS }, (_, i) => makeChunk(i)));
        const ok = child.stdin.write(big);
        if (!ok) falseCount++;
        peakBuffered = Math.max(peakBuffered, child.stdin.writableLength);
        if (!ok) await new Promise((r) => child.stdin.once("drain", r));
      } else {
        for (let i = 0; i < N_CHUNKS; i++) {
          const ok = child.stdin.write(makeChunk(i));
          if (!ok) {
            falseCount++;
            if (name === "respect") await new Promise((r) => child.stdin.once("drain", r));
          }
          if (child.stdin.writableLength > peakBuffered) peakBuffered = child.stdin.writableLength;
        }
      }
      child.stdin.end();
    })().catch(reject);

    child.on("exit", () => {
      clearTimeout(watchdog);
      // 给 readline 一拍冲刷残留行
      setTimeout(() => {
        resolve({
          variant: name,
          elapsed_ms: Date.now() - t0,
          write_false_count: falseCount,
          drain_events: drainCount,
          peak_buffered_bytes: peakBuffered,
          sink_bytes: report ? report.bytes : null,
          bytes_match: report ? report.bytes === CHUNK_SIZE * N_CHUNKS : false,
          sha256_match: report ? report.sha256 === expectHash : false,
        });
      }, 200);
    });
  });
}

const results = [];
for (const v of ["firehose", "respect", "single"]) {
  results.push(await runVariant(v));
}
console.log(JSON.stringify({
  probe: "cal6_c",
  total_bytes: CHUNK_SIZE * N_CHUNKS,
  chunk_size: CHUNK_SIZE,
  expect_sha256: expectHash.slice(0, 16) + "...",
  results,
}, null, 2));
