"""cadence 值域判定与 next-fire 计算的**唯一单点**（契约 B §11.5 #3 / §10.6，纪律 7）。

cadence 有两种表达式：interval（ISO-8601 duration，如 `PT1H`，见 interval.py）或 cron 五段式
（`分 时 日 月 周`，见 cron.py）。本模块统一判定类型并分派——三处同门（POST /reminders 端点、
run_reminder_scan 重排、LoopContractBody.cadence 一致校验）**都只调这里**，值域语义不复制。

塌缩语义两分支同构（B §11.5 #2）：interval 按锚点相位塌缩到 now 后首格；cron 直接搜绝对壁钟上
now 后首个命中。两者都保证结果**严格晚于**参照时刻，next_fire_at 不被相邻扫描重复选中。
"""

from __future__ import annotations

from enum import StrEnum

from coagentia_server.reminders import cron, interval


class CadenceKind(StrEnum):
    """cadence 表达式类型。"""

    INTERVAL = "interval"
    CRON = "cron"


_BAD = (
    "非法 cadence: {value!r}（须为 ISO-8601 duration 如 PT1H，或 cron 五段式如 `0 9 * * *`）"
)


def classify(cadence: str) -> CadenceKind:
    """判定 cadence 是 interval 还是 cron；两者皆不合法 → ValueError（端点侧转 422）。

    先试 interval（无空格、以 P 起）；失败再试 cron（五段式）。两者都不成立抛统一错。cron 段数够
    但越界/畸形时，透传 cron 的具体错误（更可诊断），而非笼统「非法 cadence」。
    """
    try:
        interval.parse_interval(cadence)
        return CadenceKind.INTERVAL
    except ValueError:
        pass
    # 五段式（按空白切）才当 cron 解析——否则给统一错，避免把 interval 笔误报成 cron 段数错。
    if len((cadence or "").split()) == 5:
        cron.parse_cron(cadence)  # 越界/畸形在此抛 cron 的具体错
        return CadenceKind.CRON
    raise ValueError(_BAD.format(value=cadence))


# 可满足性探测基准：闰年前一年，8 年窗口含 2004/2008 闰年——可满足的 2-29 命中；语法合法但组合
# 永不匹配的 2-30/4-31 等在此暴露为 ValueError（否则留到 initial_fire 处裸抛 → 端点 500）。
_PROBE_BASE = "2001-01-01T00:00:00.000Z"


def validate(cadence: str) -> CadenceKind:
    """校验 cadence 合法且**可满足**返回类型；非法/永不匹配 → ValueError。三处同门校验入口。"""
    kind = classify(cadence)
    if kind is CadenceKind.CRON:
        # cron 各字段合法但组合永不匹配（如 `0 0 30 2 *`）→ next_after 扫 8 年抛 ValueError。
        # 在 validate 单点暴露，端点转 422（否则留到 initial_fire 处未捕获 → HTTP 500）。
        cron.next_after(cadence, _PROBE_BASE)
    return kind


def initial_fire(created_iso: str, cadence: str) -> str:
    """创建时的首次触发锚点（B §10.6 #3 / §11.5 #2）。

    interval：建后**一个周期**才首触发（避免建即触发）；cron：创建时刻之后首个命中。
    """
    if classify(cadence) is CadenceKind.INTERVAL:
        return interval.add_interval(created_iso, cadence)
    return cron.next_after(cadence, created_iso)


def rearm_fire(anchor_iso: str, cadence: str, now_iso: str) -> str:
    """触发后重排 next_fire_at（run_reminder_scan，B §10.6 #3 / §11.5 #2）。

    interval：从锚点相位塌缩到 **严格晚于 now** 的下一格（next_after，保相位）；cron：now 之后
    首个绝对壁钟命中（cron 不依赖锚点相位）。两分支结果均严格 > now，不被相邻扫描复选。
    """
    if classify(cadence) is CadenceKind.INTERVAL:
        return interval.next_after(anchor_iso, cadence, now_iso)
    return cron.next_after(cadence, now_iso)
