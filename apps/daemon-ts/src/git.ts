/**
 * M6 交付链的 Git 执行底座（契约 D §5.3、B §12.6/§12.8；对等基准 = apps/daemon git.py）。
 *
 * 所有命令都用 argv 直启，不经 shell；stdout/stderr 统一显式 UTF-8 解码（校准条款 1：
 * 整段 Buffer.concat 后一次解码，严禁逐 chunk toString）。worktree 生命周期只以 Git 登记
 * 与固定数据根为事实，不新增本地持久 registry。进程内缓存仅用于重复 cleanup 时重报已知
 * branch/path，daemon 重启后的恢复仍从真实 worktree 反查。
 *
 * py→TS 翻译差异登记（逐条，非行为改进）：
 * - **取消恢复语义**：py `_await_uninterruptibly` 吃取消保 Git 恢复临界区完成（CancelledError
 *   注入 await 点）；TS 无任务取消注入，中断向量仅剩超时（TimeoutError）——merge 的
 *   「中断必回滚主干 HEAD」义务由 TimeoutError catch 内显式恢复链（restore → 校验 → 重抛）
 *   保住；runProcess 的取消清理路径在 TS 不可达，未翻译。
 * - py `exc.add_note(...)` → 追加进 Error.message（TS 无 note 机制）。
 * - py `_kill_process_tree` 内联 taskkill → 复用 checks.ts 的 killProcessTree 单点
 *   （校准条款 3：code 0/128=成功；失败回落 proc.kill）。
 * - numstat 键 py tuple → JSON.stringify([old,new]) 字符串键（JS Map 无元组键，纯内部）。
 * - py `shutil.rmtree(onexc=chmod S_IWRITE 重试)` → 手写递归删除 + chmod(0o200) 重试单点
 *   （win32 .git 只读位 EPERM 对等处理）。
 * - py `Path.resolve()` → resolvePath（realpathSync.native + 不存在后缀逐级回退），导出供
 *   测试对齐；py 私有 `_split_diff_sections` 被测 → TS 导出 splitDiffSections。
 */

import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import type { DiffFile, DiffPayload, GitDiffQuery, WorktreeCleanupData, WorktreeEnsureData } from '@coagentia/contracts-ts';
import type { WorktreeMergeData, WorktreeScanEntry, WorktreeScanQuery, WorktreeScanReply, WorktreeStatusData } from '@coagentia/contracts-ts';

import { TimeoutError, withTimeout } from './aio.ts';
import { killProcessTree } from './checks.ts';
import type { DataPaths } from './paths.ts';

export const GIT_TIMEOUT_SEC = 60.0;
export const DIFF_MAX_FILES = 200;
export const DIFF_MAX_PATCH_BYTES = 64 * 1024;
// PS-WT worktree.scan：只纳管 ULID 命名的两级目录（26 位 Crockford base32，同契约 A ids.Ulid）。
const ULID_RE = /^[0-9A-HJKMNP-TV-Z]{26}$/;
// PS-WT 孤儿清理护栏：删分支硬限定 coagentia/ 命名空间（绝不误删主干/他人分支）。
const COAGENTIA_BRANCH_PREFIX = 'coagentia/';

export interface GitResult {
  readonly argv: readonly string[];
  readonly returncode: number;
  readonly stdout: string;
  readonly stderr: string;
}

/** Git 非预期失败；原始 stdout/stderr 保留给 daemon 诊断。 */
export class GitCommandError extends Error {
  readonly result: GitResult;

  constructor(result: GitResult, message?: string) {
    const detail = [result.stdout.trim(), result.stderr.trim()].filter((part) => part !== '').join('\n');
    const prefix = message ?? `git 退出 ${result.returncode}`;
    super(detail !== '' ? `${prefix}: ${detail}` : prefix);
    this.name = 'GitCommandError';
    this.result = result;
  }
}

/** 拒绝越过固定 worktree 根或覆盖未知内容。 */
export class WorktreeSafetyError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'WorktreeSafetyError';
  }
}

export type ProcessRunner = (argv: readonly string[], timeoutSec: number) => Promise<GitResult>;

/** 直启短命令；超时只终止本次启动的进程树（py run_process；取消清理路径 TS 不可达，见头注）。 */
export async function runProcess(
  argv: readonly string[],
  timeoutSec: number = GIT_TIMEOUT_SEC,
): Promise<GitResult> {
  const args = argv.map((arg) => String(arg));
  const env = { ...process.env, GIT_TERMINAL_PROMPT: '0', LC_ALL: 'C.UTF-8', LANG: 'C.UTF-8' };
  const proc = spawn(args[0]!, args.slice(1), { stdio: ['ignore', 'pipe', 'pipe'], env });
  // cal6：spawn 当拍同步挂消费者；整段 Buffer 收集，close 后一次 UTF-8 解码（cal1）。
  const outChunks: Buffer[] = [];
  const errChunks: Buffer[] = [];
  proc.stdout!.on('data', (chunk: Buffer) => outChunks.push(chunk));
  proc.stderr!.on('data', (chunk: Buffer) => errChunks.push(chunk));
  let spawnError: Error | null = null;
  const exited = new Promise<void>((resolve) => {
    proc.on('exit', () => resolve());
    proc.on('error', (err) => {
      spawnError = err;
      resolve();
    });
  });
  // close = 进程退出且管道排空定稿（对等 py communicate() 完成点；cal6：定稿只挂 'close'）。
  const closed = new Promise<void>((resolve) => {
    proc.on('close', () => resolve());
    proc.on('error', () => resolve());
  });
  try {
    await withTimeout(closed, timeoutSec * 1000);
  } catch (err) {
    if (!(err instanceof TimeoutError)) throw err;
    const timeoutError = new TimeoutError(`子进程超时（${timeoutSec}s）：${args[0]}`);
    try {
      await terminateAndDrain(proc, exited, closed);
    } catch (cleanupError) {
      // py add_note 对应位：清理失败追加进 message，原错误照抛。
      timeoutError.message += `（子进程超时清理失败：${String(cleanupError)}）`;
    }
    throw timeoutError;
  }
  if (spawnError !== null) throw spawnError;
  return {
    argv: args,
    returncode: proc.exitCode ?? 0,
    stdout: Buffer.concat(outChunks).toString('utf-8'),
    stderr: Buffer.concat(errChunks).toString('utf-8'),
  };
}

