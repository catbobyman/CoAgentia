import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import * as fs from 'node:fs';
import * as path from 'node:path';
import {
  compareText,
  isNonEmptyString,
  isRecord,
  sha256,
  validationResult,
  type ValidationIssue,
  type ValidationResult,
} from './shared.ts';

export const INVENTORY_KIND = 'coagentia.migration-inventory';

export interface DiscoveredInventoryEntry {
  id: string;
  kind: 'file' | 'inline-script' | 'doc-command' | 'package-bin' | 'package-script' | 'pyproject-script' | 'ci-workflow' | 'ci-run' | 'executable-config';
  path: string;
  reasons: string[];
  migration_residual: boolean;
  file_mode: string;
  git_blob: string;
  detail?: string;
  fingerprint?: string;
}

type InventoryEntryCandidate = Omit<DiscoveredInventoryEntry, 'file_mode' | 'git_blob'>;

export interface InventoryScan {
  repo_root: string;
  generated_from_head: string;
  entries: DiscoveredInventoryEntry[];
  issues: ValidationIssue[];
}

export interface MigrationInventoryRecord {
  id: string;
  kind: DiscoveredInventoryEntry['kind'];
  path: string;
  file_mode: string;
  git_blob: string;
  fingerprint?: string;
  owner: string;
  disposition: 'port' | 'replace' | 'retire' | 'keep';
  target_phase?: string | null;
  target?: string | null;
}

const MIGRATION_SOURCE_EXTENSIONS = new Set([
  '.py', '.pyw', '.js', '.jsx', '.mjs', '.cjs', '.ps1', '.psm1', '.psd1', '.sh', '.bash', '.zsh', '.fish',
  '.bat', '.cmd', '.com', '.exe', '.rb', '.pl', '.php', '.lua', '.r', '.coffee',
]);

const TYPESCRIPT_SOURCE_EXTENSIONS = new Set(['.ts', '.tsx', '.mts', '.cts']);
const NON_EXECUTABLE_DATA_EXTENSIONS = new Set([
  '.css', '.csv', '.gitignore', '.html', '.htm', '.ico', '.ini', '.jpeg', '.jpg', '.json', '.lock', '.md',
  '.mdx', '.png', '.rst', '.svg', '.toml', '.txt', '.webp', '.woff', '.woff2', '.yaml', '.yml',
]);
const NON_EXECUTABLE_EXTENSIONLESS_NAMES = new Set(['.GITIGNORE', 'LICENSE', 'NOTICE', 'PATENTS', 'SECURITY']);

