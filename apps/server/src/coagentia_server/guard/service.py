"""护栏服务层（契约 B §4.6/§10 G1–G6）：freshness 门判定 + held 关联/升级 + guard 诊断。

判定与值域语义单一事实源在此，路由（`Tx`）与 hub（`GatewayTx`）共同消费——前端与其它路由不复制
新鲜度判定。核心不变量：
- **仅 Agent 主体过门**（人类/系统消息永不 held，裁决 1）；
- scope = 线程（thread_root_id 非空）或频道主流（顶级消息），未读集空 → 放行（裁决 1–2）；
- 同 scope 单活动 held 行（uq_held_drafts_active 兜并发）：再扣 held_count+1、刷新载荷、不建新行
- G5 升级：held_count 达阈值且未升级过 → escalated_at + scope 系统消息 @人类 + held_escalation
  activity + guard.escalated 诊断（停自动重评估靠 escalated_at 非空，F6 扫描排除，裁决 6）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from coagentia_contracts.enums import ActivityKind, HeldDraftStatus, MessageKind
from coagentia_contracts.ws import EventType
from sqlalchemy import and_, insert, or_, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from coagentia_server.activity import service as activity_service
from coagentia_server.db import models
from coagentia_server.ledger.service import format_iso, new_ulid, now_iso
from coagentia_server.routes.serialize import held_draft_public, message_public

_MSG = models.tbl(models.Message)
_MENTION = models.tbl(models.MessageMention)
_READ = models.tbl(models.ReadPosition)
_HELD = models.tbl(models.HeldDraft)
_MEMBER = models.tbl(models.Member)
_CHANNEL_MEMBER = models.tbl(models.ChannelMember)
_DIAG = models.tbl(models.DiagnosticEvent)

# guard.*（G6，DIAGNOSTIC_TYPES 权威登记）——server 侧护栏诊断类型（constants.py:80-84）。
GUARD_HELD = "guard.held"
GUARD_RELEASED = "guard.released"
GUARD_DISCARDED = "guard.discarded"
GUARD_REEVALUATE_REQUESTED = "guard.reevaluate_requested"
GUARD_ESCALATED = "guard.escalated"

# 三键干预对终态 held → 409：released/discarded/resolved 是终态（裁决 7）
TERMINAL_STATUSES = (
    HeldDraftStatus.RELEASED.value,
    HeldDraftStatus.DISCARDED.value,
    HeldDraftStatus.RESOLVED.value,
)
# 活动态（未终解）：held + reevaluating。held 关联/GC 豁免/清单默认过滤单一事实源。
ACTIVE_STATUSES = (HeldDraftStatus.HELD.value, HeldDraftStatus.REEVALUATING.value)

UNREAD_CAP = 50  # reasons.unread_message_ids 上限（保留最新，裁决 4）


# ---------------------------------------------------------------- 时间辅助


def _add_minutes(iso_ts: str, minutes: int) -> str:
    """`iso_ts` + N 分钟 → 同格式 Z 串（G4 倒计时锚 next_reeval_at）。"""
    base = datetime.fromisoformat(iso_ts)
    if base.tzinfo is None:  # 防御：无时区串按 UTC 解释
        base = base.replace(tzinfo=UTC)
    return format_iso(base + timedelta(minutes=minutes))


# ---------------------------------------------------------------- 未读判定（裁决 1–2）


def compute_unread(
    conn: Connection, *, agent_id: str, channel_id: str, thread_root_id: str | None
) -> list[str]:
    """返回 scope 内该 Agent 的未读**他人**消息 id（按 created_at,id 升序）。

    scope：thread_root_id 非空 → 线程（id==root 或 thread_root_id==root）；空 → 频道主流
    （channel_id 内 thread_root_id IS NULL 的顶级消息）。未读 = id > 该 Agent read_position
    的 last_read_message_id 且 author != agent（author IS NULL 的系统消息计入上下文，只排除
    Agent 自己发的）。无 read_position 行 → scope 全量他人消息算未读（保守，先读后说）。
    """
    if thread_root_id is not None:
        scope = or_(_MSG.c.id == thread_root_id, _MSG.c.thread_root_id == thread_root_id)
    else:
        scope = and_(_MSG.c.channel_id == channel_id, _MSG.c.thread_root_id.is_(None))
    read = conn.execute(
        select(_READ.c.last_read_message_id).where(
            _READ.c.member_id == agent_id, _READ.c.channel_id == channel_id
        )
    ).scalar()
    stmt = select(_MSG.c.id).where(
        scope,
        or_(_MSG.c.author_member_id.is_(None), _MSG.c.author_member_id != agent_id),
    )
    if read is not None:  # ULID 单调 ⇒ 字典序即时序
        stmt = stmt.where(_MSG.c.id > read)
    return list(conn.execute(stmt.order_by(_MSG.c.created_at, _MSG.c.id)).scalars().all())


def build_reasons(unread_ids: list[str]) -> dict[str, Any]:
    """HeldDraftReasons 载荷：unread_message_ids 上限 50（保留最新）+ total_unread 真实计数"""
    return {"unread_message_ids": unread_ids[-UNREAD_CAP:], "total_unread": len(unread_ids)}


# ---------------------------------------------------------------- held 关联/建（裁决 3）


def _read_held(conn: Connection, held_id: str) -> dict[str, Any]:
    return models.row_dict(
        conn.execute(select(_HELD).where(_HELD.c.id == held_id)).mappings().first()
    )


def _find_active(
    conn: Connection, agent_id: str, channel_id: str, thread_root_id: str | None
) -> dict[str, Any] | None:
    """同 (agent, channel, thread_root) 的活动 held 行（status ∈ held/reevaluating）；对齐
    uq_held_drafts_active 分区谓词——至多一行。"""
    stmt = select(_HELD).where(
        _HELD.c.agent_member_id == agent_id,
        _HELD.c.channel_id == channel_id,
        _HELD.c.status.in_(ACTIVE_STATUSES),
    )
    if thread_root_id is None:
        stmt = stmt.where(_HELD.c.thread_root_id.is_(None))
    else:
        stmt = stmt.where(_HELD.c.thread_root_id == thread_root_id)
    row = conn.execute(stmt).mappings().first()
    return dict(row) if row is not None else None


def _apply_rehold(
    tx: Any,
    held_id: str,
    *,
    prev_count: int,
    draft_body: str,
    file_ids: list[str] | None,
    as_task: dict[str, Any] | None,
    reasons: dict[str, Any],
    reeval_at: str,
) -> dict[str, Any]:
    """再扣同活动行（裁决 3）：held_count+1、status 回 held、刷新载荷/reasons/next_reeval_at；
    **不动 escalated_at**（升级历史留存，升级后再扣不二次喊人，裁决 6）。"""
    tx.conn.execute(
        update(_HELD)
        .where(_HELD.c.id == held_id)
        .values(
            status=HeldDraftStatus.HELD.value,
            held_count=prev_count + 1,
            draft_body=draft_body,
            file_ids=file_ids,
            as_task=as_task,
            reasons=reasons,
            next_reeval_at=reeval_at,
        )
    )
    return _read_held(tx.conn, held_id)


def hold_or_update(
    tx: Any, *, workspace: dict[str, Any], channel: dict[str, Any], agent: dict[str, Any],
    body: Any, reasons: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """扣草稿：已有活动行 → 再扣（held_count+1）；否则新建（held_count=1）。返回 (held 行, is_new)。

    并发再扣靠 uq_held_drafts_active 兜（SAVEPOINT 隔离 IntegrityError → 重查活动行改 UPDATE，
    不污染外层 REST 事务；M3a 修订链竞态同法）。
    """
    now = now_iso()
    reeval_at = _add_minutes(now, channel["held_reeval_min"])
    thread_root_id = body.thread_root_id
    file_ids = list(body.file_ids) or None  # staging 附件原样保存（放行不丢附件，裁决 8）
    as_task = {"title": body.as_task.title} if body.as_task is not None else None

    existing = _find_active(tx.conn, agent["id"], channel["id"], thread_root_id)
    if existing is not None:
        return (
            _apply_rehold(
                tx, existing["id"], prev_count=existing["held_count"], draft_body=body.body,
                file_ids=file_ids, as_task=as_task, reasons=reasons, reeval_at=reeval_at,
            ),
            False,
        )

    held_id = new_ulid()
    values = {
        "id": held_id,
        "workspace_id": workspace["id"],
        "agent_member_id": agent["id"],
        "channel_id": channel["id"],
        "thread_root_id": thread_root_id,
        "draft_body": body.body,
        "file_ids": file_ids,
        "as_task": as_task,
        "reasons": reasons,
        "status": HeldDraftStatus.HELD.value,
        "held_count": 1,
        "next_reeval_at": reeval_at,
        "escalated_at": None,
        "resolved_by_member_id": None,
        "resolved_at": None,
        "resolution": None,
        "created_at": now,
    }
    try:
        with tx.conn.begin_nested():  # SAVEPOINT：撞唯一索引只回滚本 INSERT，外层事务续用
            tx.conn.execute(insert(_HELD).values(**values))
    except IntegrityError:
        # 并发再扣撞 uq_held_drafts_active（活动行唯一）→ 重查该活动行改 UPDATE（裁决 3）。
        existing = _find_active(tx.conn, agent["id"], channel["id"], thread_root_id)
        if existing is None:  # 理论不达（唯一冲突必有对手行）——防御性重抛
            raise
        return (
            _apply_rehold(
                tx, existing["id"], prev_count=existing["held_count"], draft_body=body.body,
                file_ids=file_ids, as_task=as_task, reasons=reasons, reeval_at=reeval_at,
            ),
            False,
        )
    return _read_held(tx.conn, held_id), True


# ---------------------------------------------------------------- 系统消息 / 诊断底座


def _channel_human_members(conn: Connection, channel_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        select(_MEMBER.c.id, _MEMBER.c.name).select_from(
            _CHANNEL_MEMBER.join(_MEMBER, _CHANNEL_MEMBER.c.member_id == _MEMBER.c.id)
        ).where(
            _CHANNEL_MEMBER.c.channel_id == channel_id,
            _MEMBER.c.kind == "human",
            _MEMBER.c.removed_at.is_(None),
        )
    ).mappings()
    return [dict(r) for r in rows]


def _post_system_message(
    tx: Any, *, workspace_id: str, channel_id: str, body: str,
    thread_root_id: str | None, mention_member_ids: list[str], created_at: str,
) -> str:
    """插 durable 系统消息（author=NULL, kind=SYSTEM）+ @mention 行 + emit（§8.2 视同唤醒触发）。"""
    msg_id = new_ulid()
    tx.conn.execute(
        insert(_MSG).values(
            id=msg_id,
            workspace_id=workspace_id,
            channel_id=channel_id,
            thread_root_id=thread_root_id,
            author_member_id=None,
            kind=MessageKind.SYSTEM,
            card_kind=None,
            card_ref=None,
            body=body,
            created_at=created_at,
        )
    )
    for member_id in mention_member_ids:
        tx.conn.execute(insert(_MENTION).values(message_id=msg_id, member_id=member_id))
    msg_row = models.row_dict(
        tx.conn.execute(select(_MSG).where(_MSG.c.id == msg_id)).mappings().first()
    )
    tx.emit(EventType.MESSAGE_CREATED, channel_id, {"message": message_public(msg_row)})
    return msg_id


def write_guard_diagnostic(
    tx: Any, diag_type: str, *, workspace_id: str, agent_member_id: str,
    channel_id: str | None, payload: dict[str, Any], created_at: str | None = None,
) -> None:
    """写一条 guard.* 诊断；三键端点与 hub 桥共用（released/discarded/reevaluate_requested）。"""
    tx.conn.execute(
        insert(_DIAG).values(
            workspace_id=workspace_id,
            agent_member_id=agent_member_id,
            type=diag_type,
            channel_id=channel_id,
            task_id=None,
            batch_id=None,
            payload=payload,
            created_at=created_at or now_iso(),
        )
    )


# ---------------------------------------------------------------- G5 升级（裁决 6）


def _escalate(
    tx: Any, *, workspace_id: str, channel: dict[str, Any], agent: dict[str, Any],
    held_row: dict[str, Any],
) -> dict[str, Any]:
    """置 escalated_at + 向 scope 发系统消息 @人类 + held_escalation activity + guard.escalated。

    scope = 线程（held.thread_root_id 非空）或频道主流。停自动重评估靠 escalated_at 非空表达
    （F6 扫描排除）；人工 reevaluate 仍可，其后再扣不二次升级（_apply_rehold 不动 escalated_at）。
    """
    now = now_iso()
    tx.conn.execute(
        update(_HELD).where(_HELD.c.id == held_row["id"]).values(escalated_at=now)
    )
    humans = _channel_human_members(tx.conn, channel["id"])
    mention_txt = " ".join(f"@{h['name']}" for h in humans)
    suffix = f"：{mention_txt}" if mention_txt else "。"
    body = (
        f"护栏升级：Agent「{agent['name']}」的草稿已连续被扣 "
        f"{held_row['held_count']} 次仍待处理，需人类介入{suffix}"
    )
    msg_id = _post_system_message(
        tx,
        workspace_id=workspace_id,
        channel_id=channel["id"],
        body=body,
        thread_root_id=held_row["thread_root_id"],
        mention_member_ids=[h["id"] for h in humans],
        created_at=now,
    )
    for h in humans:
        activity_service.emit_activity(
            tx,
            workspace_id=workspace_id,
            member_id=h["id"],
            kind=ActivityKind.HELD_ESCALATION.value,
            channel_id=channel["id"],
            message_id=msg_id,
            created_at=now,
        )
    write_guard_diagnostic(
        tx,
        GUARD_ESCALATED,
        workspace_id=workspace_id,
        agent_member_id=agent["id"],
        channel_id=channel["id"],
        payload={"held_draft_id": held_row["id"], "held_count": held_row["held_count"]},
        created_at=now,
    )
    return _read_held(tx.conn, held_row["id"])


# ---------------------------------------------------------------- 门总装（裁决 5）


def freshness_hold(
    tx: Any, *, workspace: dict[str, Any], channel: dict[str, Any], agent: dict[str, Any],
    body: Any,
) -> dict[str, Any] | None:
    """freshness 门总装：未读集空 → None（放行）；否则扣草稿建/刷新 held 行、G5 升级、写诊断、
    emit held_draft.created/updated，返回 HeldDraftPublic dict（路由包 202 MessageHeld）。"""
    unread = compute_unread(
        tx.conn, agent_id=agent["id"], channel_id=channel["id"],
        thread_root_id=body.thread_root_id,
    )
    if not unread:  # 未读集空 → 放行（裁决 2）
        return None
    reasons = build_reasons(unread)
    row, is_new = hold_or_update(
        tx, workspace=workspace, channel=channel, agent=agent, body=body, reasons=reasons
    )
    # G5：达阈值且未升级过 → 升级一次（裁决 6）。
    if row["held_count"] >= channel["held_escalate_n"] and row["escalated_at"] is None:
        row = _escalate(
            tx, workspace_id=workspace["id"], channel=channel, agent=agent, held_row=row
        )
    held_pub = held_draft_public(row)
    write_guard_diagnostic(
        tx,
        GUARD_HELD,
        workspace_id=workspace["id"],
        agent_member_id=agent["id"],
        channel_id=channel["id"],
        payload={
            "held_draft_id": row["id"],
            "held_count": row["held_count"],
            "total_unread": reasons["total_unread"],
        },
    )
    tx.emit(
        EventType.HELD_DRAFT_CREATED if is_new else EventType.HELD_DRAFT_UPDATED,
        channel["id"],
        {"draft": held_pub},
    )
    return held_pub


__all__ = [
    "ACTIVE_STATUSES",
    "GUARD_DISCARDED",
    "GUARD_HELD",
    "GUARD_REEVALUATE_REQUESTED",
    "GUARD_RELEASED",
    "TERMINAL_STATUSES",
    "compute_unread",
    "freshness_hold",
    "write_guard_diagnostic",
]
