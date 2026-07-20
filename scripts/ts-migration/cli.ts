#!/usr/bin/env node

import * as path from 'node:path';
import { pathToFileURL } from 'node:url';
import {
  formatValidationResult,
  isNonEmptyString,
  isRecord,
  parseJson,
  parseJsonlRecords,
  readText,
  stableStringify,
  validationResult,
  verifyP0EvidenceOnlyRepositoryState,
  writeText,
} from './shared.ts';
import {
  buildOracleManifest,
  collectOracleManifestForRepository,
  runOracleSyntheticMutants,
  validateOracleManifest,
  verifyOracleCollectionForRepository,
} from './oracle.ts';
import {
  buildInitialTestLedger,
  runLedgerSyntheticMutants,
  verifyTestLedgerDocument,
} from './test-ledger.ts';
import {
  assertInventoryGenerationBaseline,
  inventoryTemplate,
  resolveInventoryBaseline,
  runInventorySyntheticMutants,
  scanMigrationInventory,
  verifyMigrationInventoryDocument,
} from './migration-inventory.ts';
import { runAuthoritySyntheticMutants, verifyPlanAuthority } from './authority.ts';
import { buildP0MigrationInventory } from './inventory-policy.ts';
import {
  collectTsTargetManifest,
  runTargetCollectionSyntheticMutants,
  verifyCurrentTsTargets,
} from './target-collection.ts';

interface ParsedArgs {
  values: Map<string, string[]>;
  booleans: Set<string>;
  positionals: string[];
}

interface StructuredInput {
  format: 'json' | 'jsonl';
  value: unknown;
}

const BOOLEAN_OPTIONS = new Set(['help', 'json', 'list', 'self-test', 'baseline']);

function parseArgs(argv: string[]): ParsedArgs {
  const parsed: ParsedArgs = { values: new Map(), booleans: new Set(), positionals: [] };
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index]!;
    // pnpm forwards its conventional `--` separator to Node on Windows.
    if (token === '--') continue;
    if (!token.startsWith('--')) {
      parsed.positionals.push(token);
      continue;
    }
    const equalsAt = token.indexOf('=');
    const name = token.slice(2, equalsAt < 0 ? undefined : equalsAt);
    if (name.length === 0) throw new Error('空选项名');
    if (BOOLEAN_OPTIONS.has(name)) {
      if (equalsAt >= 0) throw new Error(`--${name} 是布尔选项，不接受值`);
      parsed.booleans.add(name);
      continue;
    }
    const value = equalsAt >= 0 ? token.slice(equalsAt + 1) : argv[++index];
    if (value === undefined || value.startsWith('--')) throw new Error(`--${name} 缺少值`);
    const values = parsed.values.get(name) ?? [];
    values.push(value);
    parsed.values.set(name, values);
  }
  return parsed;
}

function one(parsed: ParsedArgs, name: string, required = false): string | undefined {
  const values = parsed.values.get(name) ?? [];
  if (values.length > 1) throw new Error(`--${name} 只能出现一次`);
  const value = values[0];
  if (required && !isNonEmptyString(value)) throw new Error(`缺少 --${name}`);
  return value;
}

function assertOptions(parsed: ParsedArgs, allowedValues: readonly string[], allowedBooleans: readonly string[]): void {
  const valueSet = new Set(allowedValues);
  const booleanSet = new Set(allowedBooleans);
  for (const key of parsed.values.keys()) {
    if (!valueSet.has(key)) throw new Error(`未知选项 --${key}`);
  }
  for (const key of parsed.booleans) {
    if (!booleanSet.has(key)) throw new Error(`未知选项 --${key}`);
  }
  if (parsed.positionals.length > 0) throw new Error(`未知位置参数: ${parsed.positionals.join(' ')}`);
}