const NON_TS_COMMAND_RE = /(?:^|[\s"'`/\\])(?:python(?:3(?:\.\d+)?)?|py|pytest|uvx?|ruff|pyright|alembic|pipx?|poetry|tox|nox|uvicorn|gunicorn|hypercorn|flask|django-admin)(?:\.exe)?(?:$|[\s"'`])|\.(?:py|pyw|mjs|cjs|ps1|psm1|sh|bat|cmd)(?:$|[\s"'`])/iu;
const GIT_SHA_RE = /^[0-9a-f]{7,64}$/iu;
const GIT_OBJECT_RE = /^[0-9a-f]{40}(?:[0-9a-f]{24})?$/iu;
const FILE_MODE_RE = /^(?:100644|100755|120000|untracked)$/u;
const COMMAND_START_RE = /^(?:uvx?|python(?:3(?:\.\d+)?)?|py|pytest|ruff|pyright|alembic|pipx?|poetry|tox|nox|uvicorn|gunicorn|hypercorn|flask|django-admin)(?:\.exe)?\b|^(?:pnpm|npm|npx|node|git)(?:\.exe)?\b/iu;
const ACTIVE_COMMAND_DOCS = new Set([
  'plan.md',
  'README.md',
  'README_EN.md',
  'README_JA.md',
  'AGENTS.md',
  'docs/project-handoffs/CURRENT-HANDOFF.md',
  'docs/project-handoffs/README.md',
  'scratchpad/GIT-CALIBRATION.md',
  'scratchpad/PREVIEW-CALIBRATION.md',
]);
const EXECUTABLE_CONFIG_RE = /(?:^|\/)(?:Dockerfile(?:\.[^/]+)?|Procfile(?:\.[^/]+)?|Makefile|GNUmakefile|Justfile|Jenkinsfile|Taskfile\.ya?ml|azure-pipelines\.ya?ml|\.gitlab-ci\.ya?ml|\.vscode\/tasks\.json)$/iu;

interface RepositoryFileState {
  file_mode: string;
  git_blob: string;
}

function toPosix(value: string): string {
  return value.replaceAll('\\', '/');
}

function runGit(repo: string, args: string[]): string {
  const result = spawnSync('git', ['-C', repo, ...args], {
    encoding: 'utf8',
    windowsHide: true,
    maxBuffer: 64 * 1024 * 1024,
  });
  if (result.error !== undefined) throw result.error;
  if (result.status !== 0) {
    throw new Error(`git ${args.join(' ')} 失败 (${String(result.status)}): ${(result.stderr ?? '').trim()}`);
  }
  return result.stdout;
}

export function resolveInventoryBaseline(repo: string, baselineSha: string): string {
  const candidate = baselineSha.trim().toLowerCase();
  const repoRoot = runGit(repo, ['rev-parse', '--show-toplevel']).trim();
  if (candidate === 'head') {
    const head = runGit(repoRoot, ['rev-parse', '--verify', 'HEAD^{commit}']).trim().toLowerCase();
    if (!GIT_OBJECT_RE.test(head)) throw new Error(`git returned an invalid commit SHA for HEAD: ${head}`);
    return head;
  }
  if (!GIT_SHA_RE.test(candidate)) {
    throw new Error('--baseline-sha must be HEAD or a 7-64 digit hexadecimal Git SHA');
  }
  const resolved = runGit(repoRoot, ['rev-parse', '--verify', `${candidate}^{commit}`]).trim().toLowerCase();
  if (!GIT_SHA_RE.test(resolved)) throw new Error(`git returned an invalid commit SHA for baseline ${candidate}`);
  return resolved;
}

export function resolveInventoryBaselineTree(repo: string, baselineSha: string): string {
  const resolvedCommit = resolveInventoryBaseline(repo, baselineSha);
  const repoRoot = runGit(repo, ['rev-parse', '--show-toplevel']).trim();
  const tree = runGit(repoRoot, ['rev-parse', '--verify', `${resolvedCommit}^{tree}`]).trim().toLowerCase();
  if (!GIT_OBJECT_RE.test(tree)) throw new Error(`git returned an invalid tree object for baseline ${resolvedCommit}`);
  return tree;
}

export function assertInventoryGenerationBaseline(scan: InventoryScan, baselineSha: string): void {
  if (baselineSha !== scan.generated_from_head) {
    throw new Error(`inventory baseline must equal current HEAD: ${baselineSha} != ${scan.generated_from_head}`);
  }
  const status = runGit(scan.repo_root, ['status', '--porcelain=v1', '--untracked-files=all']);
  if (status.trim().length > 0) {
    throw new Error('inventory generation requires a clean tracked and standard-untracked worktree');
  }
}

function repositoryFiles(repoRoot: string): Map<string, RepositoryFileState> {
  const output = runGit(repoRoot, ['ls-files', '--stage', '-z']);
  const files = new Map<string, RepositoryFileState>();
  for (const record of output.split('\0')) {
    if (record.length === 0) continue;
    const match = /^(\d+) ([0-9a-f]+) (\d+)\t([\s\S]+)$/u.exec(record);
    if (match === null) throw new Error(`无法解析 git ls-files --stage 记录: ${record}`);
    const mode = match[1]!;
    const blob = match[2]!.toLowerCase();
    const stage = match[3]!;
    const file = toPosix(match[4]!);
    if (stage !== '0') throw new Error(`inventory 不接受 unresolved index stage ${stage}: ${file}`);
    if (!FILE_MODE_RE.test(mode) && mode !== '160000') throw new Error(`inventory 不支持 Git file mode ${mode}: ${file}`);
    if (!GIT_OBJECT_RE.test(blob)) throw new Error(`inventory 收到无效 Git blob ${blob}: ${file}`);
    if (files.has(file)) throw new Error(`git ls-files --stage 返回重复 path: ${file}`);
    files.set(file, { file_mode: mode, git_blob: blob });
  }
  const untracked = runGit(repoRoot, ['ls-files', '--others', '--exclude-standard', '-z']);
  for (const rawPath of untracked.split('\0')) {
    if (rawPath.length === 0) continue;
    const file = toPosix(rawPath);
    if (!files.has(file)) {
      const blob = runGit(repoRoot, ['hash-object', '--no-filters', '--', file]).trim().toLowerCase();
      if (!GIT_OBJECT_RE.test(blob)) throw new Error(`inventory 无法为标准未跟踪文件计算 Git blob: ${file}`);
      files.set(file, { file_mode: 'untracked', git_blob: blob });
    }
  }
  return files;
}

function readPrefix(file: string, size = 512): string {
  const handle = fs.openSync(file, 'r');
  try {
    const buffer = Buffer.alloc(size);
    const bytes = fs.readSync(handle, buffer, 0, buffer.length, 0);
    return buffer.subarray(0, bytes).toString('utf8');
  } finally {
    fs.closeSync(handle);
  }
}

function commandFingerprint(kind: string, detail: string): string {
  return sha256(`${kind}\0${detail}`);
}

function gitBlobContentFingerprints(repoRoot: string, blobIds: readonly string[]): Map<string, string> {
  const unique = [...new Set(blobIds)].sort(compareText);
  if (unique.length === 0) return new Map();
  const result = spawnSync('git', ['-C', repoRoot, 'cat-file', '--batch'], {
    env: { ...process.env, GIT_NO_REPLACE_OBJECTS: '1' },
    input: `${unique.join('\n')}\n`,
    windowsHide: true,
    maxBuffer: 256 * 1024 * 1024,
  });
  if (result.error !== undefined) throw result.error;
  if (result.status !== 0) {
    const stderr = result.stderr?.toString('utf8').trim() ?? '';
    throw new Error(`git cat-file --batch 失败 (${String(result.status)}): ${stderr}`);
  }
  const output = result.stdout ?? Buffer.alloc(0);
  const fingerprints = new Map<string, string>();
  let offset = 0;
  for (const expectedBlob of unique) {
    const headerEnd = output.indexOf(0x0a, offset);
    if (headerEnd < 0) throw new Error(`git cat-file --batch 缺少 header: ${expectedBlob}`);
    const header = output.subarray(offset, headerEnd).toString('ascii');
    const match = /^([0-9a-f]{40}(?:[0-9a-f]{24})?) blob (\d+)$/u.exec(header);
    if (match === null || match[1] !== expectedBlob) {
      throw new Error(`git cat-file --batch header 不匹配: expected ${expectedBlob}, got ${header}`);
    }
    const size = Number.parseInt(match[2]!, 10);
    if (!Number.isSafeInteger(size) || size < 0) throw new Error(`git blob size 无效: ${header}`);
    const contentStart = headerEnd + 1;
    const contentEnd = contentStart + size;
    if (contentEnd >= output.length || output[contentEnd] !== 0x0a) {
      throw new Error(`git cat-file --batch payload 截断: ${expectedBlob}`);
    }
    fingerprints.set(
      expectedBlob,
      createHash('sha256').update(output.subarray(contentStart, contentEnd)).digest('hex'),
    );
    offset = contentEnd + 1;
  }
  if (offset !== output.length) throw new Error('git cat-file --batch 返回未消费的额外字节');
  return fingerprints;
}

function applyFileContentFingerprints(
  repoRoot: string,
  files: ReadonlyMap<string, RepositoryFileState>,
  entries: Map<string, DiscoveredInventoryEntry>,
): void {
  const contentEntries = [...entries.values()].filter((entry) => (
    entry.kind === 'file' || entry.kind === 'ci-workflow' || entry.kind === 'executable-config'
  ));
  const trackedBlobs = contentEntries.flatMap((entry) => {
    const state = files.get(entry.path);
    return state === undefined || state.file_mode === 'untracked' ? [] : [state.git_blob];
  });
  const trackedFingerprints = gitBlobContentFingerprints(repoRoot, trackedBlobs);
  for (const entry of contentEntries) {
    const state = files.get(entry.path);
    if (state === undefined) throw new Error(`fingerprint entry 缺少文件状态: ${entry.id}`);
    if (state.file_mode === 'untracked') {
      const absolutePath = path.resolve(repoRoot, ...entry.path.split('/'));
      entry.fingerprint = createHash('sha256').update(fs.readFileSync(absolutePath)).digest('hex');
      continue;
    }
    const fingerprint = trackedFingerprints.get(state.git_blob);
    if (fingerprint === undefined) throw new Error(`fingerprint entry 缺少 canonical Git blob: ${entry.id}`);
    entry.fingerprint = fingerprint;
  }
}

export function inventoryScanScopeSha256(entries: readonly DiscoveredInventoryEntry[]): string {
  const canonical = [...entries]
    .sort((left, right) => compareText(left.id, right.id))
    .map((entry) => ({
      id: entry.id,
      kind: entry.kind,
      path: entry.path,
      reasons: [...entry.reasons],
      migration_residual: entry.migration_residual,
      file_mode: entry.file_mode,
      git_blob: entry.git_blob,
      detail: entry.detail ?? null,
      fingerprint: entry.fingerprint ?? null,
    }));
  return sha256(JSON.stringify(canonical));
}

function packageBinIsMigrationResidual(target: string): boolean {
  const normalized = toPosix(target).replace(/^\.\//u, '');
  if (/^dist\/.+\.(?:cjs|mjs|js)$/iu.test(normalized)) return false;
  return /(?:^|\/)src(?:\/|$)/iu.test(normalized)
    || /\.(?:tsx?|jsx?|mjs|cjs)$/iu.test(normalized)
    || NON_TS_COMMAND_RE.test(target);
}

function isUnclassifiedSourceCandidate(relativePath: string, extension: string): boolean {
  if (TYPESCRIPT_SOURCE_EXTENSIONS.has(extension) || NON_EXECUTABLE_DATA_EXTENSIONS.has(extension)) return false;
  if (MIGRATION_SOURCE_EXTENSIONS.has(extension) || relativePath.endsWith('.py.mako')) return false;
  if (EXECUTABLE_CONFIG_RE.test(relativePath)) return false;
  if (extension.length === 0 && NON_EXECUTABLE_EXTENSIONLESS_NAMES.has(path.basename(relativePath).toUpperCase())) return false;
  return true;
}

function hasLegacySourceSuffixBeforeDataExtension(relativePath: string, extension: string): boolean {
  if (!NON_EXECUTABLE_DATA_EXTENSIONS.has(extension) || extension.length === 0) return false;
  const basename = path.basename(relativePath).toLowerCase();
  const stem = basename.slice(0, -extension.length);
  return [...MIGRATION_SOURCE_EXTENSIONS].some((sourceExtension) => (
    stem.endsWith(sourceExtension) || stem.includes(`${sourceExtension}.`)
  ));
}

function normalizeDocumentCommand(value: string): string {
  let command = value.trim()
    .replace(/^PS\s+[^>\r\n]+>\s*/iu, '')
    .replace(/^(?:\$\s+|PS>\s*)/iu, '')
    .replace(/^&\s*/u, '');
  let previous = '';
  while (command !== previous) {
    previous = command;
    command = command
      .replace(/^cd\s+(?:"[^"]+"|'[^']+'|[^&\r\n]+)\s*&&\s*/iu, '')
      .replace(/^env\s+(?=[A-Za-z_][A-Za-z0-9_]*=)/u, '')
      .replace(/^\$env:[A-Za-z_][A-Za-z0-9_]*\s*=\s*(?:"[^"]*"|'[^']*'|[^;\s]+)\s*;\s*/u, '')
      .replace(/^[A-Za-z_][A-Za-z0-9_]*=(?:"[^"]*"|'[^']*'|[^\s]+)\s+/u, '')
      .replace(/^set\s+[A-Za-z_][A-Za-z0-9_]*=[^&\r\n]*&&\s*/iu, '')
      .replace(/^&\s*/u, '');
  }
  return command.trim();
}

function isDocumentCommand(value: string): boolean {
  const command = normalizeDocumentCommand(value);
  if (COMMAND_START_RE.test(command)) return true;
  const executable = /^(?:"([^"]+)"|'([^']+)'|([^\s]+))/u.exec(command);
  const token = (executable?.[1] ?? executable?.[2] ?? executable?.[3] ?? '').replaceAll('\\', '/');
  return /^(?:uvx?|python(?:3(?:\.\d+)?)?|py|pytest|ruff|pyright|alembic|pipx?|poetry|tox|nox|uvicorn|gunicorn|hypercorn|flask|django-admin)(?:\.exe)?$/iu
    .test(path.posix.basename(token));
}

function documentCommandIsMigrationResidual(value: string): boolean {
  const command = normalizeDocumentCommand(value);
  if (/^git(?:\.exe)?\s+ls-files(?:\s|$)/iu.test(command) && !/[;&|`]/u.test(command)) return false;
  if (/^git(?:\.exe)?\b/iu.test(command)) return true;
  return NON_TS_COMMAND_RE.test(value);
}

