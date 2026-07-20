import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import test from 'node:test';

import {
  buildOracleManifest,
  runOracleSyntheticMutants,
  runPytestCollection,
  validateOracleManifest,
} from '../oracle.ts';
import {
  assertRepositoryWorktreeClean,
  hashStringList,
  stableStringify,
  verifyP0EvidenceOnlyRepositoryState,
} from '../shared.ts';
import {
  buildInitialTestLedger,
  buildTargetCollection,
  parseTargetCollection,
  runLedgerSyntheticMutants,
  verifyTestLedger,
} from '../test-ledger.ts';
import {
  assertInventoryGenerationBaseline,
  resolveInventoryBaseline,
  runInventorySyntheticMutants,
  scanMigrationInventory,
  verifyMigrationInventoryDocument,
} from '../migration-inventory.ts';
import { runAuthoritySyntheticMutants, verifyPlanAuthority } from '../authority.ts';
import { buildP0MigrationInventory } from '../inventory-policy.ts';
import { runTargetCollectionSyntheticMutants } from '../target-collection.ts';

test('build-oracle-collection normalizes, sorts, and hashes deterministically', () => {
  const options = {
    baselineSha: '0123456789abcdef0123456789abcdef01234567',
    collectCommand: 'uv run pytest --collect-only -q',
    environment: { python: '3.14', os: 'windows' },
  };
  const left = buildOracleManifest(
    'apps\\server\\tests\\test_b.py::test_b\r\napps/server/tests/test_a.py::test_a\r\n2 tests collected in 0.1s\r\n',
    options,
  );
  const right = buildOracleManifest(
    'apps/server/tests/test_a.py::test_a\napps/server/tests/test_b.py::test_b\n2 tests collected in 0.1s\n',
    options,
  );
  assert.deepEqual(left, right);
  assert.deepEqual(left.nodeids, ['apps/server/tests/test_a.py::test_a', 'apps/server/tests/test_b.py::test_b']);
  assert.equal(validateOracleManifest(left).ok, true);
});

test('oracle collection uses a controlled uv argv and fails closed on non-zero exit', () => {
  let observed: { command: string; args: readonly string[]; cwd: string } | undefined;
  const output = 'apps/server/tests/test_alpha.py::test_one\n1 test collected in 0.01s\n';
  const actual = runPytestCollection(process.cwd(), (command, args, options) => {
    observed = { command, args: [...args], cwd: options.cwd };
    return { status: 0, stdout: output, stderr: '' };
  });
  assert.equal(actual, output);
  assert.deepEqual(observed, {
    command: 'uv',
    args: ['run', 'pytest', '--collect-only', '-q'],
    cwd: path.resolve(process.cwd()),
  });
  assert.throws(
    () => runPytestCollection(process.cwd(), () => ({ status: 3, stdout: '', stderr: 'collection failed' })),
    /exited 3/u,
  );
});

test('string-list hashing is collision-safe and JSON output sorts nested object keys', () => {
  assert.notEqual(hashStringList(['a\nb']), hashStringList(['a', 'b']));
  assert.equal(
    stableStringify({ z: { b: 2, a: 1 }, a: [{ d: 4, c: 3 }] }),
    stableStringify({ a: [{ c: 3, d: 4 }], z: { a: 1, b: 2 } }),
  );
});

