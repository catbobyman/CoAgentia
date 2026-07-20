import { createHash } from 'node:crypto';
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
  asOracleManifest,
  buildOracleManifest,
  normalizeNodeId,
  validateOracleManifest,
  type OracleCollectionManifest,
} from './oracle.ts';

export const TARGET_COLLECTION_KIND = 'coagentia.ts-test-collection';
export const TEST_LEDGER_KIND = 'coagentia.test-ledger';

export interface TargetTestRecord {
  id: string;
  suite: string;
  file: string;
  status: 'passed' | 'skipped';
}

export interface TargetCollectionManifest {
  schema_version: 1;
  kind: typeof TARGET_COLLECTION_KIND;
  collector: 'vitest-json-reporter';
  baseline_sha: string;
  runner: { name: 'vitest'; version: string };
  commands: Array<{ suite: string; argv: string[]; exit_code: 0 }>;
  test_id_count: number;
  test_ids_sha256: string;
  test_ids: string[];
  tests: TargetTestRecord[];
}

export interface LedgerEntry {
  legacy_nodeid: string;
  subsystem: string;
  wave: string;
  status: 'pending' | 'mapped' | 'accepted';
  disposition?: 'port' | 'replace' | 'retire' | null;
  target_test_id?: string | null;
  target_group_reason?: string;
  rationale: string;
  evidence: string | string[];
  owner: string;
  reviewer?: string | null;
  allocation_evidence?: string | string[];
  allocation_reviewer?: string | null;
  allocation_approval?: DecisionApproval | null;
  retire_approval?: RetireApproval | null;
}

export interface DecisionApproval {
  approved_by: string;
  decision_ref: string;
  decision_sha256: string;
  approved_at: string;
}

export type RetireApproval = DecisionApproval;

export interface VerifyLedgerOptions {
  mode: 'baseline' | 'strict';
  waves?: ReadonlySet<string>;
  repo?: string;
}

export interface TestLedgerDocument {
  schema_version: 1;
  kind: typeof TEST_LEDGER_KIND;
  oracle_baseline_sha: string;
  oracle_nodeids_sha256: string;
  entries: LedgerEntry[];
}

function evidencePresent(value: unknown): boolean {
  if (isNonEmptyString(value)) return true;
  return Array.isArray(value) && value.length > 0 && value.every(isNonEmptyString);
}

function validateAllocationReview(
  value: Record<string, unknown>,
  issues: ValidationIssue[],
  subject: string,
): void {
  if (!evidencePresent(value.allocation_evidence)) {
    issues.push({
      code: 'ledger_allocation_evidence_missing',
      message: 'allocation changes and accepted entries require allocation_evidence',
      subject,
    });
  }
  if (!isNonEmptyString(value.allocation_reviewer)) {
    issues.push({
      code: 'ledger_allocation_reviewer_missing',
      message: 'allocation changes and accepted entries require allocation_reviewer',
      subject,
    });
  } else if (sameIdentity(value.allocation_reviewer, value.owner) || sameIdentity(value.allocation_reviewer, value.reviewer)) {
    issues.push({
      code: 'ledger_allocation_reviewer_not_independent',
      message: 'allocation_reviewer must be independent from owner and reviewer',
      subject,
    });
  }
}

const SHA256_RE = /^[0-9a-f]{64}$/u;
const UTC_TIMESTAMP_RE = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{3}))?Z$/u;
const MIGRATION_OWNER_ID = 'owner';

function sameIdentity(left: unknown, right: unknown): boolean {
  return isNonEmptyString(left)
    && isNonEmptyString(right)
    && left.trim().toLowerCase() === right.trim().toLowerCase();
}

function isCanonicalUtcTimestamp(value: unknown): value is string {
  if (!isNonEmptyString(value) || !UTC_TIMESTAMP_RE.test(value)) return false;
  const instant = new Date(value);
  if (!Number.isFinite(instant.getTime())) return false;
  const canonical = instant.toISOString();
  return value === canonical || (canonical.endsWith('.000Z') && value === canonical.replace('.000Z', 'Z'));
}

function fileSha256(filePath: string): string {
  return createHash('sha256').update(fs.readFileSync(filePath)).digest('hex');
}

function parseDecisionFields(text: string): Map<string, string> {
  const fields = new Map<string, string>();
  for (const line of text.split(/\r?\n/u)) {
    const match = /^\s*(decision|approved_by|legacy_nodeid|subsystem|wave|approved_at)\s*:\s*(.+?)\s*$/u.exec(line);
    if (match !== null) fields.set(match[1]!, match[2]!);
  }
  return fields;
}