function stripMarkdownContainers(value: string): { text: string; changed: boolean } {
  let text = value;
  let changed = false;
  for (;;) {
    const next = text
      .replace(/^\s*>\s?/u, '')
      .replace(/^\s*(?:[-+*]|\d+[.)])\s+/u, '')
      .replace(/^\s*\[[ xX]\]\s+/u, '');
    if (next === text) return { text, changed };
    text = next;
    changed = true;
  }
}

function isHermeticToolchainDenyGuard(command: string): boolean {
  if (!command.includes('coagentia-runtime-deny')) return false;
  const createsFailingShims = command.includes('CoAgentiaHermeticDeny')
    && command.includes('OutputType ConsoleApplication')
    && command.includes('Copy-Item')
    && command.includes('return 86')
    && command.includes('exit /b 86')
    && command.includes('GITHUB_PATH');
  const assertsDenyShims = command.includes("spawnSync(command, ['--version']")
    && command.includes('result.status !== 86')
    && command.includes('COAGENTIA_HERMETIC_DENY')
    && command.includes('Node deny-shim assertions failed');
  return createsFailingShims || assertsDenyShims;
}

function scanPackageJson(
  filePath: string,
  relativePath: string,
  add: (entry: InventoryEntryCandidate) => void,
  issues: ValidationIssue[],
): void {
  let value: unknown;
  try {
    value = JSON.parse(fs.readFileSync(filePath, 'utf8')) as unknown;
  } catch (error: unknown) {
    issues.push({
      code: 'inventory_package_json_invalid',
      message: error instanceof Error ? error.message : String(error),
      subject: relativePath,
    });
    return;
  }
  if (!isRecord(value)) {
    issues.push({ code: 'inventory_package_json_invalid', message: 'package.json 顶层必须是 object', subject: relativePath });
    return;
  }

  if (isRecord(value.scripts)) {
    for (const [name, command] of Object.entries(value.scripts).sort(([a], [b]) => compareText(a, b))) {
      if (typeof command !== 'string') {
        issues.push({ code: 'inventory_package_script_invalid', message: 'script command 必须是字符串', subject: `${relativePath}#${name}` });
        continue;
      }
      add({
        id: `package-script:${relativePath}#${name}`,
        kind: 'package-script',
        path: relativePath,
        reasons: ['package.json scripts entry'],
        migration_residual: NON_TS_COMMAND_RE.test(command),
        detail: command,
        fingerprint: commandFingerprint('package-script', command),
      });
    }
  }

  const bins: Array<[string, string]> = [];
  if (typeof value.bin === 'string') {
    const name = isNonEmptyString(value.name) ? value.name : '(package-name-missing)';
    bins.push([name, value.bin]);
  } else if (isRecord(value.bin)) {
    for (const [name, target] of Object.entries(value.bin)) {
      if (typeof target === 'string') bins.push([name, target]);
      else issues.push({ code: 'inventory_package_bin_invalid', message: 'bin target 必须是字符串', subject: `${relativePath}#${name}` });
    }
  }
  for (const [name, target] of bins.sort(([a], [b]) => compareText(a, b))) {
    add({
      id: `package-bin:${relativePath}#${name}`,
      kind: 'package-bin',
      path: relativePath,
      reasons: ['package.json bin entry'],
      // dist/*.js is a supported generated package artifact, not first-party source residual.
      migration_residual: packageBinIsMigrationResidual(target),
      detail: target,
      fingerprint: commandFingerprint('package-bin', target),
    });
  }
}