function parseEnvironment(values: readonly string[]): Record<string, string> {
  const environment: Record<string, string> = {};
  for (const value of values) {
    const equalsAt = value.indexOf('=');
    if (equalsAt <= 0) throw new Error(`--env 必须为 KEY=VALUE: ${value}`);
    const key = value.slice(0, equalsAt).trim();
    if (key.length === 0 || Object.hasOwn(environment, key)) throw new Error(`--env key 为空或重复: ${key}`);
    environment[key] = value.slice(equalsAt + 1);
  }
  return environment;
}

function readStructuredInput(filePath: string): StructuredInput {
  const text = readText(filePath);
  if (filePath !== '-' && path.extname(filePath).toLowerCase() === '.jsonl') {
    return { format: 'jsonl', value: parseJsonlRecords(text, filePath) };
  }
  return { format: 'json', value: parseJson(text, filePath) };
}

function inventoryDocumentBaseline(input: StructuredInput): string {
  const header = input.format === 'json'
    ? input.value
    : Array.isArray(input.value) ? input.value[0] : undefined;
  if (!isRecord(header) || !isNonEmptyString(header.baseline_sha)) {
    throw new Error('inventory document header is missing baseline_sha');
  }
  return header.baseline_sha;
}

function printHelp(): void {
  process.stdout.write(`CoAgentia TS migration machine checks\n\n`);
  process.stdout.write(`Commands:\n`);
  process.stdout.write(`  collect-pytest-oracle --repo <path> --output <manifest.json|-> --baseline-sha <sha|HEAD>\n`);
  process.stdout.write(`  verify-oracle-collection --repo <path> --oracle <manifest.json> [--json]\n`);
  process.stdout.write(`  build-oracle-collection --input <pytest.txt|-> --output <manifest.json|-> --baseline-sha <sha> [--command <text>] [--env KEY=VALUE ...]\n`);
  process.stdout.write(`  build-oracle-collection --self-test [--json]\n`);
  process.stdout.write(`  build-test-ledger --oracle <manifest.json> --output <ledger.json|-> --owner <name>\n`);
  process.stdout.write(`  verify-test-ledger --oracle <manifest.json> --ledger <ledger.json|jsonl> --baseline [--json]\n`);
  process.stdout.write(`  verify-test-ledger --repo <path> --oracle <manifest.json> --ledger <ledger.json|jsonl> --targets <ts-tests.json> [--wave <wave> ...] [--json]\n`);
  process.stdout.write(`  verify-test-ledger --self-test [--json]\n`);
  process.stdout.write(`  collect-ts-test-targets --repo <path> --output <manifest.json|-> --baseline-sha <sha|HEAD>\n`);
  process.stdout.write(`  verify-ts-test-targets --repo <path> --targets <manifest.json> [--json]\n`);
  process.stdout.write(`  verify-ts-test-targets --self-test [--json]\n`);
  process.stdout.write(`  verify-migration-inventory --repo <path> --inventory <inventory.json|jsonl> [--baseline-sha <sha>] [--completed-phase <phase> ...] [--json]\n`);
  process.stdout.write(`  build-migration-inventory --repo <path> --baseline-sha <sha|HEAD> --output <inventory.json|->\n`);
  process.stdout.write(`  verify-migration-inventory --repo <path> --baseline-sha <sha|HEAD> --list\n`);
  process.stdout.write(`  verify-migration-inventory --self-test [--json]\n`);
  process.stdout.write(`  verify-plan-authority --repo <path> [--json]\n`);
  process.stdout.write(`  verify-plan-authority --self-test [--json]\n`);
}

function collectPytestOracleCommand(parsed: ParsedArgs): number {
  assertOptions(parsed, ['repo', 'output', 'baseline-sha'], ['help']);
  if (parsed.booleans.has('help')) { printHelp(); return 0; }
  const repo = one(parsed, 'repo', true)!;
  const output = one(parsed, 'output', true)!;
  const baselineSha = one(parsed, 'baseline-sha', true)!;
  const manifest = collectOracleManifestForRepository(repo, baselineSha);
  writeText(output, stableStringify(manifest));
  if (output !== '-') {
    process.stdout.write(`collect-pytest-oracle: PASS (${manifest.nodeid_count}, ${manifest.nodeids_sha256})\n`);
  }
  return 0;
}

