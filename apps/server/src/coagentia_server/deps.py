"""请求级依赖：短事务 Connection + 提交后事件发射 + 主体身份解析（契约 A §1 / 契约 B §2）。

事务纪律（契约 A §1）：每请求一 Connection、一事务；写端点在**提交后**发射契约 C 事件
（事件缓冲在 Tx.pending，get_tx 于 commit 之后按序 flush 到 bus——契约 C §1.4）。

身份（契约 B §2，MVP）：浏览器 = Owner 人类（本地即身份，无登录）。daemon/Agent 主体必须
同时提供 Bearer computer api-key 与 `X-Acting-Member`，服务端校验 Agent 隶属该 Computer。
"""

from __future__ import annotations

import contextlib
import hashlib
from collections.abc import Iterator
from typing import Any

from coagentia_contracts import rest
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.engine import Connection

from coagentia_server.api import ApiError
from coagentia_server.db import models
from coagentia_server.events import EventBus, PendingEvent
from coagentia_server.files import FileStore

_WS = models.tbl(models.Workspace)
_MEMBER = models.tbl(models.Member)
_AGENT = models.tbl(models.Agent)
_COMPUTER = models.tbl(models.Computer)


class Tx:
    """请求级事务上下文：Connection + 待发事件 + app 级资源句柄。"""

    def __init__(self, conn: Connection, request: Request) -> None:
        self.conn = conn
        self.request = request
        self.pending: list[PendingEvent] = []
        self.bound_file_ids: list[str] = []

    @property
    def bus(self) -> EventBus:
        return self.request.app.state.bus

    @property
    def file_store(self) -> FileStore:
        return self.request.app.state.file_store

    def emit(self, etype: Any, channel_id: str | None, data: dict[str, Any]) -> None:
        """登记一条待广播事件（提交后由 get_tx flush，契约 C §1.4）。"""
        self.pending.append(PendingEvent(type=etype, channel_id=channel_id, data=data))

    def bind_file(self, file_id: str) -> str:
        """登记可补偿的 staging → files 搬运。"""
        stored_path = self.file_store.bind(file_id)
        self.bound_file_ids.append(file_id)
        return stored_path

    def rollback_file_bindings(self) -> None:
        for file_id in reversed(self.bound_file_ids):
            with contextlib.suppress(OSError):
                self.file_store.rollback_bind(file_id)

    def finalize_file_bindings(self) -> None:
        for file_id in self.bound_file_ids:
            # 数据库已经提交；清 sidecar 失败时正文和 files 行仍一致，后续可安全重试清理。
            with contextlib.suppress(OSError):
                self.file_store.finalize_bind(file_id)


def get_tx(request: Request) -> Iterator[Tx]:
    """开短事务 → yield Tx → 成功则 commit 后按序发射事件；异常回滚不发射。"""
    engine = request.app.state.engine
    conn = engine.connect()
    txn = conn.begin()
    tx = Tx(conn, request)
    try:
        yield tx
        txn.commit()
    except BaseException:
        if txn.is_active:
            txn.rollback()
        tx.rollback_file_bindings()
        conn.close()
        raise
    else:
        tx.finalize_file_bindings()
        conn.close()
        bus = request.app.state.bus
        for ev in tx.pending:
            bus.emit(ev.type, ev.channel_id, ev.data)


# ---------------------------------------------------------------- 身份解析


def workspace_row(conn: Connection) -> dict[str, Any] | None:
    row = conn.execute(select(_WS).limit(1)).mappings().first()
    return dict(row) if row is not None else None


def require_workspace(conn: Connection) -> dict[str, Any]:
    ws = workspace_row(conn)
    if ws is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "工作区尚未初始化（先 POST /workspace）")
    return ws


def owner_member(conn: Connection) -> dict[str, Any]:
    """Owner 人类成员（浏览器身份，契约 B §2）。"""
    row = (
        conn.execute(
            select(_MEMBER).where(_MEMBER.c.kind == "human", _MEMBER.c.role == "owner").limit(1)
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "Owner 成员不存在")
    return dict(row)


def acting_member(request: Request, conn: Connection) -> dict[str, Any]:
    """解析浏览器 Owner 或经 Computer api-key 认证的 Agent 主体（契约 B §2）。"""
    hdr = request.headers.get("X-Acting-Member")
    if not hdr:
        return owner_member(conn)

    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiError(
            403,
            rest.ErrorCode.PERMISSION_DENIED,
            "Agent 代理请求缺少有效的 Computer Bearer 凭证",
            rule="B§2",
        )

    digest = hashlib.sha256(token.encode()).hexdigest()
    row = (
        conn.execute(
            select(_MEMBER)
            .select_from(
                _MEMBER.join(_AGENT, _AGENT.c.member_id == _MEMBER.c.id).join(
                    _COMPUTER, _COMPUTER.c.id == _AGENT.c.computer_id
                )
            )
            .where(
                _MEMBER.c.id == hdr,
                _MEMBER.c.kind == "agent",
                _MEMBER.c.removed_at.is_(None),
                _COMPUTER.c.api_key_hash == digest,
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ApiError(
            403,
            rest.ErrorCode.PERMISSION_DENIED,
            "Agent 身份无效或不隶属该 Computer",
            rule="B§2",
        )
    return dict(row)


def is_admin(member: dict[str, Any]) -> bool:
    """admin 门（契约 B §2）：owner 与 admin 皆过。"""
    return member.get("role") in ("admin", "owner")


def require_admin(member: dict[str, Any]) -> None:
    if not is_admin(member):
        raise ApiError(
            403, rest.ErrorCode.PERMISSION_DENIED, "需要管理员权限", rule="admin"
        )
