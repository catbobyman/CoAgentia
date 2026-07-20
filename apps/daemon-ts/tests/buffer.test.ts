/**
 * 遥测缓冲（契约 D §7 缓冲纪律 / §9.1）：落盘跨重启、环形溢出、重传不虚增。
 * 对等基准 = apps/daemon tests/test_buffer.py（9 用例逐条对应；it 名后缀标注 py 用例名）。
 *
 * 行为差异登记：py 测试 monkeypatch buffer_module.json.dumps / os.fsync / os.replace；
 * TS 侧 node:fs 命名空间冻结不可 spy → 补丁点 = src/buffer.ts 导出的 _io 接缝（语义同款）。
 */

import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type {
  CheckFinishedData,
  DiagnosticEventIn,
  TokenUsageEventIn,
} from '@coagentia/contracts-ts';

import { TelemetryBuffer, _io } from '../src/buffer.ts';
import { DataPaths } from '../src/paths.ts';
import { newUlid, nowIso } from '../src/util.ts';
import { usageEvent } from './helpers.ts';

let tmp: string;

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'coagentia-buffer-'));
});

afterEach(() => {
  vi.restoreAllMocks();
  fs.rmSync(tmp, { recursive: true, force: true });
});

/** 对等 py _buf(tmp_path, **kw)。 */
function makeBuf(opts: { diagnosticsMax?: number; usageMax?: number } = {}): {
  buf: TelemetryBuffer;
  paths: DataPaths;
} {
  const p = new DataPaths(path.join(tmp, 'root'));
  p.ensureDirs();
  return { buf: new TelemetryBuffer(p, opts), paths: p };
}

/** 对等 py _usage()。 */
function makeUsage(agent = '01K5AGENT0000000000000000A'): TokenUsageEventIn {
  return usageEvent(agent) as unknown as TokenUsageEventIn;
}

/** 对等 py _check(output)。 */
function makeCheck(output: string): CheckFinishedData {
  return {
    run_id: newUlid(),
    node_id: newUlid(),
    status: 'success',
    exit_code: 0,
    output_tail: output,
  };
}

function makeDiag(payload: Record<string, unknown>): DiagnosticEventIn {
  return { type: 'agent.command', payload, at: nowIso() };
}