function scanPyproject(
  filePath: string,
  relativePath: string,
  add: (entry: InventoryEntryCandidate) => void,
): void {
  const lines = fs.readFileSync(filePath, 'utf8').split(/\r?\n/u);
  let section = '';
  for (const rawLine of lines) {
    const sectionMatch = /^\s*\[([^\]]+)\]\s*(?:#.*)?$/u.exec(rawLine);
    if (sectionMatch !== null) {
      section = sectionMatch[1]!.trim();
      continue;
    }
    if (
      section !== 'project.scripts'
      && section !== 'project.gui-scripts'
      && section !== 'tool.poetry.scripts'
      && !section.startsWith('project.entry-points.')
    ) continue;
    const entryMatch = /^\s*("[^"]+"|'[^']+'|[A-Za-z0-9_.-]+)\s*=\s*(.+?)\s*$/u.exec(rawLine);
    if (entryMatch === null) continue;
    const name = entryMatch[1]!.replace(/^(?:"|')|(?:"|')$/gu, '');
    const target = entryMatch[2]!;
    add({
      id: `pyproject-script:${relativePath}#${section}#${name}`,
      kind: 'pyproject-script',
      path: relativePath,
      reasons: [`[${section}] entry`],
      migration_residual: true,
      detail: target,
      fingerprint: commandFingerprint('pyproject-script', target),
    });
  }
}

function indentation(value: string): number {
  const match = /^\s*/u.exec(value);
  return match?.[0].replaceAll('\t', '  ').length ?? 0;
}

function scanWorkflow(
  filePath: string,
  relativePath: string,
  add: (entry: InventoryEntryCandidate) => void,
): void {
  const lines = fs.readFileSync(filePath, 'utf8').split(/\r?\n/u);
  let ordinal = 0;
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index]!;
    const match = /^(\s*)(?:-\s*)?run:\s*(.*)$/u.exec(line);
    if (match === null) continue;
    ordinal += 1;
    const marker = match[2]!.trim();
    let command = marker;
    if (marker === '|' || marker === '>' || marker.startsWith('|-') || marker.startsWith('>-')) {
      const baseIndent = indentation(line);
      const block: string[] = [];
      for (let next = index + 1; next < lines.length; next += 1) {
        const blockLine = lines[next]!;
        if (blockLine.trim().length > 0 && indentation(blockLine) <= baseIndent) break;
        block.push(blockLine);
        index = next;
      }
      command = block.join('\n').trim();
    }
    add({
      id: `ci-run:${relativePath}#${ordinal}`,
      kind: 'ci-run',
      path: relativePath,
      reasons: [`workflow run step ${ordinal}`],
      migration_residual: NON_TS_COMMAND_RE.test(command) && !isHermeticToolchainDenyGuard(command),
      detail: command,
      fingerprint: commandFingerprint('ci-run', command),
    });
  }
}

function scanInlineScripts(
  filePath: string,
  relativePath: string,
  add: (entry: InventoryEntryCandidate) => void,
): void {
  const html = fs.readFileSync(filePath, 'utf8');
  const scriptRe = /<script\b([^>]*)>([\s\S]*?)<\/script\s*>/giu;
  let ordinal = 0;
  for (const match of html.matchAll(scriptRe)) {
    const attributes = match[1] ?? '';
    if (/\bsrc\s*=/iu.test(attributes)) continue;
    const content = (match[2] ?? '').replaceAll('\r\n', '\n').trim();
    if (content.length === 0) continue;
    ordinal += 1;
    add({
      id: `inline-script:${relativePath}#${ordinal}`,
      kind: 'inline-script',
      path: relativePath,
      reasons: [`inline <script> ${ordinal}`],
      migration_residual: true,
      fingerprint: commandFingerprint('inline-script', content),
    });
  }
}

