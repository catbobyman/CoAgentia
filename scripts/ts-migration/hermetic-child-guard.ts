import { createRequire, syncBuiltinESMExports } from 'node:module';
import * as path from 'node:path';

const require = createRequire(import.meta.url);
const childProcess = require('node:child_process') as typeof import('node:child_process');
const mutableChildProcess = childProcess as unknown as Record<string, unknown>;
const workerThreads = require('node:worker_threads') as typeof import('node:worker_threads');
const mutableWorkerThreads = workerThreads as unknown as Record<string, unknown>;

const FORBIDDEN_TOOL_RE = /(?:^|[\s"'`=;|&()\\/])(?:python(?:3(?:\.\d+)*)?|py|uv|pip3?|node-gyp|git)(?:\.exe|\.cmd|\.bat|\.com)?(?=$|[\s"'`;|&()\\/])/iu;
const FORBIDDEN_BASENAME_RE = /^(?:python(?:3(?:\.\d+)*)?|py|uv|pip3?|node-gyp|git)(?:\.exe|\.cmd|\.bat|\.com)?$/iu;
const NODE_BASENAME_RE = /^node(?:\.exe|\.cmd|\.bat|\.com)?$/iu;
const SHELL_BASENAME_RE = /^(?:cmd|powershell|pwsh|bash|sh)(?:\.exe|\.cmd|\.bat|\.com)?$/iu;
const DYNAMIC_SHELL_RE = /(?:%[^%\r\n]+%|\$env:[$\w]+|\$\{?[A-Za-z_]\w*\}?|\$\(|`)/iu;
const GUARD_IMPORT_ARGUMENT = `--import=${import.meta.url}`;

function sameExecutable(left: string, right: string): boolean {
  const normalize = (value: string): string => path.resolve(value).replaceAll('\\', '/').toLowerCase();
  return normalize(left) === normalize(right);
}

function isNodeExecutable(command: string): boolean {
  return sameExecutable(command, process.execPath) || NODE_BASENAME_RE.test(path.basename(command));
}

function firstShellExecutable(command: string): string | null {
  const trimmed = command.trimStart();
  const match = /^(?:"([^"]+)"|'([^']+)'|([^\s]+))/u.exec(trimmed);
  return match?.[1] ?? match?.[2] ?? match?.[3] ?? null;
}

function shellStartsWithNode(command: string): boolean {
  const executable = firstShellExecutable(command);
  return executable !== null && isNodeExecutable(executable);
}

function withGuardNodeOptions(environment: NodeJS.ProcessEnv | undefined): NodeJS.ProcessEnv {
  const result = { ...(environment ?? process.env) };
  const existing = result.NODE_OPTIONS?.trim() ?? '';
  if (!existing.includes(GUARD_IMPORT_ARGUMENT)) {
    result.NODE_OPTIONS = `${existing} ${GUARD_IMPORT_ARGUMENT}`.trim();
  }
  result.COAGENTIA_HERMETIC_CORE = '1';
  return result;
}

function withGuardExecArgv(execArgv: readonly string[] | undefined): string[] {
  const result = [...(execArgv ?? process.execArgv)];
  if (!result.includes(GUARD_IMPORT_ARGUMENT)) result.push(GUARD_IMPORT_ARGUMENT);
  return result;
}

function commandOptionsIndex(args: readonly unknown[]): number {
  return Array.isArray(args[1]) ? 2 : 1;
}

function forceNodeGuard(args: unknown[], optionsIndex: number): void {
  const current = args[optionsIndex];
  const options = current !== null && typeof current === 'object'
    ? current as Record<string, unknown>
    : {};
  const environment = options.env !== null && typeof options.env === 'object'
    ? options.env as NodeJS.ProcessEnv
    : undefined;
  args[optionsIndex] = { ...options, env: withGuardNodeOptions(environment) };
}

function denyForbiddenTool(command: unknown, args: readonly unknown[] = []): void {
  if (typeof command !== 'string' || command.length === 0) return;
  if (isNodeExecutable(command)) return;
  const basename = path.basename(command);
  const shellCommand = command.includes(' ') || SHELL_BASENAME_RE.test(basename);
  const detail = [command, ...args.filter((value): value is string => typeof value === 'string')].join(' ');
  if (FORBIDDEN_BASENAME_RE.test(basename) || (shellCommand && FORBIDDEN_TOOL_RE.test(detail))) {
    throw new Error(`COAGENTIA_HERMETIC_PROCESS_DENY: ${detail}`);
  }
  if (
    (SHELL_BASENAME_RE.test(basename) || command.includes(' '))
    && DYNAMIC_SHELL_RE.test(detail)
    && !shellStartsWithNode(command)
  ) {
    throw new Error(`COAGENTIA_HERMETIC_PROCESS_DENY: dynamic shell command: ${detail}`);
  }
}

function wrapCommand(name: 'spawn' | 'spawnSync' | 'execFile' | 'execFileSync'): void {
  const original = childProcess[name] as unknown as (...args: unknown[]) => unknown;
  mutableChildProcess[name] = function guardedCommand(...args: unknown[]): unknown {
    const argv = Array.isArray(args[1]) ? args[1] : [];
    denyForbiddenTool(args[0], argv);
    const command = args[0];
    const optionsIndex = commandOptionsIndex(args);
    const options = args[optionsIndex];
    const usesShell = options !== null && typeof options === 'object'
      && Boolean((options as Record<string, unknown>).shell);
    const launchesNode = typeof command === 'string'
      && (isNodeExecutable(command) || (usesShell && shellStartsWithNode(command)));
    if (launchesNode) forceNodeGuard(args, optionsIndex);
    return Reflect.apply(original, childProcess, args);
  };
}

function wrapShell(name: 'exec' | 'execSync'): void {
  const original = childProcess[name] as unknown as (...args: unknown[]) => unknown;
  mutableChildProcess[name] = function guardedShell(...args: unknown[]): unknown {
    denyForbiddenTool(args[0]);
    if (typeof args[0] === 'string' && shellStartsWithNode(args[0])) {
      if (name === 'exec' && typeof args[1] === 'function') args.splice(1, 0, { env: withGuardNodeOptions(undefined) });
      else forceNodeGuard(args, 1);
    }
    return Reflect.apply(original, childProcess, args);
  };
}

for (const name of ['spawn', 'spawnSync', 'execFile', 'execFileSync'] as const) wrapCommand(name);
for (const name of ['exec', 'execSync'] as const) wrapShell(name);

const originalFork = childProcess.fork as unknown as (...args: unknown[]) => unknown;
mutableChildProcess.fork = function guardedFork(...args: unknown[]): unknown {
  const optionsIndex = Array.isArray(args[1]) ? 2 : 1;
  const current = args[optionsIndex];
  const options = current !== null && typeof current === 'object'
    ? current as Record<string, unknown>
    : {};
  if (typeof options?.execPath === 'string') denyForbiddenTool(options.execPath);
  const environment = options.env !== null && typeof options.env === 'object'
    ? options.env as NodeJS.ProcessEnv
    : undefined;
  args[optionsIndex] = {
    ...options,
    env: withGuardNodeOptions(environment),
    execArgv: withGuardExecArgv(Array.isArray(options.execArgv) ? options.execArgv as string[] : undefined),
  };
  return Reflect.apply(originalFork, childProcess, args);
};

const OriginalWorker = workerThreads.Worker;
mutableWorkerThreads.Worker = new Proxy(OriginalWorker, {
  construct() {
    throw new Error('COAGENTIA_HERMETIC_PROCESS_DENY: worker_threads disabled in hermetic core');
  },
});

syncBuiltinESMExports();
