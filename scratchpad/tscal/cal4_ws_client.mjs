// cal4 探针: node 22 原生 WebSocket (undici) 客户端能力校准
// a) 文本中文/emoji、二进制、5MB 大消息往返完好性
// b) ping/pong API 有无、close code/reason、对端强杀感知延迟
// c) 100 条 8KB 并发发送保序
import { spawn } from "node:child_process";
import crypto from "node:crypto";
import { performance } from "node:perf_hooks";

const PORT = 8917;
const COAGENTIA = "D:/Project4work/Agenthub_7_8/coagentia";
const results = { node: process.version, probes: {} };
let serverProc = null;
let serverExited = false;

function startServer() {
  return new Promise((resolve, reject) => {
    serverProc = spawn(
      "uv",
      ["run", "python", "scratchpad/tscal/cal4_echo_server.py"],
      { cwd: COAGENTIA, shell: true, windowsHide: true }
    );
    serverProc.stdout.setEncoding("utf8");
    let buf = "";
    serverProc.stdout.on("data", (d) => {
      buf += d;
      if (buf.includes("READY")) resolve();
    });
    serverProc.stderr.setEncoding("utf8");
    serverProc.stderr.on("data", (d) => process.stderr.write("[srv] " + d));
    serverProc.on("exit", (code) => {
      serverExited = true;
    });
    setTimeout(() => reject(new Error("server start timeout 30s")), 30000);
  });
}

function killServerTree() {
  if (serverProc && !serverExited) {
    return spawn("taskkill", ["/F", "/T", "/PID", String(serverProc.pid)], {
      windowsHide: true,
    });
  }
  return null;
}

function connect() {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(`ws://127.0.0.1:${PORT}/`);
    ws.binaryType = "arraybuffer";
    const to = setTimeout(() => reject(new Error("connect timeout 10s")), 10000);
    ws.addEventListener(
      "open",
      () => {
        clearTimeout(to);
        resolve(ws);
      },
      { once: true }
    );
    ws.addEventListener(
      "error",
      (e) => {
        clearTimeout(to);
        reject(new Error("connect error: " + (e.message || e.type)));
      },
      { once: true }
    );
  });
}

function roundtrip(ws, payload, timeoutMs = 60000) {
  return new Promise((resolve, reject) => {
    const to = setTimeout(
      () => reject(new Error(`roundtrip timeout ${timeoutMs}ms`)),
      timeoutMs
    );
    ws.addEventListener(
      "message",
      (ev) => {
        clearTimeout(to);
        resolve(ev.data);
      },
      { once: true }
    );
    ws.send(payload);
  });
}

async function probeA(ws) {
  const out = {};

  // A1 文本 中文/emoji
  const text = "中文测试：你好，世界！🚀🎉🐍 café ñ € ①②③ 𝕌𝕟𝕚𝕔𝕠𝕕𝕖";
  const t1 = performance.now();
  const echoText = await roundtrip(ws, text);
  out.text = {
    sent_chars: text.length,
    sent_utf8_bytes: Buffer.byteLength(text, "utf8"),
    echo_type: typeof echoText,
    identical: echoText === text,
    ms: +(performance.now() - t1).toFixed(2),
  };

  // A2 二进制 64KB 随机
  const bin = crypto.randomBytes(64 * 1024);
  const t2 = performance.now();
  const echoBin = await roundtrip(ws, bin);
  const echoBinBuf = Buffer.from(echoBin);
  out.binary_64k = {
    sent_bytes: bin.length,
    recv_bytes: echoBinBuf.length,
    recv_ctor: echoBin?.constructor?.name,
    identical: Buffer.compare(bin, echoBinBuf) === 0,
    ms: +(performance.now() - t2).toFixed(2),
  };

  // A3 二进制 5MB 随机
  const big = crypto.randomBytes(5 * 1024 * 1024);
  const t3 = performance.now();
  const echoBig = await roundtrip(ws, big, 120000);
  const echoBigBuf = Buffer.from(echoBig);
  out.binary_5mb = {
    sent_bytes: big.length,
    recv_bytes: echoBigBuf.length,
    identical: Buffer.compare(big, echoBigBuf) === 0,
    ms: +(performance.now() - t3).toFixed(2),
  };

  // A4 文本 5MB (多字节字符, 模拟大 JSON 帧场景)
  const unit = '{"节点":"任务🚀","状态":"完成"}';
  const unitBytes = Buffer.byteLength(unit, "utf8");
  const repeat = Math.ceil((5 * 1024 * 1024) / unitBytes);
  const bigText = unit.repeat(repeat);
  const t4 = performance.now();
  const echoBigText = await roundtrip(ws, bigText, 120000);
  out.text_5mb = {
    sent_utf8_bytes: Buffer.byteLength(bigText, "utf8"),
    recv_utf8_bytes: Buffer.byteLength(echoBigText, "utf8"),
    identical: echoBigText === bigText,
    ms: +(performance.now() - t4).toFixed(2),
  };

  return out;
}

async function probeB_api(ws) {
  const protoNames = Object.getOwnPropertyNames(
    Object.getPrototypeOf(ws)
  ).sort();
  return {
    prototype_props: protoNames,
    has_ping_method: typeof ws.ping === "function",
    has_pong_method: typeof ws.pong === "function",
    has_bufferedAmount: typeof ws.bufferedAmount === "number",
    has_on_ping_event: false, // WHATWG 事件集只有 open/message/error/close
  };
}

