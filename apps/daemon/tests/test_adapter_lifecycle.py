"""ClaudeCodeAdapter 生命周期单测（E §4/§5/§6）：桩 spawn，无真 claude。

覆盖：start→idle、幂等、deliver→busy→result→idle+usage 恰一条、三档重置、
崩溃拉起退避、resume 损坏降级 session_lost、崩溃熔断放弃。
"""

from __future__ import annotations

import json
from pathlib import Path

from adapter_helpers import (
    RecordingSink,
    SpawnRecorder,
    f_block_start,
    f_init,
    f_result,
    seq_ulid,
)
from coagentia_contracts.daemon import AgentBoot
from coagentia_contracts.enums import AgentStatus, WakeReason
from coagentia_daemon.adapters import claude_code, cmdline
from coagentia_daemon.adapters.claude_code import ClaudeCodeAdapter
from coagentia_daemon.paths import DataPaths
from helpers import until

AID = "01K5CMPT00000000000000000A"


def _boot(home: Path) -> AgentBoot:
    return AgentBoot(
        agent_member_id=AID,
        name="Pat",
        runtime="claude_code",
        model="claude-opus-4-8",
        home_path=str(home),
        skills=[],
    )


def _make(tmp_path: Path) -> tuple[ClaudeCodeAdapter, RecordingSink, SpawnRecorder, DataPaths]:
    paths = DataPaths(tmp_path / "root")
    paths.ensure_dirs()
    spawn = SpawnRecorder()
    adapter = ClaudeCodeAdapter(
        paths, server_url="http://s", api_key="cak_x", spawn=spawn, ulid=seq_ulid
    )
    sink = RecordingSink()
    adapter.bind(sink)
    return adapter, sink, spawn, paths


async def test_start_reaches_idle_on_init(tmp_path: Path) -> None:
    adapter, sink, spawn, paths = _make(tmp_path)
    boot = _boot(tmp_path / "home")
    assert await adapter.start(boot) is True
    assert sink.statuses()[0] == AgentStatus.STARTING
    assert len(spawn.procs) == 1
    # 就绪 idle 解耦于 init（实测本 CLI init 首输入后才到，E §11.3）
    await until(lambda: AgentStatus.IDLE in sink.statuses())
    # init 帧 → 会话簿记（session_id 持久化 + process_table 反映）
    spawn.procs[0].stdout.push(f_init(session_id="uuid-xyz"))
    await until(lambda: paths.read_session(AID).get("session_id") == "uuid-xyz")
    assert adapter.process_table()[0].source_session == "uuid-xyz"
    # process_started 诊断
    assert "agent.process_started" in sink.diag_types()


async def test_start_idempotent_while_running(tmp_path: Path) -> None:
    adapter, sink, spawn, paths = _make(tmp_path)
    boot = _boot(tmp_path / "home")
    assert await adapter.start(boot) is True
    assert await adapter.start(boot) is False  # 已在跑 → noop
    assert len(spawn.procs) == 1


async def test_full_turn_deliver_busy_activity_result_idle_usage(tmp_path: Path) -> None:
    """用例 2 桩版：喂输入→busy+activity→result→idle + usage 恰一条 ULID。"""
    adapter, sink, spawn, paths = _make(tmp_path)
    boot = _boot(tmp_path / "home")
    await adapter.start(boot)
    proc = spawn.procs[0]
    proc.stdout.push(f_init(session_id="uuid-1"))
    await until(lambda: AgentStatus.IDLE in sink.statuses())

    msg = {
        "id": "01K5MSG100000000000000000A",
        "channel_id": "01K5CHAN00000000000000000A",
        "author_member_id": "01K5AUTH00000000000000000A",
        "created_at": "2026-07-09T00:00:00.000Z",
        "body": "hi",
    }
    assert await adapter.deliver(AID, "01K5CHAN00000000000000000A", [msg], None) is True
    assert AgentStatus.BUSY in sink.statuses()
    assert proc.stdin.lines()  # 写 stdin 即 ack
    assert "hi" in proc.stdin.lines()[0]

    proc.stdout.push(f_block_start("thinking"))
    proc.stdout.push(f_result(input_tokens=42, output_tokens=8))
    await until(lambda: len(sink.usage) == 1)
    assert sink.statuses()[-1] == AgentStatus.IDLE
    assert [d for _, d in sink.activity] == ["Thinking…"]
    ev = sink.usage[0]
    assert ev.input_tokens == 42 and ev.output_tokens == 8
    assert ev.channel_id == "01K5CHAN00000000000000000A"
    assert ev.source_session == "uuid-1"