function validateDecisionApproval(
  value: unknown,
  decision: 'retire' | 'allocation',
  legacyNodeId: string,
  subsystem: string,
  wave: string,
  owner: unknown,
  reviewer: unknown,
  allocationReviewer: unknown,
  repo: string,
  issues: ValidationIssue[],
  subject: string,
): void {
  if (!isRecord(value)) {
    issues.push({
      code: decision === 'retire' ? 'ledger_retire_unapproved' : 'ledger_allocation_unapproved',
      message: `${decision} requires a structured, repository-bound approval`,
      subject,
    });
    return;
  }
  if (!isNonEmptyString(value.approved_by)) {
    issues.push({ code: 'ledger_decision_approver_missing', message: 'approval.approved_by is required', subject });
  } else if (!sameIdentity(value.approved_by, MIGRATION_OWNER_ID)) {
    issues.push({ code: 'ledger_decision_approver_not_owner', message: `approved_by must equal frozen owner identity ${MIGRATION_OWNER_ID}`, subject });
  } else if (
    sameIdentity(value.approved_by, owner)
    || sameIdentity(value.approved_by, reviewer)
    || sameIdentity(value.approved_by, allocationReviewer)
  ) {
    issues.push({
      code: 'ledger_decision_approver_not_independent',
      message: 'decision approver must be independent from implementer/reviewer/allocation reviewer',
      subject,
    });
  }
  if (!isNonEmptyString(value.decision_ref)) {
    issues.push({ code: 'ledger_decision_ref_missing', message: 'approval.decision_ref is required', subject });
    return;
  }
  if (typeof value.decision_sha256 !== 'string' || !SHA256_RE.test(value.decision_sha256)) {
    issues.push({
      code: 'ledger_decision_hash_invalid',
      message: 'approval.decision_sha256 must be 64 lowercase hexadecimal digits',
      subject,
    });
  }
  if (!isCanonicalUtcTimestamp(value.approved_at)) {
    issues.push({
      code: 'ledger_decision_approved_at_invalid',
      message: 'approval.approved_at must be a valid canonical UTC timestamp',
      subject,
    });
  }

  const relative = value.decision_ref.trim().replaceAll('\\', '/');
  if (
    relative.includes('#')
    || relative === '.git'
    || relative.startsWith('.git/')
    || path.posix.isAbsolute(relative)
    || /^[A-Za-z]:/u.test(relative)
  ) {
    issues.push({ code: 'ledger_decision_ref_invalid', message: 'decision_ref must be a fragment-free repo-relative path', subject });
    return;
  }
  const repoRoot = fs.realpathSync(path.resolve(repo));
  const absolute = path.resolve(repoRoot, ...relative.split('/'));
  const guard = path.relative(repoRoot, absolute);
  if (
    guard.startsWith('..')
    || path.isAbsolute(guard)
    || !fs.existsSync(absolute)
    || fs.lstatSync(absolute).isSymbolicLink()
    || !fs.statSync(absolute).isFile()
  ) {
    issues.push({ code: 'ledger_decision_missing', message: 'decision_ref does not resolve to a repository file', subject: relative });
    return;
  }
  if (typeof value.decision_sha256 === 'string' && fileSha256(absolute) !== value.decision_sha256) {
    issues.push({ code: 'ledger_decision_hash_mismatch', message: 'decision artifact SHA-256 mismatch', subject: relative });
  }
  const fields = parseDecisionFields(fs.readFileSync(absolute, 'utf8'));
  const contentMatches = fields.get('decision') === decision
    && sameIdentity(fields.get('approved_by'), value.approved_by)
    && fields.get('legacy_nodeid') === legacyNodeId
    && fields.get('approved_at') === value.approved_at
    && (decision !== 'allocation' || (fields.get('subsystem') === subsystem && fields.get('wave') === wave));
  if (!contentMatches) {
    issues.push({ code: 'ledger_decision_content_mismatch', message: 'decision artifact scope/owner/time does not match ledger approval', subject: relative });
  }
}

export type LedgerDocumentFormat = 'json' | 'jsonl';

function validateLedgerHeader(
  header: Record<string, unknown>,
  oracleValue: unknown,
  issues: ValidationIssue[],
): void {
  if (header.schema_version !== 1) {
    issues.push({ code: 'ledger_document_schema_version', message: 'ledger schema_version 必须为 1' });
  }
  if (header.kind !== TEST_LEDGER_KIND) {
    issues.push({ code: 'ledger_document_kind', message: `ledger kind 必须为 ${TEST_LEDGER_KIND}` });
  }
  const baselineSha = header.oracle_baseline_sha;
  if (!isNonEmptyString(baselineSha) || !/^[0-9a-f]{7,64}$/iu.test(baselineSha)) {
    issues.push({ code: 'ledger_document_baseline_sha', message: 'oracle_baseline_sha 必须是 7–64 位十六进制 Git SHA' });
  }
  const oracleHash = header.oracle_nodeids_sha256;
  if (!isNonEmptyString(oracleHash) || !/^[0-9a-f]{64}$/iu.test(oracleHash)) {
    issues.push({ code: 'ledger_document_oracle_hash', message: 'oracle_nodeids_sha256 必须是 64 位十六进制 SHA-256' });
  }
  if (isRecord(oracleValue)) {
    if (baselineSha !== oracleValue.baseline_sha) {
      issues.push({ code: 'ledger_document_baseline_mismatch', message: 'ledger oracle_baseline_sha 与 oracle manifest 不一致' });
    }
    if (oracleHash !== oracleValue.nodeids_sha256) {
      issues.push({ code: 'ledger_document_oracle_hash_mismatch', message: 'ledger oracle_nodeids_sha256 与 oracle manifest 不一致' });
    }
  }
}

export function parseTestLedgerDocument(
  value: unknown,
  oracleValue: unknown,
  format: LedgerDocumentFormat,
): { entries: unknown[]; issues: ValidationIssue[] } {
  const issues: ValidationIssue[] = [];
  if (format === 'json') {
    if (!isRecord(value)) {
      return {
        entries: [],
        issues: [{ code: 'ledger_document_not_object', message: '正式 JSON ledger 必须是带完整 header 的 object，不接受裸 array' }],
      };
    }
    validateLedgerHeader(value, oracleValue, issues);
    if (!Array.isArray(value.entries)) {
      issues.push({ code: 'ledger_document_entries', message: 'ledger entries 必须是 array' });
      return { entries: [], issues };
    }
    return { entries: value.entries, issues };
  }

  if (!Array.isArray(value) || value.length === 0 || !isRecord(value[0])) {
    return { entries: [], issues: [{ code: 'ledger_jsonl_header_missing', message: 'JSONL 首条必须是 header object' }] };
  }
  const header = value[0];
  if (header.record_type !== 'header') {
    issues.push({ code: 'ledger_jsonl_header_missing', message: 'JSONL 首条 record_type 必须为 header' });
  }
  validateLedgerHeader(header, oracleValue, issues);
  const entries: unknown[] = [];
  for (const [index, row] of value.slice(1).entries()) {
    if (!isRecord(row) || row.record_type !== 'entry') {
      issues.push({ code: 'ledger_jsonl_entry_type', message: 'JSONL 后续记录的 record_type 必须为 entry', subject: `line:${index + 2}` });
      continue;
    }
    entries.push(row);
  }
  return { entries, issues };
}

export function verifyTestLedgerDocument(
  oracleValue: unknown,
  ledgerValue: unknown,
  format: LedgerDocumentFormat,
  targetValue: unknown | undefined,
  options: VerifyLedgerOptions,
): ValidationResult {
  const parsed = parseTestLedgerDocument(ledgerValue, oracleValue, format);
  const core = verifyTestLedger(oracleValue, parsed.entries, targetValue, options);
  return validationResult([...parsed.issues, ...core.issues]);
}

