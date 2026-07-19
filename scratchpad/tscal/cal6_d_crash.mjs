// cal6 探针 d：子进程自杀(os._exit(42))时 node 侧事件与残留数据。
// 关键手法：不先挂 stdout 读取(流保持 paused)，等 'exit' 事件确认进程已死，
// 再开始读 stdout —— 验证进程死后管道残留数据(32KB+终言行)是否仍完整可取。
// 同时记录 exit/end/close 事件顺序与 stderr 残留。
import { spawn } from "node:child_process";

const PY = "D:/Project4work/Agenthub_7_8/coagentia/.venv/Scripts/python.exe";
const SCRIPT = "D:/Project4work/Agenthub_7_8/coagentia/scratchpad/tscal/cal6_suicide.py";
// 预期 stdout 字节：'HELLO\n'(6) + 32×1024(32768) + 'LAST-WORDS marker=cal6-final\n'(29)
const EXPECT_STDOUT_BYTES = 6 + 32768 + 29;

const t0 = Date.now();
const timeline = [];
const mark = (ev, extra = {}) => timeline.push({ t_ms: Date.now() - t0, ev, ...extra });

const child = spawn(PY, ["-X", "utf8", SCRIPT], { stdio: ["pipe", "pipe", "pipe"] });
const watchdog = setTimeout(() => { try { child.kill(); } catch {} ; console.error("WATCHDOG"); process.exit(2); }, 30_000);

let stdoutBytes = 0;
let stderrText = "";
let firstDataAfterExit = null;
let exitSeen = false;

child.on("exit", (code, signal) => { exitSeen = true; mark("exit", { code, signal }); });
child.on("close", (code, signal) => { mark("close", { code, signal }); });
child.stdout.on("end", () => mark("stdout_end"));
child.stderr.setEncoding("utf8");
child.stderr.on("data", (d) => { stderrText += d; });

// 等 exit 事件（进程已死），再开始读 stdout
await new Promise((res) => child.once("exit", res));
mark("begin_reading_stdout_after_exit");
child.stdout.on("data", (d) => {
  if (firstDataAfterExit === null) firstDataAfterExit = exitSeen;
  stdoutBytes += d.length;
});

await new Promise((res) => child.once("close", res));
clearTimeout(watchdog);
// stderr 流已随 close 结束
const tail = "LAST-WORDS marker=cal6-final";

console.log(JSON.stringify({
  probe: "cal6_d",
  expect_stdout_bytes: EXPECT_STDOUT_BYTES,
  got_stdout_bytes: stdoutBytes,
  residual_data_complete: stdoutBytes === EXPECT_STDOUT_BYTES,
  data_arrived_after_exit_event: firstDataAfterExit === true,
  stderr_residual: stderrText.trim(),
  stderr_residual_ok: stderrText.includes("dying-now code=42"),
  timeline,
}, null, 2));
