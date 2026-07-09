"""遥测缓冲（契约 D §7 缓冲纪律 / §9.1 daemon/buffer/）：需 ack 类上报的离线落盘 + 重传。

两条独立缓冲：
- diagnostics.jsonl：重复可容忍（铁律 5），无客户端主键，ack 后按已发条数移除；
- usage.jsonl：以适配器 ULID 主键，exactly-once 去重根基；ack 后按 id 集合移除。

环形上限（constants.BUFFER_*）：溢出丢最旧并追加一条 daemon.buffer_overflow 诊断（丢弃计数可见）。
落盘跨 daemon 重启：append 增量写、ack/溢出重写全量。重传**不虚增**——ULID 落盘后不再生成。
"""

from __future__ import annotations

import json
from typing import Any

from coagentia_contracts.constants import (
    BUFFER_DIAGNOSTICS_MAX,
    BUFFER_USAGE_MAX,
)
from coagentia_contracts.daemon import (
    BufferedCounts,
    DiagnosticEventIn,
    TokenUsageEventIn,
)

from coagentia_daemon.paths import DataPaths
from coagentia_daemon.util import now_iso

_OVERFLOW_TYPE = "daemon.buffer_overflow"


class TelemetryBuffer:
    """diagnostics / usage 双缓冲，JSONL 落盘（契约 D §7/§9.1）。"""

    def __init__(
        self,
        paths: DataPaths,
        *,
        diagnostics_max: int = BUFFER_DIAGNOSTICS_MAX,
        usage_max: int = BUFFER_USAGE_MAX,
    ) -> None:
        self._paths = paths
        self._diag_max = diagnostics_max
        self._usage_max = usage_max
        self._diag: list[dict[str, Any]] = []
        self._usage: list[dict[str, Any]] = []
        self._dropped_diag = 0
        self._dropped_usage = 0
        self._load()

    # ---------------------------------------------------------------- 落盘装载/重写

    @property
    def _diag_path(self) -> Any:
        return self._paths.buffer_dir / "diagnostics.jsonl"

    @property
    def _usage_path(self) -> Any:
        return self._paths.buffer_dir / "usage.jsonl"

    def _load(self) -> None:
        self._paths.buffer_dir.mkdir(parents=True, exist_ok=True)
        self._diag = _read_jsonl(self._diag_path)
        self._usage = _read_jsonl(self._usage_path)

    def _rewrite_diag(self) -> None:
        _write_jsonl(self._diag_path, self._diag)

    def _rewrite_usage(self) -> None:
        _write_jsonl(self._usage_path, self._usage)

    # ---------------------------------------------------------------- 追加（含溢出处置）

    def append_diagnostic(self, event: DiagnosticEventIn) -> None:
        self._diag.append(event.model_dump(mode="json"))
        if len(self._diag) > self._diag_max:
            overflow = len(self._diag) - self._diag_max
            del self._diag[:overflow]
            self._dropped_diag += overflow
            self._append_overflow_marker("diagnostics", self._dropped_diag)
        self._rewrite_diag()

    def append_usage(self, event: TokenUsageEventIn) -> None:
        self._usage.append(event.model_dump(mode="json"))
        if len(self._usage) > self._usage_max:
            overflow = len(self._usage) - self._usage_max
            del self._usage[:overflow]
            self._dropped_usage += overflow
            # usage 溢出计入 diagnostics 缓冲（成本口径尽量不丢，但仍留痕）。
            self._append_overflow_marker("usage", self._dropped_usage)
        self._rewrite_usage()

    def _append_overflow_marker(self, buffer_name: str, dropped_total: int) -> None:
        """溢出留痕：追加一条 daemon.buffer_overflow 诊断（不再触发二次溢出判定）。"""
        marker = DiagnosticEventIn(
            type=_OVERFLOW_TYPE,
            payload={"buffer": buffer_name, "dropped_total": dropped_total},
            at=now_iso(),
        )
        self._diag.append(marker.model_dump(mode="json"))
        if len(self._diag) > self._diag_max:
            del self._diag[: len(self._diag) - self._diag_max]

    # ---------------------------------------------------------------- 读取/确认（重传语义）

    def peek_diagnostics(self, n: int) -> list[DiagnosticEventIn]:
        return [DiagnosticEventIn.model_validate(e) for e in self._diag[:n]]

    def peek_usage(self, n: int) -> list[TokenUsageEventIn]:
        return [TokenUsageEventIn.model_validate(e) for e in self._usage[:n]]

    def ack_diagnostics(self, count: int) -> None:
        """确认前 count 条已落库 → 移除（重复可容忍，按发送顺序移除）。"""
        if count <= 0:
            return
        del self._diag[:count]
        self._rewrite_diag()

    def ack_usage(self, ids: list[str]) -> None:
        """确认给定 ULID 已 exactly-once 落库 → 按 id 移除（未 ack 的保留待重传）。"""
        if not ids:
            return
        drop = set(ids)
        self._usage = [e for e in self._usage if e.get("id") not in drop]
        self._rewrite_usage()

    def counts(self) -> BufferedCounts:
        return BufferedCounts(diagnostics=len(self._diag), usage=len(self._usage))

    def has_diagnostics(self) -> bool:
        return bool(self._diag)

    def has_usage(self) -> bool:
        return bool(self._usage)


def _read_jsonl(path: Any) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _write_jsonl(path: Any, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
