/**
 * M7b K4 daemon 部署执行器：流式日志 + chunk_seq 单调 + url 提取 + 超时杀树 + 自然键幂等 +
 * deploy-log/deploy-finished 缓冲（对等基准 = py test_deploy.py，体例同 checks.test.ts）。
 *
 * py→TS 移植登记（非行为改进）：
 * - py 用 sys.executable(python) 起真子进程；TS 用 process.execPath(node) 等价替换（同为
 *   「打印行/URL/长睡眠」探针，避免测试依赖 python 解释器在位，与 checks.test.ts 同款登记）。
 * - 流式三触发：py wait_for(readline, 0.5) 超时即 flush；TS 用行事件 + 静默计时器重构
 *   （每行重置 0.5s 计时、满 20 行立即 flush、EOF flush 残批），三触发语义逐条保住——
 *   文末两条「TS 侧补充」用真子进程分别钉死静默触发与满批触发（py 无对应例）。
 * - client 集成面（handle_instr ack 帧 / TelemetryBuffer 接线 / _flush 重传）归 W4 client 波：
 *   test_deploy_handler_acks_done_… 按 DeployRunner 面对等移植其检查点；
 *   test_deploy_handler_replays_buffered_… / 两条 retransmit 已由 W4 收账（见文中：makeClient +
 *   handleInstr / flushDeployLogs / flushDeployFinished，flush 面为 private 无公开驱动 →
 *   (client as ...) 结构断言直调，对等 py client._flush_* / _resolve_report_ack 直调）。
 */

import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import type {
  DeployFinishedData,
  DeployLogReportData,
  DeployRunData,
} from '@coagentia/contracts-ts';

import { AsyncEvent, sleep, withTimeout } from '../src/aio.ts';
import { TelemetryBuffer } from '../src/buffer.ts';
import { DeployRunner, runDeployProcess } from '../src/deploy.ts';
import type { DeployProcessResult, DeployProcessRunner, LogBatchCallback } from '../src/deploy.ts';
import { DataPaths } from '../src/paths.ts';
import { newUlid } from '../src/util.ts';
import { RecordingTransport, instr, makeClient, until } from './helpers.ts';

let tmp: string;

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'coagentia-deploy-'));
});

afterEach(() => {
  fs.rmSync(tmp, { recursive: true, force: true });
});

function makeRun(repo: string, command = 'deploy'): DeployRunData {
  return {
    deployment_id: newUlid(),
    repo_path: repo,
    command,
    branch: 'main',
    commit_hash: 'abc123',
  };
}

const noopLog: LogBatchCallback = async () => {};

async function noopReport(_: unknown): Promise<void> {}

