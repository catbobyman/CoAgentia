// cal5_c_release.mjs — c) 探针：spawn holder → taskkill /F /T → 循环重试 listen 计时端口释放
// 用法: node cal5_c_release.mjs <port> [trials=5]
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import net from 'node:net';

const port = Number(process.argv[2] || 8921);
const trials = Number(process.argv[3] || 5);
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
        try {
          const msg = JSON.parse(buf.slice(0, nl));
          if (msg.event === 'listening') resolve({ child, pid: msg.pid });
          else reject(new Error('holder error: ' + JSON.stringify(msg)));
        } catch (e) {
          reject(e);
        }
      }
    });
    child.on('error', reject);
    setTimeout(() => reject(new Error('holder start timeout')), 5000);
  });
}

const results = [];
for (let i = 0; i < trials; i++) {
  const { pid } = await spawnHolder();
  await sleep(50); // 确认稳定持有
  const pre = await tryListen(port);
  if (pre.ok) {
    pre.srv.close();
    console.log(JSON.stringify({ trial: i, error: 'port not actually held' }));
    continue;
  }
  const killRes = spawnSync('taskkill', ['/F', '/T', '/PID', String(pid)], { encoding: 'utf8' });
  const t0 = process.hrtime.bigint();
  let attempts = 0;
  let codes = [];
  let elapsedMs;
  // 紧循环重试，间隔 5ms
  for (;;) {
    attempts++;
    const r = await tryListen(port);
    if (r.ok) {
      elapsedMs = Number(process.hrtime.bigint() - t0) / 1e6;
      await new Promise((res) => r.srv.close(res));
      break;
    }
    codes.push(r.code);
    if (Number(process.hrtime.bigint() - t0) / 1e6 > 15000) {
      elapsedMs = -1;
      break;
    }
    await sleep(5);
  }
  results.push({
    trial: i,
    taskkill_status: killRes.status,
    preHeldCode: pre.code,
    release_ms: elapsedMs === -1 ? 'TIMEOUT>15000' : +elapsedMs.toFixed(2),
    attempts,
    failCodes: [...new Set(codes)],
  });
  console.log(JSON.stringify(results[results.length - 1]));
  await sleep(100);
}
const nums = results.map((r) => r.release_ms).filter((x) => typeof x === 'number');
if (nums.length) {
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
}