test('P0 artifacts require a clean baseline and evidence-only commits afterwards', () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'coagentia-evidence-history-'));
  try {
    const git = (...args: string[]): string => execFileSync('git', args, {
      cwd: tempRoot,
      encoding: 'utf8',
      stdio: 'pipe',
      windowsHide: true,
    });
    git('init');
    git('config', 'user.name', 'CoAgentia Test');
    git('config', 'user.email', 'coagentia-test@example.invalid');
    fs.writeFileSync(path.join(tempRoot, 'README.md'), 'baseline\n', 'utf8');
    git('add', 'README.md');
    git('commit', '-m', 'baseline');
    const baseline = git('rev-parse', 'HEAD').trim();
    assert.doesNotThrow(() => { assertRepositoryWorktreeClean(tempRoot); });

    const evidencePath = path.join(tempRoot, 'docs', 'verify', 'ts-migration', 'artifact.json');
    fs.mkdirSync(path.dirname(evidencePath), { recursive: true });
    fs.writeFileSync(evidencePath, '{}\n', 'utf8');
    git('add', 'docs/verify/ts-migration/artifact.json');
    git('commit', '-m', 'evidence');
    assert.equal(verifyP0EvidenceOnlyRepositoryState(tempRoot, baseline, 'fixture').ok, true);

    const dirtyPath = path.join(tempRoot, 'dirty.tmp');
    fs.writeFileSync(dirtyPath, 'dirty\n', 'utf8');
    const dirty = verifyP0EvidenceOnlyRepositoryState(tempRoot, baseline, 'fixture');
    assert.equal(dirty.issues.some((issue) => issue.code === 'fixture_worktree_dirty'), true);
    fs.rmSync(dirtyPath);

    fs.writeFileSync(path.join(tempRoot, 'README.md'), 'tooling drift\n', 'utf8');
    git('add', 'README.md');
    git('commit', '-m', 'non evidence');
    const drift = verifyP0EvidenceOnlyRepositoryState(tempRoot, baseline, 'fixture');
    assert.equal(drift.issues.some((issue) => issue.code === 'fixture_non_evidence_history'), true);

    fs.writeFileSync(path.join(tempRoot, 'README.md'), 'baseline\n', 'utf8');
    git('add', 'README.md');
    git('commit', '-m', 'revert non evidence');
    const reverted = verifyP0EvidenceOnlyRepositoryState(tempRoot, baseline, 'fixture');
    assert.equal(
      reverted.issues.some((issue) => issue.code === 'fixture_non_evidence_history'),
      true,
      'change-then-revert commits must remain auditable violations',
    );
  } finally {
    const resolved = path.resolve(tempRoot);
    assert.equal(path.basename(resolved).startsWith('coagentia-evidence-history-'), true);
    assert.equal(resolved.startsWith(path.resolve(os.tmpdir())), true);
    fs.rmSync(resolved, { recursive: true, force: true });
  }
});

test('strict target collection rejects a bare array manifest', () => {
  const result = parseTargetCollection(['server/example.test.ts::case']);
  assert.equal(result.issues.some((issue) => issue.code === 'target_not_object'), true);
});

test('package ledger gate forwards wave arguments to the real verifier', () => {
  const packageJson = JSON.parse(fs.readFileSync(path.join(process.cwd(), 'package.json'), 'utf8')) as {
    scripts?: Record<string, string>;
  };
  const command = packageJson.scripts?.['verify:test-ledger'] ?? '';
  assert.match(command, /cli\.ts verify-test-ledger/u);
  assert.match(command, /--targets docs\/verify\/ts-migration\/ts-test-collection\.json/u);
  assert.doesNotMatch(command, /--baseline|--self-test|&&/u);
  assert.equal(packageJson.scripts?.['verify:test-ledger:baseline'], 'pnpm verify:test-ledger -- --baseline');
  for (const name of [
    'verify:test-ledger',
    'verify:ts-test-targets',
    'verify:migration-inventory',
    'verify:plan-authority',
  ]) {
    assert.doesNotMatch(packageJson.scripts?.[name] ?? '', /&&|--self-test/u, `${name} must be the real gate only`);
  }
});

test('Vitest target collection provenance mutants are all detected', () => {
  const result = runTargetCollectionSyntheticMutants();
  assert.equal(result.ok, true, result.failures.join('\n'));
  assert.equal(result.passed, result.total);
});

test('oracle manifest synthetic mutants reject empty collections and weak provenance', () => {
  const result = runOracleSyntheticMutants();
  assert.equal(result.ok, true, result.failures.join('\n'));
  assert.equal(result.passed, result.total);
});

