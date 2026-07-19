// cal5_c2_conn.mjs — c 变体：holder 持端口且有 1 条活动 TCP 连接时被 taskkill，测端口重绑延迟
// 用法: node cal5_c2_conn.mjs <port> [trials=3]
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import net from 'node:net';

const port = Number(process.argv[2] || 8919);
const trials = Number(process.argv[3] || 3);
const holderPath = fileURLToPath(new URL('./cal5_holder.mjs', import.meta.url));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function tryListen(port) {
  return new Promise((resolve) => {
    const srv = net.createServer();
    srv.on('error', (e) => resolve({ ok: false, code: e.code }));
    srv.listen({ port, host: '127.0.0.1', exclusive: true }, () => resolve({ ok: true, srv }));
  });
}

function spawnHolder() {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [holderPath, String(port), '127.0.0.1', 'true'], {
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let buf = '';
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', (d) => {
      buf += d;
      const nl = buf.indexOf('\n');
      if (nl >= 0) {
        const msg = JSON.parse(buf.slice(0, nl));
        if (msg.event === 'listening') resolve({ child, pid: msg.pid });
        else reject(new Error(JSON.stringify(msg)));
      }
    });
    child.on('error', reject);
    setTimeout(() => reject(new Error('holder start timeout')), 5000);
  });
}

function connectClient(port) {
  return new Promise((resolve, reject) => {
    const c = net.connect({ port, host: '127.0.0.1' }, () => resolve(c));
    c.on('error', reject);
  });
}

const results = [];
for (let i = 0; i < trials; i++) {
  const { pid } = await spawnHolder();
  const client = await connectClient(port); // 活动连接
  client.write('ping');
  await sleep(50);
  spawnSync('taskkill', ['/F', '/T', '/PID', String(pid)], { encoding: 'utf8' });
  const t0 = process.hrtime.bigint();
  let attempts = 0;
  let elapsedMs = -1;
  const codes = [];
  for (;;) {
    attempts++;
    const r = await tryListen(port);
    if (r.ok) {
      elapsedMs = Number(process.hrtime.bigint() - t0) / 1e6;
      await new Promise((res) => r.srv.close(res));
      break;
    }
    codes.push(r.code);
    if (Number(process.hrtime.bigint() - t0) / 1e6 > 15000) break;
    await sleep(5);
  }
  client.destroy();
  const rec = {
    trial: i,
    with_active_conn: true,
    release_ms: elapsedMs === -1 ? 'TIMEOUT' : +elapsedMs.toFixed(2),
    attempts,
    failCodes: [...new Set(codes)],
  };
  results.push(rec);
  console.log(JSON.stringify(rec));
  await sleep(100);
}
const nums = results.map((r) => r.release_ms).filter((x) => typeof x === 'number');
if (nums.length)
  console.log(
    JSON.stringify({
      summary: {
        trials: nums.length,
        min_ms: Math.min(...nums),
        max_ms: Math.max(...nums),
        avg_ms: +(nums.reduce((a, b) => a + b, 0) / nums.length).toFixed(2),
      },
    })
  );
