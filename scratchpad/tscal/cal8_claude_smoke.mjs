/**
 * cal8：真 claude CLI 冒烟诊断探针（TS 迁移批 adapter_smoke 超时根因定位）。
 *
 * 两相：
 *   node cal8_claude_smoke.mjs raw      —— 复刻 ClaudeCodeProcess.start 的 argv/env/cwd，直接
 *                                          child_process.spawn，全量落盘 stdout/stderr 原文 + 时间线。
 *   node cal8_claude_smoke.mjs adapter  —— 走真 RuntimeManager（src 全链，defaultSpawn/NodeLineReader），
 *                                          sink 事件全落盘 + daemon.log DEBUG。
 *
 * 纪律：不带掩盖性 env（env = buildEnv(home) 原样）；结束 taskkill /F /T 数组形式杀树。
 */

import { spawnSync, spawn } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const srcDir = path.resolve(here, '..', '..', 'apps', 'daemon-ts', 'src');
const srcUrl = (...p) => pathToFileURL(path.join(srcDir, ...p)).href;

const { buildArgv, buildEnv, materializeCredentials, materializeMcpConfig } = await import(
  srcUrl('adapters', 'cmdline.ts')
);
const { renderDeliver, userFrameLine } = await import(srcUrl('adapters', 'encoding.ts'));
const { DataPaths } = await import(srcUrl('paths.ts'));

const AID = '01K5CMPT00000000000000000A';
const CHAN = '01K5CHAN00000000000000000A';
const mode = process.argv[2] ?? 'raw';
const runDir = path.join(here, 'cal8_out', `${mode}-${Date.now()}`);
fs.mkdirSync(runDir, { recursive: true });

const t0 = Date.now();
const eventsPath = path.join(runDir, 'events.log');
function logEvent(tag, text) {
  const line = `[+${String(Date.now() - t0).padStart(6, ' ')}ms] ${tag}: ${text}\n`;
  fs.appendFileSync(eventsPath, line, 'utf-8');
  process.stdout.write(line);
}

function makeBoot(homePath) {
  return {
    agent_member_id: AID,
    name: 'Pat',
    runtime: 'claude_code',
    model: 'claude-opus-4-8',
    home_path: homePath,
    skills: [],
  };
}

function turnBody(n, body) {
  return [
    {
      id: `01K5MSG1000000000000000${String.fromCharCode(65 + (n % 26))}0`,
      channel_id: CHAN,
      author_member_id: '01K5AUTH00000000000000000A',
      created_at: '2026-07-09T00:00:00.000Z',
      body,
    },
  ];
}

function killTree(pid) {
  if (!pid) return;
  const r = spawnSync('taskkill', ['/F', '/T', '/PID', String(pid)], { encoding: 'utf-8' });
  logEvent('killTree', `pid=${pid} status=${r.status} out=${(r.stdout || '').trim()} err=${(r.stderr || '').trim()}`);
}

function envDigest(env) {
  const keys = Object.keys(env);
  const interesting = {};
  for (const k of keys) {
    if (/^(CLAUDE|COAGENTIA|NODE_OPTIONS|NODE_ENV|VITEST|ANTHROPIC)/i.test(k)) interesting[k] = env[k];
  }
  interesting['PATH?'] = keys.some((k) => k.toUpperCase() === 'PATH');
  interesting['env_key_count'] = keys.length;
  return interesting;
}

