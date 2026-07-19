/**
 * runtime 探测（FR-2.3 / 契约 D §7 runtimes.detected；runtime.rescan 复用）。
 *
 * 探测 claude CLI 可执行与版本（`claude --version`）→ detected_runtimes。命令执行经可注入
 * runner（测试注桩免依赖真 CLI）；默认 runner 用 node 子进程（win32 下 which 解析
 * `claude.cmd` 绝对路径）。DetectedRuntime 形状（runtime/installed/models[/skills]）在 contracts。
 *
 * codex（M5，契约 E2）：`which codex` + `codex --version` 判在装；再 spawn `codex app-server`
 * 调 model/list + skills/list 填 models / 候选技能池，taskkill 收尾（冷路径，CALIBRATION §8）。
 * skills 字段由 H0 在 contracts 落地——未生成时探测仍拿名，构造时按字段存在与否兼容（不阻塞）。
 *
 * 对等基准 = apps/daemon probe.py。py→TS 差异登记（非行为改进）：
 * - py 捕 `(OSError, ValueError)` 判未安装；TS 无对应异常层级 → runner 抛任意异常统一按未安装。
 * - py `_make_detected` 按 `DetectedRuntime.model_fields` 运行时反射做 H0 skills 兼容降级；
 *   contracts-ts 类型已固化 skills 字段且无运行时模型 → 恒定「非空即填/空省略」
 *   （≡ py 字段已落地分支）。
 * - py 深探 15s 上限靠 asyncio.wait_for 任务取消触发 finally 杀进程；TS 无取消注入 →
 *   queryCodexAppServer 内部自带同值 withTimeout + finally killProcessTree（观测语义等价：
 *   超时 → 退化 [] + 进程收尾）。
 * - py readline 有 64KB 默认上限（超限 ValueError → 退化 []）；TS 手写 Buffer 行读无上限坑，
 *   但按校准条款 2 自设 32MB 守门（超限抛错 → 同样退化 []）。
 * - `_parse_version` 的 py str.isdigit 接受 Unicode 数字；TS 用 /^\d+$/ 仅 ASCII。
 * - win32 `.cmd` shim 裸 spawn EINVAL → shell:true 直启（校准条款 3；py CreateProcess 无此约束）。
 */

import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { once } from 'node:events';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import type { DetectedRuntime, Runtime } from '@coagentia/contracts-ts';

import { withTimeout } from './aio.ts';
import { killProcessTree } from './checks.ts';

// runner: (argv) -> [returncode, stdout, stderr]
export type CommandRunner = (argv: string[]) => Promise<[number, string, string]>;

// codex app-server 深探（model/list + skills/list）：(codexPath) -> [modelIds, skillNames]。
export type CodexQuery = (codexPath: string) => Promise<[string[], string[]]>;

type WhichFn = (cmd: string) => string | null;

const CODEX_QUERY_TIMEOUT_SEC = 15; // 深探总上限（慢/挂的 app-server 不阻塞 hello，退化 models/skills=[]）

// 校准条款 2：手写行读必须自设上限（node 累积无界）；超限抛错 → 深探退化 []。
const PROBE_LINE_LIMIT = 32 * 1024 * 1024;

// py 侧 = coagentia_daemon.__version__（0.1.0）；W4 前无共享版本单点（对齐 package.json version）。
const DAEMON_VERSION = '0.1.0';

const RUNTIME_CLAUDE_CODE: Runtime = 'claude_code';
const RUNTIME_CODEX: Runtime = 'codex';

// claude Code 已知模型（UI 模型下拉候选；契约无版本字段，模型列表为 detected_runtimes.models）。
// 权威候选随 CLI 演进——A7 真冒烟后可从 init 帧的 model/slash_commands 富化，此处给 MVP 默认。
export const DEFAULT_CLAUDE_MODELS: readonly string[] = [
  'claude-opus-4-8',
  'claude-sonnet-4-5',
  'claude-haiku-4-5',
];