function activeDocumentPrefix(relativePath: string, text: string): string {
  if (relativePath === 'docs/project-handoffs/CURRENT-HANDOFF.md') {
    return text.split(/^## 历史交接明细/mu, 1)[0] ?? text;
  }
  if (relativePath === 'docs/project-handoffs/README.md') {
    return text.split(/^## 原文件迁移表/mu, 1)[0] ?? text;
  }
  return text;
}

function scanDocumentCommands(
  filePath: string,
  relativePath: string,
  add: (entry: InventoryEntryCandidate) => void,
): void {
  const text = activeDocumentPrefix(relativePath, fs.readFileSync(filePath, 'utf8'));
  const lines = text.split(/\r?\n/u);
  let fenceCharacter: '`' | '~' | undefined;
  let fenceLength = 0;
  let fenceCapturesCommands = false;
  let inHtmlCode = false;
  for (const [index, line] of lines.entries()) {
    const container = stripMarkdownContainers(line);
    const structuralLine = container.text;
    const fence = /^\s*(`{3,}|~{3,})\s*([^\s`~]*)/u.exec(structuralLine);
    if (fence !== null) {
      const marker = fence[1]!;
      const character = marker[0] as '`' | '~';
      if (fenceCharacter === character && marker.length >= fenceLength) {
        fenceCharacter = undefined;
        fenceLength = 0;
        fenceCapturesCommands = false;
      } else if (fenceCharacter === undefined) {
        const language = (fence[2] ?? '').toLowerCase().replace(/^\{\./u, '').replace(/\}$/u, '');
        fenceCharacter = character;
        fenceLength = marker.length;
        fenceCapturesCommands = language.length === 0 || /^(?:bash|sh|shell|console|text|plaintext|terminal|powershell|pwsh|cmd|bat)$/u.test(language);
      }
      continue;
    }
    const candidates = new Set<string>();
    const trimmed = structuralLine.trim();
    if (fenceCharacter !== undefined && fenceCapturesCommands && isDocumentCommand(trimmed)) candidates.add(trimmed);
    if (fenceCharacter === undefined) {
      if (isDocumentCommand(trimmed)) candidates.add(trimmed);
      const indented = /^(?: {4}|\t)([\s\S]*)$/u.exec(line);
      if (indented !== null) {
        const command = (indented[1] ?? '').trim();
        if (isDocumentCommand(command)) candidates.add(command);
      }
      if (container.changed && isDocumentCommand(trimmed)) candidates.add(trimmed);
      if (line.includes('|')) {
        for (const cell of line.split('|')) {
          const tableCommand = cell.trim();
          if (isDocumentCommand(tableCommand)) candidates.add(tableCommand);
        }
      }

      if (/<(?:pre|code)\b/iu.test(line)) inHtmlCode = true;
      if (inHtmlCode) {
        const htmlCommand = line.replace(/<[^>]+>/gu, ' ').trim();
        if (isDocumentCommand(htmlCommand)) candidates.add(htmlCommand);
      }
      if (/<\/(?:pre|code)\s*>/iu.test(line)) inHtmlCode = false;

      for (const match of line.matchAll(/`([^`\r\n]+)`/gu)) {
        const inline = (match[1] ?? '').trim();
        if (isDocumentCommand(inline)) candidates.add(inline);
      }
    }
    for (const [ordinal, command] of [...candidates].entries()) {
      add({
        id: `doc-command:${relativePath}#L${index + 1}-${ordinal + 1}`,
        kind: 'doc-command',
        path: relativePath,
        reasons: [`active documentation command at line ${index + 1}`],
        migration_residual: documentCommandIsMigrationResidual(command),
        detail: command,
        fingerprint: commandFingerprint('doc-command', command),
      });
    }
  }
}

export function scanMigrationInventory(repo = '.'): InventoryScan {
  const rootOutput = runGit(repo, ['rev-parse', '--show-toplevel']).trim();
  const repoRoot = path.resolve(rootOutput);
  const generatedFromHead = runGit(repoRoot, ['rev-parse', 'HEAD']).trim().toLowerCase();
  const files = repositoryFiles(repoRoot);
  const entries = new Map<string, DiscoveredInventoryEntry>();
  const issues: ValidationIssue[] = [];

  const add = (entry: InventoryEntryCandidate): void => {
    if (entries.has(entry.id)) {
      issues.push({ code: 'inventory_scanner_duplicate_id', message: 'scanner 产生重复 ID', subject: entry.id });
      return;
    }
    const fileState = files.get(entry.path);
    if (fileState === undefined) {
      issues.push({ code: 'inventory_scanner_file_state_missing', message: 'entry 缺少 Git file mode/blob 绑定', subject: entry.id });
      return;
    }
    entries.set(entry.id, { ...entry, ...fileState });
  };

  for (const [relativePath, fileState] of [...files.entries()].sort(([a], [b]) => compareText(a, b))) {
    const mode = fileState.file_mode;
    if (mode === '160000') continue;
    const absolutePath = path.resolve(repoRoot, ...relativePath.split('/'));
    const relativeGuard = path.relative(repoRoot, absolutePath);
    if (relativeGuard.startsWith('..') || path.isAbsolute(relativeGuard)) {
      issues.push({ code: 'inventory_path_escape', message: 'tracked path 逃出 repo root', subject: relativePath });
      continue;
    }
    if (!fs.existsSync(absolutePath) || !fs.statSync(absolutePath).isFile()) {
      issues.push({ code: 'inventory_tracked_file_missing', message: 'git tracked file 在工作树缺失', subject: relativePath });
      continue;
    }

    const extension = path.extname(relativePath).toLowerCase();
    const reasons: string[] = [];
    const unclassifiedSource = isUnclassifiedSourceCandidate(relativePath, extension);
    const disguisedLegacySource = hasLegacySourceSuffixBeforeDataExtension(relativePath, extension);
    let hasShebang = false;
    try {
      hasShebang = readPrefix(absolutePath).startsWith('#!');
    } catch (error: unknown) {
      issues.push({ code: 'inventory_file_read_failed', message: error instanceof Error ? error.message : String(error), subject: relativePath });
    }
    const executableNonTypeScript = (hasShebang || mode === '100755') && !TYPESCRIPT_SOURCE_EXTENSIONS.has(extension);
    const isResidualSource = MIGRATION_SOURCE_EXTENSIONS.has(extension)
      || relativePath.endsWith('.py.mako')
      || unclassifiedSource
      || disguisedLegacySource
      || executableNonTypeScript;
    if (isResidualSource) reasons.push(`migration executable extension ${extension}`);
    if (unclassifiedSource) reasons.push('unclassified first-party extension (fail-closed)');
    if (disguisedLegacySource) reasons.push('legacy source suffix before data extension (fail-closed)');
    if (hasShebang) reasons.push('shebang');
    if (mode === '100755') reasons.push('git executable bit');
    if (mode === 'untracked' && reasons.length > 0) reasons.push('standard untracked file');
    if (reasons.length > 0) {
      add({
        id: `file:${relativePath}`,
        kind: 'file',
        path: relativePath,
        reasons,
        migration_residual: isResidualSource,
      });
    }

    if (path.basename(relativePath) === 'package.json') scanPackageJson(absolutePath, relativePath, add, issues);
    if (path.basename(relativePath) === 'pyproject.toml') scanPyproject(absolutePath, relativePath, add);
    if (/^\.github\/(?:workflows\/.+|actions\/.+\/action)\.ya?ml$/iu.test(relativePath)) {
      add({
        id: `ci-workflow:${relativePath}`,
        kind: 'ci-workflow',
        path: relativePath,
        reasons: [mode === 'untracked' ? 'standard untracked workflow' : 'workflow file'],
        migration_residual: false,
      });
      scanWorkflow(absolutePath, relativePath, add);
    }
    if (/\.html?$/iu.test(relativePath)) scanInlineScripts(absolutePath, relativePath, add);
    if (ACTIVE_COMMAND_DOCS.has(relativePath)) scanDocumentCommands(absolutePath, relativePath, add);
    if (EXECUTABLE_CONFIG_RE.test(relativePath)) {
      const text = fs.readFileSync(absolutePath, 'utf8');
      add({
        id: `executable-config:${relativePath}`,
        kind: 'executable-config',
        path: relativePath,
        reasons: ['executable build/task configuration'],
        migration_residual: NON_TS_COMMAND_RE.test(text),
      });
      if (/\.ya?ml$/iu.test(relativePath)) scanWorkflow(absolutePath, relativePath, add);
    }
  }

  applyFileContentFingerprints(repoRoot, files, entries);
  return {
    repo_root: toPosix(repoRoot),
    generated_from_head: generatedFromHead,
    entries: [...entries.values()].sort((a, b) => compareText(a.id, b.id)),
    issues,
  };
}