export function parseTargetCollection(value: unknown): {
  testIds: string[];
  testStatuses: Map<string, 'passed' | 'skipped'>;
  baselineSha?: string;
  issues: ValidationIssue[];
} {
  const issues: ValidationIssue[] = [];
  if (!isRecord(value)) {
    return {
      testIds: [],
      testStatuses: new Map(),
      issues: [{ code: 'target_not_object', message: 'target collection must be a manifest object; bare arrays are forbidden' }],
    };
  }
  if (value.kind !== TARGET_COLLECTION_KIND) {
    issues.push({ code: 'target_kind', message: `kind 必须为 ${TARGET_COLLECTION_KIND}` });
  }
  if (value.schema_version !== 1) {
    issues.push({ code: 'target_schema_version', message: 'schema_version 必须为 1' });
  }
  if (value.collector !== 'vitest-json-reporter') {
    issues.push({ code: 'target_collector', message: 'collector 必须为 vitest-json-reporter' });
  }
  const baselineSha = isNonEmptyString(value.baseline_sha) ? value.baseline_sha.toLowerCase() : undefined;
  if (baselineSha === undefined || !/^(?:[0-9a-f]{40}|[0-9a-f]{64})$/u.test(baselineSha)) {
    issues.push({ code: 'target_baseline_sha', message: 'baseline_sha 必须是完整的 40 或 64 位十六进制 Git commit SHA' });
  }
  if (!isRecord(value.runner) || value.runner.name !== 'vitest' || !isNonEmptyString(value.runner.version)) {
    issues.push({ code: 'target_runner', message: 'runner 必须记录 vitest 与非空版本' });
  }
  const commandSuites = new Set<string>();
  const commandsValid = Array.isArray(value.commands)
    && value.commands.length > 0
    && value.commands.every((command) => {
      if (!isRecord(command)
        || !isNonEmptyString(command.suite)
        || !Array.isArray(command.argv)
        || command.argv.length === 0
        || command.argv.some((part) => !isNonEmptyString(part))
        || command.exit_code !== 0) return false;
      const argv = command.argv as string[];
      if (!argv.includes('run') || !argv.includes('--reporter=json')) return false;
      const suite = command.suite.trim();
      if (commandSuites.has(suite)) return false;
      commandSuites.add(suite);
      return true;
    });
  if (!commandsValid) {
    issues.push({ code: 'target_commands', message: 'commands 必须记录至少一条 suite/argv/exit_code=0 的 reporter 命令' });
  }
  const rawIds = value.test_ids;
  const declaredCount = value.test_id_count;
  const declaredHash = value.test_ids_sha256;

  if (!Array.isArray(rawIds) || rawIds.length === 0 || rawIds.some((item) => !isNonEmptyString(item))) {
    issues.push({ code: 'target_test_ids', message: 'test_ids 必须是非空字符串数组' });
    return { testIds: [], testStatuses: new Map(), issues };
  }
  const ids = (rawIds as string[]).map((item) => item.trim());
  const counts = new Map<string, number>();
  for (const id of ids) counts.set(id, (counts.get(id) ?? 0) + 1);
  for (const [id, count] of counts) {
    if (count > 1) issues.push({ code: 'target_duplicate_test_id', message: `出现 ${count} 次`, subject: id });
  }
  const sorted = [...ids].sort(compareText);
  if (sorted.some((item, index) => item !== ids[index])) {
    issues.push({ code: 'target_test_ids_not_sorted', message: 'test_ids 必须按稳定字典序排序' });
  }
  if (declaredCount !== ids.length) {
    issues.push({ code: 'target_count_mismatch', message: `test_id_count=${String(declaredCount)}，实际=${ids.length}` });
  }
  const expectedHash = hashStringList(ids);
  if (declaredHash !== expectedHash) {
    issues.push({ code: 'target_hash_mismatch', message: `test_ids_sha256 应为 ${expectedHash}` });
  }
  const testStatuses = new Map<string, 'passed' | 'skipped'>();
  if (!Array.isArray(value.tests) || value.tests.length !== ids.length) {
    issues.push({ code: 'target_tests', message: 'tests 必须与 test_ids 等长并保存 reporter 状态' });
  } else {
    const testIds: string[] = [];
    for (const [index, test] of value.tests.entries()) {
      const subject = `target-test:${index + 1}`;
      if (!isRecord(test)
        || !isNonEmptyString(test.id)
        || !isNonEmptyString(test.suite)
        || !isNonEmptyString(test.file)
        || (test.status !== 'passed' && test.status !== 'skipped')) {
        issues.push({ code: 'target_test_record', message: 'test record 必须含 id/suite/file/passed|skipped', subject });
        continue;
      }
      const id = test.id.trim();
      const status = test.status as 'passed' | 'skipped';
      const file = test.file.trim().replaceAll('\\', '/');
      const idFile = id.split('::', 1)[0] ?? '';
      if (file !== idFile || !commandSuites.has(test.suite.trim())) {
        issues.push({ code: 'target_test_provenance', message: 'test file 必须匹配 id，suite 必须有对应 reporter command', subject });
      }
      testIds.push(id);
      testStatuses.set(id, status);
    }
    if (testIds.length === ids.length && testIds.some((id, index) => id !== ids[index])) {
      issues.push({ code: 'target_tests_id_mismatch', message: 'tests 的有序 id 必须与 test_ids 完全一致' });
    }
  }
  return { testIds: ids, testStatuses, ...(baselineSha === undefined ? {} : { baselineSha }), issues };
}

export function buildTargetCollection(testIds: readonly string[]): TargetCollectionManifest {
  const sorted = [...testIds].map((item) => item.trim()).sort(compareText);
  return {
    schema_version: 1,
    kind: TARGET_COLLECTION_KIND,
    collector: 'vitest-json-reporter',
    baseline_sha: '0123456789abcdef0123456789abcdef01234567',
    runner: { name: 'vitest', version: 'synthetic-fixture' },
    commands: [{ suite: 'synthetic', argv: ['vitest', 'run', '--reporter=json'], exit_code: 0 }],
    test_id_count: sorted.length,
    test_ids_sha256: hashStringList(sorted),
    test_ids: sorted,
    tests: sorted.map((id) => ({
      id,
      suite: 'synthetic',
      file: id.split('::', 1)[0] ?? id,
      status: 'passed',
    })),
  };
}