// py shutil.which 的 win32 默认 PATHEXT（PATHEXT 未设时兜底）。
const WIN_DEFAULT_PATHEXT = '.COM;.EXE;.BAT;.CMD;.VBS;.JS;.WS;.MSC';

function accessCheck(p: string): boolean {
  try {
    const st = fs.statSync(p);
    if (!st.isFile()) return false;
    if (process.platform !== 'win32') fs.accessSync(p, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

/**
 * 手写 shutil.which 近似（≡ codex_cmdline.ts 私有 which；共享件缺口已登记）：
 * 带目录成分 → 直查；否则遍历 PATH（win32 前置 '.' + PATHEXT，命中解析 .cmd shim 绝对路径）。
 */
function defaultWhich(cmd: string): string | null {
  const isWin = process.platform === 'win32';
  const hasSep = cmd.includes('/') || (isWin && cmd.includes('\\'));
  let dirs: string[];
  let base: string;
  if (hasSep) {
    dirs = [path.dirname(cmd)];
    base = path.basename(cmd);
  } else {
    base = cmd;
    dirs = (process.env['PATH'] ?? '').split(path.delimiter);
    if (isWin && !dirs.includes('.')) dirs.unshift('.');
  }
  const files = isWin
    ? [
        base,
        ...(process.env['PATHEXT'] || WIN_DEFAULT_PATHEXT)
          .split(';')
          .filter(Boolean)
          .map((ext) => base + ext),
      ]
    : [base];
  for (const dir of dirs) {
    for (const file of files) {
      const name = path.join(dir, file);
      if (accessCheck(name)) return name;
    }
  }
  return null;
}

/** shell:true 拼串时给含空格的路径加引号（cmd.exe /s 语义下外层引号成对剥离）。 */
function quoteForShell(s: string): string {
  return s.includes(' ') && !s.startsWith('"') ? `"${s}"` : s;
}

/** win32 .cmd/.bat shim 判定（校准条款 3：裸 spawn EINVAL，须 shell:true）。 */
function isCmdShim(file: string): boolean {
  return process.platform === 'win32' && /\.(cmd|bat)$/i.test(file);
}

async function defaultRunner(argv: string[]): Promise<[number, string, string]> {
  const [file, ...args] = argv;
  const child = isCmdShim(file)
    ? spawn(quoteForShell(file), args.map(quoteForShell), {
        shell: true,
        stdio: ['ignore', 'pipe', 'pipe'],
      })
    : spawn(file, args, { stdio: ['ignore', 'pipe', 'pipe'] });
  // cal1：整段 Buffer.concat 后一次解码（严禁逐 chunk toString，多字节跨 chunk 会碎成 U+FFFD）。
  const outChunks: Buffer[] = [];
  const errChunks: Buffer[] = [];
  child.stdout?.on('data', (chunk: Buffer) => outChunks.push(chunk));
  child.stderr?.on('data', (chunk: Buffer) => errChunks.push(chunk));
  const code = await new Promise<number>((resolve, reject) => {
    child.on('error', (err) => reject(err)); // 对应 py 创建点 OSError（probe 侧统一按未安装）。
    child.on('close', (c) => resolve(c ?? 0)); // ≡ py `proc.returncode or 0`
  });
  // node Buffer→utf8 对非法序列替换 U+FFFD，≡ py decode(errors="replace")。
  return [code, Buffer.concat(outChunks).toString('utf-8'), Buffer.concat(errChunks).toString('utf-8')];
}

/** claude 全局配置目录：`CLAUDE_CONFIG_DIR` 覆盖，回退 `~/.claude`（契约 E §2 隔离基）。 */
function claudeConfigDir(): string {
  const override = process.env['CLAUDE_CONFIG_DIR'];
  return override ? override : path.join(os.homedir(), '.claude');
}

/**
 * claude 全局技能目录名清单（候选池，契约 E v1.4 §9；**列出 ≠ 授予**，授予走白名单）。
 *
 * 扫 `<configDir>/skills/` 下的子目录名（每个子目录 = 一个技能）；非目录条目（SCHEMA.md 等）
 * 与隐藏项跳过。目录缺失/不可读 → [] 不阻塞探测（与 codex 深探失败退化一致）。
 */
export function scanClaudeSkills(configDir?: string | null): string[] {
  const root = path.join(configDir ?? claudeConfigDir(), 'skills');
  try {
    if (!fs.statSync(root).isDirectory()) return [];
  } catch {
    return [];
  }
  const names: string[] = [];
  try {
    const entries = fs.readdirSync(root, { withFileTypes: true });
    entries.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
    for (const entry of entries) {
      // 跳过隐藏项与 symlink（review #3：不跟随，防指向大目录/循环卡住或越界）。
      if (entry.name.startsWith('.') || entry.isSymbolicLink()) continue;
      if (entry.isDirectory()) names.push(entry.name);
    }
  } catch {
    // py contextlib.suppress(OSError) 对齐：读目录失败不阻塞探测。
  }
  return names;
}

/**
 * 探测 claude CLI。返回 [DetectedRuntime, version|null]。
 *
 * 未安装（which 未命中）→ installed=false, models=[], skills=[]。
 * 命中 → `claude --version` rc==0 视为可用，models 用已知候选，skills 扫全局技能目录
 * （契约 E v1.4 §9 候选池；测试注入 skillsScan 桩免依赖真目录）。
 */
export async function probeClaude(
  runner?: CommandRunner | null,
  opts: { which?: WhichFn; skillsScan?: () => string[] } = {},
): Promise<[DetectedRuntime, string | null]> {
  const which = opts.which ?? defaultWhich;
  const skillsScan = opts.skillsScan ?? scanClaudeSkills;
  const claudePath = which('claude');
  if (!claudePath) return [makeDetected(RUNTIME_CLAUDE_CODE, false, [], []), null];
  const run = runner ?? defaultRunner;
  let rc: number;
  let out: string;
  try {
    [rc, out] = await run([claudePath, '--version']);
  } catch {
    // py 捕 (OSError, ValueError)；TS 无对应层级 → runner 任意异常统一按未安装（见文件头登记）。
    return [makeDetected(RUNTIME_CLAUDE_CODE, false, [], []), null];
  }
  const installed = rc === 0;
  const version = installed ? parseVersion(out) : null;
  const models = installed ? [...DEFAULT_CLAUDE_MODELS] : [];
  const skills = installed ? skillsScan() : [];
  return [makeDetected(RUNTIME_CLAUDE_CODE, installed, models, skills), version];
}

/** 从 `2.1.205 (Claude Code)` 一类输出提取首个 x.y.z。 */
export function parseVersion(text: string): string | null {
  for (const token of text.replace(/\(/g, ' ').replace(/\)/g, ' ').split(/\s+/)) {
    if (token === '') continue;
    const parts = token.split('.');
    if (parts.length >= 2 && parts.slice(0, 2).every((p) => /^\d+$/.test(p))) {
      return token;
    }
  }
  return null;
}

/** 构造 DetectedRuntime；skills 非空即填、空则省略（py H0 反射兼容分支的固化等价，见文件头）。 */
function makeDetected(
  runtime: Runtime,
  installed: boolean,
  models: string[],
  skills: string[],
): DetectedRuntime {
  const base: DetectedRuntime = { runtime, installed, models };
  if (skills.length > 0) return { ...base, skills };
  return base;
}

/**
 * 探测 codex CLI（契约 E2 / FR-2.5）。返回 [DetectedRuntime, version|null]。
 *
 * 未安装（which 未命中）或 `codex --version` 非零 → installed=false。命中 → 冷路径 spawn
 * `codex app-server` 调 model/list + skills/list 填 models / 候选技能池；深探失败退化 [] 不阻塞。
 */
export async function probeCodex(
  runner?: CommandRunner | null,
  opts: { which?: WhichFn; query?: CodexQuery | null } = {},
): Promise<[DetectedRuntime, string | null]> {
  const which = opts.which ?? defaultWhich;
  const codexPath = which('codex');
  if (!codexPath) return [makeDetected(RUNTIME_CODEX, false, [], []), null];
  const run = runner ?? defaultRunner;
  let rc: number;
  let out: string;
  try {
    [rc, out] = await run([codexPath, '--version']);
  } catch {
    return [makeDetected(RUNTIME_CODEX, false, [], []), null];
  }
  if (rc !== 0) return [makeDetected(RUNTIME_CODEX, false, [], []), null];
  const version = parseVersion(out);
  // 深探（spawn app-server）仅在生产默认路径跑（runner/query 均未注入）——注入 runner=测试上下文，
  // 跳过真机 spawn（deep query 走带内 stdio，无法经 runner 抽象；注入 query 则用桩）。
  let q: CodexQuery | null;
  if (opts.query !== undefined && opts.query !== null) {
    q = opts.query;
  } else if (runner === undefined || runner === null) {
    q = queryCodexAppServer;
  } else {
    q = null;
  }
  let models: string[] = [];
  let skills: string[] = [];
  if (q !== null) {
    try {
      [models, skills] = await withTimeout(q(codexPath), CODEX_QUERY_TIMEOUT_SEC * 1000);
    } catch {
      // py suppress(Exception) + wait_for：深探失败/超时退化 []，不阻塞 hello。
      models = [];
      skills = [];
    }
  }
  return [makeDetected(RUNTIME_CODEX, true, models, skills), version];
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

/** model/list 响应 → 模型 id 列表（去重保序；id 优先，回退 model 字段）。 */
function extractModelIds(result: unknown): string[] {
  const data = isRecord(result) ? result['data'] : null;
  const out: string[] = [];
  for (const item of Array.isArray(data) ? data : []) {
    if (!isRecord(item)) continue;
    const mid = item['id'] || item['model']; // ≡ py `item.get("id") or item.get("model")`（falsy 回退）
    if (typeof mid === 'string' && mid !== '' && !out.includes(mid)) out.push(mid);
  }
  return out;
}

/** skills/list 响应 → 技能名列表（跨 cwd 条目展平去重；候选池，列出≠授予）。 */
function extractSkillNames(result: unknown): string[] {
  const data = isRecord(result) ? result['data'] : null;
  const out: string[] = [];
  for (const entry of Array.isArray(data) ? data : []) {
    const skillList = isRecord(entry) ? entry['skills'] : null;
    for (const skill of Array.isArray(skillList) ? skillList : []) {
      const name = isRecord(skill) ? skill['name'] : null;
      if (typeof name === 'string' && name !== '' && !out.includes(name)) out.push(name);
    }
  }
  return out;
}

/**
 * spawn `codex app-server` → initialize/initialized → model/list + skills/list（E2 §8）。
 *
 * 一次性冷探：读到两条响应即收；win32 taskkill /F /T 杀进程树收尾（terminate 杀不掉 node）。
 * TS 差异（文件头登记）：15s 上限在本函数内自带（py 靠外层 wait_for 取消注入 finally）；
 * 行读手写 Buffer 累积按 \n 切分 + 32MB 守门（校准条款 2）。
 */
async function queryCodexAppServer(codexPath: string): Promise<[string[], string[]]> {
  const child = isCmdShim(codexPath)
    ? spawn(quoteForShell(codexPath), ['app-server'], {
        shell: true,
        stdio: ['pipe', 'pipe', 'ignore'], // stderr=DEVNULL 对齐 py（非管道，无积压面）
      })
    : spawn(codexPath, ['app-server'], { stdio: ['pipe', 'pipe', 'ignore'] });
  let models: string[] = [];
  let skills: string[] = [];
  const stdin = child.stdin;
  const stdout = child.stdout;
  // 写失败（进程早死 EPIPE）不炸事件循环：错误经 stdout EOF / child error 面收敛。
  stdin?.on('error', () => {});

  const seen = new Set<number>();
  let buf: Buffer = Buffer.alloc(0); // 显式 Buffer（ArrayBufferLike）——chunk 回填不撞泛型窄化
  const handleLine = (line: Buffer): void => {
    let frame: unknown;
    try {
      frame = JSON.parse(line.toString('utf-8')); // ≡ py decode(replace)+json.loads；坏行跳过
    } catch {
      return; // py suppress(ValueError)
    }
    const rid = isRecord(frame) ? frame['id'] : null;
    if (rid === 2) {
      models = extractModelIds(isRecord(frame) ? frame['result'] : null);
      seen.add(2);
    } else if (rid === 3) {
      skills = extractSkillNames(isRecord(frame) ? frame['result'] : null);
      seen.add(3);
    }
  };
  // cal6：spawn 当拍同步挂 stdout 消费者再 await（否则残留数据静默丢）。
  const done = new Promise<void>((resolve, reject) => {
    if (stdout === null) {
      resolve();
      return;
    }
    stdout.on('data', (chunk: Buffer) => {
      buf = buf.length === 0 ? chunk : Buffer.concat([buf, chunk]);
      let idx: number;
      while ((idx = buf.indexOf(0x0a)) !== -1) {
        const line = buf.subarray(0, idx);
        buf = Buffer.from(buf.subarray(idx + 1));
        handleLine(line);
        if (seen.size >= 2) {
          resolve(); // ≡ py while len(seen) < 2 收口
          return;
        }
      }
      if (buf.length > PROBE_LINE_LIMIT) {
        reject(new Error(`probe line exceeds ${PROBE_LINE_LIMIT} bytes`)); // 校准条款 2 守门
      }
    });
    stdout.on('end', () => resolve()); // ≡ py `if not line: break`（EOF 收部分结果）
    stdout.on('error', (err) => reject(err));
    child.on('error', (err) => reject(err));
  });
  done.catch(() => {
    // 预挂接消费：超时离场后 done 再 reject 不产生 unhandled rejection。
  });

  const interaction = (async (): Promise<void> => {
    const requests: Array<Record<string, unknown>> = [
      {
        id: 1,
        method: 'initialize',
        params: { clientInfo: { name: 'coagentia-probe', version: DAEMON_VERSION } },
      },
      { method: 'initialized' },
      { id: 2, method: 'model/list', params: {} },
      { id: 3, method: 'skills/list', params: { cwds: [os.homedir()] } },
    ];
    if (stdin !== null) {
      const payload = requests.map((m) => `${JSON.stringify(m)}\n`).join('');
      // cal6：write()===false → await drain（背压；15s 总上限之内）。
      if (!stdin.write(payload, 'utf-8')) {
        await once(stdin, 'drain');
      }
    }
    await done;
  })();
  interaction.catch(() => {
    // 同上：预挂接消费。
  });

  try {
    await withTimeout(interaction, CODEX_QUERY_TIMEOUT_SEC * 1000);
  } finally {
    await killProbeProcess(child);
  }
  return [models, skills];
}

/** 收尾杀探测进程（≡ py _kill_probe_process：win32 taskkill 树杀 / 其它 kill；≤3s 等退出）。 */
async function killProbeProcess(child: ChildProcess): Promise<void> {
  if (child.exitCode !== null || child.signalCode !== null) return;
  if (child.pid === undefined) {
    // spawn 未成活（error 路径）：无树可杀。
    try {
      child.kill();
    } catch {
      // suppress
    }
    return;
  }
  try {
    await killProcessTree(child.pid); // 校准条款 3：taskkill /F /T，code 128=幂等成功
  } catch {
    // py suppress(Exception)：非 0/128 退出码等异常不外抛。
  }
  try {
    await withTimeout(
      new Promise<void>((resolve) => {
        if (child.exitCode !== null || child.signalCode !== null) {
          resolve();
          return;
        }
        child.once('close', () => resolve()); // cal6：生命周期定稿只挂 'close'
      }),
      3000,
    );
  } catch {
    // py suppress(wait_for(proc.wait(), 3.0))：等收敛超时不外抛。
  }
}

/** 全 runtime 探测（claude_code + codex，契约 E2）。 */
export async function probeRuntimes(runner?: CommandRunner | null): Promise<DetectedRuntime[]> {
  const [claude] = await probeClaude(runner);
  const [codex] = await probeCodex(runner);
  return [claude, codex];
}
