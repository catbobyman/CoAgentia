"""4.6 消息、文件与已读（契约 B §4.6）+ 文件 staging/绑定（契约 D §9.2）。"""

from __future__ import annotations

import re
from typing import Any

from coagentia_contracts import entities, rest
from coagentia_contracts.constants import OPID_REST_IDEMPOTENCY
from coagentia_contracts.enums import ActivityKind, ChannelKind, MemberKind, MessageKind
from coagentia_contracts.kernel.fingerprint import fingerprint
from coagentia_contracts.ws import EventType
from fastapi import APIRouter, Depends, Header, Request, Response, UploadFile
from sqlalchemy import insert, select, update

from coagentia_server.api import ApiError
from coagentia_server.db import models
from coagentia_server.deps import Tx, acting_member, get_tx, owner_member, require_workspace
from coagentia_server.files.store import StagedMeta, sha256_hex
from coagentia_server.ledger import service
from coagentia_server.routes.serialize import (
    activity_item_public,
    file_public,
    message_public,
    read_position_public,
    task_public,
)
from coagentia_server.tasks import service as tasks_service

router = APIRouter(prefix="/api", tags=["messages"])

_CHANNEL = models.Channel.__table__
_MSG = models.Message.__table__
_MENTION = models.MessageMention.__table__
_MEMBER = models.Member.__table__
_FILE = models.File.__table__
_READ = models.ReadPosition.__table__
_TASK = models.Task.__table__
_MTR = models.MessageTaskRef.__table__
_ACTIVITY = models.ActivityItem.__table__
_CHANNEL_MEMBER = models.ChannelMember.__table__

# task #n 解析（B §9.5）：task 与 # 间允许空白，# 与数字紧邻；大小写不敏感。
_TASK_REF_RE = re.compile(r"task\s*#(\d+)", re.IGNORECASE)

def _require_channel(tx: Tx, channel_id: str) -> dict[str, Any]:
    row = tx.conn.execute(select(_CHANNEL).where(_CHANNEL.c.id == channel_id)).mappings().first()
    if row is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "频道不存在")
    return dict(row)


def _resolve_mentions(
    tx: Tx, workspace_id: str, message_id: str, body: str
) -> list[dict[str, Any]]:
    """@名字 服务端解析一次落 message_mentions（body 是唯一事实源，契约 A messages）。

    返回命中的成员行（含 kind），供 _generate_activity 直接消费——省去对刚插入
    mentions 行的回读 JOIN（M2 review：无 @ 消息也白付一次查询）。
    """
    members = list(
        tx.conn.execute(
            select(_MEMBER.c.id, _MEMBER.c.name, _MEMBER.c.kind).where(
                _MEMBER.c.workspace_id == workspace_id,
                _MEMBER.c.removed_at.is_(None),
            )
        ).mappings()
    )
    if not members:
        return []

    # 以成员名目录为事实源，而不是把 @ 后直到空白都当句柄。这样 `@Hank，让他处理`
    # 会在中文逗号处正确结束，也不会把 `@PatBot` 误解析成较短的 `@Pat`。
    alternatives = "|".join(
        re.escape(str(member["name"]))
        for member in sorted(members, key=lambda member: len(str(member["name"])), reverse=True)
    )
    mention_re = re.compile(rf"@({alternatives})(?=$|[^\w.-])", re.IGNORECASE)
    handles = {match.group(1).casefold() for match in mention_re.finditer(body)}
    if not handles:
        return []

    mentioned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in members:
        if str(m["name"]).casefold() in handles and m["id"] not in seen:
            tx.conn.execute(
                insert(_MENTION).values(message_id=message_id, member_id=m["id"])
            )
            seen.add(m["id"])
            mentioned.append(dict(m))
    return mentioned


