import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';

export interface ValidationIssue {
  code: string;
  message: string;
  subject?: string;
}

export interface ValidationResult {
  ok: boolean;
  issues: ValidationIssue[];
}

export const P0_EVIDENCE_ONLY_PREFIXES = [
  'docs/reviews/ts-migration/',
  'docs/verify/ts-migration/',
] as const;

function runGit(repo: string, args: readonly string[]): string {
  const result = spawnSync('git', ['-C', repo, ...args], {
    encoding: 'utf8',
    windowsHide: true,
    maxBuffer: 64 * 1024 * 1024,
  });
  if (result.error !== undefined) throw result.error;
  if (result.status !== 0) {
    throw new Error(`git ${args.join(' ')} failed (${String(result.status)}): ${(result.stderr ?? '').trim()}`);
  }
  return result.stdout;
}

function toPosix(value: string): string {
  return value.replaceAll('\\', '/');
}

export function assertRepositoryWorktreeClean(repo: string): void {
  const repoRoot = path.resolve(runGit(repo, ['rev-parse', '--show-toplevel']).trim());
  const status = runGit(repoRoot, ['status', '--porcelain=v1', '-z', '--untracked-files=all']);
  if (status.length > 0) {
    throw new Error('P0 artifact generation requires a fully clean tracked and standard-untracked worktree');
  }
}

export function verifyP0EvidenceOnlyRepositoryState(
  repo: string,
  baselineSha: string,
  codePrefix: string,
): ValidationResult {
  const issues: ValidationIssue[] = [];
  try {
    const repoRoot = path.resolve(runGit(repo, ['rev-parse', '--show-toplevel']).trim());
    const baseline = runGit(repoRoot, ['rev-parse', '--verify', `${baselineSha}^{commit}`]).trim().toLowerCase();
    const head = runGit(repoRoot, ['rev-parse', '--verify', 'HEAD^{commit}']).trim().toLowerCase();
    const ancestor = spawnSync('git', ['-C', repoRoot, 'merge-base', '--is-ancestor', baseline, head], {
      encoding: 'utf8',
      windowsHide: true,
    });
    if (ancestor.error !== undefined) throw ancestor.error;
    if (ancestor.status !== 0) {
      issues.push({
        code: `${codePrefix}_baseline_not_ancestor`,
        message: 'artifact baseline must be an ancestor of the current HEAD',
        subject: baseline,
      });
    }

    const commits = runGit(repoRoot, ['rev-list', '--reverse', `${baseline}..${head}`])
      .split(/\r?\n/u)
      .filter((item) => item.length > 0);
    for (const commit of commits) {
      const changed = runGit(repoRoot, [
        'diff-tree', '--root', '--no-commit-id', '--name-only', '-r', '-m', '-z', commit, '--',
      ])
        .split('\0')
        .filter((item) => item.length > 0)
        .map(toPosix);
      for (const relativePath of changed) {
        if (!P0_EVIDENCE_ONLY_PREFIXES.some((prefix) => relativePath.startsWith(prefix))) {
          issues.push({
            code: `${codePrefix}_non_evidence_history`,
            message: 'every commit after the artifact baseline may touch only P0 evidence/review paths',
            subject: `${commit.slice(0, 12)}:${relativePath}`,
          });
        }
      }
    }

    const status = runGit(repoRoot, ['status', '--porcelain=v1', '-z', '--untracked-files=all']);
    if (status.length > 0) {
      issues.push({
        code: `${codePrefix}_worktree_dirty`,
        message: 'artifact verification requires a fully clean tracked and standard-untracked worktree',
      });
    }
  } catch (error: unknown) {
    issues.push({
      code: `${codePrefix}_repository_history`,
      message: error instanceof Error ? error.message : String(error),
    });
  }
  return validationResult(issues);
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function isNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0;
}

export function compareText(left: string, right: string): number {
  if (left < right) return -1;
  if (left > right) return 1;
  return 0;
}

export function sha256(text: string): string {
  return createHash('sha256').update(text, 'utf8').digest('hex');
}

export function hashStringList(values: readonly string[]): string {
  // A JSON string array is an unambiguous, length-delimited representation.
  // In particular, ["a\\nb"] must never collide with ["a", "b"].
  return sha256(JSON.stringify(values));
}

function sortJsonValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortJsonValue);
  if (!isRecord(value)) return value;

  const sorted: Record<string, unknown> = {};
  for (const key of Object.keys(value).sort(compareText)) {
    sorted[key] = sortJsonValue(value[key]);
  }
  return sorted;
}

export function stableStringify(value: unknown): string {
  return `${JSON.stringify(sortJsonValue(value), null, 2)}\n`;
}

export function readText(path: string): string {
  return path === '-' ? fs.readFileSync(0, 'utf8') : fs.readFileSync(path, 'utf8');
}

export function writeText(path: string, text: string): void {
  if (path === '-') {
    process.stdout.write(text);
    return;
  }
  fs.writeFileSync(path, text, 'utf8');
}

export function parseJson(text: string, label: string): unknown {
  try {
    return JSON.parse(text) as unknown;
  } catch (error: unknown) {
    const detail = error instanceof Error ? error.message : String(error);
    throw new Error(`${label} 不是合法 JSON: ${detail}`);
  }
}

/** Explicit JSONL compatibility mode. Callers must opt in by using a .jsonl path. */
export function parseJsonlRecords(text: string, label: string): unknown[] {
  const rows: unknown[] = [];
  for (const [index, rawLine] of text.split(/\r?\n/u).entries()) {
    const line = rawLine.trim();
    if (line.length === 0 || line.startsWith('#')) continue;
    try {
      const row = JSON.parse(line) as unknown;
      if (!isRecord(row)) throw new Error('每行必须是 JSON object');
      rows.push(row);
    } catch (error: unknown) {
      const detail = error instanceof Error ? error.message : String(error);
      throw new Error(`${label} JSONL 第 ${index + 1} 行无效: ${detail}`);
    }
  }
  if (rows.length === 0) throw new Error(`${label} JSONL 没有记录`);
  return rows;
}

export function sortedRecord(input: Readonly<Record<string, string>>): Record<string, string> {
  return Object.fromEntries(Object.entries(input).sort(([a], [b]) => compareText(a, b)));
}

export function validationResult(issues: ValidationIssue[]): ValidationResult {
  return { ok: issues.length === 0, issues };
}

export function formatValidationResult(name: string, result: ValidationResult): string {
  if (result.ok) return `${name}: PASS\n`;
  const lines = [`${name}: FAIL (${result.issues.length})`];
  for (const issue of result.issues) {
    const subject = issue.subject === undefined ? '' : ` [${issue.subject}]`;
    lines.push(`- ${issue.code}${subject}: ${issue.message}`);
  }
  return `${lines.join('\n')}\n`;
}