export function verifyTestLedger(
  oracleValue: unknown,
  ledgerRows: readonly unknown[],
  targetValue: unknown | undefined,
  options: VerifyLedgerOptions = { mode: 'strict' },
): ValidationResult {
  const issues: ValidationIssue[] = [];
  const oracleResult = validateOracleManifest(oracleValue);
  issues.push(...oracleResult.issues);
  const targetResult = options.mode === 'strict'
    ? parseTargetCollection(targetValue)
    : { testIds: [], testStatuses: new Map<string, 'passed' | 'skipped'>(), issues: [] };
  if (options.mode === 'strict') issues.push(...targetResult.issues);
  if (!oracleResult.ok) return validationResult(issues);

  const oracle = asOracleManifest(oracleValue);
  if (options.mode === 'strict' && targetResult.baselineSha !== oracle.baseline_sha) {
    issues.push({
      code: 'target_oracle_baseline_mismatch',
      message: 'TS target collection 与 Python oracle 必须绑定同一 baseline commit',
    });
  }
  const sourceCounts = new Map<string, number>();
  const targetGroups = new Map<string, Array<{ index: number; row: Record<string, unknown> }>>();
  const seenWaves = new Set<string>();

  for (const [index, value] of ledgerRows.entries()) {
    const subject = `entry:${index + 1}`;
    if (!isRecord(value)) {
      issues.push({ code: 'ledger_entry_not_object', message: 'ledger entry 必须是 object', subject });
      continue;
    }
    const source = isNonEmptyString(value.legacy_nodeid) ? normalizeNodeId(value.legacy_nodeid) : '';
    if (source.length === 0) {
      issues.push({ code: 'ledger_source_missing', message: 'legacy_nodeid 缺失或为空', subject });
    } else {
      sourceCounts.set(source, (sourceCounts.get(source) ?? 0) + 1);
    }

    if (!isNonEmptyString(value.subsystem)) issues.push({ code: 'ledger_subsystem_missing', message: 'subsystem 缺失或为空', subject });
    if (!isNonEmptyString(value.wave)) issues.push({ code: 'ledger_wave_missing', message: 'wave 缺失或为空', subject });
    else seenWaves.add(value.wave.trim());
    if (!isNonEmptyString(value.rationale)) issues.push({ code: 'ledger_rationale_missing', message: 'rationale 缺失或为空', subject });
    if (!evidencePresent(value.evidence)) issues.push({ code: 'ledger_evidence_missing', message: 'evidence 必须是非空字符串或非空字符串数组', subject });
    if (!isNonEmptyString(value.owner)) issues.push({ code: 'ledger_owner_missing', message: 'owner 缺失或为空', subject });
    const status = value.status;
    if (status !== 'pending' && status !== 'mapped' && status !== 'accepted') {
      issues.push({ code: 'ledger_status_invalid', message: 'status 必须为 pending/mapped/accepted', subject });
    }
    const disposition = value.disposition;
    const target = isNonEmptyString(value.target_test_id) ? value.target_test_id.trim() : '';
    const subsystem = isNonEmptyString(value.subsystem) ? value.subsystem.trim() : '';
    const wave = isNonEmptyString(value.wave) ? value.wave.trim() : '';
    let defaultAllocation: { subsystem: string; wave: string } | undefined;
    if (source.length > 0) {
      try {
        defaultAllocation = classifyLegacyNodeId(source);
      } catch {
        // Unknown oracle paths are rejected by the global source coverage checks below.
      }
    }
    const allocationChanged = defaultAllocation !== undefined
      && (subsystem !== defaultAllocation.subsystem || wave !== defaultAllocation.wave);
    if (options.mode === 'strict' && target.length > 0 && disposition !== 'retire') {
      const group = targetGroups.get(target) ?? [];
      group.push({ index, row: value });
      targetGroups.set(target, group);
    }

    if (options.mode === 'baseline') {
      if (status !== 'pending') {
        issues.push({ code: 'ledger_baseline_requires_pending', message: '--baseline 只允许 status=pending', subject });
      }
      if (value.disposition !== undefined && value.disposition !== null && value.disposition !== '') {
        issues.push({ code: 'ledger_baseline_has_disposition', message: 'pending baseline 不得预填 disposition', subject });
      }
      if (isNonEmptyString(value.target_test_id)) {
        issues.push({ code: 'ledger_baseline_has_target', message: 'pending baseline 不得伪造 target_test_id', subject });
      }
      if (isNonEmptyString(value.reviewer)) {
        issues.push({ code: 'ledger_baseline_has_reviewer', message: 'pending baseline 尚未评审，不得预填 reviewer', subject });
      }
      if (Object.hasOwn(value, 'retire_approved') || Object.hasOwn(value, 'retire_approval')) {
        issues.push({ code: 'ledger_baseline_has_retire_approval', message: 'pending baseline must not contain any retire approval', subject });
      }
      if (allocationChanged && defaultAllocation !== undefined) {
        issues.push({
          code: 'ledger_baseline_allocation_mismatch',
          message: `pending baseline allocation must remain ${defaultAllocation.subsystem}/${defaultAllocation.wave}`,
          subject,
        });
      }
      if (
        Object.hasOwn(value, 'allocation_evidence')
        || Object.hasOwn(value, 'allocation_reviewer')
        || Object.hasOwn(value, 'allocation_approval')
      ) {
        issues.push({
          code: 'ledger_baseline_has_allocation_approval',
          message: 'pending baseline must not contain allocation review or approval fields',
          subject,
        });
      }
      continue;
    }

    if (allocationChanged) {
      validateAllocationReview(value, issues, subject);
      validateDecisionApproval(
        value.allocation_approval,
        'allocation',
        source,
        subsystem,
        wave,
        value.owner,
        value.reviewer,
        value.allocation_reviewer,
        options.repo ?? process.cwd(),
        issues,
        subject,
      );
    } else if (value.allocation_approval !== undefined && value.allocation_approval !== null) {
      issues.push({
        code: 'ledger_allocation_approval_unnecessary',
        message: 'allocation_approval is only valid when subsystem/wave differs from the frozen path allocation',
        subject,
      });
    }

    const selected = options.waves === undefined || options.waves.size === 0
      || (isNonEmptyString(value.wave) && options.waves.has(value.wave.trim()));
    if (!selected) continue;
    if (status !== 'accepted') {
      issues.push({ code: 'ledger_status_not_accepted', message: 'strict/default/--wave 门只接受 status=accepted', subject });
    }
    if (!isNonEmptyString(value.reviewer)) issues.push({ code: 'ledger_reviewer_missing', message: 'reviewer 缺失或为空', subject });
    if (sameIdentity(value.owner, value.reviewer)) {
      issues.push({ code: 'ledger_reviewer_not_independent', message: 'owner 与 reviewer 不得相同', subject });
    }
    if (status === 'accepted' && !allocationChanged) validateAllocationReview(value, issues, subject);
    if (Object.hasOwn(value, 'retire_approved')) {
      issues.push({
        code: 'ledger_retire_approval_legacy_boolean',
        message: 'retire_approved boolean is forbidden; use structured retire_approval',
        subject,
      });
    }
    if (disposition !== 'port' && disposition !== 'replace' && disposition !== 'retire') {
      issues.push({ code: 'ledger_disposition_invalid', message: 'disposition 必须为 port/replace/retire', subject });
      continue;
    }
    if (disposition === 'retire') {
      if (target.length > 0) issues.push({ code: 'ledger_retire_has_target', message: 'retire 记录不得填写 target_test_id', subject });
      validateDecisionApproval(
        value.retire_approval,
        'retire',
        source,
        subsystem,
        wave,
        value.owner,
        value.reviewer,
        value.allocation_reviewer,
        options.repo ?? process.cwd(),
        issues,
        subject,
      );
    } else if (target.length === 0) {
      issues.push({ code: 'ledger_target_missing', message: `${disposition} 记录必须填写 target_test_id`, subject });
    } else {
      if (!targetResult.testIds.includes(target)) {
        issues.push({ code: 'ledger_target_not_collected', message: 'target_test_id 不在 TS reporter collection 中', subject: target });
      } else if (targetResult.testStatuses.get(target) !== 'passed') {
        issues.push({ code: 'ledger_target_not_passed', message: 'port/replace target 必须在 TS reporter collection 中实际 passed，不接受 skipped', subject: target });
      }
    }
    if (disposition !== 'retire' && value.retire_approval !== undefined && value.retire_approval !== null) {
      issues.push({ code: 'ledger_non_retire_has_retire_approval', message: 'only retire entries may contain retire_approval', subject });
    }
  }

  const oracleSet = new Set(oracle.nodeids);
  for (const nodeid of oracle.nodeids) {
    const count = sourceCounts.get(nodeid) ?? 0;
    if (count === 0) issues.push({ code: 'ledger_source_missing_from_ledger', message: 'oracle nodeid 未登记', subject: nodeid });
    else if (count > 1) issues.push({ code: 'ledger_source_duplicate', message: `oracle nodeid 登记 ${count} 次`, subject: nodeid });
  }
  for (const [nodeid] of sourceCounts) {
    if (!oracleSet.has(nodeid)) issues.push({ code: 'ledger_unknown_source', message: 'legacy_nodeid 不在冻结 oracle collection 中', subject: nodeid });
  }

  for (const [target, group] of targetGroups) {
    if (group.length < 2) continue;
    const reasons = group.map(({ row }) => isNonEmptyString(row.target_group_reason) ? row.target_group_reason.trim() : '');
    if (reasons.some((reason) => reason.length === 0) || new Set(reasons).size !== 1) {
      issues.push({
        code: 'ledger_target_group_unexplained',
        message: `同一 target 映射 ${group.length} 个 legacy case 时，每条必须填写相同的 target_group_reason`,
        subject: target,
      });
    }
  }

  if (options.mode === 'strict' && options.waves !== undefined) {
    for (const wave of options.waves) {
      if (!seenWaves.has(wave)) {
        issues.push({ code: 'ledger_wave_not_found', message: '--wave 未匹配任何 ledger entry（拒绝空范围伪绿）', subject: wave });
      }
    }
  }

  return validationResult(issues);
}