test('verify-test-ledger synthetic mutants are all detected', () => {
  const result = runLedgerSyntheticMutants();
  assert.equal(result.ok, true, result.failures.join('\n'));
  assert.equal(result.passed, result.total);
});

test('baseline ledger is explicit pending and strict mode rejects it', () => {
  const oracle = buildOracleManifest(
    'apps/server/tests/test_alpha.py::test_one\n1 test collected in 0.01s\n',
    {
      baselineSha: '0123456789abcdef0123456789abcdef01234567',
      collectCommand: 'uv run pytest --collect-only -q',
      environment: {},
    },
  );
  const ledger = buildInitialTestLedger(oracle, 'p0-integrator');
  assert.equal(verifyTestLedger(oracle, ledger.entries, undefined, { mode: 'baseline' }).ok, true);
  const strict = verifyTestLedger(oracle, ledger.entries, buildTargetCollection([]), { mode: 'strict' });
  assert.equal(strict.issues.some((issue) => issue.code === 'ledger_status_not_accepted'), true);
});

test('--wave strictness applies to the selected wave while preserving global source coverage', () => {
  const oracle = buildOracleManifest(
    'apps/server/tests/test_alpha.py::test_one\napps/daemon/tests/test_beta.py::test_two\n2 tests collected in 0.01s\n',
    {
      baselineSha: '0123456789abcdef0123456789abcdef01234567',
      collectCommand: 'uv run pytest --collect-only -q',
      environment: {},
    },
  );
  const ledger = buildInitialTestLedger(oracle, 'p0-integrator');
  const serverEntry = ledger.entries.find((entry) => entry.legacy_nodeid.startsWith('apps/server/'));
  assert.ok(serverEntry);
  Object.assign(serverEntry, {
    status: 'accepted', disposition: 'port', target_test_id: 'server/db.test.ts::one', reviewer: 'reviewer-a',
    allocation_evidence: ['allocation:test-fixture'], allocation_reviewer: 'allocation-reviewer-a',
  });
  const result = verifyTestLedger(
    oracle,
    ledger.entries,
    buildTargetCollection(['server/db.test.ts::one']),
    { mode: 'strict', waves: new Set(['B0-B10']) },
  );
  assert.equal(result.ok, true, result.issues.map((issue) => issue.code).join(', '));
  const emptyScope = verifyTestLedger(
    oracle,
    ledger.entries,
    buildTargetCollection(['server/db.test.ts::one']),
    { mode: 'strict', waves: new Set(['B9-missing']) },
  );
  assert.equal(emptyScope.issues.some((issue) => issue.code === 'ledger_wave_not_found'), true);
});

test('verify-migration-inventory synthetic mutants are all detected', () => {
  const result = runInventorySyntheticMutants();
  assert.equal(result.ok, true, result.failures.join('\n'));
  assert.equal(result.passed, result.total);
});

test('inventory scanner sees tracked Python source and configured entrypoints', () => {
  const scan = scanMigrationInventory(process.cwd());
  assert.deepEqual(scan.issues, []);
  const ids = new Set(scan.entries.map((entry) => entry.id));
  assert.equal(ids.has('file:scripts/export_schemas.py'), true);
  assert.equal(ids.has('file:apps/server/migrations/script.py.mako'), true);
  assert.equal(ids.has('inline-script:site/index.html#1'), true);
  assert.equal([...ids].some((id) => id.startsWith('doc-command:README.md#')), true);
  assert.equal([...ids].some((id) => id.startsWith('doc-command:AGENTS.md#')), true);
  assert.equal([...ids].some((id) => id.startsWith('doc-command:plan.md#')), true);
  assert.equal(
    scan.entries.filter((entry) => (
      entry.id.startsWith('doc-command:scratchpad/PREVIEW-CALIBRATION.md#')
      && entry.migration_residual
    )).length,
    3,
  );
  assert.equal(ids.has('file:scripts/ts-migration/cli.ts'), true, 'standard untracked shebang entry must be scanned');
  assert.equal(ids.has('package-script:package.json#gen:schemas'), true);
  assert.equal(
    [...ids].some((id) => id.startsWith('package-bin:apps/daemon-ts/package.json#')),
    true,
  );
  for (const id of [
    'file:apps/server/migrations/script.py.mako',
    'inline-script:site/index.html#1',
    'file:scripts/ts-migration/cli.ts',
  ]) {
    assert.match(scan.entries.find((entry) => entry.id === id)?.fingerprint ?? '', /^[0-9a-f]{64}$/u);
  }
  assert.equal(
    scan.entries.every((entry) => /^(?:100644|100755|120000|untracked)$/u.test(entry.file_mode)),
    true,
  );
  assert.equal(scan.entries.every((entry) => /^[0-9a-f]{40}(?:[0-9a-f]{24})?$/u.test(entry.git_blob)), true);
  const daemonBin = scan.entries.find(
    (entry) => entry.id === 'package-bin:apps/daemon-ts/package.json#coagentia-daemon-ts',
  );
  assert.equal(daemonBin?.migration_residual, true);
  const denyGuards = scan.entries.filter(
    (entry) => entry.kind === 'ci-run' && (entry.detail ?? '').includes('coagentia-runtime-deny'),
  );
  assert.equal(denyGuards.length, 2);
  assert.equal(denyGuards.every((entry) => !entry.migration_residual), true);
});