export type InventoryDocumentFormat = 'json' | 'jsonl';

function validateInventoryHeader(
  header: Record<string, unknown>,
  expectedBaselineSha: string,
  expectedBaselineTreeSha: string,
  expectedScanScopeSha256: string,
  issues: ValidationIssue[],
): void {
  if (header.schema_version !== 1) {
    issues.push({ code: 'inventory_document_schema_version', message: 'inventory schema_version 必须为 1' });
  }
  if (header.kind !== INVENTORY_KIND) {
    issues.push({ code: 'inventory_document_kind', message: `inventory kind 必须为 ${INVENTORY_KIND}` });
  }
  if (!isNonEmptyString(header.baseline_sha) || !GIT_SHA_RE.test(header.baseline_sha)) {
    issues.push({ code: 'inventory_document_baseline_sha', message: 'baseline_sha 必须是 7–64 位十六进制 Git SHA' });
  }
  if (header.baseline_sha !== expectedBaselineSha) {
    issues.push({ code: 'inventory_document_baseline_mismatch', message: 'inventory baseline_sha does not match the frozen source anchor' });
  }
  if (!isNonEmptyString(header.baseline_tree_sha) || !GIT_OBJECT_RE.test(header.baseline_tree_sha)) {
    issues.push({ code: 'inventory_document_baseline_tree_sha', message: 'baseline_tree_sha 必须是完整 Git tree object ID' });
  }
  if (header.baseline_tree_sha !== expectedBaselineTreeSha) {
    issues.push({ code: 'inventory_document_baseline_tree_mismatch', message: 'inventory baseline_tree_sha 与 frozen source anchor 的真实 tree 不一致' });
  }
  if (!isNonEmptyString(header.scan_scope_sha256) || !/^[0-9a-f]{64}$/iu.test(header.scan_scope_sha256)) {
    issues.push({ code: 'inventory_document_scan_scope_sha256', message: 'scan_scope_sha256 必须是 64 位 SHA-256' });
  }
  if (header.scan_scope_sha256 !== expectedScanScopeSha256) {
    issues.push({ code: 'inventory_document_scan_scope_mismatch', message: 'inventory scan_scope_sha256 与当前 canonical scan scope 不一致' });
  }
}

export function parseMigrationInventoryDocument(
  value: unknown,
  format: InventoryDocumentFormat,
  expectedBaselineSha: string,
  expectedBaselineTreeSha: string,
  expectedScanScopeSha256: string,
): { records: unknown[]; issues: ValidationIssue[] } {
  const issues: ValidationIssue[] = [];
  if (format === 'json') {
    if (!isRecord(value)) {
      return {
        records: [],
        issues: [{ code: 'inventory_document_not_object', message: '正式 JSON inventory 必须是带完整 header 的 object，不接受裸 array' }],
      };
    }
    validateInventoryHeader(value, expectedBaselineSha, expectedBaselineTreeSha, expectedScanScopeSha256, issues);
    if (!Array.isArray(value.entries)) {
      issues.push({ code: 'inventory_document_entries', message: 'inventory entries 必须是 array' });
      return { records: [], issues };
    }
    return { records: value.entries, issues };
  }

  if (!Array.isArray(value) || value.length === 0 || !isRecord(value[0])) {
    return { records: [], issues: [{ code: 'inventory_jsonl_header_missing', message: 'JSONL 首条必须是 header object' }] };
  }
  const header = value[0];
  if (header.record_type !== 'header') {
    issues.push({ code: 'inventory_jsonl_header_missing', message: 'JSONL 首条 record_type 必须为 header' });
  }
  validateInventoryHeader(header, expectedBaselineSha, expectedBaselineTreeSha, expectedScanScopeSha256, issues);
  const records: unknown[] = [];
  for (const [index, row] of value.slice(1).entries()) {
    if (!isRecord(row) || row.record_type !== 'entry') {
      issues.push({ code: 'inventory_jsonl_entry_type', message: 'JSONL 后续记录的 record_type 必须为 entry', subject: `line:${index + 2}` });
      continue;
    }
    records.push(row);
  }
  return { records, issues };
}

export function verifyMigrationInventoryDocument(
  scan: InventoryScan,
  value: unknown,
  format: InventoryDocumentFormat,
  expectedBaselineSha: string,
  completedPhases: ReadonlySet<string> = new Set(),
): ValidationResult {
  const documentIssues: ValidationIssue[] = [];
  let expectedBaselineTreeSha = '';
  try {
    expectedBaselineTreeSha = resolveInventoryBaselineTree(scan.repo_root, expectedBaselineSha);
  } catch (error: unknown) {
    documentIssues.push({
      code: 'inventory_baseline_tree_resolution_failed',
      message: error instanceof Error ? error.message : String(error),
      subject: expectedBaselineSha,
    });
  }
  const expectedScanScopeSha256 = inventoryScanScopeSha256(scan.entries);
  const parsed = parseMigrationInventoryDocument(
    value,
    format,
    expectedBaselineSha,
    expectedBaselineTreeSha,
    expectedScanScopeSha256,
  );
  const core = verifyMigrationInventory(scan, parsed.records, completedPhases);
  return validationResult([...documentIssues, ...parsed.issues, ...core.issues]);
}

