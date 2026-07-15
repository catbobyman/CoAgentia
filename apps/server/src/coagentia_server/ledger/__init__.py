"""ledger 账本模块（契约 A §4.7）：通用幂等账本 + 落地批次管理 + 重放引擎骨架。

- service：record 三态幂等 · create_batch/mark_done/mark_fail_closed · fail-closed 处置链。
- replay ：register(kind, handler) 注册表 + replay_batch（前段跳过、补齐尾段、写 :done）。
"""

from coagentia_server.ledger import replay, service

__all__ = ["replay", "service"]
