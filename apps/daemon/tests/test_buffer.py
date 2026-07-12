"""遥测缓冲（契约 D §7 缓冲纪律 / §9.1）：落盘跨重启、环形溢出、重传不虚增。"""

from __future__ import annotations

from pathlib import Path

import pytest
from coagentia_contracts.daemon import (
    CheckFinishedData,
    DiagnosticEventIn,
    TokenUsageEventIn,
)
from coagentia_daemon import buffer as buffer_module
from coagentia_daemon.buffer import TelemetryBuffer
from coagentia_daemon.paths import DataPaths
from coagentia_daemon.util import new_ulid, now_iso
from helpers import usage_event


def _buf(tmp_path: Path, **kw) -> tuple[TelemetryBuffer, DataPaths]:
    p = DataPaths(tmp_path / "root")
    p.ensure_dirs()
    return TelemetryBuffer(p, **kw), p


def _usage(agent="01K5AGENT0000000000000000A") -> TokenUsageEventIn:
    return TokenUsageEventIn.model_validate(usage_event(agent))


def _check(output: str) -> CheckFinishedData:
    return CheckFinishedData(
        run_id=new_ulid(),
        node_id=new_ulid(),
        status="success",
        exit_code=0,
        output_tail=output,
    )


def test_usage_append_peek_ack(tmp_path: Path) -> None:
    buf, _ = _buf(tmp_path)
    ids = []
    for _ in range(5):
        e = _usage()
        ids.append(e.id)
        buf.append_usage(e)
    assert buf.counts().usage == 5
    peeked = buf.peek_usage(3)
    assert [e.id for e in peeked] == ids[:3]
    buf.ack_usage([ids[0], ids[2]])
    assert buf.counts().usage == 3


def test_usage_persists_across_restart(tmp_path: Path) -> None:
    buf, paths = _buf(tmp_path)
    e = _usage()
    buf.append_usage(e)
    # 新实例从 jsonl 重载（模拟 daemon 重启）。
    buf2 = TelemetryBuffer(paths)
    assert buf2.counts().usage == 1
    assert buf2.peek_usage(1)[0].id == e.id


def test_diagnostics_ack_removes_in_order(tmp_path: Path) -> None:
    buf, _ = _buf(tmp_path)
    for i in range(4):
        buf.append_diagnostic(
            DiagnosticEventIn(type="agent.command", payload={"i": i}, at=now_iso())
        )
    assert buf.counts().diagnostics == 4
    buf.ack_diagnostics(2)
    remaining = buf.peek_diagnostics(10)
    assert [e.payload["i"] for e in remaining] == [2, 3]


def test_usage_overflow_drops_oldest_and_marks(tmp_path: Path) -> None:
    buf, _ = _buf(tmp_path, usage_max=3)
    kept = []
    for _ in range(5):
        e = _usage()
        buf.append_usage(e)
        kept.append(e.id)
    # 上限 3：最旧 2 条被丢弃。
    assert buf.counts().usage == 3
    ids = [e.id for e in buf.peek_usage(10)]
    assert ids == kept[2:]
    # 溢出留痕：daemon.buffer_overflow 诊断入 diagnostics 缓冲。
    diags = buf.peek_diagnostics(10)
    assert any(d.type == "daemon.buffer_overflow" for d in diags)


def test_diagnostics_overflow_marks(tmp_path: Path) -> None:
    buf, _ = _buf(tmp_path, diagnostics_max=3)
    for i in range(5):
        buf.append_diagnostic(
            DiagnosticEventIn(type="agent.command", payload={"i": i}, at=now_iso())
        )
    assert buf.counts().diagnostics == 3
    assert any(d.type == "daemon.buffer_overflow" for d in buf.peek_diagnostics(10))


