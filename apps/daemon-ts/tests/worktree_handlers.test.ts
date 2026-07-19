/**
 * M6 J3 daemon 帧接缝：worktree 指令后台化（#1）——status 先于 ack、reader 不阻塞、幂等重放。
 * 对等基准 = py test_worktree_handlers.py（6 用例逐条对应）。
 *
 * py→TS 移植登记（非行为改进）：
 * - 真 git 用例照 git_worktrees.test.ts 既有做法（mkdtemp + spawnSync 真 git；win32 .git
 *   对象只读位 → chmod 后重试删 tmp）。
 * - py client._worktree_tasks / client._dispatch 私有面直访；TS 同名 worktreeTasks/dispatch
 *   为 private 且无公开观测面 → (client as ...) 结构断言直访（任务书授权的最后手段）。
 * - test_shutdown_cancels_inflight_worktree：py = task.cancel() 注入取消并等回收；TS 无任务
 *   取消注入（client.ts 头部登记差异「等其自然完成，不强杀」）→ 等价义务改为断言 shutdown
 *   在在飞任务完成前保持挂起、放行后收尾且注册表清空（「断连不取消，仅 shutdown 收口」不变）。
 */

import { spawnSync } from 'node:child_process';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import type { FrameError, WorktreeMergeData } from '@coagentia/contracts-ts';

import { AsyncEvent, sleep, withTimeout } from '../src/aio.ts';
import type { DaemonClient } from '../src/client.ts';
import { resolvePath } from '../src/git.ts';
import type { WorktreeOperation } from '../src/git.ts';
import { TransportClosed } from '../src/transport.ts';
import type { JsonObject } from '../src/transport.ts';
import { newUlid } from '../src/util.ts';
import { RecordingTransport, instr, makeClient, until } from './helpers.ts';

let tmp: string;

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'coagentia-wt-handlers-'));
});

afterEach(() => {
  rmrfClearingReadonly(tmp);
});

/** 首个 ack 发送即抛 TransportClosed（对等 py _DropFirstAckTransport）。 */
class DropFirstAckTransport extends RecordingTransport {
  dropAck = true;

  override async send(frame: JsonObject): Promise<void> {
    if (frame['kind'] === 'ack' && this.dropAck) {
      this.dropAck = false;
      throw new TransportClosed('drop ack');
    }
    await super.send(frame);
  }
}

/** 对等 py 测试的 _repo 助手（真 git init + 种子提交；UTF-8 路径）。 */
function repoDir(base: string): string {
  const repo = path.join(base, '处理器 中文 repo');
  fs.mkdirSync(repo);
  const init = spawnSync('git', ['init', '-b', 'main', repo], { encoding: 'utf-8' });
  if (init.status !== 0) {
    throw new Error(`git init failed (${init.status}): ${init.stdout}\n${init.stderr}`);
  }
  git(repo, ['config', 'user.name', 'CoAgentia Test']);
  git(repo, ['config', 'user.email', 'test@coagentia.local']);
  git(repo, ['config', 'core.autocrlf', 'false']);
  fs.writeFileSync(path.join(repo, 'seed.txt'), 'seed\n', 'utf-8');
  git(repo, ['add', '--', 'seed.txt']);
  git(repo, ['commit', '-m', 'seed']);
  return repo;
}

function git(repo: string, args: string[]): void {
  const res = spawnSync('git', ['-C', repo, ...args], { encoding: 'utf-8' });
  const status = res.status ?? -1;
  if (status !== 0) {
    throw new Error(`git ${args.join(' ')} failed (${status}): ${res.stdout}\n${res.stderr}`);
  }
}

function mergeData(repo: string, taskId: string, projectId: string): JsonObject {
  return {
    task_id: taskId,
    project_id: projectId,
    repo_path: repo,
    branch: `coagentia/task-${taskId}`,
    message: `Merge task ${taskId}`,
  };
}

/** 私有后台任务注册表的结构视图（对等 py client._worktree_tasks 直访）。 */
function tasksOf(client: DaemonClient): Map<string, Promise<void>> {
  return (client as unknown as { worktreeTasks: Map<string, Promise<void>> }).worktreeTasks;
}