async def test_deliver_dedup_by_max_message_id(tmp_path: Path) -> None:
    adapter, sink, spawn, paths = _make(tmp_path)
    boot = _boot(tmp_path / "home")
    await adapter.start(boot)
    msg = {"id": "01K5MSG100000000000000000A", "channel_id": "C", "body": "a"}
    assert await adapter.deliver(AID, "C", [msg], None) is True
    assert await adapter.deliver(AID, "C", [msg], None) is False  # 同批 → noop


async def test_deliver_dedup_is_per_channel(tmp_path: Path) -> None:
    """#2：去重游标按 channel_id 维度——频道 A 的较大 id 不压制频道 B 较早消息的投递。"""
    adapter, sink, spawn, paths = _make(tmp_path)
    boot = _boot(tmp_path / "home")
    await adapter.start(boot)
    # 频道 A 先投较大 message_id。
    msg_a = {"id": "01K5MSG900000000000000000A", "channel_id": "A", "body": "a"}
    assert await adapter.deliver(AID, "A", [msg_a], None) is True
    # 频道 B 投较早（更小）message_id：跨频道独立游标 → 不被 A 误判 noop。
    msg_b = {"id": "01K5MSG100000000000000000A", "channel_id": "B", "body": "b"}
    assert await adapter.deliver(AID, "B", [msg_b], None) is True
    # 同频道 B 重投同批 → 按频道去重仍 noop。
    assert await adapter.deliver(AID, "B", [msg_b], None) is False


async def test_stop_emits_offline(tmp_path: Path) -> None:
    adapter, sink, spawn, paths = _make(tmp_path)
    boot = _boot(tmp_path / "home")
    await adapter.start(boot)
    assert await adapter.stop(AID) is True
    assert sink.statuses()[-1] == AgentStatus.OFFLINE
    assert await adapter.stop(AID) is False  # 已停 → noop


async def test_restart_keeps_session_reset_clears(tmp_path: Path) -> None:
    adapter, sink, spawn, paths = _make(tmp_path)
    boot = _boot(tmp_path / "home")
    await adapter.start(boot)
    spawn.procs[0].stdout.push(f_init(session_id="uuid-keep"))
    await until(lambda: paths.read_session(AID).get("session_id") == "uuid-keep")

    await adapter.restart(boot)  # 一档：保 session → 新进程带 --resume
    assert len(spawn.procs) == 2
    assert "--resume" in spawn.procs[1].argv
    assert paths.read_session(AID).get("session_id") == "uuid-keep"

    await adapter.reset_session(boot)  # 二档：新会话 → 清簿记、无 --resume
    assert len(spawn.procs) == 3
    assert "--resume" not in spawn.procs[2].argv
    assert paths.read_session(AID) == {}


async def test_crash_restart_backoff(tmp_path: Path, monkeypatch) -> None:
    """崩溃拉起（§5）：进程意外退出 → --resume 拉起 + crash_restarted 诊断。"""
    monkeypatch.setattr(claude_code, "CRASH_BACKOFF", (0.0, 0.0, 0.0))
    adapter, sink, spawn, paths = _make(tmp_path)
    boot = _boot(tmp_path / "home")
    await adapter.start(boot)
    spawn.procs[0].stdout.push(f_init(session_id="uuid-c"))
    await until(lambda: AgentStatus.IDLE in sink.statuses())
    # 意外退出（非 stop）
    spawn.procs[0].finish(1)
    await until(lambda: len(spawn.procs) == 2, timeout=5)
    assert "agent.process_exited" in sink.diag_types()
    assert "agent.crash_restarted" in sink.diag_types()
    assert "--resume" in spawn.procs[1].argv  # 保上下文


