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
    ActivityKind,
    CardKind,
    LandingBatchKind,
    LandingBatchStatus,
    MemberKind,
    MessageKind,
)
from coagentia_contracts.kernel.fingerprint import fingerprint
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError

from coagentia_server.db import models

# landing.fail_closed 是诊断类型（契约 A §4.6 命名空间；constants.DIAGNOSTIC_TYPES 为权威登记表）。
# type 列是自由文本，此处绑定契约常量以拒绝拼写漂移（若契约删除该类型，导入即失败）。
_DIAG_FAIL_CLOSED = "landing.fail_closed"
assert _DIAG_FAIL_CLOSED in DIAGNOSTIC_TYPES

# fail-closed 告警卡消息正文（UI §4.7：卡片 = 不可变锚点，活状态从 landing_batches 行读）。
_FAIL_CLOSED_CARD_BODY = "落地批次 fail-closed：请求指纹与账本记录不一致，已停止本批次后续操作。"


class LedgerFailClosed(Exception):
    """回滚路径 fail-closed 信号（携批元数据）：REST 落地事务器中途命中同键异指纹时抛出——
    不 inline 写（会随外层回滚撤销），由 app 层异常处理器于 get_tx 回滚**之后**调
    persist_fail_closed 独立落盘。"""

    def __init__(self, batch: LandingBatchRow, *, reason: str | None = None) -> None:
        super().__init__(reason or "request_hash mismatch")
        self.batch = batch
        self.reason = reason

_LEDGER = models.tbl(models.LedgerEntry)
_BATCH = models.tbl(models.LandingBatch)
_DIAG = models.tbl(models.DiagnosticEvent)
_MSG = models.tbl(models.Message)
_ACTIVITY = models.tbl(models.ActivityItem)
_MEMBER = models.tbl(models.Member)
_CHANNEL_MEMBER = models.tbl(models.ChannelMember)

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford Base32（天然排除 I/L/O/U）


