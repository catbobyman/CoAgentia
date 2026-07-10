"""daemon 网关（契约 D §2–§8 的 server 端）：接入认证、握手、帧收发底座、对账器、
投递引擎、上报处理、断连级联、reminder 调度。

架构（与 WsHub 同构，坑 3）：
- Hub 在 lifespan 起：订阅 bus（消费 message.created 驱动投递）+ 周期对账 loop + reminder loop；
  捕获运行 loop 供 REST 端点跨线程投递指令（run_coroutine_threadsafe）。
- 每条 daemon 连接一个 `_reader` 协程顺序收帧；ack/reply 经 Future 唤醒等待方；
  report 分发到 §7 处置。指令下发经 `send_instr`：**同 Agent 串行**（per-agent 锁）+
  at-least-once（10s 超时原帧重发）。
- DB 写用 gateway_tx（提交后 bus.emit）；agents.status 唯一写入方 = agent.status_changed 上报
  与对账 #1 presence 纠偏（契约 D §7）。
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from coagentia_contracts.daemon import (
    ACK_TIMEOUT_SEC,
    CLOSE_PROTOCOL_MISMATCH,
    CLOSE_SUPERSEDED,
    DAEMON_PROTOCOL_V,
    RECONCILE_INTERVAL_SEC,
    AckFrame,
    AckResult,
    AgentBoot,
    AgentRefData,
    AgentStartData,
    AgentStatusChangedData,
    AgentWakeData,
    DaemonAgentActivityData,
    DaemonHelloAckData,
    DaemonHelloData,
    DiagnosticsBatchData,
    FrameKind,
    HomeFileQuery,
    HomeTreeQuery,
    InstrFrame,
    InstrType,
    MessageDeliverData,
    QueryFrame,
    QueryType,
    ReportType,
    RuntimesDetectedData,
    UsageBatchData,
    WakeRefs,
)
from coagentia_contracts.enums import (
    AgentStatus,
    ChannelKind,
    ComputerStatus,
    LifecycleAction,
    MemberKind,
    MessageKind,
    WakeReason,
)
from coagentia_contracts.ws import EventType
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection, Engine
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from coagentia_server.computers.gateway_tx import gateway_tx
from coagentia_server.db import models
from coagentia_server.events import EventBus
from coagentia_server.ledger.service import new_ulid, now_iso
from coagentia_server.routes.serialize import (
    computer_public,
    diagnostic_public,
    message_public,
    reminder_public,
)

_COMPUTER = models.Computer.__table__
_AGENT = models.Agent.__table__
_MEMBER = models.Member.__table__
_SKILL = models.AgentSkill.__table__
_CHANNEL = models.Channel.__table__
_CHANNEL_MEMBER = models.ChannelMember.__table__
_MSG = models.Message.__table__
_MENTION = models.MessageMention.__table__
_READ = models.ReadPosition.__table__
_DIAG = models.DiagnosticEvent.__table__
_USAGE = models.TokenUsageEvent.__table__
_TASK = models.Task.__table__
_REMINDER = models.Reminder.__table__

# 最后已知态里"应存活"的期望集合（对账 #2 自动 resume 的触发条件，契约 D §4.4）。
_RESUMABLE = {AgentStatus.STARTING.value, AgentStatus.IDLE.value, AgentStatus.BUSY.value}
_DELIVERABLE = {AgentStatus.IDLE.value, AgentStatus.BUSY.value}

_ACTIVITY_THROTTLE_SEC = 0.5  # 契约 D §7：server ≥500ms 节流转发 agent.activity
_LAST_SEEN_THROTTLE_SEC = 60  # 契约 D §2：last_seen_at 写库节流


class DaemonOffline(Exception):
    """无活跃 daemon 连接 / 指令 ack 或 query reply 超时（→ REST 503 DAEMON_OFFLINE）。"""


@dataclass
class DeliverMeta:
    """message.deliver 帧的投递游标元数据：ack(done) 后写该 Agent read_positions（§8.3）。"""

    agent_member_id: str
    channel_id: str
    workspace_id: str
    last_message_id: str


@dataclass(eq=False)
class DaemonConnection:
    sock: WebSocket
    computer_id: str
    workspace_id: str
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    agent_locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    pending_acks: dict[str, asyncio.Future] = field(default_factory=dict)
    pending_replies: dict[str, asyncio.Future] = field(default_factory=dict)
    deliver_meta: dict[str, DeliverMeta] = field(default_factory=dict)
    present: dict[str, str] = field(default_factory=dict)  # agent_member_id -> status
    tasks: set[asyncio.Task] = field(default_factory=set)
    last_ping_monotonic: float = field(default_factory=time.monotonic)
    last_seen_written: float = 0.0
    superseded: bool = False

    def agent_lock(self, agent_id: str) -> asyncio.Lock:
        lock = self.agent_locks.get(agent_id)
        if lock is None:
            lock = asyncio.Lock()
            self.agent_locks[agent_id] = lock
        return lock


class DaemonHub:
    """单进程 daemon 网关：每 computer 恰一活跃连接（契约 D §2 单连接规则）。"""

    def __init__(
        self,
        engine: Engine,
        bus: EventBus,
        server_version: str,
        *,
        ack_timeout: float = ACK_TIMEOUT_SEC,
        query_timeout: float = ACK_TIMEOUT_SEC,
        reconcile_interval: float = RECONCILE_INTERVAL_SEC,
        reminder_interval: float = 5.0,
        heartbeat_timeout: float = 60.0,
    ) -> None:
        self._engine = engine
        self._bus = bus
        self._server_version = server_version
        self.ack_timeout = ack_timeout
        self.query_timeout = query_timeout
        self.reconcile_interval = reconcile_interval
        self.reminder_interval = reminder_interval
        self.heartbeat_timeout = heartbeat_timeout
        self._conns: dict[str, DaemonConnection] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sub_token: int | None = None
        self._bg: list[asyncio.Task] = []
        self._activity_last: dict[str, float] = {}

    # ---------------------------------------------------------------- lifespan 装配

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._sub_token = self._bus.subscribe(self._on_bus_event)
        self._bg = [
            loop.create_task(self._reconcile_loop()),
            loop.create_task(self._reminder_loop()),
            loop.create_task(self._heartbeat_loop()),
        ]

    async def stop(self) -> None:
        if self._sub_token is not None:
            self._bus.unsubscribe(self._sub_token)
            self._sub_token = None
        for task in self._bg:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._bg = []

    def _on_bus_event(self, event: Any) -> None:
        """bus 订阅回调（可能在线程池线程）：message.created → 投递引擎（§8）。"""
        if event.type != EventType.MESSAGE_CREATED:
            return
        loop = self._loop
        if loop is None:
            return
        message = event.data.get("message") or {}
        mid = message.get("id")
        cid = message.get("channel_id")
        if mid and cid:
            loop.call_soon_threadsafe(self._spawn, self._deliver_message(mid, cid))

    def _spawn(self, coro: Any) -> None:
        task = self._loop.create_task(coro)  # type: ignore[union-attr]
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

    # ---------------------------------------------------------------- 接入认证 + 握手

    async def serve(self, sock: WebSocket) -> None:
        """/api/daemon/ws 端点主体：认证 → 顶掉旧连接 → 握手 → 对账 → 收帧循环。"""
        computer = await self._authenticate(sock)
        if computer is None:
            return  # 认证失败：升级前已拒（契约 D §2）

        computer_id = computer["id"]
        # 单连接规则：同 computer 新连接顶掉旧连接（close 4001，契约 D §2）。
        old = self._conns.pop(computer_id, None)
        if old is not None:
            old.superseded = True
            with contextlib.suppress(Exception):
                await old.sock.close(code=CLOSE_SUPERSEDED)

        await sock.accept()
        conn = DaemonConnection(
            sock=sock, computer_id=computer_id, workspace_id=computer["workspace_id"]
        )
        try:
            hello = await self._recv_hello(conn)
            if hello is None:
                return
            hello_frame_id, hello_data = hello
            self._register_hello(conn, hello_data)
            await self._send_hello_ack(conn, hello_frame_id)
            self._conns[computer_id] = conn
            self._spawn_on_conn(conn, self.reconcile(conn))
            await self._reader(conn)
        except WebSocketDisconnect:
            pass
        finally:
            await self._teardown(conn)

    async def _authenticate(self, sock: WebSocket) -> dict[str, Any] | None:
        """Bearer <api-key> → SHA-256 比对 computers.api_key_hash；失败升级前拒（契约 D §2）。"""
        auth = sock.headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        if not token:
            await sock.close(code=1008)  # 契约意图 HTTP 401；WS 层以策略违规关闭
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self._engine.connect() as c:
            row = c.execute(
                select(_COMPUTER).where(_COMPUTER.c.api_key_hash == digest)
            ).mappings().first()
        if row is None:
            await sock.close(code=1008)
            return None
        return dict(row)

    async def _recv_hello(
        self, conn: DaemonConnection
    ) -> tuple[str, DaemonHelloData] | None:
        raw = await conn.sock.receive_json()
        if raw.get("kind") != FrameKind.REPORT or raw.get("type") != ReportType.HELLO:
            await conn.sock.close(code=CLOSE_PROTOCOL_MISMATCH)
            return None
        if int(raw.get("v", DAEMON_PROTOCOL_V)) > DAEMON_PROTOCOL_V:
            await conn.sock.close(code=CLOSE_PROTOCOL_MISMATCH)  # 过高版本（契约 D §2）
            return None
        return raw["frame_id"], DaemonHelloData.model_validate(raw["data"])

    def _register_hello(self, conn: DaemonConnection, hello: DaemonHelloData) -> None:
        """更新 computers 行 + 广播 computer.connected/updated（契约 D §4.1）。"""
        conn.present = {a.agent_member_id: a.status.value for a in hello.agents}
        with gateway_tx(self._engine, self._bus) as tx:
            tx.conn.execute(
                update(_COMPUTER)
                .where(_COMPUTER.c.id == conn.computer_id)
                .values(
                    os=hello.os,
                    arch=hello.arch,
                    daemon_version=hello.daemon_version,
                    detected_runtimes=[r.model_dump(mode="json") for r in hello.detected_runtimes],
                    status=ComputerStatus.CONNECTED,
                    last_seen_at=now_iso(),
                )
            )
            row = dict(
                tx.conn.execute(
                    select(_COMPUTER).where(_COMPUTER.c.id == conn.computer_id)
                ).mappings().first()
            )
            pub = computer_public(row)
            tx.emit(EventType.COMPUTER_CONNECTED, None, {"computer": pub})
            tx.emit(EventType.COMPUTER_UPDATED, None, {"computer": pub})
        conn.last_seen_written = time.monotonic()

    async def _send_hello_ack(self, conn: DaemonConnection, ref: str) -> None:
        """hello_ack（借 ack 信封承载 DaemonHelloAckData，ref 指向 hello 帧；契约 D §4.1）。"""
        data = DaemonHelloAckData(
            protocol_v=DAEMON_PROTOCOL_V,
            server_version=self._server_version,
            computer_id=conn.computer_id,
            workspace_id=conn.workspace_id,
            heartbeat_sec=25,
        )
        await self._send_raw(
            conn,
            {
                "v": DAEMON_PROTOCOL_V,
                "kind": FrameKind.ACK.value,
                "ref": ref,
                "result": AckResult.DONE.value,
                "data": data.model_dump(mode="json"),
            },
        )

    # ---------------------------------------------------------------- 收帧循环 + 分发

    async def _reader(self, conn: DaemonConnection) -> None:
        while True:
            raw = await conn.sock.receive_json()
            kind = raw.get("kind")
            if kind == FrameKind.ACK:
                self._handle_ack(conn, AckFrame.model_validate(raw))
            elif kind == FrameKind.REPLY:
                self._handle_reply(conn, raw)
            elif kind == FrameKind.PING:
                await self._handle_ping(conn)
            elif kind == FrameKind.REPORT:
                await self._handle_report(conn, raw)
            # instr / query 仅 server→daemon，daemon 侧不应发；忽略

    def _handle_ack(self, conn: DaemonConnection, ack: AckFrame) -> None:
        """ack 到达：deliver 帧先写 read_positions（§8.3）再唤醒等待方。

        仅 result=DONE 推进 read_position（契约 D §5.2「deliver ack(done) 后写 read_positions」）。
        NOOP = 该批已喂过（daemon 按频道去重命中，或原帧 at-least-once 重发命中 frame_id 短窗）——
        原 DONE（TCP 有序 + daemon 处理后才记 frame_id）必已先到并推进过，NOOP 不应再推
        （否则跨频道 daemon 全局去重误判 NOOP 时会把落后频道的 read_position 错误推进 → 丢消息）。
        """
        meta = conn.deliver_meta.pop(ack.ref, None)
        if meta is not None and ack.result == AckResult.DONE:
            self._write_read_position(meta)
        fut = conn.pending_acks.get(ack.ref)
        if fut is not None and not fut.done():
            fut.set_result(ack)

    def _handle_reply(self, conn: DaemonConnection, raw: dict[str, Any]) -> None:
        fut = conn.pending_replies.get(raw.get("ref", ""))
        if fut is not None and not fut.done():
            fut.set_result(raw.get("data"))

    async def _handle_ping(self, conn: DaemonConnection) -> None:
        conn.last_ping_monotonic = time.monotonic()
        now = time.monotonic()
        if now - conn.last_seen_written >= _LAST_SEEN_THROTTLE_SEC:
            conn.last_seen_written = now
            with self._engine.begin() as c:
                c.execute(
                    update(_COMPUTER)
                    .where(_COMPUTER.c.id == conn.computer_id)
                    .values(last_seen_at=now_iso())
                )
        await self._send_raw(conn, {"v": DAEMON_PROTOCOL_V, "kind": FrameKind.PONG.value})

    # ---------------------------------------------------------------- 上报处理（契约 D §7）

    async def _handle_report(self, conn: DaemonConnection, raw: dict[str, Any]) -> None:
        rtype = raw.get("type")
        data = raw.get("data") or {}
        frame_id = raw.get("frame_id")
        if rtype == ReportType.AGENT_STATUS_CHANGED:
            self._report_status_changed(conn, AgentStatusChangedData.model_validate(data))
        elif rtype == ReportType.AGENT_ACTIVITY:
            self._report_activity(DaemonAgentActivityData.model_validate(data))
        elif rtype == ReportType.RUNTIMES_DETECTED:
            self._report_runtimes(conn, RuntimesDetectedData.model_validate(data))
        elif rtype == ReportType.DIAGNOSTICS_BATCH:
            self._report_diagnostics(conn, DiagnosticsBatchData.model_validate(data))
            await self._ack(conn, frame_id, AckResult.DONE)
        elif rtype == ReportType.USAGE_BATCH:
            self._report_usage(conn, UsageBatchData.model_validate(data))
            await self._ack(conn, frame_id, AckResult.DONE)
        # hello（重复）/ M6/M7 上报：M1 忽略

    def _report_status_changed(self, conn: DaemonConnection, d: AgentStatusChangedData) -> None:
        """agents.status 的唯一写入方（契约 D §7）+ 广播 presence.changed。"""
        if d.status.value in _RESUMABLE:
            conn.present[d.agent_member_id] = d.status.value
        else:
            conn.present.pop(d.agent_member_id, None)
        with gateway_tx(self._engine, self._bus) as tx:
            tx.conn.execute(
                update(_AGENT)
                .where(_AGENT.c.member_id == d.agent_member_id)
                .values(status=d.status)
            )
            tx.emit(
                EventType.PRESENCE_CHANGED,
                None,
                {
                    "member_id": d.agent_member_id,
                    "kind": MemberKind.AGENT.value,
                    "status": d.status.value,
                },
            )

    def _report_activity(self, d: DaemonAgentActivityData) -> None:
        """节流转发 agent.activity（每 Agent ≥500ms，不入库；契约 D §7）。"""
        now = time.monotonic()
        last = self._activity_last.get(d.agent_member_id, 0.0)
        if now - last < _ACTIVITY_THROTTLE_SEC:
            return
        self._activity_last[d.agent_member_id] = now
        self._bus.emit(
            EventType.AGENT_ACTIVITY, None, {"member_id": d.agent_member_id, "detail": d.detail}
        )

    def _report_runtimes(self, conn: DaemonConnection, d: RuntimesDetectedData) -> None:
        with gateway_tx(self._engine, self._bus) as tx:
            tx.conn.execute(
                update(_COMPUTER)
                .where(_COMPUTER.c.id == conn.computer_id)
                .values(detected_runtimes=[r.model_dump(mode="json") for r in d.runtimes])
            )
            row = dict(
                tx.conn.execute(
                    select(_COMPUTER).where(_COMPUTER.c.id == conn.computer_id)
                ).mappings().first()
            )
            tx.emit(EventType.COMPUTER_UPDATED, None, {"computer": computer_public(row)})

    def _report_diagnostics(self, conn: DaemonConnection, d: DiagnosticsBatchData) -> None:
        """逐条 INSERT diagnostic_events（seq server 赋，重复可容忍）+ 转发订阅流（契约 D §7）。"""
        with gateway_tx(self._engine, self._bus) as tx:
            by_agent: dict[str, list[dict[str, Any]]] = {}
            for ev in d.events:
                res = tx.conn.execute(
                    insert(_DIAG)
                    .values(
                        workspace_id=conn.workspace_id,
                        agent_member_id=ev.agent_member_id,
                        type=ev.type,
                        channel_id=ev.channel_id,
                        task_id=ev.task_id,
                        batch_id=ev.batch_id,
                        payload=ev.payload,
                        created_at=ev.at,
                    )
                    .returning(_DIAG.c.seq)
                )
                seq = res.scalar_one()
                if ev.agent_member_id is None:
                    continue
                pub = diagnostic_public(
                    {
                        "seq": seq,
                        "workspace_id": conn.workspace_id,
                        "agent_member_id": ev.agent_member_id,
                        "type": ev.type,
                        "channel_id": ev.channel_id,
                        "task_id": ev.task_id,
                        "batch_id": ev.batch_id,
                        "payload": ev.payload,
                        "created_at": ev.at,
                    }
                )
                by_agent.setdefault(ev.agent_member_id, []).append(pub)
            for agent_id, events in by_agent.items():
                tx.emit(
                    EventType.DIAGNOSTIC_APPENDED,
                    None,
                    {"agent_member_id": agent_id, "events": events},
                )

    def _report_usage(self, conn: DaemonConnection, d: UsageBatchData) -> None:
        """id(ULID) 主键 INSERT OR IGNORE → exactly-once；仅新行广播 token_usage.reported（§7）。"""
        with gateway_tx(self._engine, self._bus) as tx:
            for ev in d.events:
                exists = tx.conn.execute(
                    select(_USAGE.c.id).where(_USAGE.c.id == ev.id)
                ).first()
                if exists is not None:
                    continue  # 重传去重（铁律 5）
                # 归属富化（契约 E §7.4）：thread_root_id 命中 tasks.root_message_id → task_id。
                # 三路：无提示→None；有提示无匹配→None；命中→task.id。
                task_id: str | None = None
                if ev.thread_root_id is not None:
                    task_row = tx.conn.execute(
                        select(_TASK.c.id).where(_TASK.c.root_message_id == ev.thread_root_id)
                    ).first()
                    if task_row is not None:
                        task_id = task_row[0]
                tx.conn.execute(
                    insert(_USAGE).values(
                        id=ev.id,
                        workspace_id=conn.workspace_id,
                        agent_member_id=ev.agent_member_id,
                        task_id=task_id,
                        channel_id=ev.channel_id,
                        input_tokens=ev.input_tokens,
                        output_tokens=ev.output_tokens,
                        cache_read_tokens=ev.cache_read_tokens,
                        cache_write_tokens=ev.cache_write_tokens,
                        source_session=ev.source_session,
                        reported_at=ev.reported_at,
                    )
                )
                tx.emit(
                    EventType.TOKEN_USAGE_REPORTED,
                    None,
                    {
                        "agent_member_id": ev.agent_member_id,
                        "task_id": task_id,
                        "totals": {
                            "input_tokens": ev.input_tokens,
                            "output_tokens": ev.output_tokens,
                            "cache_read_tokens": ev.cache_read_tokens,
                            "cache_write_tokens": ev.cache_write_tokens,
                        },
                    },
                )

    # ---------------------------------------------------------------- 帧下发底座（契约 D §3）

    async def _send_raw(self, conn: DaemonConnection, payload: dict[str, Any]) -> None:
        async with conn.send_lock:
            if conn.sock.client_state != WebSocketState.CONNECTED:
                raise DaemonOffline("连接已关闭")
            await conn.sock.send_json(payload)

    async def _ack(self, conn: DaemonConnection, ref: str | None, result: AckResult) -> None:
        if ref is None:
            return
        await self._send_raw(
            conn,
            {
                "v": DAEMON_PROTOCOL_V,
                "kind": FrameKind.ACK.value,
                "ref": ref,
                "result": result.value,
            },
        )

    async def send_instr(
        self,
        conn: DaemonConnection,
        agent_id: str,
        itype: InstrType,
        data: Any = None,
        *,
        deliver_meta: DeliverMeta | None = None,
        prepare: Callable[[], tuple[Any, DeliverMeta | None] | None] | None = None,
    ) -> AckFrame | None:
        """同 Agent 串行 + at-least-once（10s ack 超时原帧重发；契约 D §3/§8）。

        `prepare`（可选）在**取得 agent_lock 之后、构帧之前**重算 (data, deliver_meta)：投递引擎
        用它让 busy 期并发投递任务在锁内重算积压，反映此前 ack 已推进的 read_position，避免发送
        陈旧重叠批（#5 重复投递）。返回 None ⇒ 无内容可投（并发任务已投完）→ 不发帧，返回 None。
        """
        async with conn.agent_lock(agent_id):
            if prepare is not None:
                prepared = prepare()
                if prepared is None:
                    return None
                data, deliver_meta = prepared
            frame = InstrFrame(
                frame_id=new_ulid(), type=itype, at=now_iso(), data=data.model_dump(mode="json")
            )
            payload = frame.model_dump(mode="json")
            while True:
                fut: asyncio.Future = self._loop.create_future()  # type: ignore[union-attr]
                conn.pending_acks[frame.frame_id] = fut
                if deliver_meta is not None:
                    conn.deliver_meta[frame.frame_id] = deliver_meta
                await self._send_raw(conn, payload)
                try:
                    return await asyncio.wait_for(fut, timeout=self.ack_timeout)
                except TimeoutError:
                    if conn.superseded or conn.sock.client_state != WebSocketState.CONNECTED:
                        raise DaemonOffline("重发前连接失效") from None
                    continue  # 原帧原样重发（同 frame_id）
                finally:
                    conn.pending_acks.pop(frame.frame_id, None)

    async def send_query(
        self, conn: DaemonConnection, qtype: QueryType, data: Any
    ) -> dict[str, Any]:
        """query/reply（10s 超时 → DAEMON_OFFLINE；不重试，契约 D §6）。"""
        frame = QueryFrame(
            frame_id=new_ulid(), type=qtype, at=now_iso(), data=data.model_dump(mode="json")
        )
        fut: asyncio.Future = self._loop.create_future()  # type: ignore[union-attr]
        conn.pending_replies[frame.frame_id] = fut
        try:
            await self._send_raw(conn, frame.model_dump(mode="json"))
            return await asyncio.wait_for(fut, timeout=self.query_timeout)
        except (TimeoutError, DaemonOffline):
            raise DaemonOffline("query 超时或连接失效") from None
        finally:
            conn.pending_replies.pop(frame.frame_id, None)

    # ---------------------------------------------------------------- 投递引擎（契约 D §8）

    def _write_read_position(self, meta: DeliverMeta) -> None:
        """deliver ack(done) → 写该 Agent read_positions + 广播 read.updated（§8.3 游标即已读）。"""
        with gateway_tx(self._engine, self._bus) as tx:
            exists = tx.conn.execute(
                select(_READ.c.member_id).where(
                    _READ.c.member_id == meta.agent_member_id,
                    _READ.c.channel_id == meta.channel_id,
                )
            ).first()
            if exists is None:
                tx.conn.execute(
                    insert(_READ).values(
                        member_id=meta.agent_member_id,
                        channel_id=meta.channel_id,
                        last_read_message_id=meta.last_message_id,
                        last_read_at=now_iso(),
                    )
                )
            else:
                tx.conn.execute(
                    update(_READ)
                    .where(
                        _READ.c.member_id == meta.agent_member_id,
                        _READ.c.channel_id == meta.channel_id,
                    )
                    .values(last_read_message_id=meta.last_message_id, last_read_at=now_iso())
                )
            tx.emit(
                EventType.READ_UPDATED,
                meta.channel_id,
                {
                    "channel_id": meta.channel_id,
                    "member_id": meta.agent_member_id,
                    "last_read_message_id": meta.last_message_id,
                },
            )

    async def _deliver_message(self, message_id: str, channel_id: str) -> None:
        """新消息投递（bus 驱动）：决定给谁/何时/是否唤醒（契约 D §8.1/§8.2）。"""
        with self._engine.connect() as c:
            msg = c.execute(select(_MSG).where(_MSG.c.id == message_id)).mappings().first()
            channel = (
                c.execute(select(_CHANNEL).where(_CHANNEL.c.id == channel_id)).mappings().first()
            )
            if msg is None or channel is None:
                return
            msg = dict(msg)
            channel = dict(channel)
            recipients = self._channel_agent_recipients(c, channel_id)
        for agent_id, computer_id in recipients:
            conn = self._conns.get(computer_id)
            if conn is None:
                continue  # daemon 离线 → 积压，重连对账 #3 补
            status = conn.present.get(agent_id)
            if status not in _DELIVERABLE:
                continue  # offline/starting/error → 不投不唤醒（§8.2）
            with self._engine.connect() as c:
                reason = self._compute_trigger(c, msg, channel, agent_id)
            if status == AgentStatus.BUSY.value:
                await self._deliver_backlog(conn, agent_id, channel_id)  # 直投（§8.2）
            elif reason is not None:  # idle + 触发命中 → wake + deliver
                await self.send_instr(
                    conn,
                    agent_id,
                    InstrType.AGENT_WAKE,
                    AgentWakeData(
                        agent_member_id=agent_id,
                        reason=reason,
                        refs=WakeRefs(message_ids=[message_id]),
                    ),
                )
                await self._deliver_backlog(conn, agent_id, channel_id)
            # idle + 未命中 → 静默积压（不发帧，随下次唤醒随批投递）

    async def _deliver_backlog(
        self, conn: DaemonConnection, agent_id: str, channel_id: str
    ) -> None:
        """投递积压批 = read_position 之后全部应投消息，一次喂齐（§8.2）。

        积压在 send_instr 取得 agent_lock **之后**重算（prepare）：busy 期每条新消息各起一个并发
        投递任务，若在锁外先算批、锁内后发，相邻批会重叠重投较低 message_id（#5）。锁内重算使
        后到任务反映此前 ack 已推进的 read_position——已投完则返回 None 不发帧。
        """
        def _prepare() -> tuple[MessageDeliverData, DeliverMeta] | None:
            with self._engine.connect() as c:
                backlog = self._backlog(c, agent_id, channel_id)
            if not backlog:
                return None
            messages = [message_public(m) for m in backlog]
            meta = DeliverMeta(
                agent_member_id=agent_id,
                channel_id=channel_id,
                workspace_id=conn.workspace_id,
                last_message_id=backlog[-1]["id"],
            )
            data = MessageDeliverData(
                agent_member_id=agent_id, channel_id=channel_id, messages=messages
            )
            return data, meta

        await self.send_instr(
            conn, agent_id, InstrType.MESSAGE_DELIVER, prepare=_prepare
        )

    def _compute_trigger(
        self, c: Connection, msg: dict[str, Any], channel: dict[str, Any], agent_id: str
    ) -> WakeReason | None:
        """唤醒触发判定（契约 D §8.2）：DM 恒触发 / @mention / reminder 锚点系统消息视同 mention。

        对账 #3 的"无触发→静默积压"要求普通频道非 @ 消息**不**唤醒——故只有 DM、@mention、
        reminder 锚点（system+mention）构成唤醒触发；其余进静默积压随下次唤醒随批投递。
        """
        if msg.get("author_member_id") == agent_id:
            return None  # 不因自己发的消息被唤醒
        mentioned = c.execute(
            select(_MENTION.c.member_id).where(
                _MENTION.c.message_id == msg["id"], _MENTION.c.member_id == agent_id
            )
        ).first() is not None
        if msg.get("kind") == MessageKind.SYSTEM.value and mentioned:
            return WakeReason.REMINDER  # reminder 锚点系统消息视同 @mention（§4.4 #8）
        if channel.get("kind") == ChannelKind.DM.value:
            return WakeReason.CHANNEL_MESSAGE  # DM 必达恒触发（FR-4.7）
        if mentioned:
            return WakeReason.MENTION
        return None

    def _channel_agent_recipients(
        self, c: Connection, channel_id: str
    ) -> list[tuple[str, str]]:
        rows = c.execute(
            select(_AGENT.c.member_id, _AGENT.c.computer_id)
            .select_from(_CHANNEL_MEMBER.join(_MEMBER, _CHANNEL_MEMBER.c.member_id == _MEMBER.c.id)
                         .join(_AGENT, _AGENT.c.member_id == _MEMBER.c.id))
            .where(
                _CHANNEL_MEMBER.c.channel_id == channel_id,
                _MEMBER.c.kind == MemberKind.AGENT,
                _MEMBER.c.removed_at.is_(None),
            )
        ).all()
        return [(r[0], r[1]) for r in rows]

    def _backlog(self, c: Connection, agent_id: str, channel_id: str) -> list[dict[str, Any]]:
        read = c.execute(
            select(_READ.c.last_read_message_id).where(
                _READ.c.member_id == agent_id, _READ.c.channel_id == channel_id
            )
        ).first()
        # 排除收件 Agent 自己发的消息（§8：自己发的不回喂，避免自我应答回环 #3）；系统消息
        # author=NULL 仍投。与 _compute_trigger 的 self-author 排除同源。
        stmt = select(_MSG).where(
            _MSG.c.channel_id == channel_id,
            (_MSG.c.author_member_id.is_(None)) | (_MSG.c.author_member_id != agent_id),
        )
        if read is not None:
            stmt = stmt.where(_MSG.c.id > read[0])  # ULID 单调 ⇒ 字典序即时序
        rows = c.execute(stmt.order_by(_MSG.c.created_at, _MSG.c.id)).mappings()
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------- 对账器（契约 D §4.4）

    async def reconcile(self, conn: DaemonConnection) -> None:
        """重连握手后 + 周期兜底扫描共用（契约 D §4.4）。

        M1 规则：#1 presence 纠偏、#2 自动 resume、#3 投递补投、#8 reminder 补触发（#3 特例）。
        """
        present = dict(conn.present)
        resume_boots: list[AgentBoot] = []
        deliver_plans: list[tuple[str, str, list[dict[str, Any]], WakeReason | None, bool]] = []
        with gateway_tx(self._engine, self._bus) as tx:
            agents = self._agents_on_computer(tx.conn, conn.computer_id)
            status_by_id = {a["member_id"]: a["status"] for a in agents}
            # #1 presence 纠偏：以 daemon 进程表为准写 agents.status + 广播。
            for a in agents:
                aid = a["member_id"]
                if aid in present and status_by_id[aid] != present[aid]:
                    tx.conn.execute(
                        update(_AGENT).where(_AGENT.c.member_id == aid).values(status=present[aid])
                    )
                    status_by_id[aid] = present[aid]
                    tx.emit(
                        EventType.PRESENCE_CHANGED,
                        None,
                        {"member_id": aid, "kind": MemberKind.AGENT.value, "status": present[aid]},
                    )
            # #2 自动 resume：最后已知态应存活但不在进程表 → agent.start。
            for a in sorted(agents, key=lambda x: x["member_id"]):
                aid = a["member_id"]
                if aid not in present and status_by_id[aid] in _RESUMABLE:
                    resume_boots.append(self._agent_boot(tx.conn, a))
            # #3/#8 投递补投：present 的 idle/busy Agent 逐频道积压 → wake+deliver / 静默。
            for a in sorted(agents, key=lambda x: x["member_id"]):
                aid = a["member_id"]
                if aid not in present:
                    continue
                status = present[aid]
                if status not in _DELIVERABLE:
                    continue
                for ch in self._agent_channels(tx.conn, aid):
                    backlog = self._backlog(tx.conn, aid, ch["id"])
                    if not backlog:
                        continue
                    if status == AgentStatus.BUSY.value:
                        deliver_plans.append((aid, ch["id"], backlog, None, True))
                    else:  # idle：积压中命中任一触发 → wake+deliver；否则静默积压
                        reason = self._backlog_trigger(tx.conn, backlog, ch, aid)
                        if reason is not None:
                            deliver_plans.append((aid, ch["id"], backlog, reason, True))
        # 事务外下发指令（DB 已提交，避免持锁跨 await）。
        for boot in resume_boots:
            await self.send_instr(
                conn, boot.agent_member_id, InstrType.AGENT_START, AgentStartData(agent=boot)
            )
        for aid, channel_id, backlog, reason, _do in deliver_plans:
            if reason is not None:
                await self.send_instr(
                    conn,
                    aid,
                    InstrType.AGENT_WAKE,
                    AgentWakeData(
                        agent_member_id=aid,
                        reason=reason,
                        refs=WakeRefs(message_ids=[m["id"] for m in backlog]),
                    ),
                )
            messages = [message_public(m) for m in backlog]
            meta = DeliverMeta(
                agent_member_id=aid,
                channel_id=channel_id,
                workspace_id=conn.workspace_id,
                last_message_id=backlog[-1]["id"],
            )
            await self.send_instr(
                conn,
                aid,
                InstrType.MESSAGE_DELIVER,
                MessageDeliverData(agent_member_id=aid, channel_id=channel_id, messages=messages),
                deliver_meta=meta,
            )

    def _backlog_trigger(
        self, c: Connection, backlog: list[dict[str, Any]], channel: dict[str, Any], agent_id: str
    ) -> WakeReason | None:
        for m in backlog:
            reason = self._compute_trigger(c, m, channel, agent_id)
            if reason is not None:
                return reason
        return None

    def _agents_on_computer(self, c: Connection, computer_id: str) -> list[dict[str, Any]]:
        rows = c.execute(
            select(_AGENT, _MEMBER.c.name)
            .select_from(_AGENT.join(_MEMBER, _AGENT.c.member_id == _MEMBER.c.id))
            .where(_AGENT.c.computer_id == computer_id, _MEMBER.c.removed_at.is_(None))
        ).mappings()
        return [dict(r) for r in rows]

    def _agent_channels(self, c: Connection, agent_id: str) -> list[dict[str, Any]]:
        rows = c.execute(
            select(_CHANNEL)
            .select_from(
                _CHANNEL.join(_CHANNEL_MEMBER, _CHANNEL.c.id == _CHANNEL_MEMBER.c.channel_id)
            )
            .where(_CHANNEL_MEMBER.c.member_id == agent_id, _CHANNEL.c.archived_at.is_(None))
        ).mappings()
        return [dict(r) for r in rows]

    def _agent_boot(self, c: Connection, agent: dict[str, Any]) -> AgentBoot:
        skills = [
            r[0]
            for r in c.execute(
                select(_SKILL.c.skill).where(_SKILL.c.agent_member_id == agent["member_id"])
            ).all()
        ]
        return AgentBoot(
            agent_member_id=agent["member_id"],
            name=agent["name"],
            runtime=agent["runtime"],
            model=agent["model"],
            home_path=agent["home_path"],
            skills=skills,
        )

    # ---------------------------------------------------------------- REST 同步桥（契约 D §5/§6）

    def send_lifecycle(self, agent_id: str, action: LifecycleAction) -> str:
        """生命周期指令同步下发（契约 D §4.3；离线 → DaemonOffline）。返回 ack result。"""
        conn, agent = self._require_conn_for_agent(agent_id)
        with self._engine.connect() as c:
            boot = self._agent_boot(c, agent)
        if action == LifecycleAction.START:
            itype, data = InstrType.AGENT_START, AgentStartData(agent=boot)
        elif action == LifecycleAction.STOP:
            itype, data = InstrType.AGENT_STOP, AgentRefData(agent_member_id=agent_id)
        elif action == LifecycleAction.RESTART:
            itype, data = InstrType.AGENT_RESTART, AgentStartData(agent=boot)
        elif action == LifecycleAction.RESET_SESSION:
            itype, data = InstrType.AGENT_RESET_SESSION, AgentStartData(agent=boot)
        else:  # RESET_FULL
            itype, data = InstrType.AGENT_RESET_FULL, AgentStartData(agent=boot)
        ack = self._run_sync(self.send_instr(conn, agent_id, itype, data))
        return ack.result.value

    def query_home_tree(self, agent_id: str, path: str) -> dict[str, Any]:
        conn, _agent = self._require_conn_for_agent(agent_id)
        return self._run_sync(
            self.send_query(
                conn, QueryType.HOME_TREE, HomeTreeQuery(agent_member_id=agent_id, path=path)
            )
        )

    def query_home_file(self, agent_id: str, path: str) -> dict[str, Any]:
        conn, _agent = self._require_conn_for_agent(agent_id)
        return self._run_sync(
            self.send_query(
                conn, QueryType.HOME_FILE, HomeFileQuery(agent_member_id=agent_id, path=path)
            )
        )

    def _require_conn_for_agent(self, agent_id: str) -> tuple[DaemonConnection, dict[str, Any]]:
        with self._engine.connect() as c:
            agent = c.execute(
                select(_AGENT, _MEMBER.c.name)
                .select_from(_AGENT.join(_MEMBER, _AGENT.c.member_id == _MEMBER.c.id))
                .where(_AGENT.c.member_id == agent_id)
            ).mappings().first()
        if agent is None:
            raise DaemonOffline("Agent 不存在")
        conn = self._conns.get(agent["computer_id"])
        if conn is None:
            raise DaemonOffline("daemon 离线")
        return conn, dict(agent)

    def _run_sync(self, coro: Any) -> Any:
        """从线程池线程把协程投到 loop 执行并阻塞取结果（REST 端点用）。"""
        if self._loop is None:
            raise DaemonOffline("网关未就绪")
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return fut.result(timeout=self.query_timeout + self.ack_timeout + 5)
        except DaemonOffline:
            raise
        except Exception as exc:  # noqa: BLE001 — 超时/连接失效统一收敛为 DAEMON_OFFLINE
            raise DaemonOffline(str(exc)) from exc

    # ---------------------------------------------------------------- reminder 调度（§4.4 #8）

    async def run_reminder_scan(self) -> int:
        """next_fire_at 到点 → 锚点系统消息（durable，视同 @mention）+ 推进调度。返回触发数。

        锚点消息经 bus MESSAGE_CREATED 驱动投递引擎：daemon 在线即 wake+deliver，离线则消息照发、
        调度照推进，补唤醒由重连对账 #3 覆盖（离线安全）。
        """
        now = now_iso()
        fired = 0
        with gateway_tx(self._engine, self._bus) as tx:
            due = tx.conn.execute(
                select(_REMINDER).where(
                    _REMINDER.c.status == "active", _REMINDER.c.next_fire_at <= now
                )
            ).mappings().all()
            for r in due:
                reminder = dict(r)
                msg_id = new_ulid()
                ts = now_iso()
                tx.conn.execute(
                    insert(_MSG).values(
                        id=msg_id,
                        workspace_id=reminder["workspace_id"],
                        channel_id=reminder["anchor_channel_id"],
                        thread_root_id=None,
                        author_member_id=None,
                        kind=MessageKind.SYSTEM,
                        card_kind=None,
                        card_ref=None,
                        body=f"提醒触发（reminder {reminder['id']}）。",
                        created_at=ts,
                    )
                )
                # 锚点系统消息对创建者 Agent 视同 @mention（§8.2）。
                tx.conn.execute(
                    insert(_MENTION).values(
                        message_id=msg_id, member_id=reminder["agent_member_id"]
                    )
                )
                tx.conn.execute(
                    update(_REMINDER)
                    .where(_REMINDER.c.id == reminder["id"])
                    .values(status="done")  # M1：once 一次性；recurring（需 LoopContract）留 M3
                )
                msg_row = dict(
                    tx.conn.execute(select(_MSG).where(_MSG.c.id == msg_id)).mappings().first()
                )
                rem_row = dict(
                    tx.conn.execute(
                        select(_REMINDER).where(_REMINDER.c.id == reminder["id"])
                    ).mappings().first()
                )
                tx.emit(
                    EventType.MESSAGE_CREATED,
                    reminder["anchor_channel_id"],
                    {"message": message_public(msg_row)},
                )
                tx.emit(
                    EventType.REMINDER_UPDATED,
                    reminder["anchor_channel_id"],
                    {"reminder": reminder_public(rem_row)},
                )
                fired += 1
        return fired

    # ---------------------------------------------------------------- 周期后台 loop

    async def _reconcile_loop(self) -> None:
        while True:
            await asyncio.sleep(self.reconcile_interval)
            for conn in list(self._conns.values()):
                self._spawn_on_conn(conn, self.reconcile(conn))

    async def _reminder_loop(self) -> None:
        while True:
            await asyncio.sleep(self.reminder_interval)
            with contextlib.suppress(Exception):
                await self.run_reminder_scan()

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(min(self.heartbeat_timeout, 30.0))
            now = time.monotonic()
            for conn in list(self._conns.values()):
                if now - conn.last_ping_monotonic > self.heartbeat_timeout:
                    with contextlib.suppress(Exception):
                        await conn.sock.close(code=1001)  # 60s 未收 ping 断连（契约 D §2）

    def _spawn_on_conn(self, conn: DaemonConnection, coro: Any) -> None:
        task = self._loop.create_task(coro)  # type: ignore[union-attr]
        conn.tasks.add(task)
        task.add_done_callback(conn.tasks.discard)
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

    # ---------------------------------------------------------------- 断连级联（契约 D §2）

    async def _teardown(self, conn: DaemonConnection) -> None:
        # 仅当本连接仍是该 computer 的注册连接才级联（被顶掉的旧连接不触发离线，避免误报）。
        if self._conns.get(conn.computer_id) is conn:
            self._conns.pop(conn.computer_id, None)
            with gateway_tx(self._engine, self._bus) as tx:
                tx.conn.execute(
                    update(_COMPUTER)
                    .where(_COMPUTER.c.id == conn.computer_id)
                    .values(status=ComputerStatus.OFFLINE)
                )
                row = dict(
                    tx.conn.execute(
                        select(_COMPUTER).where(_COMPUTER.c.id == conn.computer_id)
                    ).mappings().first()
                )
                tx.emit(EventType.COMPUTER_DISCONNECTED, None, {"computer": computer_public(row)})
                # 级联每 Agent presence.changed(offline)——不改写 agents.status（保留最后已知态）。
                agents = self._agents_on_computer(tx.conn, conn.computer_id)
                for a in agents:
                    tx.emit(
                        EventType.PRESENCE_CHANGED,
                        None,
                        {
                            "member_id": a["member_id"],
                            "kind": MemberKind.AGENT.value,
                            "status": AgentStatus.OFFLINE.value,
                        },
                    )
        for task in list(conn.tasks):
            task.cancel()
        for fut in list(conn.pending_acks.values()) + list(conn.pending_replies.values()):
            if not fut.done():
                fut.set_exception(DaemonOffline("连接断开"))