def _resolve_task_refs(tx: Tx, channel_id: str, message_id: str, body: str) -> None:
    """C3a：body 里的 `task #<n>` 服务端解析一次落 message_task_refs（B §9.5）。

    编号是频道内自增，故只解析**当前频道**的编号；未命中保持纯文本、不报错。body 是
    唯一事实源，refs 是派生持久化（与 message_mentions 同构）。同一 task 只插一行。
    """
    numbers = {int(match.group(1)) for match in _TASK_REF_RE.finditer(body)}
    if not numbers:
        return
    seen: set[str] = set()
    for n in numbers:
        task_id = tx.conn.execute(
            select(_TASK.c.id).where(_TASK.c.channel_id == channel_id, _TASK.c.number == n)
        ).scalar_one_or_none()
        if task_id is not None and task_id not in seen:
            tx.conn.execute(
                insert(_MTR).values(message_id=message_id, task_id=task_id)
            )
            seen.add(task_id)


def _emit_activity(
    tx: Tx,
    *,
    workspace_id: str,
    member_id: str,
    actor_member_id: str,
    kind: str,
    channel_id: str,
    message_id: str,
    ts: str,
) -> None:
    """插一条 activity_items 并广播 activity.created（channel_id=None 全局，供前端 P9）。

    actor_member_id（消息作者）只进 Public 载荷不落库——表列 member_id 语义=接收者。
    """
    row = {
        "id": service.new_ulid(),
        "workspace_id": workspace_id,
        "member_id": member_id,
        "kind": kind,
        "channel_id": channel_id,
        "message_id": message_id,
        "task_id": None,
        "created_at": ts,
        "done_at": None,
    }
    tx.conn.execute(insert(_ACTIVITY).values(**row))
    pub = activity_item_public({**row, "actor_member_id": actor_member_id})
    tx.emit(EventType.ACTIVITY_CREATED, None, {"item": pub})


def _generate_activity(
    tx: Tx,
    workspace_id: str,
    channel: dict[str, Any],
    message_id: str,
    author_member_id: str,
    ts: str,
    mentioned: list[dict[str, Any]],
) -> None:
    """C3b：Activity 生成（M2 子集，B §9.7）。Agent 成员永不作为接收者生成 activity。

    - channel 频道：消费 _resolve_mentions 返回的命中成员（同事务内存数据，免回读 JOIN），
      取人类且非作者的接收者 → kind='mention'。
    - dm 频道：取该 DM 对端人类（非作者）→ kind='dm'。DM 消息不再生成 mention（裁决：避免双写）。
    """
    channel_id = channel["id"]
    if channel["kind"] == ChannelKind.DM:
        recipients = list(
            tx.conn.execute(
                select(_MEMBER.c.id)
                .select_from(
                    _CHANNEL_MEMBER.join(_MEMBER, _CHANNEL_MEMBER.c.member_id == _MEMBER.c.id)
                )
                .where(
                    _CHANNEL_MEMBER.c.channel_id == channel_id,
                    _MEMBER.c.kind == MemberKind.HUMAN,
                    _MEMBER.c.id != author_member_id,
                    _MEMBER.c.removed_at.is_(None),
                )
            ).scalars()
        )
        kind = ActivityKind.DM.value
    else:
        if not mentioned:  # 无 @ 的普通消息（绝大多数）：零额外查询
            return
        recipients = [
            m["id"]
            for m in mentioned
            if m["kind"] == MemberKind.HUMAN and m["id"] != author_member_id
        ]
        kind = ActivityKind.MENTION.value

    for recipient_id in dict.fromkeys(recipients):  # 去重（防成员多次命中）
        _emit_activity(
            tx,
            workspace_id=workspace_id,
            member_id=recipient_id,
            actor_member_id=author_member_id,
            kind=kind,
            channel_id=channel_id,
            message_id=message_id,
            ts=ts,
        )


# ---------------------------------------------------------------- 读