async function terminateAndDrain(
  proc: ChildProcess,
  exited: Promise<void>,
  closed: Promise<void>,
): Promise<void> {
  await killTreeOf(proc, exited);
  await closed; // 排空管道防僵尸（对等 py await communicate()，无界等——杀树后必定稿）。
}

/** 对等 py _kill_process_tree(proc)：win32 走 checks.ts killProcessTree，失败回落 proc.kill。 */
async function killTreeOf(proc: ChildProcess, exited: Promise<void>): Promise<void> {
  if (proc.exitCode !== null || proc.signalCode !== null) return;
  let killedTree = false;
  if (process.platform === 'win32' && proc.pid !== undefined && proc.pid !== 0) {
    try {
      await killProcessTree(proc.pid);
      killedTree = true;
    } catch {
      killedTree = false;
    }
  }
  if (!killedTree) {
    try {
      proc.kill('SIGKILL');
    } catch {
      // 已不存在（py suppress ProcessLookupError 对应位）。
    }
  }
  try {
    await withTimeout(exited, 3000);
  } catch {
    // py suppress(wait_for(proc.wait(), 3.0))：等收敛超时不外抛。
  }
}

export interface RunGitOptions {
  timeoutSec?: number;
  runner?: ProcessRunner;
  gitBin?: string;
}

/** J3/J4/J5 共用 Git 入口；NUL 输出保留在解码后的 str 中供机器解析。 */
export async function runGit(
  repoPath: string,
  args: readonly string[],
  opts: RunGitOptions = {},
): Promise<GitResult> {
  const timeoutSec = opts.timeoutSec ?? GIT_TIMEOUT_SEC;
  const runner = opts.runner ?? runProcess;
  const gitBin = opts.gitBin ?? 'git';
  const argv = [gitBin, '-c', 'core.quotepath=false', '-c', 'color.ui=false', '-C', repoPath, ...args];
  return runner(argv, timeoutSec);
}

export interface WorktreeOperation {
  readonly changed: boolean;
  readonly status: WorktreeStatusData | null;
}

interface WorktreeEntry {
  readonly path: string;
  readonly branch: string | null;
  readonly locked: boolean;
}

interface DiffMeta {
  readonly path: string;
  readonly status: 'added' | 'modified' | 'deleted' | 'renamed';
  readonly old_path: string | null;
}

interface DiffCount {
  readonly additions: number;
  readonly deletions: number;
  readonly binary: boolean;
}

/** worktree.ensure/cleanup/merge 的自然键幂等执行器。 */
export class GitWorktreeManager {
  readonly paths: DataPaths;
  private readonly runner: ProcessRunner;
  private readonly gitBin: string;
  private readonly timeoutSec: number;
  // 仅优化同进程重复帧；不落盘、不作为恢复事实源。
  private readonly known = new Map<string, WorktreeStatusData>();
  private readonly knownRepos = new Map<string, string>();

  constructor(
    paths: DataPaths,
    opts: { runner?: ProcessRunner; gitBin?: string; timeoutSec?: number } = {},
  ) {
    this.paths = paths;
    this.runner = opts.runner ?? runProcess;
    this.gitBin = opts.gitBin ?? 'git';
    this.timeoutSec = opts.timeoutSec ?? GIT_TIMEOUT_SEC;
  }

  async ensure(data: WorktreeEnsureData): Promise<WorktreeOperation> {
    const repo = await this.validateRepo(data.repo_path);
    await this.validateBranch(repo, data.branch);
    const target = resolvePath(this.paths.worktreePath(data.project_id, data.task_id));
    this.assertManagedTarget(target, data.task_id);
    fs.mkdirSync(path.dirname(target), { recursive: true });

    let entries = await this.worktreeEntries(repo);
    let registered = entryAt(entries, target);
    if (registered !== null && !lexists(target)) {
      await this.git(repo, ['worktree', 'prune']);
      entries = await this.worktreeEntries(repo);
      registered = entryAt(entries, target);
    }
    if (registered !== null) {
      const actualBranch = shortBranch(registered.branch);
      if (actualBranch !== data.branch) {
        throw new WorktreeSafetyError(
          `目标路径已登记为分支 ${JSON.stringify(actualBranch)}，不是 ${JSON.stringify(data.branch)}`,
        );
      }
      const known = this.known.get(data.task_id);
      const status =
        known !== undefined &&
        known.status !== 'cleaned' &&
        known.path === target &&
        known.branch === data.branch
          ? known
          : makeStatus(data.task_id, 'active', data.branch, target);
      this.remember(data.task_id, repo, status);
      return { changed: false, status };
    }

    const expectedRef = `refs/heads/${data.branch}`;
    const other = entries.find((entry) => entry.branch === expectedRef) ?? null;
    if (other !== null) {
      throw new WorktreeSafetyError(`分支已在另一 worktree 使用：${other.path}`);
    }
    if (lexists(target)) {
      if (!isDir(target) || fs.readdirSync(target).length > 0) {
        throw new WorktreeSafetyError(`未登记的目标路径非空，拒绝覆盖：${target}`);
      }
    }

    const branchExists = await this.branchExists(repo, data.branch);
    const result = branchExists
      ? await this.git(repo, ['worktree', 'add', target, data.branch], false)
      : await this.git(repo, ['worktree', 'add', '-b', data.branch, target], false);
    if (result.returncode !== 0) {
      throw new GitCommandError(result, '创建 worktree 失败');
    }

    const confirmed = entryAt(await this.worktreeEntries(repo), target);
    if (confirmed === null || shortBranch(confirmed.branch) !== data.branch) {
      throw new WorktreeSafetyError('git worktree add 成功后未找到预期登记');
    }
    const status = makeStatus(data.task_id, 'active', data.branch, target);
    this.remember(data.task_id, repo, status);
    return { changed: true, status };
  }

