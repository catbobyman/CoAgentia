import { spawnSync } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';

import {
  assertRepositoryWorktreeClean,
  compareText,
  hashStringList,
  isNonEmptyString,
  isRecord,
  sortedRecord,
  validationResult,
  verifyP0EvidenceOnlyRepositoryState,
  type ValidationIssue,
  type ValidationResult,
} from './shared.ts';

export const ORACLE_KIND = 'coagentia.pytest-oracle-collection';
export const ORACLE_SCHEMA_VERSION = 1;
export const ORACLE_COLLECT_COMMAND = 'uv run pytest --collect-only -q';
export const ORACLE_SOURCE_SCOPE = [
  'apps/server',
  'apps/daemon',
  'apps/mock-server',
  'packages/contracts',
  'pyproject.toml',
  'uv.lock',
] as const;

const ORACLE_ENVIRONMENT_POLICY = 'allowlist-v1';
const ORACLE_FIXED_CHILD_ENVIRONMENT = {
  FORCE_COLOR: '0',
  NO_COLOR: '1',
  PYTHONDONTWRITEBYTECODE: '1',
  PYTHONHASHSEED: '0',
  PYTHONIOENCODING: 'utf-8',
  PYTHONNOUSERSITE: '1',
  PYTHONUTF8: '1',
  UV_NO_CONFIG: '1',
} as const;
const ORACLE_COMMON_INHERITED_ENVIRONMENT = [
  'PATH',
  'TEMP',
  'TMP',
] as const;
const ORACLE_WINDOWS_INHERITED_ENVIRONMENT = [
  'APPDATA',
  'COMSPEC',
  'LOCALAPPDATA',
  'PATHEXT',
  'PROGRAMDATA',
  'SystemDrive',
  'SystemRoot',
  'USERPROFILE',
  'WINDIR',
] as const;
const ORACLE_POSIX_INHERITED_ENVIRONMENT = [
  'HOME',
  'LANG',
  'LC_ALL',
  'LC_CTYPE',
  'TMPDIR',
] as const;
const ORACLE_DROPPED_COLLECTION_ENVIRONMENT = [
  'COVERAGE_FILE',
  'COVERAGE_PROCESS_START',
  'COVERAGE_RCFILE',
  'PYTEST_ADDOPTS',
  'PYTEST_CURRENT_TEST',
  'PYTEST_PLUGINS',
  'PYTHONBREAKPOINT',
  'PYTHONHOME',
  'PYTHONINSPECT',
  'PYTHONPATH',
  'PYTHONSTARTUP',
  'PYTHONWARNINGS',
  'UV_CONFIG_FILE',
  'UV_NO_SYNC',
  'UV_PROJECT',
  'UV_PROJECT_ENVIRONMENT',
  'UV_PYTHON',
  'VIRTUAL_ENV',
] as const;

export interface ScopedSourceState {
  scopes: string[];
  tracked_entry_count: number;
  tracked_entries_sha256: string;
  worktree_status: 'clean';
  worktree_status_sha256: string;
}

export interface RepositoryProvenance {
  baseline_tree_sha: string;
  source_state: ScopedSourceState;
  source_fingerprint_sha256: string;
}

export interface OracleToolchainVersions {
  uv: string;
  python: string;
  pytest: string;
}

export interface OracleExecutionMetadata {
  environment: Record<string, string>;
  toolchain: OracleToolchainVersions;
}

export interface OracleCollectionRun extends OracleExecutionMetadata {
  output: string;
}

export interface OracleCollectionManifest {
  schema_version: 1;
  kind: typeof ORACLE_KIND;
  baseline_sha: string;
  baseline_tree_sha?: string;
  source_state?: ScopedSourceState;
  source_fingerprint_sha256?: string;
  collect_command: string;
  environment: Record<string, string>;
  toolchain?: OracleToolchainVersions;
  nodeid_count: number;
  nodeids_sha256: string;
  nodeids: string[];
}

export interface BuildOracleOptions {
  baselineSha: string;
  collectCommand: string;
  environment: Record<string, string>;
  toolchain?: OracleToolchainVersions;
}

