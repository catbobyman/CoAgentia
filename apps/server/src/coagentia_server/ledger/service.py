"""通用幂等账本服务层（契约 A §4.7；03 §3.2 基础设施，非拆解私产）。

三态幂等（§4.7 重放与 fail-closed 规则 1/2）：
- 新键                → 写 ledger_entries 行，返回 {status:'new'}；
- 同键 同 request_hash → 跳过写入，返回 {status:'hit', entry:原行}（幂等命中返回原结果）；
- 同键 异 request_hash → {status:'mismatch'}，触发 fail-closed 处置链（规则 2）。

`UNIQUE(op_id)` 是并发防线：预查未命中后仍可能被并发写入抢先，INSERT 触发 IntegrityError，
用 SAVEPOINT 回退该 INSERT 后重查，退化为 hit/mismatch 判定。

纪律（契约 A §8.1）：枚举/诊断类型串一律取自 packages/contracts，不重复定义字面量；
request_hash 用 kernel.fingerprint（契约 A §2 规范序列化）。
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from typing import Any

from coagentia_contracts.constants import DIAGNOSTIC_TYPES
from coagentia_contracts.entities import LandingBatchRow, LedgerEntryRow
from coagentia_contracts.enums import (
    CardKind,
    LandingBatchKind,
    LandingBatchStatus,
    MessageKind,
)
from coagentia_contracts.kernel.fingerprint import fingerprint
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from coagentia_server.db import models

# landing.fail_closed 是诊断类型（契约 A §4.6 命名空间；constants.DIAGNOSTIC_TYPES 为权威登记表）。
# type 列是自由文本，此处绑定契约常量以拒绝拼写漂移（若契约删除该类型，导入即失败）。
_DIAG_FAIL_CLOSED = "landing.fail_closed"
assert _DIAG_FAIL_CLOSED in DIAGNOSTIC_TYPES

# fail-closed 告警卡消息正文（UI §4.7：卡片 = 不可变锚点，活状态从 landing_batches 行读）。
_FAIL_CLOSED_CARD_BODY = "落地批次 fail-closed：请求指纹与账本记录不一致，已停止本批次后续操作。"

_LEDGER = models.LedgerEntry.__table__
_BATCH = models.LandingBatch.__table__
_DIAG = models.DiagnosticEvent.__table__
_MSG = models.Message.__table__

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford Base32（天然排除 I/L/O/U）


def new_ulid() -> str:
    """26 字符 Crockford Base32 大写 ULID（48-bit 毫秒时间 + 80-bit 随机；契约 A §1）。"""
    value = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), "big")
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def now_iso() -> str:
    """ISO-8601 UTC 毫秒 Z 字符串（契约 A §1 时间戳；与 TimestampZ 形状一致）。"""
    dt = datetime.now(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


# ---------------------------------------------------------------- 行读取


def _fetch_entry(conn: Connection, op_id: str) -> LedgerEntryRow | None:
    row = conn.execute(select(_LEDGER).where(_LEDGER.c.op_id == op_id)).mappings().first()
    return LedgerEntryRow(**row) if row is not None else None


def _fetch_batch(conn: Connection, batch_id: str) -> LandingBatchRow | None:
    row = conn.execute(select(_BATCH).where(_BATCH.c.id == batch_id)).mappings().first()
    return LandingBatchRow(**row) if row is not None else None


# ---------------------------------------------------------------- 账本 record（三态）


def record(
    conn: Connection,
    op_id: str,
    kind: str,
    payload: dict[str, Any],
    *,
    request_hash: str | None = None,
    batch_id: str | None = None,
    actor: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """幂等写入账本，返回三态结果 {status, entry?}。

    request_hash 缺省用 kernel.fingerprint(payload)（契约 A §2）；调用方也可显式传入。
    mismatch 且 batch_id 非空时触发 fail-closed 处置链（§4.7 规则 2）。
    """
    if request_hash is None:
        request_hash = fingerprint(payload)

    existing = _fetch_entry(conn, op_id)
    if existing is not None:
        return _classify(conn, existing, request_hash, batch_id)

    try:
        # SAVEPOINT 包裹 INSERT：并发抢先时 IntegrityError 只回退本 INSERT，外层事务仍可用。
        with conn.begin_nested():
            conn.execute(
                insert(_LEDGER).values(
                    op_id=op_id,
                    request_hash=request_hash,
                    batch_id=batch_id,
                    actor_member_id=actor,
                    kind=kind,
                    payload=payload,
                    created_at=created_at or now_iso(),
                )
            )
    except IntegrityError:
        existing = _fetch_entry(conn, op_id)
        assert existing is not None  # UNIQUE(op_id) 冲突 ⇒ 必有既存行
        return _classify(conn, existing, request_hash, batch_id)

    entry = _fetch_entry(conn, op_id)
    assert entry is not None
    return {"status": "new", "entry": entry}


def _classify(
    conn: Connection,
    existing: LedgerEntryRow,
    request_hash: str,
    batch_id: str | None,
) -> dict[str, Any]:
    """既存行的指纹判定：同指纹 → hit（返回原结果）；异指纹 → mismatch + fail-closed。"""
    if existing.request_hash == request_hash:
        return {"status": "hit", "entry": existing}
    if batch_id is not None:
        mark_fail_closed(conn, batch_id, reason="request_hash mismatch")
    return {"status": "mismatch", "entry": existing}


# ---------------------------------------------------------------- landing_batches 管理


def create_batch(
    conn: Connection,
    *,
    workspace_id: str,
    channel_id: str,
    kind: LandingBatchKind | str,
    content_hash: str,
    source_ref: str,
    confirmed_by: str,
    batch_id: str | None = None,
    created_at: str | None = None,
) -> LandingBatchRow:
    """建 running 批次（幂等键命名空间锚，§4.7）。batch_id 缺省自动生成 ULID。"""
    bid = batch_id or new_ulid()
    conn.execute(
        insert(_BATCH).values(
            id=bid,
            workspace_id=workspace_id,
            channel_id=channel_id,
            kind=LandingBatchKind(kind),
            content_hash=content_hash,
            source_ref=source_ref,
            confirmed_by=confirmed_by,
            status=LandingBatchStatus.RUNNING,
            created_at=created_at or now_iso(),
            done_at=None,
        )
    )
    batch = _fetch_batch(conn, bid)
    assert batch is not None
    return batch


def mark_done(conn: Connection, batch_id: str, *, done_at: str | None = None) -> None:
    """写 done_at = 批次 :done 事实源（S4）+ status='done'。"""
    conn.execute(
        update(_BATCH)
        .where(_BATCH.c.id == batch_id)
        .values(status=LandingBatchStatus.DONE, done_at=done_at or now_iso())
    )


def mark_fail_closed(conn: Connection, batch_id: str, *, reason: str | None = None) -> None:
    """fail-closed 处置链（契约 A §4.7 规则 2，M1 可达部分）：

    1. landing_batches.status='fail_closed'；
    2. 写 diagnostic_events(type='landing.fail_closed', batch_id=…)；
    3. 向 source 线程发 fail-closed 告警卡（messages: card_kind='fail_closed',
       card_ref=batch_id, author=NULL 系统消息）。

    activity_items(kind='fail_closed') 置顶与铃声（notif_sound）属 M2 表——见下方 TODO 接缝。
    """
    batch = _fetch_batch(conn, batch_id)
    assert batch is not None, f"unknown batch_id: {batch_id}"

    # 1. 批次置 fail_closed（landing_batches 非不可变表，UPDATE 合法）。
    conn.execute(
        update(_BATCH)
        .where(_BATCH.c.id == batch_id)
        .values(status=LandingBatchStatus.FAIL_CLOSED)
    )

    # 2. 诊断留痕（按 batch 过滤 = fail-closed 卡"查看诊断"入口，§4.6 索引）。
    conn.execute(
        insert(_DIAG).values(
            workspace_id=batch.workspace_id,
            agent_member_id=None,
            type=_DIAG_FAIL_CLOSED,
            channel_id=batch.channel_id,
            task_id=None,
            batch_id=batch_id,
            payload={"reason": reason or "request_hash mismatch"},
            created_at=now_iso(),
        )
    )

    # 3. source 线程 fail-closed 告警卡（系统消息，author=NULL；thread_root_id 未知则落频道级）。
    conn.execute(
        insert(_MSG).values(
            id=new_ulid(),
            workspace_id=batch.workspace_id,
            channel_id=batch.channel_id,
            thread_root_id=None,
            author_member_id=None,
            kind=MessageKind.SYSTEM,
            card_kind=CardKind.FAIL_CLOSED,
            card_ref=batch_id,
            body=_FAIL_CLOSED_CARD_BODY,
            created_at=now_iso(),
        )
    )

    # TODO(M2): activity_items(kind='fail_closed') 置顶 + 按 workspaces.notif_sound 铃声
    #   （契约 A §4.7 规则 2 尾段）。activity_items 是 M2 表，此处留接缝，不实现、不建表。
