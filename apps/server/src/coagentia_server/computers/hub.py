"""daemon 网关（契约 D §2–§8 的 server 端）：接入认证、握手、帧收发底座、对账器、
投递引擎、上报处理、断连级联、reminder 调度。

架构（与 WsHub 同构，坑 3）：
- Hub 在 lifespan 起：订阅 bus（消费 message.created 驱动投递）+ 周期对账/reminder/沉默 loop；
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
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from coagentia_contracts import entities
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
    CheckFinishedData,
    DaemonAgentActivityData,
    DaemonHelloAckData,
    DaemonHelloData,
    DiagnosticsBatchData,
    FrameKind,
    GitDiffQuery,
    HomeFileQuery,
    HomeTreeQuery,
    InjectSource,
    InstrFrame,
    InstrType,
    MessageDeliverData,
    MessageInjectData,
    QueryFrame,
    QueryType,
    ReportType,
    RuntimesDetectedData,
    UsageBatchData,
    WakeRefs,
    WorktreeCleanupData,
    WorktreeEnsureData,
    WorktreeStatusData,
)
from coagentia_contracts.enums import (
    ActivityKind,
    AgentStatus,
    ChannelKind,
    ComputerStatus,
    ContractKind,
    HeldDraftStatus,
    InjectKind,
    LifecycleAction,
    MemberKind,
    MessageKind,
    ReminderKind,
    ReminderStatus,
    TaskEventKind,
    TaskStatus,
    WakeReason,
)
from coagentia_contracts.ws import EventType
from sqlalchemy import case, func, insert, select, update
from sqlalchemy.engine import Connection, Engine
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from coagentia_server.activity import service as activity_service
from coagentia_server.canvas import service as canvas_service
from coagentia_server.computers.gateway_tx import gateway_tx
from coagentia_server.db import models
from coagentia_server.events import EventBus
from coagentia_server.guard import service as guard_service
from coagentia_server.ledger.service import format_iso, new_ulid, now_iso
from coagentia_server.orchestration import landing as landing_domain
from coagentia_server.orchestration import proposal as proposal_domain
from coagentia_server.reminders import cadence as reminder_cadence
from coagentia_server.routes.serialize import (
    computer_public,
    diagnostic_public,
    held_draft_public,
    message_public,
    reminder_public,
    worktree_public,
)
from coagentia_server.system_nodes import service as system_node_service
from coagentia_server.tasks import silence as silence_logic
from coagentia_server.worktrees import service as worktree_service

_COMPUTER = models.tbl(models.Computer)
_AGENT = models.tbl(models.Agent)
_MEMBER = models.tbl(models.Member)
_SKILL = models.tbl(models.AgentSkill)
_CHANNEL = models.tbl(models.Channel)
_CHANNEL_MEMBER = models.tbl(models.ChannelMember)
_MSG = models.tbl(models.Message)
_MENTION = models.tbl(models.MessageMention)
_READ = models.tbl(models.ReadPosition)
_DIAG = models.tbl(models.DiagnosticEvent)
_USAGE = models.tbl(models.TokenUsageEvent)
_TASK = models.tbl(models.Task)
_TASK_EVENT = models.tbl(models.TaskEvent)
_CANVAS_NODE = models.tbl(models.CanvasNode)
_REMINDER = models.tbl(models.Reminder)
_HELD = models.tbl(models.HeldDraft)
_WORKTREE = models.tbl(models.Worktree)

# 最后已知态里"应存活"的期望集合（对账 #2 自动 resume 的触发条件，契约 D §4.4）。
_RESUMABLE = {AgentStatus.STARTING.value, AgentStatus.IDLE.value, AgentStatus.BUSY.value}
_DELIVERABLE = {AgentStatus.IDLE.value, AgentStatus.BUSY.value}

_ACTIVITY_THROTTLE_SEC = 0.5  # 契约 D §7：server ≥500ms 节流转发 agent.activity
_LAST_SEEN_THROTTLE_SEC = 60  # 契约 D §2：last_seen_at 写库节流
_WORKTREE_ENSURE_ESCALATE_AFTER = 3  # #2：worktree ensure 连续失败达此次数 → 升级喊人（一次性）


class DaemonOffline(Exception):
    """无活跃 daemon 连接 / 指令 ack 或 query reply 超时（→ REST 503 DAEMON_OFFLINE）。"""


class GitQueryError(Exception):
    """daemon 在线但 git.diff 查询本身失败（坏 base ref / worktree 安全拒绝 → REST 422
    VALIDATION_FAILED）。**不继承 DaemonOffline**——二者并列独立，否则 routes 的 except
    DaemonOffline 会抢先吞掉本类致 422 永不触发（#5）。"""