def new_ulid() -> str:
    """26 字符 Crockford Base32 大写 ULID（48-bit 毫秒时间 + 80-bit 随机；契约 A §1）。"""
    value = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), "big")
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def format_iso(dt: datetime) -> str:
    """datetime → ISO-8601 UTC 毫秒 Z 字符串（契约 A §1 / TimestampZ 形状的唯一格式化点）。"""
    dt = dt.astimezone(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def now_iso() -> str:
    """ISO-8601 UTC 毫秒 Z 字符串（契约 A §1 时间戳；与 TimestampZ 形状一致）。"""
    return format_iso(datetime.now(UTC))


# ---------------------------------------------------------------- 行读取


def _fetch_entry(conn: Connection, op_id: str) -> LedgerEntryRow | None:
    row = conn.execute(select(_LEDGER).where(_LEDGER.c.op_id == op_id)).mappings().first()
    return LedgerEntryRow(**row) if row is not None else None


def _fetch_batch(conn: Connection, batch_id: str) -> LandingBatchRow | None:
    row = conn.execute(select(_BATCH).where(_BATCH.c.id == batch_id)).mappings().first()
    return LandingBatchRow(**row) if row is not None else None


def batch_node_task_ids(conn: Connection, batch_id: str) -> list[str]:
    """回收该落地批 `create_node` 账本行携带的 task_id（按落地顺序）。

    模板实例化幂等重放重建 InstantiateResult 用（reserve-before 语义下 REST op_id 只记 batch_id，
    task_ids 从已落库的逐节点账本行派生——见 routes/templates._reconstruct_from_ledger）。

    按 `seq`（自增 PK = 落库顺序）排序 → 与首次 201 的 body.nodes 落地顺序一致；勿按
    (created_at, op_id)：同毫秒时 op_id=tmpl:<batch>:<node_key> 的字典序会打乱顺序（n10<n2、
    语义键乱序），令同键重放与首次响应的 tasks 顺序相异，违「同键同响应」。
    """
    rows = (
        conn.execute(
            select(_LEDGER.c.payload)
            .where(_LEDGER.c.batch_id == batch_id, _LEDGER.c.kind == "create_node")
            .order_by(_LEDGER.c.seq)
        )
        .scalars()
        .all()
    )
    return [p["task_id"] for p in rows if isinstance(p, dict) and "task_id" in p]


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


def lookup(conn: Connection, op_id: str, request_hash: str) -> dict[str, Any]:
    """只读探账本，返回 {status: hit|mismatch|absent, entry?}——**不写入**。

    供「先返回已登记首次结果、再走可能不落库的后续校验」的端点（如 freshness 门可能 202 扣草稿
    不落库）：若在 record() 前用它探得 hit，则重放能拿回原结果而不被后续门误伤；absent 时后续
    才在落库路径调 record() 真正登记（避免留悬挂账本行指向未落库消息）。竞态由 record() 兜底。
    """
    existing = _fetch_entry(conn, op_id)
    if existing is None:
        return {"status": "absent"}
    if existing.request_hash == request_hash:
        return {"status": "hit", "entry": existing}
    return {"status": "mismatch", "entry": existing}


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


def _channel_human_member_ids(conn: Connection, channel_id: str) -> list[str]:
    """本频道未软删人类成员 id（fail-closed activity 接收者；activity 是人类面，B §9.7）。"""
    rows = conn.execute(
        select(_MEMBER.c.id)
        .select_from(_CHANNEL_MEMBER.join(_MEMBER, _CHANNEL_MEMBER.c.member_id == _MEMBER.c.id))
        .where(
            _CHANNEL_MEMBER.c.channel_id == channel_id,
            _MEMBER.c.kind == MemberKind.HUMAN,
            _MEMBER.c.removed_at.is_(None),
        )
        .order_by(_MEMBER.c.id)
    ).scalars()
    return list(rows)


def _write_fail_closed_disposition(
    conn: Connection, batch: LandingBatchRow, *, reason: str | None
) -> None:
    """fail-closed 处置链的 DB 写入（契约 A §4.7 规则 2 全套；inline 与独立连接两路共用）：

    1. landing_batches.status='fail_closed'（幂等 UPDATE）；
    2. diagnostic_events(type='landing.fail_closed', batch_id=…)；
    3. source 线程 fail-closed 告警卡（messages: card_kind='fail_closed', card_ref=batch_id,
       author=NULL 系统消息）；
    4. **activity_items(kind='fail_closed') 置顶**（B §9.7 #2「随 M6 启用」——每个频道人类成员一条，
       message_id 锚到告警卡；铃声 workspaces.notif_sound 属前端播放策略，此处只落持久行）。

    纯 DB 写、无 bus emit（fail-closed 是低频损坏告警，WS 广播由持事务上下文的调用方另发；独立
    连接路径无 bus，客户端下次拉取 REST 即见 activity/卡片）。
    """
    ts = now_iso()
    # 1. 批次置 fail_closed（landing_batches 非不可变表，UPDATE 合法；幂等）。
    conn.execute(
        update(_BATCH).where(_BATCH.c.id == batch.id).values(status=LandingBatchStatus.FAIL_CLOSED)
    )
    # 2. 诊断留痕（按 batch 过滤 = fail-closed 卡"查看诊断"入口，§4.6 索引）。
    conn.execute(
        insert(_DIAG).values(
            workspace_id=batch.workspace_id,
            agent_member_id=None,
            type=_DIAG_FAIL_CLOSED,
            channel_id=batch.channel_id,
            task_id=None,
            batch_id=batch.id,
            payload={"reason": reason or "request_hash mismatch"},
            created_at=ts,
        )
    )
    # 3. fail-closed 告警卡（系统消息，author=NULL；thread_root_id 未知则落频道级）。
    card_id = new_ulid()
    conn.execute(
        insert(_MSG).values(
            id=card_id,
            workspace_id=batch.workspace_id,
            channel_id=batch.channel_id,
            thread_root_id=None,
            author_member_id=None,
            kind=MessageKind.SYSTEM,
            card_kind=CardKind.FAIL_CLOSED,
            card_ref=batch.id,
            body=_FAIL_CLOSED_CARD_BODY,
            created_at=ts,
        )
    )
    # 4. activity_items(kind='fail_closed') 置顶（每频道人类成员一条，锚到告警卡消息）。
    for member_id in _channel_human_member_ids(conn, batch.channel_id):
        conn.execute(
            insert(_ACTIVITY).values(
                id=new_ulid(),
                workspace_id=batch.workspace_id,
                member_id=member_id,
                kind=ActivityKind.FAIL_CLOSED,
                channel_id=batch.channel_id,
                message_id=card_id,
                task_id=None,
                created_at=ts,
                done_at=None,
            )
        )


def mark_fail_closed(conn: Connection, batch_id: str, *, reason: str | None = None) -> None:
    """inline fail-closed（契约 A §4.7 规则 2）：写在传入 conn 上——**仅供提交路径**（重放执行器
    逐 op 小事务 record 三态命中 mismatch、既有账本测试等，事务提交后处置持久）。

    ⚠️ 回滚路径（如 REST 落地事务器中途抛 ApiError → get_tx 回滚）**不得用本函数**：inline 写会
    随外层回滚一并撤销（M5b 挂账缺陷）。回滚路径改用 `persist_fail_closed`（独立连接 + 提交），
    见其 docstring 的锁时序方案。
    """
    batch = _fetch_batch(conn, batch_id)
    assert batch is not None, f"unknown batch_id: {batch_id}"
    _write_fail_closed_disposition(conn, batch, reason=reason)


def persist_fail_closed(
    engine: Engine, batch: LandingBatchRow, *, reason: str | None = None
) -> None:
    """回滚路径专用 fail-closed 落盘（M5b 挂账修复；契约 B §12.5 #4）：从 engine **另开独立连接**写
    处置链并提交——外层业务事务无论回滚与否，fail-closed 告警恒持久。

    SQLite 锁时序（外层持写锁时另开连接写会 busy）：**调用方必须先让外层事务释放写锁**再调本函数。
    落地事务器路径 = 抛 `LedgerFailClosed`（不 inline 写）→ get_tx 捕获回滚（释放锁）→ app 层异常
    处理器于回滚**之后**调本函数（此时无并发写锁，独立连接即刻取锁提交，不触 busy_timeout）。

    批行落盘用 upsert：外层回滚已撤销 create_batch 时（tmpl 首次落地中途 mismatch）→ INSERT 全字段
    以 fail_closed 建行；批行已提交存在时（落地执行器 confirm 事务先建批）→ 走处置链的 UPDATE 覆盖。
    """
    with engine.begin() as conn:
        exists = conn.execute(select(_BATCH.c.id).where(_BATCH.c.id == batch.id)).first()
        if exists is None:
            conn.execute(
                insert(_BATCH).values(
                    id=batch.id,
                    workspace_id=batch.workspace_id,
                    channel_id=batch.channel_id,
                    kind=LandingBatchKind(batch.kind),
                    content_hash=batch.content_hash,
                    source_ref=batch.source_ref,
                    confirmed_by=batch.confirmed_by,
                    status=LandingBatchStatus.FAIL_CLOSED,
                    created_at=batch.created_at,
                    done_at=None,
                )
            )
            batch_row = _fetch_batch(conn, batch.id)
            assert batch_row is not None
            _write_fail_closed_disposition(conn, batch_row, reason=reason)
        else:
            batch_row = _fetch_batch(conn, batch.id)
            assert batch_row is not None
            _write_fail_closed_disposition(conn, batch_row, reason=reason)
