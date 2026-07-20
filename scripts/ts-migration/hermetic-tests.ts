import { spawnSync } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

import {
  compareText,
  formatValidationResult,
  isRecord,
  type ValidationIssue,
  type ValidationResult,
  validationResult,
} from './shared.ts';

export const DAEMON_TEST_CLASSIFICATION_KIND = 'coagentia-daemon-test-classification';
export const DEFAULT_DAEMON_TEST_CLASSIFICATION = 'scripts/ts-migration/daemon-test-classification.json';
export const REQUIRED_GIT_INTEGRATION_TESTS = [
  'apps/daemon-ts/tests/git_diff.test.ts',
  'apps/daemon-ts/tests/git_worktrees.test.ts',
  'apps/daemon-ts/tests/worktree_handlers.test.ts',
] as const;

const DAEMON_PACKAGE_PATH = 'apps/daemon-ts';
const DAEMON_TEST_PREFIX = `${DAEMON_PACKAGE_PATH}/`;
const DISCOVERY_IGNORES = new Set(['.git', 'coverage', 'dist', 'node_modules']);
const MANIFEST_KEYS = new Set(['schema_version', 'kind', 'core', 'integration']);

export interface DaemonTestClassification {
  schema_version: 1;
  kind: typeof DAEMON_TEST_CLASSIFICATION_KIND;
  core: string[];
  integration: string[];
}

export interface CoreVitestInvocation {
  command: string;
  args: string[];
  cwd: string;
}

export function buildHermeticCoreEnvironment(repoRoot: string): NodeJS.ProcessEnv {
  const guardPath = path.resolve(repoRoot, 'scripts', 'ts-migration', 'hermetic-child-guard.ts');
  if (!fs.existsSync(guardPath)) throw new Error(`hermetic child-process guard 不存在: ${guardPath}`);
  return {
    ...process.env,
    COAGENTIA_HERMETIC_CORE: '1',
    NODE_OPTIONS: `--import=${pathToFileURL(guardPath).href}`,
  };
}

export interface SyntheticMutantResult {
  ok: boolean;
  passed: number;
  total: number;
  failures: string[];
}

function toPosix(value: string): string {
  return value.replaceAll('\\', '/');
}

function isCanonicalDaemonTestPath(value: string): boolean {
  if (!value.startsWith(DAEMON_TEST_PREFIX) || !value.endsWith('.test.ts')) return false;
  if (value.includes('\\') || value.includes('//') || path.posix.isAbsolute(value)) return false;
  return value.split('/').every((part) => part.length > 0 && part !== '.' && part !== '..');
}

function readStringArray(
  value: Record<string, unknown>,
  key: 'core' | 'integration',
  issues: ValidationIssue[],
): string[] {
  const candidate = value[key];
  if (!Array.isArray(candidate) || candidate.some((entry) => typeof entry !== 'string')) {
    issues.push({
      code: 'daemon_test_classification_shape',
      message: `${key} 必须是 string[]`,
      subject: key,
    });
    return [];
  }
  return candidate as string[];
}

function validateCategory(
  name: 'core' | 'integration',
  entries: readonly string[],
  issues: ValidationIssue[],
): void {
  const sorted = [...entries].sort(compareText);
  if (entries.some((entry, index) => entry !== sorted[index])) {
    issues.push({
      code: 'daemon_test_classification_unsorted',
      message: `${name} 必须按 repo-relative path 升序排列`,
      subject: name,
    });
  }
  const seen = new Set<string>();
  for (const entry of entries) {
    if (!isCanonicalDaemonTestPath(entry)) {
      issues.push({
        code: 'daemon_test_classification_path',
        message: '测试路径必须是 apps/daemon-ts 下规范化的 repo-relative *.test.ts 路径',
        subject: entry,
      });
    }
    if (seen.has(entry)) {
      issues.push({
        code: 'daemon_test_classification_duplicate',
        message: `${name} 内存在重复路径`,
        subject: entry,
      });
    }
    seen.add(entry);
  }
}

export function discoverDaemonTestFiles(repoRoot: string): string[] {
  const absoluteRepo = path.resolve(repoRoot);
  const packageRoot = path.join(absoluteRepo, ...DAEMON_PACKAGE_PATH.split('/'));
  if (!fs.existsSync(packageRoot)) throw new Error(`daemon package 不存在: ${packageRoot}`);

  const discovered: string[] = [];
  const visit = (directory: string): void => {
    const entries = fs.readdirSync(directory, { withFileTypes: true })
      .sort((left, right) => compareText(left.name, right.name));
    for (const entry of entries) {
      if (entry.isDirectory()) {
        if (!DISCOVERY_IGNORES.has(entry.name)) visit(path.join(directory, entry.name));
        continue;
      }
      if (!entry.isFile() || !entry.name.endsWith('.test.ts')) continue;
      discovered.push(toPosix(path.relative(absoluteRepo, path.join(directory, entry.name))));
    }
  };
  visit(packageRoot);
  return discovered.sort(compareText);
}