export function verifyMigrationInventory(
  scan: InventoryScan,
  records: readonly unknown[],
  completedPhases: ReadonlySet<string> = new Set(),
): ValidationResult {
  const issues: ValidationIssue[] = [...scan.issues];
  const discoveredById = new Map(scan.entries.map((entry) => [entry.id, entry]));
  const recordsById = new Map<string, Array<{ index: number; row: Record<string, unknown> }>>();

  for (const [index, value] of records.entries()) {
    const subject = `entry:${index + 1}`;
    if (!isRecord(value)) {
      issues.push({ code: 'inventory_entry_not_object', message: 'inventory entry 必须是 object', subject });
      continue;
    }
    if (!isNonEmptyString(value.id)) {
      issues.push({ code: 'inventory_id_missing', message: 'id 缺失或为空', subject });
      continue;
    }
    const id = value.id.trim();
    const group = recordsById.get(id) ?? [];
    group.push({ index, row: value });
    recordsById.set(id, group);

    if (!isNonEmptyString(value.owner)) issues.push({ code: 'inventory_owner_missing', message: 'owner 缺失或为空', subject: id });
    const disposition = value.disposition;
    if (disposition !== 'port' && disposition !== 'replace' && disposition !== 'retire' && disposition !== 'keep') {
      issues.push({ code: 'inventory_disposition_invalid', message: 'disposition 必须为 port/replace/retire/keep', subject: id });
      continue;
    }
    if (disposition === 'port' || disposition === 'replace') {
      if (!isNonEmptyString(value.target)) issues.push({ code: 'inventory_target_missing', message: `${disposition} 必须填写 target`, subject: id });
    }
    if (disposition === 'retire' && isNonEmptyString(value.target)) {
      issues.push({ code: 'inventory_retire_has_target', message: 'retire 不得填写 target', subject: id });
    }
    if (disposition !== 'keep' && !isNonEmptyString(value.target_phase)) {
      issues.push({ code: 'inventory_target_phase_missing', message: `${disposition} 必须填写 target_phase`, subject: id });
    }

    const discovered = discoveredById.get(id);
    if (discovered === undefined) continue;
    if (value.kind !== discovered.kind) issues.push({ code: 'inventory_kind_mismatch', message: `kind 应为 ${discovered.kind}`, subject: id });
    if (value.path !== discovered.path) issues.push({ code: 'inventory_path_mismatch', message: `path 应为 ${discovered.path}`, subject: id });
    if (value.file_mode !== discovered.file_mode) {
      issues.push({ code: 'inventory_file_mode_mismatch', message: `file_mode 应为 ${discovered.file_mode}`, subject: id });
    }
    if (value.git_blob !== discovered.git_blob) {
      issues.push({ code: 'inventory_git_blob_mismatch', message: `git_blob 应为 ${discovered.git_blob}`, subject: id });
    }
    if (discovered.fingerprint !== undefined && value.fingerprint !== discovered.fingerprint) {
      issues.push({ code: 'inventory_fingerprint_mismatch', message: `entrypoint 内容漂移；fingerprint 应为 ${discovered.fingerprint}`, subject: id });
    }
    if (discovered.migration_residual && disposition === 'keep') {
      issues.push({ code: 'inventory_residual_marked_keep', message: '非 TS 残留必须 port/replace/retire，不得 keep', subject: id });
    }
    if (
      discovered.migration_residual
      && isNonEmptyString(value.target_phase)
      && completedPhases.has(value.target_phase.trim())
    ) {
      issues.push({ code: 'inventory_residual_after_phase', message: `目标阶段 ${value.target_phase.trim()} 已完成但残留仍存在`, subject: id });
    }
  }

  for (const [id, group] of recordsById) {
    if (group.length > 1) issues.push({ code: 'inventory_duplicate_id', message: `inventory id 登记 ${group.length} 次`, subject: id });
  }
  for (const entry of scan.entries) {
    if (!recordsById.has(entry.id)) {
      issues.push({ code: 'inventory_unregistered', message: '发现未登记/新增的 tracked executable entry', subject: entry.id });
    }
  }
  for (const [id] of recordsById) {
    if (!discoveredById.has(id)) {
      issues.push({ code: 'inventory_dangling', message: 'inventory 记录没有对应的 tracked executable entry', subject: id });
    }
  }
  return validationResult(issues);
}

export function inventoryTemplate(scan: InventoryScan, baselineSha: string): Record<string, unknown> {
  if (baselineSha !== scan.generated_from_head) {
    throw new Error(`inventory baseline must equal current HEAD: ${baselineSha} != ${scan.generated_from_head}`);
  }
  return {
    schema_version: 1,
    kind: INVENTORY_KIND,
    baseline_sha: baselineSha,
    baseline_tree_sha: resolveInventoryBaselineTree(scan.repo_root, baselineSha),
    scan_scope_sha256: inventoryScanScopeSha256(scan.entries),
    entries: scan.entries.map((entry) => ({
      id: entry.id,
      kind: entry.kind,
      path: entry.path,
      file_mode: entry.file_mode,
      git_blob: entry.git_blob,
      ...(entry.fingerprint === undefined ? {} : { fingerprint: entry.fingerprint }),
      owner: '',
      disposition: entry.migration_residual ? 'port' : 'keep',
      target_phase: entry.migration_residual ? '' : null,
      target: entry.migration_residual ? '' : null,
    })),
  };
}

interface InventorySyntheticFixture {
  baseline_sha: string;
  scan: InventoryScan;
  records: MigrationInventoryRecord[];
  completed: Set<string>;
}

function inventorySyntheticFixture(): InventorySyntheticFixture {
  const repoRoot = path.resolve(runGit('.', ['rev-parse', '--show-toplevel']).trim());
  const baselineSha = runGit(repoRoot, ['rev-parse', 'HEAD']).trim().toLowerCase();
  const scan: InventoryScan = {
    repo_root: toPosix(repoRoot),
    generated_from_head: baselineSha,
    issues: [],
    entries: [
      {
        id: 'file:legacy.py', kind: 'file', path: 'legacy.py', reasons: ['.py'], migration_residual: true,
        file_mode: '100644', git_blob: 'a'.repeat(40), fingerprint: 'a'.repeat(64),
      },
      {
        id: 'package-script:package.json#test', kind: 'package-script', path: 'package.json', reasons: ['script'],
        migration_residual: false, file_mode: '100644', git_blob: 'b'.repeat(40), detail: 'node --test',
        fingerprint: commandFingerprint('package-script', 'node --test'),
      },
    ],
  };
  return {
    baseline_sha: baselineSha,
    scan,
    completed: new Set(),
    records: [
      {
        id: 'file:legacy.py', kind: 'file', path: 'legacy.py', owner: 'owner-a', disposition: 'port', target_phase: 'C2',
        target: 'legacy.ts', file_mode: '100644', git_blob: 'a'.repeat(40), fingerprint: 'a'.repeat(64),
      },
      {
        id: 'package-script:package.json#test', kind: 'package-script', path: 'package.json', owner: 'owner-b', disposition: 'keep',
        file_mode: '100644', git_blob: 'b'.repeat(40), fingerprint: commandFingerprint('package-script', 'node --test'),
      },
    ],
  };
}

