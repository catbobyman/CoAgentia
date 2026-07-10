"""interval util 单测（B §10.6）：ISO-8601 duration 解析 + next_fire_at 推进。"""

from __future__ import annotations

from datetime import timedelta

import pytest
from coagentia_server.reminders import interval


@pytest.mark.parametrize(
    ("cadence", "expected"),
    [
        ("PT1H", timedelta(hours=1)),
        ("PT30M", timedelta(minutes=30)),
        ("PT45S", timedelta(seconds=45)),
        ("P1D", timedelta(days=1)),
        ("PT1H30M", timedelta(hours=1, minutes=30)),
        ("P2DT3H4M5S", timedelta(days=2, hours=3, minutes=4, seconds=5)),
    ],
)
def test_parse_interval_valid(cadence: str, expected: timedelta) -> None:
    assert interval.parse_interval(cadence) == expected


@pytest.mark.parametrize(
    "cadence",
    [
        "0 9 * * *",  # cron 归 M5+，非 interval
        "1h",  # 非 ISO-8601
        "P",  # 无分量
        "PT",  # 无分量
        "PT0S",  # 零 → 非正
        "P1M",  # 月（非定长，不支持）
        "P1Y",  # 年（非定长，不支持）
        "P1W",  # 周（未纳入子集）
        "",  # 空
        "PT1H30",  # 缺单位
    ],
)
def test_parse_interval_invalid_raises(cadence: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 — 端点侧统一转 422
        interval.parse_interval(cadence)


def test_add_interval_advances_and_keeps_format() -> None:
    # 毫秒 + Z 形状保持（与 now_iso 一致）。
    assert interval.add_interval("2026-07-10T09:00:00.000Z", "PT1H") == "2026-07-10T10:00:00.000Z"
    assert interval.add_interval("2026-07-10T09:00:00.000Z", "P1D") == "2026-07-11T09:00:00.000Z"


def test_add_interval_crosses_day_boundary() -> None:
    assert interval.add_interval("2026-07-10T23:30:00.000Z", "PT1H") == "2026-07-11T00:30:00.000Z"


def test_add_interval_repeated_composes() -> None:
    ts = "2026-07-10T09:00:00.000Z"
    for _ in range(3):
        ts = interval.add_interval(ts, "PT2H")
    assert ts == "2026-07-10T15:00:00.000Z"  # +6h


def test_next_after_advances_one_step_when_barely_due() -> None:
    # anchor == now → 推进恰一格（k=1，严格 > now）。
    assert (
        interval.next_after("2026-07-10T09:00:00.000Z", "PT1H", "2026-07-10T09:00:00.000Z")
        == "2026-07-10T10:00:00.000Z"
    )


def test_next_after_collapses_missed_intervals_in_one_step() -> None:
    # 停机漏掉多个周期：一次塌缩到未来的下一格，而非逐格（防风暴）。
    # anchor 09:00 + PT1H 网格，now=11:30 → 下一格 12:00（跨过 10/11/12 中首个 > now 的）。
    assert (
        interval.next_after("2026-07-10T09:00:00.000Z", "PT1H", "2026-07-10T11:30:00.000Z")
        == "2026-07-10T12:00:00.000Z"
    )


def test_next_after_preserves_grid_phase_over_huge_gap() -> None:
    # 远古 anchor（长停机）→ 结果严格 > now、落 anchor+k*interval 网格、且是首个 > now 的格。
    from datetime import datetime, timedelta

    anchor = "2020-01-01T00:00:00.000Z"
    now = "2026-07-10T09:17:00.000Z"
    nxt = interval.next_after(anchor, "PT1H", now)
    assert nxt > now  # 严格未来（不再 <= now，故次轮扫描不重复选中）
    base = datetime.fromisoformat(anchor)
    nxt_dt = datetime.fromisoformat(nxt)
    assert (nxt_dt - base).total_seconds() % 3600 == 0  # 不丢相位
    # 是"下一个">now 的格：退一格应 <= now（否则塌缩过头）。
    assert nxt_dt - timedelta(hours=1) <= datetime.fromisoformat(now)