test('P0 inventory policy gives every discovered entry an auditable decision', () => {
  const scan = scanMigrationInventory(process.cwd());
  const baseline = resolveInventoryBaseline(process.cwd(), 'HEAD');
  const document = buildP0MigrationInventory(scan, baseline);
  const result = verifyMigrationInventoryDocument(scan, document, 'json', baseline);
  assert.equal(result.ok, true, result.issues.map((issue) => `${issue.code}: ${issue.subject ?? ''}`).join('\n'));
  assert.equal(document.entries.length, scan.entries.length);
  assert.equal(document.entries.every((entry) => entry.owner.length > 0 && entry.rationale.length > 0), true);
  assert.match(String(document.baseline_tree_sha), /^[0-9a-f]{40}(?:[0-9a-f]{24})?$/u);
  assert.match(String(document.scan_scope_sha256), /^[0-9a-f]{64}$/u);
  assert.equal(Object.hasOwn(document, 'generated_from_head'), false);
  const daemonBin = document.entries.find(
    (entry) => entry.id === 'package-bin:apps/daemon-ts/package.json#coagentia-daemon-ts',
  );
  assert.deepEqual(
    daemonBin === undefined
      ? undefined
      : { disposition: daemonBin.disposition, target_phase: daemonBin.target_phase, owner: daemonBin.owner },
    { disposition: 'replace', target_phase: 'A1', owner: 'owner:A1-distribution' },
  );
});

test('verify-plan-authority synthetic mutants are all detected', () => {
  const result = runAuthoritySyntheticMutants();
  assert.equal(result.ok, true, result.failures.join('\n'));
  assert.equal(result.passed, result.total);
});

test('repository has one active TypeScript migration authority', () => {
  const result = verifyPlanAuthority(process.cwd());
  assert.equal(result.ok, true, result.issues.map((issue) => `${issue.code}: ${issue.subject ?? ''}`).join('\n'));
});

