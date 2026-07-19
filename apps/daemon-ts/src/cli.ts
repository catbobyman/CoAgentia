/**
 * CLI 入口（契约 D §2 daemon 主进程；契约 E §3 `mcp` 子命令；对等基准 = apps/daemon cli.py）。
 *
 * - `node src/cli.ts --server-url <url> --api-key <key>`：daemon 主循环。
 * - `node src/cli.ts mcp --agent-member <id> --server-url <url> --api-key <key>`：
 *   coagentia stdio MCP server（由 claude 子进程经 --mcp-config 拉起，E §3）。
 *
 * py 的 win32 Proactor loop 策略在 node 无对应物（child_process 天然可用）。
 * SIGINT：py 靠 KeyboardInterrupt 打断 asyncio.run；TS 挂 SIGINT → client.stop()
 * （stop 关传输促 reader 终结）→ run 返回 → finally shutdown（登记差异，语义等价）。
 */

import { RuntimeManager } from './adapters/claude_code.ts';
import { TelemetryBuffer } from './buffer.ts';
import { DaemonClient } from './client.ts';
import { setupFileLogging, getLogger } from './logconfig.ts';
import { DataPaths } from './paths.ts';
import { DAEMON_VERSION } from './version.ts';
import * as os from 'node:os';

interface Args {
  command: 'daemon' | 'mcp';
  serverUrl: string | null;
  apiKey: string | null;
  dataRoot: string | null;
  agentMember: string | null;
}

function parseArgs(argv: string[]): Args {
  const args: Args = { command: 'daemon', serverUrl: null, apiKey: null, dataRoot: null, agentMember: null };
  let i = 0;
  if (argv[0] === 'mcp') {
    args.command = 'mcp';
    i = 1;
  }
  for (; i < argv.length; i += 1) {
    const a = argv[i]!;
    const next = () => {
      i += 1;
      const v = argv[i];
      if (v === undefined) throw new Error(`${a} 缺参数值`);
      return v;
    };
    if (a === '--server-url') args.serverUrl = next();
    else if (a === '--api-key') args.apiKey = next();
    else if (a === '--data-root') args.dataRoot = next();
    else if (a === '--agent-member') args.agentMember = next();
    else if (a === '--version') {
      process.stdout.write(`coagentia-daemon ${DAEMON_VERSION}\n`);
      process.exit(0);
    } else throw new Error(`未知参数: ${a}`);
  }
  return args;
}

export function buildClient(serverUrl: string, apiKey: string, dataRoot?: string | null): DaemonClient {
  const paths = new DataPaths(dataRoot ?? undefined);
  paths.ensureDirs();
  const buffer = new TelemetryBuffer(paths);
  // runtime 管理器按 boot.runtime 分派 claude / codex 进程类（契约 E2）。
  const adapter = new RuntimeManager(paths, { serverUrl, apiKey });
  return new DaemonClient({
    serverUrl,
    apiKey,
    adapter,
    buffer,
    paths,
    osName: `${os.type()} ${os.release()}`,
    arch: os.arch(),
  });
}

export async function main(argv: string[]): Promise<number> {
  const args = parseArgs(argv);
  if (args.command === 'mcp') {
    if (!args.agentMember || !args.serverUrl || !args.apiKey) {
      throw new Error('mcp 子命令须带 --agent-member/--server-url/--api-key');
    }
    const mcp = await import('./adapters/mcp.ts');
    return mcp.run(args.agentMember, args.serverUrl, args.apiKey);
  }
  if (!args.serverUrl || !args.apiKey) {
    process.stderr.write('--server-url 与 --api-key 必填\n');
    return 2;
  }
  // daemon 主进程文件日志装配（B-4 可观测性；mcp 子进程路径已在上面 return，不装配）。
  const paths = new DataPaths(args.dataRoot ?? undefined);
  paths.ensureDirs();
  setupFileLogging(paths);
  getLogger('coagentia_daemon.cli').info(
    `daemon starting: version=${DAEMON_VERSION} server_url=${args.serverUrl} data_root=${args.dataRoot ?? '(default)'}`,
  );
  const client = buildClient(args.serverUrl, args.apiKey, args.dataRoot);
  process.on('SIGINT', () => client.stop());
  try {
    await client.run();
  } finally {
    await client.shutdown();
  }
  return 0;
}

// 直跑判定按 URL 尾段对齐（win32 盘符大小写/分隔符差异容忍）；vitest/import 消费不触发。
const argv1 = process.argv[1];
const isDirectRun =
  argv1 !== undefined &&
  import.meta.url.toLowerCase().endsWith(argv1.replace(/\\/g, '/').toLowerCase().replace(/^.*\//, '/'));
if (isDirectRun) {
  main(process.argv.slice(2)).then(
    (code) => process.exit(code),
    (err: unknown) => {
      process.stderr.write(`${err instanceof Error ? err.message : String(err)}\n`);
      process.exit(1);
    },
  );
}
