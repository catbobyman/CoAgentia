/**
 * 帧映射单测（契约 E §7/§8）：防腐层 / 相位聚合 / usage 提取 / 诊断映射。
 *
 * 对等基准 = apps/daemon tests/test_adapter_frames.py（9 用例逐条对应，零行为改进）。
 */

import { describe, expect, it } from 'vitest';

import { FrameRouter } from '../src/adapters/frames.ts';
import { RecordingSink, fAssistant, fBlockStart, fInit, fResult, seqUlid } from './adapter_helpers.ts';

const AID = '01K5CMPT00000000000000000A';

function router(sink: RecordingSink, opts: { onSession?: (sid: string) => void } = {}): FrameRouter {
  return new FrameRouter(AID, sink, {
    ulid: seqUlid,
    now: () => '2026-07-09T00:00:00.000Z',
    ...opts,
  });
}

describe('FrameRouter 帧映射（契约 E §7/§8）', () => {
  it('init 帧 → idle + 会话簿记（test_init_frame_idle_and_session_bookkeeping）', async () => {
    const sink = new RecordingSink();
    const captured: string[] = [];
    const r = router(sink, { onSession: (sid) => captured.push(sid) });
    await r.process(fInit('uuid-abc'));
    expect(sink.statuses()).toEqual(['idle']);
    expect(r.sessionId).toBe('uuid-abc');
    expect(captured).toEqual(['uuid-abc']); // 会话簿记回调命中
  });

  it('帧防腐（铁律 4）：契约外帧不崩、计数、首现一条 agent.unknown_frame（test_unknown_frames_ignored_and_counted）', async () => {
    const sink = new RecordingSink();
    const r = router(sink);
    await r.process({ type: 'rate_limit_event', rate_limit_info: { status: 'allowed' } });
    await r.process({ type: 'system', subtype: 'notification', text: 'hi' });
    await r.process({ type: 'system', subtype: 'status' });
    await r.process({ type: 'rate_limit_event' }); // 同类型第二次 → 静默累加
    await r.process({ type: 'totally_new_kind' });
    expect(r.unknownCounts['rate_limit_event']).toBe(2);
    expect(r.unknownCounts['system/notification']).toBe(1);
    expect(r.unknownCounts['system/status']).toBe(1);
    expect(r.unknownCounts['totally_new_kind']).toBe(1);
    // 每种未知类型首现一条低频诊断（4 种 → 4 条），无重复
    const unknownDiags = sink.diagnostics.filter((d) => d.type === 'agent.unknown_frame');
    expect(unknownDiags).toHaveLength(4);
    expect(sink.statuses()).toEqual([]); // 未知帧不改状态
  });

  it('相位聚合（§7.2/用例 8）：activity 帧数 = 相位切换数，非 delta 数（test_phase_aggregation_only_on_switch）', async () => {
    const sink = new RecordingSink();
    const r = router(sink);
    await r.process(fBlockStart('thinking'));
    // 同相位内多次 delta 帧不上报
    for (let i = 0; i < 50; i += 1) {
      await r.process({ type: 'stream_event', event: { type: 'content_block_delta' } });
    }
    await r.process(fBlockStart('text'));
    await r.process(fBlockStart('tool_use', { name: 'Bash', blockId: 't1' }));
    await r.process(fBlockStart('tool_use', { name: 'Read', blockId: 't2' }));
    await r.process(fBlockStart('tool_use', { name: 'mcp__coagentia__send_message', blockId: 't3' }));
    const details = sink.activity.map(([, d]) => d);
    expect(details).toEqual([
      'Thinking…',
      'Replying…',
      'Running command…',
      'Reading files…',
      'Using send_message…',
    ]);
  });

  it('同相位重复不上报（test_repeated_same_phase_no_report）', async () => {
    const sink = new RecordingSink();
    const r = router(sink);
    await r.process(fBlockStart('tool_use', { name: 'Read', blockId: 'a' }));
    await r.process(fBlockStart('tool_use', { name: 'Grep', blockId: 'b' })); // 同 Reading 相位
    expect(sink.activity.map(([, d]) => d)).toEqual(['Reading files…']);
  });

  it('usage 提取（§7.4）：唯一提取点 result 帧；字段映射精确；恰一条（test_usage_extraction_from_result_only）', async () => {
    const sink = new RecordingSink();
    const r = router(sink);
    r.setTurnContext('01K5CHAN00000000000000000A', '01K5THRD00000000000000000A');
    await r.process(fInit('sess-uuid'));
    await r.process(fResult({ inputTokens: 111, outputTokens: 22, cacheRead: 7, cacheWrite: 3 }));
    expect(sink.usage).toHaveLength(1);
    const ev = sink.usage[0];
    expect(ev.input_tokens).toBe(111);
    expect(ev.output_tokens).toBe(22);
    expect(ev.cache_read_tokens).toBe(7); // cache_read_input_tokens → cache_read_tokens
    expect(ev.cache_write_tokens).toBe(3); // cache_creation_input_tokens → cache_write_tokens
    expect(ev.source_session).toBe('sess-uuid');
    expect(ev.channel_id).toBe('01K5CHAN00000000000000000A');
    expect(ev.thread_root_id).toBe('01K5THRD00000000000000000A');
    expect(ev.id).toBeTruthy(); // 适配器 ULID（exactly-once 去重根基）
    // result success → idle
    expect(sink.statuses().at(-1)).toBe('idle');
  });

  it('result error subtype → ERROR + error_detail（test_result_error_subtype_maps_error）', async () => {
    const sink = new RecordingSink();
    const r = router(sink);
    await r.process(fResult({ subtype: 'error_max_turns', isError: true }));
    expect(sink.statuses().at(-1)).toBe('error');
    expect(sink.status.at(-1)![2]).toBe('error_max_turns');
  });

  it('assistant 帧（§8）：正文不外发，截断 ≤500 留痕 + 工具调用数（test_assistant_turn_output_diagnostic_truncates）', async () => {
    const sink = new RecordingSink();
    const r = router(sink);
    const longText = 'x'.repeat(800);
    const toolUses = [{ id: 't1', name: 'Bash', input: { command: 'ls' } }];
    await r.process(fAssistant({ text: longText, toolUses }));
    const diags = sink.diagnostics.filter((d) => d.type === 'agent.turn_output');
    expect(diags).toHaveLength(1);
    const payload = diags[0].payload as Record<string, unknown>;
    expect((payload['preview'] as string).length).toBe(500);
    expect(payload['tool_calls']).toBe(1);
    expect(payload['stop_reason']).toBe('end_turn');
  });

  it('user tool_result（§8）：命令 / 文件编辑 / 通用工具三类诊断（test_tool_result_diagnostics）', async () => {
    const sink = new RecordingSink();
    const r = router(sink);
    // 先经 assistant 帧登记 tool_use id→(name,input)
    await r.process(
      fAssistant({
        toolUses: [
          { id: 'c1', name: 'Bash', input: { command: 'pytest -q' } },
          { id: 'w1', name: 'Write', input: { file_path: 'notes.md' } },
          { id: 'u1', name: 'mcp__coagentia__send_message', input: {} },
        ],
      }),
    );
    const userFrame = {
      type: 'user',
      message: {
        content: [
          { type: 'tool_result', tool_use_id: 'c1', is_error: false },
          { type: 'tool_result', tool_use_id: 'w1', is_error: false },
          { type: 'tool_result', tool_use_id: 'u1', is_error: true },
        ],
      },
    };
    await r.process(userFrame);
    const byType: Record<string, Record<string, unknown>> = {};
    for (const d of sink.diagnostics) {
      if (d.type.startsWith('agent.')) {
        byType[d.type] = d.payload as Record<string, unknown>;
      }
    }
    expect(byType['agent.command']!['command']).toBe('pytest -q');
    expect(byType['agent.file_edit']!['path']).toBe('notes.md');
    expect(byType['agent.file_edit']!['kind']).toBe('create');
    expect(byType['agent.tool_call']!['tool']).toBe('mcp__coagentia__send_message');
    expect(byType['agent.tool_call']!['ok']).toBe(false);
  });

  it('畸形帧不抛（防腐）（test_malformed_frame_does_not_raise）', async () => {
    const sink = new RecordingSink();
    const r = router(sink);
    // 缺字段/类型错乱一律不抛（防腐）
    await r.process({ type: 'stream_event' });
    await r.process({ type: 'assistant' });
    await r.process({ type: 'user', message: { content: 'not-a-list' } });
    await r.process({});
    expect(true).toBe(true); // 未抛即通过
  });
});
