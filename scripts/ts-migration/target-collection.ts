import { spawnSync } from 'node:child_process';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import {
  compareText,
  hashStringList,
  isNonEmptyString,
  isRecord,
  validationResult,
  type ValidationIssue,
  type ValidationResult,
} from './shared.ts';
import {
  TARGET_COLLECTION_KIND,
  buildTargetCollection,
  parseTargetCollection,
  type TargetCollectionManifest,
  type TargetTestRecord,
} from './test-ledger.ts';
import {
  captureScopedRepositoryProvenance,
  validateRepositoryProvenanceFields,
  verifyScopedRepositoryProvenance,
  type RepositoryProvenance,
} from './oracle.ts';

export const TARGET_SOURCE_SCOPE = [
  'apps/web',
  'apps/daemon-ts',
  'packages/contracts-ts',
  'packages/fixtures',
  'package.json',
  'pnpm-lock.yaml',
  'pnpm-workspace.yaml',
] as const;

export interface ProvenancedTargetCollectionManifest extends TargetCollectionManifest, RepositoryProvenance {}

interface VitestSuite {
  name: 'web' | 'daemon-ts';
  packagePath: string;
}

const SUITES: readonly VitestSuite[] = [
  { name: 'web', packagePath: 'apps/web' },
  { name: 'daemon-ts', packagePath: 'apps/daemon-ts' },
];

function toPosix(value: string): string {
  return value.replaceAll('\\', '/');
}

function runGit(repo: string, args: readonly string[]): string {
  const result = spawnSync('git', ['-C', repo, ...args], {
    encoding: 'utf8', windowsHide: true, maxBuffer: 4 * 1024 * 1024,
  });
  if (result.error !== undefined) throw result.error;
  if (result.status !== 0) throw new Error(`git ${args.join(' ')} failed: ${(result.stderr ?? '').trim()}`);
  return result.stdout.trim();
}

export function resolveTargetBaseline(repo: string, baselineSha: string): string {
  const candidate = baselineSha.trim().toLowerCase();
  if (candidate === 'head') {
    const head = runGit(repo, ['rev-parse', '--verify', 'HEAD^{commit}']).toLowerCase();
    if (!/^(?:[0-9a-f]{40}|[0-9a-f]{64})$/u.test(head)) {
      throw new Error(`HEAD did not resolve to a full Git commit SHA: ${head}`);
    }
    return head;
  }
  if (!/^(?:[0-9a-f]{40}|[0-9a-f]{64})$/u.test(candidate)) {
    throw new Error('--baseline-sha 必须是 HEAD 或完整的 40/64 位十六进制 Git commit SHA');
  }
  const resolved = runGit(repo, ['rev-parse', '--verify', `${candidate}^{commit}`]).toLowerCase();
  if (resolved !== candidate) throw new Error(`baseline commit 必须解析为同一个完整 SHA: ${candidate} -> ${resolved}`);
  return resolved;
}

export function validateTargetCollectionProvenance(value: unknown): ValidationResult {
  return validateRepositoryProvenanceFields(value, TARGET_SOURCE_SCOPE, 'target');
}

function readVitestVersion(packageRoot: string): string {
  const packageJson = JSON.parse(fs.readFileSync(path.join(packageRoot, 'node_modules', 'vitest', 'package.json'), 'utf8')) as unknown;
  if (!isRecord(packageJson) || !isNonEmptyString(packageJson.version)) throw new Error(`${packageRoot}: 无法读取 vitest version`);
  return packageJson.version;
}

