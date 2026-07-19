/**
 * M6a J5 daemon check.run：主工作区执行、UTF-8 尾、自然键与落盘重传（对等基准 = py test_checks.py）。
 *
 * py→TS 移植登记（非行为改进）：
 * - py 用 sys.executable(python) 起子进程；TS 用 process.execPath(node) 等价替换（同为
 *   「打印 cwd 名/超长行/中文尾」与「长睡眠」探针，避免测试依赖 python 解释器在位）。
 * - py 取消 = asyncio task.cancel()；TS 无任务取消注入 → AbortController.abort()（checks.ts
 *   的取消通道），命中 CheckCancelledError（= py CancelledError 穿透位）。
 * - py 用 powershell Get-Process 探活；TS 按校准条款 5 轻量档 process.kill(pid, 0)
 *   （powershell 冷启动 ≈1s 自带宽限，故 TS 侧以 ≤3s 有界轮询等价）。
 * - test_check_handler_buffers_once_… / test_long_check_acks_… 的 client 集成面
 *   （handle_instr ack 帧 / 终态重进缓冲）归 W4 client 波；此处按 CheckRunner.start 面
 *   对等移植其检查点；test_check_finished_retransmits_… 已由 W4 收账（见文末：makeClient +
 *   flushChecks/resolveReportAck 私有面直调，对等 py client._flush_checks/_resolve_report_ack）。
 */

import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import type { CheckFinishedData, CheckRunData } from '@coagentia/contracts-ts';

import { AsyncEvent, sleep, withTimeout } from '../src/aio.ts';
import { TelemetryBuffer } from '../src/buffer.ts';
import {
  CheckCancelledError,
  CheckRunner,
  runCheckProcess,
} from '../src/checks.ts';
import type { CheckProcessResult } from '../src/checks.ts';
import { DataPaths } from '../src/paths.ts';
import { newUlid } from '../src/util.ts';
import { RecordingTransport, makeClient, until } from './helpers.ts';

let tmp: string;

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'coagentia-checks-'));
});

afterEach(() => {
  fs.rmSync(tmp, { recursive: true, force: true });
});