function classifyLegacyNodeId(nodeid: string): { subsystem: string; wave: string } {
  const normalized = normalizeNodeId(nodeid);
  if (normalized.startsWith('packages/contracts/')) return { subsystem: 'contracts', wave: 'P2' };
  if (normalized.startsWith('apps/mock-server/')) return { subsystem: 'mock-server', wave: 'C1' };
  if (normalized.startsWith('apps/daemon/')) return { subsystem: 'daemon', wave: 'A3' };
  if (normalized.startsWith('apps/server/')) return { subsystem: 'server', wave: 'B0-B10' };
  throw new Error(`无法按路径映射 subsystem/wave: ${nodeid}`);
}

export function buildInitialTestLedger(oracleValue: unknown, owner: string): TestLedgerDocument {
  if (!isNonEmptyString(owner)) throw new Error('--owner 缺失或为空');
  const oracle = asOracleManifest(oracleValue);
  const evidence = `oracle:${oracle.baseline_sha}:${oracle.nodeids_sha256}`;
  return {
    schema_version: 1,
    kind: TEST_LEDGER_KIND,
    oracle_baseline_sha: oracle.baseline_sha,
    oracle_nodeids_sha256: oracle.nodeids_sha256,
    entries: oracle.nodeids.map((legacyNodeid) => {
      const classification = classifyLegacyNodeId(legacyNodeid);
      return {
        legacy_nodeid: legacyNodeid,
        subsystem: classification.subsystem,
        wave: classification.wave,
        status: 'pending',
        disposition: null,
        target_test_id: null,
        rationale: 'P0 baseline：映射与处置待实现者及 reviewer 审阅',
        evidence: [evidence],
        owner: owner.trim(),
        reviewer: null,
      };
    }),
  };
}

interface SyntheticCase {
  name: string;
  expectedCode: string;
  mutate: (fixture: SyntheticFixture) => void;
}

interface SyntheticFixture {
  oracle: OracleCollectionManifest;
  ledger: LedgerEntry[];
  targets: TargetCollectionManifest;
  repoRoot: string;
}