describe('deploy（契约 D §5.3/§7）', () => {
  // ---------------------------------------------------------------- runDeployProcess（真子进程）

  it('真部署流式回日志并提取最后一个 URL（test_real_deploy_streams_logs_and_extracts_last_url）', async () => {
    const repo = path.join(tmp, 'repo');
    fs.mkdirSync(repo);
    const script =
      "console.log('building');" +
      "console.log('deployed to https://old.example.com');" +
      "console.log('final https://final.example.com/app')";
    const command = `"${process.execPath}" -e "${script}"`;
    const batches: string[][] = [];
    const onLog: LogBatchCallback = async (lines) => {
      batches.push(lines);
    };
    const result = await runDeployProcess(makeRun(repo, command), onLog, 10);
    expect(result.exitCode).toBe(0);
    expect(result.url).toBe('https://final.example.com/app'); // 取最后一个 URL
    const allLines = batches.flat();
    expect(allLines.some((line) => line.includes('building'))).toBe(true);
  });

  it('repo_path 不存在 → 127（test_deploy_nonexistent_repo_fails_127）', async () => {
    const data: DeployRunData = {
      deployment_id: newUlid(),
      repo_path: path.join(tmp, 'nope'),
      command: 'echo x',
      branch: 'main',
    };
    const result = await runDeployProcess(data, noopLog, 5);
    expect(result.exitCode).toBe(127);
    expect(result.url).toBeNull();
  });

  it('失败部署不吐 url（test_failed_deploy_yields_no_url）', async () => {
    const repo = path.join(tmp, 'repo');
    fs.mkdirSync(repo);
    const script = "console.log('https://should-not-leak.example.com');process.exit(3)";
    const command = `"${process.execPath}" -e "${script}"`;
    const result = await runDeployProcess(makeRun(repo, command), noopLog, 10);
    expect(result.exitCode).toBe(3);
    expect(result.url).toBeNull(); // 仅 success 吐 url
  });

  it.runIf(process.platform === 'win32')(
    '超时 taskkill /F /T 杀整棵进程树且 exit_code=null（test_deploy_timeout_kills_process_tree）',
    async () => {
      const command = `"${process.execPath}" -e "setTimeout(() => {}, 30000)"`;
      const started = Date.now();
      const result = await runDeployProcess(makeRun(tmp, command), noopLog, 0.2);
      expect(result.exitCode).toBeNull(); // 超时 = exit_code null
      expect(result.url).toBeNull();
      expect(Date.now() - started).toBeLessThan(8000);
    },
  );

  // -------------------------------------------------------- DeployRunner（自然键 + chunk_seq）

  it('Runner 流式批携单调 chunk_seq 并报终态（test_runner_streams_monotonic_chunk_seq_and_finished）', async () => {
    const fake: DeployProcessRunner = async (_data, onLog, _timeoutSec) => {
      await onLog(['line-0', 'line-1']);
      await onLog(['line-2']);
      return { exitCode: 0, url: 'https://ok.example.com' };
    };
    const logs: DeployLogReportData[] = [];
    const finished: DeployFinishedData[] = [];
    const onLog = async (data: DeployLogReportData): Promise<void> => {
      logs.push(data);
    };
    const onFinished = async (data: DeployFinishedData): Promise<void> => {
      finished.push(data);
    };

    const runner = new DeployRunner({ runner: fake });
    const data = makeRun(tmp);
    const [started, known] = runner.start(data, onLog, onFinished);
    expect(started).toBe(true);
    expect(known).toBeNull();
    // 等后台 execute 自然跑完（waitClosed 会取消，不能用；对齐 py 50×0.01 轮询）。
    await until(() => finished.length > 0);
    expect(logs.map((d) => d.chunk_seq)).toEqual([0, 1]); // per-deployment 单调递增
    expect(logs[0]!.lines).toEqual(['line-0', 'line-1']);
    expect(finished[0]!.status).toBe('success');
    expect(finished[0]!.url).toBe('https://ok.example.com');
  });

  it('同 deployment_id 在跑重发 → noop（test_runner_natural_key_noop_when_running）', async () => {
    const entered = new AsyncEvent();
    const release = new AsyncEvent();
    let calls = 0;
    const slow: DeployProcessRunner = async () => {
      calls += 1;
      entered.set();
      await release.wait();
      return { exitCode: 0, url: null };
    };

    const runner = new DeployRunner({ runner: slow });
    const data = makeRun(tmp);
    const [started1] = runner.start(data, noopReport, noopReport);
    expect(started1).toBe(true);
    await withTimeout(entered.wait(), 1000);
    const [started2, known] = runner.start(data, noopReport, noopReport); // 同 deployment_id 在跑 → noop
    expect(started2).toBe(false);
    expect(known).toBeNull();
    expect(calls).toBe(1);
    release.set();
    await runner.waitClosed();
  });

  it('终态重发返回已知终态供重报（test_runner_terminal_returns_known_on_replay）', async () => {
    const fake: DeployProcessRunner = async () => ({ exitCode: 1, url: null });
    const runner = new DeployRunner({ runner: fake });
    const data = makeRun(tmp);
    const finished: DeployFinishedData[] = [];
    const onFinished = async (d: DeployFinishedData): Promise<void> => {
      finished.push(d);
    };

    runner.start(data, noopReport, onFinished);
    await until(() => finished.length > 0); // 等 execute 自然跑完（finished 记入内存终态）
    await sleep(0); // 让执行 Promise 的 finally 清掉 running 记忆（对齐 py add_done_callback 时序）。
    // 终态后重发（同 deployment_id）→ noop + 返回已知终态供重报。
    const [started, known] = runner.start(data, noopReport, onFinished);
    expect(started).toBe(false);
    expect(known).not.toBeNull();
    expect(known!.status).toBe('failed');
  });

  // ---------------------------------------------------------------- handler + buffer 接线

  it('deploy.run 起后台即可 ack，终态/日志经回调交付（test_deploy_handler_acks_done_and_buffers_finished 的 DeployRunner 面）', async () => {
    // py 原例经 client.handle_instr 断言 transport ack done 与 TelemetryBuffer 落盘
    // （has_deploy_finished / peek / has_deploy_logs）；client 接线归 W4，此处对等 DeployRunner
    // 检查点：start 同步返回 [true, null]（=instr 可立即 ack done）+ 日志与终态经回调按序交付。
    const fake: DeployProcessRunner = async (_data, onLog) => {
      await onLog(['deploying...']);
      return { exitCode: 0, url: 'https://x.example.com' };
    };
    const logs: DeployLogReportData[] = [];
    const finished: DeployFinishedData[] = [];
    const runner = new DeployRunner({ runner: fake });
    const data = makeRun(tmp);
    const [started, known] = runner.start(
      data,
      async (d) => {
        logs.push(d);
      },
      async (d) => {
        finished.push(d);
      },
    );
    expect(started).toBe(true); // 起后台即返回 → instr 可立即 ack done
    expect(known).toBeNull();

    await until(() => finished.length > 0);
    expect(finished[0]!.deployment_id).toBe(data.deployment_id);
    expect(finished[0]!.status).toBe('success');
    expect(logs.length).toBeGreaterThan(0); // = py buffer.has_deploy_logs 对应检查点
  });

  it('已终态缓冲重发 deploy.run → ack noop 不重跑（test_deploy_handler_replays_buffered_finished_without_rerun）', async () => {
    // W4 收账：被测主体 = client.handleInstr 先查 buffer.findDeployFinished → 重报终态、
    // 不重跑（副作用不可重放，铁律 3）。py client.deploys = DeployRunner(...) 猴补；TS
    // deploys 为 readonly 面 → 结构剥离后赋值（等价 monkeypatch，任务书授权的最后手段）。
    const transport = new RecordingTransport();
    const { client } = makeClient(tmp, { transport });
    const data = makeRun(tmp);
    // 预置已终态缓冲（模拟先前跑完未 ack）：重发 deploy.run → 重报终态、不重跑。
    client.buffer.appendDeployFinished({
      deployment_id: data.deployment_id,
      status: 'success',
      exit_code: 0,
    });
    let calls = 0;
    const fake: DeployProcessRunner = async () => {
      calls += 1;
      return { exitCode: 0, url: null };
    };
    (client as unknown as { deploys: DeployRunner }).deploys = new DeployRunner({ runner: fake });
    await client.handleInstr(instr('deploy.run', data as unknown as Record<string, unknown>));
    expect(transport.lastAck()['result']).toBe('noop'); // 已终态缓冲 → noop
    expect(calls).toBe(0); // 未重跑（副作用不可重放）
  });

  // ---------------------------------------------------------------- buffer 落盘 / 去重 / 重传

  it('deploy.log 缓冲 (deployment_id, chunk_seq) 去重与落盘（test_deploy_log_buffer_dedup_and_persist）', () => {
    const paths = new DataPaths(path.join(tmp, 'root'));
    paths.ensureDirs();
    const did = newUlid();
    const buffer = new TelemetryBuffer(paths);
    buffer.appendDeployLog({ deployment_id: did, chunk_seq: 0, lines: ['a'] });
    buffer.appendDeployLog({ deployment_id: did, chunk_seq: 1, lines: ['b'] });
    buffer.appendDeployLog({ deployment_id: did, chunk_seq: 0, lines: ['a2'] });
    // (deployment_id, chunk_seq) 去重：chunk_seq=0 被替换，非新增。
    const restarted = new TelemetryBuffer(paths);
    const logs = restarted.peekDeployLogs(10);
    expect(logs.map((line) => line.chunk_seq)).toEqual([0, 1]);
    expect(logs[0]!.lines).toEqual(['a2']);
    restarted.ackDeployLog(did, 0);
    expect(restarted.peekDeployLogs(10).map((line) => line.chunk_seq)).toEqual([1]);
  });

  it('deploy.finished 缓冲按 deployment_id 去重与 find（test_deploy_finished_buffer_dedup_by_deployment_and_find）', () => {
    const paths = new DataPaths(path.join(tmp, 'root'));
    paths.ensureDirs();
    const did = newUlid();
    const buffer = new TelemetryBuffer(paths);
    buffer.appendDeployFinished({ deployment_id: did, status: 'failed', exit_code: 1 });
    buffer.appendDeployFinished({ deployment_id: did, status: 'success', exit_code: 0 });
    const restarted = new TelemetryBuffer(paths);
    expect(restarted.peekDeployFinished(10)).toHaveLength(1); // 同 deployment_id 去重
    expect(restarted.findDeployFinished(did)!.status).toBe('success');
    restarted.ackDeployFinished([did]);
    expect(restarted.findDeployFinished(did)).toBeNull();
  });

  it('deploy.log 未 ack 前原样重传（test_deploy_log_retransmits_until_ack）', async () => {
    // W4 收账：被测主体 = client 的 flushDeployLogs + resolveReportAck 重传闭环（private 无
    // 公开驱动面 → (client as ...) 结构断言直调，对等 py _flush_deploy_logs 直调）。
    const transport = new RecordingTransport();
    const { client } = makeClient(tmp, { transport, ackTimeout: 0.03 });
    const did = newUlid();
    client.buffer.appendDeployLog({ deployment_id: did, chunk_seq: 0, lines: ['x'] });
    await (client as unknown as { flushDeployLogs(): Promise<void> }).flushDeployLogs(); // ack 超时 → 保留待重传
    expect(client.buffer.hasDeployLogs()).toBe(true);
    const first = transport.reports('deploy.log').at(-1)!;
    expect((first['data'] as DeployLogReportData).deployment_id).toBe(did);

    const flush = (client as unknown as { flushDeployLogs(): Promise<void> }).flushDeployLogs();
    await sleep(10);
    const second = transport.reports('deploy.log').at(-1)!; // 原样重发
    expect(second['data']).toEqual(first['data']);
    (client as unknown as { resolveReportAck(f: Record<string, unknown>): void }).resolveReportAck({
      kind: 'ack',
      ref: second['frame_id'],
      result: 'done',
    });
    await withTimeout(flush, 2000);
    expect(client.buffer.hasDeployLogs()).toBe(false);
  });

  it('deploy.finished 未 ack 前原样重传（test_deploy_finished_retransmits_until_ack）', async () => {
    // W4 收账：被测主体 = client 的 flushDeployFinished（驱动方式同上条，对等 py
    // _flush_deploy_finished 直调）。
    const transport = new RecordingTransport();
    const { client } = makeClient(tmp, { transport, ackTimeout: 0.03 });
    const did = newUlid();
    client.buffer.appendDeployFinished({ deployment_id: did, status: 'success', exit_code: 0 });
    await (client as unknown as { flushDeployFinished(): Promise<void> }).flushDeployFinished();
    expect(client.buffer.hasDeployFinished()).toBe(true);
    const flush = (client as unknown as { flushDeployFinished(): Promise<void> }).flushDeployFinished();
    await sleep(10);
    const frame = transport.reports('deploy.finished').at(-1)!;
    (client as unknown as { resolveReportAck(f: Record<string, unknown>): void }).resolveReportAck({
      kind: 'ack',
      ref: frame['frame_id'],
      result: 'done',
    });
    await withTimeout(flush, 2000);
    expect(client.buffer.hasDeployFinished()).toBe(false);
  });

  // ------------------------------------------------- TS 侧补充（无 py 对应例；验三触发重构等价）

  it('TS 侧补充：0.5s 静默触发 flush（py wait_for(readline, 0.5) 的静默 flush 等价验证）', async () => {
    const repo = path.join(tmp, 'repo');
    fs.mkdirSync(repo);
    // 首行后静默 1.2s（> 0.5s 静默窗）再出次行：首行必须在静默窗到期时单独成批先行送达。
    const script =
      "console.log('first');" +
      "setTimeout(() => { console.log('second https://late.example.com'); }, 1200)";
    const command = `"${process.execPath}" -e "${script}"`;
    const batches: string[][] = [];
    const onLog: LogBatchCallback = async (lines) => {
      batches.push(lines);
    };
    const result = await runDeployProcess(makeRun(repo, command), onLog, 10);
    expect(result.exitCode).toBe(0);
    expect(result.url).toBe('https://late.example.com');
    expect(batches[0]).toEqual(['first']); // 静默触发：不等 EOF/满批即先行 flush
    expect(batches.flat()).toEqual(['first', 'second https://late.example.com']);
  });

  it('TS 侧补充：单行跨多 chunk（分段写无换行）拼接为一行（LineSplitter parts 聚合等价验证）', async () => {
    const repo = path.join(tmp, 'repo');
    fs.mkdirSync(repo);
    // 先写无换行前段，150ms 后补后段+换行：两次 pipe 到达 → LineSplitter 跨 chunk 聚合为一行
    // （聚合重构后完行才 concat 一次；URL 必须在拼接后的整行上才可见）。
    const script =
      "process.stdout.write('part1-');" +
      "setTimeout(() => { process.stdout.write('part2 https://joined.example.com\\n'); }, 150)";
    const command = `"${process.execPath}" -e "${script}"`;
    const batches: string[][] = [];
    const onLog: LogBatchCallback = async (lines) => {
      batches.push(lines);
    };
    const result = await runDeployProcess(makeRun(repo, command), onLog, 10);
    expect(result.exitCode).toBe(0);
    expect(result.url).toBe('https://joined.example.com'); // 整行拼好后才匹配得到
    expect(batches.flat()).toEqual(['part1-part2 https://joined.example.com']);
  });

  it('TS 侧补充：满 20 行立即成批（py len(buffer) >= 20 的满批 flush 等价验证）', async () => {
    const repo = path.join(tmp, 'repo');
    fs.mkdirSync(repo);
    const script = "for (let i = 0; i < 45; i++) console.log('line-' + i)";
    const command = `"${process.execPath}" -e "${script}"`;
    const batches: string[][] = [];
    const onLog: LogBatchCallback = async (lines) => {
      batches.push(lines);
    };
    const result = await runDeployProcess(makeRun(repo, command), onLog, 10);
    expect(result.exitCode).toBe(0);
    expect(batches.map((batch) => batch.length)).toEqual([20, 20, 5]); // 满批×2 + EOF 残批
    expect(batches.flat()).toEqual(
      Array.from({ length: 45 }, (_unused, i) => `line-${i}`),
    );
  });
});
