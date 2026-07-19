"""4.1 工作区与设置（契约 B §4.1）：bootstrap / 读 / 改。"""

from __future__ import annotations

from typing import Any

from coagentia_contracts import entities, rest
from coagentia_contracts.enums import ChannelKind, MemberKind, MemberRole
from coagentia_contracts.ws import EventType
from fastapi import APIRouter, Depends
from sqlalchemy import insert, select, update

from coagentia_server.api import ApiError
from coagentia_server.db import models
from coagentia_server.deps import Tx, get_tx, require_workspace, workspace_row
from coagentia_server.ledger.service import new_ulid, now_iso
from coagentia_server.routes.serialize import workspace_public

router = APIRouter(prefix="/api", tags=["workspace"])

_WS = models.tbl(models.Workspace)
_MEMBER = models.tbl(models.Member)
_CHANNEL = models.tbl(models.Channel)
_CHANNEL_MEMBER = models.tbl(models.ChannelMember)


@router.post("/workspace", response_model=entities.WorkspacePublic, status_code=201)
def create_workspace(body: rest.WorkspaceCreate, tx: Tx = Depends(get_tx)) -> Any:
    if workspace_row(tx.conn) is not None:
        raise ApiError(409, rest.ErrorCode.WORKSPACE_EXISTS, "MVP 单工作区，已存在")

    ts = now_iso()
    ws_id = new_ulid()
    tx.conn.execute(
        insert(_WS).values(
            id=ws_id,
            name=body.name,
            slug=body.slug,
            created_at=ts,
        )
    )

    # Owner 人类成员（role=owner；R1 只约束 Agent，人类 owner 合法）。
    owner_id = new_ulid()
    tx.conn.execute(
        insert(_MEMBER).values(
            id=owner_id,
            workspace_id=ws_id,
            kind=MemberKind.HUMAN,
            name="Owner",
            role=MemberRole.OWNER,
            removed_at=None,
            created_at=ts,
        )
    )

    # #all 频道 + Owner 入频道。
    all_id = new_ulid()
    tx.conn.execute(
        insert(_CHANNEL).values(
            id=all_id,
            workspace_id=ws_id,
            kind=ChannelKind.CHANNEL,
            name="all",
            description="",
            is_private=False,
            created_at=ts,
        )
    )
    tx.conn.execute(
        insert(_CHANNEL_MEMBER).values(channel_id=all_id, member_id=owner_id, joined_at=ts)
    )

    # DEDAG：画布退役，bootstrap 不再为 #all 建 canvases 行（表冻结仅存量）。
    ws = models.row_dict(tx.conn.execute(select(_WS).where(_WS.c.id == ws_id)).mappings().first())
    return workspace_public(ws)


@router.get("/workspace", response_model=entities.WorkspacePublic)
def get_workspace(tx: Tx = Depends(get_tx)) -> Any:
    return workspace_public(require_workspace(tx.conn))


@router.patch("/workspace", response_model=entities.WorkspacePublic)
def patch_workspace(body: rest.WorkspacePatch, tx: Tx = Depends(get_tx)) -> Any:
    ws = require_workspace(tx.conn)
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    if changes:
        tx.conn.execute(update(_WS).where(_WS.c.id == ws["id"]).values(**changes))
    fresh = tx.conn.execute(select(_WS).where(_WS.c.id == ws["id"]).limit(1)).mappings().first()
    pub = workspace_public(models.row_dict(fresh))
    tx.emit(EventType.WORKSPACE_UPDATED, None, {"workspace": pub})
    return pub
