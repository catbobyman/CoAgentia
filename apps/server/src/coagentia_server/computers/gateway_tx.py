"""daemon 网关侧短事务 + 提交后事件发射（复用 deps.get_tx 的纪律，契约 A §1 / 契约 C §1.4）。

REST 端点在线程池跑，用 deps.get_tx；daemon 网关在事件 loop 跑，用本模块。两者同构：
每次开一 Connection + 一事务，成功 commit 后按序 bus.emit（浏览器事件在事务后发射）。

网关运行于 loop 线程，bus.emit → WsHub 订阅回调用 loop.call_soon_threadsafe 投队列，
在同一 loop 内亦成立（call_soon_threadsafe 对本 loop 合法）。
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

from sqlalchemy.engine import Connection, Engine

from coagentia_server.events import EventBus


class GatewayTx:
    """网关事务上下文：Connection + 待发浏览器事件（提交后 flush）。"""

    def __init__(self, conn: Connection) -> None:
        self.conn = conn
        self.pending: list[tuple[Any, str | None, dict[str, Any]]] = []

    def emit(self, etype: Any, channel_id: str | None, data: dict[str, Any]) -> None:
        self.pending.append((etype, channel_id, data))


@contextlib.contextmanager
def gateway_tx(engine: Engine, bus: EventBus) -> Iterator[GatewayTx]:
    """开短事务 → yield → 成功 commit 后按序 bus.emit；异常回滚不发射。"""
    conn = engine.connect()
    txn = conn.begin()
    tx = GatewayTx(conn)
    try:
        yield tx
    except BaseException:
        txn.rollback()
        conn.close()
        raise
    else:
        txn.commit()
        conn.close()
        for etype, channel_id, data in tx.pending:
            bus.emit(etype, channel_id, data)
