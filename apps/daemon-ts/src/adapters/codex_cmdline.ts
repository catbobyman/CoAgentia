/**
 * Codex 命令行拼装 + CODEX_HOME 隔离 + config.toml 物化（契约 E2 §1/§2）。
 *
 * 纯函数（argv/env/config 构造）——可全量单测，不触发子进程。
 *
 * 与 claude cmdline 的差异面（E2 §1/§2 冻结）：
 * - 命令行：裸 `codex app-server`（stdio 默认监听；无 --listen 旗标，0.144.0 实测校准）。
 * - 隔离：`CODEX_HOME=<home>/.codex`（等价 CLAUDE_CONFIG_DIR，全局配置不继承，R6）；cwd=home_path。
 * - MCP 注入：CODEX_HOME/config.toml `[mcp_servers.coagentia]`（command/args）——工具目录复用
 *   `mcpCommand()`（契约 E §3 REST 纯代理，runtime 无关）。
 * - 权限姿态（NFR5 bypassPermissions 等价）：approvalPolicy=never + sandbox=danger-full-access
 *   经 thread/start params 注入（见 codex.ts），config.toml 只承载 MCP。
 * - 凭证物化：机器级 `~/.codex/auth.json` 复制进隔离 CODEX_HOME（ChatGPT 登录态；E2 §2.2）。
 *
 * 对等基准 = apps/daemon adapters/codex_cmdline.py。
 * resolveCodexBin 的 which = 手写 shutil.which 近似（win32 PATHEXT 遍历 + 当前目录前置），
 * 命中语义对齐 py（win32 下解析 codex.cmd npm shim）。
 */

import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import * as cmdline from './cmdline.ts';

// win32 npm shim = codex.cmd；允许 env 覆盖（同 COAGENTIA_CLAUDE_BIN 先例）。
export const CODEX_BIN = process.env['COAGENTIA_CODEX_BIN'] ?? 'codex';

// 机器级凭证文件（ChatGPT 登录态；隔离 CODEX_HOME 需物化才能鉴权，E2 §2.2）。
const CREDENTIAL_FILES = ['auth.json'] as const;

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

/** 手写 shutil.which 近似：带目录成分 → 直查；否则遍历 PATH（win32 前置 '.' + PATHEXT）。 */
function which(cmd: string): string | null {
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

/** 解析 codex 可执行绝对路径（win32 下 which 命中 codex.cmd）；未命中回退裸名。 */
export function resolveCodexBin(): string {
  return which(CODEX_BIN) ?? CODEX_BIN;
}

/** `codex app-server` 命令行（E2 §1.2；stdio 为默认监听面，无 --listen 旗标）。 */
export function buildAppServerArgv(): string[] {
  return [resolveCodexBin(), 'app-server'];
}

/** 机器级 codex home（凭证物化源）：env CODEX_HOME 或 ~/.codex。 */
export function machineCodexHome(): string {
  const env = process.env['CODEX_HOME'];
  return env ? env : path.join(os.homedir(), '.codex');
}

/** per-Agent 隔离 CODEX_HOME = <home>/.codex（E2 §2.1）。 */
export function isolatedCodexHome(homePath: string): string {
  return path.join(cmdline.expanduser(homePath), '.codex');
}

/** CODEX_HOME 隔离（E2 §2.1）：配置目录钉在 Home 内，全局配置/技能不继承。 */
export function buildEnv(
  homePath: string,
  baseEnv?: Record<string, string> | null,
): Record<string, string> {
  const env: Record<string, string> = {
    ...((baseEnv ?? process.env) as Record<string, string>),
  };
  env['CODEX_HOME'] = isolatedCodexHome(homePath);
  return env;
}

/**
 * 把机器级 codex 凭证复制进隔离 CODEX_HOME（E2 §2.2；ChatGPT 登录态）。
 *
 * best-effort：源缺失/损坏不抛（未登录 → 鉴权失败由 turn error 面暴露，verify 阶段确认）。
 */
export function materializeCredentials(codexHome: string, source?: string | null): string[] {
  const src = source ?? machineCodexHome();
  const home = cmdline.expanduser(codexHome);
  if (cmdline.resolveKey(src) === cmdline.resolveKey(home)) return [];
  const copied: string[] = [];
  fs.mkdirSync(home, { recursive: true });
  for (const name of CREDENTIAL_FILES) {
    const s = path.join(src, name);
    let srcIsFile = false;
    try {
      srcIsFile = fs.statSync(s).isFile();
    } catch {
      srcIsFile = false;
    }
    if (!srcIsFile) continue;
    try {
      const d = path.join(home, name);
      // 新鲜度选优（review #5）：隔离目标已存在且不比机器源旧 → 保留。codex app-server 运行时
      // 会刷新 OAuth token（写隔离 auth.json），无条件覆写会用机器旧凭证回退刷新；仅目标缺失
      // （首次）或机器源更新（用户重登）才复制。
      let dIsFile = false;
      try {
        dIsFile = fs.statSync(d).isFile();
      } catch {
        dIsFile = false;
      }
      if (dIsFile && fs.statSync(d).mtimeMs >= fs.statSync(s).mtimeMs) continue;
      const data = fs.readFileSync(s);
      const tmp = path.join(home, `${name}.${process.pid}.tmp`);
      fs.writeFileSync(tmp, data);
      try {
        fs.chmodSync(tmp, 0o600); // win32 近 no-op，保留调用（py 同款 suppress）
      } catch {
        // best-effort：chmod 失败不阻断
      }
      fs.renameSync(tmp, d); // ≡ py Path.replace（win32 覆盖既有）
      copied.push(name);
    } catch {
      // py contextlib.suppress(OSError) 对齐：单文件失败静默跳过
    }
  }
  return copied;
}

/**
 * config.toml 内容（E2 §2.3）：注入名为 coagentia 的 stdio MCP server。
 *
 * 值用 JSON.stringify 序列化——JSON basic string / array 是合法 TOML basic string / array
 * （同一双引号转义规则），win32 反斜杠路径由此正确转义（≡ py json.dumps）。
 */
export function buildConfigToml(opts: {
  agentMemberId: string;
  serverUrl: string;
  apiKey: string;
}): string {
  const [cmd, baseArgs] = cmdline.mcpCommand();
  const args = [
    ...baseArgs,
    '--agent-member',
    opts.agentMemberId,
    '--server-url',
    opts.serverUrl,
    '--api-key',
    opts.apiKey,
  ];
  return (
    '# CoAgentia 生成（E2 §2.3）——per-Agent 隔离 CODEX_HOME；勿手改。\n' +
    '[mcp_servers.coagentia]\n' +
    `command = ${JSON.stringify(cmd)}\n` +
    `args = ${JSON.stringify(args)}\n`
  );
}

/** 把 config.toml 写入 <CODEX_HOME>/config.toml，返回路径。 */
export function materializeConfig(
  codexHome: string,
  opts: { agentMemberId: string; serverUrl: string; apiKey: string },
): string {
  const home = cmdline.expanduser(codexHome);
  fs.mkdirSync(home, { recursive: true });
  const p = path.join(home, 'config.toml');
  fs.writeFileSync(p, buildConfigToml(opts), 'utf-8');
  return p;
}

/** win32 杀进程树（E2 §1.2）：terminate 杀不掉 app-server 底层 node，须 taskkill /F /T。 */
export function taskkillArgv(pid: number): string[] {
  return ['taskkill', '/F', '/T', '/PID', String(pid)];
}

export function isWin32(): boolean {
  return process.platform === 'win32';
}
