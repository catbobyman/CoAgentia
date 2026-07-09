"""进程内事件总线（契约 C §1.4：事务提交后按提交序发射）。"""

from coagentia_server.events.bus import EventBus, PendingEvent

__all__ = ["EventBus", "PendingEvent"]
