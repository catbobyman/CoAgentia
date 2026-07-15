"""4.14 护栏三键人类干预（契约 B §4.14）：held 清单 + release / discard / reevaluate。

held 行只由 §4.6 freshness 门（202 路径）创建，无 POST 创建端点。三键仅人类（Agent → 403 G3）；
对终态 held（released/discarded/resolved）→ 409 HELD_DRAFT_RESOLVED（details 携当前最新态）。
- release（G3「放行」）：跳过 freshness 复查，以原载荷落消息（author=原 Agent），held→released
  不依赖 daemon（投递靠 emit 的 message.created 事件驱动 hub 投递引擎）。
- discard：held→discarded + 直投 guard_feedback 告知 Agent（daemon 离线 → 503 且整事务回滚）。
- reevaluate：委托 hub 同步桥 reevaluate_held（死锁规避——路由自身 tx 只读，状态改写 + daemon I/O
  全在 hub loop 的独立已提交事务里跑，见 hub.reevaluate_held / 裁决 10）。
"""

from __future__ import annotations

import contextlib
from typing import Any

from coagentia_contracts import entities, rest
from coagentia_contracts.enums import HeldDraftStatus, HeldResolution, MemberKind
from coagentia_contracts.ws import EventType
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select, update

from coagentia_server.api import ApiError
from coagentia_server.db import models
from coagentia_server.deps import Tx, acting_member, get_tx
from coagentia_server.guard import service as guard_service
from coagentia_server.ledger import service
from coagentia_server.routes._pagination import keyset_page
from coagentia_server.routes.messages import persist_message
from coagentia_server.routes.serialize import held_draft_public

router = APIRouter(prefix="/api", tags=["held-drafts"])

_HELD = models.tbl(models.HeldDraft)
_CHANNEL = models.tbl(models.Channel)


def _require_held(tx: Tx, held_id: str) -> dict[str, Any]:
    row = tx.conn.execute(select(_HELD).where(_HELD.c.id == held_id)).mappings().first()
    if row is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "被扣草稿不存在")
    return dict(row)


def _require_human(request: Request, tx: Tx) -> dict[str, Any]:
    """三键干预仅人类（裁决 7）：Agent 主体 → 403 rule='G3'。返回人类干预者成员行。"""
    me = acting_member(request, tx.conn)
    if me["kind"] == MemberKind.AGENT:
        raise ApiError(
            403, rest.ErrorCode.PERMISSION_DENIED, "护栏干预仅限人类成员", rule="G3"
        )
    return me


def _reject_terminal(held: dict[str, Any]) -> None:
    """终态 held（released/discarded/resolved）三键 → 409，details 携当前最新态（裁决 7）。"""
    if held["status"] in guard_service.TERMINAL_STATUSES:
        raise ApiError(
            409,
            rest.ErrorCode.HELD_DRAFT_RESOLVED,
            "该被扣草稿已处于终态，无法再次干预",
            rule="G3",
            details={"held_draft": held_draft_public(held)},
        )


# ---------------------------------------------------------------- 清单（§6 重同步成员）


@router.get("/held-drafts", response_model=rest.Page[entities.HeldDraftPublic])
def list_held_drafts(
    tx: Tx = Depends(get_tx),
    status: str | None = None,
    channel_id: str | None = None,
    after: str | None = None,
    limit: int = rest.PAGE_DEFAULT_LIMIT,
) -> Any:
    """被扣草稿清单；keyset 按 (created_at,id) 升序，宽容过滤器。

    **status 省略 → 默认只回活动态（held/reevaluating）**（评审 #1）：前端频道视图消费此端点渲染
    现行被扣卡；若回全量，终态（released/discarded/resolved）历史会随频道生命周期无界累积，且
    keyset 升序把最老的终态行填满首页、真正在扣的活动行被挤到永不翻取的后页。终态回执由三键
    动作响应 / WS held_draft.updated 在会话内瞬态呈现，无需在此持久回灌。显式传 status 仍精确过滤。
    """
    stmt = select(_HELD)
    if status is not None:  # 无效值 → 空结果集（过滤器宽容）
        stmt = stmt.where(_HELD.c.status == status)
    else:  # 默认只回活动态（held/reevaluating），排除终态
        stmt = stmt.where(_HELD.c.status.in_(guard_service.ACTIVE_STATUSES))
    if channel_id is not None:
        stmt = stmt.where(_HELD.c.channel_id == channel_id)
    return keyset_page(
        tx.conn, _HELD, stmt, after=after, limit=limit, serialize=held_draft_public
    )


