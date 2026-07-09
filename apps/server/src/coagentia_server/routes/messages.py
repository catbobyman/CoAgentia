"""4.6 消息、文件与已读（契约 B §4.6）+ 文件 staging/绑定（契约 D §9.2）。"""

from __future__ import annotations

import re
from typing import Any

from coagentia_contracts import entities, rest
from coagentia_contracts.constants import OPID_REST_IDEMPOTENCY
from coagentia_contracts.enums import ChannelKind, MessageKind
from coagentia_contracts.kernel.fingerprint import fingerprint
from coagentia_contracts.ws import EventType
from fastapi import APIRouter, Depends, Header, Request, Response, UploadFile
from sqlalchemy import insert, select, update

from coagentia_server.api import ApiError
from coagentia_server.db import models
from coagentia_server.deps import Tx, acting_member, get_tx, owner_member, require_workspace
from coagentia_server.files.store import StagedMeta, sha256_hex
from coagentia_server.ledger import service
from coagentia_server.routes.serialize import message_public, read_position_public

router = APIRouter(prefix="/api", tags=["messages"])

_CHANNEL = models.Channel.__table__
_MSG = models.Message.__table__
_MENTION = models.MessageMention.__table__
_MEMBER = models.Member.__table__
_FILE = models.File.__table__
_READ = models.ReadPosition.__table__

_MENTION_RE = re.compile(r"@([^\s@]+)")


def _require_channel(tx: Tx, channel_id: str) -> dict[str, Any]:
    row = tx.conn.execute(select(_CHANNEL).where(_CHANNEL.c.id == channel_id)).mappings().first()
    if row is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "频道不存在")
    return dict(row)


def _resolve_mentions(tx: Tx, workspace_id: str, message_id: str, body: str) -> None:
    """@名字 服务端解析一次落 message_mentions（body 是唯一事实源，契约 A messages）。"""
    handles = {h.lower() for h in _MENTION_RE.findall(body)}
    if not handles:
        return
    members = tx.conn.execute(
        select(_MEMBER.c.id, _MEMBER.c.name).where(_MEMBER.c.workspace_id == workspace_id)
    ).mappings()
    seen: set[str] = set()
    for m in members:
        if m["name"].lower() in handles and m["id"] not in seen:
            tx.conn.execute(
                insert(_MENTION).values(message_id=message_id, member_id=m["id"])
            )
            seen.add(m["id"])


# ---------------------------------------------------------------- 读


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
    return {"items": [message_public(m) for m in page], "next_cursor": next_cursor}


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
    items = [message_public(dict(root)), *[message_public(dict(r)) for r in replies]]
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
            return {"message": message_public(dict(prior)), "task": None}
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
    _resolve_mentions(tx, ws["id"], msg_id, body.body)

    # 文件绑定（契约 D §9.2）：同事务落 files 行并把正文移入 files/<id>。
    for upload_id in dict.fromkeys(body.file_ids):
        meta = tx.file_store.read_staged_meta(upload_id)
        if meta is None or not tx.file_store.is_staged(upload_id):
            raise ApiError(404, rest.ErrorCode.NOT_FOUND, f"文件未预上传或已绑定: {upload_id}")
        stored_path = tx.file_store.bind(upload_id)
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
    pub = message_public(msg)
    tx.emit(EventType.MESSAGE_CREATED, channel_id, {"message": pub})
    # as_task：tasks 是 M2 表（未建）→ M1 返回 task=null（MessageCreated.task 是 Optional）。
    # TODO(M2)：as_task 成功建 L2 任务 + 广播 task.created（原子）。
    return {"message": pub, "task": None}


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
