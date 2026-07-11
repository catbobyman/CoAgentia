"""cron cadence 单测（B §11.5）：五段式解析/校验 + next-fire 塌缩语义 + cadence 单点分派。

时区无关：期望值一律经 `format_iso(datetime(...))`（本地壁钟 → UTC Z，与实现同一换算）构造，故在任
何机器时区下都成立——cron 字段按服务器本地时区解释，实现只在入口/出口做 UTC↔本地换算。
"""

from __future__ import annotations

from datetime import datetime

import pytest
from coagentia_server.ledger.service import format_iso
from coagentia_server.reminders import cadence, cron, interval


def L(y: int, mo: int, d: int, h: int, mi: int) -> str:
    """本地壁钟 → UTC Z 串（与 cron 实现的 naive-local→UTC 换算一致，故 tz 无关）。"""
    return format_iso(datetime(y, mo, d, h, mi))


# ---------------------------------------------------------------- 分类/校验（红绿例）


@pytest.mark.parametrize(
    ("cadence_str", "kind"),
    [
        ("PT1H", cadence.CadenceKind.INTERVAL),
        ("P1D", cadence.CadenceKind.INTERVAL),
        ("PT30M", cadence.CadenceKind.INTERVAL),
        ("0 9 * * *", cadence.CadenceKind.CRON),
        ("*/15 * * * *", cadence.CadenceKind.CRON),
        ("0 0 1 * *", cadence.CadenceKind.CRON),
        ("0 9 * * 1", cadence.CadenceKind.CRON),
        ("30 8,20 * * 1-5", cadence.CadenceKind.CRON),
        ("0 0 29 2 *", cadence.CadenceKind.CRON),  # 合法值域（虽稀疏）
        ("* * * * *", cadence.CadenceKind.CRON),
    ],
)
def test_classify_valid(cadence_str: str, kind: cadence.CadenceKind) -> None:
    assert cadence.classify(cadence_str) is kind
    assert cadence.validate(cadence_str) is kind  # validate = classify 别名（三处同门入口）


