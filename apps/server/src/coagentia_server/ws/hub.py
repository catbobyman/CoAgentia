"""WS 广播枢纽（契约 C §2/§3/§5/§8）：连接生命周期、信封组装、心跳、订阅流、owner presence。

架构（坑 3）：
- Hub 在 lifespan 起单一消费任务 `run()`，从进程内队列取事务后事件并广播；不在 WS handler 内起
  长期后台任务。
- 写端点为同步 def，在 FastAPI 线程池执行；`get_tx` 提交后调用 `bus.emit`（线程池线程）→
  Hub 的订阅回调用 `loop.call_soon_threadsafe` 把事件投回 loop 队列（契约 C §1.4 事务后发射）。
- 每连接一把 `asyncio.Lock`，seq 赋值与 send 在锁内原子完成 → 连接内 seq 单调无空洞（契约 C §3）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from coagentia_contracts.enums import MemberKind, PresenceStatus
from coagentia_contracts.ws import (
    HEARTBEAT_SEC,
    PROTOCOL_V,
    EventType,
    PingMsg,
    SubDeployLogMsg,
    SubDiagnosticMsg,
)
from sqlalchemy import select
from sqlalchemy.engine import Engine
from starlette.websockets import WebSocket, WebSocketState

from coagentia_server.db import models
from coagentia_server.events import EventBus, PendingEvent
from coagentia_server.ledger.service import new_ulid, now_iso

_WS = models.Workspace.__table__
_MEMBER = models.Member.__table__

# 幂等键 <实体>:<id>:<单调标记>（契约 C §3）——扫 payload 内的实体 dict 取 id + 时间标记。
_ENTITY_FIELDS = (
    "message", "task", "channel", "member", "computer", "workspace",
    "draft", "reminder", "deployment", "proposal", "item", "agent",
    "contract", "worktree", "preview", "edge", "node",
)
_MARKER_FIELDS = ("updated_at", "status_changed_at", "created_at")


def build_key(etype: str, data: dict[str, Any]) -> str:
    """幂等键：优先实体 id + 单调标记；否则退化到 member_id / 类型 + 时刻（契约 C §3）。"""
    for field_name in _ENTITY_FIELDS:
        entity = data.get(field_name)
        if isinstance(entity, dict) and "id" in entity:
            marker = next(
                (entity[m] for m in _MARKER_FIELDS if entity.get(m)), None
            ) or now_iso()
            return f"{field_name}:{entity['id']}:{marker}"
    if "member_id" in data:
        return f"{etype}:{data['member_id']}:{now_iso()}"
    if "channel_id" in data and data["channel_id"]:
        return f"{etype}:{data['channel_id']}:{now_iso()}"
    return f"{etype}:{now_iso()}"


@dataclass(eq=False)
class Connection:
    """一条 /api/ws 连接：seq 计数、发送锁、诊断订阅集合（契约 C §3/§8）。"""

    sock: WebSocket
    conn_id: str
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    seq: int = 0
    diagnostic_subs: set[str] = field(default_factory=set)  # 订阅的 agent_member_id 集合


class WsHub:
    """单进程 WS 广播枢纽：消费 EventBus → 各连接独立 seq 全量下发（契约 C §2/§3）。"""

    def __init__(self, engine: Engine, bus: EventBus, server_version: str) -> None:
        self._engine = engine
        self._bus = bus
        self._server_version = server_version
        self._conns: set[Connection] = set()
        self._queue: asyncio.Queue[PendingEvent] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sub_token: int | None = None
        self._workspace_id: str | None = None
        self._owner_member_id: str | None = None

    # ---------------------------------------------------------------- lifespan 装配

    def start(self, loop: asyncio.AbstractEventLoop) -> asyncio.Task[None]:
        """挂 lifespan：捕获 loop、订阅 bus、起消费任务（返回任务供 lifespan 收放）。"""
        self._loop = loop
        self._sub_token = self._bus.subscribe(self._on_bus_event)
        return loop.create_task(self.run())

    def stop(self) -> None:
        if self._sub_token is not None:
            self._bus.unsubscribe(self._sub_token)
            self._sub_token = None

    def _on_bus_event(self, event: PendingEvent) -> None:
        """EventBus 订阅回调（在写端点线程池线程执行）：跨回 loop 投队列（契约 C §1.4）。"""
        loop = self._loop
        if loop is None:
            return
        loop.call_soon_threadsafe(self._queue.put_nowait, event)

    async def run(self) -> None:
        """消费任务：按提交顺序取事件并广播（提交序 = 队列入队序，契约 C §1.4）。"""
        while True:
            event = await self._queue.get()
            await self._dispatch(event)

    # ---------------------------------------------------------------- 连接生命周期

    def _load_scope(self) -> None:
        """缓存 workspace_id 与 owner_member_id（信封作用域 + owner presence）。"""
        if self._workspace_id is not None:
            return
        with self._engine.connect() as conn:
            ws = conn.execute(select(_WS.c.id).limit(1)).first()
            owner = conn.execute(
                select(_MEMBER.c.id)
                .where(_MEMBER.c.kind == MemberKind.HUMAN, _MEMBER.c.role == "owner")
                .limit(1)
            ).first()
        self._workspace_id = ws[0] if ws is not None else ""
        self._owner_member_id = owner[0] if owner is not None else None

    async def attach(self, sock: WebSocket) -> Connection:
        """接受连接 → 立发 sys.hello（seq 1）→ 注册 → 首条连接广播 owner online（契约 C §2）。"""
        await sock.accept()
        self._load_scope()
        conn = Connection(sock=sock, conn_id=new_ulid())
        # 先发 hello（seq 1）再入广播集，保证 hello 是本连接首帧。
        await self._send(
            conn,
            EventType.SYS_HELLO,
            None,
            {
                "protocol_v": PROTOCOL_V,
                "server_version": self._server_version,
                "workspace_id": self._workspace_id,
                "conn_id": conn.conn_id,
                "heartbeat_sec": HEARTBEAT_SEC,
            },
        )
        first = len(self._conns) == 0
        self._conns.add(conn)
        if first:
            await self._broadcast_owner_presence(PresenceStatus.ONLINE)
        return conn

    async def detach(self, conn: Connection) -> None:
        """断连清连接与订阅；最后一条断开 → 广播 owner offline（契约 C §2 末）。"""
        self._conns.discard(conn)
        if not self._conns:
            await self._broadcast_owner_presence(PresenceStatus.OFFLINE)

    async def handle_uplink(self, conn: Connection, raw: Any) -> None:
        """上行分派（契约 C §5 全集仅三种）：ping / sub / unsub；未知忽略。"""
        if not isinstance(raw, dict):
            return
        mtype = raw.get("type")
        if mtype == "ping":
            PingMsg.model_validate(raw)
            await self._send(conn, EventType.SYS_PONG, None, {})
            return
        if mtype in ("sub", "unsub"):
            stream = raw.get("stream")
            if stream == "diagnostic":
                msg = SubDiagnosticMsg.model_validate(raw)
                if msg.type == "sub":
                    conn.diagnostic_subs.add(msg.agent_member_id)  # 重复 sub 幂等
                else:
                    conn.diagnostic_subs.discard(msg.agent_member_id)
            elif stream == "deploy_log":
                SubDeployLogMsg.model_validate(raw)  # M6 流：接受但 M1 无源（留接缝）

    # ---------------------------------------------------------------- 广播

    async def _dispatch(self, event: PendingEvent) -> None:
        """把一条 bus 事件广播给相关连接：诊断流按订阅过滤，其余全量（契约 C §6/§8）。"""
        if event.type == EventType.DIAGNOSTIC_APPENDED:
            agent_member_id = event.data.get("agent_member_id")
            targets = [c for c in self._conns if agent_member_id in c.diagnostic_subs]
        else:
            targets = list(self._conns)
        for conn in targets:
            await self._send(conn, event.type, event.channel_id, event.data)

    async def _broadcast_owner_presence(self, status: PresenceStatus) -> None:
        if self._owner_member_id is None:
            return
        data = {
            "member_id": self._owner_member_id,
            "kind": MemberKind.HUMAN.value,
            "status": status.value,
        }
        for conn in list(self._conns):
            await self._send(conn, EventType.PRESENCE_CHANGED, None, data)

    async def _send(
        self, conn: Connection, etype: EventType, channel_id: str | None, data: dict[str, Any]
    ) -> None:
        """连接内 seq 单调赋值 + 发送，锁内原子（契约 C §3：空洞 = 致命）。失败即断连清理。"""
        async with conn.lock:
            if conn.sock.client_state != WebSocketState.CONNECTED:
                self._conns.discard(conn)
                return
            conn.seq += 1
            envelope = {
                "v": PROTOCOL_V,
                "seq": conn.seq,
                "type": etype.value,
                "workspace_id": self._workspace_id,
                "channel_id": channel_id,
                "key": build_key(etype.value, data),
                "at": now_iso(),
                "data": data,
            }
            try:
                await conn.sock.send_json(envelope)
            except Exception:  # noqa: BLE001 — 单连接故障不阻断其它连接
                self._conns.discard(conn)