  async cleanup(data: WorktreeCleanupData): Promise<WorktreeOperation> {
    const known = this.known.get(data.task_id) ?? null;
    let repo: string | null = this.knownRepos.get(data.task_id) ?? null;
    const projectId = data.project_id ?? null;
    let target: string;
    let branch: string;
    if (projectId !== null) {
      // PS-WT 孤儿清理：DB 无 task 行、无法从缓存/登记反查，按 (project_id, task_id) 自拼
      // 固定两级路径（下方 assertManagedTarget 双保险仍强制其落在 worktreesDir 内）。
      target = this.paths.worktreePath(projectId, data.task_id);
      branch = known !== null ? known.branch : `coagentia/task-${data.task_id}`;
      if (repo === null && fs.existsSync(path.join(target, '.git'))) {
        try {
          [repo, branch] = await this.recoverFromWorktree(target);
        } catch (err) {
          if (err instanceof GitCommandError || err instanceof WorktreeSafetyError) repo = null;
          else throw err;
        }
      }
    } else if (known !== null) {
      target = known.path;
      branch = known.branch;
    } else {
      const candidates = this.taskCandidates(data.task_id);
      if (candidates.length === 0) {
        // 帧只有 task_id；物理树和登记都已消失时没有可恢复的 project/path，noop 即目标态。
        return { changed: false, status: null };
      }
      if (candidates.length !== 1) {
        throw new WorktreeSafetyError(`同 task_id 出现多个 worktree 路径：${candidates.join(', ')}`);
      }
      target = candidates[0]!;
      branch = `coagentia/task-${data.task_id}`;
      if (fs.existsSync(path.join(target, '.git'))) {
        try {
          const [recoveredRepo, recoveredBranch] = await this.recoverFromWorktree(target);
          repo = recoveredRepo;
          branch = recoveredBranch;
        } catch (err) {
          // remove 可能已摘掉主仓登记但留下失效 .git 指针。固定两级受管路径足以
          // 允许物理清理；此时不碰未知主仓、不尝试删除任何锁。
          if (err instanceof GitCommandError || err instanceof WorktreeSafetyError) repo = null;
          else throw err;
        }
      }
    }

    target = resolvePath(target);
    this.assertManagedTarget(target, data.task_id);
    const existed = lexists(target);
    let registered = false;
    let removeResult: GitResult | null = null;
    if (repo !== null && isDir(repo)) {
      const entries = await this.worktreeEntries(repo);
      registered = entryAt(entries, target) !== null;
      if (registered) {
        removeResult = await this.git(repo, ['worktree', 'remove', '--force', target], false);
        const stillRegistered = entryAt(await this.worktreeEntries(repo), target) !== null;
        if (stillRegistered) {
          throw new GitCommandError(removeResult, '清理 worktree 失败（登记仍存在）');
        }
      }
    }

    if (lexists(target)) {
      removeManagedTree(target);
    }
    if (repo !== null && isDir(repo)) {
      await this.git(repo, ['worktree', 'prune']);
      if (entryAt(await this.worktreeEntries(repo), target) !== null) {
        if (removeResult !== null) {
          throw new GitCommandError(removeResult, 'prune 后 worktree 登记仍存在');
        }
        throw new WorktreeSafetyError('prune 后 worktree 登记仍存在');
      }
    }
    if (lexists(target)) {
      throw new WorktreeSafetyError(`worktree 物理目录仍存在：${target}`);
    }

    if (projectId !== null) {
      // PS-WT 孤儿清理收尾：删除无 task 引用的死分支（M6 常规清理不删分支、行为不变）。
      await this.cleanupOrphanBranch(repo, branch);
    }

    const status = makeStatus(data.task_id, 'cleaned', branch, target, {
      mergeCommit: known !== null ? (known.merge_commit ?? null) : null,
    });
    const changed = registered || existed || known === null || known.status !== 'cleaned';
    this.remember(data.task_id, repo, status);
    return { changed, status };
  }

  /**
   * PS-WT 孤儿清理收尾：删无 task 引用的死分支。**硬护栏：仅删 coagentia/ 命名空间**，
   * 其余分支一律不碰（绝不误删主干/他人分支）；主仓不可用则跳过（尽力，不炸清理）。
   */
  private async cleanupOrphanBranch(repo: string | null, branch: string | null): Promise<void> {
    if (repo === null || !isDir(repo)) return;
    if (branch === null || !branch.startsWith(COAGENTIA_BRANCH_PREFIX)) return;
    await this.git(repo, ['branch', '-D', '--', branch], false);
  }

  /**
   * PS-WT worktree.scan：扫 worktreesDir 两级 {project_id}/{task_id}，**只报双级 ULID
   * 命名的目录**（非 ULID 一律跳过，别的目录不纳管）。逐树尽力采集 branch/head/dirty/
   * ahead/behind；单树 git 失败逐条降级填 error，绝不炸整扫（契约 D §6）。
   */
  async scan(_data: WorktreeScanQuery): Promise<WorktreeScanReply> {
    const root = this.paths.worktreesDir;
    const entries: WorktreeScanEntry[] = [];
    if (!isDir(root)) return { entries };
    for (const projectDir of sortedByName(safeListDir(root))) {
      if (!isUlidDir(projectDir)) continue;
      for (const taskDir of sortedByName(safeListDir(projectDir))) {
        if (!isUlidDir(taskDir)) continue;
        entries.push(await this.scanTree(path.basename(projectDir), path.basename(taskDir), taskDir));
      }
    }
    return { entries };
  }

  private async scanTree(projectId: string, taskId: string, treePath: string): Promise<WorktreeScanEntry> {
    let branch: string | null = null;
    let headCommit: string | null = null;
    let dirty = false;
    const errors: string[] = [];

    let res = await this.scanGit(treePath, ['rev-parse', '--abbrev-ref', 'HEAD']);
    if (res !== null && res.returncode === 0) {
      const name = res.stdout.trim();
      branch = name === '' || name === 'HEAD' ? null : name; // detached → "HEAD" → null
    } else {
      errors.push(scanErr('rev-parse --abbrev-ref HEAD', res));
    }

    res = await this.scanGit(treePath, ['rev-parse', 'HEAD']);
    if (res !== null && res.returncode === 0) {
      headCommit = res.stdout.trim() !== '' ? res.stdout.trim() : null;
    } else {
      errors.push(scanErr('rev-parse HEAD', res));
    }

    res = await this.scanGit(treePath, ['status', '--porcelain']);
    if (res !== null && res.returncode === 0) {
      dirty = res.stdout.trim() !== '';
    } else {
      errors.push(scanErr('status --porcelain', res));
    }

    const [ahead, behind] = await this.scanAheadBehind(treePath);

    return {
      project_id: projectId,
      task_id: taskId,
      path: treePath,
      branch,
      head_commit: headCommit,
      dirty,
      ahead,
      behind,
      error: errors.length > 0 ? errors.join('; ') : null,
    };
  }