class HeldDraftResolved(Exception):
    """held 三键委托 hub 时行已被并发终解（TOCTOU）→ REST 409 HELD_DRAFT_RESOLVED（评审 #5）。"""


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
        silence_interval: float = 60.0,
        held_interval: float = 5.0,
        heartbeat_timeout: float = 60.0,
        landing_interval: float = 15.0,
    ) -> None:
        self._engine = engine
        self._bus = bus
        self._server_version = server_version
        self.ack_timeout = ack_timeout
        self.query_timeout = query_timeout
        self.reconcile_interval = reconcile_interval
        self.reminder_interval = reminder_interval
        self.silence_interval = silence_interval
        self.held_interval = held_interval
        self.heartbeat_timeout = heartbeat_timeout
        self.landing_interval = landing_interval
        self._conns: dict[str, DaemonConnection] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sub_token: int | None = None
        self._bg: list[asyncio.Task] = []
        self._activity_last: dict[str, float] = {}
        self._worktree_locks: dict[str, asyncio.Lock] = {}
        self._system_node_locks: dict[str, asyncio.Lock] = {}
        self._system_pending: dict[str, tuple[tuple[str, str], str]] = {}
        self._landing_lock = asyncio.Lock()  # J9 落地扫描进程内防重入（跨进程由账本三态兜）

    # ---------------------------------------------------------------- lifespan 装配

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._sub_token = self._bus.subscribe(self._on_bus_event)
        self._bg = [
            loop.create_task(self._reconcile_loop()),
            loop.create_task(self._reminder_loop()),
            loop.create_task(self._silence_loop()),
            loop.create_task(self._held_loop()),
            loop.create_task(self._heartbeat_loop()),
            loop.create_task(self._landing_loop()),
        ]
        # 对账 #4 启动扫描（J9）：崩溃遗留的 running decomp 批次 / 直落 landing 提案即刻续跑
        # （不等首个周期；幂等——前段 hit 跳过尾段补齐，§9.2）。
        self._bg.append(loop.create_task(self._run_landing_scan()))

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
        """提交后事件 → 消息投递或低延迟 worktree 激活扫描。"""
        loop = self._loop
        if loop is None:
            return
        if event.type == EventType.MESSAGE_CREATED:
            message = event.data.get("message") or {}
            mid = message.get("id")
            cid = message.get("channel_id")
            if mid and cid:
                loop.call_soon_threadsafe(self._spawn, self._deliver_message(mid, cid))
            return
        if event.type in {EventType.TASK_CREATED, EventType.TASK_UPDATED}:
            task = event.data.get("task") or {}
            change = event.data.get("change") or {}
            # 快速/as_task 的普通任务占绝大多数；只在自身写代码或 done 可能解锁下游时扫。
            needs_scan = bool(task.get("writes_code")) or change.get("to_status") == "done"
            channel_id = event.channel_id
            if channel_id and needs_scan:
                loop.call_soon_threadsafe(
                    self._spawn, self._scan_channel_worktrees(channel_id)
                )
                loop.call_soon_threadsafe(
                    self._spawn, self._scan_channel_system_nodes(channel_id)
                )
            if event.type == EventType.TASK_UPDATED and task.get("writes_code"):
                if task.get("id") and change.get("kind") in {
                    TaskEventKind.CLAIM.value,
                    TaskEventKind.ASSIGN.value,
                }:
                    loop.call_soon_threadsafe(
                        self._spawn, self._notify_active_task_owner(task["id"])
                    )
            return
        if event.type in {
            EventType.CANVAS_NODE_ADDED,
            EventType.CANVAS_NODE_UPDATED,
            EventType.CANVAS_EDGE_ADDED,
            EventType.CANVAS_EDGE_REMOVED,
        }:
            channel_id = event.channel_id
            if channel_id:
                loop.call_soon_threadsafe(
                    self._spawn, self._scan_channel_worktrees(channel_id)
                )
                loop.call_soon_threadsafe(
                    self._spawn, self._scan_channel_system_nodes(channel_id)
                )
            return
        # J9 落地执行器低延迟触发：confirm 建批（landing.started）与直落转态（proposal.updated
        # status=landing）即刻领批执行；周期 _landing_loop 兜崩溃恢复（对账 #4）。
        if event.type == EventType.LANDING_STARTED:
            loop.call_soon_threadsafe(self._spawn, self._run_landing_scan())
            return
        if event.type == EventType.PROPOSAL_UPDATED:
            proposal = event.data.get("proposal") or {}
            if proposal.get("status") == "landing":
                loop.call_soon_threadsafe(self._spawn, self._run_landing_scan())

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
            # 握手对账复验既有 active worktree 行（#3）；周期 _reconcile_loop 不复验（避免噪声）。
            self._spawn_on_conn(conn, self.reconcile(conn, revalidate_worktrees=True))
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
        # 新连接可能来自 daemon 进程重启：解除该机器已 ack 的运行记忆，由 running 事实重派同自然键。
        self._system_pending = {
            node_id: pending
            for node_id, pending in self._system_pending.items()
            if pending[1] != conn.computer_id
        }
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
            row = models.row_dict(
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
        elif rtype == ReportType.WORKTREE_STATUS:
            self._report_worktree_status(conn, WorktreeStatusData.model_validate(data))
        elif rtype == ReportType.CHECK_FINISHED:
            self._report_check_finished(conn, CheckFinishedData.model_validate(data))
            await self._ack(conn, frame_id, AckResult.DONE)
        # hello（重复）/ M7 上报：忽略

    def _report_worktree_status(
        self, conn: DaemonConnection, data: WorktreeStatusData
    ) -> None:
        """worktree.status：按 task_id 持久状态、广播；首次 active 落 durable 目录消息。"""
        if data.status == "merged" and not data.merge_commit:
            with gateway_tx(self._engine, self._bus) as tx:
                merge_node_ids = system_node_service.pending_merge_node_ids(
                    tx.conn,
                    data.task_id,
                    computer_id=conn.computer_id,
                    branch=data.branch,
                    path=data.path,
                )
                for node_id in merge_node_ids:
                    system_node_service.fail_dispatch(
                        tx,
                        node_id=node_id,
                        action=InstrType.WORKTREE_MERGE.value,
                        task_id=data.task_id,
                        reason="daemon merged 上报缺 merge_commit",
                    )
            for node_id in merge_node_ids:
                self._system_pending.pop(node_id, None)
            # #10 fail-closed：缺 merge_commit 不能标 MERGED（否则 apply_status 误置假终态），
            # 故跳过 apply_status。merge_node_ids 非空 → fail_dispatch 走人工 retry；空集=迟到/
            # 重复/跨机越界报（live merge 节点已 SUCCESS/FAILED，worktree 行终态经有效 merged 报或
            # 任务 DONE/CLOSED→cleanup_plans 独立可达），静默丢弃不 wedge。
            return
        continue_channels: set[str] = set()
        with gateway_tx(self._engine, self._bus) as tx:
            merge_node_ids = (
                system_node_service.pending_merge_node_ids(
                    tx.conn,
                    data.task_id,
                    computer_id=conn.computer_id,
                    branch=data.branch,
                    path=data.path,
                )
                if data.status in {"merged", "conflicted"}
                else []
            )
            result = worktree_service.apply_status(
                tx.conn,
                computer_id=conn.computer_id,
                data=data,
                trusted_running_merge=bool(merge_node_ids),
            )
            if result is None:
                return  # 非本机 Project/非画布任务的越界上报不污染事实源
            for updated_row in (result.row, *result.alias_rows):
                tx.emit(
                    EventType.WORKTREE_UPDATED,
                    result.channel_id,
                    {"worktree": worktree_public(updated_row)},
                )
            if merge_node_ids:
                continue_channels = system_node_service.apply_merge_result(
                    tx,
                    node_ids=merge_node_ids,
                    data=data,
                    worktree_row=result.row,
                )
            if not result.became_active or result.task_status in {
                TaskStatus.DONE.value,
                TaskStatus.CLOSED.value,
            }:
                pass
            else:
                mention_ids: tuple[str, ...] = ()
                if result.owner_member_id is not None:
                    is_agent = tx.conn.execute(
                        select(_AGENT.c.member_id).where(
                            _AGENT.c.member_id == result.owner_member_id
                        )
                    ).first()
                    if is_agent is not None:
                        mention_ids = (result.owner_member_id,)
                self._post_system_message(
                    tx,
                    workspace_id=result.workspace_id,
                    channel_id=result.channel_id,
                    thread_root_id=result.root_message_id,
                    mention_member_ids=mention_ids,
                    body=worktree_service.directory_message(result.row["path"]),
                )
        for channel_id in continue_channels:
            self._spawn(self._scan_channel_system_nodes(channel_id))
        for node_id in merge_node_ids:
            self._system_pending.pop(node_id, None)

    def _report_check_finished(
        self, conn: DaemonConnection, data: CheckFinishedData
    ) -> None:
        continue_channel: str | None
        with gateway_tx(self._engine, self._bus) as tx:
            handled, continue_channel = system_node_service.complete_check(
                tx, computer_id=conn.computer_id, data=data
            )
        if handled:
            self._system_pending.pop(data.node_id, None)
        if continue_channel is not None:
            self._spawn(self._scan_channel_system_nodes(continue_channel))

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
            row = models.row_dict(
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
        # 画布锚点/briefing 本身就是开工信号：在线时先 ensure 并消费 active 绝对路径，再构造
        # wake/deliver，禁止 Agent 先醒却拿不到工作目录。
        await self._scan_channel_worktrees(channel_id)
        with self._engine.connect() as c:
            msg = c.execute(select(_MSG).where(_MSG.c.id == message_id)).mappings().first()
            channel = (
                c.execute(select(_CHANNEL).where(_CHANNEL.c.id == channel_id)).mappings().first()
            )
            if msg is None or channel is None:
                return
            msg = dict(msg)
            channel = dict(channel)
            # 投递 gating（裁决 2）：本消息属 blocked 任务线程 → 压制唤醒与投递（含 busy 直投），
            # 消息留积压，解除阻塞后随后续触发/对账投递。gating 与 msg 绑定（与收件 Agent 无关），
            # 故一算即对全体收件人生效，直接短路。
            if canvas_service.message_delivery_gated(c, msg):
                return
            recipients = self._channel_agent_recipients(c, channel_id)
        for agent_id, computer_id in recipients:
            conn = self._conns.get(computer_id)
            if conn is None:
                continue  # daemon 离线 → 积压，重连对账 #3 补
            status = conn.present.get(agent_id)
            if status not in _DELIVERABLE:
                continue  # offline/starting/error → 不投不唤醒（§8.2）
            with self._engine.connect() as c:
                if worktree_service.delivery_waits_for_directory(
                    c, agent_member_id=agent_id, message=msg
                ):
                    continue
                prefix, _ = self._filter_agent_delivery(
                    c, self._backlog(c, agent_id, channel_id), agent_id
                )
                if message_id not in {item["id"] for item in prefix}:
                    continue
                reason = self._compute_trigger(c, msg, channel, agent_id)
            if status == AgentStatus.BUSY.value:
                await self._deliver_backlog(conn, agent_id, channel_id)  # 直投（§8.2）
            elif reason is not None:  # idle + 触发命中 → wake + deliver
                with self._engine.connect() as c:
                    refs = self._wake_refs(
                        c, reason=reason, agent_id=agent_id, messages=[msg]
                    )
                await self.send_instr(
                    conn,
                    agent_id,
                    InstrType.AGENT_WAKE,
                    AgentWakeData(
                        agent_member_id=agent_id,
                        reason=reason,
                        refs=refs,
                    ),
                )
                await self._deliver_backlog(conn, agent_id, channel_id)
            # idle + 未命中 → 静默积压（不发帧，随下次唤醒随批投递）

    def _filter_agent_delivery(
        self, c: Connection, raw: list[dict[str, Any]], agent_id: str
    ) -> tuple[list[dict[str, Any]], str | None]:
        """gating（blocked 线程）叠加 worktree path fail-closed；只投**连续前缀**，被扣消息与其后
        消息都不得推进水位。必须只投连续前缀：daemon 按频道最大 message_id 去重，若越过 held 先投
        later，解锁后的早消息会被 noop 永久漏投——故遇首个 gated/held 即停（#7 权衡：延迟不丢）。"""
        deliver: list[dict[str, Any]] = []
        watermark: str | None = None
        hit_held = False
        for message in raw:
            if hit_held:
                continue
            held = canvas_service.message_delivery_gated(
                c, message
            ) or worktree_service.delivery_waits_for_directory(
                c, agent_member_id=agent_id, message=message
            )
            if held:
                hit_held = True
                continue
            deliver.append(message)
            watermark = message["id"]
        return deliver, watermark

    async def _deliver_backlog(
        self, conn: DaemonConnection, agent_id: str, channel_id: str
    ) -> None:
        """投递积压批 = read_position 之后全部**非 gated** 应投消息，一次喂齐（§8.2 + 裁决 2）。

        积压在 send_instr 取得 agent_lock **之后**重算（prepare）：busy 期每条新消息各起一个并发
        投递任务，若在锁外先算批、锁内后发，相邻批会重叠重投较低 message_id（#5）。锁内重算使
        后到任务反映此前 ack 已推进的 read_position——已投完则返回 None 不发帧。
        """
        def _prepare() -> tuple[MessageDeliverData, DeliverMeta | None] | None:
            with self._engine.connect() as c:
                deliver, watermark = self._filter_agent_delivery(
                    c, self._backlog(c, agent_id, channel_id), agent_id
                )
                deliver = worktree_service.inject_directory_context(
                    c, agent_member_id=agent_id, messages=deliver
                )
            if not deliver:
                return None
            # deliver 帧是未附着面（契约 A v1.0.4：files=None），直接 model 化免 dict 中转
            messages = [entities.MessagePublic.model_validate(m) for m in deliver]
            # 水位 None（首条即 gated）→ 不带 meta，不推进 read_position（消息留积压重评）。
            meta = (
                DeliverMeta(
                    agent_member_id=agent_id,
                    channel_id=channel_id,
                    workspace_id=conn.workspace_id,
                    last_message_id=watermark,
                )
                if watermark is not None
                else None
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
        if worktree_service.activation_context(
            c, agent_member_id=agent_id, message=msg
        ) is not None:
            return WakeReason.CANVAS_ACTIVATION
        if msg.get("kind") == MessageKind.SYSTEM.value and mentioned:
            return WakeReason.REMINDER  # reminder 锚点系统消息视同 @mention（§4.4 #8）
        if channel.get("kind") == ChannelKind.DM.value:
            return WakeReason.CHANNEL_MESSAGE  # DM 必达恒触发（FR-4.7）
        if mentioned:
            return WakeReason.MENTION
        return None

    def _wake_refs(
        self,
        c: Connection,
        *,
        reason: WakeReason,
        agent_id: str,
        messages: list[dict[str, Any]],
    ) -> WakeRefs:
        node_id: str | None = None
        if reason == WakeReason.CANVAS_ACTIVATION:
            for message in messages:
                context = worktree_service.activation_context(
                    c, agent_member_id=agent_id, message=message
                )
                if context is not None:
                    node_id = context.node_id
                    break
        return WakeRefs(message_ids=[message["id"] for message in messages], node_id=node_id)

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

    # ---------------------------------------------------------------- worktree 生命周期（M6a J3）

    def _worktree_lock(self, task_id: str) -> asyncio.Lock:
        lock = self._worktree_locks.get(task_id)
        if lock is None:
            lock = asyncio.Lock()
            self._worktree_locks[task_id] = lock
        return lock

    async def _scan_channel_worktrees(self, channel_id: str) -> None:
        """画布/任务提交后低延迟扫描；上游 done 解锁无需等 60s 周期。"""
        with self._engine.connect() as c:
            plans = worktree_service.ensure_plans(c, channel_id=channel_id)
        for plan in plans:
            await self._ensure_worktree(plan.task_id)

    async def _ensure_worktree(
        self, task_id: str, *, allow_blocked: bool = False, revalidate: bool = False
    ) -> bool:
        """task 自然键串行重查后 ensure；返回绝对 path 是否已持久。

        revalidate=True（仅 reconnect 握手）：对既有 active 行也重下发 ensure —— daemon 幂等，树在
        则 noop 上报 active，树没了（重启/prune/丢失）则 prune 重建（#3）。仅 active，不含
        conflicted：re-ensure 会让 daemon 报 active，apply_status 会把冲突态覆盖回 active。"""
        async with self._worktree_lock(task_id):
            with self._engine.connect() as c:
                existing = c.execute(
                    select(_WORKTREE.c.path, _WORKTREE.c.status).where(
                        _WORKTREE.c.task_id == task_id
                    )
                ).first()
                if existing is not None:
                    already = bool(existing[0]) and existing[1] in {"active", "conflicted"}
                    if not revalidate or existing[1] != "active":
                        return already
                    # 复验：既有 active 行 → 用 revalidation_plans 重下发（ensure_plans 会因 task_id
                    # 非空排除该行，故须专用 plan 构造器）。
                    plans = worktree_service.revalidation_plans(c, task_id=task_id)
                else:
                    plans = worktree_service.ensure_plans(
                        c, task_id=task_id, allow_blocked=allow_blocked
                    )
            if not plans:
                return False
            plan = plans[0]
            conn = self._conns.get(plan.computer_id)
            if conn is None:
                return False
            ack = await self.send_instr(
                conn,
                f"worktree:{task_id}",
                InstrType.WORKTREE_ENSURE,
                WorktreeEnsureData(
                    task_id=plan.task_id,
                    project_id=plan.project_id,
                    repo_path=plan.repo_path,
                    branch=plan.branch,
                ),
            )
            if ack is None:
                return False
            if ack.result == AckResult.FAILED:
                self._record_worktree_failure(conn, task_id, InstrType.WORKTREE_ENSURE, ack)
                return False
            # daemon 约定 status→ack；仍以 DB 事实复核，不以 ack 自称完成替代绝对路径落库。
            with self._engine.connect() as c:
                row = c.execute(
                    select(_WORKTREE.c.path).where(_WORKTREE.c.task_id == task_id)
                ).first()
            return row is not None and bool(row[0])

    async def _cleanup_worktree(self, task_id: str, computer_id: str) -> bool:
        async with self._worktree_lock(task_id):
            with self._engine.connect() as c:
                due = any(
                    item.task_id == task_id
                    for item in worktree_service.cleanup_plans(c, computer_id=computer_id)
                )
            if not due:
                return False
            conn = self._conns.get(computer_id)
            if conn is None:
                return False
            ack = await self.send_instr(
                conn,
                f"worktree:{task_id}",
                InstrType.WORKTREE_CLEANUP,
                WorktreeCleanupData(task_id=task_id),
            )
            if ack is None:
                return False
            if ack.result == AckResult.FAILED:
                self._record_worktree_failure(conn, task_id, InstrType.WORKTREE_CLEANUP, ack)
                return False
            # daemon 重启后登记/目录都已不存在时 cleanup 合法 NOOP 且无法回报 status；server 以
            # 已有 worktrees(branch/path) 收敛 cleaned，避免 DB 永远卡在 active/merged。
            self._converge_worktree_cleaned(task_id, computer_id)
            return True

    def _record_worktree_failure(
        self,
        conn: DaemonConnection,
        task_id: str,
        instruction: InstrType,
        ack: AckFrame,
    ) -> None:
        """instr failed 写既有 agent.command 诊断（#2：归属 owner/channel + DIAGNOSTIC_APPENDED 让
        人类可见）；ensure 累计失败达阈值 → 升级喊人。不发明事件/错误码。"""
        error = ack.error.model_dump(mode="json") if ack.error is not None else None
        ts = now_iso()
        payload = {
            "instruction": instruction.value,
            "result": AckResult.FAILED.value,
            "error": error,
        }
        with gateway_tx(self._engine, self._bus) as tx:
            task = tx.conn.execute(
                select(
                    _TASK.c.owner_member_id,
                    _TASK.c.channel_id,
                    _TASK.c.workspace_id,
                    _TASK.c.title,
                ).where(_TASK.c.id == task_id)
            ).mappings().first()
            workspace_id = task["workspace_id"] if task else conn.workspace_id
            owner = task["owner_member_id"] if task else None
            channel_id = task["channel_id"] if task else None
            seq = tx.conn.execute(
                insert(_DIAG)
                .values(
                    workspace_id=workspace_id,
                    agent_member_id=owner,
                    type="agent.command",
                    channel_id=channel_id,
                    task_id=task_id,
                    batch_id=None,
                    payload=payload,
                    created_at=ts,
                )
                .returning(_DIAG.c.seq)
            ).scalar_one()
            if owner is not None:
                pub = diagnostic_public(
                    {
                        "seq": seq,
                        "workspace_id": workspace_id,
                        "agent_member_id": owner,
                        "type": "agent.command",
                        "channel_id": channel_id,
                        "task_id": task_id,
                        "batch_id": None,
                        "payload": payload,
                        "created_at": ts,
                    }
                )
                tx.emit(
                    EventType.DIAGNOSTIC_APPENDED,
                    None,
                    {"agent_member_id": owner, "events": [pub]},
                )
            # ensure 累计失败达阈值 → 一次性升级喊人（严格 ==，_worktree_lock 串行化计数逐一递增；
            # 成功不清零：第 3 次失败即升级、之后不再重复）。
            if instruction == InstrType.WORKTREE_ENSURE and owner is not None and task is not None:
                fail_count = tx.conn.execute(
                    select(func.count())
                    .select_from(_DIAG)
                    .where(
                        _DIAG.c.task_id == task_id,
                        _DIAG.c.type == "agent.command",
                        func.json_extract(_DIAG.c.payload, "$.instruction")
                        == InstrType.WORKTREE_ENSURE.value,
                        func.json_extract(_DIAG.c.payload, "$.result")
                        == AckResult.FAILED.value,
                    )
                ).scalar_one()
                if fail_count == _WORKTREE_ENSURE_ESCALATE_AFTER:
                    self._escalate_worktree_failure(tx, dict(task), task_id)

    def _escalate_worktree_failure(self, tx: Any, task: dict[str, Any], task_id: str) -> None:
        """worktree ensure 连续失败升级：频道主流系统消息 + activity(fail_closed) 给人类（#2）。

        沿用沉默/held 升级"喊人"范式：主流系统消息不落 mention 行，信号靠 activity 置顶。"""
        humans = self._channel_human_members(tx.conn, task["channel_id"])
        human_txt = " ".join(f"@{h['name']}" for h in humans)
        suffix = f"：{human_txt}" if human_txt else "。"
        body = (
            f"工作区创建失败：任务「{task['title']}」的 worktree 已累计 "
            f"{_WORKTREE_ENSURE_ESCALATE_AFTER} 次创建失败，Agent 无法开工，需人类介入{suffix}"
        )
        ts = now_iso()
        msg_id = self._post_system_message(
            tx,
            workspace_id=task["workspace_id"],
            channel_id=task["channel_id"],
            body=body,
            thread_root_id=None,
            created_at=ts,
        )
        for h in humans:
            activity_service.emit_activity(
                tx,
                workspace_id=task["workspace_id"],
                member_id=h["id"],
                kind=ActivityKind.FAIL_CLOSED.value,
                channel_id=task["channel_id"],
                message_id=msg_id,
                task_id=task_id,
                created_at=ts,
            )

    def _converge_worktree_cleaned(self, task_id: str, computer_id: str) -> None:
        with gateway_tx(self._engine, self._bus) as tx:
            prior = (
                tx.conn.execute(
                    select(_WORKTREE).where(_WORKTREE.c.task_id == task_id)
                )
                .mappings()
                .first()
            )
            if prior is None or prior["status"] == "cleaned":
                return
            result = worktree_service.apply_status(
                tx.conn,
                computer_id=computer_id,
                data=WorktreeStatusData(
                    task_id=task_id,
                    status="cleaned",
                    branch=prior["branch"],
                    path=prior["path"],
                ),
            )
            if result is not None:
                for updated_row in (result.row, *result.alias_rows):
                    tx.emit(
                        EventType.WORKTREE_UPDATED,
                        result.channel_id,
                        {"worktree": worktree_public(updated_row)},
                    )

    # ---------------------------------------------------------------- 系统节点执行（M6a J5）

    def _system_node_lock(self, node_id: str) -> asyncio.Lock:
        lock = self._system_node_locks.get(node_id)
        if lock is None:
            lock = asyncio.Lock()
            self._system_node_locks[node_id] = lock
        return lock

    async def _scan_channel_system_nodes(self, channel_id: str) -> None:
        with self._engine.connect() as c:
            node_ids = system_node_service.candidate_node_ids(c, channel_id=channel_id)
        for node_id in node_ids:
            await self._drive_system_node(node_id)

    async def _scan_workspace_system_nodes(self, workspace_id: str) -> None:
        with self._engine.connect() as c:
            node_ids = system_node_service.candidate_node_ids(c, workspace_id=workspace_id)
        for node_id in node_ids:
            await self._drive_system_node(node_id)

    async def _drive_system_node(self, node_id: str) -> None:
        """节点级串行重读：重复 bus/对账不会重复下发同一未决步骤。"""
        async with self._system_node_lock(node_id):
            with gateway_tx(self._engine, self._bus) as tx:
                dispatch = system_node_service.prepare_dispatch(tx, node_id)
            if dispatch is None:
                return
            conn = self._conns.get(dispatch.computer_id)
            if conn is None:
                return  # running + diagnostic 是事实源，重连对账复用同 run_id/步骤。
            if isinstance(dispatch, system_node_service.CheckDispatch):
                instruction = InstrType.CHECK_RUN
                task_id = None
                lock_key = f"project:{dispatch.data.project_id}"
                identity = (instruction.value, dispatch.data.run_id)
            else:
                instruction = InstrType.WORKTREE_MERGE
                task_id = dispatch.data.task_id
                lock_key = f"project:{dispatch.data.project_id}"
                identity = (instruction.value, dispatch.data.task_id)
            pending = self._system_pending.get(node_id)
            if pending is not None and pending[0] == identity:
                return
            self._system_pending[node_id] = (identity, dispatch.computer_id)
            try:
                ack = await self.send_instr(
                    conn,
                    lock_key,
                    instruction,
                    dispatch.data,
                )
            except DaemonOffline:
                self._system_pending.pop(node_id, None)
                return
            if ack is not None and ack.result == AckResult.FAILED:
                self._system_pending.pop(node_id, None)
                error = ack.error.model_dump(mode="json") if ack.error is not None else None
                with gateway_tx(self._engine, self._bus) as tx:
                    system_node_service.fail_dispatch(
                        tx,
                        node_id=node_id,
                        action=instruction.value,
                        task_id=task_id,
                        reason=f"daemon 指令失败：{error}",
                    )

    async def _notify_active_task_owner(self, task_id: str) -> None:
        """树先于 owner 就绪时，assign/claim 后补一条且只补一条 durable 目录消息。"""
        with gateway_tx(self._engine, self._bus) as tx:
            row = (
                tx.conn.execute(
                    select(
                        _TASK.c.workspace_id,
                        _TASK.c.channel_id,
                        _TASK.c.root_message_id,
                        _TASK.c.owner_member_id,
                        _WORKTREE.c.path,
                    )
                    .select_from(_TASK.join(_WORKTREE, _WORKTREE.c.task_id == _TASK.c.id))
                    .where(
                        _TASK.c.id == task_id,
                        _TASK.c.owner_member_id.is_not(None),
                        _WORKTREE.c.status.in_(("active", "conflicted")),
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                return
            owner = row["owner_member_id"]
            if tx.conn.execute(
                select(_AGENT.c.member_id).where(_AGENT.c.member_id == owner)
            ).first() is None:
                return
            body = worktree_service.directory_message(row["path"])
            already = tx.conn.execute(
                select(_MSG.c.id)
                .select_from(_MSG.join(_MENTION, _MENTION.c.message_id == _MSG.c.id))
                .where(
                    _MSG.c.thread_root_id == row["root_message_id"],
                    _MSG.c.kind == MessageKind.SYSTEM.value,
                    _MSG.c.body == body,
                    _MENTION.c.member_id == owner,
                )
            ).first()
            if already is not None:
                return
            self._post_system_message(
                tx,
                workspace_id=row["workspace_id"],
                channel_id=row["channel_id"],
                thread_root_id=row["root_message_id"],
                mention_member_ids=(owner,),
                body=body,
            )

    # ---------------------------------------------------------------- 对账器（契约 D §4.4）

    async def reconcile(
        self, conn: DaemonConnection, *, revalidate_worktrees: bool = False
    ) -> None:
        """重连握手后 + 周期兜底扫描共用（契约 D §4.4）。

        M1 规则：#1 presence 纠偏、#2 自动 resume、#3 投递补投、#8 reminder 补触发（#3 特例）。
        revalidate_worktrees=True 仅由 reconnect 握手传入：对既有 active worktree 行复验（#3 陈旧
        行复验，避免每次周期扫描重下发噪声）。
        """
        present = dict(conn.present)
        resume_boots: list[AgentBoot] = []
        ensure_task_ids: list[str] = []
        cleanup_task_ids: list[str] = []
        revalidate_task_ids: list[str] = []
        repair_injects: list[proposal_domain.PendingInject] = []
        deliver_plans: list[
            tuple[str, str, list[dict[str, Any]], str | None, WakeReason | None]
        ] = []
        with gateway_tx(self._engine, self._bus) as tx:
            ensure_task_ids = [
                plan.task_id
                for plan in worktree_service.ensure_plans(
                    tx.conn, computer_id=conn.computer_id
                )
            ]
            if revalidate_worktrees:
                revalidate_task_ids = [
                    plan.task_id
                    for plan in worktree_service.revalidation_plans(
                        tx.conn, computer_id=conn.computer_id
                    )
                ]
            cleanup_task_ids = [
                plan.task_id
                for plan in worktree_service.cleanup_plans(
                    tx.conn, computer_id=conn.computer_id
                )
            ]
            agents = self._agents_on_computer(tx.conn, conn.computer_id)
            status_by_id = {a["member_id"]: a["status"] for a in agents}
            # #6 修复循环续传（契约 D §4.4）：复用本 tx.conn 查本机 repairing 提案并从 body 重算错误
            # 清单（不另开连接，避 loop 上 SQLite 锁竞争拖慢 ack 触发重发——冲突测教训）。
            repair_injects = proposal_domain.repairing_reconcile_injects(
                tx.conn, agent_member_ids={a["member_id"] for a in agents}
            )
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
                    raw = self._backlog(tx.conn, aid, ch["id"])
                    if not raw:
                        continue
                    # 投递批剔除 blocked 任务线程消息（裁决 2），水位截到首个 gated 之前。
                    deliver, watermark = self._filter_agent_delivery(tx.conn, raw, aid)
                    if not deliver:
                        continue  # 全 gated → 无可投
                    if status == AgentStatus.BUSY.value:
                        deliver_plans.append((aid, ch["id"], deliver, watermark, None))
                    else:  # idle：积压中命中任一**非 gated** 触发 → wake+deliver；否则静默积压
                        reason = self._backlog_trigger(tx.conn, deliver, ch, aid)
                        if reason is not None:
                            deliver_plans.append((aid, ch["id"], deliver, watermark, reason))
        # 事务外下发指令（DB 已提交，避免持锁跨 await）。
        for boot in resume_boots:
            await self.send_instr(
                conn, boot.agent_member_id, InstrType.AGENT_START, AgentStartData(agent=boot)
            )
        # #5 必须先于 wake/deliver：active status 持久绝对 path 后，投递副本才能注入目录。
        for task_id in ensure_task_ids:
            await self._ensure_worktree(task_id)
        # #3 复验既有 active 行（仅 reconnect 握手）：daemon 幂等，树没了则 prune 重建，须在投递前。
        for task_id in revalidate_task_ids:
            await self._ensure_worktree(task_id, revalidate=True)
        for task_id in cleanup_task_ids:
            await self._cleanup_worktree(task_id, conn.computer_id)
        for aid, channel_id, deliver, watermark, reason in deliver_plans:
            with self._engine.connect() as c:
                channel_row = c.execute(
                    select(_CHANNEL).where(_CHANNEL.c.id == channel_id)
                ).mappings().one()
                if reason is not None:
                    reason = self._backlog_trigger(c, deliver, dict(channel_row), aid)
                    if reason is None:
                        continue
                refs = (
                    self._wake_refs(c, reason=reason, agent_id=aid, messages=deliver)
                    if reason is not None
                    else None
                )
                deliver = worktree_service.inject_directory_context(
                    c, agent_member_id=aid, messages=deliver
                )
            if reason is not None:
                await self.send_instr(
                    conn,
                    aid,
                    InstrType.AGENT_WAKE,
                    AgentWakeData(
                        agent_member_id=aid,
                        reason=reason,
                        refs=refs or WakeRefs(message_ids=[m["id"] for m in deliver]),
                    ),
                )
            # deliver 帧是未附着面（契约 A v1.0.4：files=None），直接 model 化免 dict 中转
            messages = [entities.MessagePublic.model_validate(m) for m in deliver]
            # 水位 None（首条即 gated）→ 不带 meta、不推进 read_position（消息留积压重评）。
            meta = (
                DeliverMeta(
                    agent_member_id=aid,
                    channel_id=channel_id,
                    workspace_id=conn.workspace_id,
                    last_message_id=watermark,
                )
                if watermark is not None
                else None
            )
            await self.send_instr(
                conn,
                aid,
                InstrType.MESSAGE_DELIVER,
                MessageDeliverData(agent_member_id=aid, channel_id=channel_id, messages=messages),
                deliver_meta=meta,
            )
        # 系统节点无独立 outbox：idle/running + agent.command 运行身份由同一对账恢复。
        await self._scan_workspace_system_nodes(conn.workspace_id)
        # #6 修复循环续传（契约 D §4.4）：repairing 提案完整错误清单 S1 直投重发（全量非增量）。
        for inj in repair_injects:
            data = MessageInjectData(
                agent_member_id=inj.agent_member_id,
                body=inj.body,
                source=InjectSource(kind=inj.kind, ref=inj.ref),
                diagnostic_type="agent.tool_call",
            )
            with contextlib.suppress(DaemonOffline):
                await self.send_instr(
                    conn, inj.agent_member_id, InstrType.MESSAGE_INJECT, data
                )

    def _backlog_trigger(
        self, c: Connection, backlog: list[dict[str, Any]], channel: dict[str, Any], agent_id: str
    ) -> WakeReason | None:
        for m in backlog:
            # 投递 gating（裁决 2）：积压里属 blocked 任务线程的消息不构成唤醒触发。
            if canvas_service.message_delivery_gated(c, m):
                break
            if worktree_service.delivery_waits_for_directory(
                c, agent_member_id=agent_id, message=m
            ):
                break
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

    def inject_contract_draft_request(
        self, agent_member_id: str, task_id: str, kind: ContractKind
    ) -> str:
        """S1 定向直投"起草契约"请求（P-3；契约 D §5.2 InjectKind.CONTRACT_DRAFT_REQUEST）。

        离线（无活跃 daemon 连接）→ DaemonOffline（REST 层收敛为 503 DAEMON_OFFLINE）；在线则同
        send_lifecycle 走 send_instr 同步等 ack，返回 ack.result（'done'/'noop'/'failed'）。
        """
        conn, _agent = self._require_conn_for_agent(agent_member_id)
        body = (
            "[system → 仅你可见] 契约起草请求：请为下列任务起草契约并通过 "
            "POST /tasks/{task_id}/contracts 提交。\n"
            f"task_id={task_id}\nkind={kind.value}"
        )
        data = MessageInjectData(
            agent_member_id=agent_member_id,
            body=body,
            source=InjectSource(kind=InjectKind.CONTRACT_DRAFT_REQUEST, ref=task_id),
            diagnostic_type="agent.tool_call",
        )
        ack = self._run_sync(
            self.send_instr(conn, agent_member_id, InstrType.MESSAGE_INJECT, data)
        )
        return ack.result.value

    def inject_orchestrator(
        self, agent_member_id: str, body: str, *, kind: InjectKind, ref: str | None = None
    ) -> str:
        """Orchestrator 定向直投（J8；契约 D §5.2）：上下文注入（kind=SYSTEM）与修复循环错误清单
        （kind=REPAIR）共用。离线（无活跃 daemon 连接）→ DaemonOffline（decompose 端点收敛 503；
        修复循环 best-effort 吞，靠对账 #6 续传）。inject 不动 read_positions（S1），与未提交 REST
        写事务无写锁死锁（同 inject_guard_feedback 裁决 9）。"""
        conn, _agent = self._require_conn_for_agent(agent_member_id)
        data = MessageInjectData(
            agent_member_id=agent_member_id,
            body=body,
            source=InjectSource(kind=kind, ref=ref),
            diagnostic_type="agent.tool_call",
        )
        ack = self._run_sync(
            self.send_instr(conn, agent_member_id, InstrType.MESSAGE_INJECT, data)
        )
        return ack.result.value

    def inject_guard_feedback(
        self, agent_member_id: str, body: str, *, ref: str | None = None
    ) -> str:
        """护栏反馈定向直投（G3/G4；契约 D §5.2 InjectKind.GUARD_FEEDBACK）。

        discard 告知「草稿已被丢弃」、reevaluate 告知「已触发重评估」共用。离线（无活跃 daemon
        连接）→ DaemonOffline（REST 层收敛 503）；在线则 send_instr 同步等 ack，返回 ack.result。
        inject 只发帧、不动 read_positions（S1 语义），故与未提交 REST 写事务无写锁死锁（裁决 9）。
        """
        conn, _agent = self._require_conn_for_agent(agent_member_id)
        data = MessageInjectData(
            agent_member_id=agent_member_id,
            body=body,
            source=InjectSource(kind=InjectKind.GUARD_FEEDBACK, ref=ref),
            diagnostic_type="agent.tool_call",
        )
        ack = self._run_sync(
            self.send_instr(conn, agent_member_id, InstrType.MESSAGE_INJECT, data)
        )
        return ack.result.value

    async def _held_reevaluation_combo(
        self, conn: DaemonConnection, agent_id: str, channel_id: str, held_id: str
    ) -> None:
        """重评估组合（裁决 10；F6 run_held_scan 复用）：wake(channel_message) + deliver 积压
        （经 _write_read_position 推进 read_position，防复扣死循环）+ inject guard_feedback。

        独立可 await 的 async 方法：由 reevaluate_held 经 _run_sync 调用（此时无未提交 REST 写锁），
        F6 后台扫描则直接 await。deliver 推进游标是关键——重评估后 Agent 的 read_position 前移，
        再发同一草稿时未读集收敛（不再无限被扣）。
        """
        await self.send_instr(
            conn,
            agent_id,
            InstrType.AGENT_WAKE,
            AgentWakeData(
                agent_member_id=agent_id,
                reason=WakeReason.CHANNEL_MESSAGE,
                refs=WakeRefs(message_ids=[]),
            ),
        )
        await self._deliver_backlog(conn, agent_id, channel_id)  # ack 后推进 read_position
        data = MessageInjectData(
            agent_member_id=agent_id,
            body=(
                "[system → 仅你可见] 你此前被扣的草稿已由人类触发重评估：请复核频道最新未读消息后"
                "再决定是否重发。"
            ),
            source=InjectSource(kind=InjectKind.GUARD_FEEDBACK, ref=held_id),
            diagnostic_type="agent.tool_call",
        )
        await self.send_instr(conn, agent_id, InstrType.MESSAGE_INJECT, data)

    def reevaluate_held(self, held_id: str, resolved_by: str) -> None:
        """reevaluate 同步桥（裁决 10）：供 REST 端点委托，避 REST 写锁与 deliver 写游标死锁。

        流程：_require_conn_for_agent（离线→DaemonOffline→路由 503）→ 自己的 gateway_tx 里置
        reevaluating + guard.reevaluate_requested 诊断 + emit held_draft.updated 并提交 → 提交后
        _run_sync(_held_reevaluation_combo)（wake+deliver 推进游标+inject，无未提交 REST 写锁）。
        reevaluate 是进行中态：不写 resolved_*、resolution 仍空。
        """
        with self._engine.connect() as c:
            held = c.execute(
                select(_HELD).where(_HELD.c.id == held_id)
            ).mappings().first()
        if held is None:
            raise DaemonOffline("被扣草稿不存在")  # 路由已校验存在；防御性收敛
        held = dict(held)
        agent_id = held["agent_member_id"]
        channel_id = held["channel_id"]
        # 离线先探（在改写状态前）→ DaemonOffline 冒泡到路由收 503，reevaluating 不落库。
        conn, _agent = self._require_conn_for_agent(agent_id)
        now = now_iso()
        with gateway_tx(self._engine, self._bus) as tx:
            # 终态守卫（评审 #5）：路由 _reject_terminal 与本 UPDATE 之间若并发 discard/release
            # 提交了终态，无条件写会复活已终解草稿（status=reevaluating 却 resolution=discarded）。
            # 故 UPDATE 限活动态；影响 0 行 = 已被并发终解 → 抛 HeldDraftResolved（路由收 409）。
            res = tx.conn.execute(
                update(_HELD)
                .where(
                    _HELD.c.id == held_id,
                    _HELD.c.status.in_(guard_service.ACTIVE_STATUSES),
                )
                .values(status=HeldDraftStatus.REEVALUATING.value)
            )
            if res.rowcount == 0:
                raise HeldDraftResolved(held_id)
            guard_service.write_guard_diagnostic(
                tx,
                guard_service.GUARD_REEVALUATE_REQUESTED,
                workspace_id=held["workspace_id"],
                agent_member_id=agent_id,
                channel_id=channel_id,
                payload={"held_draft_id": held_id, "resolved_by": resolved_by},
                created_at=now,
            )
            held_row = models.row_dict(
                tx.conn.execute(select(_HELD).where(_HELD.c.id == held_id)).mappings().first()
            )
            tx.emit(
                EventType.HELD_DRAFT_UPDATED, channel_id, {"draft": held_draft_public(held_row)}
            )
        # 提交后再做 daemon I/O（无未提交 REST 写锁 → deliver 写 read_position 安全）。
        self._run_sync(self._held_reevaluation_combo(conn, agent_id, channel_id, held_id))

    def force_start_wake(
        self,
        owner_member_id: str | None,
        channel_id: str,
        *,
        task_id: str,
        force_event_seq: int,
    ) -> None:
        """force-start override（裁决 3）：对任务 owner agent 绕过 blocked 门直投一次 wake+deliver。

        best-effort：owner 为人类/空 或 daemon 离线 → 仅留痕（route 已写 task_events + 系统消息），
        静默返回不报错。范式仿 send_lifecycle/inject_contract_draft_request 经 _run_sync 等 ack。
        """
        if owner_member_id is None:
            return  # 未认领 → 无投递面
        with self._engine.connect() as c:
            row = c.execute(
                select(_AGENT.c.computer_id).where(_AGENT.c.member_id == owner_member_id)
            ).first()
        if row is None:
            return  # owner 是人类成员（非 agent）→ 无 daemon 投递面
        conn = self._conns.get(row[0])
        if conn is None:
            return  # daemon 离线
        with self._engine.connect() as c:
            writes_code = bool(
                c.execute(select(_TASK.c.writes_code).where(_TASK.c.id == task_id)).scalar_one()
            )
        if writes_code:
            # route 当前仍持写事务；延后到精确 force_start seq 可见后再 ensure，避免 status 上报
            # 与未提交事务争 SQLite 写锁。ensure/status/path 完成后才允许 canvas_activation wake。
            loop = self._loop
            if loop is not None:
                loop.call_soon_threadsafe(
                    self._spawn,
                    self._force_start_after_commit(
                        conn,
                        owner_member_id,
                        channel_id,
                        task_id,
                        force_event_seq,
                    ),
                )
            return
        with contextlib.suppress(DaemonOffline):
            self._run_sync(
                self._force_start_deliver(
                    conn, owner_member_id, channel_id, task_id=task_id
                )
            )

    async def _force_start_after_commit(
        self,
        conn: DaemonConnection,
        agent_id: str,
        channel_id: str,
        task_id: str,
        force_event_seq: int,
    ) -> None:
        for _ in range(250):
            with self._engine.connect() as c:
                committed = c.execute(
                    select(_TASK_EVENT.c.seq).where(_TASK_EVENT.c.seq == force_event_seq)
                ).first()
            if committed is not None:
                break
            await asyncio.sleep(0.02)
        else:
            return
        try:
            if not await self._ensure_worktree(task_id, allow_blocked=True):
                return
            await self._force_start_deliver(
                conn, agent_id, channel_id, task_id=task_id
            )
        except DaemonOffline:
            return

    async def _force_start_deliver(
        self,
        conn: DaemonConnection,
        agent_id: str,
        channel_id: str,
        *,
        task_id: str,
    ) -> None:
        """force-start 直投（绕过 blocked 门，即「本次」放行）：wake + deliver 一次。

        **不带 deliver_meta**：本方法经 _run_sync 在 force-start 路由的写事务尚未提交时同步调用；
        若 deliver ack 回写 read_positions 会与该未提交写事务争 SQLite 写锁（busy_timeout 内阻塞
        事件循环 → 死锁）。故 override 投递不推进 read_position——本条消息仍留积压，解除阻塞后正常
        投递再推进（at-least-once + daemon 频道去重覆盖重叠）。积压读的是已提交态（含此前被 gating
        压制的消息），未含 route 尚未提交的锚点系统消息，符合 override 语义。
        """
        with self._engine.connect() as c:
            backlog = self._backlog(c, agent_id, channel_id)
            backlog = worktree_service.inject_directory_context(
                c, agent_member_id=agent_id, messages=backlog
            )
            node_id = c.execute(
                select(_CANVAS_NODE.c.id).where(_CANVAS_NODE.c.task_id == task_id)
            ).scalar_one_or_none()
        await self.send_instr(
            conn,
            agent_id,
            InstrType.AGENT_WAKE,
            AgentWakeData(
                agent_member_id=agent_id,
                reason=WakeReason.CANVAS_ACTIVATION,
                refs=WakeRefs(
                    message_ids=[m["id"] for m in backlog], node_id=node_id
                ),
            ),
        )
        if backlog:
            messages = [entities.MessagePublic.model_validate(m) for m in backlog]
            await self.send_instr(
                conn,
                agent_id,
                InstrType.MESSAGE_DELIVER,
                MessageDeliverData(
                    agent_member_id=agent_id, channel_id=channel_id, messages=messages
                ),
            )

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

    def query_git_diff(
        self, *, computer_id: str, query: GitDiffQuery
    ) -> dict[str, Any]:
        conn = self._conns.get(computer_id)
        if conn is None:
            raise DaemonOffline("daemon 离线")
        reply = self._run_sync(self.send_query(conn, QueryType.GIT_DIFF, query))
        error = reply.get("error")
        if isinstance(error, str):
            # daemon 回帧（在线）只是 git 查询失败（坏 base ref 等）→ 4xx，非 503（#5）。
            raise GitQueryError(error)
        return reply

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

    # ---------------------------------------------------------------- 系统消息发射（三处共用）

    def _post_system_message(
        self,
        tx: Any,
        *,
        workspace_id: str,
        channel_id: str,
        body: str,
        thread_root_id: str | None = None,
        mention_member_ids: Iterable[str] = (),
        created_at: str | None = None,
    ) -> str:
        """插一条 durable 系统消息（author=NULL, kind=SYSTEM）+ 可选 @mention 行 + emit。

        reminder 触发 / 沉默提醒 / 沉默升级三处共用，避免 insert(_MSG)+mention+回读+emit 骨架
        多份漂移（§8.2：系统消息 + mention 对目标 Agent 视同唤醒触发）。返回 msg_id。
        """
        msg_id = new_ulid()
        ts = created_at or now_iso()
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
                created_at=ts,
            )
        )
        for member_id in mention_member_ids:
            tx.conn.execute(
                insert(_MENTION).values(message_id=msg_id, member_id=member_id)
            )
        msg_row = models.row_dict(
            tx.conn.execute(select(_MSG).where(_MSG.c.id == msg_id)).mappings().first()
        )
        tx.emit(
            EventType.MESSAGE_CREATED, channel_id, {"message": message_public(msg_row)}
        )
        return msg_id

    # ---------------------------------------------------------------- reminder 调度（§4.4 #8）

    async def run_reminder_scan(self) -> int:
        """next_fire_at 到点 → 锚点系统消息（durable，视同 @mention）+ 推进调度。返回触发数。

        锚点消息经 bus MESSAGE_CREATED 驱动投递引擎：daemon 在线即 wake+deliver，离线则消息照发、
        调度照推进，补唤醒由重连对账 #3 覆盖（离线安全）。

        触发后调度（B §10.6 / §11.5）：`once` → status=done（一次性）；`recurring` → next_fire_at
        经 cadence 单点塌缩到 **严格晚于 now 的下一个命中点**（interval 保锚点相位 / cron 搜绝对
        壁钟，rearm_fire，O(命中距离)）**保持 active**——停机漏掉多个周期时一次追平，不逐格重触发
        洪泛（code-review 修）。每条触发只发一次锚点系统消息 + mention + REMINDER_UPDATED（载荷由
        内存行拼出，免回读）。
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
                if reminder["kind"] == ReminderKind.RECURRING.value:
                    # 按 cadence 类型塌缩重排（interval 保锚点相位 / cron 搜绝对壁钟）——走单点分派。
                    next_at = reminder_cadence.rearm_fire(
                        reminder["next_fire_at"], reminder["cadence"], now
                    )
                    tx.conn.execute(
                        update(_REMINDER)
                        .where(_REMINDER.c.id == reminder["id"])
                        .values(next_fire_at=next_at)
                    )
                    rem_after = {**reminder, "next_fire_at": next_at}
                else:
                    tx.conn.execute(
                        update(_REMINDER)
                        .where(_REMINDER.c.id == reminder["id"])
                        .values(status="done")  # once 一次性（触发即终态）
                    )
                    rem_after = {**reminder, "status": ReminderStatus.DONE.value}
                # 锚点系统消息对创建者 Agent 视同 @mention（§8.2）。
                self._post_system_message(
                    tx,
                    workspace_id=reminder["workspace_id"],
                    channel_id=reminder["anchor_channel_id"],
                    body=f"提醒触发（reminder {reminder['id']}）。",
                    mention_member_ids=[reminder["agent_member_id"]],
                )
                tx.emit(
                    EventType.REMINDER_UPDATED,
                    reminder["anchor_channel_id"],
                    {"reminder": reminder_public(rem_after)},
                )
                fired += 1
        return fired

    # ---------------------------------------------- G4 定时自动重评估（B §10 / D §9.2）

    async def run_held_scan(self) -> int:
        """G4 自动重评估：held 到点行置 reevaluating + guard.reevaluate_requested + emit（提交后
        对在线 Agent await 重评估组合）。返回本轮进入重评估的行数。

        扫描 status='held' AND next_reeval_at<=now AND escalated_at IS NULL——已升级（escalated_at
        非空）的行**停自动重评估**（裁决 6：交人类，不再自动喊）。与 reminder/silence 同节奏，在
        gateway_tx 里翻状态并提交，提交后对在线 Agent 复用 F5 的 `_held_reevaluation_combo`
        （wake + deliver 推进 read_position + inject）——deliver 推进游标是防复扣死循环关键（裁 4）。

        **在线先探再翻状态（评审 #6）**：Agent 离线的行**不翻 reevaluating**、留 held——否则翻了状态
        后扫描（只选 status='held'）再不选它、对账无 held 感知，行会永卡 reevaluating（Agent 从不被
        重评估提示、附件永久 GC 豁免）。留 held 则下轮 Agent 在线时正常重评估（对齐 reevaluate_held
        的「离线先探」范式）。
        """
        now = now_iso()
        combos: list[tuple[str, str, str]] = []  # (agent_id, channel_id, held_id)——提交后触发
        with gateway_tx(self._engine, self._bus) as tx:
            due = tx.conn.execute(
                select(_HELD).where(
                    _HELD.c.status == HeldDraftStatus.HELD.value,
                    _HELD.c.next_reeval_at <= now,
                    _HELD.c.escalated_at.is_(None),  # 升级后停自动（裁决 6）
                )
            ).mappings().all()
            for h in due:
                held = dict(h)
                try:  # 离线先探：不翻状态、留 held 下轮重试（评审 #6）
                    self._require_conn_for_agent(held["agent_member_id"])
                except DaemonOffline:
                    continue
                tx.conn.execute(
                    update(_HELD)
                    .where(_HELD.c.id == held["id"])
                    .values(status=HeldDraftStatus.REEVALUATING.value)
                )
                guard_service.write_guard_diagnostic(
                    tx,
                    guard_service.GUARD_REEVALUATE_REQUESTED,
                    workspace_id=held["workspace_id"],
                    agent_member_id=held["agent_member_id"],
                    channel_id=held["channel_id"],
                    payload={"held_draft_id": held["id"], "resolved_by": None},  # 自动触发无人类
                    created_at=now,
                )
                held_row = models.row_dict(
                    tx.conn.execute(
                        select(_HELD).where(_HELD.c.id == held["id"])
                    ).mappings().first()
                )
                tx.emit(
                    EventType.HELD_DRAFT_UPDATED,
                    held["channel_id"],
                    {"draft": held_draft_public(held_row)},
                )
                combos.append((held["agent_member_id"], held["channel_id"], held["id"]))
        # 提交后对（已确认在线的）Agent 触发组合（含 deliver 推进游标）。
        for agent_id, channel_id, held_id in combos:
            try:
                conn, _agent = self._require_conn_for_agent(agent_id)
            except DaemonOffline:
                continue  # 探后到组合前刚断连（罕见）→ 留 reevaluating，下次人工/对账处理
            with contextlib.suppress(Exception):  # 单行 daemon I/O 失败不拖垮整轮扫描
                await self._held_reevaluation_combo(conn, agent_id, channel_id, held_id)
        return len(combos)

    # ---------------------------------------------------------------- D5 沉默提醒（契约 B §10.5）

    async def run_silence_scan(self) -> int:
        """沉默任务扫描：超阈值先提醒后升级（B §10.5）。返回本轮动作数（提醒/升级各计 1）。

        判定纯逻辑在 tasks/silence.py（防自激 last_activity 计算 + 三态判定）；本方法只取数
        （把 DB 行喂成 SilenceInputs）+ 写副作用（锚点线程系统消息 / mention / task_events /
        emit_activity）。提醒锚点消息经 bus MESSAGE_CREATED 驱动投递引擎（@Agent 视同 mention
        触发唤醒），与 run_reminder_scan 同构。判定/升级历史全在 task_events 纯推导，无状态列。
        """
        now = now_iso()
        actions = 0
        with gateway_tx(self._engine, self._bus) as tx:
            tasks = [
                dict(r)
                for r in tx.conn.execute(
                    select(_TASK).where(_TASK.c.status.in_(silence_logic.SCAN_STATUSES))
                ).mappings()
            ]
            # 频道阈值行一次 IN 批取（去重 channel_id）避免逐任务 N+1 反复取同一行。
            channel_ids = {t["channel_id"] for t in tasks}
            channels = {
                c["id"]: dict(c)
                for c in tx.conn.execute(
                    select(_CHANNEL).where(_CHANNEL.c.id.in_(channel_ids))
                ).mappings()
            } if channel_ids else {}
            for task in tasks:
                channel = channels.get(task["channel_id"])
                if channel is None:
                    continue  # 频道缺失（防御）：无投递面
                threshold_h = silence_logic.threshold_hours(
                    task["status"],
                    silence_override_h=task["silence_override_h"],
                    remind_todo_h=channel["remind_todo_h"],
                    remind_inprog_h=channel["remind_inprog_h"],
                    remind_review_h=channel["remind_review_h"],
                )
                inp = self._silence_inputs(tx.conn, task, channel, threshold_h, now)
                action = silence_logic.decide(inp)
                if action is silence_logic.SilenceAction.REMIND:
                    self._emit_silence_reminder(tx, task, threshold_h)
                    actions += 1
                elif action is silence_logic.SilenceAction.ESCALATE:
                    self._emit_silence_escalation(tx, task)
                    actions += 1
        return actions

    def _silence_inputs(
        self,
        c: Connection,
        task: dict[str, Any],
        channel: dict[str, Any],
        threshold_h: int,
        now: str,
    ) -> silence_logic.SilenceInputs:
        """取数：last_activity 三来源 + 提醒/升级时刻（func.max，字典序=时序）。防自激两处排除。"""
        root = task["root_message_id"]
        # 锚点线程最新**非系统**消息（root 本身 + thread_root_id=root 的回复；kind != system）。
        last_thread_msg_at = c.execute(
            select(func.max(_MSG.c.created_at)).where(
                (_MSG.c.id == root) | (_MSG.c.thread_root_id == root),
                _MSG.c.kind != MessageKind.SYSTEM.value,
            )
        ).scalar()
        # task_events 三个时刻按 kind 分区一次条件聚合取（免同表同任务三趟 max 往返/三遍扫描）：
        #   last_event_at   = 排除 reminder_sent/escalated（防自激，B §10.5.2）
        #   last_reminder_at/last_escalated_at = 链条生效性判定（不进 last_activity）
        ev = c.execute(
            select(
                func.max(
                    case(
                        (
                            _TASK_EVENT.c.kind.notin_(
                                silence_logic.SELF_EXCITE_EVENT_KINDS
                            ),
                            _TASK_EVENT.c.created_at,
                        )
                    )
                ).label("last_event_at"),
                func.max(
                    case(
                        (
                            _TASK_EVENT.c.kind == TaskEventKind.REMINDER_SENT.value,
                            _TASK_EVENT.c.created_at,
                        )
                    )
                ).label("last_reminder_at"),
                func.max(
                    case(
                        (
                            _TASK_EVENT.c.kind == TaskEventKind.ESCALATED.value,
                            _TASK_EVENT.c.created_at,
                        )
                    )
                ).label("last_escalated_at"),
            ).where(_TASK_EVENT.c.task_id == task["id"])
        ).mappings().first()
        return silence_logic.SilenceInputs(
            now=now,
            threshold_h=threshold_h,
            remind_escalation=bool(channel["remind_escalation"]),
            status_changed_at=task["status_changed_at"],
            last_thread_msg_at=last_thread_msg_at,
            last_event_at=ev["last_event_at"] if ev else None,
            last_reminder_at=ev["last_reminder_at"] if ev else None,
            last_escalated_at=ev["last_escalated_at"] if ev else None,
        )

    def _reminder_targets(
        self, c: Connection, task: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """第一次提醒的 @ 目标（B §10.5.4）：Todo→创建者 / InProgress→owner / InReview→频道人类。

        返回成员行（id/name/kind）；owner 空（in_progress 未认领）→ 空列表（发消息不 @，链条照走）。
        """
        status = task["status"]
        if status == TaskStatus.TODO.value:
            member_ids = [task["created_by_member_id"]]
        elif status == TaskStatus.IN_PROGRESS.value:
            member_ids = [task["owner_member_id"]] if task["owner_member_id"] else []
        else:  # in_review → 频道全体人类成员
            return self._channel_human_members(c, task["channel_id"])
        rows = c.execute(
            select(_MEMBER.c.id, _MEMBER.c.name, _MEMBER.c.kind).where(
                _MEMBER.c.id.in_(member_ids), _MEMBER.c.removed_at.is_(None)
            )
        ).mappings()
        return [dict(r) for r in rows]

    def _channel_human_members(
        self, c: Connection, channel_id: str
    ) -> list[dict[str, Any]]:
        rows = c.execute(
            select(_MEMBER.c.id, _MEMBER.c.name, _MEMBER.c.kind)
            .select_from(
                _CHANNEL_MEMBER.join(_MEMBER, _CHANNEL_MEMBER.c.member_id == _MEMBER.c.id)
            )
            .where(
                _CHANNEL_MEMBER.c.channel_id == channel_id,
                _MEMBER.c.kind == MemberKind.HUMAN,
                _MEMBER.c.removed_at.is_(None),
            )
        ).mappings()
        return [dict(r) for r in rows]

    def _emit_silence_reminder(
        self, tx: Any, task: dict[str, Any], threshold_h: int
    ) -> None:
        """第一次提醒：锚点线程系统消息（@目标）+ message_mentions（@Agent 触发唤醒）+
        task_events(reminder_sent)。mention 行是唤醒事实源（_compute_trigger 视 system+mention
        为 REMINDER），故所有目标（含人类，用于渲染）统一插行。"""
        targets = self._reminder_targets(tx.conn, task)
        mention_txt = " ".join(f"@{t['name']}" for t in targets)
        suffix = f"：{mention_txt}" if mention_txt else "。"
        body = (
            f"沉默提醒：任务「{task['title']}」已超过 {threshold_h} 小时无进展，请跟进{suffix}"
        )
        ts = now_iso()
        # 锚点线程内（B §10.5.4）+ mention 行（@Agent 触发唤醒事实源，人类目标同插供渲染）。
        self._post_system_message(
            tx,
            workspace_id=task["workspace_id"],
            channel_id=task["channel_id"],
            body=body,
            thread_root_id=task["root_message_id"],
            mention_member_ids=[t["id"] for t in targets],
            created_at=ts,
        )
        tx.conn.execute(
            insert(_TASK_EVENT).values(
                task_id=task["id"],
                kind=TaskEventKind.REMINDER_SENT,
                from_status=None,
                to_status=None,
                owner_member_id=None,
                actor_member_id=None,
                created_at=ts,
            )
        )

    def _emit_silence_escalation(self, tx: Any, task: dict[str, Any]) -> None:
        """升级：频道主流系统消息 + activity(silence_escalation) 给人类 + task_events(escalated)。

        升级是人类面"喊人"：主流系统消息（不落 mention 行，信号靠 activity 置顶）；activity 经
        F2 服务层 emit_activity（conn 注入式，提交后广播）逐人类成员发射。
        """
        humans = self._channel_human_members(tx.conn, task["channel_id"])
        human_txt = " ".join(f"@{h['name']}" for h in humans)
        suffix = f"：{human_txt}" if human_txt else "。"
        body = (
            f"沉默升级：任务「{task['title']}」经提醒后仍无进展，需人类成员处理{suffix}"
        )
        ts = now_iso()
        # 频道主流（B §10.5.5）：升级是人类面"喊人"，不落 mention 行——信号靠 activity 置顶。
        msg_id = self._post_system_message(
            tx,
            workspace_id=task["workspace_id"],
            channel_id=task["channel_id"],
            body=body,
            thread_root_id=None,
            created_at=ts,
        )
        tx.conn.execute(
            insert(_TASK_EVENT).values(
                task_id=task["id"],
                kind=TaskEventKind.ESCALATED,
                from_status=None,
                to_status=None,
                owner_member_id=None,
                actor_member_id=None,
                created_at=ts,
            )
        )
        for h in humans:
            activity_service.emit_activity(
                tx,
                workspace_id=task["workspace_id"],
                member_id=h["id"],
                kind=ActivityKind.SILENCE_ESCALATION.value,
                channel_id=task["channel_id"],
                message_id=msg_id,
                task_id=task["id"],
                created_at=ts,
            )

    # ---------------------------------------------------------------- F5 AwaitingConfirm 24h 提醒

    async def run_awaiting_confirm_scan(self) -> int:
        """F5：awaiting_confirm 超 24h 无人确认 → source 线程系统消息 @提案请求者（拆解设计 §8.1）。

        与 D5 沉默提醒同一后台节奏（silence loop）。防重发纯推导（proposal.awaiting_reminder_sent
        诊断行，不给 proposals 加列）；判定/副作用在 proposal.awaiting_confirm_reminder_scan。提醒
        锚点系统消息经 bus MESSAGE_CREATED 驱动投递（@Agent 请求者视同 mention 唤醒），同 D5 同构。
        """
        cutoff = format_iso(
            datetime.now(UTC)
            - timedelta(hours=proposal_domain.AWAITING_CONFIRM_REMIND_HOURS)
        )
        with gateway_tx(self._engine, self._bus) as tx:
            return proposal_domain.awaiting_confirm_reminder_scan(tx, cutoff_iso=cutoff)

    # ---------------------------------------------------------------- J9 落地执行器宿主

    async def _run_landing_scan(self) -> None:
        """落地待办扫描（orchestration.landing.pending_landing_scan 的异步宿主）：进程内
        asyncio.Lock 串行（防重入双执行），DB 面由账本 record 三态兜（跨进程/竞态安全）。
        同步 SQLite 执行体在线程池跑，不阻塞事件 loop。"""
        async with self._landing_lock:
            await asyncio.to_thread(
                landing_domain.pending_landing_scan, self._engine, self._bus
            )

    async def _landing_loop(self) -> None:
        """对账 #4 周期兜底（契约 D §4.4 语义的落地面）：崩溃/错过事件触发的 running 批次与
        直落 landing 提案按 landing_interval 重入（幂等）。"""
        while True:
            await asyncio.sleep(self.landing_interval)
            with contextlib.suppress(Exception):
                await self._run_landing_scan()

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

    async def _silence_loop(self) -> None:
        while True:
            await asyncio.sleep(self.silence_interval)
            with contextlib.suppress(Exception):
                await self.run_silence_scan()
            with contextlib.suppress(Exception):
                await self.run_awaiting_confirm_scan()  # F5：与 D5 沉默提醒同节奏

    async def _held_loop(self) -> None:
        while True:
            await asyncio.sleep(self.held_interval)
            with contextlib.suppress(Exception):
                await self.run_held_scan()

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
                row = models.row_dict(
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