async def test_resume_corruption_degrades_session_lost(tmp_path: Path, monkeypatch) -> None:
    """resume 损坏降级（§4）：resume 启动从未就绪即退 → session_lost + 冷启。"""
    monkeypatch.setattr(claude_code, "CRASH_BACKOFF", (0.0, 0.0, 0.0))
    adapter, sink, spawn, paths = _make(tmp_path)
    paths.write_session(AID, {"session_id": "corrupt-old"})
    boot = _boot(tmp_path / "home")
    await adapter.start(boot)  # resume=True
    assert "--resume" in spawn.procs[0].argv
    # 从未 init 就崩
    spawn.procs[0].finish(1)
    await until(lambda: "agent.session_lost" in sink.diag_types(), timeout=5)
    await until(lambda: len(spawn.procs) == 2, timeout=5)
    assert paths.read_session(AID) == {}  # 会话簿记已清
    assert "--resume" not in spawn.procs[1].argv  # 降级冷启


async def test_crash_loop_giveup_error(tmp_path: Path, monkeypatch) -> None:
    """崩溃熔断（§5/用例 6）：5 分钟窗 ≥3 次 → error 放弃拉起。"""
    monkeypatch.setattr(claude_code, "CRASH_BACKOFF", (0.0, 0.0, 0.0))
    adapter, sink, spawn, paths = _make(tmp_path)
    boot = _boot(tmp_path / "home")
    await adapter.start(boot)
    import asyncio

    entry = adapter._agents[AID]
    loop = asyncio.get_running_loop()
    entry.crash_times.extend([loop.time()] * 3)  # 预置 3 次近期崩溃
    spawn.procs[0].finish(1)  # 第 4 次 → 超阈
    await until(lambda: AgentStatus.ERROR in sink.statuses(), timeout=5)
    assert len(spawn.procs) == 1  # 放弃拉起，无新进程


async def test_wake_flips_busy(tmp_path: Path) -> None:
    adapter, sink, spawn, paths = _make(tmp_path)
    boot = _boot(tmp_path / "home")
    await adapter.start(boot)
    spawn.procs[0].stdout.push(f_init())
    await until(lambda: AgentStatus.IDLE in sink.statuses())
    assert await adapter.wake(AID, WakeReason.MENTION, None) is True
    assert sink.statuses()[-1] == AgentStatus.BUSY
    assert await adapter.wake(AID, WakeReason.MENTION, None) is False  # 已 busy → noop


async def test_inject_writes_stdin_and_diagnostic(tmp_path: Path) -> None:
    adapter, sink, spawn, paths = _make(tmp_path)
    boot = _boot(tmp_path / "home")
    await adapter.start(boot)
    await adapter.inject(AID, "看这里", {"kind": "guard_feedback"}, "guard.reevaluate_requested")
    assert "guard.reevaluate_requested" in sink.diag_types()
    assert any("看这里" in ln for ln in spawn.procs[0].stdin.lines())


def _write_credentials(path: Path, expires_at: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": f"access-{expires_at}",
                    "refreshToken": f"refresh-{expires_at}",
                    "expiresAt": expires_at,
                    "refreshTokenExpiresAt": expires_at + 1000,
                }
            }
        ),
        encoding="utf-8",
    )


async def test_auth_failure_absorbs_peer_credentials_and_retries_turn(
    tmp_path: Path, monkeypatch
) -> None:
    machine = tmp_path / "machine"
    _write_credentials(machine / ".credentials.json", 0)
    monkeypatch.setattr(cmdline, "default_config_dir", lambda: machine)
    monkeypatch.setattr(claude_code, "AUTH_RECOVERY_DELAYS", (0.0,))

    adapter, sink, spawn, paths = _make(tmp_path)
    boot = _boot(tmp_path / "home")
    await adapter.start(boot)
    channel_id = "01K5CHAN00000000000000000A"
    await adapter.deliver(
        AID,
        channel_id,
        [
            {
                "id": "01K5MSG100000000000000000A",
                "channel_id": channel_id,
                "body": "retry me",
            }
        ],
        None,
    )
    assert len(spawn.procs[0].stdin.lines()) == 1

    peer = paths.agents_dir / "peer" / ".claude" / ".credentials.json"
    _write_credentials(peer, 5000)
    auth_error = f_result(subtype="error_during_execution", is_error=True)
    auth_error["result"] = "Failed to authenticate: OAuth session expired"
    await adapter._agents[AID].process._on_line(json.dumps(auth_error))

    assert len(spawn.procs[0].stdin.lines()) == 2
    assert spawn.procs[0].stdin.lines()[0] == spawn.procs[0].stdin.lines()[1]