  /** 相对主仓库当前 HEAD 的 ahead/behind（尽力）；基线无法干净解析即 null，不报 error。 */
  private async scanAheadBehind(treePath: string): Promise<[number | null, number | null]> {
    const listing = await this.scanGit(treePath, ['worktree', 'list', '--porcelain']);
    if (listing === null || listing.returncode !== 0) return [null, null];
    const mainHead = mainWorktreeHead(listing.stdout);
    if (mainHead === null) return [null, null];
    const counts = await this.scanGit(treePath, [
      'rev-list',
      '--left-right',
      '--count',
      `${mainHead}...HEAD`,
    ]);
    if (counts === null || counts.returncode !== 0) return [null, null];
    const parts = splitTokens(counts.stdout);
    if (parts.length !== 2) return [null, null];
    if (!/^[+-]?\d+$/.test(parts[0]!) || !/^[+-]?\d+$/.test(parts[1]!)) return [null, null];
    const behind = Number(parts[0]); // 左=主仓独有(落后) 右=本树独有(领先)
    const ahead = Number(parts[1]);
    return [ahead, behind];
  }

  /**
   * scan 专用只读执行：check=false（返回码留给调用方判读）+ 吞子进程级异常返回 null，
   * 使单树 git 失败逐条降级而非炸整扫（py 捕 OSError/TimeoutError/GitCommandError）。
   */
  private async scanGit(treePath: string, args: readonly string[]): Promise<GitResult | null> {
    try {
      return await this.git(treePath, args, false);
    } catch (err) {
      if (err instanceof TimeoutError || err instanceof GitCommandError || isOsError(err)) return null;
      throw err;
    }
  }

  async merge(data: WorktreeMergeData): Promise<WorktreeOperation> {
    const repo = await this.validateRepo(data.repo_path);
    await this.validateBranch(repo, data.branch);
    const target = resolvePath(this.paths.worktreePath(data.project_id, data.task_id));
    this.assertManagedTarget(target, data.task_id);
    if (!(await this.branchExists(repo, data.branch))) {
      throw new WorktreeSafetyError(`待合并分支不存在：${data.branch}`);
    }

    const branchHead = (await this.git(repo, ['rev-parse', data.branch])).stdout.trim();
    const mainHead = (await this.git(repo, ['rev-parse', 'HEAD'])).stdout.trim();
    const unfinished = await this.git(repo, ['rev-parse', '-q', '--verify', 'MERGE_HEAD'], false);
    if (unfinished.returncode === 0) {
      if (!splitTokens(unfinished.stdout).includes(branchHead)) {
        throw new WorktreeSafetyError('主工作区存在不属于本任务的未完成 merge，拒绝 abort');
      }
      const conflictsResult = await this.git(repo, ['diff', '--name-only', '--diff-filter=U', '-z']);
      const conflictFiles = conflictsResult.stdout.split('\0').filter((name) => name !== '');
      const abort = await this.git(repo, ['merge', '--abort'], false);
      if (abort.returncode !== 0) {
        throw new GitCommandError(abort, '恢复上次未完成 merge 时 abort 失败');
      }
      if ((await this.git(repo, ['rev-parse', 'HEAD'])).stdout.trim() !== mainHead) {
        throw new WorktreeSafetyError('恢复上次未完成 merge 后主干 HEAD 改变');
      }
      if (conflictFiles.length > 0) {
        const status = makeStatus(data.task_id, 'conflicted', data.branch, target, { conflictFiles });
        this.remember(data.task_id, repo, status);
        return { changed: true, status };
      }
      // clean merge 在 commit 前崩溃：abort 回到前态后，同一次重放继续执行。
    }

    const ancestor = await this.git(repo, ['merge-base', '--is-ancestor', data.branch, 'HEAD'], false);
    if (ancestor.returncode === 0) {
      const mergeCommit = await this.findMergeCommit(repo, branchHead);
      if (mergeCommit === null) {
        throw new WorktreeSafetyError('分支已在主干中，但找不到对应的 --no-ff merge commit');
      }
      const status = makeStatus(data.task_id, 'merged', data.branch, target, { mergeCommit });
      this.remember(data.task_id, repo, status);
      return { changed: false, status };
    }
    if (ancestor.returncode !== 1) {
      throw new GitCommandError(ancestor, '判断分支合并状态失败');
    }

    const beforeStatus = (
      await this.git(repo, ['status', '--porcelain=v1', '-z', '--untracked-files=all'])
    ).stdout;
    if (beforeStatus !== '') {
      throw new WorktreeSafetyError('主工作区存在未提交更改，拒绝 merge');
    }
    let result: GitResult;
    try {
      result = await this.git(repo, ['merge', '--no-ff', '-m', data.message, '--', data.branch], false);
    } catch (interrupted) {
      // py 有 CancelledError（_await_uninterruptibly 吃取消）与 TimeoutError（shield 恢复）两路；
      // TS 无取消注入，中断向量仅剩超时——恢复链在此保住「merge 中断必回滚主干 HEAD」义务。
      if (interrupted instanceof TimeoutError) {
        try {
          await this.restoreCancelledMerge(repo, mainHead, beforeStatus);
        } catch (recoveryError) {
          interrupted.message += `（merge 超时恢复失败：${String(recoveryError)}）`;
        }
      }
      throw interrupted;
    }
    if (result.returncode === 0) {
      const mergeCommit = (await this.git(repo, ['rev-parse', 'HEAD'])).stdout.trim();
      const parents = splitTokens(
        (await this.git(repo, ['rev-list', '--parents', '-n', '1', mergeCommit])).stdout,
      );
      if (mergeCommit === mainHead || parents.length < 3) {
        throw new WorktreeSafetyError('merge 未生成预期的 --no-ff 双亲提交');
      }
      const status = makeStatus(data.task_id, 'merged', data.branch, target, { mergeCommit });
      this.remember(data.task_id, repo, status);
      return { changed: true, status };
    }

    const conflictsResult = await this.git(repo, ['diff', '--name-only', '--diff-filter=U', '-z']);
    const conflictFiles = conflictsResult.stdout.split('\0').filter((name) => name !== '');
    const mergeHead = await this.git(repo, ['rev-parse', '-q', '--verify', 'MERGE_HEAD'], false);
    if (conflictFiles.length === 0) {
      if (mergeHead.returncode === 0) {
        const abort = await this.git(repo, ['merge', '--abort'], false);
        if (abort.returncode !== 0) {
          throw new GitCommandError(abort, '非冲突 merge 失败后的 abort 失败');
        }
      }
      throw new GitCommandError(result, 'git merge 失败（非内容冲突）');
    }

    const abort = await this.git(repo, ['merge', '--abort'], false);
    if (abort.returncode !== 0) {
      throw new GitCommandError(abort, 'git merge --abort 失败');
    }
    const restoredHead = (await this.git(repo, ['rev-parse', 'HEAD'])).stdout.trim();
    const restoredStatus = (
      await this.git(repo, ['status', '--porcelain=v1', '-z', '--untracked-files=all'])
    ).stdout;
    if (restoredHead !== mainHead || restoredStatus !== beforeStatus) {
      throw new WorktreeSafetyError('冲突 abort 后主干未恢复到合并前状态');
    }
    const status = makeStatus(data.task_id, 'conflicted', data.branch, target, { conflictFiles });
    this.remember(data.task_id, repo, status);
    return { changed: true, status };
  }

