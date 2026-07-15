// R-14（M8a L6）部署日志累积纯逻辑：chunk_seq 单调去重 + 历史首页前 pending 缓冲 + flush 拼接。
import { describe, expect, it } from 'vitest';

import {
  EMPTY_DEPLOY_LOG,
  appendDeployLogChunk,
  flushPendingDeployLog,
  mergeDeployLogPage,
} from './deployLog';

describe('appendDeployLogChunk（live 块并入）', () => {
  it('历史已并入：按 seq 追加，seq 单调去重（<= appliedSeq 跳过）', () => {
    let s = { ...EMPTY_DEPLOY_LOG, historyLoaded: true };
    s = appendDeployLogChunk(s, 0, ['l0']);
    s = appendDeployLogChunk(s, 1, ['l1']);
    s = appendDeployLogChunk(s, 1, ['l1-dup']);  // seq=1 已并入 → 跳过
    s = appendDeployLogChunk(s, 0, ['l0-late']);  // seq=0 <= appliedSeq → 跳过
    expect(s.lines).toEqual(['l0', 'l1']);
    expect(s.appliedSeq).toBe(1);
  });

  it('空行块 no-op（不动 lines/appliedSeq）', () => {
    const s = appendDeployLogChunk({ ...EMPTY_DEPLOY_LOG, historyLoaded: true }, 5, []);
    expect(s.lines).toEqual([]);
    expect(s.appliedSeq).toBeNull();
  });

  it('历史未并入：进 pending 缓冲（按 seq 去重），lines 不动', () => {
    let s = appendDeployLogChunk(EMPTY_DEPLOY_LOG, 3, ['a']);
    s = appendDeployLogChunk(s, 4, ['b']);
    s = appendDeployLogChunk(s, 3, ['a-dup']);  // pending 已含 seq=3 → 去重
    expect(s.lines).toEqual([]);
    expect(s.pending).toEqual([{ seq: 3, lines: ['a'] }, { seq: 4, lines: ['b'] }]);
  });
});

describe('mergeDeployLogPage（历史并入 + flush pending）', () => {
  it('首页并入把 pending 按 seq 升序 flush 到历史尾部（历史行先于实时块）', () => {
    // 先订阅缓冲两块（乱序到达），再并入历史首页。
    let s = appendDeployLogChunk(EMPTY_DEPLOY_LOG, 4, ['live-4']);
    s = appendDeployLogChunk(s, 3, ['live-3']);
    s = mergeDeployLogPage(s, { lines: ['hist-0', 'hist-1'], next_after: null, truncated: false });
    expect(s.lines).toEqual(['hist-0', 'hist-1', 'live-3', 'live-4']);  // 历史在前，pending 升序在后
    expect(s.historyLoaded).toBe(true);
    expect(s.pending).toEqual([]);
    expect(s.appliedSeq).toBe(4);
  });

  it('翻页（next_after 非空）保留游标；后续「加载更多」页不重复 flush', () => {
    let s = appendDeployLogChunk(EMPTY_DEPLOY_LOG, 9, ['live-9']);
    s = mergeDeployLogPage(s, { lines: ['h0'], next_after: 1, truncated: false });
    expect(s.lines).toEqual(['h0', 'live-9']);  // 首页并入即 flush
    expect(s.nextAfter).toBe(1);
    s = mergeDeployLogPage(s, { lines: ['h1'], next_after: null, truncated: false });
    expect(s.lines).toEqual(['h0', 'live-9', 'h1']);  // 加载更多不重复 flush（pending 已空）
    expect(s.nextAfter).toBeNull();
  });

  it('truncated 并入（任一页截断即置真）', () => {
    const s = mergeDeployLogPage(EMPTY_DEPLOY_LOG, { lines: [], next_after: null, truncated: true });
    expect(s.truncated).toBe(true);
  });
});

describe('flushPendingDeployLog（历史拉取失败兜底）', () => {
  it('标记 historyLoaded 并 flush pending，防 live 块永卡缓冲', () => {
    let s = appendDeployLogChunk(EMPTY_DEPLOY_LOG, 2, ['live-2']);
    expect(s.lines).toEqual([]);  // 缓冲
    s = flushPendingDeployLog(s);
    expect(s.historyLoaded).toBe(true);
    expect(s.lines).toEqual(['live-2']);  // 兜底 flush，实时流可见
  });

  it('已 historyLoaded → no-op', () => {
    const base = { ...EMPTY_DEPLOY_LOG, historyLoaded: true, lines: ['x'] };
    expect(flushPendingDeployLog(base)).toBe(base);
  });
});