test('inventory scanner includes standard untracked source and workflow before commit', () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'coagentia-inventory-test-'));
  try {
    const git = (...args: string[]): void => {
      execFileSync('git', args, { cwd: tempRoot, stdio: 'pipe', windowsHide: true });
    };
    git('init');
    git('config', 'user.name', 'CoAgentia Test');
    git('config', 'user.email', 'coagentia-test@example.invalid');
    fs.writeFileSync(
      path.join(tempRoot, 'README.md'),
      [
        '```powershell',
        'uv run pytest -q --plain-fence',
        '```',
        '~~~powershell',
        'uv run pytest -q --tilde-fence',
        '~~~',
        '    uv run pytest -q --indented',
        '<pre><code>uv run pytest -q --html</code></pre>',
        '> uv run pytest -q --quote',
        '- uv run pytest -q --bullet',
        '`uv run pytest -q --inline`',
        "> $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; uv run pytest -q --env-prefix",
        "`git ls-files '*.py'`",
        '- [ ] uv run pytest -q --task-list',
        '    - uv run pytest -q --nested-list',
        '| migration command | uv run pytest -q --table |',
        '```text',
        'uv run pytest -q --text-fence',
        '```',
        '```{.powershell}',
        'uv run pytest -q --pandoc-fence',
        '```',
        'env FOO=1 uv run pytest -q --posix-env',
        '& uv run pytest -q --powershell-call',
        '.venv\\Scripts\\python.exe -m pytest -q --venv-python',
        'cd app && uv run pytest -q --cd-chain',
        'PS C:\\repo> uv run pytest -q --ps-prompt',
        "`git -c alias.legacy='!python hidden.py.backup.txt' legacy`",
        "`git ls-files '*.py' && python hidden.py.backup.txt`",
        '',
      ].join('\n'),
      'utf8',
    );
    fs.writeFileSync(path.join(tempRoot, 'legacy.txt'), '#!/usr/bin/env python\nprint("legacy")\n', 'utf8');
    fs.writeFileSync(path.join(tempRoot, 'hidden.py.txt'), 'print("hidden legacy source")\n', 'utf8');
    fs.writeFileSync(path.join(tempRoot, 'hidden.py.backup.txt'), 'print("hidden backup source")\n', 'utf8');
    fs.writeFileSync(path.join(tempRoot, 'source.py.config.json'), '{"legacy": true}\n', 'utf8');
    git('add', 'README.md', 'legacy.txt', 'hidden.py.txt');
    git('update-index', '--chmod=+x', 'legacy.txt');
    git('commit', '-m', 'fixture');
    fs.mkdirSync(path.join(tempRoot, '.github', 'workflows'), { recursive: true });
    fs.writeFileSync(
      path.join(tempRoot, '.github', 'workflows', 'new.yml'),
      'name: new\non: push\njobs:\n  check:\n    runs-on: windows-latest\n    steps:\n      - uses: actions/checkout@v4\n',
      'utf8',
    );
    fs.writeFileSync(path.join(tempRoot, 'new_tool.py'), 'print("new")\n', 'utf8');
    fs.writeFileSync(path.join(tempRoot, 'main.go'), 'package main\nfunc main() {}\n', 'utf8');
    fs.writeFileSync(path.join(tempRoot, 'worker.coffee'), 'square = (x) -> x * x\n', 'utf8');
    fs.mkdirSync(path.join(tempRoot, '.github', 'actions', 'local'), { recursive: true });
    fs.writeFileSync(
      path.join(tempRoot, '.github', 'actions', 'local', 'action.yml'),
      'name: local\nruns:\n  using: composite\n  steps:\n    - shell: pwsh\n      run: uv run pytest -q\n',
      'utf8',
    );
    fs.writeFileSync(path.join(tempRoot, 'Dockerfile'), 'FROM scratch\nRUN python tool.py\n', 'utf8');
    fs.writeFileSync(path.join(tempRoot, 'Procfile'), 'web: uvicorn app:app\n', 'utf8');
    fs.writeFileSync(path.join(tempRoot, 'page.html'), '<script>globalThis.fixture = true;</script>\n', 'utf8');
    fs.writeFileSync(path.join(tempRoot, 'template.py.mako'), '<% import pathlib %>\n', 'utf8');
    fs.writeFileSync(path.join(tempRoot, 'legacy-view.jsx'), 'export const View = () => null;\n', 'utf8');
    fs.writeFileSync(
      path.join(tempRoot, 'package.json'),
      JSON.stringify({
        name: 'inventory-fixture',
        bin: { fixture: './src/cli.ts' },
        scripts: {
          pythonExe: 'python.exe -c "print(1)"',
          serve: 'uvicorn app:app',
          hiddenBackup: 'python hidden.py.backup.txt',
          sourceConfig: 'python source.py.config.json',
        },
      }),
      'utf8',
    );
    fs.writeFileSync(
      path.join(tempRoot, 'pyproject.toml'),
      '[project.gui-scripts]\nfixture-gui = "fixture.gui:main"\n\n[tool.poetry.scripts]\nfixture-poetry = "fixture.cli:main"\n',
      'utf8',
    );
    fs.mkdirSync(path.join(tempRoot, 'scratchpad'), { recursive: true });
    fs.writeFileSync(
      path.join(tempRoot, 'scratchpad', 'PREVIEW-CALIBRATION.md'),
      '`python.exe -c "print(1)"`\n\n```powershell\nuvicorn app:app\n```\n',
      'utf8',
    );

    const scan = scanMigrationInventory(tempRoot);
    assert.throws(
      () => { assertInventoryGenerationBaseline(scan, resolveInventoryBaseline(tempRoot, 'HEAD')); },
      /requires a clean/u,
    );
    const byId = new Map(scan.entries.map((entry) => [entry.id, entry]));
    assert.equal(byId.has('ci-workflow:.github/workflows/new.yml'), true);
    assert.equal(byId.has('ci-workflow:.github/actions/local/action.yml'), true);
    assert.equal(byId.has('ci-run:.github/actions/local/action.yml#1'), true);
    assert.equal(byId.has('executable-config:Dockerfile'), true);
    assert.equal(byId.has('executable-config:Procfile'), true);
    assert.equal(byId.has('inline-script:page.html#1'), true);
    assert.equal(byId.has('file:template.py.mako'), true);
    assert.equal(byId.has('file:legacy-view.jsx'), true);
    assert.equal(byId.has('pyproject-script:pyproject.toml#project.gui-scripts#fixture-gui'), true);
    assert.equal(byId.has('pyproject-script:pyproject.toml#tool.poetry.scripts#fixture-poetry'), true);
    assert.equal(byId.has('package-bin:package.json#fixture'), true);
    assert.equal(byId.get('package-bin:package.json#fixture')?.migration_residual, true);
    assert.equal(byId.get('package-script:package.json#pythonExe')?.migration_residual, true);
    assert.equal(byId.get('package-script:package.json#serve')?.migration_residual, true);
    assert.equal(
      [...byId.keys()].filter((id) => id.startsWith('doc-command:scratchpad/PREVIEW-CALIBRATION.md#')).length,
      2,
    );
    const readmeCommands = [...byId.values()].filter((entry) => entry.id.startsWith('doc-command:README.md#'));
    const expectedMarkers = [
      '--plain-fence', '--tilde-fence', '--indented', '--html', '--quote', '--bullet', '--inline', '--env-prefix',
      '--task-list', '--nested-list', '--table', '--text-fence', '--pandoc-fence', '--posix-env',
      '--powershell-call', '--venv-python', '--cd-chain', '--ps-prompt',
    ];
    assert.deepEqual(
      expectedMarkers.filter((marker) => !readmeCommands.some((entry) => entry.detail?.includes(marker))),
      [],
      'all documented command container/prefix variants must be scanned',
    );
    assert.equal(readmeCommands.length, 21);
    const gitAssertion = readmeCommands.find((entry) => entry.detail?.startsWith('git ls-files'));
    assert.equal(gitAssertion?.migration_residual, false, 'pure Git negative assertions must remain permanent keep gates');
    const gitAlias = readmeCommands.find((entry) => entry.detail?.startsWith('git -c alias.legacy'));
    assert.equal(gitAlias?.migration_residual, true, 'Git aliases executing Python are migration residuals');
    const chainedGitAssertion = readmeCommands.find((entry) => entry.detail?.includes('&& python'));
    assert.equal(chainedGitAssertion?.migration_residual, true, 'git ls-files exemption cannot hide a chained Python command');
    assert.equal(byId.has('file:new_tool.py'), true);
    assert.equal(byId.has('file:main.go'), true, 'unknown first-party source extensions must fail closed into inventory');
    assert.equal(byId.has('file:worker.coffee'), true);
    assert.match(byId.get('ci-workflow:.github/workflows/new.yml')?.fingerprint ?? '', /^[0-9a-f]{64}$/u);
    assert.match(byId.get('file:new_tool.py')?.fingerprint ?? '', /^[0-9a-f]{64}$/u);
    assert.equal(byId.get('ci-run:.github/actions/local/action.yml#1')?.migration_residual, true);
    assert.equal(byId.get('executable-config:Dockerfile')?.migration_residual, true);
    assert.equal(byId.get('executable-config:Procfile')?.migration_residual, true);
    assert.equal(byId.get('file:legacy-view.jsx')?.migration_residual, true);
    const trackedReadme = [...byId.values()].find((entry) => entry.id.startsWith('doc-command:README.md#'));
    assert.equal(trackedReadme?.file_mode, '100644');
    assert.match(trackedReadme?.git_blob ?? '', /^[0-9a-f]{40}(?:[0-9a-f]{24})?$/u);
    assert.equal(byId.get('file:new_tool.py')?.file_mode, 'untracked');
    assert.equal(byId.get('file:main.go')?.migration_residual, true);
    assert.equal(byId.get('file:legacy.txt')?.migration_residual, true);
    assert.equal(byId.get('file:hidden.py.txt')?.migration_residual, true);
    assert.equal(byId.get('file:hidden.py.backup.txt')?.migration_residual, true);
    assert.equal(byId.get('file:source.py.config.json')?.migration_residual, true);
    assert.match(byId.get('file:legacy.txt')?.reasons.join(' ') ?? '', /shebang/u);
    assert.match(byId.get('file:hidden.py.txt')?.reasons.join(' ') ?? '', /legacy source suffix/u);
    assert.match(byId.get('file:hidden.py.backup.txt')?.reasons.join(' ') ?? '', /legacy source suffix/u);
    assert.match(byId.get('file:source.py.config.json')?.reasons.join(' ') ?? '', /legacy source suffix/u);
    assert.match(byId.get('file:main.go')?.reasons.join(' ') ?? '', /fail-closed/u);
    assert.match(byId.get('file:new_tool.py')?.git_blob ?? '', /^[0-9a-f]{40}(?:[0-9a-f]{24})?$/u);
  } finally {
    const resolved = path.resolve(tempRoot);
    assert.equal(path.basename(resolved).startsWith('coagentia-inventory-test-'), true);
    assert.equal(resolved.startsWith(path.resolve(os.tmpdir())), true);
    fs.rmSync(resolved, { recursive: true, force: true });
  }
});