  private async restoreCancelledMerge(repo: string, mainHead: string, beforeStatus: string): Promise<void> {
    const mergeHead = await this.git(repo, ['rev-parse', '-q', '--verify', 'MERGE_HEAD'], false);
    const restored =
      mergeHead.returncode === 0
        ? await this.git(repo, ['merge', '--abort'], false)
        : await this.git(repo, ['reset', '--hard', mainHead], false);
    if (restored.returncode !== 0) {
      throw new GitCommandError(restored, '取消 merge 后恢复主工作区失败');
    }
    const restoredHead = (await this.git(repo, ['rev-parse', 'HEAD'])).stdout.trim();
    const restoredStatus = (
      await this.git(repo, ['status', '--porcelain=v1', '-z', '--untracked-files=all'])
    ).stdout;
    if (restoredHead !== mainHead || restoredStatus !== beforeStatus) {
      throw new WorktreeSafetyError('取消 merge 后主工作区未恢复到合并前状态');
    }
  }

  /** 读取任务分支相对主干的逐文件 diff（契约 D §6）。 */
  async diff(
    data: GitDiffQuery,
    opts: { maxFiles?: number; maxPatchBytes?: number } = {},
  ): Promise<DiffPayload> {
    const maxFiles = opts.maxFiles ?? DIFF_MAX_FILES;
    const maxPatchBytes = opts.maxPatchBytes ?? DIFF_MAX_PATCH_BYTES;
    if (maxFiles < 0 || maxPatchBytes < 0) {
      throw new RangeError('Diff 截断上限不得为负数');
    }
    const repo = await this.validateRepo(data.repo_path);
    const branch = `coagentia/task-${data.task_id}`;
    await this.validateBranch(repo, branch);
    const target = resolvePath(this.paths.worktreePath(data.project_id, data.task_id));
    this.assertManagedTarget(target, data.task_id);
    const registered = entryAt(await this.worktreeEntries(repo), target);
    if (registered !== null && shortBranch(registered.branch) !== branch) {
      throw new WorktreeSafetyError('任务 worktree 登记分支与约定不一致');
    }

    let baseRef: string;
    let baseCommit: string;
    const base = data.base ?? null;
    if (base === null) {
      baseCommit = (await this.git(repo, ['rev-parse', 'HEAD'])).stdout.trim();
      const branchName = (await this.git(repo, ['branch', '--show-current'])).stdout.trim();
      baseRef = branchName !== '' ? branchName : baseCommit;
    } else {
      baseRef = base;
      baseCommit = await this.resolveCommit(repo, base);
    }
    const headRef = branch;
    const headCommit = await this.resolveCommit(repo, `refs/heads/${branch}`);

    const nameStatus = await this.git(repo, [
      'diff',
      '--name-status',
      '-z',
      '--find-renames',
      '--no-ext-diff',
      baseCommit,
      headCommit,
      '--',
    ]);
    const numstat = await this.git(repo, [
      'diff',
      '--numstat',
      '-z',
      '--find-renames',
      '--no-ext-diff',
      baseCommit,
      headCommit,
      '--',
    ]);
    const metadata = parseNameStatusZ(nameStatus.stdout);
    const counts = parseNumstatZ(numstat.stdout);
    let totalAdditions = 0;
    let totalDeletions = 0;
    for (const item of counts.values()) {
      totalAdditions += item.additions;
      totalDeletions += item.deletions;
    }

    // 一次全量 unified diff → 按 `diff --git ` 头切分逐文件（#8：子进程数塌缩为常数）。
    // name-status 与本次 patch 用同一 flag 集（--find-renames 等），diffcore 顺序一致，
    // 故 metadata 与 sections 按位对齐；段数不符则 fail-closed，绝不错配 patch。
    const fullDiff = await this.git(repo, [
      'diff',
      '--no-color',
      '--no-ext-diff',
      '--find-renames',
      baseCommit,
      headCommit,
      '--',
    ]);
    const sections = splitDiffSections(fullDiff.stdout);
    if (sections.length !== metadata.length) {
      throw new WorktreeSafetyError(
        `Diff 段数与 name-status 不一致：${sections.length} != ${metadata.length}`,
      );
    }

    const files: DiffFile[] = [];
    const limit = Math.min(maxFiles, metadata.length, sections.length);
    for (let i = 0; i < limit; i += 1) {
      const item = metadata[i]!;
      const section = sections[i]!;
      const count = counts.get(countKey(item.old_path, item.path));
      if (count === undefined) {
        throw new WorktreeSafetyError(`Diff 元数据与 numstat 不一致：${item.path}`);
      }
      let patch: string;
      let patchTruncated: boolean;
      if (count.binary) {
        patch = '';
        patchTruncated = false;
      } else {
        [patch, patchTruncated] = truncateUtf8(section, maxPatchBytes);
      }
      files.push({
        path: item.path,
        status: item.status,
        old_path: item.old_path,
        additions: count.additions,
        deletions: count.deletions,
        patch,
        patch_truncated: patchTruncated,
      });
    }
    return {
      base_ref: baseRef,
      head_ref: headRef,
      files,
      total_additions: totalAdditions,
      total_deletions: totalDeletions,
      files_truncated: metadata.length > maxFiles,
    };
  }

