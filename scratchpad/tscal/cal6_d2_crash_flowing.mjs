// cal6 探针 d2：cal6_d 的对照组。同一自杀子进程(32KB 残留+os._exit(42))：
// 变体 flowing:   spawn 当拍即挂 stdout data 监听(流动态) —— 残留能否完整取回?
// 变体 pause-mid: 挂监听收到第一包后立即 pause()，等 exit 后再 resume —— 已开始读但暂停时的残留?
import { spawn } from "node:child_process";

const PY = "D:/Project4work/Agenthub_7_8/coagentia/.venv/Scripts/python.exe";
const SCRIPT = "D:/Project4work/Agenthub_7_8/coagentia/scratchpad/tscal/cal6_suicide.py";
const EXPECT_STDOUT_BYTES = 6 + 32768 + 29; // 32803

function runVariant(name) {
  return new Promise((resolve) => {
    const t0 = Date.now();
    const timeline = [];
    const mark = (ev, extra = {}) => timeline.push({ t_ms: Date.now() - t0, ev, ...extra });
    const child = spawn(PY, ["-X", "utf8", SCRIPT], { stdio: ["pipe", "pipe", "pipe"] });
    const watchdog = setTimeout(() => { try { child.kill(); } catch {} ; resolve({ variant: name, verdict: "WATCHDOG-TIMEOUT" }); }, 30_000);

    let stdoutBytes = 0;
    let bytesBeforeExit = 0;
    let exitSeen = false;
    let paused = false;
    let stderrText = "";
    let lastText = "";

    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (d) => { stderrText += d; });
    child.on("exit", (code, signal) => {
      exitSeen = true;
      bytesBeforeExit = stdoutBytes;
      mark("exit", { code, signal, stdout_bytes_so_far: stdoutBytes });
      if (name === "pause-mid" && paused) {
        setTimeout(() => { mark("resume"); child.stdout.resume(); }, 100);
      }
    });
    child.stdout.on("data", (d) => {
      stdoutBytes += d.length;
      lastText += d.toString("utf8");
      if (lastText.length > 64) lastText = lastText.slice(-64);
      if (name === "pause-mid" && !paused) {
        paused = true;
        child.stdout.pause();
        mark("pause_after_first_data", { bytes: stdoutBytes });
      }
    });
    child.stdout.on("end", () => mark("stdout_end", { total: stdoutBytes }));
    child.on("close", (code) => {
      mark("close", { code });
      clearTimeout(watchdog);
      resolve({
        variant: name,
        expect_bytes: EXPECT_STDOUT_BYTES,
        got_bytes: stdoutBytes,
        complete: stdoutBytes === EXPECT_STDOUT_BYTES,
        bytes_already_read_at_exit: bytesBeforeExit,
        bytes_recovered_after_exit: stdoutBytes - bytesBeforeExit,
        last_line_seen: lastText.includes("LAST-WORDS marker=cal6-final"),
        stderr_ok: stderrText.includes("dying-now code=42"),
        exit_seen: exitSeen,
        timeline,
      });
    });
  });
}

const results = [];
for (const v of ["flowing", "pause-mid"]) {
  results.push(await runVariant(v));
}
console.log(JSON.stringify({ probe: "cal6_d2", results }, null, 2));
