"""D5 沉默判定纯逻辑（契约 B §10.5）：last_activity 计算 + 阈值取值 + 三态动作判定。

本模块**无副作用、不碰 DB**（纪律 7：判定单点、黄金用例守门）——输入 = 任务行相关时间戳
+ channel 阈值 + task_events 派生的提醒/升级时刻；输出 = 该发提醒 / 升级 / 静默。hub 的
`run_silence_scan` 负责取数（把 DB 行喂成 SilenceInputs）与写副作用（系统消息 / mention /
task_events / emit_activity）。

判定核心（防自激，B §10.5.2）：
- last_activity = max(status_changed_at, 锚点线程最新**非系统**消息, 最新 task_events
  **排除 reminder_sent/escalated**)——提醒系统消息与提醒留痕不计入活动，否则链条自我重置
  永不升级。取数侧已按此过滤，本模块只对已过滤的时刻取 max。
- 提醒/升级历史全在 task_events（append-only），无状态列。链条是否"仍在生效"靠
  `last_reminder_at / last_escalated_at` 是否**晚于 last_activity** 推导：一旦有真实新活动
  刷新 last_activity 越过它们，标记失效 = 整条链重置。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from coagentia_contracts.enums import TaskEventKind, TaskStatus

# 扫描对象状态（B §10.5.1：done/closed 不扫）。
SCAN_STATUSES: tuple[str, ...] = (
    TaskStatus.TODO.value,
    TaskStatus.IN_PROGRESS.value,
    TaskStatus.IN_REVIEW.value,
)

# last_activity 与 last_event 计算须排除的自激事件（B §10.5.2）。
SELF_EXCITE_EVENT_KINDS: tuple[str, ...] = (
    TaskEventKind.REMINDER_SENT.value,
    TaskEventKind.ESCALATED.value,
)


class SilenceAction(StrEnum):
    """一次扫描对单个任务的判定结果（None 表示不动作 = 静默）。"""

    REMIND = "remind"  # 第一次提醒（超阈值且当前链无生效提醒留痕）
    ESCALATE = "escalate"  # 升级（已提醒 + 开关开 + 提醒后再超一个阈值周期仍无新活动）


@dataclass(frozen=True)
class SilenceInputs:
    """decide 的全部输入（primitive，便于黄金用例单测）。

    时间戳均为 ISO-8601 UTC 毫秒 Z 串（now_iso 形状）——同格式下字典序即时序，故取数侧可用
    `func.max` 求"最新"，本模块可直接字符串比较判定链条生效性。
    """

    now: str
    threshold_h: int  # 已按状态/override 解析后的阈值小时数（见 threshold_hours）
    remind_escalation: bool
    status_changed_at: str  # NOT NULL（沉默计时锚，B §10.5）
    last_thread_msg_at: str | None  # 锚点线程最新非系统消息 created_at
    last_event_at: str | None  # 最新 task_events created_at（已排除 reminder_sent/escalated）
    last_reminder_at: str | None  # 最新 reminder_sent 事件 created_at
    last_escalated_at: str | None  # 最新 escalated 事件 created_at


def threshold_hours(
    status: str,
    *,
    silence_override_h: int | None,
    remind_todo_h: int,
    remind_inprog_h: int,
    remind_review_h: int,
) -> int:
    """按状态取阈值；silence_override_h 非空则**三态同值覆盖**（B §10.5.3 / 裁决 8）。"""
    if silence_override_h is not None:
        return silence_override_h
    if status == TaskStatus.TODO.value:
        return remind_todo_h
    if status == TaskStatus.IN_PROGRESS.value:
        return remind_inprog_h
    if status == TaskStatus.IN_REVIEW.value:
        return remind_review_h
    raise ValueError(f"不可扫描状态取阈值: {status}")  # SCAN_STATUSES 之外不该到此


def parse_iso(ts: str) -> datetime:
    """now_iso 形状（'…Z'）→ aware datetime（UTC）。"""
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def compute_last_activity(inp: SilenceInputs) -> str:
    """last_activity = max(status_changed_at, 锚点线程非系统消息, 非提醒 task_events)。

    status_changed_at NOT NULL 保底非空；其余可空的取数侧已按"非系统 / 排除提醒事件"过滤，
    本函数只对已过滤时刻取 max（字典序 = 时序）。"""
    candidates = [inp.status_changed_at]
    if inp.last_thread_msg_at is not None:
        candidates.append(inp.last_thread_msg_at)
    if inp.last_event_at is not None:
        candidates.append(inp.last_event_at)
    return max(candidates)


def decide(inp: SilenceInputs) -> SilenceAction | None:
    """三态判定（B §10.5.4/5）：返回该发的动作，None = 静默不动作。

    链条生效性推导（无状态列）：`reminded`/`escalated` 仅当对应事件时刻**晚于 last_activity**
    才算当前链的生效标记——真实新活动刷新 last_activity 越过它们即整链重置（自然回到"未提醒"）。
    """
    last_activity = compute_last_activity(inp)
    now_dt = parse_iso(inp.now)
    threshold = timedelta(hours=inp.threshold_h)

    # 当前链的生效标记：事件晚于 last_activity 才未被新活动作废。
    reminded = inp.last_reminder_at is not None and inp.last_reminder_at >= last_activity
    escalated = inp.last_escalated_at is not None and inp.last_escalated_at >= last_activity

    if escalated:
        return None  # 升级后静默（B §10.5.5）：等新活动重置整链
    if not reminded:
        # 第一次提醒：超阈值且当前链无生效 reminder_sent 留痕。
        if now_dt - parse_iso(last_activity) >= threshold:
            return SilenceAction.REMIND
        return None
    # 已提醒未升级：开关关则永不升级（B §10.5.5 前置）。
    if not inp.remind_escalation:
        return None
    # reminded ⟺ 提醒后无新活动（有则 last_activity 越过 last_reminder_at 使 reminded 失效）；
    # 故升级只需再判"最新 reminder_sent 之后再超一个阈值周期"。
    assert inp.last_reminder_at is not None  # reminded 为真必非空
    if now_dt - parse_iso(inp.last_reminder_at) >= threshold:
        return SilenceAction.ESCALATE
    return None


__all__ = [
    "SCAN_STATUSES",
    "SELF_EXCITE_EVENT_KINDS",
    "SilenceAction",
    "SilenceInputs",
    "compute_last_activity",
    "decide",
    "parse_iso",
    "threshold_hours",
]
