"""进程内 pub/sub 事件总线（契约 C §1.4）。

铁律（契约 C §1.4）：事件在其 DB 事务**提交后、按提交顺序**从单一进程内事件总线发出。
本模块只负责"待广播事件"的缓冲与分发——`seq` 由 WS 连接侧按连接赋值（A4），
`Envelope` 组装同样在传输层完成。写端点在 DB commit 之后调用 `emit`（见 deps.Tx）。

订阅者是**同步**回调（`PendingEvent -> None`）：A3 侧订阅者是测试收集器；A4 的 WS 传输
注册一个把事件投递到各连接异步队列的线程安全订阅者。emit 本身同步、可从线程池调用
（sync 端点在 FastAPI 线程池执行），故此处不 create_task（避坑 3）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from coagentia_contracts.ws import EventType


@dataclass(frozen=True)
class PendingEvent:
    """一条待广播事件：类型 + 作用域（channel_id）+ payload（*Public 形状的 dict）。"""

    type: EventType
    channel_id: str | None
    data: dict[str, Any]


Subscriber = Callable[[PendingEvent], None]


class EventBus:
    """单进程 pub/sub：写端点 emit → 分发给全部订阅者。"""

    def __init__(self) -> None:
        self._subs: dict[int, Subscriber] = {}
        self._next_token = 0

    def subscribe(self, callback: Subscriber) -> int:
        """登记订阅者，返回退订令牌。"""
        token = self._next_token
        self._next_token += 1
        self._subs[token] = callback
        return token

    def unsubscribe(self, token: int) -> None:
        self._subs.pop(token, None)

    def emit(self, etype: EventType, channel_id: str | None, data: dict[str, Any]) -> None:
        """发射一条事件（提交后调用）。分发失败的订阅者被丢弃，不影响其它订阅者。"""
        event = PendingEvent(type=etype, channel_id=channel_id, data=data)
        for token, callback in list(self._subs.items()):
            try:
                callback(event)
            except Exception:  # noqa: BLE001 — 单个订阅者故障不得阻断广播
                self._subs.pop(token, None)