function collectSuite(repoRoot: string, suite: VitestSuite, tempRoot: string): { version: string; tests: TargetTestRecord[]; argv: string[] } {
  const packageRoot = path.join(repoRoot, ...suite.packagePath.split('/'));
  const vitestEntry = path.join(packageRoot, 'node_modules', 'vitest', 'vitest.mjs');
  const outputPath = path.join(tempRoot, `${suite.name}.json`);
  if (!fs.existsSync(vitestEntry)) throw new Error(`${suite.packagePath}: vitest 未安装，请先 pnpm install --frozen-lockfile`);
  const argv = [vitestEntry, 'run', '--reporter=json', `--outputFile=${outputPath}`];
  const result = spawnSync(process.execPath, argv, {
    cwd: packageRoot,
    encoding: 'utf8',
    windowsHide: true,
    maxBuffer: 64 * 1024 * 1024,
    env: { ...process.env, FORCE_COLOR: '0' },
  });
  if (result.error !== undefined) throw result.error;
  if (result.status !== 0) {
    throw new Error(`${suite.name} vitest reporter failed (${String(result.status)}): ${(result.stderr ?? result.stdout ?? '').slice(-8000)}`);
  }
  const report = JSON.parse(fs.readFileSync(outputPath, 'utf8')) as unknown;
  if (!isRecord(report) || report.success !== true || !Array.isArray(report.testResults)) {
    throw new Error(`${suite.name}: Vitest JSON report 无效或 success != true`);
  }
  const tests: TargetTestRecord[] = [];
  for (const resultFile of report.testResults) {
    if (!isRecord(resultFile) || !isNonEmptyString(resultFile.name) || !Array.isArray(resultFile.assertionResults)) {
      throw new Error(`${suite.name}: Vitest testResults 形状无效`);
    }
    const absoluteFile = path.resolve(resultFile.name);
    const relativeFile = toPosix(path.relative(repoRoot, absoluteFile));
    if (relativeFile.startsWith('../') || path.isAbsolute(relativeFile)) throw new Error(`${suite.name}: test file 逃出 repo: ${resultFile.name}`);
    for (const assertion of resultFile.assertionResults) {
      if (!isRecord(assertion) || !isNonEmptyString(assertion.fullName) || !isNonEmptyString(assertion.status)) {
        throw new Error(`${suite.name}: assertionResults 形状无效`);
      }
      const status = assertion.status === 'passed'
        ? 'passed'
        : assertion.status === 'skipped' || assertion.status === 'pending'
          ? 'skipped'
          : undefined;
      if (status === undefined) throw new Error(`${suite.name}: 非绿 reporter status ${assertion.status}`);
      tests.push({
        id: `${relativeFile}::${assertion.fullName.trim()}`,
        suite: suite.name,
        file: relativeFile,
        status,
      });
    }
  }
  if (report.numTotalTests !== tests.length) {
    throw new Error(`${suite.name}: reporter numTotalTests=${String(report.numTotalTests)}，实际 assertion=${tests.length}`);
  }
  return {
    version: readVitestVersion(packageRoot),
    tests,
    argv: ['node', `${suite.packagePath}/node_modules/vitest/vitest.mjs`, 'run', '--reporter=json', '--outputFile=<temp>'],
  };
}

function collectTsTargetManifestInternal(
  repo: string,
  baselineSha: string,
  requireBaselineAtHead: boolean,
): ProvenancedTargetCollectionManifest {
  const repoRoot = path.resolve(runGit(repo, ['rev-parse', '--show-toplevel']));
  const resolvedBaseline = resolveTargetBaseline(repoRoot, baselineSha);
  const provenanceBefore = captureScopedRepositoryProvenance(
    repoRoot,
    resolvedBaseline,
    TARGET_SOURCE_SCOPE,
    requireBaselineAtHead,
  );
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'coagentia-ts-targets-'));
  try {
    const results = SUITES.map((suite) => ({ suite, result: collectSuite(repoRoot, suite, tempRoot) }));
    const versions = new Set(results.map(({ result }) => result.version));
    if (versions.size !== 1) throw new Error(`Vitest 版本不一致: ${[...versions].join(', ')}`);
    const tests = results.flatMap(({ result }) => result.tests).sort((left, right) => compareText(left.id, right.id));
    const ids = tests.map((test) => test.id);
    if (new Set(ids).size !== ids.length) throw new Error('Vitest reporter 产生重复 target test id');
    const provenanceAfter = captureScopedRepositoryProvenance(
      repoRoot,
      resolvedBaseline,
      TARGET_SOURCE_SCOPE,
      requireBaselineAtHead,
    );
    if (
      provenanceBefore.baseline_tree_sha !== provenanceAfter.baseline_tree_sha
      || provenanceBefore.source_fingerprint_sha256 !== provenanceAfter.source_fingerprint_sha256
    ) {
      throw new Error('target source provenance changed while Vitest collection was running');
    }
    return {
      schema_version: 1,
      kind: TARGET_COLLECTION_KIND,
      collector: 'vitest-json-reporter',
      baseline_sha: resolvedBaseline,
      runner: { name: 'vitest', version: [...versions][0]! },
      commands: results.map(({ suite, result }) => ({ suite: suite.name, argv: result.argv, exit_code: 0 })),
      test_id_count: ids.length,
      test_ids_sha256: hashStringList(ids),
      test_ids: ids,
      tests,
      ...provenanceAfter,
    };
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
}

export function collectTsTargetManifest(repo: string, baselineSha: string): ProvenancedTargetCollectionManifest {
  return collectTsTargetManifestInternal(repo, baselineSha, true);
}

