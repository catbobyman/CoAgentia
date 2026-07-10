"""循环 Reminder cadence 的 ISO-8601 duration（interval）解析与时间戳推进（契约 B §10.6）。

MVP **仅支持 interval 表达式**（ISO-8601 duration，如 `PT1H`/`P1D`/`PT30M`）；cron 归 M5+（解析
与时区面大，MVP 不背）。纯函数：`parse_interval` 供 create_reminder 创建时校验（非法 → ValueError
→ 端点转 422 VALIDATION_FAILED）；`add_interval` 供创建时算首次触发锚点；`next_after` 供
run_reminder_scan 触发后**塌缩式**重排 next_fire_at（跨越停机漏掉的周期一次性追平到未来）。

年/月/周分量**不支持**：其长度随基准月份/年份浮动（非定长），与"每 X 触发一次"的固定周期语义
相悖——interval reminder 的 cadence 必须是可加的定长 timedelta。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from coagentia_server.ledger.service import format_iso

# ISO-8601 duration 子集：P[nD]T[nH][nM][nS]。`M` 只出现在 T 之后 = 分钟（非月）；日期段仅 D。
_DURATION_RE = re.compile(
    r"^P"
    r"(?:(?P<days>\d+)D)?"
    r"(?:T"
    r"(?:(?P<hours>\d+)H)?"
    r"(?:(?P<minutes>\d+)M)?"
    r"(?:(?P<seconds>\d+)S)?"
    r")?$"
)

_BAD = "非法 interval cadence: {value!r}（MVP 仅支持 ISO-8601 duration，如 PT1H；cron 归 M5+）"


def parse_interval(cadence: str) -> timedelta:
    """ISO-8601 duration（定长子集）→ 正 timedelta。非法/零/缺分量 → ValueError。"""
    match = _DURATION_RE.match(cadence or "")
    if match is None:
        raise ValueError(_BAD.format(value=cadence))
    parts = {name: int(raw) for name, raw in match.groupdict().items() if raw is not None}
    if not parts:  # 裸 'P' / 'PT'：无任何时间分量
        raise ValueError(_BAD.format(value=cadence))
    delta = timedelta(
        days=parts.get("days", 0),
        hours=parts.get("hours", 0),
        minutes=parts.get("minutes", 0),
        seconds=parts.get("seconds", 0),
    )
    if delta <= timedelta(0):  # 全零分量（如 PT0S）
        raise ValueError(_BAD.format(value=cadence))
    return delta


def _parse(iso_ts: str) -> datetime:
    base = datetime.fromisoformat(iso_ts)  # 3.11+ 原生吃末尾 'Z'
    if base.tzinfo is None:  # 防御：无时区串按 UTC 解释
        base = base.replace(tzinfo=UTC)
    return base


def add_interval(iso_ts: str, cadence: str) -> str:
    """`iso_ts` + 一个 interval → 同格式 Z 串。

    创建 recurring 时算首触发锚点：建后一个周期才首次触发（非建即触发）。
    """
    return format_iso(_parse(iso_ts) + parse_interval(cadence))


def next_after(anchor_iso: str, cadence: str, now: str) -> str:
    """从 `anchor_iso` 起、对齐 cadence 网格、**严格晚于 now** 的下一个触发点（k≥1），O(1) 直接算。

    塌缩式重排：reminder 若因停机漏掉 N 个周期，一次追平到未来的下一格，而非每次 +1 interval
    反复触发风暴（code-review 修：`while next<=now` 逐格对短 cadence + 长停机退化为海量迭代/洪泛）。
    触发后至少推进一格（k≥1）保证 next_fire_at 严格 > now，不被同轮/相邻扫描重复选中。
    """
    delta = parse_interval(cadence)
    base = _parse(anchor_iso)
    now_dt = _parse(now)
    elapsed = now_dt - base
    steps = elapsed // delta + 1 if elapsed >= timedelta(0) else 1
    return format_iso(base + steps * delta)
