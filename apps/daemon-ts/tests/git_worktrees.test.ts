/**
 * M6 J3 worktree 执行面：用真实 scratch git 仓库锁定生命周期语义（对等基准 = py test_git_worktrees.py）。
 *
 * py→TS 移植登记（非行为改进）：
 * - py 取消 = asyncio task.cancel()（CancelledError 注入 await 点）；TS 无任务取消注入，
 *   中断向量仅剩超时——两条 win32 取消用例（runner 杀树 / merge 恢复重放）以**短超时中断
 *   同一卡点**对等翻译，断言面（进程树死净 / 主干恢复 / 重放成功）逐条保留。
 * - py 探针子进程用 sys.executable(python)；TS 用 process.execPath(node) 等价替换
 *   （checks.test.ts 同款先例）。
 * - py powershell Get-Process 探活 → 校准条款 5 轻量档 process.kill(pid, 0)。
 * - py write_text 在 win32 会把 \n 翻成 \r\n（newline=None）；TS writeFileSync 写 LF——
 *   写读两侧自洽（core.autocrlf=false），断言面无差。
 * - afterEach 清理 tmp 时须处理 .git 对象只读位（win32 EPERM → chmod 后重试）。
 */

import { spawnSync } from 'node:child_process';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import type { WorktreeEnsureData, WorktreeMergeData } from '@coagentia/contracts-ts';

import { TimeoutError, withTimeout } from '../src/aio.ts';
import { GitCommandError, GitWorktreeManager, resolvePath, runProcess } from '../src/git.ts';
import { DataPaths } from '../src/paths.ts';
import { newUlid } from '../src/util.ts';
import { until } from './helpers.ts';

let tmp: string;

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'coagentia-git-wt-'));
});

afterEach(() => {
  rmrfClearingReadonly(tmp);
});

interface GitOutput {
  status: number;
  stdout: string;
  stderr: string;
}

/** 对等 py 测试的 _git 助手（-c core.quotepath=false -C repo；check 默认真）。 */
function git(repo: string, args: string[], check = true): GitOutput {
  const res = spawnSync('git', ['-c', 'core.quotepath=false', '-C', repo, ...args], {
    encoding: 'utf-8',
  });
  const status = res.status ?? -1;
  const out: GitOutput = { status, stdout: res.stdout ?? '', stderr: res.stderr ?? '' };
  if (check && status !== 0) {
    throw new Error(`git ${args.join(' ')} failed (${status}): ${out.stdout}\n${out.stderr}`);
  }
  return out;
}

function scratchRepo(base: string): string {
  const repo = path.join(base, '中文 项目');
  fs.mkdirSync(repo);
  const init = spawnSync('git', ['init', '-b', 'main', repo], { encoding: 'utf-8' });
  if (init.status !== 0) {
    throw new Error(`git init failed (${init.status}): ${init.stdout}\n${init.stderr}`);
  }
  git(repo, ['config', 'user.name', 'CoAgentia Test']);
  git(repo, ['config', 'user.email', 'test@coagentia.local']);
  git(repo, ['config', 'core.autocrlf', 'false']);
  fs.writeFileSync(path.join(repo, 'conflict.txt'), 'base\n', 'utf-8');
  git(repo, ['add', '--', 'conflict.txt']);
  git(repo, ['commit', '-m', '种子提交']);
  return repo;
}

function makeManager(base: string): [GitWorktreeManager, DataPaths] {
  const paths = new DataPaths(path.join(base, '数据 根'));
  paths.ensureDirs();
  return [new GitWorktreeManager(paths), paths];
}

function ensureData(repo: string, taskId: string, projectId: string): WorktreeEnsureData {
  return {
    task_id: taskId,
    project_id: projectId,
    repo_path: repo,
    branch: `coagentia/task-${taskId}`,
  };
}

function mergeData(repo: string, taskId: string, projectId: string, branch: string): WorktreeMergeData {
  return {
    task_id: taskId,
    project_id: projectId,
    repo_path: repo,
    branch,
    message: `Merge task ${taskId}`,
  };
}

function isAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

/** tmp 清理：.git 对象只读位（win32）会让 rmSync EPERM，chmod 全树后重试。 */
function rmrfClearingReadonly(dir: string): void {
  try {
    fs.rmSync(dir, { recursive: true, force: true, maxRetries: 3, retryDelay: 50 });
  } catch {
    chmodTreeWritable(dir);
    fs.rmSync(dir, { recursive: true, force: true, maxRetries: 3, retryDelay: 50 });
  }
}

function chmodTreeWritable(p: string): void {
  let st: fs.Stats;
  try {
    st = fs.lstatSync(p);
  } catch {
    return;
  }
  try {
    fs.chmodSync(p, 0o700);
  } catch {
    // 尽力
  }
  if (st.isDirectory() && !st.isSymbolicLink()) {
    let names: string[] = [];
    try {
      names = fs.readdirSync(p);
    } catch {
      names = [];
    }
    for (const name of names) chmodTreeWritable(path.join(p, name));
  }
}

describe('git worktrees（契约 D §5.3）', () => {
  it.runIf(process.platform === 'win32')(
    '运行器中断杀整棵进程树（test_git_runner_cancellation_kills_process_tree；py=任务取消，TS 无取消注入→超时中断对等）',
    async () => {
      const pidPath = path.join(tmp, 'git-runner-child.pid');
      const script =
        "const cp=require('node:child_process');const fs=require('node:fs');" +
        "const p=cp.spawn(process.execPath,['-e','setTimeout(()=>{},30000)']);" +
        `fs.writeFileSync(${JSON.stringify(pidPath)},String(p.pid));setTimeout(()=>{},30000);`;
      const running = runProcess([process.execPath, '-e', script], 5);
      running.catch(() => {}); // 预挂接消费，避免断言前 rejection 未处理告警。
      await until(() => fs.existsSync(pidPath), 4000);
      expect(fs.existsSync(pidPath), 'git runner 子进程未写出 pid').toBe(true);
      const childPid = Number(fs.readFileSync(pidPath, 'utf-8'));
      expect(Number.isInteger(childPid)).toBe(true);

      await expect(withTimeout(running, 15_000)).rejects.toBeInstanceOf(TimeoutError);
      // py 侧 powershell Get-Process 探活；此处轻量档 process.kill(pid, 0)（校准条款 5）。
      await until(() => !isAlive(childPid), 3000);
      expect(isAlive(childPid), `git runner child pid ${childPid} 仍存活`).toBe(false);
    },
  );

  it('ensure 中文路径下天然幂等（test_ensure_is_naturally_idempotent_in_chinese_paths）', async () => {
    const repo = scratchRepo(tmp);
    const [manager, paths] = makeManager(tmp);
    const taskId = newUlid();
    const projectId = newUlid();
    const data = ensureData(repo, taskId, projectId);

    const first = await manager.ensure(data);
    // 新 manager 模拟 daemon 重启：幂等不能依赖进程内 cache。
    const second = await new GitWorktreeManager(paths).ensure(data);

    const target = resolvePath(paths.worktreePath(projectId, taskId));
    expect(first.changed).toBe(true);
    expect(second.changed).toBe(false);
    expect(first.status).not.toBeNull();
    expect(first.status!.status).toBe('active');
    expect(first.status!.path).toBe(target);
    expect(first.status!.branch).toBe(data.branch);
    expect(git(target, ['branch', '--show-current']).stdout.trim()).toBe(data.branch);
    const listed = git(repo, ['worktree', 'list', '--porcelain']).stdout.replace(/\\/g, '/');
    expect(listed).toContain(target.replace(/\\/g, '/'));
  });

  it('cleanup 从既有 worktree 反查主仓与分支（test_cleanup_recovers_repo_and_branch_from_existing_worktree）', async () => {
    const repo = scratchRepo(tmp);
    const [manager, paths] = makeManager(tmp);
    const taskId = newUlid();
    const projectId = newUlid();
    const data = ensureData(repo, taskId, projectId);
    await manager.ensure(data);

    // cleanup 帧只有 task_id；daemon 重启后从固定目录与 .git 指向恢复，不读持久 registry。
    const restarted = new GitWorktreeManager(paths);
    const result = await restarted.cleanup({ task_id: taskId });

    expect(result.changed).toBe(true);
    expect(result.status).not.toBeNull();
    expect(result.status!.status).toBe('cleaned');
    expect(result.status!.branch).toBe(data.branch);
    expect(result.status!.path).toBe(resolvePath(paths.worktreePath(projectId, taskId)));
  });

  it('cleanup 清除 daemon 重启后的失效 gitfile 残留（test_cleanup_removes_stale_gitfile_residual_after_daemon_restart）', async () => {
    const repo = scratchRepo(tmp);
    const [manager, paths] = makeManager(tmp);
    const taskId = newUlid();
    const projectId = newUlid();
    const data = ensureData(repo, taskId, projectId);
    await manager.ensure(data);
    const target = resolvePath(paths.worktreePath(projectId, taskId));
    const staleGitfile = fs.readFileSync(path.join(target, '.git'), 'utf-8');

    // 模拟 Windows remove 半完成态：登记已消失，物理目录（含失效 .git 指针）仍残留。
    git(repo, ['worktree', 'remove', '--force', target]);
    fs.mkdirSync(target, { recursive: true });
    fs.writeFileSync(path.join(target, '.git'), staleGitfile, 'utf-8');
    fs.writeFileSync(path.join(target, 'occupied-leftover.txt'), '残留', 'utf-8');

    const restarted = new GitWorktreeManager(paths);
    const result = await restarted.cleanup({ task_id: taskId });

    expect(result.changed).toBe(true);
    expect(result.status).not.toBeNull();
    expect(result.status!.status).toBe('cleaned');
    expect(result.status!.branch).toBe(data.branch);
    expect(result.status!.path).toBe(target);
    expect(fs.existsSync(target)).toBe(false);
  });

  it('cleanup 处理已摘登记的物理残留且重复为 noop（test_cleanup_handles_deregistered_residual_and_repeats_noop）', async () => {
    const repo = scratchRepo(tmp);
    const [manager, paths] = makeManager(tmp);
    const taskId = newUlid();
    const projectId = newUlid();
    const data = ensureData(repo, taskId, projectId);
    await manager.ensure(data);
    const target = resolvePath(paths.worktreePath(projectId, taskId));

    // 复现校准得到的半完成态：Git 登记已消失，但物理目录残留。
    git(repo, ['worktree', 'remove', '--force', target]);
    fs.mkdirSync(target, { recursive: true });
    fs.writeFileSync(path.join(target, 'occupied-leftover.txt'), '残留', 'utf-8');

    const first = await manager.cleanup({ task_id: taskId });
    const second = await manager.cleanup({ task_id: taskId });

    expect(first.changed).toBe(true);
    expect(second.changed).toBe(false);
    expect(first.status).not.toBeNull();
    expect(second.status).not.toBeNull();
    expect(first.status!.status).toBe('cleaned');
    expect(second.status!.status).toBe('cleaned');
    expect(first.status!.branch).toBe(data.branch);
    expect(second.status!.branch).toBe(data.branch);
    expect(first.status!.path).toBe(target);
    expect(second.status!.path).toBe(target);
    expect(fs.existsSync(target)).toBe(false);
    expect(
      git(repo, ['worktree', 'list', '--porcelain']).stdout.replace(/\\/g, '/'),
    ).not.toContain(target.replace(/\\/g, '/'));
  });

  it('cleanup 不越过显式 worktree lock（test_cleanup_does_not_override_explicit_worktree_lock）', async () => {
    const repo = scratchRepo(tmp);
    const [manager, paths] = makeManager(tmp);
    const taskId = newUlid();
    const projectId = newUlid();
    const data = ensureData(repo, taskId, projectId);
    await manager.ensure(data);
    const target = resolvePath(paths.worktreePath(projectId, taskId));
    git(repo, ['worktree', 'lock', '--reason', 'J3 test', target]);

    await expect(manager.cleanup({ task_id: taskId })).rejects.toBeInstanceOf(GitCommandError);

    expect(fs.statSync(target).isDirectory()).toBe(true);
    const listed = git(repo, ['worktree', 'list', '--porcelain']).stdout.replace(/\\/g, '/');
    expect(listed).toContain(target.replace(/\\/g, '/'));
    git(repo, ['worktree', 'unlock', target]);
  });

  it('merge 产 --no-ff 双亲提交且幂等（test_merge_creates_no_ff_commit_and_is_idempotent）', async () => {
    const repo = scratchRepo(tmp);
    const [manager, paths] = makeManager(tmp);
    const taskId = newUlid();
    const projectId = newUlid();
    const ensure = ensureData(repo, taskId, projectId);
    await manager.ensure(ensure);
    const target = resolvePath(paths.worktreePath(projectId, taskId));
    fs.writeFileSync(path.join(target, '中文交付.txt'), 'done\n', 'utf-8');
    git(target, ['add', '--', '中文交付.txt']);
    git(target, ['commit', '-m', '任务中文交付']);
    const merge = mergeData(repo, taskId, projectId, ensure.branch);

    const first = await manager.merge(merge);
    const second = await manager.merge(merge);

    expect(first.changed).toBe(true);
    expect(second.changed).toBe(false);
    expect(first.status).not.toBeNull();
    expect(second.status).not.toBeNull();
    expect(first.status!.status).toBe('merged');
    expect(second.status!.status).toBe('merged');
    expect(first.status!.merge_commit).toBe(second.status!.merge_commit);
    const parents = git(repo, ['rev-list', '--parents', '-n', '1', 'HEAD'])
      .stdout.split(/\s+/)
      .filter((t) => t !== '');
    expect(parents).toHaveLength(3); // commit + 两个 parent，证明不是 fast-forward。
    expect(first.status!.merge_commit).toBe(parents[0]);
    expect(fs.readFileSync(path.join(repo, '中文交付.txt'), 'utf-8')).toBe('done\n');
  });

  it.runIf(process.platform === 'win32')(
    '中断的 merge 恢复干净主干并可重放（test_cancelled_merge_restores_clean_main_and_replays；py=任务取消注入，TS 无取消→短超时中断同一 hook 卡点对等）',
    async () => {
      const repo = scratchRepo(tmp);
      const [manager, paths] = makeManager(tmp);
      const taskId = newUlid();
      const projectId = newUlid();
      const ensure = ensureData(repo, taskId, projectId);
      await manager.ensure(ensure);
      const target = resolvePath(paths.worktreePath(projectId, taskId));
      fs.writeFileSync(path.join(target, 'cancelled.txt'), 'branch\n', 'utf-8');
      git(target, ['add', '--', 'cancelled.txt']);
      git(target, ['commit', '-m', 'blocked merge branch']);
      const merge = mergeData(repo, taskId, projectId, ensure.branch);
      const beforeHead = git(repo, ['rev-parse', 'HEAD']).stdout.trim();
      const hook = path.join(repo, '.git', 'hooks', 'pre-merge-commit');
      fs.writeFileSync(hook, '#!/bin/sh\nprintf started > .git/hook-started\nwhile :; do :; done\n', 'utf-8');
      fs.chmodSync(hook, 0o755);

      // py：默认超时 + 等 marker 后 task.cancel()；TS：短超时中断卡在同一 hook 死循环上。
      const interrupted = new GitWorktreeManager(paths, { timeoutSec: 3 });
      await expect(withTimeout(interrupted.merge(merge), 12_000)).rejects.toBeInstanceOf(TimeoutError);
      expect(fs.existsSync(path.join(repo, '.git', 'hook-started')), 'pre-merge-commit hook 未启动').toBe(true);

      expect(git(repo, ['rev-parse', 'HEAD']).stdout.trim()).toBe(beforeHead);
      expect(git(repo, ['status', '--porcelain=v1', '-z']).stdout).toBe('');
      expect(git(repo, ['rev-parse', '-q', '--verify', 'MERGE_HEAD'], false).status).not.toBe(0);
      fs.unlinkSync(hook);
      const replay = await new GitWorktreeManager(paths).merge(merge);
      expect(replay.status).not.toBeNull();
      expect(replay.status!.status).toBe('merged');
      expect(fs.readFileSync(path.join(repo, 'cancelled.txt'), 'utf-8')).toBe('branch\n');
    },
  );

  it.runIf(process.platform === 'win32')(
    '超时的 merge 恢复干净主干并可重放（test_timed_out_merge_restores_clean_main_and_replays）',
    async () => {
      const repo = scratchRepo(tmp);
      const [manager, paths] = makeManager(tmp);
      const taskId = newUlid();
      const projectId = newUlid();
      const ensure = ensureData(repo, taskId, projectId);
      await manager.ensure(ensure);
      const target = resolvePath(paths.worktreePath(projectId, taskId));
      fs.writeFileSync(path.join(target, 'timeout.txt'), 'branch\n', 'utf-8');
      git(target, ['add', '--', 'timeout.txt']);
      git(target, ['commit', '-m', 'timed out merge branch']);
      const merge = mergeData(repo, taskId, projectId, ensure.branch);
      const beforeHead = git(repo, ['rev-parse', 'HEAD']).stdout.trim();
      const hook = path.join(repo, '.git', 'hooks', 'pre-merge-commit');
      fs.writeFileSync(hook, '#!/bin/sh\nwhile :; do :; done\n', 'utf-8');
      fs.chmodSync(hook, 0o755);

      await expect(
        new GitWorktreeManager(paths, { timeoutSec: 0.3 }).merge(merge),
      ).rejects.toBeInstanceOf(TimeoutError);

      expect(git(repo, ['rev-parse', 'HEAD']).stdout.trim()).toBe(beforeHead);
      expect(git(repo, ['status', '--porcelain=v1', '-z']).stdout).toBe('');
      expect(git(repo, ['rev-parse', '-q', '--verify', 'MERGE_HEAD'], false).status).not.toBe(0);
      fs.unlinkSync(hook);
      const replay = await new GitWorktreeManager(paths).merge(merge);
      expect(replay.status).not.toBeNull();
      expect(replay.status!.status).toBe('merged');
    },
  );

  it('clean 未完成 merge 重放到成功（test_clean_unfinished_merge_replays_to_success）', async () => {
    const repo = scratchRepo(tmp);
    const [manager, paths] = makeManager(tmp);
    const taskId = newUlid();
    const projectId = newUlid();
    const ensure = ensureData(repo, taskId, projectId);
    await manager.ensure(ensure);
    const target = resolvePath(paths.worktreePath(projectId, taskId));
    fs.writeFileSync(path.join(target, 'unfinished.txt'), 'branch\n', 'utf-8');
    git(target, ['add', '--', 'unfinished.txt']);
    git(target, ['commit', '-m', 'unfinished clean merge']);
    const merge = mergeData(repo, taskId, projectId, ensure.branch);
    git(repo, ['merge', '--no-ff', '--no-commit', ensure.branch]);
    expect(git(repo, ['diff', '--name-only', '--diff-filter=U']).stdout).toBe('');
    expect(git(repo, ['rev-parse', '-q', '--verify', 'MERGE_HEAD']).status).toBe(0);

    const replay = await new GitWorktreeManager(paths).merge(merge);

    expect(replay.status).not.toBeNull();
    expect(replay.status!.status).toBe('merged');
    expect(git(repo, ['status', '--porcelain=v1', '-z']).stdout).toBe('');
    const parents = git(repo, ['rev-list', '--parents', '-n', '1', 'HEAD'])
      .stdout.split(/\s+/)
      .filter((t) => t !== '');
    expect(parents).toHaveLength(3);
  });

  it('merge 冲突在 abort 前采集文件并恢复主干（test_merge_conflict_collects_files_before_abort_and_restores_main）', async () => {
    const repo = scratchRepo(tmp);
    const [manager, paths] = makeManager(tmp);
    const taskId = newUlid();
    const projectId = newUlid();
    const ensure = ensureData(repo, taskId, projectId);
    await manager.ensure(ensure);
    const target = resolvePath(paths.worktreePath(projectId, taskId));

    fs.writeFileSync(path.join(target, 'conflict.txt'), 'branch 中文\n', 'utf-8');
    git(target, ['add', '--', 'conflict.txt']);
    git(target, ['commit', '-m', '任务侧冲突']);
    fs.writeFileSync(path.join(repo, 'conflict.txt'), 'main 中文\n', 'utf-8');
    git(repo, ['add', '--', 'conflict.txt']);
    git(repo, ['commit', '-m', '主干侧冲突']);
    const beforeHead = git(repo, ['rev-parse', 'HEAD']).stdout.trim();
    const beforeStatus = git(repo, ['status', '--porcelain=v1', '-z']).stdout;

    const result = await manager.merge(mergeData(repo, taskId, projectId, ensure.branch));

    expect(result.changed).toBe(true);
    expect(result.status).not.toBeNull();
    expect(result.status!.status).toBe('conflicted');
    expect(result.status!.conflict_files).toEqual(['conflict.txt']);
    expect(git(repo, ['rev-parse', 'HEAD']).stdout.trim()).toBe(beforeHead);
    expect(git(repo, ['status', '--porcelain=v1', '-z']).stdout).toBe(beforeStatus);
    expect(git(repo, ['rev-parse', '-q', '--verify', 'MERGE_HEAD'], false).status).not.toBe(0);
    expect(fs.readFileSync(path.join(repo, 'conflict.txt'), 'utf-8')).toBe('main 中文\n');
  });

  it('merge 恢复崩溃 daemon 留下的冲突态（test_merge_recovers_conflict_left_by_crashed_daemon）', async () => {
    const repo = scratchRepo(tmp);
    const [manager, paths] = makeManager(tmp);
    const taskId = newUlid();
    const projectId = newUlid();
    const ensure = ensureData(repo, taskId, projectId);
    await manager.ensure(ensure);
    const target = resolvePath(paths.worktreePath(projectId, taskId));
    fs.writeFileSync(path.join(target, 'conflict.txt'), 'branch crash\n', 'utf-8');
    git(target, ['add', '--', 'conflict.txt']);
    git(target, ['commit', '-m', 'branch before crash']);
    fs.writeFileSync(path.join(repo, 'conflict.txt'), 'main crash\n', 'utf-8');
    git(repo, ['add', '--', 'conflict.txt']);
    git(repo, ['commit', '-m', 'main before crash']);
    const beforeHead = git(repo, ['rev-parse', 'HEAD']).stdout.trim();

    const crashed = git(repo, ['merge', '--no-ff', '-m', 'crashed merge', '--', ensure.branch], false);
    expect(crashed.status).toBe(1);
    expect(git(repo, ['rev-parse', '-q', '--verify', 'MERGE_HEAD']).status).toBe(0);

    const restarted = new GitWorktreeManager(paths);
    const result = await restarted.merge(mergeData(repo, taskId, projectId, ensure.branch));

    expect(result.changed).toBe(true);
    expect(result.status).not.toBeNull();
    expect(result.status!.status).toBe('conflicted');
    expect(result.status!.conflict_files).toEqual(['conflict.txt']);
    expect(git(repo, ['rev-parse', 'HEAD']).stdout.trim()).toBe(beforeHead);
    expect(git(repo, ['rev-parse', '-q', '--verify', 'MERGE_HEAD'], false).status).not.toBe(0);
    expect(git(repo, ['status', '--porcelain=v1']).stdout).toBe('');
  });
});