def files_by_message(tx: Tx, message_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    """按 message_id 批量取附件 → FilePublic dict 列表（契约 A v1.0.4 读面派生 files）。

    消息读面统一走此 helper 附着（列表/线程/发消息响应/搜索命中），前端不再依赖
    channelFiles 首页 ≤50 的间接聚合（M2 挂账）。"""
    if not message_ids:
        return {}
    rows = tx.conn.execute(
        select(_FILE)
        .where(_FILE.c.message_id.in_(message_ids))
        .order_by(_FILE.c.created_at, _FILE.c.id)
    ).mappings()
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(r["message_id"], []).append(file_public(dict(r)))
    return out


@router.get("/channels/{channel_id}/messages", response_model=rest.Page[entities.MessagePublic])
def get_messages(
    channel_id: str,
    tx: Tx = Depends(get_tx),
    after: str | None = None,
    before: str | None = None,
    limit: int = rest.PAGE_DEFAULT_LIMIT,
) -> Any:
    _require_channel(tx, channel_id)
    rows = [
        dict(r)
        for r in tx.conn.execute(
            select(_MSG)
            .where(_MSG.c.channel_id == channel_id)
            .order_by(_MSG.c.created_at, _MSG.c.id)
        ).mappings()
    ]
    ids = [m["id"] for m in rows]
    if after and after in ids:
        rows = rows[ids.index(after) + 1 :]
    if before and before in ids:
        ids2 = [m["id"] for m in rows]
        rows = rows[: ids2.index(before)]
    limit = min(max(1, limit), rest.PAGE_MAX_LIMIT)
    page, tail = rows[:limit], rows[limit:]
    next_cursor = page[-1]["id"] if tail and page else None
    fmap = files_by_message(tx, [m["id"] for m in page])
    return {
        "items": [message_public(m, fmap.get(m["id"], [])) for m in page],
        "next_cursor": next_cursor,
    }


@router.get("/messages/{message_id}/thread", response_model=rest.Page[entities.MessagePublic])
def get_thread(message_id: str, tx: Tx = Depends(get_tx), after: str | None = None) -> Any:
    root = tx.conn.execute(select(_MSG).where(_MSG.c.id == message_id)).mappings().first()
    if root is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "消息不存在")
    replies = tx.conn.execute(
        select(_MSG)
        .where(_MSG.c.thread_root_id == message_id)
        .order_by(_MSG.c.created_at, _MSG.c.id)
    ).mappings()
    rows = [dict(root), *[dict(r) for r in replies]]
    fmap = files_by_message(tx, [m["id"] for m in rows])
    items = [message_public(m, fmap.get(m["id"], [])) for m in rows]
    return {"items": items, "next_cursor": None}


# ---------------------------------------------------------------- 发消息