function syntheticFixture(): SyntheticFixture {
  const oracle = buildOracleManifest(
    [
      'apps/server/tests/test_alpha.py::test_one',
      'apps/server/tests/test_alpha.py::test_two',
      'apps/daemon/tests/test_beta.py::test_three',
      '3 tests collected in 0.01s',
    ].join('\n'),
    { baselineSha: '0123456789abcdef0123456789abcdef01234567', collectCommand: 'pytest --collect-only -q', environment: {} },
  );
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'coagentia-ledger-'));
  const decisionDirectory = path.join(repoRoot, 'decisions');
  fs.mkdirSync(decisionDirectory, { recursive: true });
  const retireDecisionRef = 'decisions/retire-c.md';
  const approvedAt = '2026-07-20T00:00:00Z';
  const retireDecision = [
    'decision: retire',
    `approved_by: ${MIGRATION_OWNER_ID}`,
    `legacy_nodeid: ${oracle.nodeids[0]!}`,
    `approved_at: ${approvedAt}`,
    '',
  ].join('\n');
  const retireDecisionPath = path.join(repoRoot, ...retireDecisionRef.split('/'));
  fs.writeFileSync(retireDecisionPath, retireDecision, 'utf8');
  return {
    oracle,
    repoRoot,
    targets: buildTargetCollection(['server/alpha.test.ts::one', 'server/alpha.test.ts::two']),
    ledger: [
      {
        legacy_nodeid: oracle.nodeids[1]!, subsystem: 'server', wave: 'B0-B10', status: 'accepted', disposition: 'port',
        target_test_id: 'server/alpha.test.ts::one', rationale: '等价移植', evidence: ['evidence:a'], owner: 'impl-a', reviewer: 'review-a',
        allocation_evidence: ['allocation:a'], allocation_reviewer: 'allocation-review-a',
      },
      {
        legacy_nodeid: oracle.nodeids[2]!, subsystem: 'server', wave: 'B0-B10', status: 'accepted', disposition: 'replace',
        target_test_id: 'server/alpha.test.ts::two', rationale: '黑盒替代', evidence: 'evidence:b', owner: 'impl-b', reviewer: 'review-b',
        allocation_evidence: 'allocation:b', allocation_reviewer: 'allocation-review-b',
      },
      {
        legacy_nodeid: oracle.nodeids[0]!, subsystem: 'daemon', wave: 'A3', status: 'accepted', disposition: 'retire',
        rationale: '职责已被集成门覆盖', evidence: ['evidence:c'], owner: 'impl-c', reviewer: 'review-c',
        allocation_evidence: ['allocation:c'], allocation_reviewer: 'allocation-review-c',
        retire_approval: {
          approved_by: MIGRATION_OWNER_ID,
          decision_ref: retireDecisionRef,
          decision_sha256: fileSha256(retireDecisionPath),
          approved_at: approvedAt,
        },
      },
    ],
  };
}

