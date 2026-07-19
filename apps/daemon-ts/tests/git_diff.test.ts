/**
 * M6 J4 Diff 查询：真实 Git 仓库形状、截断与 query/reply 验收（对等基准 = py test_git_diff.py）。
 *
 * py→TS 移植登记（非行为改进）：
 * - py 猴补 manager._git 计数子进程 → TS 经构造参数 runner 注入计数（每次 _git 恰对应一次
 *   runner 调用，计数面等价）。
 * - py 用 `patch.encode().decode()` 验证合法 UTF-8 → TS 断言无 U+FFFD 替换符（JS 字符串
 *   恒为 UTF-16，截断缺陷只会以替换符形态显形）。
 * - test_git_diff_query_returns_contract_reply_and_errors_like_home_queries 已由 W4 收账
 *   （见文中：makeClient + client.handleQuery 公开面；py AsyncMock 猴补 client.git.diff →
 *   TS 直赋实例方法 + 入参记录数组等价，断言面逐条保留）。
 */

import { spawnSync } from 'node:child_process';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import type { DiffPayload, GitDiffQuery, WorktreeEnsureData } from '@coagentia/contracts-ts';

import {
  DIFF_MAX_FILES,
  DIFF_MAX_PATCH_BYTES,
  GitCommandError,
  GitWorktreeManager,
  runProcess,
  splitDiffSections,
} from '../src/git.ts';
import type { ProcessRunner } from '../src/git.ts';
import { DataPaths } from '../src/paths.ts';
import { newUlid, nowIso } from '../src/util.ts';
import { RecordingTransport, makeClient } from './helpers.ts';

let tmp: string;

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'coagentia-git-diff-'));
});

afterEach(() => {
  rmrfClearingReadonly(tmp);
});

/** 对等 py 测试的 _git 助手（check 恒真，返回 stdout）。 */
function git(repo: string, ...args: string[]): string {
  const res = spawnSync('git', ['-c', 'core.quotepath=false', '-C', repo, ...args], {
    encoding: 'utf-8',
  });
  const status = res.status ?? -1;
  if (status !== 0) {
    throw new Error(`git ${args.join(' ')} failed (${status}): ${res.stdout}\n${res.stderr}`);
  }
  return res.stdout ?? '';
}

function scratchRepo(base: string): string {
  const repo = path.join(base, '中文 Diff 项目');
  fs.mkdirSync(repo);
  const init = spawnSync('git', ['init', '-b', 'main', repo], { encoding: 'utf-8' });
  if (init.status !== 0) {
    throw new Error(`git init failed (${init.status}): ${init.stdout}\n${init.stderr}`);
  }
  git(repo, 'config', 'user.name', 'CoAgentia Test');
  git(repo, 'config', 'user.email', 'test@coagentia.local');
  git(repo, 'config', 'core.autocrlf', 'false');
  fs.writeFileSync(path.join(repo, '修改.txt'), 'before\n', 'utf-8');
  fs.writeFileSync(path.join(repo, 'delete.txt'), 'delete me\n', 'utf-8');
  fs.writeFileSync(path.join(repo, 'rename = old.txt'), 'same\n', 'utf-8');
  fs.writeFileSync(path.join(repo, 'binary.bin'), Buffer.concat([Buffer.from([0, 1]), Buffer.from('old', 'utf-8')]));
  git(repo, 'add', '-A');
  git(repo, 'commit', '-m', 'base');
  return repo;
}

interface TreeSetup {
  manager: GitWorktreeManager;
  paths: DataPaths;
  repo: string;
  taskId: string;
  projectId: string;
  branch: string;
}

async function managerWithTree(base: string): Promise<TreeSetup> {
  const repo = scratchRepo(base);
  const paths = new DataPaths(path.join(base, '数据 根'));
  paths.ensureDirs();
  const manager = new GitWorktreeManager(paths);
  const taskId = newUlid();
  const projectId = newUlid();
  const branch = `coagentia/task-${taskId}`;
  const ensure: WorktreeEnsureData = {
    task_id: taskId,
    project_id: projectId,
    repo_path: repo,
    branch,
  };
  await manager.ensure(ensure);
  return { manager, paths, repo, taskId, projectId, branch };
}

