/**
 * 命令行拼装 + 环境隔离 + MCP 配置物化（契约 E §2/§3）。
 *
 * 纯函数（argv/env/config 构造）——可全量单测，不触发子进程。
 *
 * E §2 命令行：
 *     claude --output-format stream-json --input-format stream-json
 *            --include-partial-messages --permission-mode bypassPermissions
 *            --model <model> --append-system-prompt <身份注入>
 *            --mcp-config <coagentia-mcp.json>
 *            --disallowed-tools <DISALLOWED_TOOLS...> --verbose
 * 隔离：CLAUDE_CONFIG_DIR=<home>/.claude（全局技能/配置不继承，R6）；cwd=home_path。
 * `--verbose` 本模式真机实测必需（否则帧不全，E §11.2 已确认）。
 *
 * 对等基准 = apps/daemon adapters/cmdline.py。
 * 接缝（任务书裁决 #10）：mcpCommand 指向 node 版 MCP 入口（daemon-ts 自身 cli.ts mcp），
 * 非 py 的 `sys.executable -m coagentia_daemon mcp`。
 */

import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { fileURLToPath } from 'node:url';

import type { AgentBoot } from '@coagentia/contracts-ts';

import { DISALLOWED_TOOLS } from '../generated/constants.ts';

export type JsonObject = Record<string, unknown>;

export const CLAUDE_BIN = process.env['COAGENTIA_CLAUDE_BIN'] ?? 'claude';

// ---- 内部助手（py 侧为 pathlib 内建，TS 手写；codex_cmdline 复用，不重发明）----

/** py Path.expanduser 对等（仅 `~`/`~/` 前缀；`~user` 形式不支持，登记差异）。 */
export function expanduser(p: string): string {
  if (p === '~') return os.homedir();
  if (p.startsWith('~/') || p.startsWith('~\\')) {
    return path.join(os.homedir(), p.slice(2));
  }
  return p;
}

/**
 * py `Path.resolve() ==` 对等的路径同一性键：绝对化 + win32 大小写折叠
 * （py WindowsPath 相等比较大小写不敏感；node path.resolve 不触盘、不解析符号链接，登记差异）。
 */
export function resolveKey(p: string): string {
  const resolved = path.resolve(p);
  return process.platform === 'win32' ? resolved.toLowerCase() : resolved;
}

// 身份注入文案（E §2：文本是产品文案不冻结；必含名字/member_id/工具用法/护栏约定）。
// 与 py _IDENTITY_TEMPLATE 逐字对齐（【交付纪律】等原文保留）。
function identityText(name: string, memberId: string): string {
  return (
    `你是 CoAgentia 工作区的 Agent「${name}」（member_id=${memberId}）。\n` +
    '工作区语言：中文；沟通简洁、对事不对人。\n' +
    '【发言纪律】你的一切主动行为都必须通过名为 coagentia 的 MCP server 提供的工具完成，' +
    '对应关系：\n' +
    '  · 发频道/线程消息 → coagentia 的 send_message 工具（**不是**内置 SendMessage）；\n' +
    '  · 上传文件 → upload_file；回看历史 → get_messages / get_thread；\n' +
    '  · 建/销提醒 → create_reminder / cancel_reminder；' +
    '看频道/成员 → list_channels / list_members。\n' +
    '这些工具属 coagentia MCP server；若尚未加载，先用 ToolSearch 搜 "coagentia" 载入再调用。\n' +
    '散文正文不会被转成频道消息——只有显式调用 coagentia 工具才会真正发出。\n' +
    '【交付纪律】完成实现/评审后，置任务 in_review/done 之前，先用 submit_task_contract 工具' +
    '提交 TaskHandoff（kind=task_handoff，含 deliverables≥1 + evidence + verify_plan）；' +
    '跳过则 set_task_status 会以 422 HANDOFF_INCOMPLETE 退回（错误里带补齐提示）。' +
    '置 in_review 后，在频道发一条交付消息并 @ 派活人（通常是协调者）——对方只有被 @ 才会' +
    '被唤醒验收，交付不 @ 人会停在 in_review 没人接。\n' +
    '护栏：send_message 返回 202 held（被扣）时停止重发、等待反馈直投，勿盲目重试。\n' +
    '记忆载体是你的 Home（MEMORY.md / notes/），当前工作目录即你的 Home。'
  );
}