async function drain(client: DaemonClient): Promise<void> {
  await until(() => tasksOf(client).size === 0);
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

describe('worktree handlers（契约 D §5.3；#1 后台化）', () => {
  it('ensure/cleanup 报 status 先于 ack 且幂等（test_ensure_and_cleanup_report_status_before_ack）', async () => {
    const repo = repoDir(tmp);
    const transport = new RecordingTransport();
    const { client } = makeClient(tmp, { transport });
    const taskId = newUlid();
    const projectId = newUlid();
    const branch = `coagentia/task-${taskId}`;
    const ensureData = {
      task_id: taskId,
      project_id: projectId,
      repo_path: repo,
      branch,
    };

    const ensureFrame = instr('worktree.ensure', ensureData);
    await client.handleInstr(ensureFrame);
    await drain(client);
    const firstTail = transport.sent.slice(-2);
    expect(firstTail.map((frame) => frame['kind'])).toEqual(['report', 'ack']); // status→ack 保序不变
    expect(firstTail[0]!['type']).toBe('worktree.status');
    expect((firstTail[0]!['data'] as JsonObject)['status']).toBe('active');
    expect(firstTail[1]!['result']).toBe('done');

    await client.handleInstr(ensureFrame);
    await drain(client);
    const secondTail = transport.sent.slice(-2);
    expect((secondTail[0]!['data'] as JsonObject)['status']).toBe('active');
    expect(secondTail[1]!['result']).toBe('noop');

    const cleanupFrame = instr('worktree.cleanup', { task_id: taskId });
    await client.handleInstr(cleanupFrame);
    await drain(client);
    const cleanupTail = transport.sent.slice(-2);
    expect(cleanupTail[0]!['type']).toBe('worktree.status');
    expect(cleanupTail[0]!['data']).toEqual({
      task_id: taskId,
      status: 'cleaned',
      branch,
      path: resolvePath(client.paths.worktreePath(projectId, taskId)),
      merge_commit: null,
      conflict_files: null,
    });
    expect(cleanupTail[1]!['result']).toBe('done');

    await client.handleInstr(cleanupFrame);
    await drain(client);
    const repeatedTail = transport.sent.slice(-2);
    expect((repeatedTail[0]!['data'] as JsonObject)['status']).toBe('cleaned');
    expect(repeatedTail[1]!['result']).toBe('noop');
  });

  it('同 merge 帧重发重放终态（test_same_merge_frame_replays_terminal_status）', async () => {
    const repo = repoDir(tmp);
    const transport = new RecordingTransport();
    const { client } = makeClient(tmp, { transport });
    const taskId = newUlid();
    const projectId = newUlid();
    await client.handleInstr(
      instr('worktree.ensure', {
        task_id: taskId,
        project_id: projectId,
        repo_path: repo,
        branch: `coagentia/task-${taskId}`,
      }),
    );
    await drain(client);
    const target = client.paths.worktreePath(projectId, taskId);
    fs.writeFileSync(path.join(target, 'delivery.txt'), 'done\n', 'utf-8');
    git(target, ['add', '--', 'delivery.txt']);
    git(target, ['commit', '-m', 'delivery']);
    const mergeFrame = instr('worktree.merge', mergeData(repo, taskId, projectId));

    await client.handleInstr(mergeFrame);
    await drain(client);
    expect((transport.sent[transport.sent.length - 2]!['data'] as JsonObject)['status']).toBe('merged');
    expect(transport.sent[transport.sent.length - 1]!['result']).toBe('done');

    await client.handleInstr(mergeFrame);
    await drain(client);
    expect((transport.sent[transport.sent.length - 2]!['data'] as JsonObject)['status']).toBe('merged');
    expect(transport.sent[transport.sent.length - 1]!['result']).toBe('noop');
  });

  it('ack 发送丢失后同帧重发补报 status（test_same_frame_retries_status_when_ack_send_was_lost）', async () => {
    const repo = repoDir(tmp);
    const dropped = new DropFirstAckTransport();
    const { client } = makeClient(tmp, { transport: dropped });
    const taskId = newUlid();
    const projectId = newUlid();
    const frame = instr('worktree.ensure', {
      task_id: taskId,
      project_id: projectId,
      repo_path: repo,
      branch: `coagentia/task-${taskId}`,
    });

    // 首轮：后台任务里 ack 被 drop（TransportClosed 被 suppress，不再上抛）→ 只报 status。
    await client.handleInstr(frame);
    await drain(client);
    expect(dropped.reports().map((sent) => sent['type'])).toEqual(['worktree.status']);
    expect(dropped.acks()).toEqual([]);

    // 换新传输重发同帧（frame_id 已不在 worktreeTasks）→ 后台幂等重跑 → 再报 status + ack=noop。
    const retryTransport = new RecordingTransport();
    client._transport = retryTransport;
    await client.handleInstr(frame);
    await drain(client);
    expect(retryTransport.reports().map((sent) => sent['type'])).toEqual(['worktree.status']);
    expect(retryTransport.lastAck()['result']).toBe('noop');
  });

  it('worktree op 不阻塞 reader（test_worktree_op_does_not_block_reader）', async () => {
    // #1 核心回归：worktree op 后台化 → handleInstr 立即返回，reader 仍能处理 ping→pong。
    const transport = new RecordingTransport();
    const { client } = makeClient(tmp, { transport });
    const gate = new AsyncEvent();

    client.git.merge = async (_data: WorktreeMergeData): Promise<WorktreeOperation> => {
      await gate.wait();
      return { changed: true, status: null };
    };
    const frame = instr('worktree.merge', mergeData(path.join(tmp, 'x'), newUlid(), newUlid()));
    await withTimeout(client.handleInstr(frame), 1000); // 不等 git 子进程
    expect(tasksOf(client).size).toBeGreaterThan(0); // 后台任务在飞
    // reader 侧仍即时响应（py client._dispatch 私有面直调对应）。
    await (client as unknown as { dispatch(f: JsonObject): Promise<void> }).dispatch({ kind: 'ping' });
    expect(transport.sent[transport.sent.length - 1]!['kind']).toBe('pong');
    gate.set();
    await drain(client);
  });

  it('merge 硬失败经后台仍 ack FAILED（test_worktree_hard_failure_acks_failed）', async () => {
    // server 侧 fail_dispatch 路径不变。
    const transport = new RecordingTransport();
    const { client } = makeClient(tmp, { transport });

    client.git.merge = async (_data: WorktreeMergeData): Promise<WorktreeOperation> => {
      throw new Error('merge boom');
    };
    const frame = instr('worktree.merge', mergeData(path.join(tmp, 'x'), newUlid(), newUlid()));
    await client.handleInstr(frame);
    await drain(client);
    expect(transport.lastAck()['result']).toBe('failed');
    expect((transport.lastAck()['error'] as FrameError).code).toBe('HANDLER_ERROR');
  });

  it('shutdown 等在飞 worktree 任务收尾（test_shutdown_cancels_inflight_worktree 的 TS 等价面）', async () => {
    // py = task.cancel() 注入取消并等回收；TS 无取消注入（client.ts 登记「等其自然完成」）→
    // 等价断言：shutdown 在在飞任务完成前保持挂起（不强杀），放行后收尾且注册表清空。
    // 「断连不取消，仅 shutdown 收口」的义务面不变。
    const transport = new RecordingTransport();
    const { client } = makeClient(tmp, { transport });
    const gate = new AsyncEvent();

    client.git.merge = async (_data: WorktreeMergeData): Promise<WorktreeOperation> => {
      await gate.wait();
      return { changed: true, status: null };
    };
    const frame = instr('worktree.merge', mergeData(path.join(tmp, 'x'), newUlid(), newUlid()));
    await client.handleInstr(frame);
    expect(tasksOf(client).size).toBeGreaterThan(0);
    let settled = false;
    const done = client.shutdown().then(() => {
      settled = true;
    });
    await sleep(50);
    expect(settled).toBe(false); // 在飞任务未完 → shutdown 挂起等待
    gate.set();
    await withTimeout(done, 5000);
    expect(tasksOf(client).size).toBe(0);
  });
});