# ---------------------------------------------------------------- release（裁决 8）


@router.post("/held-drafts/{held_draft_id}/release", response_model=rest.HeldDraftReleaseResponse)
def release_held_draft(held_draft_id: str, request: Request, tx: Tx = Depends(get_tx)) -> Any:
    """放行原样发送（G3）：跳过 freshness 复查，以原载荷落消息（author=原 Agent）、held→released。

    基础校验重跑（频道归档 → 409 CHANNEL_ARCHIVED）；不依赖 daemon——投递靠 persist_message 广播的
    message.created 事件在提交后驱动 hub 投递引擎（即「放行 1 分钟内交付」）。
    """
    me = _require_human(request, tx)
    held = _require_held(tx, held_draft_id)
    _reject_terminal(held)

    channel = tx.conn.execute(
        select(_CHANNEL).where(_CHANNEL.c.id == held["channel_id"])
    ).mappings().first()
    if channel is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "频道不存在")
    channel = dict(channel)
    if channel.get("archived_at"):  # 基础校验重跑（B §7）
        raise ApiError(409, rest.ErrorCode.CHANNEL_ARCHIVED, "归档频道拒收新消息", rule="FR-1.3")

    as_task = held["as_task"]  # {"title": ...} | None（原样保存的 as_task 意图）
    msg_pub, _task_pub = persist_message(
        tx,
        workspace_id=held["workspace_id"],
        channel=channel,
        msg_id=service.new_ulid(),
        author_member_id=held["agent_member_id"],  # 放行 = 以原 Agent 身份落消息
        body_text=held["draft_body"],
        thread_root_id=held["thread_root_id"],
        file_ids=list(held["file_ids"] or []),
        as_task_title=(as_task or {}).get("title"),
        create_as_task=as_task is not None,
    )

    now = service.now_iso()
    tx.conn.execute(
        update(_HELD).where(_HELD.c.id == held_draft_id).values(
            status=HeldDraftStatus.RELEASED.value,
            resolution=HeldResolution.RELEASED.value,
            resolved_by_member_id=me["id"],
            resolved_at=now,
        )
    )
    held_row = models.row_dict(
        tx.conn.execute(select(_HELD).where(_HELD.c.id == held_draft_id)).mappings().first()
    )
    held_pub = held_draft_public(held_row)
    guard_service.write_guard_diagnostic(
        tx,
        guard_service.GUARD_RELEASED,
        workspace_id=held["workspace_id"],
        agent_member_id=held["agent_member_id"],
        channel_id=held["channel_id"],
        payload={"held_draft_id": held_draft_id, "resolved_by": me["id"]},
        created_at=now,
    )
    tx.emit(EventType.HELD_DRAFT_UPDATED, held["channel_id"], {"draft": held_pub})
    return {"message": msg_pub, "held_draft": held_pub}


# ---------------------------------------------------------------- discard（裁决 9）