function runLedgerSyntheticMutantsForFixture(
  baseline: SyntheticFixture,
): { ok: boolean; passed: number; total: number; failures: string[] } {
  const baselineResult = verifyTestLedger(
    baseline.oracle,
    baseline.ledger,
    baseline.targets,
    { mode: 'strict', repo: baseline.repoRoot },
  );
  if (!baselineResult.ok) {
    return { ok: false, passed: 0, total: 1, failures: [`baseline unexpectedly failed: ${baselineResult.issues.map((item) => item.code).join(', ')}`] };
  }
  const cases: SyntheticCase[] = [
    { name: 'missing source', expectedCode: 'ledger_source_missing_from_ledger', mutate: (f) => { f.ledger.splice(0, 1); } },
    { name: 'duplicate source', expectedCode: 'ledger_source_duplicate', mutate: (f) => { f.ledger.push(structuredClone(f.ledger[0]!)); } },
    { name: 'unknown source', expectedCode: 'ledger_unknown_source', mutate: (f) => { f.ledger[0]!.legacy_nodeid = 'tests/test_unknown.py::test_unknown'; } },
    { name: 'missing target', expectedCode: 'ledger_target_missing', mutate: (f) => { delete f.ledger[0]!.target_test_id; } },
    { name: 'dangling target', expectedCode: 'ledger_target_not_collected', mutate: (f) => { f.ledger[0]!.target_test_id = 'missing.test.ts::case'; } },
    {
      name: 'skipped target cannot satisfy accepted mapping', expectedCode: 'ledger_target_not_passed', mutate: (f) => {
        const target = f.targets.tests.find((test) => test.id === f.ledger[0]!.target_test_id);
        if (target !== undefined) target.status = 'skipped';
      },
    },
    { name: 'missing evidence', expectedCode: 'ledger_evidence_missing', mutate: (f) => { f.ledger[0]!.evidence = []; } },
    { name: 'missing reviewer', expectedCode: 'ledger_reviewer_missing', mutate: (f) => { f.ledger[0]!.reviewer = ''; } },
    { name: 'same reviewer', expectedCode: 'ledger_reviewer_not_independent', mutate: (f) => { f.ledger[0]!.reviewer = f.ledger[0]!.owner; } },
    {
      name: 'missing allocation evidence', expectedCode: 'ledger_allocation_evidence_missing',
      mutate: (f) => { delete f.ledger[0]!.allocation_evidence; },
    },
    {
      name: 'missing allocation reviewer', expectedCode: 'ledger_allocation_reviewer_missing',
      mutate: (f) => { delete f.ledger[0]!.allocation_reviewer; },
    },
    {
      name: 'allocation reviewer is owner', expectedCode: 'ledger_allocation_reviewer_not_independent',
      mutate: (f) => { f.ledger[0]!.allocation_reviewer = f.ledger[0]!.owner; },
    },
    {
      name: 'allocation reviewer is reviewer', expectedCode: 'ledger_allocation_reviewer_not_independent',
      mutate: (f) => { f.ledger[0]!.allocation_reviewer = f.ledger[0]!.reviewer; },
    },
    { name: 'unapproved retire', expectedCode: 'ledger_retire_unapproved', mutate: (f) => { delete f.ledger[2]!.retire_approval; } },
    {
      name: 'legacy boolean retire approval', expectedCode: 'ledger_retire_approval_legacy_boolean', mutate: (f) => {
        (f.ledger[2] as unknown as Record<string, unknown>).retire_approved = true;
      },
    },
    {
      name: 'retire decision hash shape', expectedCode: 'ledger_decision_hash_invalid',
      mutate: (f) => { f.ledger[2]!.retire_approval!.decision_sha256 = 'not-a-sha'; },
    },
    {
      name: 'retire approval time', expectedCode: 'ledger_decision_approved_at_invalid',
      mutate: (f) => { f.ledger[2]!.retire_approval!.approved_at = '2026-02-30T00:00:00Z'; },
    },
    {
      name: 'retire approver is implementer', expectedCode: 'ledger_decision_approver_not_owner',
      mutate: (f) => { f.ledger[2]!.retire_approval!.approved_by = f.ledger[2]!.owner; },
    },
    {
      name: 'retire approver is reviewer', expectedCode: 'ledger_decision_approver_not_owner',
      mutate: (f) => { f.ledger[2]!.retire_approval!.approved_by = f.ledger[2]!.reviewer ?? ''; },
    },
    {
      name: 'retire decision hash mismatch', expectedCode: 'ledger_decision_hash_mismatch',
      mutate: (f) => { f.ledger[2]!.retire_approval!.decision_sha256 = '0'.repeat(64); },
    },
    {
      name: 'retire decision missing', expectedCode: 'ledger_decision_missing',
      mutate: (f) => { f.ledger[2]!.retire_approval!.decision_ref = 'decisions/missing.md'; },
    },
    {
      name: 'retire decision fragment is forbidden', expectedCode: 'ledger_decision_ref_invalid',
      mutate: (f) => { f.ledger[2]!.retire_approval!.decision_ref = 'decisions/retire-c.md#row'; },
    },
    {
      name: 'retire decision content mismatch', expectedCode: 'ledger_decision_content_mismatch',
      mutate: (f) => { f.ledger[2]!.retire_approval!.approved_at = '2026-07-21T00:00:00Z'; },
    },
    {
      name: 'frozen owner must be independent', expectedCode: 'ledger_decision_approver_not_independent',
      mutate: (f) => { f.ledger[2]!.owner = MIGRATION_OWNER_ID; },
    },
    {
      name: 'wave relabel requires owner decision', expectedCode: 'ledger_allocation_unapproved',
      mutate: (f) => { f.ledger[1]!.wave = 'B2'; },
    },
    {
      name: 'unexplained target merge', expectedCode: 'ledger_target_group_unexplained',
      mutate: (f) => { f.ledger[1]!.target_test_id = f.ledger[0]!.target_test_id; },
    },
    { name: 'strict rejects pending', expectedCode: 'ledger_status_not_accepted', mutate: (f) => { f.ledger[0]!.status = 'pending'; } },
    { name: 'oracle hash drift', expectedCode: 'oracle_hash_mismatch', mutate: (f) => { f.oracle.nodeids_sha256 = '0'.repeat(64); } },
    {
      name: 'target and oracle baseline diverge', expectedCode: 'target_oracle_baseline_mismatch',
      mutate: (f) => { f.targets.baseline_sha = 'f'.repeat(40); },
    },
    { name: 'duplicate target collection', expectedCode: 'target_duplicate_test_id', mutate: (f) => { f.targets.test_ids.push(f.targets.test_ids[0]!); } },
    {
      name: 'bare target collection array', expectedCode: 'target_not_object', mutate: (f) => {
        const bare = [...f.targets.test_ids];
        (f as unknown as { targets: unknown }).targets = bare;
      },
    },
  ];

  let passed = 0;
  const failures: string[] = [];
  for (const item of cases) {
    const fixture = structuredClone(baseline);
    item.mutate(fixture);
    const result = verifyTestLedger(
      fixture.oracle,
      fixture.ledger,
      fixture.targets,
      { mode: 'strict', repo: fixture.repoRoot },
    );
    if (result.issues.some((issue) => issue.code === item.expectedCode)) passed += 1;
    else failures.push(`${item.name}: expected ${item.expectedCode}, got ${result.issues.map((issue) => issue.code).join(', ') || 'PASS'}`);
  }

  const crossWave = structuredClone(baseline);
  crossWave.ledger[1]!.wave = 'B2';
  crossWave.ledger[1]!.target_test_id = crossWave.ledger[0]!.target_test_id;
  const crossWaveResult = verifyTestLedger(
    crossWave.oracle,
    crossWave.ledger,
    crossWave.targets,
    { mode: 'strict', waves: new Set(['B0-B10']), repo: crossWave.repoRoot },
  );
  if (crossWaveResult.issues.some((issue) => issue.code === 'ledger_target_group_unexplained')) passed += 1;
  else failures.push(`cross-wave target merge bypassed --wave: got ${crossWaveResult.issues.map((issue) => issue.code).join(', ') || 'PASS'}`);

  const waveAllocation = structuredClone(baseline);
  delete waveAllocation.ledger[0]!.allocation_evidence;
  const waveAllocationResult = verifyTestLedger(
    waveAllocation.oracle,
    waveAllocation.ledger,
    waveAllocation.targets,
    { mode: 'strict', waves: new Set(['B0-B10']), repo: waveAllocation.repoRoot },
  );
  if (waveAllocationResult.issues.some((issue) => issue.code === 'ledger_allocation_evidence_missing')) passed += 1;
  else failures.push(`wave allocation evidence bypassed --wave: got ${waveAllocationResult.issues.map((issue) => issue.code).join(', ') || 'PASS'}`);

  const approvedRelabel = structuredClone(baseline);
  approvedRelabel.ledger[0]!.wave = 'B1';
  const allocationDecisionRef = 'decisions/allocation-a.md';
  const allocationApprovedAt = '2026-07-20T01:00:00Z';
  const allocationDecision = [
    'decision: allocation',
    `approved_by: ${MIGRATION_OWNER_ID}`,
    `legacy_nodeid: ${approvedRelabel.ledger[0]!.legacy_nodeid}`,
    `subsystem: ${approvedRelabel.ledger[0]!.subsystem}`,
    `wave: ${approvedRelabel.ledger[0]!.wave}`,
    `approved_at: ${allocationApprovedAt}`,
    '',
  ].join('\n');
  const allocationDecisionPath = path.join(approvedRelabel.repoRoot, ...allocationDecisionRef.split('/'));
  fs.writeFileSync(allocationDecisionPath, allocationDecision, 'utf8');
  approvedRelabel.ledger[0]!.allocation_approval = {
    approved_by: MIGRATION_OWNER_ID,
    decision_ref: allocationDecisionRef,
    decision_sha256: fileSha256(allocationDecisionPath),
    approved_at: allocationApprovedAt,
  };
  const approvedRelabelResult = verifyTestLedger(
    approvedRelabel.oracle,
    approvedRelabel.ledger,
    approvedRelabel.targets,
    { mode: 'strict', repo: approvedRelabel.repoRoot },
  );
  if (approvedRelabelResult.ok) passed += 1;
  else failures.push(`owner-approved allocation unexpectedly failed: ${approvedRelabelResult.issues.map((issue) => issue.code).join(', ')}`);

  const waveRelabel = structuredClone(baseline);
  waveRelabel.ledger[1]!.wave = 'B2';
  const waveRelabelResult = verifyTestLedger(
    waveRelabel.oracle,
    waveRelabel.ledger,
    waveRelabel.targets,
    { mode: 'strict', waves: new Set(['B0-B10']), repo: waveRelabel.repoRoot },
  );
  if (waveRelabelResult.issues.some((issue) => issue.code === 'ledger_allocation_unapproved')) passed += 1;
  else failures.push(`wave relabel bypassed --wave: got ${waveRelabelResult.issues.map((issue) => issue.code).join(', ') || 'PASS'}`);

  const pending = buildInitialTestLedger(baseline.oracle, 'p0-integrator');
  const pendingBaseline = verifyTestLedger(baseline.oracle, pending.entries, undefined, { mode: 'baseline' });
  if (!pendingBaseline.ok) failures.push(`pending baseline unexpectedly failed: ${pendingBaseline.issues.map((issue) => issue.code).join(', ')}`);
  else {
    const baselineMutant = structuredClone(pending.entries);
    baselineMutant[0]!.status = 'mapped';
    const result = verifyTestLedger(baseline.oracle, baselineMutant, undefined, { mode: 'baseline' });
    if (result.issues.some((issue) => issue.code === 'ledger_baseline_requires_pending')) passed += 1;
    else failures.push(`baseline accepts mapped: expected ledger_baseline_requires_pending, got ${result.issues.map((issue) => issue.code).join(', ') || 'PASS'}`);
  }
  const baselineApproval = structuredClone(pending.entries);
  baselineApproval[0]!.retire_approval = {
    approved_by: 'premature-approver',
    decision_ref: 'PROJECT-RECORD#premature',
    decision_sha256: 'b'.repeat(64),
    approved_at: '2026-07-20T00:00:00Z',
  };
  const baselineApprovalResult = verifyTestLedger(
    baseline.oracle,
    baselineApproval,
    undefined,
    { mode: 'baseline' },
  );
  if (baselineApprovalResult.issues.some((issue) => issue.code === 'ledger_baseline_has_retire_approval')) passed += 1;
  else failures.push(`baseline accepts retire approval: got ${baselineApprovalResult.issues.map((issue) => issue.code).join(', ') || 'PASS'}`);
  const baselineRelabel = structuredClone(pending.entries);
  baselineRelabel[0]!.wave = 'B2';
  const baselineRelabelResult = verifyTestLedger(
    baseline.oracle,
    baselineRelabel,
    undefined,
    { mode: 'baseline' },
  );
  if (baselineRelabelResult.issues.some((issue) => issue.code === 'ledger_baseline_allocation_mismatch')) passed += 1;
  else failures.push(`baseline accepts wave relabel: got ${baselineRelabelResult.issues.map((issue) => issue.code).join(', ') || 'PASS'}`);
  const documentBaseline = verifyTestLedgerDocument(
    baseline.oracle, pending, 'json', undefined, { mode: 'baseline' },
  );
  if (!documentBaseline.ok) failures.push(`ledger document baseline unexpectedly failed: ${documentBaseline.issues.map((issue) => issue.code).join(', ')}`);
  const documentCases: Array<{ name: string; code: string; mutate: (document: TestLedgerDocument) => unknown }> = [
    {
      name: 'formal JSON bare array', code: 'ledger_document_not_object',
      mutate: (document) => document.entries,
    },
    {
      name: 'document schema version', code: 'ledger_document_schema_version',
      mutate: (document) => { (document as unknown as Record<string, unknown>).schema_version = 2; return document; },
    },
    {
      name: 'document kind', code: 'ledger_document_kind',
      mutate: (document) => { (document as unknown as Record<string, unknown>).kind = 'wrong'; return document; },
    },
    {
      name: 'document baseline', code: 'ledger_document_baseline_mismatch',
      mutate: (document) => { document.oracle_baseline_sha = 'f'.repeat(40); return document; },
    },
    {
      name: 'document oracle hash', code: 'ledger_document_oracle_hash_mismatch',
      mutate: (document) => { document.oracle_nodeids_sha256 = 'f'.repeat(64); return document; },
    },
  ];
  for (const item of documentCases) {
    const document = structuredClone(pending);
    const result = verifyTestLedgerDocument(
      baseline.oracle, item.mutate(document), 'json', undefined, { mode: 'baseline' },
    );
    if (result.issues.some((issue) => issue.code === item.code)) passed += 1;
    else failures.push(`${item.name}: expected ${item.code}, got ${result.issues.map((issue) => issue.code).join(', ') || 'PASS'}`);
  }
  const jsonl: unknown[] = [
    {
      record_type: 'header', schema_version: 1, kind: TEST_LEDGER_KIND,
      oracle_baseline_sha: pending.oracle_baseline_sha,
      oracle_nodeids_sha256: pending.oracle_nodeids_sha256,
    },
    ...pending.entries.map((entry) => ({ record_type: 'entry', ...entry })),
  ];
  const jsonlBaseline = verifyTestLedgerDocument(
    baseline.oracle, jsonl, 'jsonl', undefined, { mode: 'baseline' },
  );
  if (jsonlBaseline.ok) passed += 1;
  else failures.push(`explicit JSONL baseline unexpectedly failed: ${jsonlBaseline.issues.map((issue) => issue.code).join(', ')}`);
  const missingHeader = verifyTestLedgerDocument(
    baseline.oracle, jsonl.slice(1), 'jsonl', undefined, { mode: 'baseline' },
  );
  if (missingHeader.issues.some((issue) => issue.code === 'ledger_jsonl_header_missing')) passed += 1;
  else failures.push(`JSONL missing header: got ${missingHeader.issues.map((issue) => issue.code).join(', ') || 'PASS'}`);
  return { ok: failures.length === 0, passed, total: cases.length + 7 + documentCases.length + 2, failures };
}

export function runLedgerSyntheticMutants(): { ok: boolean; passed: number; total: number; failures: string[] } {
  const fixture = syntheticFixture();
  try {
    return runLedgerSyntheticMutantsForFixture(fixture);
  } finally {
    fs.rmSync(fixture.repoRoot, { recursive: true, force: true });
  }
}