  private async git(repo: string, args: readonly string[], check = true): Promise<GitResult> {
    const result = await runGit(repo, args, {
      timeoutSec: this.timeoutSec,
      runner: this.runner,
      gitBin: this.gitBin,
    });
    if (check && result.returncode !== 0) {
      throw new GitCommandError(result);
    }
    return result;
  }

  private async validateRepo(repoPath: string): Promise<string> {
    const repo = resolvePath(expanduser(repoPath));
    if (!isDir(repo)) {
      throw new WorktreeSafetyError(`repo_path 不存在或不是目录：${repo}`);
    }
    const inside = await this.git(repo, ['rev-parse', '--is-inside-work-tree'], false);
    if (inside.returncode !== 0 || inside.stdout.trim() !== 'true') {
      throw new GitCommandError(inside, 'repo_path 不是可用 git 工作区');
    }
    return repo;
  }

  private async validateBranch(repo: string, branch: string): Promise<void> {
    const result = await this.git(repo, ['check-ref-format', '--branch', branch], false);
    if (result.returncode !== 0) {
      throw new GitCommandError(result, `非法分支名 ${JSON.stringify(branch)}`);
    }
  }

  private async branchExists(repo: string, branch: string): Promise<boolean> {
    const result = await this.git(
      repo,
      ['show-ref', '--verify', '--quiet', `refs/heads/${branch}`],
      false,
    );
    if (result.returncode === 0) return true;
    if (result.returncode === 1) return false;
    throw new GitCommandError(result, '查询分支失败');
  }

  private async worktreeEntries(repo: string): Promise<WorktreeEntry[]> {
    const result = await this.git(repo, ['worktree', 'list', '--porcelain']);
    return parseWorktreePorcelain(result.stdout);
  }

  private async recoverFromWorktree(target: string): Promise<[string, string]> {
    const entries = await this.worktreeEntries(target);
    if (entries.length === 0) {
      throw new WorktreeSafetyError(`无法从 worktree 反查主仓库：${target}`);
    }
    const branchResult = await this.git(target, ['branch', '--show-current']);
    const branch = branchResult.stdout.trim();
    if (branch === '') {
      throw new WorktreeSafetyError(`worktree 处于 detached HEAD：${target}`);
    }
    return [resolvePath(entries[0]!.path), branch];
  }

  private async findMergeCommit(repo: string, branchHead: string): Promise<string | null> {
    // 范围限界 {branch_head}..HEAD（M6 review 效率）：要找的 --no-ff 合并提交以 branch_head
    // 为第二父，必是其后代、且在 HEAD 首父链上，故必在范围内；分叉点之前的全史被排除——
    // 无界 HEAD 在大仓库上是整史 O(history) 遍历（本函数走幂等重放/恢复路径，每次重放都付）。
    const result = await this.git(repo, [
      'rev-list',
      '--first-parent',
      '--parents',
      '--merges',
      `${branchHead}..HEAD`,
    ]);
    for (const line of splitLines(result.stdout)) {
      const parts = splitTokens(line);
      if (parts.length >= 3 && parts.slice(2).includes(branchHead)) {
        return parts[0]!;
      }
    }
    return null;
  }

  private async resolveCommit(repo: string, ref: string): Promise<string> {
    const result = await this.git(
      repo,
      ['rev-parse', '--verify', '--end-of-options', `${ref}^{commit}`],
      false,
    );
    if (result.returncode !== 0) {
      throw new GitCommandError(result, `Diff ref 不存在：${ref}`);
    }
    return result.stdout.trim();
  }

  private taskCandidates(taskId: string): string[] {
    if (!isDir(this.paths.worktreesDir)) return [];
    const candidates: string[] = [];
    for (const projectDir of safeListDir(this.paths.worktreesDir)) {
      const candidate = path.join(projectDir, taskId);
      if (lexists(candidate)) candidates.push(candidate);
    }
    return candidates;
  }

  private assertManagedTarget(target: string, taskId: string): void {
    const root = resolvePath(this.paths.worktreesDir);
    const resolved = resolvePath(target);
    const relative = path.relative(root, resolved);
    if (relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
      throw new WorktreeSafetyError(`worktree 路径越出数据根：${target}`);
    }
    const parts = relative === '' ? [] : relative.split(path.sep);
    if (parts.length !== 2 || parts[parts.length - 1] !== taskId) {
      throw new WorktreeSafetyError(`worktree 路径不符合 project/task 布局：${target}`);
    }
  }

  private remember(taskId: string, repo: string | null, status: WorktreeStatusData): void {
    this.known.set(taskId, status);
    if (repo !== null) this.knownRepos.set(taskId, repo);
  }
}

function makeStatus(
  taskId: string,
  status: 'active' | 'merged' | 'conflicted' | 'cleaned',
  branch: string,
  targetPath: string,
  opts: { mergeCommit?: string | null; conflictFiles?: string[] | null } = {},
): WorktreeStatusData {
  return {
    task_id: taskId,
    status,
    branch,
    path: targetPath,
    merge_commit: opts.mergeCommit ?? null,
    conflict_files: opts.conflictFiles ?? null,
  };
}

