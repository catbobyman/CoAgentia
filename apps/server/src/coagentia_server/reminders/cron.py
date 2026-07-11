"""cron 五段式 cadence 的解析与 next-fire 计算（契约 B §11.5）。

cadence 的第二种表达式（interval 之外）：cron 五段式 `分 时 日 月 周`，**服务器本地时区**解释；
**不支持**秒/年扩展与 `@keyword` 别名（B §11.5 #1）。每段支持 `*`、数字、逗号列表、范围 `a-b`、
步长 `*/n`·`a-b/n`·`a/n`（`a/n` 按 Vixie 语义 = `a-max/n`）。

`next_after(cadence, after_iso)` = "after 之后**首个**命中时刻"（塌缩语义，与 interval `next_after`
同——停机漏拍不逐格重放，B §11.5 #2）。cron 命中点是**绝对壁钟**（不依赖锚点相位），故直接正向搜
到首个 > after 的命中，无逐周期回放之虞。

时区处理（避免 DST 歧义，任务书建议 naive local）：命中判定在 **naive 本地壁钟**上做（cron 语义即
壁钟语义），仅在入口把 UTC 的 after 转本地、出口把本地命中转回 UTC（`format_iso` 对 naive 按本地解
释→UTC），DST 换算交给系统 tz 规则，cron 字段算术不碰偏移。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from coagentia_server.ledger.service import format_iso

# 各字段值域（闭区间）：分 时 日 月 周。周允许 0..7（0 与 7 同为周日，见 _parse_field 归一）。
_BOUNDS: tuple[tuple[int, int], ...] = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))

_BAD = (
    "非法 cron cadence: {value!r}（须五段式 `分 时 日 月 周`；"
    "支持 * 数字 逗号 范围 步长，无秒/年/@别名）"
)


@dataclass(frozen=True)
class CronExpr:
    """解析后的 cron 表达式：五个允许值集合 + 日/周是否受限（Vixie 日∨周语义所需）。"""

    minutes: frozenset[int]
    hours: frozenset[int]
    doms: frozenset[int]
    months: frozenset[int]
    dows: frozenset[int]
    dom_restricted: bool  # 日字段非 '*'
    dow_restricted: bool  # 周字段非 '*'


def _parse_term(term: str, lo: int, hi: int, raw: str) -> set[int]:
    """单个逗号项 → 值集合：`*`、`a`、`a-b`、`*/n`、`a-b/n`、`a/n`（Vixie：a..hi 步 n）。"""
    has_step = "/" in term
    base, _, step_s = term.partition("/")
    step = 1
    if has_step:
        if not step_s.isdigit() or int(step_s) < 1:
            raise ValueError(_BAD.format(value=raw))
        step = int(step_s)
    if base == "*":
        start, end = lo, hi
    elif "-" in base:
        a_s, _, b_s = base.partition("-")
        if not (a_s.isdigit() and b_s.isdigit()):
            raise ValueError(_BAD.format(value=raw))
        start, end = int(a_s), int(b_s)
    elif base.isdigit():
        start = int(base)
        # `a/n` = a..hi 步 n（Vixie 扩展）；裸 `a` = 单值。
        end = hi if has_step else start
    else:
        raise ValueError(_BAD.format(value=raw))
    if start < lo or end > hi or start > end:
        raise ValueError(_BAD.format(value=raw))
    return set(range(start, end + 1, step))


def _parse_field(field: str, lo: int, hi: int, raw: str) -> frozenset[int]:
    """逗号列表 → 各项并集；空项/空字段非法。"""
    if field == "":
        raise ValueError(_BAD.format(value=raw))
    values: set[int] = set()
    for term in field.split(","):
        if term == "":
            raise ValueError(_BAD.format(value=raw))
        values |= _parse_term(term, lo, hi, raw)
    return frozenset(values)


def parse_cron(cadence: str) -> CronExpr:
    """cron 五段式 → CronExpr（值域唯一解析点）。段数≠5 或任一段越界/畸形 → ValueError。"""
    parts = (cadence or "").split()
    if len(parts) != 5:
        raise ValueError(_BAD.format(value=cadence))
    minutes, hours, doms, months, dows_raw = (
        _parse_field(parts[i], _BOUNDS[i][0], _BOUNDS[i][1], cadence) for i in range(5)
    )
    # 周：cron 0 与 7 均为周日，归一到 0（后续用 (weekday()+1)%7 对齐）。
    dows = frozenset(0 if d == 7 else d for d in dows_raw)
    return CronExpr(
        minutes=minutes,
        hours=hours,
        doms=doms,
        months=months,
        dows=dows,
        dom_restricted=parts[2] != "*",
        dow_restricted=parts[4] != "*",
    )


def _day_ok(dt: datetime, expr: CronExpr) -> bool:
    """日期是否命中：日、周字段的 Vixie 语义——两者都受限时取**并**，否则取交（`*` 恒真）。"""
    dom_ok = dt.day in expr.doms
    cron_dow = (dt.weekday() + 1) % 7  # Python 周一=0..周日=6 → cron 周日=0..周六=6
    dow_ok = cron_dow in expr.dows
    if expr.dom_restricted and expr.dow_restricted:
        return dom_ok or dow_ok
    return dom_ok and dow_ok


def _first_of_next_month(dt: datetime) -> datetime:
    if dt.month == 12:
        return dt.replace(year=dt.year + 1, month=1, day=1, hour=0, minute=0)
    return dt.replace(month=dt.month + 1, day=1, hour=0, minute=0)


def _next_day_midnight(dt: datetime) -> datetime:
    return (dt + timedelta(days=1)).replace(hour=0, minute=0)


def _next_local(expr: CronExpr, after_local: datetime) -> datetime:
    """naive 本地壁钟上：**严格晚于 after_local** 的首个 cron 命中（字段跳跃搜索，O(命中距离)）。"""
    # 下取整到分再 +1 分：保证严格 > after（after 恰在整分时跳过该分，否则也落到其后首分）。
    cand = after_local.replace(second=0, microsecond=0) + timedelta(minutes=1)
    year_cap = cand.year + 8  # 防御上限：不存在的日期组合（如 2 月 30 日）不至死循环
    while cand.year <= year_cap:
        if cand.month not in expr.months:
            cand = _first_of_next_month(cand)
            continue
        if not _day_ok(cand, expr):
            cand = _next_day_midnight(cand)
            continue
        if cand.hour not in expr.hours:
            cand = cand.replace(minute=0) + timedelta(hours=1)  # 跳到下一小时初
            continue
        if cand.minute not in expr.minutes:
            cand = cand + timedelta(minutes=1)
            continue
        return cand
    raise ValueError(
        f"cron cadence 在 8 年内无命中（可能是不存在的日期组合）: {cand!r}"
    )


def _parse_utc(iso_ts: str) -> datetime:
    base = datetime.fromisoformat(iso_ts)  # 3.11+ 原生吃末尾 'Z'
    if base.tzinfo is None:  # 防御：无时区串按 UTC 解释
        base = base.replace(tzinfo=UTC)
    return base


def next_after(cadence: str, after_iso: str) -> str:
    """`after_iso`（UTC Z）之后首个 cron 命中 → 同格式 UTC Z 串（塌缩语义）。

    创建时（after=创建时刻）与 run_reminder_scan 重排（after=now）共用——cron 命中绝对壁钟，两处
    语义一致（"其后首个命中"），无需锚点相位。
    """
    expr = parse_cron(cadence)
    after_local = _parse_utc(after_iso).astimezone().replace(tzinfo=None)  # UTC → naive 本地壁钟
    match_local = _next_local(expr, after_local)
    return format_iso(match_local)  # naive 本地 → UTC Z（format_iso 对 naive 按本地解释）