/** --append-system-prompt 身份注入文本（§2）。 */
export function buildIdentityPrompt(boot: AgentBoot): string {
  return identityText(boot.name, boot.agent_member_id);
}

/** CLAUDE_CONFIG_DIR 隔离（§2）：配置目录钉在 Home 内，全局技能/配置不继承。 */
export function buildEnv(
  homePath: string,
  baseEnv?: Record<string, string> | null,
): Record<string, string> {
  const env: Record<string, string> = {
    ...((baseEnv ?? process.env) as Record<string, string>),
  };
  env['CLAUDE_CONFIG_DIR'] = path.join(expanduser(homePath), '.claude');
  return env;
}

/** 机器级 claude 配置目录（凭证物化源，FR-2.3）。 */
export function defaultConfigDir(): string {
  const env = process.env['CLAUDE_CONFIG_DIR'];
  return env ? env : path.join(os.homedir(), '.claude');
}

const CREDENTIAL_FILES = ['.credentials.json'] as const;

// py 元组评分 → 数组逐位比较（index 3 = mtimeNs bigint，避免 ms 精度丢失）。
type CredentialScore = [number, number, number, bigint];

function isRecord(v: unknown): v is JsonObject {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

/** py `int(x or 0)` 对等：falsy → 0；非数值抛错（py int() 同抛 ValueError）。 */
function asInt(v: unknown): number {
  if (!v) return 0;
  const n = Number(v);
  if (!Number.isFinite(n)) throw new TypeError(`invalid int: ${String(v)}`);
  return Math.trunc(n);
}

/** 优先选择含 OAuth token 且过期时间更新的凭证；损坏 JSON 不参与同步。 */
function credentialScore(p: string): CredentialScore | null {
  let raw: unknown;
  let mtimeNs: bigint;
  try {
    raw = JSON.parse(fs.readFileSync(p, 'utf-8'));
    mtimeNs = fs.statSync(p, { bigint: true }).mtimeNs;
  } catch {
    return null;
  }
  const oauth = isRecord(raw) ? raw['claudeAiOauth'] : null;
  if (!isRecord(oauth)) return [0, 0, 0, mtimeNs];
  const hasTokens = oauth['accessToken'] && oauth['refreshToken'] ? 1 : 0;
  const expiresAt = asInt(oauth['expiresAt']);
  const refreshExpiresAt = asInt(oauth['refreshTokenExpiresAt']);
  return [hasTokens, expiresAt, refreshExpiresAt, mtimeNs];
}

/** py 元组字典序比较对等：a<b → -1，a>b → 1，相等 → 0。 */
function compareScore(a: CredentialScore, b: CredentialScore): number {
  for (let i = 0; i < a.length; i += 1) {
    const x = a[i]!;
    const y = b[i]!;
    if (x < y) return -1;
    if (x > y) return 1;
  }
  return 0;
}

function credentialCandidates(configDir: string, source: string): string[] {
  const candidates: string[] = CREDENTIAL_FILES.map((name) => path.join(source, name));
  const agentsDir = path.dirname(path.dirname(configDir));
  let agentsIsDir = false;
  try {
    agentsIsDir = fs.statSync(agentsDir).isDirectory();
  } catch {
    agentsIsDir = false;
  }
  if (path.basename(agentsDir) === 'agents' && agentsIsDir) {
    for (const peer of fs.readdirSync(agentsDir)) {
      for (const name of CREDENTIAL_FILES) {
        candidates.push(path.join(agentsDir, peer, '.claude', name));
      }
    }
  }
  return candidates;
}

/**
 * 把机器级 runtime 凭证复制进隔离配置目录（§2 凭证物化；BYO Key 不经 server）。
 *
 * 每次启动/投递前从机器级配置和同 daemon 的 Agent 配置中选择最新有效凭证。OAuth 刷新会
 * 轮换 refresh token；因此一个 Agent 刷新成功后，其他隔离配置可自动吸收新凭证并自愈。
 */
export function materializeCredentials(configDir: string, source?: string | null): string[] {
  const src = source ?? defaultConfigDir();
  if (resolveKey(src) === resolveKey(configDir)) return [];
  const copied: string[] = [];
  fs.mkdirSync(configDir, { recursive: true });
  for (const name of CREDENTIAL_FILES) {
    const d = path.join(configDir, name);
    const dKey = resolveKey(d);
    const scored: Array<[CredentialScore, string]> = [];
    for (const candidate of credentialCandidates(configDir, src)) {
      if (path.basename(candidate) !== name) continue;
      if (resolveKey(candidate) === dKey) continue;
      const score = credentialScore(candidate);
      if (score !== null) scored.push([score, candidate]);
    }
    if (scored.length === 0) continue;
    // py max()：仅严格更大才替换 → 平分保留先遇者
    let [bestScore, best] = scored[0]!;
    for (const [s, c] of scored.slice(1)) {
      if (compareScore(s, bestScore) > 0) {
        bestScore = s;
        best = c;
      }
    }
    const currentScore = credentialScore(d);
    if (currentScore !== null && compareScore(currentScore, bestScore) >= 0) continue;
    try {
      const data = fs.readFileSync(best);
      const tmp = path.join(configDir, `${name}.${process.pid}.tmp`);
      fs.writeFileSync(tmp, data);
      try {
        fs.chmodSync(tmp, 0o600); // win32 近 no-op，保留调用（py 同款 suppress）
      } catch {
        // best-effort：chmod 失败不阻断
      }
      fs.renameSync(tmp, d); // node win32 rename 覆盖既有文件（≡ py Path.replace）
      copied.push(name);
    } catch {
      // py contextlib.suppress(OSError) 对齐：单文件失败静默跳过
    }
  }
  return copied;
}

/**
 * coagentia MCP stdio server 的启动命令（§3）。
 *
 * 接缝（裁决 #10）：node 直跑 daemon-ts 自身入口 `node <src/cli.ts> mcp ...`
 * （py 侧为 `sys.executable -m coagentia_daemon mcp`；适配器拉起的 MCP server 身份随宿主语言）。
 * cli.ts 属 W4 波产物——路径按本模块相对位置预先解析。
 */
export function mcpCommand(): [string, string[]] {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const cliPath = path.resolve(here, '..', 'cli.ts');
  return [process.execPath, [cliPath, 'mcp']];
}

/** coagentia-mcp.json 内容（§3）：注入名为 coagentia 的 stdio MCP server。 */
export function buildMcpConfig(opts: {
  agentMemberId: string;
  serverUrl: string;
  apiKey: string;
}): JsonObject {
  const [cmd, baseArgs] = mcpCommand();
  return {
    mcpServers: {
      coagentia: {
        type: 'stdio',
        command: cmd,
        args: [
          ...baseArgs,
          '--agent-member',
          opts.agentMemberId,
          '--server-url',
          opts.serverUrl,
          '--api-key',
          opts.apiKey,
        ],
      },
    },
  };
}

/** 把 MCP 配置写入 <config_dir>/coagentia-mcp.json，返回路径。 */
export function materializeMcpConfig(
  configDir: string,
  opts: { agentMemberId: string; serverUrl: string; apiKey: string },
): string {
  fs.mkdirSync(configDir, { recursive: true });
  const p = path.join(configDir, 'coagentia-mcp.json');
  const payload = buildMcpConfig(opts);
  fs.writeFileSync(p, JSON.stringify(payload, null, 2), 'utf-8');
  return p;
}

/** claude CLI 命令行（§2）。resumeSessionId 给定 → 附 `--resume <id>`（会话续接）。 */
export function buildArgv(
  boot: AgentBoot,
  opts: { mcpConfigPath?: string | null; resumeSessionId?: string | null } = {},
): string[] {
  const argv: string[] = [
    CLAUDE_BIN,
    '--output-format',
    'stream-json',
    '--input-format',
    'stream-json',
    '--include-partial-messages',
    '--permission-mode',
    'bypassPermissions',
    '--verbose', // 本模式必需（E §11.2 实测确认）
    '--model',
    boot.model,
    '--append-system-prompt',
    buildIdentityPrompt(boot),
  ];
  if (opts.mcpConfigPath !== null && opts.mcpConfigPath !== undefined) {
    argv.push('--mcp-config', String(opts.mcpConfigPath), '--strict-mcp-config');
  }
  if (DISALLOWED_TOOLS.length > 0) {
    // --disallowed-tools <tools...> 变参：逐个 argv 元素（后接的 flag 终止收集）。
    argv.push('--disallowed-tools');
    argv.push(...DISALLOWED_TOOLS);
  }
  if (opts.resumeSessionId) {
    argv.push('--resume', opts.resumeSessionId);
  }
  return argv;
}