async function phaseRaw() {
  const paths = new DataPaths(path.join(runDir, 'root'));
  paths.ensureDirs();
  const boot = makeBoot(path.join(runDir, 'home'));
  const home = paths.ensureAgentHome(AID);
  const env = buildEnv(home);
  const configDir = env['CLAUDE_CONFIG_DIR'];
  const mcpPath = materializeMcpConfig(configDir, {
    agentMemberId: AID,
    serverUrl: 'http://127.0.0.1:1',
    apiKey: 'cak_smoke',
  });
  const copied = materializeCredentials(configDir);
  const argv = buildArgv(boot, { mcpConfigPath: mcpPath, resumeSessionId: null });

  fs.writeFileSync(path.join(runDir, 'argv.json'), JSON.stringify(argv, null, 2), 'utf-8');
  fs.writeFileSync(path.join(runDir, 'env_digest.json'), JSON.stringify(envDigest(env), null, 2), 'utf-8');
  logEvent('setup', `cwd=${home}`);
  logEvent('setup', `credentials copied=${JSON.stringify(copied)} configDir=${configDir}`);
  logEvent('setup', `argv[0]=${argv[0]} argc=${argv.length}`);

  const child = spawn(argv[0], argv.slice(1), { cwd: home, env, stdio: ['pipe', 'pipe', 'pipe'] });
  const outRaw = fs.createWriteStream(path.join(runDir, 'stdout.raw'));
  const errRaw = fs.createWriteStream(path.join(runDir, 'stderr.raw'));

  let sawResult = false;
  let sawInit = false;
  let stdoutLines = 0;
  let exited = null;
  let outBuf = Buffer.alloc(0);

  child.on('error', (e) => logEvent('child.error', String(e)));
  child.on('spawn', () => logEvent('child.spawn', `pid=${child.pid}`));
  child.on('close', (code, signal) => {
    exited = { code, signal };
    logEvent('child.close', `code=${code} signal=${signal}`);
  });

  child.stdout.on('data', (chunk) => {
    outRaw.write(chunk);
    outBuf = Buffer.concat([outBuf, chunk]);
    let idx;
    while ((idx = outBuf.indexOf(0x0a)) >= 0) {
      const line = outBuf.subarray(0, idx).toString('utf-8').trim();
      outBuf = outBuf.subarray(idx + 1);
      if (!line) continue;
      stdoutLines += 1;
      let type = '<non-json>';
      try {
        const f = JSON.parse(line);
        type = `${f.type}${f.subtype ? '/' + f.subtype : ''}`;
        if (f.type === 'system' && f.subtype === 'init') sawInit = true;
        if (f.type === 'result') sawResult = true;
      } catch {}
      logEvent('stdout.line', `#${stdoutLines} type=${type} len=${line.length} head=${line.slice(0, 300)}`);
    }
  });
  child.stderr.on('data', (chunk) => {
    errRaw.write(chunk);
    for (const l of chunk.toString('utf-8').split(/\r?\n/)) {
      if (l.trim()) logEvent('stderr', l.trim().slice(0, 500));
    }
  });

  // 复刻 runTurn：spawn 后立即投第一条 turn（管理器 start→emit idle→deliver 同拍序）。
  const text = renderDeliver(turnBody(0, 'Reply in plain text with exactly: PONG. No tools.'), {
    threadRootId: null,
  });
  const frame = userFrameLine(text) + '\n';
  fs.writeFileSync(path.join(runDir, 'stdin.sent'), frame, 'utf-8');
  child.stdin.write(Buffer.from(frame, 'utf-8'));
  logEvent('stdin.write', `bytes=${Buffer.byteLength(frame)}`);

  const deadline = Date.now() + 150_000;
  while (Date.now() < deadline && !sawResult && exited === null) {
    await new Promise((r) => setTimeout(r, 200));
  }
  logEvent('verdict', `sawInit=${sawInit} sawResult=${sawResult} stdoutLines=${stdoutLines} exited=${JSON.stringify(exited)}`);

  try {
    child.stdin.end();
  } catch {}
  await new Promise((r) => setTimeout(r, 3000));
  if (exited === null) killTree(child.pid);
  await new Promise((r) => setTimeout(r, 1000));
  logEvent('done', `final exited=${JSON.stringify(exited)}`);
}

async function phaseAdapter() {
  const { RuntimeManager } = await import(srcUrl('adapters', 'claude_code.ts'));
  const { setupFileLogging } = await import(srcUrl('logconfig.ts'));

  const paths = new DataPaths(path.join(runDir, 'root'));
  paths.ensureDirs();
  setupFileLogging(paths, 'DEBUG');
  const boot = makeBoot(path.join(runDir, 'home'));

  const usage = [];
  const statuses = [];
  const sink = {
    async onStatusChanged(aid, status, errorDetail) {
      statuses.push(status);
      logEvent('sink.status', `${status}${errorDetail ? ' detail=' + errorDetail : ''}`);
    },
    async onActivity(aid, detail) {
      logEvent('sink.activity', detail);
    },
    onUsage(ev) {
      usage.push(ev);
      logEvent('sink.usage', JSON.stringify(ev));
    },
    onDiagnostic(ev) {
      logEvent('sink.diag', `${ev.type} ${JSON.stringify(ev.payload).slice(0, 300)}`);
    },
  };

  const adapter = new RuntimeManager(paths, { serverUrl: 'http://127.0.0.1:1', apiKey: 'cak_smoke' });
  adapter.bind(sink);
  logEvent('adapter', 'start');
  const started = await adapter.start(boot);
  logEvent('adapter', `start returned ${started}`);
  const pid = adapter.agents.get(AID)?.process?.pid ?? null;
  logEvent('adapter', `child pid=${pid}`);

  await adapter.deliver(AID, CHAN, turnBody(0, 'Reply in plain text with exactly: PONG. No tools.'), null);
  logEvent('adapter', 'deliver returned');

  const deadline = Date.now() + 150_000;
  while (Date.now() < deadline && !(usage.length > 0 && statuses.at(-1) === 'idle')) {
    await new Promise((r) => setTimeout(r, 200));
  }
  logEvent('verdict', `usage=${usage.length} statuses=${JSON.stringify(statuses)}`);

  await adapter.stop(AID);
  logEvent('adapter', 'stopped');
  killTree(pid);

  // daemon.log 尾部并入产物
  try {
    const lines = fs.readFileSync(paths.logPath, 'utf-8').split('\n');
    fs.writeFileSync(path.join(runDir, 'daemon.log.tail'), lines.slice(-300).join('\n'), 'utf-8');
  } catch (e) {
    logEvent('daemonlog', `read failed: ${e}`);
  }
}

if (mode === 'raw') await phaseRaw();
else if (mode === 'adapter') await phaseAdapter();
else {
  console.error('usage: node cal8_claude_smoke.mjs raw|adapter');
  process.exit(2);
}
logEvent('exit', `runDir=${runDir}`);
process.exit(0);