def test_retransmit_reuses_same_ulids(tmp_path: Path) -> None:
    """§11 用例 5 daemon 侧根基：peek 两次返回同一批 ULID（重传不虚增）。"""
    buf, _ = _buf(tmp_path)
    ids = []
    for _ in range(10):
        e = _usage()
        ids.append(e.id)
        buf.append_usage(e)
    first = [e.id for e in buf.peek_usage(500)]
    second = [e.id for e in buf.peek_usage(500)]  # 未 ack → 同批重发
    assert first == second == ids


def test_all_buffers_fsync_same_directory_temp_before_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """diagnostics/usage/check.finished 共用同一原子重写路径。"""
    buf, _paths = _buf(tmp_path)
    replaced: list[str] = []
    fsync_calls = 0
    real_replace = buffer_module.os.replace
    real_fsync = buffer_module.os.fsync

    def spy_replace(source: str | Path, target: str | Path) -> None:
        source_path = Path(source)
        target_path = Path(target)
        assert source_path.parent == target_path.parent
        assert source_path.name.startswith(f".{target_path.name}.")
        replaced.append(target_path.name)
        real_replace(source, target)

    def spy_fsync(fd: int) -> None:
        nonlocal fsync_calls
        fsync_calls += 1
        real_fsync(fd)

    monkeypatch.setattr(buffer_module.os, "replace", spy_replace)
    monkeypatch.setattr(buffer_module.os, "fsync", spy_fsync)
    buf.append_diagnostic(
        DiagnosticEventIn(type="agent.command", payload={"step": 1}, at=now_iso())
    )
    buf.append_usage(_usage())
    buf.append_check(_check("green"))

    assert replaced == ["diagnostics.jsonl", "usage.jsonl", "check-finished.jsonl"]
    assert fsync_calls == 3


def test_torn_check_rewrite_keeps_old_file_and_restart_can_recover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """临时文件已写一行后断裂，正式 check.finished 仍是旧完整版。"""
    buf, paths = _buf(tmp_path)
    first = _check("old-complete")
    second = _check("new-after-recovery")
    buf.append_check(first)
    official = paths.buffer_dir / "check-finished.jsonl"
    old_bytes = official.read_bytes()
    real_dumps = buffer_module.json.dumps
    dumps_calls = 0

    def torn_dumps(value: object, *args: object, **kwargs: object) -> str:
        nonlocal dumps_calls
        dumps_calls += 1
        if dumps_calls == 2:
            raise OSError("simulated torn JSONL write")
        return real_dumps(value, *args, **kwargs)

    monkeypatch.setattr(buffer_module.json, "dumps", torn_dumps)
    with pytest.raises(OSError, match="torn JSONL"):
        buf.append_check(second)

    assert dumps_calls == 2  # 临时文件先写出了旧行。
    assert official.read_bytes() == old_bytes
    restarted = TelemetryBuffer(paths)
    assert restarted.peek_checks(10) == [first]

    monkeypatch.setattr(buffer_module.json, "dumps", real_dumps)
    restarted.append_check(second)
    recovered = TelemetryBuffer(paths)
    assert recovered.peek_checks(10) == [first, second]


def test_replace_failure_keeps_old_check_file_and_later_write_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    buf, paths = _buf(tmp_path)
    first = _check("old-complete")
    second = _check("new-after-replace")
    buf.append_check(first)
    official = paths.buffer_dir / "check-finished.jsonl"
    old_bytes = official.read_bytes()
    real_replace = buffer_module.os.replace

    def fail_replace(_source: str | Path, _target: str | Path) -> None:
        raise PermissionError("simulated replace failure")

    monkeypatch.setattr(buffer_module.os, "replace", fail_replace)
    with pytest.raises(PermissionError, match="replace failure"):
        buf.append_check(second)

    assert official.read_bytes() == old_bytes
    restarted = TelemetryBuffer(paths)
    assert restarted.peek_checks(10) == [first]

    monkeypatch.setattr(buffer_module.os, "replace", real_replace)
    restarted.append_check(second)
    recovered = TelemetryBuffer(paths)
    assert recovered.peek_checks(10) == [first, second]