function verifyOracleCollectionCommand(parsed: ParsedArgs): number {
  assertOptions(parsed, ['repo', 'oracle'], ['help', 'json']);
  if (parsed.booleans.has('help')) { printHelp(); return 0; }
  const repo = one(parsed, 'repo', true)!;
  const oraclePath = one(parsed, 'oracle', true)!;
  const oracle = parseJson(readText(oraclePath), oraclePath);
  const frozenResult = validateOracleManifest(oracle);
  if (!frozenResult.ok) {
    process.stdout.write(
      parsed.booleans.has('json')
        ? stableStringify(frozenResult)
        : formatValidationResult('verify-oracle-collection', frozenResult),
    );
    return 1;
  }
  const result = verifyOracleCollectionForRepository(repo, oracle);
  process.stdout.write(
    parsed.booleans.has('json')
      ? stableStringify(result)
      : formatValidationResult('verify-oracle-collection', result),
  );
  return result.ok ? 0 : 1;
}

function outputSynthetic(name: string, result: { ok: boolean; passed: number; total: number; failures: string[] }, json: boolean): number {
  if (json) process.stdout.write(stableStringify({ check: name, ...result }));
  else {
    process.stdout.write(`${name}: ${result.ok ? 'PASS' : 'FAIL'} (${result.passed}/${result.total})\n`);
    for (const failure of result.failures) process.stdout.write(`- ${failure}\n`);
  }
  return result.ok ? 0 : 1;
}

function buildOracleCommand(parsed: ParsedArgs): number {
  assertOptions(parsed, ['input', 'output', 'baseline-sha', 'command', 'env'], ['help', 'json', 'self-test']);
  if (parsed.booleans.has('help')) { printHelp(); return 0; }
  if (parsed.booleans.has('self-test')) {
    return outputSynthetic('build-oracle-collection synthetic mutants', runOracleSyntheticMutants(), parsed.booleans.has('json'));
  }
  const input = one(parsed, 'input', true)!;
  const output = one(parsed, 'output', true)!;
  const baselineSha = one(parsed, 'baseline-sha', true)!;
  const collectCommand = one(parsed, 'command') ?? 'uv run pytest --collect-only -q';
  const environment = parseEnvironment(parsed.values.get('env') ?? []);
  const manifest = buildOracleManifest(readText(input), { baselineSha, collectCommand, environment });
  writeText(output, stableStringify(manifest));
  if (output !== '-') process.stdout.write(`build-oracle-collection: PASS (${manifest.nodeid_count}, ${manifest.nodeids_sha256})\n`);
  return 0;
}

function buildTestLedgerCommand(parsed: ParsedArgs): number {
  assertOptions(parsed, ['oracle', 'output', 'owner'], ['help']);
  if (parsed.booleans.has('help')) { printHelp(); return 0; }
  const oraclePath = one(parsed, 'oracle', true)!;
  const output = one(parsed, 'output', true)!;
  const owner = one(parsed, 'owner', true)!;
  const oracle = parseJson(readText(oraclePath), oraclePath);
  const ledger = buildInitialTestLedger(oracle, owner);
  writeText(output, stableStringify(ledger));
  if (output !== '-') process.stdout.write(`build-test-ledger: PASS (${ledger.entries.length} pending rows)\n`);
  return 0;
}