@router.post("/held-drafts/{held_draft_id}/discard", response_model=rest.HeldDraftResponse)
def discard_held_draft(held_draft_id: str, request: Request, tx: Tx = Depends(get_tx)) -> Any:
    """丢弃草稿：held→discarded + 直投 guard_feedback 告知 Agent。

    顺序（L4b，CR-M8-1 同族收敛，铁律 4「跨进程同步等待不得跨持锁事务」）：**预检** daemon 在线
    （离线常见路径在写事务开始前即 503，「不落库」语义保留）→ 写 held 终态 + 诊断 + emit → inject
    本体挪 `tx.after_commit`（等 ack 期间写锁已释放，真适配器回 ack 前上报 agent.status/心跳的 DB
    写畅通，不撞锁自死锁）。预检后到 after_commit 间罕见的 daemon 掉线 → inject best-effort 吞
    （丢弃已提交，符合人类意图；Agent 少收一条一次性告知，非关键）。
    """
    from coagentia_server.computers import DaemonOffline

    me = _require_human(request, tx)
    held = _require_held(tx, held_draft_id)
    _reject_terminal(held)

    hub = request.app.state.daemon_hub
    if not hub.agent_daemon_online(held["agent_member_id"]):
        raise ApiError(503, rest.ErrorCode.DAEMON_OFFLINE, "daemon 离线，无法投递丢弃通知")

    now = service.now_iso()
    tx.conn.execute(
        update(_HELD).where(_HELD.c.id == held_draft_id).values(
            status=HeldDraftStatus.DISCARDED.value,
            resolution=HeldResolution.DISCARDED.value,
            resolved_by_member_id=me["id"],
            resolved_at=now,
        )
    )
    held_row = models.row_dict(
        tx.conn.execute(select(_HELD).where(_HELD.c.id == held_draft_id)).mappings().first()
    )
    held_pub = held_draft_public(held_row)
    guard_service.write_guard_diagnostic(
        tx,
        guard_service.GUARD_DISCARDED,
        workspace_id=held["workspace_id"],
        agent_member_id=held["agent_member_id"],
        channel_id=held["channel_id"],
        payload={"held_draft_id": held_draft_id, "resolved_by": me["id"]},
        created_at=now,
    )
    tx.emit(EventType.HELD_DRAFT_UPDATED, held["channel_id"], {"draft": held_pub})

    agent_member_id = held["agent_member_id"]

    def _fire_discard_inject() -> None:
        # 提交后投递（L4b）：写锁已释放，_run_sync 等 ack 期间不持锁 → 无自死锁。
        with contextlib.suppress(DaemonOffline):
            hub.inject_guard_feedback(
                agent_member_id,
                "[system → 仅你可见] 你此前被扣的草稿已被人类丢弃，无需再发送。",
                ref=held_draft_id,
            )

    tx.after_commit(_fire_discard_inject)
    return {"held_draft": held_pub}


# ---------------------------------------------------------------- reevaluate（裁决 10）


@router.post("/held-drafts/{held_draft_id}/reevaluate", response_model=rest.HeldDraftResponse)
def reevaluate_held_draft(held_draft_id: str, request: Request, tx: Tx = Depends(get_tx)) -> Any:
    """触发重评估（死锁规避）：路由自身 tx **只读**（校验存在/终态/人类）；状态改写 + daemon I/O
    全部委托 hub 同步桥 reevaluate_held（在 hub loop 独立已提交事务里跑，避 REST 写锁与 deliver 写
    read_position 争 SQLite 写锁）。随后只读重查 held 行返回（非终态，不写 resolved_*）。
    """
    from coagentia_server.computers import DaemonOffline, HeldDraftResolved

    me = _require_human(request, tx)
    held = _require_held(tx, held_draft_id)
    _reject_terminal(held)

    hub = request.app.state.daemon_hub
    # hub 在独立已提交事务里改状态 + 做 daemon I/O。终态守卫在其 UPDATE 里（评审 #5）：
    # 路由校验与该 UPDATE 之间若并发终解 → HeldDraftResolved
    # → 以最新态回 409（与 _reject_terminal 一致）。
    engine = request.app.state.engine
    try:
        hub.reevaluate_held(held_draft_id, resolved_by=me["id"])
    except DaemonOffline as exc:
        raise ApiError(
            503, rest.ErrorCode.DAEMON_OFFLINE, "daemon 离线，无法触发重评估"
        ) from exc
    except HeldDraftResolved as exc:
        with engine.connect() as c:
            latest = models.row_dict(
                c.execute(select(_HELD).where(_HELD.c.id == held_draft_id)).mappings().first()
            )
        raise ApiError(
            409,
            rest.ErrorCode.HELD_DRAFT_RESOLVED,
            "该被扣草稿已被并发终解，无法重评估",
            rule="G3",
            details={"held_draft": held_draft_public(latest)},
        ) from exc

    # hub 在独立连接已提交 reevaluating 态；本 tx 的读快照看不到 → 用 app engine 新连接重查最新态。
    with engine.connect() as c:
        held_row = models.row_dict(
            c.execute(select(_HELD).where(_HELD.c.id == held_draft_id)).mappings().first()
        )
    return {"held_draft": held_draft_public(held_row)}