describe('TelemetryBuffer', () => {
  it('usage 追加/peek/按 id ack（test_usage_append_peek_ack）', () => {
    const { buf } = makeBuf();
    const ids: string[] = [];
    for (let i = 0; i < 5; i += 1) {
      const e = makeUsage();
      ids.push(e.id);
      buf.appendUsage(e);
    }
    expect(buf.counts().usage).toBe(5);
    const peeked = buf.peekUsage(3);
    expect(peeked.map((e) => e.id)).toEqual(ids.slice(0, 3));
    buf.ackUsage([ids[0], ids[2]]);
    expect(buf.counts().usage).toBe(3);
  });

  it('usage 落盘跨重启重载（test_usage_persists_across_restart）', () => {
    const { buf, paths } = makeBuf();
    const e = makeUsage();
    buf.appendUsage(e);
    // 新实例从 jsonl 重载（模拟 daemon 重启）。
    const buf2 = new TelemetryBuffer(paths);
    expect(buf2.counts().usage).toBe(1);
    expect(buf2.peekUsage(1)[0].id).toBe(e.id);
  });

  it('diagnostics ack 按发送顺序移除（test_diagnostics_ack_removes_in_order）', () => {
    const { buf } = makeBuf();
    for (let i = 0; i < 4; i += 1) {
      buf.appendDiagnostic(makeDiag({ i }));
    }
    expect(buf.counts().diagnostics).toBe(4);
    buf.ackDiagnostics(2);
    const remaining = buf.peekDiagnostics(10);
    expect(remaining.map((e) => (e.payload as { i: number }).i)).toEqual([2, 3]);
  });

  it('usage 溢出丢最旧并留 overflow 诊断（test_usage_overflow_drops_oldest_and_marks）', () => {
    const { buf } = makeBuf({ usageMax: 3 });
    const kept: string[] = [];
    for (let i = 0; i < 5; i += 1) {
      const e = makeUsage();
      buf.appendUsage(e);
      kept.push(e.id);
    }
    // 上限 3：最旧 2 条被丢弃。
    expect(buf.counts().usage).toBe(3);
    expect(buf.peekUsage(10).map((e) => e.id)).toEqual(kept.slice(2));
    // 溢出留痕：daemon.buffer_overflow 诊断入 diagnostics 缓冲。
    const diags = buf.peekDiagnostics(10);
    expect(diags.some((d) => d.type === 'daemon.buffer_overflow')).toBe(true);
  });

  it('diagnostics 溢出留痕（test_diagnostics_overflow_marks）', () => {
    const { buf } = makeBuf({ diagnosticsMax: 3 });
    for (let i = 0; i < 5; i += 1) {
      buf.appendDiagnostic(makeDiag({ i }));
    }
    expect(buf.counts().diagnostics).toBe(3);
    expect(buf.peekDiagnostics(10).some((d) => d.type === 'daemon.buffer_overflow')).toBe(true);
  });

  it('重传不虚增：未 ack peek 两次同批 ULID（test_retransmit_reuses_same_ulids）', () => {
    // §11 用例 5 daemon 侧根基：peek 两次返回同一批 ULID（重传不虚增）。
    const { buf } = makeBuf();
    const ids: string[] = [];
    for (let i = 0; i < 10; i += 1) {
      const e = makeUsage();
      ids.push(e.id);
      buf.appendUsage(e);
    }
    const first = buf.peekUsage(500).map((e) => e.id);
    const second = buf.peekUsage(500).map((e) => e.id); // 未 ack → 同批重发
    expect(first).toEqual(second);
    expect(second).toEqual(ids);
  });

  it('三缓冲共用同目录临时文件 fsync 原子重写（test_all_buffers_fsync_same_directory_temp_before_replace）', () => {
    // diagnostics/usage/check.finished 共用同一原子重写路径。
    const { buf } = makeBuf();
    const replaced: string[] = [];
    let fsyncCalls = 0;
    vi.spyOn(_io, 'replace').mockImplementation((source, target) => {
      expect(path.dirname(source)).toBe(path.dirname(target));
      expect(path.basename(source).startsWith(`.${path.basename(target)}.`)).toBe(true);
      replaced.push(path.basename(target));
      fs.renameSync(source, target);
    });
    vi.spyOn(_io, 'fsync').mockImplementation((fd) => {
      fsyncCalls += 1;
      fs.fsyncSync(fd);
    });
    buf.appendDiagnostic(makeDiag({ step: 1 }));
    buf.appendUsage(makeUsage());
    buf.appendCheck(makeCheck('green'));

    expect(replaced).toEqual(['diagnostics.jsonl', 'usage.jsonl', 'check-finished.jsonl']);
    expect(fsyncCalls).toBe(3);
  });

  it('临时文件断裂写不污染正式文件，重启可恢复（test_torn_check_rewrite_keeps_old_file_and_restart_can_recover）', () => {
    // 第二行序列化断裂（批量聚合下临时文件尚无已落行），正式 check-finished.jsonl 仍是旧完整版。
    const { buf, paths } = makeBuf();
    const first = makeCheck('old-complete');
    const second = makeCheck('new-after-recovery');
    buf.appendCheck(first);
    const official = path.join(paths.bufferDir, 'check-finished.jsonl');
    const oldBytes = fs.readFileSync(official);
    let dumpsCalls = 0;
    const dumpsSpy = vi.spyOn(_io, 'dumps').mockImplementation((row) => {
      dumpsCalls += 1;
      if (dumpsCalls === 2) {
        throw new Error('simulated torn JSONL write');
      }
      return JSON.stringify(row);
    });

    expect(() => buf.appendCheck(second)).toThrow(/torn JSONL/);

    expect(dumpsCalls).toBe(2); // dumps 逐行接缝不变：第二行序列化即断。
    expect(fs.readFileSync(official)).toEqual(oldBytes);
    const restarted = new TelemetryBuffer(paths);
    expect(restarted.peekChecks(10)).toEqual([first]);

    dumpsSpy.mockRestore();
    restarted.appendCheck(second);
    const recovered = new TelemetryBuffer(paths);
    expect(recovered.peekChecks(10)).toEqual([first, second]);
  });

  it('replace 失败保留旧文件，后续写恢复（test_replace_failure_keeps_old_check_file_and_later_write_recovers）', () => {
    const { buf, paths } = makeBuf();
    const first = makeCheck('old-complete');
    const second = makeCheck('new-after-replace');
    buf.appendCheck(first);
    const official = path.join(paths.bufferDir, 'check-finished.jsonl');
    const oldBytes = fs.readFileSync(official);
    const replaceSpy = vi.spyOn(_io, 'replace').mockImplementation(() => {
      throw new Error('simulated replace failure');
    });

    expect(() => buf.appendCheck(second)).toThrow(/replace failure/);

    expect(fs.readFileSync(official)).toEqual(oldBytes);
    const restarted = new TelemetryBuffer(paths);
    expect(restarted.peekChecks(10)).toEqual([first]);

    replaceSpy.mockRestore();
    restarted.appendCheck(second);
    const recovered = new TelemetryBuffer(paths);
    expect(recovered.peekChecks(10)).toEqual([first, second]);
  });
});