export function verifyCurrentTsTargets(repo: string, frozenValue: unknown): ValidationResult {
  const frozen = parseTargetCollection(frozenValue);
  const provenance = verifyScopedRepositoryProvenance(repo, frozenValue, TARGET_SOURCE_SCOPE, 'target');
  if (frozen.issues.length > 0 || !isRecord(frozenValue) || !isNonEmptyString(frozenValue.baseline_sha)) {
    return validationResult([...frozen.issues, ...provenance.issues]);
  }
  if (!provenance.ok) return provenance;
  let current: ProvenancedTargetCollectionManifest;
  try {
    current = collectTsTargetManifestInternal(repo, frozenValue.baseline_sha, false);
  } catch (error: unknown) {
    return validationResult([{ code: 'target_collection_failed', message: error instanceof Error ? error.message : String(error) }]);
  }
  const issues: ValidationIssue[] = [];
  const expected = frozenValue as unknown as ProvenancedTargetCollectionManifest;
  if (expected.baseline_sha !== current.baseline_sha) {
    issues.push({ code: 'target_baseline_drift', message: `baseline ${expected.baseline_sha} -> ${current.baseline_sha}` });
  }
  if (expected.runner.version !== current.runner.version) {
    issues.push({ code: 'target_runner_drift', message: `Vitest ${expected.runner.version} -> ${current.runner.version}` });
  }
  if (expected.baseline_tree_sha !== current.baseline_tree_sha) {
    issues.push({ code: 'target_baseline_tree_drift', message: 'target baseline tree provenance changed' });
  }
  if (expected.source_fingerprint_sha256 !== current.source_fingerprint_sha256) {
    issues.push({ code: 'target_source_drift', message: 'target source/package/lock provenance changed' });
  }
  if (expected.test_ids_sha256 !== current.test_ids_sha256 || expected.test_id_count !== current.test_id_count) {
    issues.push({ code: 'target_collection_drift', message: `target collection ${expected.test_id_count}/${expected.test_ids_sha256} -> ${current.test_id_count}/${current.test_ids_sha256}` });
  }
  const expectedStates = expected.tests.map((test) => `${test.id}\0${test.status}`);
  const currentStates = current.tests.map((test) => `${test.id}\0${test.status}`);
  if (hashStringList(expectedStates) !== hashStringList(currentStates)) {
    issues.push({ code: 'target_status_drift', message: 'target test active/skipped 状态发生漂移' });
  }
  const expectedRecords = expected.tests.map((test) => JSON.stringify([test.id, test.suite, test.file, test.status]));
  const currentRecords = current.tests.map((test) => JSON.stringify([test.id, test.suite, test.file, test.status]));
  if (hashStringList(expectedRecords) !== hashStringList(currentRecords)) {
    issues.push({ code: 'target_reporter_record_drift', message: 'target reporter suite/file/status 记录发生漂移' });
  }
  const expectedCommands = expected.commands.map((command) => JSON.stringify([command.suite, command.argv, command.exit_code]));
  const currentCommands = current.commands.map((command) => JSON.stringify([command.suite, command.argv, command.exit_code]));
  if (hashStringList(expectedCommands) !== hashStringList(currentCommands)) {
    issues.push({ code: 'target_command_drift', message: 'target reporter command provenance 发生漂移' });
  }
  return validationResult(issues);
}

export function runTargetCollectionSyntheticMutants(): { ok: boolean; passed: number; total: number; failures: string[] } {
  const baseline = buildTargetCollection(['apps/example.test.ts::case']);
  const cases: Array<{ name: string; code: string; mutate: (value: TargetCollectionManifest) => void }> = [
    { name: 'collector', code: 'target_collector', mutate: (value) => { (value as unknown as Record<string, unknown>).collector = 'manual'; } },
    { name: 'baseline', code: 'target_baseline_sha', mutate: (value) => { value.baseline_sha = 'bad'; } },
    { name: 'runner', code: 'target_runner', mutate: (value) => { value.runner.version = ''; } },
    { name: 'command exit', code: 'target_commands', mutate: (value) => { (value.commands[0] as unknown as { exit_code: number }).exit_code = 1; } },
    { name: 'command is not reporter', code: 'target_commands', mutate: (value) => { value.commands[0]!.argv = ['vitest', 'run']; } },
    { name: 'status', code: 'target_test_record', mutate: (value) => { (value.tests[0] as unknown as { status: string }).status = 'failed'; } },
    { name: 'file provenance', code: 'target_test_provenance', mutate: (value) => { value.tests[0]!.file = 'different.test.ts'; } },
    { name: 'test identity', code: 'target_tests_id_mismatch', mutate: (value) => { value.tests[0]!.id = 'different'; } },
  ];
  let passed = 0;
  const failures: string[] = [];
  for (const item of cases) {
    const value = structuredClone(baseline);
    item.mutate(value);
    const result = parseTargetCollection(value);
    if (result.issues.some((issue) => issue.code === item.code)) passed += 1;
    else failures.push(`${item.name}: expected ${item.code}, got ${result.issues.map((issue) => issue.code).join(', ') || 'PASS'}`);
  }
  return { ok: failures.length === 0, passed, total: cases.length, failures };
}