export function validateDaemonTestClassification(
  value: unknown,
  discoveredTests: readonly string[],
): ValidationResult {
  if (!isRecord(value)) {
    return validationResult([{ code: 'daemon_test_classification_shape', message: '分类清单必须是 JSON object' }]);
  }

  const issues: ValidationIssue[] = [];
  for (const key of Object.keys(value)) {
    if (!MANIFEST_KEYS.has(key)) {
      issues.push({
        code: 'daemon_test_classification_unknown_field',
        message: '分类清单包含未声明字段',
        subject: key,
      });
    }
  }
  if (value.schema_version !== 1) {
    issues.push({ code: 'daemon_test_classification_schema', message: 'schema_version 必须是 1' });
  }
  if (value.kind !== DAEMON_TEST_CLASSIFICATION_KIND) {
    issues.push({
      code: 'daemon_test_classification_kind',
      message: `kind 必须是 ${DAEMON_TEST_CLASSIFICATION_KIND}`,
    });
  }

  const core = readStringArray(value, 'core', issues);
  const integration = readStringArray(value, 'integration', issues);
  validateCategory('core', core, issues);
  validateCategory('integration', integration, issues);

  const coreSet = new Set(core);
  const integrationSet = new Set(integration);
  for (const entry of coreSet) {
    if (integrationSet.has(entry)) {
      issues.push({
        code: 'daemon_test_classification_cross_duplicate',
        message: '同一测试不可同时归入 core 和 integration',
        subject: entry,
      });
    }
  }
  for (const required of REQUIRED_GIT_INTEGRATION_TESTS) {
    if (!integrationSet.has(required) || coreSet.has(required)) {
      issues.push({
        code: 'daemon_test_classification_required_integration',
        message: '调用真 Git 的测试必须归入 integration',
        subject: required,
      });
    }
  }

  const discovered = [...discoveredTests].sort(compareText);
  const discoveredSet = new Set(discovered);
  const classified = new Set([...core, ...integration]);
  for (const testPath of discovered) {
    if (!classified.has(testPath)) {
      issues.push({
        code: 'daemon_test_classification_unclassified',
        message: '发现未分类的 daemon 测试；必须显式选择 core 或 integration',
        subject: testPath,
      });
    }
  }
  for (const testPath of classified) {
    if (!discoveredSet.has(testPath)) {
      issues.push({
        code: 'daemon_test_classification_stale',
        message: '分类清单引用了不存在的 daemon 测试',
        subject: testPath,
      });
    }
  }
  return validationResult(issues);
}

export function readDaemonTestClassification(classificationPath: string): unknown {
  return JSON.parse(fs.readFileSync(classificationPath, 'utf8')) as unknown;
}

export function verifyDaemonTestClassification(
  repoRoot: string,
  classificationPath = DEFAULT_DAEMON_TEST_CLASSIFICATION,
): ValidationResult {
  const absoluteRepo = path.resolve(repoRoot);
  const absoluteClassification = path.resolve(absoluteRepo, classificationPath);
  let value: unknown;
  let discovered: string[];
  try {
    value = readDaemonTestClassification(absoluteClassification);
  } catch (error: unknown) {
    return validationResult([{
      code: 'daemon_test_classification_read',
      message: error instanceof Error ? error.message : String(error),
      subject: toPosix(path.relative(absoluteRepo, absoluteClassification)),
    }]);
  }
  try {
    discovered = discoverDaemonTestFiles(absoluteRepo);
  } catch (error: unknown) {
    return validationResult([{
      code: 'daemon_test_classification_discovery',
      message: error instanceof Error ? error.message : String(error),
    }]);
  }
  return validateDaemonTestClassification(value, discovered);
}

export function buildCoreVitestInvocation(
  repoRoot: string,
  classification: DaemonTestClassification,
): CoreVitestInvocation {
  const absoluteRepo = path.resolve(repoRoot);
  const packageRoot = path.join(absoluteRepo, ...DAEMON_PACKAGE_PATH.split('/'));
  const vitestEntry = path.join(packageRoot, 'node_modules', 'vitest', 'vitest.mjs');
  const coreAllowlist = classification.core.map((entry) => {
    const packageRelative = path.posix.relative(DAEMON_PACKAGE_PATH, entry);
    if (packageRelative.startsWith('../') || path.posix.isAbsolute(packageRelative)) {
      throw new Error(`core 测试逃出 daemon package: ${entry}`);
    }
    return packageRelative;
  });
  if (coreAllowlist.length === 0) throw new Error('core allowlist 为空');
  if (!fs.existsSync(vitestEntry)) throw new Error(`Vitest 未安装: ${vitestEntry}`);
  return {
    command: process.execPath,
    args: [vitestEntry, 'run', ...coreAllowlist],
    cwd: packageRoot,
  };
}