function verifyLedgerCommand(parsed: ParsedArgs): number {
  assertOptions(parsed, ['repo', 'oracle', 'ledger', 'targets', 'wave'], ['help', 'json', 'self-test', 'baseline']);
  if (parsed.booleans.has('help')) { printHelp(); return 0; }
  const json = parsed.booleans.has('json');
  if (parsed.booleans.has('self-test')) return outputSynthetic('verify-test-ledger synthetic mutants', runLedgerSyntheticMutants(), json);
  const oraclePath = one(parsed, 'oracle', true)!;
  const ledgerPath = one(parsed, 'ledger', true)!;
  const baseline = parsed.booleans.has('baseline');
  const waves = new Set(parsed.values.get('wave') ?? []);
  if (baseline && waves.size > 0) throw new Error('--baseline 与 --wave 互斥');
  const targetPath = one(parsed, 'targets', !baseline);
  const oracle = parseJson(readText(oraclePath), oraclePath);
  const ledger = readStructuredInput(ledgerPath);
  const targets = targetPath === undefined ? undefined : parseJson(readText(targetPath), targetPath);
  const ledgerResult = verifyTestLedgerDocument(
    oracle,
    ledger.value,
    ledger.format,
    targets,
    baseline
      ? { mode: 'baseline', repo: one(parsed, 'repo') ?? '.' }
      : { mode: 'strict', waves, repo: one(parsed, 'repo') ?? '.' },
  );
  const reporterResult = baseline
    ? { ok: true, issues: [] }
    : verifyCurrentTsTargets(one(parsed, 'repo') ?? '.', targets);
  const result = {
    ok: ledgerResult.ok && reporterResult.ok,
    issues: [...ledgerResult.issues, ...reporterResult.issues],
  };
  process.stdout.write(json ? stableStringify(result) : formatValidationResult('verify-test-ledger', result));
  return result.ok ? 0 : 1;
}

function collectTsTargetsCommand(parsed: ParsedArgs): number {
  assertOptions(parsed, ['repo', 'output', 'baseline-sha'], ['help']);
  if (parsed.booleans.has('help')) { printHelp(); return 0; }
  const repo = one(parsed, 'repo') ?? '.';
  const output = one(parsed, 'output', true)!;
  const baselineSha = one(parsed, 'baseline-sha', true)!;
  const manifest = collectTsTargetManifest(repo, baselineSha);
  writeText(output, stableStringify(manifest));
  if (output !== '-') {
    process.stdout.write(`collect-ts-test-targets: PASS (${manifest.test_id_count}, ${manifest.test_ids_sha256})\n`);
  }
  return 0;
}

function verifyTsTargetsCommand(parsed: ParsedArgs): number {
  assertOptions(parsed, ['repo', 'targets'], ['help', 'json', 'self-test']);
  if (parsed.booleans.has('help')) { printHelp(); return 0; }
  const json = parsed.booleans.has('json');
  if (parsed.booleans.has('self-test')) {
    return outputSynthetic('verify-ts-test-targets synthetic mutants', runTargetCollectionSyntheticMutants(), json);
  }
  const repo = one(parsed, 'repo') ?? '.';
  const targetPath = one(parsed, 'targets', true)!;
  const frozen = parseJson(readText(targetPath), targetPath);
  const result = verifyCurrentTsTargets(repo, frozen);
  process.stdout.write(json ? stableStringify(result) : formatValidationResult('verify-ts-test-targets', result));
  return result.ok ? 0 : 1;
}

function verifyInventoryCommand(parsed: ParsedArgs): number {
  assertOptions(parsed, ['repo', 'baseline-sha', 'inventory', 'completed-phase'], ['help', 'json', 'list', 'self-test']);
  if (parsed.booleans.has('help')) { printHelp(); return 0; }
  const json = parsed.booleans.has('json');
  if (parsed.booleans.has('self-test')) return outputSynthetic('verify-migration-inventory synthetic mutants', runInventorySyntheticMutants(), json);
  const repo = one(parsed, 'repo') ?? '.';
  const scan = scanMigrationInventory(repo);
  if (parsed.booleans.has('list')) {
    const baselineSha = resolveInventoryBaseline(repo, one(parsed, 'baseline-sha', true)!);
    assertInventoryGenerationBaseline(scan, baselineSha);
    process.stdout.write(stableStringify(inventoryTemplate(scan, baselineSha)));
    return scan.issues.length === 0 ? 0 : 1;
  }
  const inventoryPath = one(parsed, 'inventory', true)!;
  const inventory = readStructuredInput(inventoryPath);
  const baselineSha = resolveInventoryBaseline(
    repo,
    one(parsed, 'baseline-sha') ?? inventoryDocumentBaseline(inventory),
  );
  const completed = new Set(parsed.values.get('completed-phase') ?? []);
  const inventoryResult = verifyMigrationInventoryDocument(scan, inventory.value, inventory.format, baselineSha, completed);
  const history = verifyP0EvidenceOnlyRepositoryState(repo, baselineSha, 'inventory');
  const result = validationResult([...history.issues, ...inventoryResult.issues]);
  process.stdout.write(json ? stableStringify(result) : formatValidationResult('verify-migration-inventory', result));
  return result.ok ? 0 : 1;
}