function parseWorktreePorcelain(raw: string): WorktreeEntry[] {
  const entries: WorktreeEntry[] = [];
  let current: Record<string, string | true> = {};
  for (const line of [...splitLines(raw), '']) {
    if (line === '') {
      const worktreePath = current['worktree'];
      if (typeof worktreePath === 'string') {
        const branch = current['branch'];
        entries.push({
          path: worktreePath,
          branch: typeof branch === 'string' ? branch : null,
          locked: Boolean(current['locked']),
        });
      }
      current = {};
      continue;
    }
    const sep = line.indexOf(' ');
    const key = sep === -1 ? line : line.slice(0, sep);
    const value = sep === -1 ? '' : line.slice(sep + 1);
    current[key] = value !== '' ? value : true;
  }
  return entries;
}

function entryAt(entries: readonly WorktreeEntry[], target: string): WorktreeEntry | null {
  const targetKey = pathKey(target);
  return entries.find((entry) => pathKey(entry.path) === targetKey) ?? null;
}

/** 对等 py os.path.normcase(normpath(resolve()))：win32 归一大小写与分隔符后作比较键。 */
function pathKey(p: string): string {
  const norm = path.normalize(resolvePath(p));
  return process.platform === 'win32' ? norm.toLowerCase() : norm;
}

/**
 * 对等 py Path.resolve(strict=False)：存在即 realpath（win32 canonical 大小写/短名/符号链接），
 * 不存在则对已存在前缀 realpath、缺失后缀原样拼回。导出供测试对齐 py 侧 pathlib 断言。
 */
export function resolvePath(p: string): string {
  const abs = path.resolve(p);
  try {
    return fs.realpathSync.native(abs);
  } catch {
    // 不存在：向上找可 realpath 的前缀。
  }
  const parent = path.dirname(abs);
  if (parent === abs) return abs;
  return path.join(resolvePath(parent), path.basename(abs));
}

function shortBranch(branchRef: string | null): string | null {
  const prefix = 'refs/heads/';
  if (branchRef === null) return null;
  return branchRef.startsWith(prefix) ? branchRef.slice(prefix.length) : branchRef;
}

function parseNameStatusZ(raw: string): DiffMeta[] {
  const tokens = raw.split('\0');
  if (tokens.length > 0 && tokens[tokens.length - 1] === '') tokens.pop();
  const result: DiffMeta[] = [];
  let index = 0;
  while (index < tokens.length) {
    const code = tokens[index]!;
    index += 1;
    if (code === '') {
      throw new WorktreeSafetyError('git diff --name-status 出现空状态');
    }
    const kind = code[0]!;
    if (kind === 'R') {
      if (index + 1 >= tokens.length) {
        throw new WorktreeSafetyError('git diff rename 记录不完整');
      }
      const oldPath = tokens[index]!;
      const newPath = tokens[index + 1]!;
      index += 2;
      result.push({ path: newPath, old_path: oldPath, status: 'renamed' });
      continue;
    }
    if (index >= tokens.length) {
      throw new WorktreeSafetyError('git diff name-status 记录不完整');
    }
    const filePath = tokens[index]!;
    index += 1;
    let status: DiffMeta['status'];
    if (kind === 'A') status = 'added';
    else if (kind === 'D') status = 'deleted';
    else if (kind === 'M' || kind === 'T') status = 'modified';
    else throw new WorktreeSafetyError(`不支持的 git diff 状态：${code}`);
    result.push({ path: filePath, old_path: null, status });
  }
  return result;
}

/** numstat 键（py tuple(old_path, path) → JSON 字符串键；纯内部表示差异）。 */
function countKey(oldPath: string | null, filePath: string): string {
  return JSON.stringify([oldPath, filePath]);
}

function parseNumstatZ(raw: string): Map<string, DiffCount> {
  const tokens = raw.split('\0');
  if (tokens.length > 0 && tokens[tokens.length - 1] === '') tokens.pop();
  const result = new Map<string, DiffCount>();
  let index = 0;
  while (index < tokens.length) {
    const fields = splitTab3(tokens[index]!);
    index += 1;
    if (fields.length !== 3) {
      throw new WorktreeSafetyError('git diff --numstat 记录不完整');
    }
    const additionsRaw = fields[0]!;
    const deletionsRaw = fields[1]!;
    let filePath = fields[2]!;
    let oldPath: string | null = null;
    if (filePath === '') {
      if (index + 1 >= tokens.length) {
        throw new WorktreeSafetyError('git diff rename numstat 记录不完整');
      }
      oldPath = tokens[index]!;
      filePath = tokens[index + 1]!;
      index += 2;
    }
    const binary = additionsRaw === '-' || deletionsRaw === '-';
    let additions = 0;
    let deletions = 0;
    if (!binary) {
      if (!/^[+-]?\d+$/.test(additionsRaw) || !/^[+-]?\d+$/.test(deletionsRaw)) {
        throw new WorktreeSafetyError('git diff numstat 计数不是整数');
      }
      additions = Number(additionsRaw);
      deletions = Number(deletionsRaw);
    }
    result.set(countKey(oldPath, filePath), { additions, deletions, binary });
  }
  return result;
}

/** 对等 py str.split("\t", 2)：最多切 3 段，路径含 \t 时余量留在末段。 */
function splitTab3(token: string): string[] {
  const first = token.indexOf('\t');
  if (first === -1) return [token];
  const second = token.indexOf('\t', first + 1);
  if (second === -1) return [token.slice(0, first), token.slice(first + 1)];
  return [token.slice(0, first), token.slice(first + 1, second), token.slice(second + 1)];
}

/** 对等 py `encoded[:max].decode(errors="ignore")` 的尾部行为：剥被切断的多字节序列。 */
function truncateUtf8(value: string, maxBytes: number): [string, boolean] {
  const encoded = Buffer.from(value, 'utf-8');
  if (encoded.length <= maxBytes) return [value, false];
  let end = maxBytes;
  let back = 0;
  while (back < 3 && end - 1 - back >= 0 && (encoded[end - 1 - back]! & 0xc0) === 0x80) back += 1;
  const leadIndex = end - 1 - back;
  if (leadIndex >= 0) {
    const lead = encoded[leadIndex]!;
    let expected = 1;
    if ((lead & 0xe0) === 0xc0) expected = 2;
    else if ((lead & 0xf0) === 0xe0) expected = 3;
    else if ((lead & 0xf8) === 0xf0) expected = 4;
    if (expected > 1 && leadIndex + expected > end) end = leadIndex;
  }
  return [encoded.subarray(0, end).toString('utf-8'), true];
}