test('P0 mother gate owns the live oracle while hermetic core stays Node-only', () => {
  const packageJson = JSON.parse(
    fs.readFileSync(path.join(process.cwd(), 'package.json'), 'utf8'),
  ) as { scripts: Record<string, string> };
  assert.equal(
    packageJson.scripts['verify:p0'],
    'pnpm verify:oracle-collection && pnpm verify:p0:git',
  );
  assert.doesNotMatch(packageJson.scripts['verify:p0:git'] ?? '', /verify:oracle-collection/u);

  const workflow = fs.readFileSync(
    path.join(process.cwd(), '.github', 'workflows', 'ts-migration-p0.yml'),
    'utf8',
  );
  const hermetic = workflow.split(/^  git-browser-integration-windows:/mu, 1)[0] ?? '';
  const integration = workflow.split(/^  git-browser-integration-windows:/mu)[1]?.split(/^  legacy-oracle-windows:/mu, 1)[0] ?? '';
  const legacy = workflow.split(/^  legacy-oracle-windows:/mu)[1] ?? '';
  assert.match(hermetic, /coagentia-runtime-deny/u);
  assert.doesNotMatch(hermetic, /Install checksum-pinned MinGit/u);
  assert.doesNotMatch(hermetic, /run:\s+pnpm verify:p0(?:\s|$)/u);
  assert.match(integration, /run:\s+pnpm verify:p0:git/u);
  assert.match(legacy, /run:\s+pnpm verify:p0(?:\s|$)/u);
});