export function runInventorySyntheticMutants(): { ok: boolean; passed: number; total: number; failures: string[] } {
  const baseline = inventorySyntheticFixture();
  const baselineSha = baseline.baseline_sha;
  const baselineResult = verifyMigrationInventory(baseline.scan, baseline.records, baseline.completed);
  if (!baselineResult.ok) {
    return { ok: false, passed: 0, total: 1, failures: [`baseline unexpectedly failed: ${baselineResult.issues.map((item) => item.code).join(', ')}`] };
  }
  const cases: Array<{ name: string; code: string; mutate: (fixture: InventorySyntheticFixture) => void }> = [
    { name: 'unregistered', code: 'inventory_unregistered', mutate: (f) => { f.records.splice(0, 1); } },
    {
      name: 'dangling', code: 'inventory_dangling', mutate: (f) => {
        f.records.push({
          id: 'file:gone.py', kind: 'file', path: 'gone.py', file_mode: '100644', git_blob: 'c'.repeat(40),
          owner: 'x', disposition: 'retire', target_phase: 'C2',
        });
      },
    },
    { name: 'duplicate', code: 'inventory_duplicate_id', mutate: (f) => { f.records.push(structuredClone(f.records[0]!)); } },
    { name: 'owner', code: 'inventory_owner_missing', mutate: (f) => { f.records[0]!.owner = ''; } },
    { name: 'keep residual', code: 'inventory_residual_marked_keep', mutate: (f) => { f.records[0]!.disposition = 'keep'; } },
    { name: 'fingerprint', code: 'inventory_fingerprint_mismatch', mutate: (f) => { f.records[1]!.fingerprint = '0'.repeat(64); } },
    { name: 'file fingerprint', code: 'inventory_fingerprint_mismatch', mutate: (f) => { f.records[0]!.fingerprint = '0'.repeat(64); } },
    { name: 'file mode', code: 'inventory_file_mode_mismatch', mutate: (f) => { f.records[0]!.file_mode = '100755'; } },
    { name: 'git blob', code: 'inventory_git_blob_mismatch', mutate: (f) => { f.records[0]!.git_blob = '0'.repeat(40); } },
    { name: 'expired phase', code: 'inventory_residual_after_phase', mutate: (f) => { f.completed.add('C2'); } },
  ];
  let passed = 0;
  const failures: string[] = [];
  for (const item of cases) {
    const fixture = structuredClone(baseline);
    item.mutate(fixture);
    const result = verifyMigrationInventory(fixture.scan, fixture.records, fixture.completed);
    if (result.issues.some((issue) => issue.code === item.code)) passed += 1;
    else failures.push(`${item.name}: expected ${item.code}, got ${result.issues.map((issue) => issue.code).join(', ') || 'PASS'}`);
  }
  const document = {
    schema_version: 1,
    kind: INVENTORY_KIND,
    baseline_sha: baselineSha,
    baseline_tree_sha: resolveInventoryBaselineTree(baseline.scan.repo_root, baselineSha),
    scan_scope_sha256: inventoryScanScopeSha256(baseline.scan.entries),
    entries: structuredClone(baseline.records),
  };
  const documentBaseline = verifyMigrationInventoryDocument(baseline.scan, document, 'json', baselineSha);
  if (!documentBaseline.ok) failures.push(`inventory document baseline unexpectedly failed: ${documentBaseline.issues.map((issue) => issue.code).join(', ')}`);
  const documentCases: Array<{ name: string; code: string; mutate: (value: Record<string, unknown>) => unknown }> = [
    { name: 'formal JSON bare array', code: 'inventory_document_not_object', mutate: (value) => value.entries },
    {
      name: 'document schema', code: 'inventory_document_schema_version',
      mutate: (value) => { value.schema_version = 2; return value; },
    },
    { name: 'document kind', code: 'inventory_document_kind', mutate: (value) => { value.kind = 'wrong'; return value; } },
    {
      name: 'document baseline', code: 'inventory_document_baseline_mismatch',
      mutate: (value) => { value.baseline_sha = 'f'.repeat(40); return value; },
    },
    {
      name: 'document baseline tree', code: 'inventory_document_baseline_tree_mismatch',
      mutate: (value) => { value.baseline_tree_sha = 'f'.repeat(40); return value; },
    },
    {
      name: 'document scan scope', code: 'inventory_document_scan_scope_mismatch',
      mutate: (value) => { value.scan_scope_sha256 = 'f'.repeat(64); return value; },
    },
  ];
  for (const item of documentCases) {
    const value = structuredClone(document);
    const result = verifyMigrationInventoryDocument(baseline.scan, item.mutate(value), 'json', baselineSha);
    if (result.issues.some((issue) => issue.code === item.code)) passed += 1;
    else failures.push(`${item.name}: expected ${item.code}, got ${result.issues.map((issue) => issue.code).join(', ') || 'PASS'}`);
  }
  const jsonl: unknown[] = [
    {
      record_type: 'header', schema_version: 1, kind: INVENTORY_KIND, baseline_sha: baselineSha,
      baseline_tree_sha: resolveInventoryBaselineTree(baseline.scan.repo_root, baselineSha),
      scan_scope_sha256: inventoryScanScopeSha256(baseline.scan.entries),
    },
    ...baseline.records.map((record) => ({ record_type: 'entry', ...record })),
  ];
  const jsonlBaseline = verifyMigrationInventoryDocument(baseline.scan, jsonl, 'jsonl', baselineSha);
  if (jsonlBaseline.ok) passed += 1;
  else failures.push(`explicit JSONL inventory unexpectedly failed: ${jsonlBaseline.issues.map((issue) => issue.code).join(', ')}`);
  const missingHeader = verifyMigrationInventoryDocument(baseline.scan, jsonl.slice(1), 'jsonl', baselineSha);
  if (missingHeader.issues.some((issue) => issue.code === 'inventory_jsonl_header_missing')) passed += 1;
  else failures.push(`JSONL inventory missing header: got ${missingHeader.issues.map((issue) => issue.code).join(', ') || 'PASS'}`);
  return { ok: failures.length === 0, passed, total: cases.length + documentCases.length + 2, failures };
}
