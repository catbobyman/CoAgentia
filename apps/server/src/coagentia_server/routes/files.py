"""4.6 频道文件页签（契约 B §9.5 / §4.6，v1.1 新增）：GET /channels/{id}/files 倒序游标分页。

范式照 routes/messages.py（router 前缀 /api、ApiError 报错、游标分页）。文件行由 messages.py
的文件绑定路径落库（files.channel_id 随绑定写入）；本端点只读 files 表、不改任何写路径。
"""

from __future__ import annotations

from typing import Any

from coagentia_contracts import entities, rest
from fastapi import APIRouter, Depends
from sqlalchemy import select

from coagentia_server.api import ApiError
from coagentia_server.db import models
from coagentia_server.deps import Tx, get_tx
from coagentia_server.routes._pagination import cursor_page
from coagentia_server.routes.serialize import file_public

router = APIRouter(prefix="/api", tags=["files"])

_CHANNEL = models.Channel.__table__
_FILE = models.File.__table__


def _require_channel(tx: Tx, channel_id: str) -> dict[str, Any]:
    row = tx.conn.execute(select(_CHANNEL).where(_CHANNEL.c.id == channel_id)).mappings().first()
    if row is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "频道不存在")
    return dict(row)


@router.get(
    "/channels/{channel_id}/files", response_model=rest.Page[entities.FilePublic]
)
def list_channel_files(
    channel_id: str,
    tx: Tx = Depends(get_tx),
    after: str | None = None,
    limit: int = rest.PAGE_DEFAULT_LIMIT,
) -> Any:
    _require_channel(tx, channel_id)
    # 倒序（created_at desc, id desc）——最新文件在前（B §9.5）；游标 after=id 往后翻。
    rows = [
        dict(r)
        for r in tx.conn.execute(
            select(_FILE)
            .where(_FILE.c.channel_id == channel_id)
            .order_by(_FILE.c.created_at.desc(), _FILE.c.id.desc())
        ).mappings()
    ]
    return cursor_page(rows, after, limit, file_public)
