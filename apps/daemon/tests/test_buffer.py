"""遥测缓冲（契约 D §7 缓冲纪律 / §9.1）：落盘跨重启、环形溢出、重传不虚增。"""

from __future__ import annotations

from pathlib import Path

from coagentia_contracts.daemon import DiagnosticEventIn, TokenUsageEventIn
from coagentia_daemon.buffer import TelemetryBuffer
from coagentia_daemon.paths import DataPaths
from coagentia_daemon.util import now_iso
from helpers import usage_event


def _buf(tmp_path: Path, **kw) -> tuple[TelemetryBuffer, DataPaths]:
    p = DataPaths(tmp_path / "root")
    p.ensure_dirs()
    return TelemetryBuffer(p, **kw), p


def _usage(agent="01K5AGENT0000000000000000A") -> TokenUsageEventIn:
    return TokenUsageEventIn.model_validate(usage_event(agent))


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
