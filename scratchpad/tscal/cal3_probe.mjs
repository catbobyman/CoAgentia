// cal3_probe.mjs — 探针 3「进程树终止」主控
// 树形：node(本探针) → cmd.exe /c node cal3_mid.mjs（child=cmd, 孙=node mid）→ node cal3_sleeper.mjs（曾孙）
// 该形状 = npm.cmd → node(npm) → node(vite/dev server) 的同构缩影。
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, readFileSync, rmSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const tmp = join(here, 'tmp');
mkdirSync(tmp, { recursive: true });
const MID = join(here, 'cal3_mid.mjs');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const allPids = new Set();
const results = {};

function taskkillTree(pid) {
  const t0 = Date.now();
  const r = spawnSync('taskkill', ['/F', '/T', '/PID', String(pid)], { encoding: 'buffer' });
  const out = Buffer.concat([r.stdout ?? Buffer.alloc(0), r.stderr ?? Buffer.alloc(0)]);
  return {
    code: r.status,
    ms: Date.now() - t0,
    outLatin1: out.toString('latin1').trim().slice(0, 200),
    outHasNonAscii: [...out].some((b) => b > 0x7f),
  };
}

function aliveTasklist(pid) {
  const r = spawnSync('tasklist', ['/FI', `PID eq ${pid}`, '/NH', '/FO', 'CSV'], { encoding: 'utf8' });
  return (r.stdout || '').includes(`"${pid}"`);
}

async function waitFile(p, timeoutMs = 10000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    if (existsSync(p)) {
      const s = readFileSync(p, 'utf8').trim();
      if (s) return Number(s);
    }
    await sleep(50);
  }
  throw new Error('timeout waiting pidfile ' + p);
}

async function startTree(tag, { detached = false, orphan = false } = {}) {
  const midPidFile = join(tmp, `${tag}_mid.pid`);
  const slPidFile = join(tmp, `${tag}_sleeper.pid`);
  rmSync(midPidFile, { force: true });
  rmSync(slPidFile, { force: true });
  const child = spawn(
    'cmd.exe',
    ['/c', 'node', MID, midPidFile, slPidFile, orphan ? 'orphan' : 'normal'],
    { detached, stdio: 'ignore' },
  );
  if (detached) child.unref();
  const midPid = await waitFile(midPidFile);
  const sleeperPid = await waitFile(slPidFile);
  for (const p of [child.pid, midPid, sleeperPid]) allPids.add(p);
  return { child, cmdPid: child.pid, midPid, sleeperPid };
}

async function waitAllDead(pids, timeoutMs = 6000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    if (pids.every((p) => !aliveTasklist(p))) return Date.now() - t0;
    await sleep(100);
  }
  return -1;
}

function snapshot(t) {
  return { cmd: aliveTasklist(t.cmdPid), mid: aliveTasklist(t.midPid), sleeper: aliveTasklist(t.sleeperPid) };
}

// ---- 场景 A：child.kill()（默认 detached:false）----
async function scenarioA(tag, detached) {
  const t = await startTree(tag, { detached });
  const before = snapshot(t);
  const killRet = t.child.kill(); // win32 = TerminateProcess(仅 child 本体)
  await sleep(1200);
  const after = snapshot(t);
  // 收尾：杀残留
  taskkillTree(t.midPid);
  await waitAllDead([t.cmdPid, t.midPid, t.sleeperPid]);
  return { pids: { cmd: t.cmdPid, mid: t.midPid, sleeper: t.sleeperPid }, before, killReturned: killRet, afterKill: after };
}

// ---- 场景 B：taskkill /F /T /PID ----
async function scenarioB(tag, detached) {
  const t = await startTree(tag, { detached });
  const before = snapshot(t);
  const kill = taskkillTree(t.cmdPid);
  const allDeadMs = await waitAllDead([t.cmdPid, t.midPid, t.sleeperPid]);
  const after = snapshot(t);
  const again = taskkillTree(t.cmdPid); // 已死 pid 再杀一次 → 幂等语义/退出码
  return {
    pids: { cmd: t.cmdPid, mid: t.midPid, sleeper: t.sleeperPid },
    before,
    taskkill: kill,
    allDeadWithinMs: allDeadMs,
    after,
    killDeadPidAgain: { code: again.code, outHasNonAscii: again.outHasNonAscii },
  };
}

// ---- 场景 C：孤儿逃逸（中介 spawner 已死，sleeper 父 pid 悬空）----
async function scenarioC() {
  const t = await startTree('c_orphan', { orphan: true });
  await sleep(800); // 确保 spawner 已退出
  const before = snapshot(t);
  const kill = taskkillTree(t.cmdPid);
  await sleep(1200);
  const after = snapshot(t);
  taskkillTree(t.sleeperPid); // 收尾孤儿
  await waitAllDead([t.sleeperPid]);
  return { pids: { cmd: t.cmdPid, mid: t.midPid, sleeper: t.sleeperPid }, before, taskkill: kill, after };
}

// ---- 场景 D：process.kill(-pid)（POSIX 进程组语义在 win32 的行为）----
async function scenarioD() {
  const t = await startTree('d_negpid', { detached: true });
  let negErr = null;
  try {
    process.kill(-t.cmdPid);
  } catch (e) {
    negErr = { code: e.code, message: String(e.message).slice(0, 120) };
  }
  const exists0 = (() => {
    try { return process.kill(t.cmdPid, 0); } catch (e) { return 'threw:' + e.code; }
  })();
  taskkillTree(t.cmdPid);
  await waitAllDead([t.cmdPid, t.midPid, t.sleeperPid]);
  return { negPidKill: negErr ?? 'no-throw', signal0Exists: exists0 };
}

// ---- 场景 E：spawn('npm.cmd') 不带 shell（node 22 CVE-2024-27980 行为）----
function scenarioE() {
  const direct = spawnSync('npm.cmd', ['--version'], { encoding: 'utf8' });
  const withShell = spawnSync('npm --version', { shell: true, encoding: 'utf8' });
  return {
    directNoShell: { error: direct.error ? direct.error.code : null, status: direct.status },
    withShell: { status: withShell.status, version: (withShell.stdout || '').trim() },
  };
}

const t0 = Date.now();
results.A_default_childKill = await scenarioA('a_default', false);
results.A_detached_childKill = await scenarioA('a_detached', true);
results.B_default_taskkillT = await scenarioB('b_default', false);
results.B_detached_taskkillT = await scenarioB('b_detached', true);
results.C_orphan_escape = await scenarioC();
results.D_negative_pid = await scenarioD();
results.E_npm_cmd_spawn = scenarioE();

// ---- 终检：全部探针 pid 必须已死 ----
const leftovers = [...allPids].filter((p) => aliveTasklist(p));
for (const p of leftovers) taskkillTree(p);
results.final = { totalMs: Date.now() - t0, trackedPids: allPids.size, leftoversBeforeSweep: leftovers, leftoversAfterSweep: [...allPids].filter((p) => aliveTasklist(p)) };

console.log(JSON.stringify(results, null, 2));
