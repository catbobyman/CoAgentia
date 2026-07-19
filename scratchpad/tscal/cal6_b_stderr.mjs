// cal6 探针 b：stderr 洪泛 2MB 时 stdout 是否被卡死。
// 模式1 no-drain: stderr=pipe 但永不读 —— 预期管道 64KB 满后 python 阻塞，stdout 停摆(死锁证据)。
// 模式2 drain:    stderr 挂 data 排空 —— 预期顺畅完成。
// 模式3 ignore:   stdio stderr='ignore'(丢弃) —— 预期顺畅完成(不需要 stderr 内容时的替代)。
import { spawn, execSync } from "node:child_process";
import readline from "node:readline";

const PY = "D:/Project4work/Agenthub_7_8/coagentia/.venv/Scripts/python.exe";
const SCRIPT = "D:/Project4work/Agenthub_7_8/coagentia/scratchpad/tscal/cal6_stderr_flood.py";
const EXPECT_LINES = 33; // 32 进度行 + 1 done
const STALL_TIMEOUT_MS = 8000;

function runMode(mode) {
  return new Promise((resolve) => {
    const stdio = mode === "ignore" ? ["ignore", "pipe", "ignore"] : ["ignore", "pipe", "pipe"];
    const child = spawn(PY, ["-X", "utf8", SCRIPT], { stdio });
    const t0 = Date.now();
    let lines = 0;
    let lastProgress = null;
    let stderrBytes = 0;
    let finished = false;

    if (mode === "drain") {
      child.stderr.on("data", (d) => { stderrBytes += d.length; });
    }
    // no-drain 模式：child.stderr 存在但绝不 read/绝不挂 data —— 流保持 paused，数据滞留内核管道

    const rl = readline.createInterface({ input: child.stdout });
    rl.on("line", (line) => {
      lines++;
      try { lastProgress = JSON.parse(line); } catch {}
      if (lines >= EXPECT_LINES) {
        finished = true;
        const elapsed = Date.now() - t0;
        cleanup();
        resolve({ mode, completed: true, elapsed_ms: elapsed, stdout_lines: lines, stderr_drained_bytes: stderrBytes, last_progress: lastProgress });
      }
    });

    const timer = setTimeout(() => {
      if (finished) return;
      const elapsed = Date.now() - t0;
      cleanup();
      resolve({ mode, completed: false, elapsed_ms: elapsed, stdout_lines: lines, stderr_drained_bytes: stderrBytes, last_progress: lastProgress, verdict: "STALLED(死锁)" });
    }, STALL_TIMEOUT_MS);

    function cleanup() {
      clearTimeout(timer);
      try { child.kill(); } catch {}
      try { execSync(`taskkill /F /T /PID ${child.pid}`, { stdio: "ignore" }); } catch {}
    }
  });
}

const results = [];
for (const mode of ["no-drain", "drain", "ignore"]) {
  results.push(await runMode(mode));
}
console.log(JSON.stringify({ probe: "cal6_b", expect_stdout_lines: EXPECT_LINES, results }, null, 2));