/**
 * 把全量 unified diff 按每个 `diff --git ` 头切成逐文件段（#8）。
 *
 * 只有行首字面为 `diff --git `（无 diff 前缀字符）才开新段——真正的内容行恒以 ' '/'+'/'-' 起头，
 * 头行（index、---、+++、@@、rename*、Binary files、old mode…）均不以 `diff --git ` 起头，故对含空格/
 * 中文的路径也无歧义；keepends 保留精确字节，令下游 UTF-8 字节截断与断言与旧逐文件输出一致。
 */
export function splitDiffSections(raw: string): string[] {
  const sections: string[] = [];
  let current: string[] | null = null;
  for (const line of splitLinesKeepEnds(raw)) {
    if (line.startsWith('diff --git ')) {
      if (current !== null) sections.push(current.join(''));
      current = [line];
    } else if (current !== null) {
      current.push(line);
    }
  }
  if (current !== null) sections.push(current.join(''));
  return sections;
}

/** 对等 py str.splitlines()（\n/\r\n/\r 皆切、尾部无空条目）。 */
function splitLines(raw: string): string[] {
  if (raw === '') return [];
  const lines = raw.split(/\r\n|\n|\r/);
  if (lines.length > 0 && lines[lines.length - 1] === '') lines.pop();
  return lines;
}

/** 对等 py str.splitlines(keepends=True)：按 \n 切分且每段保留行尾（\r\n 天然随行保留）。 */
function splitLinesKeepEnds(raw: string): string[] {
  if (raw === '') return [];
  return raw.split(/(?<=\n)/);
}

/** 对等 py str.split()（无参）：按任意空白切分并丢弃空段。 */
function splitTokens(raw: string): string[] {
  return raw.split(/\s+/).filter((token) => token !== '');
}

/** 列目录（全路径），权限/IO 失败即空列表（worktree.scan 逐级降级不炸整扫）。 */
function safeListDir(dir: string): string[] {
  try {
    return fs.readdirSync(dir).map((name) => path.join(dir, name));
  } catch {
    return [];
  }
}

function sortedByName(paths: string[]): string[] {
  return [...paths].sort((a, b) => {
    const an = path.basename(a);
    const bn = path.basename(b);
    return an < bn ? -1 : an > bn ? 1 : 0;
  });
}

/** worktreesDir 两级过滤：名字是 26 位 Crockford ULID 且是真目录才纳管。 */
function isUlidDir(p: string): boolean {
  if (!ULID_RE.test(path.basename(p))) return false;
  return isDir(p);
}

/**
 * git worktree list --porcelain 首个 worktree 块 = 主工作区；取其 HEAD sha 作 ahead/behind
 * 基线（主仓库当前 HEAD）。首块无 HEAD 行（罕见）→ null，调用方置 ahead/behind null。
 */
function mainWorktreeHead(porcelain: string): string | null {
  let started = false;
  for (const line of splitLines(porcelain)) {
    if (line.startsWith('worktree ')) {
      if (started) break; // 已越过首块仍未见 HEAD
      started = true;
    } else if (started && line.startsWith('HEAD ')) {
      const value = line.slice('HEAD '.length).trim();
      return value !== '' ? value : null;
    }
  }
  return null;
}

/** 把单条 git 失败折成简短错误串（stderr 优先），供 WorktreeScanEntry.error 逐条降级。 */
function scanErr(label: string, res: GitResult | null): string {
  if (res === null) return `${label}: git 无法执行`;
  const detail = res.stderr.trim() || res.stdout.trim() || `退出码 ${res.returncode}`;
  return `${label}: ${detail}`;
}

function lexists(p: string): boolean {
  try {
    fs.lstatSync(p);
    return true;
  } catch {
    return false;
  }
}

function isDir(p: string): boolean {
  try {
    return fs.statSync(p).isDirectory();
  } catch {
    return false;
  }
}

/** node 系 OS 错误（ErrnoException）判定：scan 降级面对等 py except OSError。 */
function isOsError(err: unknown): boolean {
  return err instanceof Error && typeof (err as NodeJS.ErrnoException).code === 'string';
}

function expanduser(p: string): string {
  if (p === '~') return os.homedir();
  if (p.startsWith('~/') || p.startsWith('~\\')) return path.join(os.homedir(), p.slice(2));
  return p;
}

/**
 * 对等 py _remove_managed_tree：链接/junction 拒删（node lstat 将 junction 报为符号链接）、
 * 非目录拒删；递归删除时 EPERM（win32 .git 只读位）→ chmod(S_IWRITE) 重试（py rmtree onexc）。
 */
function removeManagedTree(target: string): void {
  const st = fs.lstatSync(target);
  if (st.isSymbolicLink()) {
    throw new WorktreeSafetyError(`拒绝递归删除链接/junction：${target}`);
  }
  if (!st.isDirectory()) {
    throw new WorktreeSafetyError(`worktree 目标不是目录：${target}`);
  }
  rmTreeClearingReadonly(target);
}

function rmTreeClearingReadonly(dir: string): void {
  for (const name of fs.readdirSync(dir)) {
    const child = path.join(dir, name);
    const st = fs.lstatSync(child);
    if (st.isDirectory() && !st.isSymbolicLink()) {
      rmTreeClearingReadonly(child);
    } else {
      withReadonlyRetry(child, (p) => fs.unlinkSync(p));
    }
  }
  withReadonlyRetry(dir, (p) => fs.rmdirSync(p));
}

function withReadonlyRetry(target: string, op: (p: string) => void): void {
  try {
    op(target);
  } catch {
    // 对等 py onexc：os.chmod(name, stat.S_IWRITE) 后重试恰一次；再失败即外抛。
    fs.chmodSync(target, 0o200);
    op(target);
  }
}
