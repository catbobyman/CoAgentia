import { spawnSync } from 'node:child_process';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import {
  compareText,
  validationResult,
  type ValidationIssue,
  type ValidationResult,
} from './shared.ts';

export const AUTHORITY_KIND = 'coagentia.plan-authority';

const ROOT_PLAN = 'plan.md';
const AUTHORITY_RECORD = 'docs/verify/ts-migration/PLAN-AUTHORITY.md';
const HISTORICAL_PLANS = [
  'docs/project-handoffs/TS-MIGRATION-ROADMAP.md',
  'docs/project-handoffs/TS-DEV-PLAN.md',
] as const;
const ACTIVE_ENTRYPOINTS = [
  'README.md',
  'README_EN.md',
  'README_JA.md',
  'AGENTS.md',
  'docs/project-handoffs/CURRENT-HANDOFF.md',
  'docs/project-handoffs/README.md',
] as const;

const AUTHORITY_CLAIM_RE = /(?:唯一(?:的|迁移|计划)?(?:执行)?权威|only active authority|single source of truth|(?:the\s+)?current migration authority|active migration authority|authoritative migration (?:plan|source)|canonical migration plan(?:\s+to\s+follow)?|migration plan to follow|official migration plan|controlling migration plan|当前(?:迁移)?(?:执行)?权威|唯一の\s*active authority)/iu;
const ROOT_DELEGATION_CLAIM_RE = /(?:唯一(?:迁移|计划|执行)权威|only active authority|(?:the\s+)?current migration authority|active migration authority|authoritative migration (?:plan|source)|canonical migration plan(?:\s+to\s+follow)?|migration plan to follow|official migration plan|controlling migration plan|当前迁移(?:执行)?权威|唯一の\s*active authority)/iu;
const SELF_AUTHORITY_CLAIM_RE = /(?:(?:this|the)\s+(?:document|file|readme|record|handoff|roadmap))\s+(?:is|remains|becomes)\s+(?:(?:the|an)\s+)?(?:current|active|only)[^.;；。\n]{0,40}authority|(?:本文|本文件|本声明|本记录|本交接|本路线图|该文档).{0,30}(?:当前|唯一|active).{0,20}权威/iu;
const ROOT_PLAN_TOKEN_RE = /(?<![A-Za-z0-9_-])plan\.md(?![A-Za-z0-9_-])/iu;
const MARKDOWN_LINK_RE = /(?<!!)\[[^\]\r\n]*\]\(\s*(?:<([^>\r\n]+)>|([^\s)]+))(?:\s+["'][^)]*["'])?\s*\)/gu;

interface MarkdownLink {
  target: string;
  index: number;
  length: number;
}

function withoutHtmlComments(text: string): string {
  return text.replace(/<!--[\s\S]*?(?:-->|$)/gu, '');
}

function markdownLinks(text: string): MarkdownLink[] {
  const links: MarkdownLink[] = [];
  for (const match of withoutHtmlComments(text).matchAll(MARKDOWN_LINK_RE)) {
    const target = (match[1] ?? match[2] ?? '').trim();
    if (target.length > 0) links.push({ target, index: match.index ?? 0, length: match[0].length });
  }
  return links;
}

function linkTargetsRootPlan(sourcePath: string, target: string): boolean {
  const withoutFragment = target.split(/[?#]/u, 1)[0]!.replaceAll('\\', '/');
  if (withoutFragment.length === 0 || /^[A-Za-z][A-Za-z+.-]*:/u.test(withoutFragment) || withoutFragment.startsWith('/')) {
    return false;
  }
  let decoded: string;
  try {
    decoded = decodeURIComponent(withoutFragment);
  } catch {
    return false;
  }
  if (decoded.startsWith('/') || /^[A-Za-z]:/u.test(decoded)) return false;
  const sourceDirectory = path.posix.dirname(toPosix(sourcePath));
  return path.posix.normalize(path.posix.join(sourceDirectory, decoded)) === ROOT_PLAN;
}

function hasRootPlanLink(sourcePath: string, text: string): boolean {
  return markdownLinks(text).some((link) => linkTargetsRootPlan(sourcePath, link.target));
}

function normalizeRootPlanReferences(sourcePath: string, text: string): string {
  let normalized = withoutHtmlComments(text);
  const sourceDirectory = path.posix.dirname(toPosix(sourcePath));
  const canonicalReference = path.posix.relative(sourceDirectory, ROOT_PLAN) || ROOT_PLAN;
  const rootLinks = markdownLinks(normalized)
    .filter((link) => linkTargetsRootPlan(sourcePath, link.target))
    .sort((left, right) => right.index - left.index);
  for (const link of rootLinks) {
    normalized = `${normalized.slice(0, link.index)}${canonicalReference}${normalized.slice(link.index + link.length)}`;
  }
  return normalized;
}

function hasAlternatePlanSubjectClaim(sourcePath: string, text: string): boolean {
  const visible = normalizeRootPlanReferences(sourcePath, text);
  return visible.split(/[;；。\r\n]+/u).some((clause) => {
    const claims = [...clause.matchAll(new RegExp(AUTHORITY_CLAIM_RE.source, `${AUTHORITY_CLAIM_RE.flags}g`))];
    if (claims.length === 0) return false;
    const planTokens = [...clause.matchAll(/(?<![A-Za-z0-9_.-])((?:(?:\.{1,2}|[A-Za-z0-9_.-]+)[\\/])*[A-Za-z0-9_.-]+\.md(?:#[A-Za-z0-9_.-]+)?)/giu)]
      .map((match) => ({ target: match[1]!, index: match.index ?? 0, length: match[0].length }));
    return claims.some((claim) => {
      if (planTokens.length === 0) return false;
      const claimStart = claim.index ?? 0;
      const claimEnd = claimStart + claim[0].length;
      const distances = planTokens.map((token) => ({
        ...token,
        distance: token.index + token.length <= claimStart
          ? claimStart - (token.index + token.length)
          : token.index >= claimEnd
            ? token.index - claimEnd
            : 0,
      }));
      const nearestDistance = Math.min(...distances.map((item) => item.distance));
      return distances.some((item) => (
        item.distance === nearestDistance && !linkTargetsRootPlan(sourcePath, item.target)
      ));
    });
  });
}

const DIRECT_BINDING_LATIN_WORDS = new Set([
  'a', 'an', 'as', 'becomes', 'coagentia', 'for', 'full', 'is', 'migration', 'of', 'remains',
  'root', 'the', 'typescript',
]);

function bridgeDirectlyBindsRoot(value: string): boolean {
  if ([...value].length > 80) return false;
  const words = value.match(/[A-Za-z][A-Za-z0-9.-]*/gu) ?? [];
  if (!words.every((word) => (
    DIRECT_BINDING_LATIN_WORDS.has(word.toLowerCase()) || /^v\d+(?:\.\d+)*$/iu.test(word)
  ))) return false;
  let remainder = value.toLowerCase().replace(/\bv\d+(?:\.\d+)*\b/gu, '');
  for (const word of [...DIRECT_BINDING_LATIN_WORDS].sort((left, right) => right.length - left.length)) {
    remainder = remainder.replace(new RegExp(`\\b${word}\\b`, 'gu'), '');
  }
  remainder = remainder.replace(/(?:仓库根|仓库|全量|当前|迁移|根|为|是|的|全面|移行|ルート|です|である|は|が|を|の)/gu, '');
  return remainder.replace(/[\p{White_Space}\p{P}\p{S}\p{N}_]+/gu, '').length === 0;
}

function claimDirectlyBindsRootLink(
  sourcePath: string,
  clause: string,
  claimIndex: number,
  claimLength: number,
): boolean {
  const claimEnd = claimIndex + claimLength;
  return markdownLinks(clause)
    .filter((link) => linkTargetsRootPlan(sourcePath, link.target))
    .some((link) => {
      const linkEnd = link.index + link.length;
      if (link.index <= claimEnd && linkEnd >= claimIndex) return true;
      if (linkEnd <= claimIndex) return bridgeDirectlyBindsRoot(clause.slice(linkEnd, claimIndex));
      return bridgeDirectlyBindsRoot(clause.slice(claimEnd, link.index));
    });
}

function referenceDirectlyBindsClaim(
  clause: string,
  reference: { index: number; length: number },
  claim: { index: number; length: number },
): boolean {
  const referenceEnd = reference.index + reference.length;
  const claimEnd = claim.index + claim.length;
  if (reference.index <= claimEnd && referenceEnd >= claim.index) return true;
  if (referenceEnd <= claim.index) return bridgeDirectlyBindsRoot(clause.slice(referenceEnd, claim.index));
  return bridgeDirectlyBindsRoot(clause.slice(claimEnd, reference.index));
}

function rootPlanForeignAuthorityClauses(text: string): string[] {
  return normalizeRootPlanReferences(ROOT_PLAN, text).split(/[;；。\r\n]+/u).filter((clause) => {
    const claims = [...clause.matchAll(new RegExp(ROOT_DELEGATION_CLAIM_RE.source, `${ROOT_DELEGATION_CLAIM_RE.flags}g`))];
    if (claims.length === 0) return false;
    const references = [
      ...[...clause.matchAll(/(?:本文件|本计划|本文计划|this\s+(?:plan|file|document)|the\s+root\s+plan)/giu)]
        .map((match) => ({ index: match.index ?? 0, length: match[0].length })),
      ...[...clause.matchAll(/(?<![A-Za-z0-9_.-])((?:(?:\.{1,2}|[A-Za-z0-9_.-]+)[\\/])*[A-Za-z0-9_.-]+\.md(?:#[A-Za-z0-9_.-]+)?)/giu)]
        .filter((match) => linkTargetsRootPlan(ROOT_PLAN, match[1]!))
        .map((match) => ({ index: match.index ?? 0, length: match[0].length })),
    ];
    return claims.some((claim) => !references.some((reference) => referenceDirectlyBindsClaim(
      clause,
      reference,
      { index: claim.index ?? 0, length: claim[0].length },
    )));
  });
}

function rootPlanHasForeignAuthorityClaim(text: string): boolean {
  return rootPlanForeignAuthorityClauses(text).length > 0;
}

function rejectsRootPlan(sourcePath: string, text: string): boolean {
  return normalizeRootPlanReferences(sourcePath, text).split(/\r?\n/u).some((line) => line
    .split(/[;；。]/u)
    .some((clause) => {
      const roots = [...clause.matchAll(new RegExp(ROOT_PLAN_TOKEN_RE.source, `${ROOT_PLAN_TOKEN_RE.flags}g`))];
      return roots.some((root) => {
        const after = clause.slice((root.index ?? 0) + root[0].length);
        const before = clause.slice(0, root.index ?? 0);
        return /^[\s)`*_\]]*(?:v\d+(?:\.\d+)*\s*)?(?:(?:is|was|has been|becomes|作为|仅作|只作|已|被)\s*)?(?:deprecated|superseded|historical|background|reference(?:\s+only)?|legacy|old|废弃|作废|历史|背景|参考|旧|不再(?:是|作为)?(?:执行)?权威)/iu.test(after)
          || /^[\s)`*_\]]*(?:(?:is|was)\s+)?(?:not|no longer)\s+(?:the\s+)?(?:authoritative|authority)/iu.test(after)
          || /^[\s)`*_\]]*(?:不是|并非|不再是).{0,12}权威/iu.test(after)
          || /(?:deprecated|superseded|废弃|作废)\s+(?:(?:the|root)\s+|根\s*)?$/iu.test(before);
      });
    }));
}

function hasUnboundAuthorityClaim(sourcePath: string, text: string): boolean {
  const visible = withoutHtmlComments(text);
  return visible.split(/[;；。\r\n]+/u).some((clause) => {
    if (SELF_AUTHORITY_CLAIM_RE.test(clause)) return true;
    const ownName = path.posix.basename(sourcePath).replace(/[.*+?^${}()|[\]\\]/gu, '\\$&');
    if (new RegExp(`(?:^|[^A-Za-z0-9_.-])${ownName}\\s+(?:is|remains|becomes)\\s+(?:(?:the|an)\\s+)?(?:current|active|only)[^.;；。\\n]{0,40}authority`, 'iu').test(clause)) {
      return true;
    }
    const claimPattern = new RegExp(AUTHORITY_CLAIM_RE.source, `${AUTHORITY_CLAIM_RE.flags}g`);
    const links = markdownLinks(clause);
    for (const claim of clause.matchAll(claimPattern)) {
      if (
        links.length === 0
        || links.some((link) => !linkTargetsRootPlan(sourcePath, link.target))
        || !claimDirectlyBindsRootLink(sourcePath, clause, claim.index ?? 0, claim[0].length)
      ) return true;
    }
    return hasAlternatePlanSubjectClaim(sourcePath, clause) || rejectsRootPlan(sourcePath, clause);
  });
}

function hasAlternateLinkedAuthorityClaim(sourcePath: string, text: string): boolean {
  return withoutHtmlComments(text).split(/[;；。\r\n]+/u).some((clause) => (
    AUTHORITY_CLAIM_RE.test(clause)
    && markdownLinks(clause).some((link) => !linkTargetsRootPlan(sourcePath, link.target))
  ));
}

function hasExplicitHistoricalHeader(text: string): boolean {
  const header = text.split(/\r?\n/u).slice(0, 10).join('\n');
  return /^\s*(?:>\s*)?(?:\*\*)?(?:执行状态|status)\s*[:：][^\n]*(?:SUPERSEDED|HISTORICAL)/imu.test(header);
}

function hasExplicitActiveStatus(text: string): boolean {
  return /(?:执行状态|status|当前状态|stage)\s*[:：][^\n]*(?:ACTIVE|IN PROGRESS|进行中)/iu.test(withoutHtmlComments(text));
}

function toPosix(value: string): string {
  return value.replaceAll('\\', '/');
}

function resolveInside(repoRoot: string, relativePath: string): string {
  const absolute = path.resolve(repoRoot, ...relativePath.split('/'));
  const guard = path.relative(repoRoot, absolute);
  if (guard.startsWith('..') || path.isAbsolute(guard)) {
    throw new Error(`authority path 逃出 repo root: ${relativePath}`);
  }
  return absolute;
}

function readRequired(repoRoot: string, relativePath: string, issues: ValidationIssue[]): string | undefined {
  const absolute = resolveInside(repoRoot, relativePath);
  if (!fs.existsSync(absolute) || !fs.statSync(absolute).isFile()) {
    issues.push({ code: 'authority_required_file_missing', message: '权威拓扑必需文件不存在', subject: relativePath });
    return undefined;
  }
  return fs.readFileSync(absolute, 'utf8');
}

function trackedRepositoryPaths(repoRoot: string): Set<string> | undefined {
  const probe = spawnSync('git', ['-C', repoRoot, 'rev-parse', '--is-inside-work-tree'], {
    encoding: 'utf8',
    windowsHide: true,
  });
  if (probe.error !== undefined || probe.status !== 0) return undefined;
  const result = spawnSync('git', ['-C', repoRoot, 'ls-files', '-z'], {
    encoding: 'utf8',
    windowsHide: true,
  });
  if (result.error !== undefined || result.status !== 0) return undefined;
  return new Set(result.stdout.split('\0').filter((item) => item.length > 0).map(toPosix));
}

function activePrefix(relativePath: string, text: string): string {
  if (relativePath === 'docs/project-handoffs/CURRENT-HANDOFF.md') {
    return text.split(/^## 历史交接明细/mu, 1)[0] ?? text;
  }
  if (relativePath === 'docs/project-handoffs/README.md') {
    return text.split(/^## 原文件迁移表/mu, 1)[0] ?? text;
  }
  return text;
}

function walkFiles(root: string): string[] {
  if (!fs.existsSync(root)) return [];
  const output: string[] = [];
  const stack = [root];
  while (stack.length > 0) {
    const current = stack.pop()!;
    for (const item of fs.readdirSync(current, { withFileTypes: true })) {
      const child = path.join(current, item.name);
      if (item.isDirectory()) stack.push(child);
      else if (item.isFile()) output.push(child);
    }
  }
  return output.sort(compareText);
}

function repositoryMarkdownFiles(repoRoot: string): string[] {
  const result = spawnSync('git', ['-C', repoRoot, 'ls-files', '--cached', '--others', '--exclude-standard'], {
    encoding: 'utf8',
    shell: false,
    windowsHide: true,
  });
  if (result.error === undefined && result.status === 0) {
    return result.stdout
      .split(/\r?\n/u)
      .map((item) => item.trim())
      .filter((item) => /\.md$/iu.test(item))
      .map((item) => resolveInside(repoRoot, toPosix(item)))
      .sort(compareText);
  }
  return walkFiles(repoRoot).filter((item) => /\.md$/iu.test(item));
}

function githubConfigurationText(text: string): string {
  return text
    .split(/\r?\n/u)
    .filter((line) => !/^\s*#/u.test(line))
    .map((line) => line.replace(/\s+#.*$/u, ''))
    .join('\n');
}

function githubConfigurationRootReference(relativePath: string, text: string): {
  present: boolean;
  conflictingPlanVariable: boolean;
} {
  if (/\.md$/iu.test(relativePath)) {
    return { present: hasRootPlanLink(relativePath, text), conflictingPlanVariable: false };
  }
  const activeText = githubConfigurationText(text);
  const assignments = [...activeText.matchAll(/(?:^|[{,\s])['"]?MIGRATION_PLAN['"]?\s*:\s*(?:'([^']+)'|"([^"]+)"|([^,}\s]+))/gimu)]
    .map((match) => (match[1] ?? match[2] ?? match[3] ?? '').replaceAll('\\', '/'));
  return {
    present: hasRootPlanLink(relativePath, activeText) || assignments.some((value) => value === ROOT_PLAN),
    conflictingPlanVariable: assignments.some((value) => value !== ROOT_PLAN),
  };
}

export function verifyPlanAuthority(repo = '.'): ValidationResult {
  const repoRoot = path.resolve(repo);
  const issues: ValidationIssue[] = [];
  let rootPhase: 'p0_in_progress' | 'p0_complete_p1_ready' | undefined;
  const tracked = trackedRepositoryPaths(repoRoot);
  if (tracked !== undefined) {
    for (const required of [ROOT_PLAN, AUTHORITY_RECORD, ...HISTORICAL_PLANS, ...ACTIVE_ENTRYPOINTS]) {
      if (!tracked.has(required)) {
        issues.push({
          code: 'authority_required_file_untracked',
          message: '权威拓扑必需文件必须进入 Git index，不能依赖 ignored 本机残留',
          subject: required,
        });
      }
    }
  }

  const rootPlan = readRequired(repoRoot, ROOT_PLAN, issues);
  if (rootPlan !== undefined) {
    if (!/\bv1\.0\b/u.test(rootPlan)) {
      issues.push({ code: 'authority_root_version_missing', message: '根计划必须声明 v1.0', subject: ROOT_PLAN });
    }
    if (!/owner 已于 2026-07-20 批准/u.test(rootPlan)) {
      issues.push({ code: 'authority_root_approval_missing', message: '根计划必须保留 owner 批准记录', subject: ROOT_PLAN });
    }
    const currentStatuses = [...rootPlan.matchAll(/^> 当前状态：([^\r\n]+)$/gmu)].map((match) => match[1]);
    const p0Rows = [...rootPlan.matchAll(/^\| P0 基线\/试验裁决 \| ([^|\r\n]+) \|/gmu)]
      .map((match) => match[1]?.trim());
    const p1Rows = [...rootPlan.matchAll(/^\| P1 冻结事实源 \| ([^|\r\n]+) \|/gmu)]
      .map((match) => match[1]?.trim());
    const p0InProgress = currentStatuses.length === 1
      && currentStatuses[0] === 'P0「基线、范围和当前试验裁决」进行中'
      && p0Rows.length === 1
      && p0Rows[0] === '**进行中**'
      && p1Rows.length === 1
      && p1Rows[0] === '未开始';
    const p0Complete = currentStatuses.length === 1
      && currentStatuses[0] === 'P0「基线、范围和当前试验裁决」已完成并 reviewed；P1 可进入但尚未启动'
      && p0Rows.length === 1
      && p0Rows[0] === '**已完成（reviewed/accepted）**'
      && p1Rows.length === 1
      && p1Rows[0] === '**未开始（可进入）**'
      && /^\| P1 冻结事实源 \| \*\*未开始（可进入）\*\* \| \*\*P0 reviewed 已满足\*\* \|/mu.test(rootPlan);
    if (p0InProgress) rootPhase = 'p0_in_progress';
    else if (p0Complete) rootPhase = 'p0_complete_p1_ready';
    else {
      issues.push({
        code: 'authority_root_phase_mismatch',
        message: '根计划阶段必须是 P0 进行中，或 P0 reviewed/accepted 且 P1 仅为可进入未启动',
        subject: ROOT_PLAN,
      });
    }
    if (
      rejectsRootPlan(ROOT_PLAN, rootPlan)
      || hasAlternateLinkedAuthorityClaim(ROOT_PLAN, rootPlan)
      || hasAlternatePlanSubjectClaim(ROOT_PLAN, rootPlan)
      || rootPlanHasForeignAuthorityClaim(rootPlan)
    ) {
      issues.push({ code: 'authority_root_plan_rejected', message: '根计划不得声明自身已废弃、仅作背景、不具权威性或把权威让给其他链接', subject: ROOT_PLAN });
    }
  }

  const authority = readRequired(repoRoot, AUTHORITY_RECORD, issues);
  if (authority !== undefined) {
    if (!hasRootPlanLink(AUTHORITY_RECORD, authority)) {
      issues.push({ code: 'authority_record_root_link_missing', message: '权威声明必须链接仓库根 plan.md', subject: AUTHORITY_RECORD });
    }
    const authorityPhases = [...authority.matchAll(/^> 当前阶段：([^\r\n]+)$/gmu)].map((match) => match[1]);
    const expectedAuthorityPhase = rootPhase === 'p0_in_progress'
      ? 'P0（进行中）'
      : rootPhase === 'p0_complete_p1_ready'
        ? 'P0 已完成并 reviewed；P1 可进入但尚未启动'
        : undefined;
    const authorityPhaseMatches = expectedAuthorityPhase !== undefined
      && authorityPhases.length === 1
      && authorityPhases[0] === expectedAuthorityPhase;
    if (!authorityPhaseMatches) {
      issues.push({ code: 'authority_record_phase_mismatch', message: '权威声明阶段必须与根计划一致', subject: AUTHORITY_RECORD });
    }
    if (rejectsRootPlan(AUTHORITY_RECORD, authority) || hasUnboundAuthorityClaim(AUTHORITY_RECORD, authority)) {
      issues.push({ code: 'authority_record_conflict', message: '权威声明不得降级根计划或把自身声明为第二权威', subject: AUTHORITY_RECORD });
    }
    for (const historicalPath of HISTORICAL_PLANS) {
      if (!authority.includes(path.posix.basename(historicalPath))) {
        issues.push({ code: 'authority_record_historical_missing', message: '权威声明必须列出历史 TS 计划', subject: historicalPath });
      }
    }
  }

  for (const historicalPath of HISTORICAL_PLANS) {
    const text = readRequired(repoRoot, historicalPath, issues);
    if (text === undefined) continue;
    const header = text.split(/\r?\n/u).slice(0, 10).join('\n');
    if (!/执行状态：SUPERSEDED \/ HISTORICAL/u.test(header)) {
      issues.push({ code: 'authority_historical_marker_missing', message: '旧迁移计划头部必须有机器可识别的 SUPERSEDED / HISTORICAL 标记', subject: historicalPath });
    }
    if (!hasRootPlanLink(historicalPath, header)) {
      issues.push({ code: 'authority_historical_root_link_missing', message: '旧迁移计划头部必须链接根 plan.md', subject: historicalPath });
    }
    if (hasExplicitActiveStatus(text) || hasUnboundAuthorityClaim(historicalPath, text) || rejectsRootPlan(historicalPath, text)) {
      issues.push({ code: 'authority_historical_reactivated', message: 'historical 计划不得追加 active/current 权威声明或降级根计划', subject: historicalPath });
    }
  }

  for (const entrypoint of ACTIVE_ENTRYPOINTS) {
    const text = readRequired(repoRoot, entrypoint, issues);
    if (text === undefined) continue;
    const active = activePrefix(entrypoint, text);
    if (!hasRootPlanLink(entrypoint, active)) {
      issues.push({ code: 'authority_entrypoint_root_link_missing', message: 'active 入口必须指向根 plan.md', subject: entrypoint });
    }
    if (rejectsRootPlan(entrypoint, active)) {
      issues.push({ code: 'authority_root_plan_rejected', message: 'active 入口不得贬低、废弃或降级根 plan.md', subject: entrypoint });
    }
    if (hasUnboundAuthorityClaim(entrypoint, active)) {
      issues.push({ code: 'authority_second_active_claim', message: 'active 入口包含未绑定根 plan.md 的第二权威声明', subject: entrypoint });
    }
  }

  const githubRoot = path.join(repoRoot, '.github');
  for (const absolute of walkFiles(githubRoot)) {
    if (!/\.(?:md|ya?ml)$/iu.test(absolute)) continue;
    const relative = toPosix(path.relative(repoRoot, absolute));
    const text = fs.readFileSync(absolute, 'utf8');
    if (!/(?:TypeScript|TS[- ]migration|ts-migration|verify:p0|迁移)/iu.test(text)) continue;
    const rootReference = githubConfigurationRootReference(relative, text);
    if (!rootReference.present) {
      issues.push({ code: 'authority_github_root_link_missing', message: '迁移相关 CI/issue/PR 文件必须指向根 plan.md', subject: relative });
    }
    if (rootReference.conflictingPlanVariable) {
      issues.push({ code: 'authority_github_plan_variable_mismatch', message: 'MIGRATION_PLAN 只能精确指向仓库根 plan.md', subject: relative });
    }
    if (/(?:TS-MIGRATION-ROADMAP|TS-DEV-PLAN)\.md/u.test(text) && !/(?:HISTORICAL|historical|历史)/u.test(text)) {
      issues.push({ code: 'authority_github_historical_as_active', message: 'GitHub 配置不得把旧 TS 计划作为 active 入口', subject: relative });
    }
  }

  for (const absolute of repositoryMarkdownFiles(repoRoot)) {
    const relative = toPosix(path.relative(repoRoot, absolute));
    if (relative === ROOT_PLAN || relative === AUTHORITY_RECORD) continue;
    const text = fs.readFileSync(absolute, 'utf8');
    if (hasExplicitHistoricalHeader(text)) {
      if (hasExplicitActiveStatus(text) || hasUnboundAuthorityClaim(relative, text) || rejectsRootPlan(relative, text)) {
        issues.push({
          code: 'authority_second_active_plan',
          message: 'historical 文档不得在后文重新声明 active/current 权威',
          subject: relative,
        });
      }
      continue;
    }
    const activeText = activePrefix(relative, text);
    const header = activeText.split(/\r?\n/u).slice(0, 80).join('\n');
    const planLikeName = /(?:plan|roadmap|migration|计划|路线图|迁移)/iu.test(path.posix.basename(relative));
    const activeStatus = /(?:执行状态|status|当前状态|stage)\s*[:：][^\n]*(?:ACTIVE|IN PROGRESS|进行中)/iu.test(header);
    const unboundAuthorityClaim = hasUnboundAuthorityClaim(relative, activeText);
    const activeRootRejection = rejectsRootPlan(relative, activeText) && (planLikeName || activeStatus || AUTHORITY_CLAIM_RE.test(activeText));
    if ((planLikeName && activeStatus) || unboundAuthorityClaim || activeRootRejection) {
      issues.push({
        code: 'authority_second_active_plan',
        message: '检测到根 plan.md 之外的 active 迁移计划/权威声明',
        subject: relative,
      });
    }
  }

  return validationResult(issues);
}

interface AuthorityMutant {
  name: string;
  code: string;
  mutate: (root: string) => void;
}

function writeFixture(root: string): void {
  const values: Record<string, string> = {
    'plan.md': '# plan v1.0\n> 当前状态：P0「基线、范围和当前试验裁决」进行中\nowner 已于 2026-07-20 批准\n| P0 基线/试验裁决 | **进行中** | owner approved | evidence |\n| P1 冻结事实源 | 未开始 | P0 reviewed | evidence |\n',
    'README.md': '[plan](plan.md) 唯一 active authority\n',
    'README_EN.md': '[plan](plan.md) is the only active authority.\n',
    'README_JA.md': '[plan](plan.md) は唯一の active authority です。\n',
    'AGENTS.md': '根 [plan.md](plan.md) 是唯一执行权威\n',
    'docs/project-handoffs/CURRENT-HANDOFF.md': '[plan](../../plan.md) 唯一执行权威\n## 历史交接明细\nold\n',
    'docs/project-handoffs/README.md': '[plan](../../plan.md) 唯一执行权威\n## 原文件迁移表\n',
    'docs/project-handoffs/TS-MIGRATION-ROADMAP.md': '> **执行状态：SUPERSEDED / HISTORICAL**\n> [plan](../../plan.md)\n',
    'docs/project-handoffs/TS-DEV-PLAN.md': '> **执行状态：SUPERSEDED / HISTORICAL**\n> [plan](../../plan.md)\n',
    'docs/verify/ts-migration/PLAN-AUTHORITY.md': '[plan](../../../plan.md)\n> 当前阶段：P0（进行中）\nTS-MIGRATION-ROADMAP.md\nTS-DEV-PLAN.md\n',
    '.github/workflows/p0.yml': 'name: TS migration\nenv:\n  MIGRATION_PLAN: plan.md\n',
  };
  for (const [relative, text] of Object.entries(values)) {
    const absolute = path.join(root, ...relative.split('/'));
    fs.mkdirSync(path.dirname(absolute), { recursive: true });
    fs.writeFileSync(absolute, text, 'utf8');
  }
  for (const args of [['init'], ['add', '-f', '.']]) {
    const result = spawnSync('git', args, { cwd: root, encoding: 'utf8', windowsHide: true });
    if (result.error !== undefined) throw result.error;
    if (result.status !== 0) throw new Error(`authority fixture git ${args.join(' ')} failed: ${result.stderr}`);
  }
}

export function runAuthoritySyntheticMutants(): { ok: boolean; passed: number; total: number; failures: string[] } {
  const mutants: AuthorityMutant[] = [
    {
      name: 'missing root plan', code: 'authority_required_file_missing',
      mutate: (root) => { fs.rmSync(path.join(root, 'plan.md')); },
    },
    {
      name: 'historical marker removed', code: 'authority_historical_marker_missing',
      mutate: (root) => { fs.writeFileSync(path.join(root, 'docs/project-handoffs/TS-DEV-PLAN.md'), '[plan](../../plan.md)\n', 'utf8'); },
    },
    {
      name: 'active entry loses root link', code: 'authority_entrypoint_root_link_missing',
      mutate: (root) => { fs.writeFileSync(path.join(root, 'README.md'), 'migration status\n', 'utf8'); },
    },
    {
      name: 'workflow points nowhere', code: 'authority_github_root_link_missing',
      mutate: (root) => { fs.writeFileSync(path.join(root, '.github/workflows/p0.yml'), 'name: TS migration\n', 'utf8'); },
    },
    {
      name: 'authority record removed', code: 'authority_required_file_missing',
      mutate: (root) => { fs.rmSync(path.join(root, ...AUTHORITY_RECORD.split('/'))); },
    },
    {
      name: 'P0 completion without a P1 ready boundary is rejected', code: 'authority_root_phase_mismatch',
      mutate: (root) => {
        fs.writeFileSync(
          path.join(root, 'plan.md'),
          '# plan v1.0\nowner 已于 2026-07-20 批准\n| P0 基线/试验裁决 | **已完成（reviewed/accepted）** |\n| P1 冻结事实源 | **进行中** |\n',
          'utf8',
        );
      },
    },
    {
      name: 'authority record cannot announce completion before the root plan', code: 'authority_record_phase_mismatch',
      mutate: (root) => {
        fs.writeFileSync(
          path.join(root, ...AUTHORITY_RECORD.split('/')),
          '[plan](../../../plan.md)\n> 当前阶段：P0 已完成并 reviewed；P1 可进入但尚未启动\nTS-MIGRATION-ROADMAP.md\nTS-DEV-PLAN.md\n',
          'utf8',
        );
      },
    },
    {
      name: 'P0 completion cannot coexist with a duplicate P1 in-progress row', code: 'authority_root_phase_mismatch',
      mutate: (root) => {
        fs.writeFileSync(
          path.join(root, 'plan.md'),
          '# plan v1.0\n> 当前状态：P0「基线、范围和当前试验裁决」已完成并 reviewed；P1 可进入但尚未启动\nowner 已于 2026-07-20 批准\n| P0 基线/试验裁决 | **已完成（reviewed/accepted）** | owner approved | evidence |\n| P1 冻结事实源 | **未开始（可进入）** | **P0 reviewed 已满足** | evidence |\n| P1 冻结事实源 | **进行中** | P0 reviewed | evidence |\n',
          'utf8',
        );
      },
    },
    {
      name: 'authority record cannot contain both phase declarations', code: 'authority_record_phase_mismatch',
      mutate: (root) => {
        fs.appendFileSync(
          path.join(root, ...AUTHORITY_RECORD.split('/')),
          '> 当前阶段：P0 已完成并 reviewed；P1 可进入但尚未启动\n',
          'utf8',
        );
      },
    },
    {
      name: 'second active plan', code: 'authority_second_active_plan',
      mutate: (root) => {
        fs.writeFileSync(
          path.join(root, 'ALTERNATE-TS-PLAN.md'),
          '# Alternate TypeScript plan\n> Status: ACTIVE\nThis document is a migration plan.\n',
          'utf8',
        );
      },
    },
    {
      name: 'authority claim deprecates root plan', code: 'authority_second_active_plan',
      mutate: (root) => {
        const absolute = path.join(root, 'docs', 'MIGRATION.md');
        fs.mkdirSync(path.dirname(absolute), { recursive: true });
        fs.writeFileSync(
          absolute,
          'Status: ACTIVE\nThis is the only active authority; plan.md is deprecated.\n',
          'utf8',
        );
      },
    },
    {
      name: 'plan label points to second authority', code: 'authority_entrypoint_root_link_missing',
      mutate: (root) => {
        fs.writeFileSync(
          path.join(root, 'README.md'),
          '[Migration plan.md](docs/MIGRATION.md) is the only active authority; root plan.md is background.\n',
          'utf8',
        );
      },
    },
    {
      name: 'equivalent root rejection plus current migration authority', code: 'authority_root_plan_rejected',
      mutate: (root) => {
        fs.writeFileSync(
          path.join(root, 'README.md'),
          'plan.md is deprecated; follow [Migration](docs/MIGRATION.md).\n',
          'utf8',
        );
        const absolute = path.join(root, 'docs', 'MIGRATION.md');
        fs.mkdirSync(path.dirname(absolute), { recursive: true });
        fs.writeFileSync(absolute, 'Status: ACTIVE\nThis document is the current migration authority.\n', 'utf8');
      },
    },
    {
      name: 'nearest second-plan link owns the authority claim', code: 'authority_second_active_claim',
      mutate: (root) => {
        fs.writeFileSync(
          path.join(root, 'README.md'),
          'Root context: [plan](plan.md). [Migration](docs/MIGRATION.md) is the only active authority.\n',
          'utf8',
        );
      },
    },
    {
      name: 'HTML-comment root link cannot mask a second authority', code: 'authority_entrypoint_root_link_missing',
      mutate: (root) => {
        fs.writeFileSync(
          path.join(root, 'README.md'),
          '<!-- [root](plan.md) --> [Migration](docs/MIGRATION.md) is the only active authority.\n',
          'utf8',
        );
      },
    },
    {
      name: 'root plan cannot declare itself non-authoritative', code: 'authority_root_plan_rejected',
      mutate: (root) => {
        fs.appendFileSync(path.join(root, 'plan.md'), '\nplan.md is not authoritative.\n', 'utf8');
      },
    },
    {
      name: 'authority record cannot claim authority for itself', code: 'authority_record_conflict',
      mutate: (root) => {
        fs.appendFileSync(
          path.join(root, ...AUTHORITY_RECORD.split('/')),
          '\nThis record is the current migration authority.\n',
          'utf8',
        );
      },
    },
    {
      name: 'historical plan cannot reactivate itself', code: 'authority_historical_reactivated',
      mutate: (root) => {
        fs.appendFileSync(
          path.join(root, 'docs/project-handoffs/TS-DEV-PLAN.md'),
          '\nStatus: ACTIVE; this document is the current migration authority.\n',
          'utf8',
        );
      },
    },
    {
      name: 'workflow comment cannot mask wrong migration plan variable', code: 'authority_github_plan_variable_mismatch',
      mutate: (root) => {
        fs.writeFileSync(
          path.join(root, '.github/workflows/p0.yml'),
          '# Active authority: plan.md\nname: TS migration\nenv:\n  MIGRATION_PLAN: docs/MIGRATION.md\n',
          'utf8',
        );
      },
    },
    {
      name: 'required active entrypoint must be tracked', code: 'authority_required_file_untracked',
      mutate: (root) => {
        const result = spawnSync(
          'git',
          ['rm', '--cached', '--', 'docs/project-handoffs/README.md'],
          { cwd: root, encoding: 'utf8', windowsHide: true },
        );
        if (result.error !== undefined) throw result.error;
        if (result.status !== 0) throw new Error(result.stderr);
      },
    },
    {
      name: 'nested root context cannot bless a second-plan claim', code: 'authority_second_active_claim',
      mutate: (root) => {
        fs.writeFileSync(
          path.join(root, 'README.md'),
          '[Migration](docs/MIGRATION.md) (see [plan](plan.md)) is the only active authority.\n',
          'utf8',
        );
      },
    },
    {
      name: 'linked root plan cannot be declared non-authoritative', code: 'authority_root_plan_rejected',
      mutate: (root) => { fs.writeFileSync(path.join(root, 'README.md'), '[plan](plan.md) is not authoritative.\n', 'utf8'); },
    },
    {
      name: 'canonical alternate plan wording is an authority claim', code: 'authority_second_active_claim',
      mutate: (root) => {
        fs.writeFileSync(
          path.join(root, 'README.md'),
          '[Migration](docs/MIGRATION.md) is the canonical migration plan to follow.\nRoot context: [plan](plan.md).\n',
          'utf8',
        );
      },
    },
    {
      name: 'root plan cannot delegate authority to an alternate link', code: 'authority_root_plan_rejected',
      mutate: (root) => {
        fs.appendFileSync(path.join(root, 'plan.md'), '\n[Migration](docs/MIGRATION.md) is the only active authority.\n', 'utf8');
      },
    },
    {
      name: 'inline quoted workflow plan assignment is validated', code: 'authority_github_plan_variable_mismatch',
      mutate: (root) => {
        fs.writeFileSync(
          path.join(root, '.github/workflows/p0.yml'),
          'name: TS migration\nenv: { "MIGRATION_PLAN": "docs/MIGRATION.md" }\nx-authority: "[plan](../../plan.md)"\n',
          'utf8',
        );
      },
    },
    {
      name: 'generic historical document cannot reactivate later', code: 'authority_second_active_plan',
      mutate: (root) => {
        const absolute = path.join(root, 'docs', 'ALTERNATE-TS-PLAN.md');
        fs.writeFileSync(
          absolute,
          'Status: HISTORICAL\n[root](../plan.md)\n\nStatus: ACTIVE\nThis document is the current migration authority.\n',
          'utf8',
        );
      },
    },
    {
      name: 'root link with a Markdown title cannot be deprecated', code: 'authority_root_plan_rejected',
      mutate: (root) => {
        fs.writeFileSync(path.join(root, 'README.md'), "[plan](plan.md 'root authority') is deprecated.\n", 'utf8');
      },
    },
    {
      name: 'angle-bracket root link cannot be deprecated', code: 'authority_root_plan_rejected',
      mutate: (root) => { fs.writeFileSync(path.join(root, 'README.md'), '[plan](<plan.md>) is deprecated.\n', 'utf8'); },
    },
    {
      name: 'fragment root link cannot be deprecated', code: 'authority_root_plan_rejected',
      mutate: (root) => { fs.writeFileSync(path.join(root, 'README.md'), '[plan](plan.md#phase) is deprecated.\n', 'utf8'); },
    },
    {
      name: 'workflow plan fragment is not the exact root plan', code: 'authority_github_plan_variable_mismatch',
      mutate: (root) => {
        fs.writeFileSync(
          path.join(root, '.github/workflows/p0.yml'),
          'name: TS migration\nenv:\n  MIGRATION_PLAN: plan.md#other\nx-authority: "[plan](../../plan.md)"\n',
          'utf8',
        );
      },
    },
    {
      name: 'root context cannot bless a plain-path official plan', code: 'authority_second_active_claim',
      mutate: (root) => {
        fs.appendFileSync(
          path.join(root, 'README.md'),
          '\n[root](plan.md) is context, docs/MIGRATION.md is the official migration plan.\n',
          'utf8',
        );
      },
    },
    {
      name: 'plain-path current migration authority is rejected', code: 'authority_second_active_claim',
      mutate: (root) => {
        fs.appendFileSync(path.join(root, 'README.md'), '\nMIGRATION.md is the current migration authority.\n', 'utf8');
      },
    },
    {
      name: 'root plan cannot delegate to a plain-path official plan', code: 'authority_root_plan_rejected',
      mutate: (root) => {
        fs.appendFileSync(path.join(root, 'plan.md'), '\ndocs/MIGRATION.md is the official migration plan.\n', 'utf8');
      },
    },
    {
      name: 'generic named plan cannot borrow root context', code: 'authority_second_active_plan',
      mutate: (root) => {
        fs.writeFileSync(
          path.join(root, 'PHOENIX.md'),
          '# Project Phoenix\n[root](plan.md) provides context and Project Phoenix is the official migration plan.\n',
          'utf8',
        );
      },
    },
    {
      name: 'root plan cannot delegate to a named official plan', code: 'authority_root_plan_rejected',
      mutate: (root) => {
        fs.appendFileSync(path.join(root, 'plan.md'), '\nProject Phoenix is the official migration plan.\n', 'utf8');
      },
    },
    {
      name: 'root self phrase cannot camouflage a named official plan', code: 'authority_root_plan_rejected',
      mutate: (root) => {
        fs.appendFileSync(
          path.join(root, 'plan.md'),
          '\nThis plan provides context and Project Phoenix is the official migration plan.\n',
          'utf8',
        );
      },
    },
    {
      name: 'raw root token cannot camouflage a named official plan', code: 'authority_root_plan_rejected',
      mutate: (root) => {
        fs.appendFileSync(
          path.join(root, 'plan.md'),
          '\nplan.md provides context and Project Phoenix is the official migration plan.\n',
          'utf8',
        );
      },
    },
    {
      name: 'generic Chinese-named plan cannot borrow root context', code: 'authority_second_active_plan',
      mutate: (root) => {
        fs.writeFileSync(
          path.join(root, 'PHOENIX.md'),
          '# 凤凰计划\n[root](plan.md) 提供背景且凤凰计划是唯一执行权威。\n',
          'utf8',
        );
      },
    },
    {
      name: 'root Chinese self phrase cannot camouflage another plan', code: 'authority_root_plan_rejected',
      mutate: (root) => {
        fs.appendFileSync(path.join(root, 'plan.md'), '\n本计划提供背景且凤凰计划是唯一执行权威。\n', 'utf8');
      },
    },
  ];
  let passed = 0;
  const failures: string[] = [];
  for (const mutant of mutants) {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'coagentia-authority-'));
    try {
      writeFixture(root);
      const baseline = verifyPlanAuthority(root);
      if (!baseline.ok) {
        failures.push(`${mutant.name}: fixture baseline failed (${baseline.issues.map((item) => item.code).join(', ')})`);
        continue;
      }
      mutant.mutate(root);
      const result = verifyPlanAuthority(root);
      if (result.issues.some((issue) => issue.code === mutant.code)) passed += 1;
      else failures.push(`${mutant.name}: expected ${mutant.code}, got ${result.issues.map((item) => item.code).join(', ') || 'PASS'}`);
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  }
  return { ok: failures.length === 0, passed, total: mutants.length, failures };
}