@router.post("/channels/{channel_id}/messages", response_model=rest.MessageCreated, status_code=201)
def post_message(
    channel_id: str,
    body: rest.MessageCreate,
    request: Request,
    tx: Tx = Depends(get_tx),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Any:
    ws = require_workspace(tx.conn)
    channel = _require_channel(tx, channel_id)

    # 归档 → 一切写端点回 CHANNEL_ARCHIVED（契约 B §7）。
    if channel.get("archived_at"):
        raise ApiError(409, rest.ErrorCode.CHANNEL_ARCHIVED, "归档频道拒收新消息", rule="FR-1.3")
    # as_task 边界（纯校验，不依赖 M2 tasks 表）。
    if body.as_task is not None and channel["kind"] == ChannelKind.DM:
        raise ApiError(422, rest.ErrorCode.TASK_IN_DM, "DM 不承载任务", rule="FR-5.1")
    if body.as_task is not None and body.thread_root_id is not None:
        raise ApiError(422, rest.ErrorCode.NOT_TOP_LEVEL_MESSAGE, "仅顶级消息可转任务", rule="T3")
    # thread_root_id 校验：目标必须是同频道存在的**顶级**消息（契约 A：线程不可嵌套）。
    # 缺此校验时坏 thread_root_id → messages FK IntegrityError → 未处理 500（A8 live 实测暴露：
    # Agent 传了非消息 id 的 thread_root_id，服务端 500 而非干净 4xx，阻断对话）。
    if body.thread_root_id is not None:
        root = (
            tx.conn.execute(
                select(_MSG.c.channel_id, _MSG.c.thread_root_id).where(
                    _MSG.c.id == body.thread_root_id
                )
            )
            .mappings()
            .first()
        )
        if root is None or root["channel_id"] != channel_id:
            raise ApiError(
                404, rest.ErrorCode.NOT_FOUND, "thread_root_id 指向的消息不存在或不在本频道"
            )
        if root["thread_root_id"] is not None:
            raise ApiError(
                422,
                rest.ErrorCode.NOT_TOP_LEVEL_MESSAGE,
                "线程不可嵌套：thread_root_id 必须指向顶级消息",
                rule="T3",
            )

    # 主体身份（契约 B §2）：浏览器=Owner；daemon 代理 Agent 发消息附 X-Acting-Member。
    # 必须用 acting_member 而非 owner_member——否则 Agent 发言全被记成 Owner（A8 实测暴露）。
    me = acting_member(request, tx.conn)
    msg_id = service.new_ulid()

    # 幂等（契约 B §1）：同键同 body → 返回首次结果；同键异 body → 409。
    if idempotency_key is not None:
        op_id = OPID_REST_IDEMPOTENCY.format(key=idempotency_key)
        req_hash = fingerprint(body.model_dump())
        res = service.record(
            tx.conn,
            op_id,
            "rest_message",
            {"message_id": msg_id, "channel_id": channel_id},
            request_hash=req_hash,
        )
        if res["status"] == "hit":
            prior_id = res["entry"].payload["message_id"]
            prior = tx.conn.execute(select(_MSG).where(_MSG.c.id == prior_id)).mappings().first()
            if prior is None:
                raise ApiError(404, rest.ErrorCode.NOT_FOUND, "幂等命中但原消息缺失")
            # as_task 幂等：凭 root_message_id UNIQUE 回查既有任务（B §9.4）。
            prior_task = (
                tx.conn.execute(select(_TASK).where(_TASK.c.root_message_id == prior_id))
                .mappings()
                .first()
            )
            prior_files = files_by_message(tx, [prior_id]).get(prior_id, [])
            return {
                "message": message_public(dict(prior), prior_files),
                "task": task_public(dict(prior_task)) if prior_task is not None else None,
            }
        if res["status"] == "mismatch":
            raise ApiError(
                409, rest.ErrorCode.IDEMPOTENCY_MISMATCH, "同 Idempotency-Key 不同请求体"
            )
        msg_id = res["entry"].payload["message_id"]  # new：复用账本内记录的 id

    ts = service.now_iso()
    tx.conn.execute(
        insert(_MSG).values(
            id=msg_id,
            workspace_id=ws["id"],
            channel_id=channel_id,
            thread_root_id=body.thread_root_id,
            author_member_id=me["id"],
            kind=MessageKind.USER,
            card_kind=None,
            card_ref=None,
            body=body.body,
            created_at=ts,
        )
    )
    mentioned = _resolve_mentions(tx, ws["id"], msg_id, body.body)
    # C3a：解析 body 里的 task #n → message_task_refs（当前频道编号，未命中不报错）。
    _resolve_task_refs(tx, channel_id, msg_id, body.body)
    # C3b：由 mentions/DM 对端生成人类 Activity（Agent 接收者永不生成），广播 activity.created。
    _generate_activity(tx, ws["id"], channel, msg_id, me["id"], ts, mentioned)

    # 文件绑定（契约 D §9.2）：同事务落 files 行并把正文移入 files/<id>。
    for upload_id in dict.fromkeys(body.file_ids):
        meta = tx.file_store.read_staged_meta(upload_id)
        if meta is None or not tx.file_store.is_staged(upload_id):
            raise ApiError(404, rest.ErrorCode.NOT_FOUND, f"文件未预上传或已绑定: {upload_id}")
        stored_path = tx.bind_file(upload_id)
        tx.conn.execute(
            insert(_FILE).values(
                id=upload_id,
                workspace_id=ws["id"],
                message_id=msg_id,
                channel_id=channel_id,
                name=meta.name,
                mime=meta.mime,
                size_bytes=meta.size_bytes,
                sha256=meta.sha256,
                stored_path=stored_path,
                created_at=ts,
            )
        )

    msg = dict(tx.conn.execute(select(_MSG).where(_MSG.c.id == msg_id)).mappings().first())
    # 响应与 message.created 广播自带刚绑定的附件——前端附件卡即时渲染，不再等
    # channelFiles 失效重拉（v1.0.4）。
    pub = message_public(msg, files_by_message(tx, [msg_id]).get(msg_id, []))

    # as_task（B §9.4）：同事务建任务；顶级/非 DM 已在上方校验通过。message.created 先、
    # task.created 后严格提交序广播；任一半失败整事务回滚，双双不落库不广播（原子）。
    task_pub = None
    if body.as_task is not None:
        task_row = tasks_service.create_task(
            tx,
            workspace_id=ws["id"],
            channel_id=channel_id,
            root_message_id=msg_id,
            created_by=me["id"],
            title=body.as_task.title,
            source_body=body.body,
        )
        task_pub = task_public(task_row)

    tx.emit(EventType.MESSAGE_CREATED, channel_id, {"message": pub})
    if task_pub is not None:
        tx.emit(EventType.TASK_CREATED, channel_id, {"task": task_pub})
    return {"message": pub, "task": task_pub}


# ---------------------------------------------------------------- 文件


@router.post("/files", response_model=entities.FilePublic, status_code=201)
def upload_file(file: UploadFile, tx: Tx = Depends(get_tx)) -> Any:
    ws = require_workspace(tx.conn)
    content = file.file.read()
    max_bytes = ws["attachment_max_mb"] * 1024 * 1024
    if len(content) > max_bytes:
        raise ApiError(
            413, rest.ErrorCode.FILE_TOO_LARGE, f"超过 {ws['attachment_max_mb']}MB 上限"
        )
    upload_id = service.new_ulid()
    meta = StagedMeta(
        name=file.filename or "unnamed",
        mime=file.content_type or "application/octet-stream",
        size_bytes=len(content),
        sha256=sha256_hex(content),
        workspace_id=ws["id"],
    )
    tx.file_store.stage(upload_id, content, meta)
    return {
        "id": upload_id,
        "workspace_id": ws["id"],
        "message_id": None,  # staging 态（契约 D §9.2）
        "channel_id": None,
        "name": meta.name,
        "mime": meta.mime,
        "size_bytes": meta.size_bytes,
        "sha256": meta.sha256,
        "created_at": service.now_iso(),
    }


@router.get("/files/{file_id}/content")
def file_content(file_id: str, tx: Tx = Depends(get_tx)) -> Response:
    bound = tx.conn.execute(select(_FILE).where(_FILE.c.id == file_id)).mappings().first()
    if bound is not None:
        data = tx.file_store.read_bound(bound["stored_path"])
        if data is None:
            raise ApiError(404, rest.ErrorCode.NOT_FOUND, "文件正文缺失")
        return Response(content=data, media_type=bound["mime"])
    # staging 态（尚未绑定消息）。
    meta = tx.file_store.read_staged_meta(file_id)
    if meta is not None and tx.file_store.is_staged(file_id):
        data = (tx.file_store.staging_dir / file_id).read_bytes()
        return Response(content=data, media_type=meta.mime)
    raise ApiError(404, rest.ErrorCode.NOT_FOUND, "文件不存在")


# ---------------------------------------------------------------- 已读


@router.put("/channels/{channel_id}/read-position", response_model=entities.ReadPositionPublic)
def put_read_position(
    channel_id: str, body: rest.ReadPositionPut, tx: Tx = Depends(get_tx)
) -> Any:
    _require_channel(tx, channel_id)
    me = owner_member(tx.conn)
    msg = tx.conn.execute(
        select(_MSG.c.id).where(
            _MSG.c.id == body.last_read_message_id, _MSG.c.channel_id == channel_id
        )
    ).first()
    if msg is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "已读锚点消息不在本频道")
    ts = service.now_iso()
    existing = tx.conn.execute(
        select(_READ).where(_READ.c.member_id == me["id"], _READ.c.channel_id == channel_id)
    ).first()
    if existing is None:
        tx.conn.execute(
            insert(_READ).values(
                member_id=me["id"],
                channel_id=channel_id,
                last_read_message_id=body.last_read_message_id,
                last_read_at=ts,
            )
        )
    else:
        tx.conn.execute(
            update(_READ)
            .where(_READ.c.member_id == me["id"], _READ.c.channel_id == channel_id)
            .values(last_read_message_id=body.last_read_message_id, last_read_at=ts)
        )
    row = dict(
        tx.conn.execute(
            select(_READ).where(_READ.c.member_id == me["id"], _READ.c.channel_id == channel_id)
        ).mappings().first()
    )
    tx.emit(
        EventType.READ_UPDATED,
        channel_id,
        {
            "channel_id": channel_id,
            "member_id": me["id"],
            "last_read_message_id": body.last_read_message_id,
        },
    )
    return read_position_public(row)