function makeRun(repo: string, command = 'test'): CheckRunData {
  return {
    run_id: newUlid(),
    node_id: newUlid(),
    project_id: newUlid(),
    repo_path: repo,
    command,
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

describe('checks（契约 D §5.3/§7）', () => {
  it('真命令在 repo 主工作区执行且 UTF-8 尾有界（test_real_check_runs_in_repo_and_caps_utf8_tail）', async () => {
    const repo = path.join(tmp, '中文 repo');
    fs.mkdirSync(repo);
    const script =
      "const p=require('node:path');console.log(p.basename(process.cwd()));console.log('x'.repeat(5000));console.log('中文尾');";
    const command = `"${process.execPath}" -e "${script}"`;
    const result = await runCheckProcess(makeRun(repo, command), 10);
    expect(result.exitCode).toBe(0);
    expect(result.outputTail).toContain('中文尾');
    expect(Buffer.byteLength(result.outputTail, 'utf-8')).toBeLessThanOrEqual(4096);
    expect(result.outputTail).not.toContain('中文 repo'); // 早期输出已被有界尾截掉。
  });

  it.runIf(process.platform === 'win32')(
    '超时 taskkill /F /T 杀整棵 Windows 进程树（test_timeout_kills_windows_process_tree）',
    async () => {
      const command = `"${process.execPath}" -e "setTimeout(() => {}, 30000)"`;
      const started = Date.now();
      const result = await runCheckProcess(makeRun(tmp, command), 0.1);
      expect(result.exitCode).toBe(124);
      expect(result.outputTail).toContain('check timeout');
      expect(Date.now() - started).toBeLessThan(8000);
    },
  );

  it.runIf(process.platform === 'win32')(
    '取消杀 Windows 子进程（test_cancellation_kills_windows_child_process）',
    async () => {
      const script =
        "require('node:fs').writeFileSync('child.pid', String(process.pid));setTimeout(() => {}, 30000);";
      const command = `"${process.execPath}" -e "${script}"`;
      const controller = new AbortController();
      const check = runCheckProcess(makeRun(tmp, command), 60, controller.signal);
      check.catch(() => {}); // 预挂接消费，避免断言前 rejection 未处理告警。
      try {
        const pidPath = path.join(tmp, 'child.pid');
        await until(() => fs.existsSync(pidPath), 5000);
        const childPid = Number(fs.readFileSync(pidPath, 'utf-8'));
        expect(Number.isInteger(childPid)).toBe(true);

        controller.abort();
        await expect(withTimeout(check, 8000)).rejects.toBeInstanceOf(CheckCancelledError);
        // py 侧 powershell Get-Process 探活；此处轻量档 process.kill(pid, 0)（校准条款 5）。
        await until(() => !isAlive(childPid), 3000);
        expect(isAlive(childPid)).toBe(false);
      } finally {
        controller.abort(); // 幂等兜底：确保子进程树收尾后再清理 tmp。
        await check.catch(() => {});
      }
    },
  );

  it('CheckRunner 成功/失败与 run_id 自然键（test_check_runner_success_failure_and_natural_key）', async () => {
    const calls: string[] = [];
    const fake = async (data: CheckRunData, timeout: number): Promise<CheckProcessResult> => {
      calls.push(data.run_id);
      return { exitCode: data.command === 'ok' ? 0 : 7, outputTail: `tail:${timeout}` };
    };
    const runner = new CheckRunner({ runner: fake, timeoutSec: 12 });
    const ok = makeRun(tmp, 'ok');
    const [changed1, first] = await runner.run(ok);
    expect(changed1).toBe(true);
    expect(first.status).toBe('success');
    expect(first.exit_code).toBe(0);
    const [changed2, replay] = await runner.run(ok);
    expect(changed2).toBe(false);
    expect(replay).toEqual(first);
    const failed = makeRun(tmp, 'bad');
    const [changed3, failure] = await runner.run(failed);
    expect(changed3).toBe(true);
    expect(failure.status).toBe('failed');
    expect(failure.exit_code).toBe(7);
    expect(calls).toEqual([ok.run_id, failed.run_id]);
  });

  it('start 终态经回调恰一次，重发不再执行（test_check_handler_buffers_once_and_replays_without_execution 的 CheckRunner 面）', async () => {
    // py 原例经 client.handle_instr 断言 ack done/noop 与 TelemetryBuffer 落盘重进；该集成面
    // 归 W1 buffer / W4 client 波，此处对等其 CheckRunner 检查点：恰一次执行 + 终态回放。
    const finishedCb: CheckFinishedData[] = [];
    let calls = 0;
    const fake = async (): Promise<CheckProcessResult> => {
      calls += 1;
      return { exitCode: 0, outputTail: 'all green' };
    };
    const runner = new CheckRunner({ runner: fake });
    const run = makeRun(tmp, 'ok');
    const onFinished = async (d: CheckFinishedData): Promise<void> => {
      finishedCb.push(d);
    };

    const [changed1, known1] = runner.start(run, onFinished);
    expect(changed1).toBe(true);
    expect(known1).toBeNull();
    await until(() => finishedCb.length === 1);
    expect(finishedCb[0]!.run_id).toBe(run.run_id);

    await sleep(0); // 让执行 Promise 的 done 回调清掉 running 记忆（对齐 py await asyncio.sleep(0)）。
    const [changed2, known2] = runner.start(run, onFinished);
    expect(changed2).toBe(false); // 终态已知 → 自然键 noop（client 据此回 ack noop）。
    expect(known2).not.toBeNull();
    expect(known2!.run_id).toBe(run.run_id);
    expect(calls).toBe(1);
    expect(finishedCb).toHaveLength(1); // 不再执行、不再回调（终态重进缓冲由 client 面负责）。

    const [changed3, known3] = runner.start(run, onFinished); // py 第三段：ack 清缓冲后重发仍 noop。
    expect(changed3).toBe(false);
    expect(known3).toEqual(known2);
    expect(calls).toBe(1);
  });

  it('长命令 start 立即返回可 ack，在跑重复为 noop（test_long_check_acks_immediately_and_duplicate_is_running_noop 的 CheckRunner 面）', async () => {
    // py 原例断言 transport ack done/noop（client 面归 W4）；此处对等 CheckRunner 检查点。
    const entered = new AsyncEvent();
    const release = new AsyncEvent();
    const finishedCb: CheckFinishedData[] = [];
    let calls = 0;
    const slow = async (): Promise<CheckProcessResult> => {
      calls += 1;
      entered.set();
      await release.wait();
      return { exitCode: 0, outputTail: 'done' };
    };
    const runner = new CheckRunner({ runner: slow });
    const run = makeRun(tmp, 'slow');
    const onFinished = async (d: CheckFinishedData): Promise<void> => {
      finishedCb.push(d);
    };

    const [changed1, known1] = runner.start(run, onFinished);
    expect(changed1).toBe(true); // start 同步返回 → instr 可立即 ack。
    expect(known1).toBeNull();
    await withTimeout(entered.wait(), 1000);
    expect(finishedCb).toHaveLength(0); // 在跑未终 → 缓冲尚无终态（py: not buffer.has_checks()）。

    const [changed2, known2] = runner.start(run, onFinished);
    expect(changed2).toBe(false); // 在跑重复 → (false, null)，client 据此 ack noop。
    expect(known2).toBeNull();
    expect(calls).toBe(1);
    release.set();
    await until(() => finishedCb.length === 1);
    expect(finishedCb[0]!.output_tail).toBe('done');
  });

  it('终态缓冲落盘与按 run_id ack（test_check_finished_buffer_persists_and_acks_by_run_id）', () => {
    const paths = new DataPaths(path.join(tmp, 'root'));
    paths.ensureDirs();
    const first: CheckFinishedData = {
      run_id: newUlid(),
      node_id: newUlid(),
      status: 'success',
      exit_code: 0,
      output_tail: 'ok',
    };
    const second: CheckFinishedData = {
      run_id: newUlid(),
      node_id: newUlid(),
      status: 'failed',
      exit_code: 1,
      output_tail: 'bad',
    };
    const buffer = new TelemetryBuffer(paths);
    buffer.appendCheck(first);
    buffer.appendCheck(second);
    buffer.appendCheck(first); // 同 run_id 重复 append 幂等。
    const restarted = new TelemetryBuffer(paths);
    expect(restarted.peekChecks(10).map((item) => item.run_id)).toEqual([
      first.run_id,
      second.run_id,
    ]);
    restarted.ackChecks([first.run_id]);
    expect(restarted.findCheck(first.run_id)).toBeNull();
    expect(restarted.findCheck(second.run_id)).toEqual(second);
  });

  it('check.finished 未 ack 前同 run 重传（test_check_finished_retransmits_same_run_until_ack）', async () => {
    // W4 收账：被测主体 = client 的 flushChecks 重传闭环。py 直调私有 _flush_checks /
    // _resolve_report_ack；TS 同名 private 且无公开驱动面 → (client as ...) 结构断言直调
    // （任务书授权的最后手段；serve+AutoAck 需真握手，直调更贴 py 断言面）。
    const transport = new RecordingTransport();
    const { client } = makeClient(tmp, { transport, ackTimeout: 0.03 });
    const finished: CheckFinishedData = {
      run_id: newUlid(),
      node_id: newUlid(),
      status: 'success',
      exit_code: 0,
      output_tail: 'ok',
    };
    client.buffer.appendCheck(finished);

    // 第一次 flush：无 ack → 超时 → 缓冲保留（同 run 待重传）。
    await (client as unknown as { flushChecks(): Promise<void> }).flushChecks();
    expect(client.buffer.findCheck(finished.run_id)).toEqual(finished);
    const first = transport.reports('check.finished').at(-1)!;
    expect((first['data'] as CheckFinishedData).run_id).toBe(finished.run_id);

    // 第二次 flush：原样重发（同 run 不虚增）→ 并发解析 ack → 按 run_id 清缓冲。
    const flush = (client as unknown as { flushChecks(): Promise<void> }).flushChecks();
    await sleep(10);
    const second = transport.reports('check.finished').at(-1)!;
    expect(second['data']).toEqual(first['data']);
    (client as unknown as { resolveReportAck(f: Record<string, unknown>): void }).resolveReportAck({
      kind: 'ack',
      ref: second['frame_id'],
      result: 'done',
    });
    await withTimeout(flush, 2000);
    expect(client.buffer.findCheck(finished.run_id)).toBeNull();
  });
});
