"""帧映射单测（契约 E §7/§8）：防腐层 / 相位聚合 / usage 提取 / 诊断映射。"""

from __future__ import annotations

from adapter_helpers import (
    RecordingSink,
    f_assistant,
    f_block_start,
    f_init,
    f_result,
    seq_ulid,
)
from coagentia_contracts.enums import AgentStatus
from coagentia_daemon.adapters.frames import FrameRouter

AID = "01K5CMPT00000000000000000A"


def _router(sink: RecordingSink, **kw) -> FrameRouter:
    return FrameRouter(AID, sink, ulid=seq_ulid, now=lambda: "2026-07-09T00:00:00.000Z", **kw)


async def test_init_frame_idle_and_session_bookkeeping() -> None:
    sink = RecordingSink()
    captured: list[str] = []
    r = _router(sink, on_session=captured.append)
    await r.process(f_init(session_id="uuid-abc"))
    assert sink.statuses() == [AgentStatus.IDLE]
    assert r.session_id == "uuid-abc"
    assert captured == ["uuid-abc"]  # 会话簿记回调命中


async def test_unknown_frames_ignored_and_counted() -> None:
    """帧防腐（铁律 4）：契约外帧不崩、计数、首现一条 agent.unknown_frame。"""
    sink = RecordingSink()
    r = _router(sink)
    await r.process({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}})
    await r.process({"type": "system", "subtype": "notification", "text": "hi"})
    await r.process({"type": "system", "subtype": "status"})
    await r.process({"type": "rate_limit_event"})  # 同类型第二次 → 静默累加
    await r.process({"type": "totally_new_kind"})
    assert r.unknown_counts["rate_limit_event"] == 2
    assert r.unknown_counts["system/notification"] == 1
    assert r.unknown_counts["system/status"] == 1
    assert r.unknown_counts["totally_new_kind"] == 1
    # 每种未知类型首现一条低频诊断（4 种 → 4 条），无重复
    unknown_diags = [d for d in sink.diagnostics if d.type == "agent.unknown_frame"]
    assert len(unknown_diags) == 4
    assert sink.statuses() == []  # 未知帧不改状态


async def test_phase_aggregation_only_on_switch() -> None:
    """相位聚合（§7.2/用例 8）：activity 帧数 = 相位切换数，非 delta 数。"""
    sink = RecordingSink()
    r = _router(sink)
    await r.process(f_block_start("thinking"))
    # 同相位内多次 delta 帧不上报
    for _ in range(50):
        await r.process({"type": "stream_event", "event": {"type": "content_block_delta"}})
    await r.process(f_block_start("text"))
    await r.process(f_block_start("tool_use", name="Bash", block_id="t1"))
    await r.process(f_block_start("tool_use", name="Read", block_id="t2"))
    await r.process(f_block_start("tool_use", name="mcp__coagentia__send_message", block_id="t3"))
    details = [d for _, d in sink.activity]
    assert details == [
        "Thinking…",
        "Replying…",
        "Running command…",
        "Reading files…",
        "Using send_message…",
    ]


async def test_repeated_same_phase_no_report() -> None:
    sink = RecordingSink()
    r = _router(sink)
    await r.process(f_block_start("tool_use", name="Read", block_id="a"))
    await r.process(f_block_start("tool_use", name="Grep", block_id="b"))  # 同 Reading 相位
    assert [d for _, d in sink.activity] == ["Reading files…"]


async def test_usage_extraction_from_result_only() -> None:
    """usage 提取（§7.4）：唯一提取点 result 帧；字段映射精确；恰一条。"""
    sink = RecordingSink()
    r = _router(sink)
    r.set_turn_context("01K5CHAN00000000000000000A", "01K5THRD00000000000000000A")
    await r.process(f_init(session_id="sess-uuid"))
    await r.process(f_result(input_tokens=111, output_tokens=22, cache_read=7, cache_write=3))
    assert len(sink.usage) == 1
    ev = sink.usage[0]
    assert ev.input_tokens == 111
    assert ev.output_tokens == 22
    assert ev.cache_read_tokens == 7  # cache_read_input_tokens → cache_read_tokens
    assert ev.cache_write_tokens == 3  # cache_creation_input_tokens → cache_write_tokens
    assert ev.source_session == "sess-uuid"
    assert ev.channel_id == "01K5CHAN00000000000000000A"
    assert ev.thread_root_id == "01K5THRD00000000000000000A"
    assert ev.id  # 适配器 ULID（exactly-once 去重根基）
    # result success → idle
    assert sink.statuses()[-1] == AgentStatus.IDLE


async def test_result_error_subtype_maps_error() -> None:
    sink = RecordingSink()
    r = _router(sink)
    await r.process(f_result(subtype="error_max_turns", is_error=True))
    assert sink.statuses()[-1] == AgentStatus.ERROR
    assert sink.status[-1][2] == "error_max_turns"


async def test_assistant_turn_output_diagnostic_truncates() -> None:
    """assistant 帧（§8）：正文不外发，截断 ≤500 留痕 + 工具调用数。"""
    sink = RecordingSink()
    r = _router(sink)
    long_text = "x" * 800
    tool_uses = [{"id": "t1", "name": "Bash", "input": {"command": "ls"}}]
    await r.process(f_assistant(text=long_text, tool_uses=tool_uses))
    diags = [d for d in sink.diagnostics if d.type == "agent.turn_output"]
    assert len(diags) == 1
    assert len(diags[0].payload["preview"]) == 500
    assert diags[0].payload["tool_calls"] == 1
    assert diags[0].payload["stop_reason"] == "end_turn"


async def test_tool_result_diagnostics() -> None:
    """user tool_result（§8）：命令 / 文件编辑 / 通用工具三类诊断。"""
    sink = RecordingSink()
    r = _router(sink)
    # 先经 assistant 帧登记 tool_use id→(name,input)
    await r.process(
        f_assistant(
            tool_uses=[
                {"id": "c1", "name": "Bash", "input": {"command": "pytest -q"}},
                {"id": "w1", "name": "Write", "input": {"file_path": "notes.md"}},
                {"id": "u1", "name": "mcp__coagentia__send_message", "input": {}},
            ]
        )
    )
    user_frame = {
        "type": "user",
        "message": {
            "content": [
                {"type": "tool_result", "tool_use_id": "c1", "is_error": False},
                {"type": "tool_result", "tool_use_id": "w1", "is_error": False},
                {"type": "tool_result", "tool_use_id": "u1", "is_error": True},
            ]
        },
    }
    await r.process(user_frame)
    by_type = {d.type: d.payload for d in sink.diagnostics if d.type.startswith("agent.")}
    assert by_type["agent.command"]["command"] == "pytest -q"
    assert by_type["agent.file_edit"]["path"] == "notes.md"
    assert by_type["agent.file_edit"]["kind"] == "create"
    assert by_type["agent.tool_call"]["tool"] == "mcp__coagentia__send_message"
    assert by_type["agent.tool_call"]["ok"] is False


async def test_malformed_frame_does_not_raise() -> None:
    sink = RecordingSink()
    r = _router(sink)
    # 缺字段/类型错乱一律不抛（防腐）
    await r.process({"type": "stream_event"})
    await r.process({"type": "assistant"})
    await r.process({"type": "user", "message": {"content": "not-a-list"}})
    await r.process({})
    assert True  # 未抛即通过
