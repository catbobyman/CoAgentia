"""浏览器 WS 传输层（契约 C）：消费 A3 事件总线，向各连接广播载状态事件。

Hub 在 lifespan 启动时捕获运行 loop、订阅 EventBus、起单一消费任务（坑 3：消费任务挂
lifespan，不在 handler 内 create_task）。写端点在 DB 提交后经 bus.emit 发射（deps.get_tx，
契约 C §1.4 事务后发射），emit 在 FastAPI 线程池线程执行 → call_soon_threadsafe 跨回 loop。
"""

from coagentia_server.ws.hub import Connection, WsHub

__all__ = ["Connection", "WsHub"]