const ANSI_RE = /\u001b\[[0-?]*[ -/]*[@-~]/gu;
const NODEID_RE = /^(.+?\.py)::(.+)$/u;
const NODEID_NON_COLLECTION_SUFFIX_RE = /^.+?\.py::.+?(?:\s+(?:PASSED|FAILED|SKIPPED|XFAIL|XPASS|ERROR)(?:\s+\[\s*\d+%\s*\])?|\s+\[\s*\d+%\s*\])(?:\s+.*)?$/iu;
const GIT_SHA_RE = /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/iu;
const PYTEST_COLLECT_COMMAND_RE = /^(?:uv\s+run\s+)?(?:pytest(?:\.exe)?|python(?:3(?:\.\d+)*)?(?:\.exe)?\s+-m\s+pytest)(?:\s|$)/iu;
const COLLECT_ONLY_FLAG_RE = /(?:^|\s)--collect-only(?:\s|$)/u;
const COLLECTION_FAILURE_RE = /(?:^|[=\s])(?:FAILED|ERROR)(?:\s+collecting\b|\s+\S+\.py(?:\s|::|-|$))/iu;
const COLLECTION_SUMMARY_RE = /^(?:=+\s*)?(\d+)\s+tests?\s+collected\s+in\s+\d+(?:\.\d+)?s(?:\s+=+)?$/iu;
const SHA256_RE = /^[0-9a-f]{64}$/u;

function inheritedEnvironmentNames(platform: NodeJS.Platform): readonly string[] {
  return platform === 'win32'
    ? [...ORACLE_COMMON_INHERITED_ENVIRONMENT, ...ORACLE_WINDOWS_INHERITED_ENVIRONMENT]
    : [...ORACLE_COMMON_INHERITED_ENVIRONMENT, ...ORACLE_POSIX_INHERITED_ENVIRONMENT];
}

function sourceEnvironmentValue(
  source: Readonly<NodeJS.ProcessEnv>,
  name: string,
  platform: NodeJS.Platform,
): string | undefined {
  if (platform !== 'win32') return source[name];
  const normalizedName = name.toLowerCase();
  const match = Object.keys(source).find((key) => key.toLowerCase() === normalizedName);
  return match === undefined ? undefined : source[match];
}

/**
 * Construct the complete child environment from a narrow system-key allowlist.
 * Ambient Python, pytest, coverage, uv and virtualenv controls are never copied.
 */
export function oracleChildEnvironment(
  source: Readonly<NodeJS.ProcessEnv> = process.env,
  platform: NodeJS.Platform = process.platform,
): NodeJS.ProcessEnv {
  const environment: NodeJS.ProcessEnv = {};
  for (const name of inheritedEnvironmentNames(platform)) {
    const value = sourceEnvironmentValue(source, name, platform);
    if (value !== undefined) environment[name] = value;
  }
  Object.assign(environment, ORACLE_FIXED_CHILD_ENVIRONMENT);
  return environment;
}

/**
 * Public, non-secret description of the exact environment policy used for collection.
 * Runtime path/temp values are intentionally represented by their key names only.
 */
export function oracleFixedEnvironment(
  source: Readonly<NodeJS.ProcessEnv> = process.env,
  platform: NodeJS.Platform = process.platform,
): Record<string, string> {
  const inherited = inheritedEnvironmentNames(platform)
    .filter((name) => sourceEnvironmentValue(source, name, platform) !== undefined)
    .sort(compareText);
  return sortedRecord({
    ...ORACLE_FIXED_CHILD_ENVIRONMENT,
    ambient_environment: 'drop-all-except-listed',
    inherited_environment_keys: inherited.join(','),
    node: process.version,
    platform,
    policy: ORACLE_ENVIRONMENT_POLICY,
    removed_collection_controls: [...ORACLE_DROPPED_COLLECTION_ENVIRONMENT].sort(compareText).join(','),
  });
}

export interface PytestCommandOptions {
  cwd: string;
  env: NodeJS.ProcessEnv;
}

export interface PytestCommandResult {
  status: number | null;
  stdout: string;
  stderr: string;
  error?: Error;
}

export type PytestCommandRunner = (
  command: string,
  args: readonly string[],
  options: PytestCommandOptions,
) => PytestCommandResult;

const ORACLE_UV_VERSION_ARGS = ['--version'] as const;
const ORACLE_PYTHON_VERSION_ARGS = ['run', 'python', '--version'] as const;
const ORACLE_PYTEST_VERSION_ARGS = ['run', 'pytest', '--version'] as const;
const VERSION_LINE_RE = {
  uv: /^uv\s+\d+(?:\.\d+){1,3}(?:[-+._a-z0-9]*)?(?:\s+\([^\r\n]+\))?$/iu,
  python: /^Python\s+\d+(?:\.\d+){1,3}(?:[-+._a-z0-9]*)?$/iu,
  pytest: /^pytest\s+\d+(?:\.\d+){1,3}(?:[-+._a-z0-9]*)?$/iu,
} as const;

function runGitRaw(repo: string, args: readonly string[]): string {
  const result = spawnSync('git', ['-C', path.resolve(repo), ...args], {
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
    shell: false,
    windowsHide: true,
  });
  if (result.error !== undefined) throw result.error;
  if (result.status !== 0) {
    throw new Error(`git ${args.join(' ')} failed (${String(result.status)}): ${(result.stderr ?? '').trim()}`);
  }
  return result.stdout;
}

function runGit(repo: string, args: readonly string[]): string {
  return runGitRaw(repo, args).trim();
}

function normalizeScopes(scopes: readonly string[]): string[] {
  const normalized = scopes.map((item) => item.replaceAll('\\', '/').replace(/^\.\//u, ''));
  if (normalized.length === 0 || normalized.some((item) => item.length === 0 || item.startsWith('../') || path.isAbsolute(item))) {
    throw new Error('source scope must contain repository-relative paths');
  }
  return [...new Set(normalized)].sort(compareText);
}

function scopedTreeEntries(repoRoot: string, ref: string, scopes: readonly string[]): string[] {
  const output = runGitRaw(repoRoot, ['ls-tree', '-r', '-z', '--full-tree', ref, '--', ...scopes]);
  return output.split('\0').filter((item) => item.length > 0).sort(compareText);
}

function sourceFingerprint(state: ScopedSourceState): string {
  return hashStringList([
    ...state.scopes.map((scope) => `scope:${scope}`),
    `tracked_entry_count:${state.tracked_entry_count}`,
    `tracked_entries_sha256:${state.tracked_entries_sha256}`,
    `worktree_status:${state.worktree_status}`,
    `worktree_status_sha256:${state.worktree_status_sha256}`,
  ]);
}

function sourceStateAtRef(repoRoot: string, ref: string, scopes: readonly string[]): ScopedSourceState {
  const entries = scopedTreeEntries(repoRoot, ref, scopes);
  if (entries.length === 0) throw new Error(`source scope is empty at ${ref}`);
  return {
    scopes: [...scopes],
    tracked_entry_count: entries.length,
    tracked_entries_sha256: hashStringList(entries),
    worktree_status: 'clean',
    worktree_status_sha256: hashStringList([]),
  };
}

export function captureScopedRepositoryProvenance(
  repo: string,
  baselineSha: string,
  scopes: readonly string[],
  requireBaselineAtHead = true,
): RepositoryProvenance {
  const repoRoot = path.resolve(runGit(repo, ['rev-parse', '--show-toplevel']));
  const baseline = resolveOracleBaseline(repoRoot, baselineSha);
  const head = runGit(repoRoot, ['rev-parse', '--verify', 'HEAD^{commit}']).toLowerCase();
  if (requireBaselineAtHead && baseline !== head) {
    throw new Error(`baseline_sha must equal current HEAD: ${baseline} != ${head}`);
  }
  if (requireBaselineAtHead) assertRepositoryWorktreeClean(repoRoot);
  const normalizedScopes = normalizeScopes(scopes);
  const baselineTreeSha = runGit(repoRoot, ['rev-parse', '--verify', `${baseline}^{tree}`]).toLowerCase();
  if (!GIT_SHA_RE.test(baselineTreeSha)) throw new Error(`invalid baseline tree SHA: ${baselineTreeSha}`);

  const baselineState = sourceStateAtRef(repoRoot, baseline, normalizedScopes);
  const currentState = sourceStateAtRef(repoRoot, 'HEAD', normalizedScopes);
  if (
    baselineState.tracked_entry_count !== currentState.tracked_entry_count
    || baselineState.tracked_entries_sha256 !== currentState.tracked_entries_sha256
  ) {
    throw new Error('tracked source scope has drifted from the frozen baseline');
  }
  const status = runGitRaw(repoRoot, [
    'status', '--porcelain=v1', '-z', '--untracked-files=all', '--', ...normalizedScopes,
  ]);
  if (status.length > 0) throw new Error('tracked or untracked source-scope worktree drift detected');

  return {
    baseline_tree_sha: baselineTreeSha,
    source_state: baselineState,
    source_fingerprint_sha256: sourceFingerprint(baselineState),
  };
}

export function validateRepositoryProvenanceFields(
  value: unknown,
  expectedScopes: readonly string[],
  codePrefix: string,
): ValidationResult {
  const issues: ValidationIssue[] = [];
  if (!isRecord(value)) {
    return validationResult([{ code: `${codePrefix}_provenance_not_object`, message: 'manifest must be an object' }]);
  }
  if (typeof value.baseline_tree_sha !== 'string' || !GIT_SHA_RE.test(value.baseline_tree_sha)) {
    issues.push({ code: `${codePrefix}_baseline_tree_sha`, message: 'baseline_tree_sha must be a full Git tree SHA' });
  }
  if (!isRecord(value.source_state)) {
    issues.push({ code: `${codePrefix}_source_state`, message: 'source_state is required' });
  } else {
    const state = value.source_state;
    const scopes = normalizeScopes(expectedScopes);
    if (!Array.isArray(state.scopes) || state.scopes.some((item) => !isNonEmptyString(item))) {
      issues.push({ code: `${codePrefix}_source_scopes`, message: 'source_state.scopes must be a string array' });
    } else if (
      state.scopes.length !== scopes.length
      || state.scopes.some((item, index) => item !== scopes[index])
    ) {
      issues.push({ code: `${codePrefix}_source_scopes`, message: 'source_state.scopes do not match the controlled scope' });
    }
    if (!Number.isInteger(state.tracked_entry_count) || (state.tracked_entry_count as number) <= 0) {
      issues.push({ code: `${codePrefix}_source_entry_count`, message: 'tracked_entry_count must be a positive integer' });
    }
    if (typeof state.tracked_entries_sha256 !== 'string' || !SHA256_RE.test(state.tracked_entries_sha256)) {
      issues.push({ code: `${codePrefix}_source_entries_hash`, message: 'tracked_entries_sha256 must be SHA-256' });
    }
    if (state.worktree_status !== 'clean' || state.worktree_status_sha256 !== hashStringList([])) {
      issues.push({ code: `${codePrefix}_source_worktree`, message: 'source worktree provenance must record the canonical clean state' });
    }
    if (issues.length === 0 && value.source_fingerprint_sha256 !== sourceFingerprint(state as unknown as ScopedSourceState)) {
      issues.push({ code: `${codePrefix}_source_fingerprint`, message: 'source_fingerprint_sha256 does not match source_state' });
    }
  }
  if (typeof value.source_fingerprint_sha256 !== 'string' || !SHA256_RE.test(value.source_fingerprint_sha256)) {
    issues.push({ code: `${codePrefix}_source_fingerprint`, message: 'source_fingerprint_sha256 must be SHA-256' });
  }
  return validationResult(issues);
}

export function verifyScopedRepositoryProvenance(
  repo: string,
  value: unknown,
  expectedScopes: readonly string[],
  codePrefix: string,
): ValidationResult {
  const structural = validateRepositoryProvenanceFields(value, expectedScopes, codePrefix);
  if (!structural.ok || !isRecord(value) || !isNonEmptyString(value.baseline_sha)) return structural;
  try {
    const current = captureScopedRepositoryProvenance(repo, value.baseline_sha, expectedScopes, false);
    const history = verifyP0EvidenceOnlyRepositoryState(repo, value.baseline_sha, codePrefix);
    const issues: ValidationIssue[] = [...history.issues];
    if (value.baseline_tree_sha !== current.baseline_tree_sha) {
      issues.push({ code: `${codePrefix}_baseline_tree_drift`, message: 'baseline_tree_sha does not match baseline_sha' });
    }
    if (value.source_fingerprint_sha256 !== current.source_fingerprint_sha256) {
      issues.push({ code: `${codePrefix}_source_drift`, message: 'controlled source scope has drifted from the frozen baseline' });
    }
    return validationResult(issues);
  } catch (error: unknown) {
    return validationResult([{
      code: `${codePrefix}_repository_state`,
      message: error instanceof Error ? error.message : String(error),
    }]);
  }
}

export function resolveOracleBaseline(repo: string, baselineSha: string): string {
  const candidate = baselineSha.trim();
  if (candidate === 'HEAD') {
    const head = runGit(repo, ['rev-parse', '--verify', 'HEAD^{commit}']).toLowerCase();
    if (!GIT_SHA_RE.test(head)) throw new Error(`HEAD did not resolve to a full Git commit SHA: ${head}`);
    return head;
  }
  if (!GIT_SHA_RE.test(candidate)) {
    throw new Error('--baseline-sha 必须是 HEAD 或完整的 40/64 位十六进制 Git commit SHA');
  }
  const resolved = runGit(repo, ['rev-parse', '--verify', `${candidate}^{commit}`]).toLowerCase();
  if (!GIT_SHA_RE.test(resolved) || resolved !== candidate.toLowerCase()) {
    throw new Error(`baseline commit 必须解析为同一个完整 SHA: ${candidate} -> ${resolved}`);
  }
  return resolved;
}

const defaultPytestCommandRunner: PytestCommandRunner = (command, args, options) => {
  const result = spawnSync(command, [...args], {
    cwd: options.cwd,
    encoding: 'utf8',
    env: options.env,
    maxBuffer: 64 * 1024 * 1024,
    shell: false,
    windowsHide: true,
  });
  return {
    status: result.status,
    stdout: result.stdout ?? '',
    stderr: result.stderr ?? '',
    ...(result.error === undefined ? {} : { error: result.error }),
  };
};

function resolveRepositoryDirectory(repo: string): string {
  const cwd = path.resolve(repo);
  let stat: fs.Stats;
  try {
    stat = fs.statSync(cwd);
  } catch (error: unknown) {
    const detail = error instanceof Error ? error.message : String(error);
    throw new Error(`repository path is not readable: ${cwd}: ${detail}`);
  }
  if (!stat.isDirectory()) throw new Error(`repository path is not a directory: ${cwd}`);
  return cwd;
}

function checkedVersionProbe(
  name: keyof OracleToolchainVersions,
  cwd: string,
  args: readonly string[],
  environment: NodeJS.ProcessEnv,
  runner: PytestCommandRunner,
): string {
  const result = runner('uv', args, { cwd, env: environment });
  if (result.error !== undefined) throw new Error(`failed to start uv for ${name} version probe: ${result.error.message}`);
  if (result.status !== 0) {
    throw new Error(`${name} version probe exited ${String(result.status)}`);
  }
  const lines = [result.stdout, result.stderr]
    .join('\n')
    .replace(ANSI_RE, '')
    .split(/\r?\n/u)
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
  if (lines.length !== 1 || !VERSION_LINE_RE[name].test(lines[0]!)) {
    throw new Error(`${name} version probe returned an unexpected non-canonical result`);
  }
  return lines[0]!;
}

export function probeOracleToolchain(
  repo: string,
  runner: PytestCommandRunner = defaultPytestCommandRunner,
  sourceEnvironment: Readonly<NodeJS.ProcessEnv> = process.env,
): OracleToolchainVersions {
  const cwd = resolveRepositoryDirectory(repo);
  const environment = oracleChildEnvironment(sourceEnvironment);
  return {
    uv: checkedVersionProbe('uv', cwd, ORACLE_UV_VERSION_ARGS, environment, runner),
    python: checkedVersionProbe('python', cwd, ORACLE_PYTHON_VERSION_ARGS, environment, runner),
    pytest: checkedVersionProbe('pytest', cwd, ORACLE_PYTEST_VERSION_ARGS, environment, runner),
  };
}

function toolchainVersionsEqual(left: OracleToolchainVersions, right: OracleToolchainVersions): boolean {
  return left.uv === right.uv && left.python === right.python && left.pytest === right.pytest;
}

export function isExplicitPytestCollectCommand(value: string): boolean {
  const command = value.trim();
  return PYTEST_COLLECT_COMMAND_RE.test(command) && COLLECT_ONLY_FLAG_RE.test(command);
}

export function normalizeNodeId(value: string): string {
  const trimmed = value.replace(ANSI_RE, '').trim();
  const splitAt = trimmed.indexOf('::');
  if (splitAt < 0) return trimmed;
  const filePart = trimmed.slice(0, splitAt).replaceAll('\\', '/');
  return `${filePart}${trimmed.slice(splitAt)}`;
}

export function parsePytestCollectOutput(text: string): string[] {
  const nodeids: string[] = [];
  const seen = new Set<string>();
  const duplicates: string[] = [];
  let summaryCount: number | undefined;

  for (const rawLine of text.split(/\r?\n/u)) {
    const undecorated = rawLine.replace(ANSI_RE, '').trim();
    if (COLLECTION_FAILURE_RE.test(undecorated)) {
      throw new Error(`pytest collection reported failure: ${undecorated}`);
    }
    if (NODEID_NON_COLLECTION_SUFFIX_RE.test(undecorated)) {
      throw new Error(`pytest output contains a non-collection nodeid suffix: ${undecorated}`);
    }
    const summary = COLLECTION_SUMMARY_RE.exec(undecorated);
    if (summary !== null) {
      if (summaryCount !== undefined) throw new Error('pytest collection output contains multiple summary lines');
      summaryCount = Number.parseInt(summary[1]!, 10);
      continue;
    }

    const line = normalizeNodeId(undecorated);
    if (line.length === 0) continue;
    const match = NODEID_RE.exec(line);
    if (match === null || match[2]!.trim().length === 0) continue;
    if (seen.has(line)) duplicates.push(line);
    else {
      seen.add(line);
      nodeids.push(line);
    }
  }

  if (duplicates.length > 0) {
    throw new Error(`pytest collection 输出含重复 nodeid: ${duplicates.sort(compareText).join(', ')}`);
  }
  if (summaryCount === undefined) {
    throw new Error('pytest collection summary is missing or truncated');
  }
  if (nodeids.length === 0) {
    throw new Error('未从输入识别出 pytest nodeid；请使用 pytest --collect-only -q 输出');
  }
  if (summaryCount !== nodeids.length) {
    throw new Error(`pytest collection summary count ${summaryCount} does not match ${nodeids.length} parsed nodeids`);
  }
  return nodeids.sort(compareText);
}

export function runPytestCollection(
  repo: string,
  runner: PytestCommandRunner = defaultPytestCommandRunner,
  sourceEnvironment: Readonly<NodeJS.ProcessEnv> = process.env,
): string {
  const cwd = resolveRepositoryDirectory(repo);
  const args = ['run', 'pytest', '--collect-only', '-q'] as const;
  const result = runner('uv', args, {
    cwd,
    env: oracleChildEnvironment(sourceEnvironment),
  });
  if (result.error !== undefined) throw new Error(`failed to start uv: ${result.error.message}`);
  if (result.status !== 0) {
    const detail = [result.stderr.trim(), result.stdout.trim()].filter((item) => item.length > 0).join('\n');
    throw new Error(`uv run pytest --collect-only -q exited ${String(result.status)}${detail.length === 0 ? '' : `:\n${detail}`}`);
  }
  // Validate completeness here so every caller gets the same fail-closed behavior.
  parsePytestCollectOutput(result.stdout);
  return result.stdout;
}

export function runOracleCollection(
  repo: string,
  runner: PytestCommandRunner = defaultPytestCommandRunner,
  sourceEnvironment: Readonly<NodeJS.ProcessEnv> = process.env,
): OracleCollectionRun {
  const sourceSnapshot = { ...sourceEnvironment };
  const before = probeOracleToolchain(repo, runner, sourceSnapshot);
  const output = runPytestCollection(repo, runner, sourceSnapshot);
  const after = probeOracleToolchain(repo, runner, sourceSnapshot);
  if (!toolchainVersionsEqual(before, after)) {
    throw new Error('oracle toolchain versions changed during collection');
  }
  return {
    output,
    environment: oracleFixedEnvironment(sourceSnapshot),
    toolchain: before,
  };
}

export function buildOracleManifestForRepository(
  repo: string,
  text: string,
  baselineSha: string,
  execution: OracleExecutionMetadata,
): OracleCollectionManifest {
  const baseline = resolveOracleBaseline(repo, baselineSha);
  const provenance = captureScopedRepositoryProvenance(repo, baseline, ORACLE_SOURCE_SCOPE, true);
  return {
    ...buildOracleManifest(text, {
      baselineSha: baseline,
      collectCommand: ORACLE_COLLECT_COMMAND,
      environment: execution.environment,
      toolchain: execution.toolchain,
    }),
    ...provenance,
  };
}

export function buildOracleManifest(text: string, options: BuildOracleOptions): OracleCollectionManifest {
  if (!GIT_SHA_RE.test(options.baselineSha)) {
    throw new Error('--baseline-sha 必须是完整的 40 或 64 位十六进制 Git commit SHA');
  }
  if (!isNonEmptyString(options.collectCommand) || !isExplicitPytestCollectCommand(options.collectCommand)) {
    throw new Error('--command 必须是明确包含 --collect-only 的 pytest 命令');
  }
  const nodeids = parsePytestCollectOutput(text);
  return {
    schema_version: ORACLE_SCHEMA_VERSION,
    kind: ORACLE_KIND,
    baseline_sha: options.baselineSha.toLowerCase(),
    collect_command: options.collectCommand.trim(),
    environment: sortedRecord(options.environment),
    ...(options.toolchain === undefined ? {} : { toolchain: { ...options.toolchain } }),
    nodeid_count: nodeids.length,
    nodeids_sha256: hashStringList(nodeids),
    nodeids,
  };
}

function validateToolchainVersions(value: unknown, code: string): ValidationIssue[] {
  if (!isRecord(value)) {
    return [{ code, message: 'toolchain must record exact uv, Python and pytest versions' }];
  }
  const issues: ValidationIssue[] = [];
  for (const name of ['uv', 'python', 'pytest'] as const) {
    const version = value[name];
    if (typeof version !== 'string' || !VERSION_LINE_RE[name].test(version)) {
      issues.push({ code, message: `${name} version is missing or non-canonical`, subject: name });
    }
  }
  if (Object.keys(value).some((key) => key !== 'uv' && key !== 'python' && key !== 'pytest')) {
    issues.push({ code, message: 'toolchain contains an unknown field' });
  }
  return issues;
}

export function validateOracleManifest(value: unknown): ValidationResult {
  const issues: ValidationIssue[] = [];
  if (!isRecord(value)) {
    return validationResult([{ code: 'oracle_not_object', message: 'oracle manifest 必须是 JSON object' }]);
  }
  if (value.schema_version !== ORACLE_SCHEMA_VERSION) {
    issues.push({ code: 'oracle_schema_version', message: `schema_version 必须为 ${ORACLE_SCHEMA_VERSION}` });
  }
  if (value.kind !== ORACLE_KIND) {
    issues.push({ code: 'oracle_kind', message: `kind 必须为 ${ORACLE_KIND}` });
  }
  if (!isNonEmptyString(value.baseline_sha) || !GIT_SHA_RE.test(value.baseline_sha)) {
    issues.push({ code: 'oracle_baseline_sha', message: 'baseline_sha 必须是完整的 40 或 64 位十六进制 Git commit SHA' });
  }
  if (
    value.baseline_tree_sha !== undefined
    || value.source_state !== undefined
    || value.source_fingerprint_sha256 !== undefined
  ) {
    issues.push(...validateRepositoryProvenanceFields(value, ORACLE_SOURCE_SCOPE, 'oracle').issues);
  }
  if (!isNonEmptyString(value.collect_command) || !isExplicitPytestCollectCommand(value.collect_command)) {
    issues.push({ code: 'oracle_collect_command', message: 'collect_command 必须是明确包含 --collect-only 的 pytest 命令' });
  }
  if (!isRecord(value.environment)) {
    issues.push({ code: 'oracle_environment', message: 'environment 必须是 string map' });
  } else {
    for (const [key, item] of Object.entries(value.environment)) {
      if (!isNonEmptyString(key) || typeof item !== 'string') {
        issues.push({ code: 'oracle_environment', message: 'environment 必须是 string map', subject: key });
      }
    }
  }
  if (value.toolchain !== undefined) {
    issues.push(...validateToolchainVersions(value.toolchain, 'oracle_toolchain'));
  }
  if (!Array.isArray(value.nodeids) || value.nodeids.some((item) => !isNonEmptyString(item))) {
    issues.push({ code: 'oracle_nodeids', message: 'nodeids 必须是非空字符串数组' });
    return validationResult(issues);
  }

  const nodeids = value.nodeids as string[];
  if (nodeids.length === 0) {
    issues.push({ code: 'oracle_nodeids_empty', message: 'nodeids 不得为空' });
  }
  const normalized = nodeids.map(normalizeNodeId);
  const sorted = [...normalized].sort(compareText);
  if (normalized.some((item, index) => item !== nodeids[index])) {
    issues.push({ code: 'oracle_nodeid_not_normalized', message: 'nodeids 必须使用规范化正斜杠且无首尾空白' });
  }
  if (sorted.some((item, index) => item !== normalized[index])) {
    issues.push({ code: 'oracle_nodeids_not_sorted', message: 'nodeids 必须按稳定字典序排序' });
  }
  const counts = new Map<string, number>();
  for (const nodeid of normalized) counts.set(nodeid, (counts.get(nodeid) ?? 0) + 1);
  for (const [nodeid, count] of counts) {
    if (count > 1) issues.push({ code: 'oracle_duplicate_nodeid', message: `出现 ${count} 次`, subject: nodeid });
  }
  if (value.nodeid_count !== nodeids.length) {
    issues.push({ code: 'oracle_count_mismatch', message: `nodeid_count=${String(value.nodeid_count)}，实际=${nodeids.length}` });
  }
  const expectedHash = hashStringList(normalized);
  if (value.nodeids_sha256 !== expectedHash) {
    issues.push({ code: 'oracle_hash_mismatch', message: `nodeids_sha256 应为 ${expectedHash}` });
  }
  return validationResult(issues);
}

export function asOracleManifest(value: unknown): OracleCollectionManifest {
  const result = validateOracleManifest(value);
  if (!result.ok) {
    throw new Error(result.issues.map((item) => `${item.code}: ${item.message}`).join('; '));
  }
  return value as unknown as OracleCollectionManifest;
}

function stringRecordsEqual(left: unknown, right: Readonly<Record<string, string>>): boolean {
  if (!isRecord(left)) return false;
  const leftEntries = Object.entries(left).sort(([a], [b]) => compareText(a, b));
  const rightEntries = Object.entries(right).sort(([a], [b]) => compareText(a, b));
  return leftEntries.length === rightEntries.length
    && leftEntries.every(([key, value], index) => {
      const expected = rightEntries[index];
      return expected !== undefined && key === expected[0] && value === expected[1];
    });
}

export function verifyCurrentOracleCollection(
  oracleValue: unknown,
  collectOutput: string,
  execution?: OracleExecutionMetadata,
): ValidationResult {
  const issues: ValidationIssue[] = [];
  const manifestResult = validateOracleManifest(oracleValue);
  issues.push(...manifestResult.issues);
  if (isRecord(oracleValue)) {
    if (oracleValue.collect_command !== ORACLE_COLLECT_COMMAND) {
      issues.push({
        code: 'oracle_current_collect_command',
        message: `collect_command must be exactly ${ORACLE_COLLECT_COMMAND}`,
      });
    }
    const expectedEnvironment = execution?.environment ?? oracleFixedEnvironment();
    if (!stringRecordsEqual(oracleValue.environment, expectedEnvironment)) {
      issues.push({
        code: 'oracle_current_environment',
        message: 'recorded environment does not match the controlled collection environment',
      });
    }
    if (execution !== undefined) {
      if (!isRecord(oracleValue.toolchain)) {
        issues.push({
          code: 'oracle_current_toolchain',
          message: 'frozen manifest does not record the collection toolchain',
        });
      } else if (
        oracleValue.toolchain.uv !== execution.toolchain.uv
        || oracleValue.toolchain.python !== execution.toolchain.python
        || oracleValue.toolchain.pytest !== execution.toolchain.pytest
      ) {
        issues.push({
          code: 'oracle_current_toolchain_drift',
          message: 'current uv, Python or pytest version differs from the frozen manifest',
        });
      }
    }
  }

  let currentNodeids: string[];
  try {
    currentNodeids = parsePytestCollectOutput(collectOutput);
  } catch (error: unknown) {
    issues.push({
      code: 'oracle_current_collection_invalid',
      message: error instanceof Error ? error.message : String(error),
    });
    return validationResult(issues);
  }
  if (!manifestResult.ok) return validationResult(issues);

  const oracle = asOracleManifest(oracleValue);
  const currentHash = hashStringList(currentNodeids);
  if (currentNodeids.length !== oracle.nodeid_count) {
    issues.push({
      code: 'oracle_current_count_mismatch',
      message: `current collection has ${currentNodeids.length} nodeids; frozen manifest has ${oracle.nodeid_count}`,
    });
  }
  if (currentHash !== oracle.nodeids_sha256) {
    issues.push({
      code: 'oracle_current_hash_mismatch',
      message: `current collection hash ${currentHash} does not match frozen ${oracle.nodeids_sha256}`,
    });
  }

  const currentSet = new Set(currentNodeids);
  const frozenSet = new Set(oracle.nodeids);
  for (const nodeid of oracle.nodeids) {
    if (!currentSet.has(nodeid)) {
      issues.push({ code: 'oracle_current_missing_nodeid', message: 'frozen nodeid is absent from current collection', subject: nodeid });
    }
  }
  for (const nodeid of currentNodeids) {
    if (!frozenSet.has(nodeid)) {
      issues.push({ code: 'oracle_current_unexpected_nodeid', message: 'current collection has a nodeid absent from the frozen manifest', subject: nodeid });
    }
  }
  return validationResult(issues);
}

export function verifyCurrentOracleCollectionForRepository(
  repo: string,
  oracleValue: unknown,
  collectOutput: string,
  execution?: OracleExecutionMetadata,
): ValidationResult {
  const collection = verifyCurrentOracleCollection(oracleValue, collectOutput, execution);
  const provenance = verifyScopedRepositoryProvenance(repo, oracleValue, ORACLE_SOURCE_SCOPE, 'oracle');
  return validationResult([...collection.issues, ...provenance.issues]);
}

export function collectOracleManifestForRepository(
  repo: string,
  baselineSha: string,
  runner: PytestCommandRunner = defaultPytestCommandRunner,
  sourceEnvironment: Readonly<NodeJS.ProcessEnv> = process.env,
): OracleCollectionManifest {
  const execution = runOracleCollection(repo, runner, sourceEnvironment);
  return buildOracleManifestForRepository(repo, execution.output, baselineSha, execution);
}

export function verifyOracleCollectionForRepository(
  repo: string,
  oracleValue: unknown,
  runner: PytestCommandRunner = defaultPytestCommandRunner,
  sourceEnvironment: Readonly<NodeJS.ProcessEnv> = process.env,
): ValidationResult {
  const execution = runOracleCollection(repo, runner, sourceEnvironment);
  return verifyCurrentOracleCollectionForRepository(repo, oracleValue, execution.output, execution);
}

export function runOracleSyntheticMutants(): { ok: boolean; passed: number; total: number; failures: string[] } {
  const syntheticSourceEnvironment: NodeJS.ProcessEnv = {
    PATH: 'synthetic-system-path',
    TEMP: 'synthetic-temp',
    TMP: 'synthetic-temp',
    PYTEST_ADDOPTS: '-k ambient_filter',
    PYTHONPATH: 'ambient-python-path',
    COVERAGE_PROCESS_START: 'ambient-coverage-config',
    COAGENTIA_SYNTHETIC_SECRET: 'must-not-cross-process-boundary',
  };
  const syntheticExecution: OracleExecutionMetadata = {
    environment: oracleFixedEnvironment(syntheticSourceEnvironment),
    toolchain: {
      uv: 'uv 0.9.0',
      python: 'Python 3.14.0',
      pytest: 'pytest 8.4.2',
    },
  };
  const baseline = buildOracleManifest('apps/server/tests/test_alpha.py::test_one\n1 test collected in 0.01s\n', {
    baselineSha: '0123456789abcdef0123456789abcdef01234567',
    collectCommand: 'uv run pytest --collect-only -q',
    environment: syntheticExecution.environment,
    toolchain: syntheticExecution.toolchain,
  });
  const cases: Array<{ name: string; code: string; mutate: (manifest: OracleCollectionManifest) => void }> = [
    {
      name: 'empty nodeids', code: 'oracle_nodeids_empty', mutate: (manifest) => {
        manifest.nodeids = [];
        manifest.nodeid_count = 0;
        manifest.nodeids_sha256 = hashStringList([]);
      },
    },
    { name: 'invalid baseline sha', code: 'oracle_baseline_sha', mutate: (manifest) => { manifest.baseline_sha = 'not-a-sha'; } },
    {
      name: 'not collect-only', code: 'oracle_collect_command',
      mutate: (manifest) => { manifest.collect_command = 'uv run pytest -q'; },
    },
    {
      name: 'pytest words hidden behind shell command', code: 'oracle_collect_command',
      mutate: (manifest) => { manifest.collect_command = 'echo pytest --collect-only -q'; },
    },
  ];
  let passed = 0;
  const failures: string[] = [];
  for (const item of cases) {
    const manifest = structuredClone(baseline);
    item.mutate(manifest);
    const result = validateOracleManifest(manifest);
    if (result.issues.some((issue) => issue.code === item.code)) passed += 1;
    else failures.push(`${item.name}: expected ${item.code}, got ${result.issues.map((issue) => issue.code).join(', ') || 'PASS'}`);
  }

  const invalidOutputs: Array<{ name: string; output: string; expected: RegExp }> = [
    {
      name: 'missing summary',
      output: 'apps/server/tests/test_alpha.py::test_one\n',
      expected: /summary is missing or truncated/u,
    },
    {
      name: 'truncated summary',
      output: 'apps/server/tests/test_alpha.py::test_one\n1 test collected in\n',
      expected: /summary is missing or truncated/u,
    },
    {
      name: 'summary count mismatch',
      output: 'apps/server/tests/test_alpha.py::test_one\n2 tests collected in 0.01s\n',
      expected: /does not match/u,
    },
    {
      name: 'ERROR collecting',
      output: 'ERROR collecting apps/server/tests/test_alpha.py\n1 test collected in 0.01s\n',
      expected: /reported failure/u,
    },
    {
      name: 'FAILED collecting',
      output: 'FAILED collecting apps/server/tests/test_alpha.py\n1 test collected in 0.01s\n',
      expected: /reported failure/u,
    },
    {
      name: 'test execution result suffix',
      output: 'apps/server/tests/test_alpha.py::test_one FAILED [100%]\n1 test collected in 0.01s\n',
      expected: /non-collection nodeid suffix/u,
    },
    {
      name: 'test execution progress suffix',
      output: 'apps/server/tests/test_alpha.py::test_one [100%]\n1 test collected in 0.01s\n',
      expected: /non-collection nodeid suffix/u,
    },
  ];
  for (const item of invalidOutputs) {
    try {
      parsePytestCollectOutput(item.output);
      failures.push(`${item.name}: invalid collection output was accepted`);
    } catch (error: unknown) {
      const detail = error instanceof Error ? error.message : String(error);
      if (item.expected.test(detail)) passed += 1;
      else failures.push(`${item.name}: unexpected error: ${detail}`);
    }
  }

  const drift = verifyCurrentOracleCollection(
    baseline,
    'apps/server/tests/test_alpha.py::test_changed\n1 test collected in 0.01s\n',
  );
  if (
    drift.issues.some((issue) => issue.code === 'oracle_current_missing_nodeid')
    && drift.issues.some((issue) => issue.code === 'oracle_current_unexpected_nodeid')
  ) passed += 1;
  else failures.push(`current collection drift: got ${drift.issues.map((issue) => issue.code).join(', ') || 'PASS'}`);

  let observedChildEnvironment: NodeJS.ProcessEnv | undefined;
  try {
    runPytestCollection(process.cwd(), (_command, _args, options) => {
      observedChildEnvironment = options.env;
      return {
        status: 0,
        stdout: 'apps/server/tests/test_alpha.py::test_one\n1 test collected in 0.01s\n',
        stderr: '',
      };
    }, syntheticSourceEnvironment);
    const childKeys = Object.keys(observedChildEnvironment ?? {}).map((key) => key.toUpperCase());
    const forbidden = [
      'PYTEST_ADDOPTS',
      'PYTHONPATH',
      'COVERAGE_PROCESS_START',
      'COAGENTIA_SYNTHETIC_SECRET',
    ];
    if (
      forbidden.every((key) => !childKeys.includes(key))
      && observedChildEnvironment?.PYTHONNOUSERSITE === '1'
      && observedChildEnvironment.PYTHONIOENCODING === 'utf-8'
    ) passed += 1;
    else failures.push('controlled child environment: ambient collection controls or secret values crossed the boundary');
  } catch (error: unknown) {
    failures.push(`controlled child environment: ${error instanceof Error ? error.message : String(error)}`);
  }

  const versionDrift = verifyCurrentOracleCollection(
    baseline,
    'apps/server/tests/test_alpha.py::test_one\n1 test collected in 0.01s\n',
    {
      ...syntheticExecution,
      toolchain: { ...syntheticExecution.toolchain, pytest: 'pytest 9.0.0' },
    },
  );
  if (versionDrift.issues.some((issue) => issue.code === 'oracle_current_toolchain_drift')) passed += 1;
  else failures.push(`toolchain version drift: got ${versionDrift.issues.map((issue) => issue.code).join(', ') || 'PASS'}`);

  const environmentDrift = verifyCurrentOracleCollection(
    baseline,
    'apps/server/tests/test_alpha.py::test_one\n1 test collected in 0.01s\n',
    {
      ...syntheticExecution,
      environment: { ...syntheticExecution.environment, policy: 'allow-all-v1' },
    },
  );
  if (environmentDrift.issues.some((issue) => issue.code === 'oracle_current_environment')) passed += 1;
  else failures.push(`controlled environment drift: got ${environmentDrift.issues.map((issue) => issue.code).join(', ') || 'PASS'}`);

  let pythonProbeCount = 0;
  try {
    runOracleCollection(process.cwd(), (_command, args) => {
      const invocation = args.join(' ');
      if (invocation === '--version') return { status: 0, stdout: 'uv 0.9.0\n', stderr: '' };
      if (invocation === 'run python --version') {
        pythonProbeCount += 1;
        return {
          status: 0,
          stdout: `${pythonProbeCount === 1 ? 'Python 3.14.0' : 'Python 3.15.0'}\n`,
          stderr: '',
        };
      }
      if (invocation === 'run pytest --version') {
        return { status: 0, stdout: 'pytest 8.4.2\n', stderr: '' };
      }
      if (invocation === 'run pytest --collect-only -q') {
        return {
          status: 0,
          stdout: 'apps/server/tests/test_alpha.py::test_one\n1 test collected in 0.01s\n',
          stderr: '',
        };
      }
      return { status: 2, stdout: '', stderr: 'unexpected synthetic command' };
    }, syntheticSourceEnvironment);
    failures.push('mid-collection toolchain drift: changed Python version was accepted');
  } catch (error: unknown) {
    const detail = error instanceof Error ? error.message : String(error);
    if (/toolchain versions changed during collection/u.test(detail)) passed += 1;
    else failures.push(`mid-collection toolchain drift: unexpected error: ${detail}`);
  }

  return { ok: failures.length === 0, passed, total: cases.length + invalidOutputs.length + 5, failures };
}
