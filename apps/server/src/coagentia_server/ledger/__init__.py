"""ledger 账本模块（契约 A §4.7）：通用幂等账本（基础设施）。

- service：record 三态幂等 · create_batch/mark_done/mark_fail_closed · fail-closed 处置链。

DEDAG 批（2026-07-18）：replay 重放引擎随落地事务器退役；service 为全仓基建保留。
"""

from coagentia_server.ledger import service

__all__ = ["service"]
