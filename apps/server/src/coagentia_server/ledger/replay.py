"""重放引擎骨架（契约 A §4.7 规则 3；拆解设计 §9.2）。

规则 3：批次无 `:done` 账本行 → 按 payload 序重放整批——**前段命中的 op 跳过、补齐尾段**，
`:done` 写入后才发"已落地"系统消息。幂等由 service.record 的三态保证（前段 op 已在账本 ⇒ hit ⇒
跳过处理器；尾段 op 未落 ⇒ new ⇒ 调处理器补齐）。

处理器注册表制：register(kind, handler)。M1 用测试处理器验证机制；真实 create_node/create_edge
处理器随 M6 注册。handler 签名 = (Connection, LedgerEntryRow) -> None（在 record 已写账本行后调用，
与账本同事务落地效果）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from coagentia_contracts.entities import LedgerEntryRow
from sqlalchemy.engine import Connection

from coagentia_server.ledger import service

Handler = Callable[[Connection, LedgerEntryRow], None]


@dataclass(frozen=True)
class ReplayOp:
    """一次重放的意图 op：op_id 决定幂等键，payload 是处理器输入。

    request_hash 缺省由 service.record 用 kernel.fingerprint(payload) 计算——重放意图与首次
    落地用同一 payload 即得同一指纹，故前段自然命中 hit。
    """

    op_id: str
    kind: str
    payload: dict[str, Any]
    request_hash: str | None = None


class HandlerRegistry:
    """kind → handler 注册表（每个 kind 唯一处理器）。"""

    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}

    def register(self, kind: str, handler: Handler) -> None:
        self._handlers[kind] = handler

    def get(self, kind: str) -> Handler | None:
        return self._handlers.get(kind)


# 默认注册表（进程级；真实处理器 M6 注册）。测试用独立实例避免污染。
default_registry = HandlerRegistry()


def register(kind: str, handler: Handler) -> None:
    """向默认注册表登记处理器（契约 A §4.7 重放引擎"处理器注册表制"）。"""
    default_registry.register(kind, handler)


def done_op_id(batch_kind: str, batch_id: str) -> str:
    """批次 :done 标记 op_id（decomp:<id>:done / tmpl:<id>:done / delta:<id>:done——
    契约 A §4.7 opId 前缀方案；decomp 与 constants.OPID_DECOMP_DONE 同形）。"""
    return f"{batch_kind}:{batch_id}:done"


def replay_batch(
    conn: Connection,
    batch_id: str,
    ops: list[ReplayOp],
    *,
    registry: HandlerRegistry = default_registry,
    actor: str | None = None,
) -> dict[str, Any]:
    """重放一个未 :done 的批次（§4.7 规则 3）。

    返回 {status, applied, skipped[, op_id]}：
    - status='already_done'：批次已 :done（幂等重入，无操作）；
    - status='fail_closed'  ：某 op 指纹不一致，record 已触发处置链，停止后续；
    - status='done'         ：尾段补齐 + 写 :done 标记 + mark_done。
    前段命中的 op 进 skipped（处理器不调），补齐的进 applied。
    """
    batch = service._fetch_batch(conn, batch_id)
    assert batch is not None, f"unknown batch_id: {batch_id}"

    done_oid = done_op_id(str(batch.kind), batch_id)
    if batch.done_at is not None or service._fetch_entry(conn, done_oid) is not None:
        return {"status": "already_done", "applied": [], "skipped": []}

    applied: list[str] = []
    skipped: list[str] = []
    for op in ops:
        res = service.record(
            conn,
            op.op_id,
            op.kind,
            op.payload,
            request_hash=op.request_hash,
            batch_id=batch_id,
            actor=actor,
        )
        status = res["status"]
        if status == "new":
            handler = registry.get(op.kind)
            if handler is not None:
                handler(conn, res["entry"])
            applied.append(op.op_id)
        elif status == "hit":
            skipped.append(op.op_id)  # 前段命中：跳过处理器（不重复执行）
        else:  # mismatch：record 已触发 fail-closed，停止本批次
            return {
                "status": "fail_closed",
                "applied": applied,
                "skipped": skipped,
                "op_id": op.op_id,
            }

    # 尾段补齐后写 :done 标记 + 落 done_at（"已落地"系统消息只在 :done 后发出——M6 接线）。
    service.record(
        conn, done_oid, "mark_done", {"batch_id": batch_id}, batch_id=batch_id, actor=actor
    )
    service.mark_done(conn, batch_id)
    return {"status": "done", "applied": applied, "skipped": skipped}