@pytest.mark.parametrize(
    "cadence_str",
    [
        "",  # 空
        "0 9 * *",  # 4 段
        "0 9 * * * *",  # 6 段
        "60 9 * * *",  # 分越界（0-59）
        "0 24 * * *",  # 时越界（0-23）
        "0 9 0 * *",  # 日越界（1-31）
        "0 9 32 * *",  # 日越界
        "0 9 * 13 *",  # 月越界（1-12）
        "0 9 * 0 *",  # 月越界
        "0 9 * * 8",  # 周越界（0-7）
        "*/0 * * * *",  # 步长 0
        "abc 9 * * *",  # 非数字
        "5-2 9 * * *",  # 逆序范围
        "0,, 9 * * *",  # 空逗号项
        "@daily",  # @keyword 不支持
        "0 9 * * * 2026",  # 年扩展不支持（6 段）
        "1h",  # 非 interval 非 cron
        "bogus",
    ],
)
def test_classify_invalid_raises(cadence_str: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 — 端点侧统一转 422
        cadence.classify(cadence_str)


def test_parse_cron_fields_and_dow_normalization() -> None:
    expr = cron.parse_cron("0,30 9-11 * * 7")
    assert expr.minutes == frozenset({0, 30})
    assert expr.hours == frozenset({9, 10, 11})
    assert 0 in expr.dows and 7 not in expr.dows  # 7 归一到 0（周日）
    assert expr.dow_restricted and not expr.dom_restricted


# ---------------------------------------------------------------- cron next-fire（塌缩/边界）


def test_cron_daily_next_same_day() -> None:
    # 每日 09:00；now 本地 08:00 → 当日 09:00。
    assert cron.next_after("0 9 * * *", L(2026, 7, 11, 8, 0)) == L(2026, 7, 11, 9, 0)


def test_cron_strictly_after_on_exact_match() -> None:
    # now 恰命中 09:00 → 严格晚于，跳到次日 09:00（不重复选中当前分钟）。
    assert cron.next_after("0 9 * * *", L(2026, 7, 11, 9, 0)) == L(2026, 7, 12, 9, 0)


def test_cron_after_passed_time_next_day() -> None:
    assert cron.next_after("0 9 * * *", L(2026, 7, 11, 9, 30)) == L(2026, 7, 12, 9, 0)


def test_cron_step_and_list_within_hour() -> None:
    assert cron.next_after("*/15 * * * *", L(2026, 7, 11, 9, 7)) == L(2026, 7, 11, 9, 15)
    assert cron.next_after("0,30 * * * *", L(2026, 7, 11, 9, 7)) == L(2026, 7, 11, 9, 30)


def test_cron_hour_range() -> None:
    assert cron.next_after("0 9-11 * * *", L(2026, 7, 11, 9, 30)) == L(2026, 7, 11, 10, 0)


def test_cron_month_boundary() -> None:
    assert cron.next_after("0 0 1 * *", L(2026, 7, 11, 10, 0)) == L(2026, 8, 1, 0, 0)


def test_cron_midnight_rollover() -> None:
    assert cron.next_after("59 23 * * *", L(2026, 7, 11, 23, 59)) == L(2026, 7, 12, 23, 59)


def test_cron_day_of_week_monday() -> None:
    # 2026-07-11 是周六；周一 0 9 * * 1 → 下个周一 2026-07-13 09:00。
    assert cron.next_after("0 9 * * 1", L(2026, 7, 11, 10, 0)) == L(2026, 7, 13, 9, 0)


@pytest.mark.parametrize("dow", ["0", "7"])
def test_cron_sunday_via_0_or_7(dow: str) -> None:
    # 周日两种写法（0 与 7）等价 → 下个周日 2026-07-12 09:00。
    assert cron.next_after(f"0 9 * * {dow}", L(2026, 7, 11, 10, 0)) == L(2026, 7, 12, 9, 0)


def test_cron_dom_dow_both_restricted_is_union() -> None:
    # Vixie 语义：日与周都受限 → 命中"日"∨"周"。0 9 13 * 5 = 13 号 或 周五。
    # 2026-07-01（周三）之后：最近命中 = 周五 07-03（早于 13 号）。
    assert cron.next_after("0 9 13 * 5", L(2026, 7, 1, 10, 0)) == L(2026, 7, 3, 9, 0)


def test_cron_leap_day() -> None:
    # 0 0 29 2 * 仅闰年 2 月 29 命中；2026-03 之后 → 2028-02-29。
    assert cron.next_after("0 0 29 2 *", L(2026, 3, 1, 0, 0)) == L(2028, 2, 29, 0, 0)


def test_cron_impossible_date_raises() -> None:
    # 2 月 30 日永不存在 → 8 年内无命中 → ValueError（防死循环，端点转 422）。
    with pytest.raises(ValueError):  # noqa: PT011
        cron.next_after("0 0 30 2 *", L(2026, 1, 1, 0, 0))


# ---------------------------------------------------------------- 塌缩语义（防重放风暴）


def test_cron_next_after_is_anchor_independent() -> None:
    # cron 命中是绝对壁钟：结果只取决于 now，与"停机漏了多少周期"无关（塌缩，不逐格回放）。
    now = L(2026, 7, 11, 8, 0)
    recent = cron.next_after("0 9 * * *", now)
    from_6mo = cadence.rearm_fire(L(2026, 1, 1, 9, 0), "0 9 * * *", now)
    from_6yr = cadence.rearm_fire(L(2020, 1, 1, 9, 0), "0 9 * * *", now)
    assert recent == from_6mo == from_6yr == L(2026, 7, 11, 9, 0)


def test_cron_missed_k_periods_fires_once() -> None:
    # 停机跨 K 个周期后单次扫描：rearm 一次塌缩到 now 之后首格（严格 > now）→ 次轮扫描不再选中。
    now = L(2026, 7, 11, 8, 30)
    stored_past_next = L(2026, 5, 1, 9, 0)  # ~70 天前应触发但停机
    rearmed = cadence.rearm_fire(stored_past_next, "0 9 * * *", now)
    assert rearmed > now  # 严格未来：本轮触发一次后不被重复选中
    assert rearmed == L(2026, 7, 11, 9, 0)  # 恰当日 09:00（首个 > now），非逐日回放


def test_cron_trigger_rearm_retrigger_cycle() -> None:
    # 建 → 首触发 → 重排 → 再触发 的完整节奏（每日 09:00）。
    created = L(2026, 7, 11, 8, 0)
    first = cadence.initial_fire(created, "0 9 * * *")
    assert first == L(2026, 7, 11, 9, 0)  # 建后当日 09:00
    # 扫描 now=09:00（到点）：触发后重排到严格晚于 now 的下一命中 = 次日 09:00。
    second = cadence.rearm_fire(first, "0 9 * * *", L(2026, 7, 11, 9, 0))
    assert second == L(2026, 7, 12, 9, 0)
    # now=次日 08:00（未到点）：next_fire 仍 > now（此轮不触发）。
    assert second > L(2026, 7, 12, 8, 0)
    # now=次日 09:30（到点）：再重排到第三日 09:00。
    third = cadence.rearm_fire(second, "0 9 * * *", L(2026, 7, 12, 9, 30))
    assert third == L(2026, 7, 13, 9, 0)


def test_cron_rearm_over_huge_gap_properties() -> None:
    # 远古锚点长停机：结果严格 > now、是合法 cron 命中、且是"首个 > now"（退一命中应 <= now）。
    now = L(2026, 7, 11, 9, 17)
    nxt = cadence.rearm_fire(L(2019, 1, 1, 0, 0), "0 * * * *", now)  # 每小时整点
    assert nxt > now
    # 是整点命中且是 now 之后首个：即 now 所在小时的下一整点。
    assert nxt == L(2026, 7, 11, 10, 0)


# -------------------------------------------------------- cadence 单点分派（interval 零回归）


def test_cadence_dispatch_interval_initial_matches_interval_module() -> None:
    created = "2026-07-11T08:00:00.000Z"
    assert cadence.initial_fire(created, "PT1H") == interval.add_interval(created, "PT1H")
    assert cadence.initial_fire(created, "PT1H") == "2026-07-11T09:00:00.000Z"


def test_cadence_dispatch_interval_rearm_matches_interval_module() -> None:
    anchor, now = "2026-07-11T08:00:00.000Z", "2026-07-11T11:30:00.000Z"
    assert cadence.rearm_fire(anchor, "PT1H", now) == interval.next_after(anchor, "PT1H", now)
    # 塌缩：漏 08→11 的多格，一次到 12:00（严格 > now）。
    assert cadence.rearm_fire(anchor, "PT1H", now) == "2026-07-11T12:00:00.000Z"


def test_cadence_dispatch_interval_classify() -> None:
    assert cadence.classify("PT1H") is cadence.CadenceKind.INTERVAL
    # interval 分支 initial_fire = 建后一个周期（非建即触发）。
    assert cadence.initial_fire("2026-07-11T08:00:00.000Z", "P1D") == "2026-07-12T08:00:00.000Z"


def test_validate_rejects_impossible_cron() -> None:
    """语法合法但组合永不匹配的 cron → validate 抛 ValueError（端点转 422，非裸抛 500）。"""
    for expr in ("0 0 30 2 *", "0 0 31 4 *", "0 0 31 6 *", "0 0 31 11 *"):
        with pytest.raises(ValueError):
            cadence.validate(expr)


def test_validate_accepts_leap_day_cron() -> None:
    """2/29（闰日）可满足——validate 通过（8 年探测窗含闰年）。"""
    assert cadence.validate("0 0 29 2 *") is cadence.CadenceKind.CRON


def test_next_after_strictly_later_utc_invariant() -> None:
    """next_after 结果 UTC 恒严格晚于 after（DST 回拨兜底不变量，B §11.5 #2 fire-once）。"""
    for after in (
        "2026-07-14T10:00:00.000Z",
        "2026-11-01T08:00:00.000Z",
        "2026-03-08T09:00:00.000Z",
        "2026-01-01T00:00:00.000Z",
    ):
        nf = cron.next_after("30 1 * * *", after)
        assert nf > after, f"{nf} not strictly > {after}"  # 同格式 ISO Z 串字典序=时序
