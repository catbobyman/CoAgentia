import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';
import test from 'node:test';

import {
  buildCoreVitestInvocation,
  buildHermeticCoreEnvironment,
  DAEMON_TEST_CLASSIFICATION_KIND,
  DEFAULT_DAEMON_TEST_CLASSIFICATION,
  discoverDaemonTestFiles,
  readDaemonTestClassification,
  REQUIRED_GIT_INTEGRATION_TESTS,
  runDaemonTestClassificationSyntheticMutants,
  type DaemonTestClassification,
  validateDaemonTestClassification,
  verifyDaemonTestClassification,
} from '../hermetic-tests.ts';

test('repository daemon tests have a complete, sorted, disjoint classification', () => {
  const result = verifyDaemonTestClassification(process.cwd());
  assert.equal(result.ok, true, result.issues.map((issue) => `${issue.code}: ${issue.subject ?? ''}`).join('\n'));

  const manifest = readDaemonTestClassification(
    path.join(process.cwd(), DEFAULT_DAEMON_TEST_CLASSIFICATION),
  ) as DaemonTestClassification;
  assert.deepEqual(manifest.integration, [...REQUIRED_GIT_INTEGRATION_TESTS]);
  assert.equal(manifest.core.length, 20);
  assert.equal(manifest.integration.length, 3);
  assert.equal(discoverDaemonTestFiles(process.cwd()).length, 23);
});

test('classification mutants reject drift, duplicates, sorting errors, and true Git misclassification', () => {
  const result = runDaemonTestClassificationSyntheticMutants();
  assert.equal(result.ok, true, result.failures.join('\n'));
  assert.equal(result.passed, result.total);
});

test('a newly discovered test fails closed until it is explicitly classified', () => {
  const manifest: DaemonTestClassification = {
    schema_version: 1,
    kind: DAEMON_TEST_CLASSIFICATION_KIND,
    core: ['apps/daemon-ts/tests/core.test.ts'],
    integration: [...REQUIRED_GIT_INTEGRATION_TESTS],
  };
  const discovered = [...manifest.core, ...manifest.integration, 'apps/daemon-ts/tests/new.test.ts'];
  const result = validateDaemonTestClassification(manifest, discovered);
  assert.equal(
    result.issues.some((issue) => (
      issue.code === 'daemon_test_classification_unclassified'
      && issue.subject === 'apps/daemon-ts/tests/new.test.ts'
    )),
    true,
  );
});

test('core Vitest invocation uses the current Node binary and an explicit allowlist only', () => {
  const manifest = readDaemonTestClassification(
    path.join(process.cwd(), DEFAULT_DAEMON_TEST_CLASSIFICATION),
  ) as DaemonTestClassification;
  const invocation = buildCoreVitestInvocation(process.cwd(), manifest);
  assert.equal(invocation.command, process.execPath);
  assert.equal(invocation.args[1], 'run');
  assert.deepEqual(
    invocation.args.slice(2),
    manifest.core.map((entry) => path.posix.relative('apps/daemon-ts', entry)),
  );
  for (const integrationTest of manifest.integration) {
    assert.equal(invocation.args.includes(path.posix.relative('apps/daemon-ts', integrationTest)), false);
  }
});