function query(setup: TreeSetup): GitDiffQuery {
  return { project_id: setup.projectId, repo_path: setup.repo, task_id: setup.taskId };
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

describe('git diff（契约 D §6）', () => {
  it('diff 报全形状与 UTF-8 路径（test_diff_reports_all_git_shapes_with_utf8_paths）', async () => {
    const setup = await managerWithTree(tmp);
    const tree = setup.paths.worktreePath(setup.projectId, setup.taskId);
    fs.writeFileSync(path.join(tree, '修改.txt'), 'after\n', 'utf-8');
    fs.unlinkSync(path.join(tree, 'delete.txt'));
    fs.renameSync(path.join(tree, 'rename = old.txt'), path.join(tree, 'rename = new.txt'));
    fs.writeFileSync(
      path.join(tree, 'binary.bin'),
      Buffer.concat([Buffer.from([0, 1]), Buffer.from('new', 'utf-8')]),
    );
    fs.writeFileSync(path.join(tree, '新增 中文.txt'), '第一行\n第二行\n', 'utf-8');
    git(tree, 'add', '-A');
    git(tree, 'commit', '-m', '中文 diff');

    const payload = await setup.manager.diff(query(setup));

    expect(payload.base_ref).toBe('main');
    expect(payload.head_ref).toBe(setup.branch);
    expect(payload.files_truncated).toBe(false);
    const byPath = new Map(payload.files.map((item) => [item.path, item]));
    expect(byPath.get('新增 中文.txt')!.status).toBe('added');
    expect(byPath.get('修改.txt')!.status).toBe('modified');
    expect(byPath.get('delete.txt')!.status).toBe('deleted');
    const renamed = byPath.get('rename = new.txt')!;
    expect(renamed.status).toBe('renamed');
    expect(renamed.old_path).toBe('rename = old.txt');
    expect(renamed.patch).toContain('rename from rename = old.txt');
    expect(renamed.patch).toContain('rename to rename = new.txt');
    const binary = byPath.get('binary.bin')!;
    expect([binary.additions, binary.deletions, binary.patch]).toEqual([0, 0, '']);
    expect(binary.patch_truncated).toBe(false);
    expect(byPath.get('修改.txt')!.patch).toContain('+after');
    expect(byPath.get('修改.txt')!.patch).toContain('-before');
    expect(payload.total_additions).toBe(
      payload.files.reduce((acc, item) => acc + item.additions, 0),
    );
    expect(payload.total_deletions).toBe(
      payload.files.reduce((acc, item) => acc + item.deletions, 0),
    );
  });

  it('diff 截断 UTF-8 patch 与文件表但总量覆盖全部（test_diff_truncates_utf8_patch_and_file_list_but_totals_cover_all）', async () => {
    expect(DIFF_MAX_FILES).toBe(200);
    expect(DIFF_MAX_PATCH_BYTES).toBe(64 * 1024);
    const setup = await managerWithTree(tmp);
    const tree = setup.paths.worktreePath(setup.projectId, setup.taskId);
    fs.writeFileSync(
      path.join(tree, 'a-large.txt'),
      `${Array.from({ length: 80 }, () => '中文内容').join('\n')}\n`,
      'utf-8',
    );
    fs.writeFileSync(path.join(tree, 'z-extra.txt'), 'one\ntwo\n', 'utf-8');
    git(tree, 'add', '-A');
    git(tree, 'commit', '-m', 'large diff');

    const payload = await setup.manager.diff(query(setup), { maxFiles: 1, maxPatchBytes: 95 });

    expect(payload.files_truncated).toBe(true);
    expect(payload.files).toHaveLength(1);
    expect(payload.files[0]!.patch_truncated).toBe(true);
    expect(Buffer.byteLength(payload.files[0]!.patch, 'utf-8')).toBeLessThanOrEqual(95);
    // py 侧 encode().decode() 验证合法 UTF-8；JS 侧截断缺陷只会以 U+FFFD 显形。
    expect(payload.files[0]!.patch).not.toContain('�');
    expect(payload.total_additions).toBeGreaterThan(payload.files[0]!.additions);
  });

  it('worktree cleanup 后 diff 仍可读（test_diff_remains_readable_after_worktree_cleanup）', async () => {
    const setup = await managerWithTree(tmp);
    const tree = setup.paths.worktreePath(setup.projectId, setup.taskId);
    fs.writeFileSync(path.join(tree, 'delivered.txt'), 'done\n', 'utf-8');
    git(tree, 'add', '-A');
    git(tree, 'commit', '-m', 'delivered');

    await setup.manager.cleanup({ task_id: setup.taskId });
    const payload = await setup.manager.diff(query(setup));

    expect(fs.existsSync(tree)).toBe(false);
    expect(git(setup.repo, 'show-ref', '--verify', `refs/heads/${setup.branch}`)).toBeTruthy();
    expect(payload.head_ref).toBe(setup.branch);
    expect(payload.files.map((item) => item.path)).toEqual(['delivered.txt']);
  });

  it('任务分支缺失是查询失败（test_diff_missing_task_branch_is_a_query_failure）', async () => {
    const setup = await managerWithTree(tmp);
    await setup.manager.cleanup({ task_id: setup.taskId });
    git(setup.repo, 'branch', '-D', setup.branch);

    const err: unknown = await setup.manager.diff(query(setup)).then(
      () => null,
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(GitCommandError);
    expect(String(err)).toMatch(/Diff ref 不存在/);
  });

  it('git.diff 查询帧返回契约 reply 且错误语义同 home 查询（test_git_diff_query_returns_contract_reply_and_errors_like_home_queries）', async () => {
    // W4 收账：被测主体 = client.handleQuery（公开面）。py AsyncMock 猴补 client.git.diff →
    // TS 直赋实例方法 + diffCalls 记录入参（assert_awaited_once_with 对应）。
    const transport = new RecordingTransport();
    const { client } = makeClient(tmp, { transport });
    const taskId = newUlid();
    const projectId = newUlid();
    const expected: DiffPayload = {
      base_ref: 'main',
      head_ref: `coagentia/task-${taskId}`,
      files: [],
      total_additions: 0,
      total_deletions: 0,
      files_truncated: false,
    };
    const diffCalls: GitDiffQuery[] = [];
    client.git.diff = async (q: GitDiffQuery): Promise<DiffPayload> => {
      diffCalls.push(q);
      return expected;
    };
    const frame: Record<string, unknown> = {
      v: 1,
      kind: 'query',
      frame_id: newUlid(),
      type: 'git.diff',
      at: nowIso(),
      data: {
        project_id: projectId,
        repo_path: tmp,
        task_id: taskId,
        base: null,
      },
    };

    await client.handleQuery(frame);

    const reply = transport.sent[transport.sent.length - 1]!;
    expect(reply['kind']).toBe('reply');
    expect(reply['ref']).toBe(frame['frame_id']);
    expect(reply['data']).toEqual(expected); // = py DiffPayload.model_validate(reply.data) == expected
    expect(diffCalls).toHaveLength(1);
    expect(diffCalls[0]).toEqual(frame['data']); // = py assert_awaited_once_with(GitDiffQuery(...))

    // 失败语义同 home 查询：异常收敛为 {error: message} reply。
    client.git.diff = async (): Promise<DiffPayload> => {
      throw new Error('diff failed');
    };
    frame['frame_id'] = newUlid();
    await client.handleQuery(frame);
    expect(transport.sent[transport.sent.length - 1]!['data']).toEqual({ error: 'diff failed' });
  });

  it('#8 单测：切分只认行首字面 `diff --git `（test_split_diff_sections_handles_tricky_shapes）', () => {
    const raw =
      'diff --git a/a.txt b/a.txt\n' +
      'index 0000000..1111111 100644\n' +
      '--- a/a.txt\n' +
      '+++ b/a.txt\n' +
      '@@ -1 +1,2 @@\n' +
      ' keep\n' +
      '+diff --git a/fake b/fake\n' + // 内容行：以 '+' 起头，不得开新段
      'diff --git a/old name.txt b/new name.txt\n' +
      'similarity index 100%\n' +
      'rename from old name.txt\n' +
      'rename to new name.txt\n' +
      'diff --git a/bin.bin b/bin.bin\n' +
      'index 0000000..1111111 100644\n' +
      'Binary files a/bin.bin and b/bin.bin differ\n';
    const sections = splitDiffSections(raw);
    expect(sections).toHaveLength(3);
    expect(sections[0]!.endsWith('+diff --git a/fake b/fake\n')).toBe(true);
    expect(sections[1]!.startsWith('diff --git a/old name.txt b/new name.txt\n')).toBe(true);
    expect(sections[1]).toContain('rename to new name.txt');
    expect(sections[2]!.endsWith('differ\n')).toBe(true);
    expect(sections.join('')).toBe(raw); // keepends 无损切分
    expect(splitDiffSections('')).toEqual([]);
  });

  it('#8 端到端：内容行恰为 diff 头时不串段（test_diff_patch_content_containing_diff_header_does_not_bleed）', async () => {
    const setup = await managerWithTree(tmp);
    const tree = setup.paths.worktreePath(setup.projectId, setup.taskId);
    fs.writeFileSync(path.join(tree, 'aa.txt'), 'diff --git a/x b/x\nnormal\n', 'utf-8');
    fs.writeFileSync(path.join(tree, 'zz.txt'), 'tail\n', 'utf-8');
    git(tree, 'add', '-A');
    git(tree, 'commit', '-m', 'tricky content');

    const payload = await setup.manager.diff(query(setup));

    const byPath = new Map(payload.files.map((item) => [item.path, item]));
    expect(byPath.get('aa.txt')!.patch).toContain('+diff --git a/x b/x');
    expect(byPath.get('aa.txt')!.patch).not.toContain('tail');
    expect(byPath.get('zz.txt')!.patch).toContain('+tail');
    expect(byPath.get('zz.txt')!.patch).not.toContain('diff --git a/x b/x');
  });

  it('#8 核心回归：diff 子进程数与变更文件数无关（test_diff_spawns_constant_process_count_regardless_of_file_count）', async () => {
    const setup = await managerWithTree(tmp);
    const tree = setup.paths.worktreePath(setup.projectId, setup.taskId);
    fs.writeFileSync(path.join(tree, 'one.txt'), '1\n', 'utf-8');
    git(tree, 'add', '-A');
    git(tree, 'commit', '-m', 'one file');

    // py 猴补 manager._git 计数；TS 经构造 runner 注入等价（每次 _git 恰起一个 git 子进程）。
    const calls: string[][] = [];
    const counting: ProcessRunner = async (argv, timeoutSec) => {
      calls.push([...argv]);
      return runProcess(argv, timeoutSec);
    };
    const countingManager = new GitWorktreeManager(setup.paths, { runner: counting });
    const q = query(setup);
    await countingManager.diff(q);
    const singleFileCount = calls.length;

    for (let i = 0; i < 7; i += 1) {
      fs.writeFileSync(path.join(tree, `more-${i}.txt`), `${i}\n`, 'utf-8');
    }
    git(tree, 'add', '-A');
    git(tree, 'commit', '-m', 'seven more files');
    calls.length = 0;
    await countingManager.diff(q);
    expect(calls.length).toBe(singleFileCount); // 子进程数恒定
  });
});