function buildInventoryCommand(parsed: ParsedArgs): number {
  assertOptions(parsed, ['repo', 'baseline-sha', 'output'], ['help']);
  if (parsed.booleans.has('help')) { printHelp(); return 0; }
  const repo = one(parsed, 'repo') ?? '.';
  const baselineSha = resolveInventoryBaseline(repo, one(parsed, 'baseline-sha', true)!);
  const output = one(parsed, 'output', true)!;
  const scan = scanMigrationInventory(repo);
  assertInventoryGenerationBaseline(scan, baselineSha);
  if (scan.issues.length > 0) {
    process.stdout.write(formatValidationResult('build-migration-inventory', { ok: false, issues: scan.issues }));
    return 1;
  }
  const document = buildP0MigrationInventory(scan, baselineSha);
  writeText(output, stableStringify(document));
  if (output !== '-') process.stdout.write(`build-migration-inventory: PASS (${document.entries.length} entries)\n`);
  return 0;
}

function verifyAuthorityCommand(parsed: ParsedArgs): number {
  assertOptions(parsed, ['repo'], ['help', 'json', 'self-test']);
  if (parsed.booleans.has('help')) { printHelp(); return 0; }
  const json = parsed.booleans.has('json');
  if (parsed.booleans.has('self-test')) {
    return outputSynthetic('verify-plan-authority synthetic mutants', runAuthoritySyntheticMutants(), json);
  }
  const repo = one(parsed, 'repo') ?? '.';
  const result = verifyPlanAuthority(repo);
  process.stdout.write(json ? stableStringify(result) : formatValidationResult('verify-plan-authority', result));
  return result.ok ? 0 : 1;
}

export function main(argv: string[]): number {
  if (argv.length === 0 || argv[0] === '--help' || argv[0] === '-h') {
    printHelp();
    return 0;
  }
  const command = argv[0]!;
  const parsed = parseArgs(argv.slice(1));
  if (command === 'collect-pytest-oracle') return collectPytestOracleCommand(parsed);
  if (command === 'verify-oracle-collection') return verifyOracleCollectionCommand(parsed);
  if (command === 'build-oracle-collection') return buildOracleCommand(parsed);
  if (command === 'build-test-ledger') return buildTestLedgerCommand(parsed);
  if (command === 'verify-test-ledger') return verifyLedgerCommand(parsed);
  if (command === 'collect-ts-test-targets') return collectTsTargetsCommand(parsed);
  if (command === 'verify-ts-test-targets') return verifyTsTargetsCommand(parsed);
  if (command === 'build-migration-inventory') return buildInventoryCommand(parsed);
  if (command === 'verify-migration-inventory') return verifyInventoryCommand(parsed);
  if (command === 'verify-plan-authority') return verifyAuthorityCommand(parsed);
  throw new Error(`未知命令: ${command}`);
}

const argv1 = process.argv[1];
const isDirectRun = (() => {
  if (argv1 === undefined) return false;
  const entry = pathToFileURL(path.resolve(argv1)).href;
  return process.platform === 'win32' ? entry.toLowerCase() === import.meta.url.toLowerCase() : entry === import.meta.url;
})();
if (isDirectRun) {
  try {
    process.exitCode = main(process.argv.slice(2));
  } catch (error: unknown) {
    process.stderr.write(`ts-migration: ${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 2;
  }
}