test('core process guard rejects an absolute Git executable while allowing Node children', () => {
  const locator = process.platform === 'win32'
    ? spawnSync('where.exe', ['git'], { encoding: 'utf8', windowsHide: true })
    : spawnSync('which', ['git'], { encoding: 'utf8' });
  assert.equal(locator.status, 0, locator.stderr);
  const absoluteGit = (locator.stdout.split(/\r?\n/u).find((line) => line.trim().length > 0) ?? '').trim();
  assert.equal(path.isAbsolute(absoluteGit), true, absoluteGit);

  const environment = buildHermeticCoreEnvironment(process.cwd());
  const denied = spawnSync(
    process.execPath,
    ['--input-type=module', '--eval', `import { spawnSync } from 'node:child_process'; spawnSync(${JSON.stringify(absoluteGit)}, ['--version']);`],
    { encoding: 'utf8', env: environment, windowsHide: true },
  );
  assert.notEqual(denied.status, 0);
  assert.match(denied.stderr, /COAGENTIA_HERMETIC_PROCESS_DENY/u);

  const allowed = spawnSync(
    process.execPath,
    ['--input-type=module', '--eval', "import { spawnSync } from 'node:child_process'; const r=spawnSync(process.execPath,['--version']); if(r.status!==0) process.exit(2);"],
    { encoding: 'utf8', env: environment, windowsHide: true },
  );
  assert.equal(allowed.status, 0, allowed.stderr);
});

test('core process guard is forced into Node grandchildren even when NODE_OPTIONS is removed', () => {
  const locator = process.platform === 'win32'
    ? spawnSync('where.exe', ['git'], { encoding: 'utf8', windowsHide: true })
    : spawnSync('which', ['git'], { encoding: 'utf8' });
  assert.equal(locator.status, 0, locator.stderr);
  const absoluteGit = (locator.stdout.split(/\r?\n/u).find((line) => line.trim().length > 0) ?? '').trim();
  const innerScript = [
    "import { spawnSync } from 'node:child_process';",
    `spawnSync(${JSON.stringify(absoluteGit)}, ['--version']);`,
  ].join('\n');
  const outerScript = [
    "import { spawnSync } from 'node:child_process';",
    'const env = { ...process.env };',
    'delete env.NODE_OPTIONS;',
    'delete env.COAGENTIA_HERMETIC_CORE;',
    `const child = spawnSync(process.execPath, ['--input-type=module', '--eval', ${JSON.stringify(innerScript)}], { encoding: 'utf8', env });`,
    "if (child.status === 0 || !child.stderr.includes('COAGENTIA_HERMETIC_PROCESS_DENY')) {",
    "  process.stderr.write(`grandchild bypass: status=${child.status} stderr=${child.stderr}`);",
    '  process.exit(91);',
    '}',
  ].join('\n');
  const result = spawnSync(
    process.execPath,
    ['--input-type=module', '--eval', outerScript],
    { encoding: 'utf8', env: buildHermeticCoreEnvironment(process.cwd()), windowsHide: true },
  );
  assert.equal(result.status, 0, result.stderr);
});

test('core process guard is forced into Workers even when execArgv is emptied', () => {
  const locator = process.platform === 'win32'
    ? spawnSync('where.exe', ['git'], { encoding: 'utf8', windowsHide: true })
    : spawnSync('which', ['git'], { encoding: 'utf8' });
  assert.equal(locator.status, 0, locator.stderr);
  const absoluteGit = (locator.stdout.split(/\r?\n/u).find((line) => line.trim().length > 0) ?? '').trim();
  const parentScript = [
    "import { Worker } from 'node:worker_threads';",
    "const workerScript = `const { spawnSync } = require('node:child_process'); spawnSync(process.env.COAGENTIA_REAL_TOOL, ['--version']);`;",
    `new Worker(workerScript, { eval: true, execArgv: [], env: { COAGENTIA_REAL_TOOL: ${JSON.stringify(absoluteGit)} } });`,
  ].join('\n');
  const result = spawnSync(
    process.execPath,
    ['--input-type=module', '--eval', parentScript],
    { encoding: 'utf8', env: buildHermeticCoreEnvironment(process.cwd()), windowsHide: true },
  );
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /COAGENTIA_HERMETIC_PROCESS_DENY/u);
});

