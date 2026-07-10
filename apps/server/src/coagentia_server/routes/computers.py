"""4.2 机器（契约 B §4.2）：列表 / Add（api_key 明文一次）/ Rename / Remove。"""

from __future__ import annotations

import hashlib
from typing import Any

from coagentia_contracts import entities, rest
from coagentia_contracts.enums import ComputerStatus
from coagentia_contracts.ws import EventType
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import insert, select, update

from coagentia_server.api import ApiError
from coagentia_server.db import models
from coagentia_server.deps import Tx, acting_member, get_tx, require_admin, require_workspace
from coagentia_server.ledger.service import new_ulid, now_iso
from coagentia_server.routes.serialize import computer_public

router = APIRouter(prefix="/api", tags=["computers"])

_COMPUTER = models.tbl(models.Computer)
_AGENT = models.tbl(models.Agent)


def _fetch_computer(tx: Tx, computer_id: str) -> dict[str, Any]:
    row = tx.conn.execute(select(_COMPUTER).where(_COMPUTER.c.id == computer_id)).mappings().first()
    if row is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "机器不存在")
    return dict(row)


@router.get("/computers", response_model=list[entities.ComputerPublic])
def list_computers(tx: Tx = Depends(get_tx)) -> Any:
    ws = require_workspace(tx.conn)
    rows = tx.conn.execute(
        select(_COMPUTER)
        .where(_COMPUTER.c.workspace_id == ws["id"])
        .order_by(_COMPUTER.c.created_at)
    ).mappings()
    return [computer_public(dict(r)) for r in rows]


@router.post("/computers", response_model=rest.ComputerCreated, status_code=201)
def add_computer(body: rest.ComputerCreate, request: Request, tx: Tx = Depends(get_tx)) -> Any:
    ws = require_workspace(tx.conn)
    require_admin(acting_member(request, tx.conn))

    api_key = f"cak_{new_ulid().lower()}"
    computer_id = new_ulid()
    tx.conn.execute(
        insert(_COMPUTER).values(
            id=computer_id,
            workspace_id=ws["id"],
            name=body.name,
            os=None,
            arch=None,
            daemon_version=None,
            api_key_hash=hashlib.sha256(api_key.encode()).hexdigest(),
            detected_runtimes=[],
            status=ComputerStatus.OFFLINE,
            last_seen_at=None,
            created_at=now_iso(),
        )
    )
    row = _fetch_computer(tx, computer_id)
    server_url = getattr(request.app.state, "server_url", "http://127.0.0.1:8787")
    return {
        "computer": computer_public(row),
        "api_key": api_key,  # 明文仅此一次（契约 B §4.2；库中只存 SHA-256 哈希）
        "command_line": f"uvx coagentia-daemon --server-url {server_url} --api-key {api_key}",
    }


@router.patch("/computers/{computer_id}", response_model=entities.ComputerPublic)
def rename_computer(
    computer_id: str, body: rest.ComputerPatch, request: Request, tx: Tx = Depends(get_tx)
) -> Any:
    require_admin(acting_member(request, tx.conn))
    _fetch_computer(tx, computer_id)
    tx.conn.execute(
        update(_COMPUTER).where(_COMPUTER.c.id == computer_id).values(name=body.name)
    )
    row = _fetch_computer(tx, computer_id)
    pub = computer_public(row)
    tx.emit(EventType.COMPUTER_UPDATED, None, {"computer": pub})
    return pub


@router.delete("/computers/{computer_id}", status_code=204)
def remove_computer(computer_id: str, request: Request, tx: Tx = Depends(get_tx)) -> Response:
    require_admin(acting_member(request, tx.conn))
    _fetch_computer(tx, computer_id)
    has_agent = tx.conn.execute(
        select(_AGENT.c.member_id).where(_AGENT.c.computer_id == computer_id).limit(1)
    ).first()
    if has_agent is not None:
        raise ApiError(
            409,
            rest.ErrorCode.COMPUTER_HAS_AGENTS,
            "该机器上仍有 Agent，先删除全部 Agent",
            rule="FR-2.7",
        )
    tx.conn.execute(models.tbl(models.Computer).delete().where(_COMPUTER.c.id == computer_id))
    return Response(status_code=204)