export function runCoreDaemonTests(
  repoRoot: string,
  classificationPath = DEFAULT_DAEMON_TEST_CLASSIFICATION,
): number {
  const result = verifyDaemonTestClassification(repoRoot, classificationPath);
  process.stdout.write(formatValidationResult('verify-daemon-test-classification', result));
  if (!result.ok) return 1;

  const absoluteClassification = path.resolve(repoRoot, classificationPath);
  const classification = readDaemonTestClassification(absoluteClassification) as DaemonTestClassification;
  const invocation = buildCoreVitestInvocation(repoRoot, classification);
  const child = spawnSync(invocation.command, invocation.args, {
    cwd: invocation.cwd,
    env: buildHermeticCoreEnvironment(repoRoot),
    stdio: 'inherit',
    windowsHide: true,
  });
  if (child.error !== undefined) throw child.error;
  if (child.signal !== null) {
    process.stderr.write(`daemon core Vitest 被信号终止: ${child.signal}\n`);
    return 1;
  }
  return child.status ?? 1;
}

export function runDaemonTestClassificationSyntheticMutants(): SyntheticMutantResult {
  const core = ['apps/daemon-ts/tests/core.test.ts'];
  const integration = [...REQUIRED_GIT_INTEGRATION_TESTS];
  const discovered = [...core, ...integration].sort(compareText);
  const baseline: DaemonTestClassification = {
    schema_version: 1,
    kind: DAEMON_TEST_CLASSIFICATION_KIND,
    core,
    integration,
  };
  const cases: Array<{ name: string; code: string; mutate: (value: DaemonTestClassification, files: string[]) => void }> = [
    {
      name: 'new test is unclassified',
      code: 'daemon_test_classification_unclassified',
      mutate: (_value, files) => { files.push('apps/daemon-ts/tests/new.test.ts'); },
    },
    {
      name: 'stale classification entry',
      code: 'daemon_test_classification_stale',
      mutate: (value) => { value.core.push('apps/daemon-ts/tests/stale.test.ts'); },
    },
    {
      name: 'duplicate within core',
      code: 'daemon_test_classification_duplicate',
      mutate: (value) => { value.core.push(value.core[0]!); },
    },
    {
      name: 'cross-category duplicate',
      code: 'daemon_test_classification_cross_duplicate',
      mutate: (value) => { value.core.push(value.integration[0]!); value.core.sort(compareText); },
    },
    {
      name: 'unsorted category',
      code: 'daemon_test_classification_unsorted',
      mutate: (value) => { value.core = ['apps/daemon-ts/tests/z.test.ts', ...value.core]; },
    },
    {
      name: 'true Git test moved to core',
      code: 'daemon_test_classification_required_integration',
      mutate: (value) => {
        const moved = value.integration.shift()!;
        value.core.push(moved);
        value.core.sort(compareText);
      },
    },
  ];
  const failures: string[] = [];
  let passed = 0;
  for (const item of cases) {
    const value = structuredClone(baseline);
    const files = [...discovered];
    item.mutate(value, files);
    const result = validateDaemonTestClassification(value, files);
    if (result.issues.some((issue) => issue.code === item.code)) passed += 1;
    else failures.push(`${item.name}: expected ${item.code}, got ${result.issues.map((issue) => issue.code).join(', ') || 'PASS'}`);
  }
  return { ok: failures.length === 0, passed, total: cases.length, failures };
}

interface CommandOptions {
  repo: string;
  classification: string;
}

function parseCommandOptions(argv: readonly string[]): CommandOptions {
  let repo = '.';
  let classification = DEFAULT_DAEMON_TEST_CLASSIFICATION;
  for (let index = 0; index < argv.length; index += 1) {
    const option = argv[index];
    if (option !== '--repo' && option !== '--classification') throw new Error(`未知参数: ${option}`);
    const value = argv[index + 1];
    if (value === undefined || value.startsWith('--')) throw new Error(`${option} 缺少值`);
    if (option === '--repo') repo = value;
    else classification = value;
    index += 1;
  }
  return { repo, classification };
}

function main(argv: readonly string[]): number {
  const command = argv[0];
  const options = parseCommandOptions(argv.slice(1));
  if (command === 'verify') {
    const result = verifyDaemonTestClassification(options.repo, options.classification);
    process.stdout.write(formatValidationResult('verify-daemon-test-classification', result));
    return result.ok ? 0 : 1;
  }
  if (command === 'run-core') return runCoreDaemonTests(options.repo, options.classification);
  throw new Error(`用法: node hermetic-tests.ts <verify|run-core> [--repo <path>] [--classification <path>]`);
}

const invokedPath = process.argv[1] === undefined ? '' : path.resolve(process.argv[1]);
if (invokedPath === fileURLToPath(import.meta.url)) {
  try {
    process.exitCode = main(process.argv.slice(2));
  } catch (error: unknown) {
    process.stderr.write(`${error instanceof Error ? error.stack ?? error.message : String(error)}\n`);
    process.exitCode = 1;
  }
}