test('core process guard rejects shell expansion hiding an absolute Git executable', () => {
  const locator = process.platform === 'win32'
    ? spawnSync('where.exe', ['git'], { encoding: 'utf8', windowsHide: true })
    : spawnSync('which', ['git'], { encoding: 'utf8' });
  assert.equal(locator.status, 0, locator.stderr);
  const absoluteGit = (locator.stdout.split(/\r?\n/u).find((line) => line.trim().length > 0) ?? '').trim();
  const shellCommand = process.platform === 'win32'
    ? '"%COAGENTIA_REAL_TOOL%" --version'
    : '"$COAGENTIA_REAL_TOOL" --version';
  const script = [
    "import { execSync } from 'node:child_process';",
    `execSync(${JSON.stringify(shellCommand)}, { env: { ...process.env, COAGENTIA_REAL_TOOL: ${JSON.stringify(absoluteGit)} } });`,
  ].join('\n');
  const result = spawnSync(
    process.execPath,
    ['--input-type=module', '--eval', script],
    { encoding: 'utf8', env: buildHermeticCoreEnvironment(process.cwd()), windowsHide: true },
  );
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /COAGENTIA_HERMETIC_PROCESS_DENY/u);
});

test('P0 mother gate and Git integration own classification plus the full reporter gate', () => {
  const packageJson = JSON.parse(fs.readFileSync(path.join(process.cwd(), 'package.json'), 'utf8')) as {
    scripts: Record<string, string>;
  };
  assert.match(packageJson.scripts['verify:p0:git'] ?? '', /p0:daemon-tests:verify/u);
  assert.match(packageJson.scripts['verify:p0:git'] ?? '', /verify:ts-test-targets/u);
  assert.match(packageJson.scripts['verify:p0'] ?? '', /verify:p0:git/u);

  const workflow = fs.readFileSync(
    path.join(process.cwd(), '.github', 'workflows', 'ts-migration-p0.yml'),
    'utf8',
  );
  const hermetic = workflow.split(/^  git-browser-integration-windows:/mu, 1)[0] ?? '';
  const integration = workflow.split(/^  git-browser-integration-windows:/mu)[1]?.split(/^  legacy-oracle-windows:/mu, 1)[0] ?? '';
  assert.match(hermetic, /pnpm p0:daemon-tests:core/u);
  assert.doesNotMatch(hermetic, /@coagentia\/daemon-ts[^\n]*test/u);
  assert.match(integration, /pnpm verify:p0:git/u);
  assert.equal((workflow.match(/uses: actions\/checkout@v6\s+with:\s+fetch-depth: 0/gu) ?? []).length, 3);
  assert.match(workflow, /python-version: '3\.14\.0'/u);
  assert.doesNotMatch(workflow, /python-version: '3\.14'/u);
});

test('Windows hermetic workflow uses native exit-86 guards and proves interception from Node', () => {
  const workflow = fs.readFileSync(
    path.join(process.cwd(), '.github', 'workflows', 'ts-migration-p0.yml'),
    'utf8',
  );
  const hermetic = workflow.split(/^  git-browser-integration-windows:/mu, 1)[0] ?? '';
  for (const command of ['python', 'python3', 'py', 'uv', 'pip', 'pip3', 'node-gyp', 'git']) {
    assert.match(hermetic, new RegExp(`['\"]${command}['\"]`, 'u'));
  }
  assert.match(hermetic, /Add-Type/u);
  assert.match(hermetic, /-OutputType ConsoleApplication/u);
  assert.match(hermetic, /WindowsPowerShell\/v1\.0\/powershell\.exe/u);
  assert.match(hermetic, /return 86;/u);
  assert.match(hermetic, /Copy-Item[^\n]+\$name\.exe/u);
  assert.match(hermetic, /Set-Content[^\n]+\$name\.cmd/u);
  assert.match(hermetic, /spawnSync\(command/u);
  assert.match(hermetic, /result\.status !== 86/u);
  assert.match(hermetic, /spawnSync\(process\.execPath/u);
  assert.doesNotMatch(hermetic, /Join-Path \$denyRoot ['"]node\.(?:exe|cmd)['"]/u);
});
