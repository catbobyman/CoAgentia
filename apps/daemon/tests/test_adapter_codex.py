"""Codex 适配器单测（契约 E2 §5/§8）：JSON-RPC 帧映射 + 进程握手/turn/审批 + 按 runtime 分派。

桩 spawn（FakeProc/SpawnRecorder，复用 adapter_helpers），无真 codex——真机留 verify 阶段。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adapter_helpers import RecordingSink, SpawnRecorder, seq_ulid
from coagentia_contracts.daemon import AgentBoot
from coagentia_contracts.enums import AgentStatus
from coagentia_daemon.adapters.claude_code import RuntimeManager
from coagentia_daemon.adapters.codex import CodexFrameRouter, CodexProcess
from coagentia_daemon.paths import DataPaths
from helpers import until

AID = "01K5CMPT00000000000000000A"
CH = "01K5CHAN00000000000000000A"


def _now() -> str:
    return "2026-07-11T00:00:00.000Z"


def _router(sink: RecordingSink, **kw: Any) -> CodexFrameRouter:
    return CodexFrameRouter(AID, sink, ulid=seq_ulid, now=_now, **kw)


def n(method: str, **params: Any) -> dict[str, Any]:
    """codex 通知帧（method + params，无 id）。"""
    return {"method": method, "params": params}


def item_started(itype: str, iid: str = "i1", **extra: Any) -> dict[str, Any]:
    item = {"type": itype, "id": iid, **extra}
    return n("item/started", item=item, threadId="c", turnId="t", startedAtMs=0)


def item_done(itype: str, iid: str = "i1", **extra: Any) -> dict[str, Any]:
    item = {"type": itype, "id": iid, **extra}
    return n("item/completed", item=item, threadId="c", turnId="t", completedAtMs=0)


def turn_done(status: str = "completed", **extra: Any) -> dict[str, Any]:
    turn = {"id": "t1", "items": [], "status": status, **extra}
    return n("turn/completed", threadId="c", turn=turn)


def token_usage(inp: int = 111, out: int = 22, cached: int = 7) -> dict[str, Any]:
    last = {
        "inputTokens": inp,
        "outputTokens": out,
        "cachedInputTokens": cached,
        "reasoningOutputTokens": 0,
        "totalTokens": inp + out,
    }
    tu = {"last": last, "total": dict(last)}
    return n("thread/tokenUsage/updated", threadId="c", turnId="t", tokenUsage=tu)


# ========================================================= FrameRouter（纯逻辑）


async def test_spawn_uses_large_stream_limit(monkeypatch: Any) -> None:
    """B-4 根因回归：spawn 必须给 create_subprocess_exec 传远大于默认 64KB 的 limit。

    codex thread/resume 重放会话历史、大工具输出/大 reasoning 的单条 JSON-RPC 帧可超 64KB；
    默认上限下 readline() 抛 LimitOverrunError 杀读循环 → agent「挂死无诊断」（首个小帧正常、
    随后大帧哑火）。claude stream-json 大工具结果同理。两 runtime 共用 STREAM_LINE_LIMIT。
    """
    import asyncio as _asyncio

    from coagentia_daemon.adapters import claude_code as cc_mod
    from coagentia_daemon.adapters import codex as codex_mod

    captured: dict[str, Any] = {}

    async def fake_exec(*_argv: Any, **kw: Any) -> object:
        captured.clear()
        captured.update(kw)
        return object()  # 不真起进程——仅校验传参

    monkeypatch.setattr(_asyncio, "create_subprocess_exec", fake_exec)

    await codex_mod._default_codex_spawn(["x"], ".", {})
    assert captured["limit"] == cc_mod.STREAM_LINE_LIMIT
    assert captured["limit"] >= 8 * 1024 * 1024  # 远大于 64KB asyncio 默认

    await cc_mod._default_spawn(["x"], ".", {})
    assert captured["limit"] == cc_mod.STREAM_LINE_LIMIT


async def test_router_logs_item_and_turn_lifecycle(caplog: Any) -> None:
    """B-4 可观测性：router 处理 item/started 与 turn/completed 发 INFO 日志（挂死排查现场）。

    挂死症状 = 首个 tool call（item/completed）后哑火——item 序列日志即定位「卡在哪一 item」。
    """
    import logging

    logging.getLogger("coagentia_daemon").propagate = True  # 让 caplog 捕获（不依赖 logconfig）
    sink = RecordingSink()
    r = _router(sink)
    with caplog.at_level(logging.INFO, logger="coagentia_daemon.adapters.codex"):
        await r.process(item_started("mcpToolCall", "i1", tool="claim_task", server="coagentia"))
        await r.process(item_done("mcpToolCall", "i1", tool="claim_task", status="ok"))
        await r.process(turn_done())
    msgs = " ".join(rec.getMessage() for rec in caplog.records)
    assert "item/started type=mcpToolCall" in msgs
    assert "item/completed type=mcpToolCall" in msgs
    assert "turn/completed status=completed" in msgs


async def test_thread_started_sets_conversation_and_confirmed() -> None:
    sink = RecordingSink()
    captured: list[str] = []
    r = _router(sink, on_session=captured.append)
    await r.process(n("thread/started", thread={"id": "conv-1"}))
    assert r.session_id == "conv-1"
    assert r.confirmed is True
    assert captured == ["conv-1"]
    assert sink.statuses() == []  # 就绪 idle 由管理器发，router 不在 thread/started 发状态


async def test_turn_started_busy_completed_idle() -> None:
    sink = RecordingSink()
    r = _router(sink)
    await r.process(n("turn/started", threadId="c", turn={}))
    assert sink.statuses()[-1] == AgentStatus.BUSY
    await r.process(turn_done())
    assert sink.statuses()[-1] == AgentStatus.IDLE


async def test_usage_extracted_once_on_turn_completed() -> None:
    """usage（E2 §7）：tokenUsage/updated 缓存 → turn/completed 提取恰一条；cache_write 恒 0。"""
    sink = RecordingSink()
    r = _router(sink)
    r.set_conversation("conv-1")
    r.set_turn_context(CH, "01K5THRD00000000000000000A")
    r.begin_turn()
    await r.process(token_usage())
    assert len(sink.usage) == 0  # 未在 update 时上报（防多次 update 重复计）
    await r.process(turn_done())
    assert len(sink.usage) == 1
    ev = sink.usage[0]
    assert ev.input_tokens == 111
    assert ev.output_tokens == 22
    assert ev.cache_read_tokens == 7  # cachedInputTokens → cache_read_tokens
    assert ev.cache_write_tokens == 0  # codex 无独立 cache creation 字段
    assert ev.source_session == "conv-1"
    assert ev.channel_id == CH
    assert ev.id


async def test_multiple_token_updates_still_one_usage() -> None:
    sink = RecordingSink()
    r = _router(sink)
    r.begin_turn()
    await r.process(token_usage(10, 1))
    await r.process(token_usage(50, 8))
    await r.process(turn_done())
    assert len(sink.usage) == 1
    assert sink.usage[0].input_tokens == 50  # 提取最新增量（权威 last）


async def test_phase_aggregation_only_on_switch() -> None:
    """相位聚合（E2 §5）：item/started + delta → activity 帧数=相位切换数。"""
    sink = RecordingSink()
    r = _router(sink)
    await r.process(item_started("reasoning"))
    for _ in range(20):  # 同相位 delta 不上报
        await r.process(n("item/reasoning/textDelta", delta="x", itemId="i1"))
    await r.process(n("item/agentMessage/delta", delta="hi", itemId="i2"))
    await r.process(item_started("commandExecution", "i3"))
    await r.process(item_started("fileChange", "i4"))
    await r.process(item_started("mcpToolCall", "i5", tool="send_message", server="coagentia"))
    assert [d for _, d in sink.activity] == [
        "Thinking…",
        "Replying…",
        "Running command…",
        "Writing file…",
        "Using send_message…",
    ]


async def test_item_completed_diagnostics() -> None:
    sink = RecordingSink()
    r = _router(sink)
    await r.process(item_done("commandExecution", command=["pytest"], exitCode=0, status="ok"))
    await r.process(item_done("fileChange", changes=[{"path": "notes.md"}], status="ok"))
    await r.process(item_done("mcpToolCall", tool="send_message", status="failed"))
    by_type = {d.type: d.payload for d in sink.diagnostics if d.type.startswith("agent.")}
    assert by_type["agent.command"]["command"] == "pytest"
    assert by_type["agent.command"]["is_error"] is False
    assert by_type["agent.file_edit"]["path"] == "notes.md"
    assert by_type["agent.tool_call"]["tool"] == "send_message"
    assert by_type["agent.tool_call"]["ok"] is False


async def test_command_failed_exit_code_is_error() -> None:
    sink = RecordingSink()
    r = _router(sink)
    await r.process(item_done("commandExecution", command="bad", exitCode=2, status="completed"))
    diag = next(d for d in sink.diagnostics if d.type == "agent.command")
    assert diag.payload["is_error"] is True


async def test_turn_completed_failed_maps_error() -> None:
    sink = RecordingSink()
    r = _router(sink)
    await r.process(turn_done("failed", error={"code": "badRequest"}))
    assert sink.statuses()[-1] == AgentStatus.ERROR
    assert sink.status[-1][2] == "badRequest"


async def test_error_notification_terminal_and_transient() -> None:
    sink = RecordingSink()
    r = _router(sink)
    await r.process(n("error", error="serverOverloaded", threadId="c", turnId="t", willRetry=True))
    assert sink.statuses() == []  # 瞬态：codex 内部重试，不改状态
    await r.process(n("error", error="badRequest", threadId="c", turnId="t", willRetry=False))
    assert sink.statuses()[-1] == AgentStatus.ERROR


async def test_unknown_notification_counted_ignored_silent() -> None:
    """防腐（铁律 4）：未知通知计数 + 首现诊断；契约内已知噪声静默不计。"""
    sink = RecordingSink()
    r = _router(sink)
    await r.process(n("totally/new/method", foo=1))
    await r.process(n("totally/new/method", foo=2))  # 同类型第二次 → 静默累加
    await r.process(n("thread/status/changed", status="idle"))  # 已知噪声 → 静默忽略
    await r.process(n("account/updated"))  # 已知噪声
    assert r.unknown_counts["totally/new/method"] == 2
    assert "thread/status/changed" not in r.unknown_counts
    unknown_diags = [d for d in sink.diagnostics if d.type == "agent.unknown_frame"]
    assert len(unknown_diags) == 1
    assert sink.statuses() == []


async def test_malformed_frames_do_not_raise() -> None:
    sink = RecordingSink()
    r = _router(sink)
    await r.process(n("turn/completed", threadId="c"))  # 无 turn
    await r.process(n("item/started", item="not-a-dict"))
    await r.process(item_done("commandExecution"))  # 缺字段
    await r.process({"method": "thread/tokenUsage/updated", "params": "bad"})
    await r.process(n("thread/started", thread="bad"))
    assert True  # 未抛即通过


async def test_release_turn_callback_fires() -> None:
    calls: list[int] = []

    async def on_end() -> None:
        calls.append(1)

    sink = RecordingSink()
    r = _router(sink, on_turn_end=on_end)
    await r.process(turn_done())
    assert calls == [1]


# ========================================================= CodexProcess（桩 spawn）


def _boot(home: Path, runtime: str = "codex") -> AgentBoot:
    return AgentBoot(
        agent_member_id=AID,
        name="Codex-Pat",
        runtime=runtime,
        model="gpt-5-codex",
        home_path=str(home),
        skills=[],
    )


def _make_manager(tmp_path: Path) -> tuple[RuntimeManager, RecordingSink, SpawnRecorder, DataPaths]:
    paths = DataPaths(tmp_path / "root")
    paths.ensure_dirs()
    spawn = SpawnRecorder()
    mgr = RuntimeManager(paths, server_url="http://s", api_key="cak_x", spawn=spawn, ulid=seq_ulid)
    sink = RecordingSink()
    mgr.bind(sink)
    return mgr, sink, spawn, paths


def _has_line(proc: Any, needle: str) -> bool:
    return any(needle in ln for ln in proc.stdin.lines())


def _deliver_msg(mid: str, body: str) -> dict[str, Any]:
    return {
        "id": mid,
        "channel_id": CH,
        "author_member_id": "01K5AUTH00000000000000000A",
        "created_at": _now(),
        "body": body,
    }


async def _reach_thread_request(proc: Any, *, resume: bool = False) -> None:
    """推 initialize 响应，等到 thread/start|resume 请求写出（不推 thread 响应）。"""
    await until(lambda: _has_line(proc, '"initialize"'))
    proc.stdout.push({"id": 1, "result": {"codexHome": "/iso/.codex"}})
    method = '"thread/resume"' if resume else '"thread/start"'
    await until(lambda: _has_line(proc, method))


async def _drive_handshake(proc: Any, cid: str = "conv-1", *, resume: bool = False) -> None:
    """推 initialize / thread.* 响应，完成握手。"""
    await _reach_thread_request(proc, resume=resume)
    proc.stdout.push({"id": 2, "result": {"thread": {"id": cid}}})


async def test_dispatch_picks_codex_process(tmp_path: Path) -> None:
    """管理器按 boot.runtime 分派 CodexProcess + `codex app-server` 命令行。"""
    mgr, sink, spawn, paths = _make_manager(tmp_path)
    assert await mgr.start(_boot(tmp_path / "home")) is True
    entry = mgr._agents[AID]
    assert isinstance(entry.process, CodexProcess)
    assert len(spawn.procs) == 1
    assert spawn.procs[0].argv[-1] == "app-server"
    assert sink.statuses()[0] == AgentStatus.STARTING
    await until(lambda: AgentStatus.IDLE in sink.statuses())  # 握手前就绪 idle（同 claude）
    assert "agent.process_started" in sink.diag_types()


async def test_handshake_persists_conversation(tmp_path: Path) -> None:
    mgr, sink, spawn, paths = _make_manager(tmp_path)
    await mgr.start(_boot(tmp_path / "home"))
    proc = spawn.procs[0]
    await _drive_handshake(proc, "conv-xyz")
    await until(lambda: paths.read_session(AID).get("conversation_id") == "conv-xyz")
    assert mgr._agents[AID].process.router.confirmed is True
    assert mgr.process_table()[0].source_session == "conv-xyz"
    assert _has_line(proc, '"initialized"')  # 通知已发


async def test_full_turn_deliver_busy_activity_idle_usage(tmp_path: Path) -> None:
    """用例 2：投递→turn/start→busy+相位→usage 恰一条→idle。"""
    mgr, sink, spawn, paths = _make_manager(tmp_path)
    await mgr.start(_boot(tmp_path / "home"))
    proc = spawn.procs[0]
    await _drive_handshake(proc, "conv-1")
    await until(lambda: mgr._agents[AID].process.router.confirmed)

    msg = _deliver_msg("01K5MSG100000000000000000A", "hi codex")
    assert await mgr.deliver(AID, CH, [msg], None) is True
    assert AgentStatus.BUSY in sink.statuses()
    await until(lambda: _has_line(proc, '"turn/start"'))
    ts_line = next(ln for ln in proc.stdin.lines() if '"turn/start"' in ln)
    assert "hi codex" in ts_line  # 渲染正文进 turn/start input
    assert '"type": "user"' not in ts_line  # 非 claude stream-json 封装（纪律 8：载体各自特化）

    proc.stdout.push(n("turn/started", threadId="conv-1", turn={}))
    proc.stdout.push(item_started("agentMessage", "a1"))
    proc.stdout.push(token_usage(42, 8))
    proc.stdout.push(turn_done())
    await until(lambda: len(sink.usage) == 1)
    assert sink.statuses()[-1] == AgentStatus.IDLE
    assert [d for _, d in sink.activity] == ["Replying…"]
    ev = sink.usage[0]
    assert ev.input_tokens == 42 and ev.output_tokens == 8
    assert ev.channel_id == CH
    assert ev.source_session == "conv-1"


async def test_feed_before_ready_queues_then_drains(tmp_path: Path) -> None:
    """thread 未就绪即投递 → 入队；握手完成后排空提交（不丢投递）。"""
    mgr, sink, spawn, paths = _make_manager(tmp_path)
    await mgr.start(_boot(tmp_path / "home"))
    proc = spawn.procs[0]
    await mgr.deliver(AID, CH, [_deliver_msg("01K5MSG100000000000000000A", "early")], None)
    assert not _has_line(proc, '"turn/start"')  # 未就绪 → 未提交
    await _drive_handshake(proc, "conv-1")
    await until(lambda: _has_line(proc, '"turn/start"'))  # 就绪后排空
    assert _has_line(proc, "early")


async def test_serial_turn_queue(tmp_path: Path) -> None:
    """两连投递（同频道递增 id）→ 串行提交：turn2 待 turn1 completed 后才发。"""
    mgr, sink, spawn, paths = _make_manager(tmp_path)
    await mgr.start(_boot(tmp_path / "home"))
    proc = spawn.procs[0]
    await _drive_handshake(proc, "conv-1")
    await until(lambda: mgr._agents[AID].process.router.confirmed)
    await mgr.deliver(AID, CH, [_deliver_msg("01K5MSG100000000000000000A", "one")], None)
    await until(lambda: _has_line(proc, "one"))
    await mgr.deliver(AID, CH, [_deliver_msg("01K5MSG200000000000000000A", "two")], None)
    assert not _has_line(proc, "two")  # turn1 未完成 → turn2 入队不发
    proc.stdout.push(turn_done())
    await until(lambda: _has_line(proc, "two"))  # turn1 完成 → turn2 提交


async def test_server_request_auto_approved(tmp_path: Path) -> None:
    """ServerRequest 审批自动应答（NFR5；即使 approvalPolicy=never 也可能来）。"""
    mgr, sink, spawn, paths = _make_manager(tmp_path)
    await mgr.start(_boot(tmp_path / "home"))
    proc = spawn.procs[0]
    await _drive_handshake(proc, "conv-1")
    params = {"callId": "x", "command": ["ls"], "conversationId": "c", "cwd": "/", "parsedCmd": []}
    proc.stdout.push({"id": 99, "method": "execCommandApproval", "params": params})
    await until(lambda: _has_line(proc, '"decision"'))
    line = next(ln for ln in proc.stdin.lines() if '"decision"' in ln)
    assert '"id": 99' in line and '"approved"' in line


async def test_unknown_server_request_conservative_error(tmp_path: Path) -> None:
    mgr, sink, spawn, paths = _make_manager(tmp_path)
    await mgr.start(_boot(tmp_path / "home"))
    proc = spawn.procs[0]
    await _drive_handshake(proc, "conv-1")
    proc.stdout.push({"id": 77, "method": "attestation/generate", "params": {}})
    await until(lambda: _has_line(proc, '"error"'))
    line = next(ln for ln in proc.stdin.lines() if '"error"' in ln and '"id": 77' in ln)
    assert "-32601" in line


async def test_restart_resumes_reset_starts_new(tmp_path: Path) -> None:
    """三档：Restart→thread/resume（保 conversation）；reset_session→thread/start（清簿记）。"""
    mgr, sink, spawn, paths = _make_manager(tmp_path)
    boot = _boot(tmp_path / "home")
    await mgr.start(boot)
    await _drive_handshake(spawn.procs[0], "conv-keep")
    await until(lambda: paths.read_session(AID).get("conversation_id") == "conv-keep")

    await mgr.restart(boot)  # 一档：保 conversation → thread/resume
    assert len(spawn.procs) == 2
    await _reach_thread_request(spawn.procs[1], resume=True)
    assert _has_line(spawn.procs[1], "conv-keep")
    assert paths.read_session(AID).get("conversation_id") == "conv-keep"

    await mgr.reset_session(boot)  # 二档：清簿记 → thread/start
    assert len(spawn.procs) == 3
    await _reach_thread_request(spawn.procs[2], resume=False)
    assert paths.read_session(AID) == {}


async def test_reset_session_args_empty(tmp_path: Path) -> None:
    paths = DataPaths(tmp_path / "root")
    paths.ensure_dirs()
    sink = RecordingSink()
    proc = CodexProcess(AID, sink, paths, server_url="http://s", api_key="k")
    assert proc.reset_session_args() == []


async def test_handshake_failure_kills_process(tmp_path: Path, monkeypatch) -> None:
    """握手失败（thread 无 id）→ 杀进程触发退出（熔断降级由管理器接管）。"""
    from coagentia_daemon.adapters import claude_code

    monkeypatch.setattr(claude_code, "CRASH_BACKOFF", (0.0, 0.0, 0.0))
    mgr, sink, spawn, paths = _make_manager(tmp_path)
    await mgr.start(_boot(tmp_path / "home"))
    proc = spawn.procs[0]
    await until(lambda: _has_line(proc, '"initialize"'))
    proc.stdout.push({"id": 1, "result": {}})
    await until(lambda: _has_line(proc, '"thread/start"'))
    proc.stdout.push({"id": 2, "result": {"thread": {}}})  # 无 id → 握手失败
    await until(lambda: proc.returncode is not None, timeout=5)  # 被 kill


def test_codex_materialize_credentials_preserves_refreshed(tmp_path: Path) -> None:
    """review #5：隔离 auth.json 比机器源新（codex 刷新 OAuth）→ 保留不覆写；机器源更新才复制。"""
    import os

    from coagentia_daemon.adapters import codex_cmdline

    machine = tmp_path / "machine"
    machine.mkdir()
    target = tmp_path / "isolated"
    target.mkdir()
    (machine / "auth.json").write_text('{"v":"machine-old"}', encoding="utf-8")
    (target / "auth.json").write_text('{"v":"codex-refreshed"}', encoding="utf-8")
    # 隔离目标更新（codex 运行时刷新）——机器源置旧。
    os.utime(machine / "auth.json", (1000, 1000))
    os.utime(target / "auth.json", (5000, 5000))

    copied = codex_cmdline.materialize_credentials(target, source=machine)
    assert copied == []  # 保留刷新态，未覆写
    assert (target / "auth.json").read_text(encoding="utf-8") == '{"v":"codex-refreshed"}'

    # 机器源更新（用户重登）→ 复制覆盖。
    os.utime(machine / "auth.json", (9000, 9000))
    copied2 = codex_cmdline.materialize_credentials(target, source=machine)
    assert copied2 == ["auth.json"]
    assert (target / "auth.json").read_text(encoding="utf-8") == '{"v":"machine-old"}'