async function probeB_serverPing(ws) {
  // 服务端发协议级 ping, 若 node 客户端自动回 pong, 服务端会回 PONG_OK:<ms>
  const reply = await roundtrip(ws, "__ping__", 10000);
  const m = /^PONG_OK:([\d.]+)$/.exec(reply);
  return {
    auto_pong_reply: !!m,
    server_measured_ping_rtt_ms: m ? +m[1] : null,
    raw: String(reply).slice(0, 40),
  };
}

function probeB_closeCode() {
  return new Promise(async (resolve, reject) => {
    const ws = await connect();
    const to = setTimeout(() => reject(new Error("close event timeout 10s")), 10000);
    const t0 = performance.now();
    ws.addEventListener(
      "close",
      (ev) => {
        clearTimeout(to);
        resolve({
          got_close_event: true,
          code: ev.code,
          reason: ev.reason,
          wasClean: ev.wasClean,
          ms_after_request: +(performance.now() - t0).toFixed(2),
        });
      },
      { once: true }
    );
    ws.send("__close__");
  });
}

function probeB_killDetect() {
  return new Promise(async (resolve, reject) => {
    const ws = await connect();
    const to = setTimeout(
      () => resolve({ detected: false, note: "20s 内未收到 close/error 事件" }),
      20000
    );
    const events = [];
    let tKillIssued = 0;
    let tKillDone = 0;
    const finish = () => {
      clearTimeout(to);
      const now = performance.now();
      resolve({
        detected: true,
        events,
        ws_readyState: ws.readyState,
        ms_from_kill_issued: +(now - tKillIssued).toFixed(2),
        ms_from_taskkill_exit: tKillDone ? +(now - tKillDone).toFixed(2) : null,
      });
    };
    ws.addEventListener("error", (e) => {
      events.push({ ev: "error", at_ms: +(performance.now() - tKillIssued).toFixed(2) });
    });
    ws.addEventListener(
      "close",
      (ev) => {
        events.push({
          ev: "close",
          code: ev.code,
          reason: ev.reason,
          wasClean: ev.wasClean,
          at_ms: +(performance.now() - tKillIssued).toFixed(2),
        });
        finish();
      },
      { once: true }
    );
    tKillIssued = performance.now();
    const killer = killServerTree();
    if (killer) {
      killer.on("exit", () => {
        tKillDone = performance.now();
      });
    } else {
      reject(new Error("server 已退出, 无法做强杀探测"));
    }
  });
}

async function probeC(ws) {
  const N = 100;
  const SIZE = 8 * 1024;
  const msgs = [];
  for (let i = 0; i < N; i++) {
    const head = `MSG${String(i).padStart(4, "0")}|`;
    msgs.push(head + "x".repeat(SIZE - head.length));
  }
  const received = [];
  let maxBuffered = 0;
  const done = new Promise((resolve, reject) => {
    const to = setTimeout(() => reject(new Error("probeC timeout 30s")), 30000);
    const onMsg = (ev) => {
      received.push(ev.data);
      if (received.length === N) {
        clearTimeout(to);
        ws.removeEventListener("message", onMsg);
        resolve();
      }
    };
    ws.addEventListener("message", onMsg);
  });
  const t0 = performance.now();
  for (let i = 0; i < N; i++) {
    ws.send(msgs[i]); // 同步紧循环, 不 await, 验证发送队列保序
    if (ws.bufferedAmount > maxBuffered) maxBuffered = ws.bufferedAmount;
  }
  await done;
  const elapsed = performance.now() - t0;
  let orderOk = true;
  let contentOk = true;
  let firstBad = -1;
  for (let i = 0; i < N; i++) {
    const idx = parseInt(received[i].slice(3, 7), 10);
    if (idx !== i) {
      orderOk = false;
      if (firstBad < 0) firstBad = i;
    }
    if (received[i] !== msgs[idx >= 0 && idx < N ? idx : i]) {
      contentOk = false;
      if (firstBad < 0) firstBad = i;
    }
  }
  return {
    n: N,
    size_bytes: SIZE,
    order_preserved: orderOk,
    content_intact: contentOk,
    first_bad_index: firstBad,
    max_bufferedAmount: maxBuffered,
    total_ms: +elapsed.toFixed(2),
    throughput_msgs_per_s: +((N / elapsed) * 1000).toFixed(0),
  };
}

async function main() {
  await startServer();
  results.server = "READY on 127.0.0.1:" + PORT;

  const ws = await connect();
  results.probes.a_roundtrip = await probeA(ws);
  results.probes.b_api = await probeB_api(ws);
  results.probes.b_server_ping_auto_pong = await probeB_serverPing(ws);
  results.probes.c_order_100x8k = await probeC(ws);
  ws.close(1000, "cal4-done");

  results.probes.b_close_code = await probeB_closeCode();
  // 强杀探测放最后 (会杀掉 server)
  results.probes.b_kill_detect = await probeB_killDetect();

  console.log("===CAL4_RESULTS===");
  console.log(JSON.stringify(results, null, 2));
}

main()
  .catch((e) => {
    console.log("===CAL4_ERROR===");
    console.log(String(e && e.stack ? e.stack : e));
    process.exitCode = 1;
  })
  .finally(() => {
    killServerTree();
    setTimeout(() => process.exit(process.exitCode || 0), 1500);
  });
