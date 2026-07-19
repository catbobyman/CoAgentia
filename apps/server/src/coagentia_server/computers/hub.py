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
import logging
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from coagentia_contracts import entities
from coagentia_contracts.constants import BUFFER_DEPLOY_LOG_MAX_BYTES, DIAGNOSTIC_TYPES
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
    DeployFinishedData,
    DeployLogReportData,
    DeployRunData,
    DiagnosticsBatchData,
    FrameKind,
    FsTreeQuery,
    GitDiffQuery,
    HomeFileQuery,
    HomeTreeQuery,
    InjectSource,
    InstrFrame,
    InstrType,
    MessageDeliverData,
    MessageInjectData,
    PreviewStartData,
    PreviewStatusData,
    PreviewStopData,
    QueryFrame,
    QueryType,
    ReportType,
    RuntimesDetectedData,
    UsageBatchData,
    WakeRefs,
    WorktreeCleanupData,
    WorktreeEnsureData,
    WorktreeScanQuery,
    WorktreeStatusData,
)
from coagentia_contracts.enums import (
    ActivityKind,
    AgentStatus,
    CardKind,
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
    WorktreeStatus,
)
from coagentia_contracts.ws import EventType
from sqlalchemy import case, func, insert, select, update
from sqlalchemy.engine import Connection, Engine
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from coagentia_server.activity import service as activity_service
from coagentia_server.computers.gateway_tx import gateway_tx
from coagentia_server.db import models
from coagentia_server.events import EventBus
from coagentia_server.guard import service as guard_service
from coagentia_server.ledger.service import format_iso, new_ulid, now_iso
from coagentia_server.reminders import cadence as reminder_cadence
from coagentia_server.routes.serialize import (
    computer_public,
    deployment_public,
    diagnostic_public,
    held_draft_public,
    message_public,
    preview_session_public,
    reminder_public,
    worktree_public,
)
from coagentia_server.tasks import merge as merge_domain
from coagentia_server.tasks import silence as silence_logic
from coagentia_server.worktrees import service as worktree_service

_log = logging.getLogger(__name__)

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
_REMINDER = models.tbl(models.Reminder)
_HELD = models.tbl(models.HeldDraft)
_WORKTREE = models.tbl(models.Worktree)
_PROJECT = models.tbl(models.Project)
_PREVIEW = models.tbl(models.PreviewSession)
_CHANNEL_PROJECT = models.tbl(models.ChannelProject)
_DEPLOYMENT = models.tbl(models.Deployment)

# 「活跃预览」谓词集（starting/running）单源自 models（部分唯一索引谓词同源，杜绝 CR-10 漂移）。
_PREVIEW_ACTIVE = models.PREVIEW_ACTIVE_STATUSES
# 「活跃部署」谓词集（queued/running）同源自 models（对账 #10 / 结果卡终态判定）。
_DEPLOYMENT_ACTIVE = models.DEPLOYMENT_ACTIVE_STATUSES

# FR-12 部署完成诊断（constants.DIAGNOSTIC_TYPES 权威登记，deploy.finished 已登记）。
_DEPLOY_DIAG_FINISHED = "deploy.finished"
assert _DEPLOY_DIAG_FINISHED in DIAGNOSTIC_TYPES
# 对账 #10 fail-closed 定型文案（DB 事实推导，非 daemon 上报；副作用不可重放，铁律 3）。
_DEPLOY_FAIL_DAEMON_RESTARTED = "daemon restarted: deployment outcome unknown"
_DEPLOY_RESTART_CARD_BODY = (
    "⚠️ 部署结果未知：daemon 重启，请人工核实（副作用不可重放，已置 failed 未自动重跑）"
)


def _deploy_duration(started: str | None, finished: str | None) -> str | None:
    if not started or not finished:
        return None
    try:
        start_dt = datetime.fromisoformat(started)
        finish_dt = datetime.fromisoformat(finished)
    except ValueError:
        return None
    secs = max(0.0, (finish_dt - start_dt).total_seconds())
    return f"{secs:.1f}s"


def _deploy_result_body(row: dict[str, Any]) -> str:
    """结果卡文案（含状态/URL/退出码/耗时；success/failed 分色由前端按 card_kind+status）。"""
    head = "✅ 部署成功" if row["status"] == "success" else "❌ 部署失败"
    parts = [head]
    if row.get("url"):
        parts.append(f"URL：{row['url']}")
    if row.get("exit_code") is not None:
        parts.append(f"退出码：{row['exit_code']}")
    dur = _deploy_duration(row.get("started_at"), row.get("finished_at"))
    if dur is not None:
        parts.append(f"耗时：{dur}")
    return " ｜ ".join(parts)


# 最后已知态里"应存活"的期望集合（对账 #2 自动 resume 的触发条件，契约 D §4.4）。
_RESUMABLE = {AgentStatus.STARTING.value, AgentStatus.IDLE.value, AgentStatus.BUSY.value}
_DELIVERABLE = {AgentStatus.IDLE.value, AgentStatus.BUSY.value}

# B-1 ②′ 解锁主动唤醒：系统消息 body 固定前缀 = 节点/线程级幂等标记（kind=SYSTEM 且线程内至多一
# 任务 → 至多一条，作者恒 None 人类不可伪造，前缀 like 判存在即『是否已通知过』，无需新表/新列）。
_UNBLOCK_PREFIX = "上游已全部完成"
# 任务终态集（解锁唤醒对已终态任务无意义——与 W9 terminal 判据一致，done/closed）。
_TERMINAL_TASK = {TaskStatus.DONE.value, TaskStatus.CLOSED.value}

_ACTIVITY_THROTTLE_SEC = 0.5  # 契约 D §7：server ≥500ms 节流转发 agent.activity
_LAST_SEEN_THROTTLE_SEC = 60  # 契约 D §2：last_seen_at 写库节流

# 对账 #9 starting 超时（契约 D §4.4）：连接仍在但 starting 行迟迟未收 preview.status（healthy/
# failed）→ daemon 侧健康检查上限 120s（D §5.3）+ 裕度。超此仍 starting 视同失进程 fail-closed。
_PREVIEW_STARTING_TIMEOUT_SEC = 180.0

# FR-11.3 进程状态入 diagnostic（constants.DIAGNOSTIC_TYPES 为权威登记表，同 gc/ledger 体例）：
# running→started、failed→failed、recycled→recycled 三种进程状态均落诊断（preview.failed 由
# Fable 补齐登记——失败是最该进诊断时间线的进程状态；对账 #9 置 failed 同口径）。
_PREVIEW_DIAG_STARTED = "preview.started"
_PREVIEW_DIAG_FAILED = "preview.failed"
_PREVIEW_DIAG_RECYCLED = "preview.recycled"
assert _PREVIEW_DIAG_STARTED in DIAGNOSTIC_TYPES
assert _PREVIEW_DIAG_FAILED in DIAGNOSTIC_TYPES
assert _PREVIEW_DIAG_RECYCLED in DIAGNOSTIC_TYPES

# 对账 #9 / _report_preview_status 的 failed fail_log_tail 定型文案（DB 事实推导，非 daemon 上报）。
_PREVIEW_FAIL_DAEMON_RESTARTED = "daemon restarted"
# 同进程重连（boot_nonce 未变）但 hello 预览进程表无该会话：start 指令在断连期丢失/进程域记录
# 缺位——非重启但进程事实已失，同口径 fail-close（裁决 #11 不自动重拉），措辞区分便于诊断。
_PREVIEW_FAIL_PROCESS_LOST = "preview process lost"
_PREVIEW_FAIL_STARTING_TIMEOUT = "starting timeout: no preview.status received"
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
    # L4a（CR-M8-1 收尾）：读循环只收帧入队，独立 writer 协程消费 REPORT——DB 写 offload
    # 到线程池（asyncio.to_thread），既不阻塞事件循环、又不因撞锁把整条连接撕掉。None = 关停哨兵。
    report_queue: asyncio.Queue[dict[str, Any] | None] = field(default_factory=asyncio.Queue)
    last_ping_monotonic: float = field(default_factory=time.monotonic)
    last_seen_written: float = 0.0
    superseded: bool = False
    # hello v1.0.5：daemon 进程 boot nonce / 预览进程表快照 / 是否判定为真重启（对账 #9 消费；
    # daemon_restarted 缺省 True = 旧 daemon 无 nonce 按重启口径全量 fail-close，行为兼容）。
    boot_nonce: str | None = None
    hello_previews: dict[str, PreviewStatusData] = field(default_factory=dict)
    daemon_restarted: bool = True

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
        data_root: str | Path | None = None,
        ack_timeout: float = ACK_TIMEOUT_SEC,
        query_timeout: float = ACK_TIMEOUT_SEC,
        reconcile_interval: float = RECONCILE_INTERVAL_SEC,
        reminder_interval: float = 5.0,
        silence_interval: float = 60.0,
        held_interval: float = 5.0,
        heartbeat_timeout: float = 60.0,
        preview_recycle_interval: float = 60.0,
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
        self.preview_recycle_interval = preview_recycle_interval
        self.preview_starting_timeout_sec = _PREVIEW_STARTING_TIMEOUT_SEC
        self._conns: dict[str, DaemonConnection] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sub_token: int | None = None
        self._bg: list[asyncio.Task] = []
        self._activity_last: dict[str, float] = {}
        self._worktree_locks: dict[str, asyncio.Lock] = {}
        # DEDAG：任务级 merge 运行身份（task_id → (computer_id, project_id)）。单进程内存裁量
        # （landing_lock 先例）：跨进程/崩溃恢复不承诺——pending 丢失后行状态仍持久，人工重触发
        # 补齐副作用。project_id 供「同 Project 串行」409 判定（沿 deploy 先例）。
        self._merge_pending: dict[str, tuple[str, str]] = {}
        # L4a：_merge_pending 被 loop 侧协程（merge 下发）与 writer 线程侧上报处理（完成后 pop）
        # 并发访问 → threading.Lock 守全部读改写（GIL 只保单 op 原子，不保 get-then-set 复合）。
        # 仅护本 dict，锁内不做 I/O，无死锁面。
        self._pending_lock = threading.Lock()
        # 部署日志落盘目录（K4）：deploy.log 逐 chunk 追加 <data_root>/deploy-logs/<id>.log；首条落
        # 盘时把绝对路径写回 deployments.log_path（GET log 端点 server 直读）。data_root 与
        # FileStore 同根（app.py 传入；None 兜底 DEFAULT_DATA_ROOT，同 db 目录父级）。
        if data_root is None:
            from coagentia_server.db.engine import DEFAULT_DB_PATH

            data_root = DEFAULT_DB_PATH.parent
        self._deploy_log_dir = Path(data_root) / "deploy-logs"
        # 每 deployment 已收最大 chunk_seq（去重：重连窗口 daemon 可能重发已 ack 帧）。
        self._deploy_log_seq: dict[str, int] = {}
        # 每 computer 最后一次 hello 的 boot_nonce（对账 #9 区分 WS jitter 与真重启；server 重启
        # 丢失 → 首连按「重启」措辞，存活判定不受影响——它按 hello 预览进程表逐会话推导）。
        self._last_boot_nonce: dict[str, str | None] = {}

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
            loop.create_task(self._preview_recycle_loop()),
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
            # 快速/as_task 的普通任务占绝大多数；只在自身写代码时扫（DEDAG：无下游解锁面）。
            channel_id = event.channel_id
            if channel_id and task.get("writes_code"):
                loop.call_soon_threadsafe(self._spawn, self._scan_channel_worktrees(channel_id))
            if event.type == EventType.TASK_UPDATED and task.get("writes_code"):
                if task.get("id") and change.get("kind") in {
                    TaskEventKind.CLAIM.value,
                    TaskEventKind.ASSIGN.value,
                }:
                    loop.call_soon_threadsafe(
                        self._spawn, self._notify_active_task_owner(task["id"])
                    )
            # 回收触发②任务终态（K3；FR-11.1）：任务转 done/closed → 即回收其活跃预览（下发
            # preview.stop；判定归 server，recycled 由 daemon 上报确认，不等 keep_days cleanup）。
            if (
                event.type == EventType.TASK_UPDATED
                and task.get("id")
                and change.get("to_status") in {TaskStatus.DONE.value, TaskStatus.CLOSED.value}
            ):
                loop.call_soon_threadsafe(self._spawn, self._recycle_task_preview(task["id"]))
            return

    def _spawn(self, coro: Any) -> None:
        # L4a：可能从 writer 线程（上报处理 offload 到 to_thread 内）调用——create_task 是 loop 亲和
        # 的，跨线程须经 call_soon_threadsafe 回投 loop（先例：REST 路由用 call_soon_threadsafe(
        # self._spawn, …)）。在 loop 线程则直接建任务。
        loop = self._loop
        assert loop is not None
        try:
            on_loop = asyncio.get_running_loop() is loop
        except RuntimeError:
            on_loop = False
        if not on_loop:
            loop.call_soon_threadsafe(self._spawn, coro)
            return
        task = loop.create_task(coro)
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
            # L4a：独立 writer 消费 report_queue（DB 写 offload 线程）；随 conn.tasks 在 teardown
            # 被 cancel。须在 _reader 前起，否则入队的上报无人消费。
            self._spawn_on_conn(conn, self._writer(conn))
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
            row = (
                c.execute(select(_COMPUTER).where(_COMPUTER.c.api_key_hash == digest))
                .mappings()
                .first()
            )
        if row is None:
            await sock.close(code=1008)
            return None
        return dict(row)

    async def _recv_hello(self, conn: DaemonConnection) -> tuple[str, DaemonHelloData] | None:
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
        # 新连接可能来自 daemon 进程重启：解除该机器任务级 merge 的运行记忆（人工重触发补齐）。
        with self._pending_lock:  # L4a：与 writer 线程侧上报 pop 互斥
            self._merge_pending = {
                task_id: pending
                for task_id, pending in self._merge_pending.items()
                if pending[0] != conn.computer_id
            }
        conn.present = {a.agent_member_id: a.status.value for a in hello.agents}
        # hello v1.0.5：登记 boot nonce 与预览进程表快照（对账 #9 消费）。nonce 未变 = 同 daemon
        # 进程 WS jitter；变化/缺失/无前值 = 按真重启口径。
        conn.boot_nonce = hello.boot_nonce
        conn.hello_previews = {p.preview_session_id: p for p in hello.previews}
        prev_nonce = self._last_boot_nonce.get(conn.computer_id)
        conn.daemon_restarted = (
            hello.boot_nonce is None or prev_nonce is None or prev_nonce != hello.boot_nonce
        )
        self._last_boot_nonce[conn.computer_id] = hello.boot_nonce
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
                tx.conn.execute(select(_COMPUTER).where(_COMPUTER.c.id == conn.computer_id))
                .mappings()
                .first()
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
        # L4a：读循环只做**收帧**——ack/reply/ping 是快路径（唤醒 Future / 回 PONG，无长阻塞 DB
        # 写）就地处理；REPORT 入队交独立 writer 消费（DB 写 offload 到线程），故任一上报撞锁既不
        # 阻塞本读循环收下一帧、也不因写异常把连接撕掉（裁决 #7）。帧内顺序由 FIFO 队列保序。
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
                conn.report_queue.put_nowait(raw)
            # instr / query 仅 server→daemon，daemon 侧不应发；忽略

    async def _writer(self, conn: DaemonConnection) -> None:
        """L4a 独立上报消费者（每连接一个）：从 report_queue 顺序取帧 → DB 写 offload 到线程池
        （不阻塞 loop）→ 成功后按需回 ack。保三不变量：**帧内顺序**（FIFO 串行消费）、**ack 语义**
        （写成功后才 ack，daemon 重传判据不变）、**emit 时序**（gateway_tx 提交后才 bus.emit，在
        offload 线程内经 call_soon_threadsafe 投队列，与 loop 内等价）。单帧写异常**只记日志不撕
        连接**（daemon 按 at-least-once 重传或对账兜底）。"""
        while True:
            raw = await conn.report_queue.get()
            try:
                if raw is None:
                    return  # 关停哨兵
                try:
                    ack_ref = await asyncio.to_thread(self._handle_report_write, conn, raw)
                except Exception:  # noqa: BLE001 —— 单帧写失败不得撕连接（如 SQLITE_BUSY）
                    _log.exception("daemon report write failed; connection kept alive")
                    continue
                if ack_ref is not None:
                    with contextlib.suppress(Exception):
                        await self._ack(conn, ack_ref, AckResult.DONE)
            finally:
                conn.report_queue.task_done()  # 支撑 drain_reports 的 queue.join() 屏障

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
        # PONG 先回（liveness 优先，不等 DB 写）；last_seen 心跳写节流 + offload 到线程，撞锁既不
        # 阻塞读循环、也不撕连接（L4a：心跳裸 engine.begin 移出事件循环）。
        await self._send_raw(conn, {"v": DAEMON_PROTOCOL_V, "kind": FrameKind.PONG.value})
        if now - conn.last_seen_written >= _LAST_SEEN_THROTTLE_SEC:
            conn.last_seen_written = now
            self._spawn(self._write_last_seen(conn.computer_id))

    async def _write_last_seen(self, computer_id: str) -> None:
        """L4a：心跳 last_seen 写移出读循环——offload 到线程池（best-effort，失败仅记日志）。"""

        def _write() -> None:
            with self._engine.begin() as c:
                c.execute(
                    update(_COMPUTER)
                    .where(_COMPUTER.c.id == computer_id)
                    .values(last_seen_at=now_iso())
                )

        try:
            await asyncio.to_thread(_write)
        except Exception:  # noqa: BLE001 —— 心跳写失败非致命
            _log.exception("last_seen heartbeat write failed")

    async def drain_reports(self, computer_id: str) -> None:
        """L4a 测试确定性屏障：等某连接 report_queue 全部消费完（含在途一条）。生产不用——
        `sync()`（ping）只保帧接收/ack 副作用，不再保非 ack 上报的落库（上报改由 writer 异步消费，
        故 reader 不因写阻塞/撞锁而滞留）。需观测非 ack 上报效果的测试改调本屏障（queue.join）。"""
        conn = self._conns.get(computer_id)
        if conn is None:
            return
        await conn.report_queue.join()

    # ---------------------------------------------------------------- 上报处理（契约 D §7）

    def _handle_report_write(self, conn: DaemonConnection, raw: dict[str, Any]) -> str | None:
        """L4a：**同步**上报落库分发（由 _writer 经 asyncio.to_thread 在线程池执行，故不阻塞
        loop）。返回**需回 ack 的 frame_id**（ack 类上报：diagnostics/usage/check/deploy_log/
        deploy_finished），无需 ack 的返回 None——ack 由 writer 在 loop 上写（写成功后才 ack）。"""
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
            return frame_id
        elif rtype == ReportType.USAGE_BATCH:
            self._report_usage(conn, UsageBatchData.model_validate(data))
            return frame_id
        elif rtype == ReportType.WORKTREE_STATUS:
            self._report_worktree_status(conn, WorktreeStatusData.model_validate(data))
        elif rtype == ReportType.CHECK_FINISHED:
            self._report_check_finished(conn, CheckFinishedData.model_validate(data))
            return frame_id
        elif rtype == ReportType.PREVIEW_STATUS:
            self._report_preview_status(conn, PreviewStatusData.model_validate(data))
        elif rtype == ReportType.DEPLOY_LOG:
            self._report_deploy_log(conn, DeployLogReportData.model_validate(data))
            return frame_id
        elif rtype == ReportType.DEPLOY_FINISHED:
            self._report_deploy_finished(conn, DeployFinishedData.model_validate(data))
            return frame_id
        # hello（重复）：忽略
        return None

    def _report_worktree_status(self, conn: DaemonConnection, data: WorktreeStatusData) -> None:
        """worktree.status：按 task_id 持久状态、广播；首次 active 落 durable 目录消息。

        DEDAG（B v1.6 §14）：merged/conflicted 上报对应**任务级 merge**——运行身份 =
        `_merge_pending`（单进程内存 + `_pending_lock` 互斥，landing_lock 同款单进程裁量；
        server 崩溃后 pending 丢失 → 行状态仍经非信任路径持久，完成副作用（消息/冲突派回）
        缺失由人工重触发补齐，merge 端点校验幂等兜底）。"""
        with self._pending_lock:  # L4a
            merge_running = self._merge_pending.get(data.task_id)
        is_merge_report = (
            data.status in {"merged", "conflicted"} and merge_running is not None
        )
        if data.status == "merged" and not data.merge_commit:
            # #10 fail-closed：缺 merge_commit 不能标 MERGED（否则 apply_status 误置假终态），
            # 故跳过 apply_status；有运行中任务级 merge → 记失败留痕，无 → 静默丢弃不 wedge。
            if is_merge_report:
                with gateway_tx(self._engine, self._bus) as tx:
                    merge_domain.fail_merge(
                        tx,
                        task_id=data.task_id,
                        reason="daemon merged 上报缺 merge_commit",
                    )
                with self._pending_lock:
                    self._merge_pending.pop(data.task_id, None)
            return
        with gateway_tx(self._engine, self._bus) as tx:
            result = worktree_service.apply_status(
                tx.conn,
                computer_id=conn.computer_id,
                data=data,
                trusted_running_merge=is_merge_report,
            )
            if result is None:
                return  # 非本机 Project 的越界上报不污染事实源
            for updated_row in (result.row, *result.alias_rows):
                tx.emit(
                    EventType.WORKTREE_UPDATED,
                    result.channel_id,
                    {"worktree": worktree_public(updated_row)},
                )
            if is_merge_report:
                merge_domain.apply_merge_report(
                    tx,
                    data=data,
                    worktree_row=result.row,
                    workspace_id=result.workspace_id,
                    channel_id=result.channel_id,
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
        if is_merge_report:
            with self._pending_lock:  # L4a
                self._merge_pending.pop(data.task_id, None)

    def _report_check_finished(self, conn: DaemonConnection, data: CheckFinishedData) -> None:
        # DEDAG：check 系统节点随画布退役,server 不再下发 check.run;此上报仅协议兼容消费
        # （契约 D 零修订）——不应出现,出现即留日志观察。
        _log.info("忽略 check.finished（DEDAG 后无 check.run 下发面）: %s", data.node_id)

    # ---------------------------------------------------------------- 预览状态上报（M7 K3）

    def _report_preview_status(self, conn: DaemonConnection, data: PreviewStatusData) -> None:
        """preview.status（契约 D §7）：条件 UPDATE 推进预览行 + 广播 preview.updated（+ FR-11.3
        进程状态诊断）。

        **幂等且单调**（K2 审查 note）：daemon 对已在跑会话会重发冗余 starting/running 补报——条件
        UPDATE 的「起态门」天然防回退：starting→running 仅 WHERE status='starting' 命中一次，重复
        running/乱序帧 rowcount=0 幂等 noop；failed/recycled 终态不被后到帧复活（越界更新落 CAS
        起态门外）。判定归 server、执行归 daemon：daemon 只上报事实（status/port/log_tail），server
        落库并广播（无 ack，同 worktree.status 载状态类）。"""
        if data.status == "running":
            stmt = (
                update(_PREVIEW)
                .where(
                    _PREVIEW.c.id == data.preview_session_id,
                    _PREVIEW.c.status == "starting",
                )
                .values(status="running", port=data.port)
            )
            diag_type: str | None = _PREVIEW_DIAG_STARTED
        elif data.status == "failed":
            stmt = (
                update(_PREVIEW)
                .where(
                    _PREVIEW.c.id == data.preview_session_id,
                    _PREVIEW.c.status.in_(_PREVIEW_ACTIVE),
                )
                .values(status="failed", fail_log_tail=data.log_tail)
            )
            diag_type = _PREVIEW_DIAG_FAILED
        elif data.status == "recycled":
            stmt = (
                update(_PREVIEW)
                .where(
                    _PREVIEW.c.id == data.preview_session_id,
                    _PREVIEW.c.status.in_(_PREVIEW_ACTIVE),
                )
                .values(status="recycled", recycled_at=now_iso())
            )
            diag_type = _PREVIEW_DIAG_RECYCLED
        else:  # 冗余 starting 补报：行已建为 starting，永不回退——直接 noop
            return
        with gateway_tx(self._engine, self._bus) as tx:
            if tx.conn.execute(stmt).rowcount == 0:
                return  # 竞败/重复/乱序/已终态：CAS 起态门未命中 → 幂等 noop（不广播）
            self._emit_preview_updated(tx, data.preview_session_id, diag_type=diag_type)

    def _emit_preview_updated(
        self, tx: Any, preview_id: str, *, diag_type: str | None = None
    ) -> None:
        """回读预览行 → 广播 preview.updated 到任务频道；diag_type 非空则附一条进程状态诊断
        （FR-11.3）。tx = 进行中 gateway_tx（提交后按序 flush 事件）。"""
        row = (
            tx.conn.execute(select(_PREVIEW).where(_PREVIEW.c.id == preview_id)).mappings().first()
        )
        if row is None:
            return
        row = dict(row)
        ctx = (
            tx.conn.execute(
                select(_TASK.c.channel_id, _TASK.c.owner_member_id).where(
                    _TASK.c.id == row["task_id"]
                )
            )
            .mappings()
            .first()
        )
        channel_id = ctx["channel_id"] if ctx is not None else None
        owner = ctx["owner_member_id"] if ctx is not None else None
        tx.emit(EventType.PREVIEW_UPDATED, channel_id, {"preview": preview_session_public(row)})
        if diag_type is not None:
            self._append_preview_diagnostic(
                tx, row, channel_id=channel_id, owner=owner, diag_type=diag_type
            )

    def _emit_agent_diagnostic(
        self,
        tx: Any,
        *,
        workspace_id: str,
        diag_type: str,
        payload: dict[str, Any],
        member_id: str | None,
        channel_id: str | None = None,
        task_id: str | None = None,
    ) -> None:
        """诊断行落库 + member_id 为 Agent 时广播 DIAGNOSTIC_APPENDED（preview.*/deploy.* 共用单点；
        非 Agent/None → 只落库不广播）。归属 agent/channel/task 供人类循。"""
        ts = now_iso()
        agent_owner: str | None = None
        if member_id is not None:
            is_agent = tx.conn.execute(
                select(_AGENT.c.member_id).where(_AGENT.c.member_id == member_id)
            ).first()
            if is_agent is not None:
                agent_owner = member_id
        seq = tx.conn.execute(
            insert(_DIAG)
            .values(
                workspace_id=workspace_id,
                agent_member_id=agent_owner,
                type=diag_type,
                channel_id=channel_id,
                task_id=task_id,
                batch_id=None,
                payload=payload,
                created_at=ts,
            )
            .returning(_DIAG.c.seq)
        ).scalar_one()
        if agent_owner is not None:
            pub = diagnostic_public(
                {
                    "seq": seq,
                    "workspace_id": workspace_id,
                    "agent_member_id": agent_owner,
                    "type": diag_type,
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
                {"agent_member_id": agent_owner, "events": [pub]},
            )

    def _append_preview_diagnostic(
        self,
        tx: Any,
        row: dict[str, Any],
        *,
        channel_id: str | None,
        owner: str | None,
        diag_type: str,
    ) -> None:
        """FR-11.3：preview.* 进程状态诊断（Agent owner 时广播，_emit_agent_diagnostic 单点）。"""
        self._emit_agent_diagnostic(
            tx,
            workspace_id=row["workspace_id"],
            diag_type=diag_type,
            payload={"preview_session_id": row["id"], "status": row["status"], "port": row["port"]},
            member_id=owner,
            channel_id=channel_id,
            task_id=row["task_id"],
        )

    # ---------------------------------------------------------------- 部署上报（M7b K4）

    def _deploy_log_file(self, deployment_id: str) -> Path:
        return self._deploy_log_dir / f"{deployment_id}.log"

    def _append_deploy_log_file(self, deployment_id: str, lines: list[str]) -> str:
        """把 lines 追加到该 deployment 的落盘日志文件，返回绝对路径（首次落盘写回 log_path）。

        超 BUFFER_DEPLOY_LOG_MAX_BYTES（5MB）停止追加（truncated 判定交给 GET log 端点）。判定归
        server：server 单点落盘，GET /deployments/{id}/log 直读该文件（不依赖 daemon 在线）。"""
        self._deploy_log_dir.mkdir(parents=True, exist_ok=True)
        path = self._deploy_log_file(deployment_id)
        if lines:
            try:
                over_cap = path.exists() and path.stat().st_size >= BUFFER_DEPLOY_LOG_MAX_BYTES
                if not over_cap:
                    with open(path, "a", encoding="utf-8", newline="\n") as f:
                        for line in lines:
                            f.write(line + "\n")
            except OSError:
                pass  # 落盘失败不阻断状态推进/广播（日志尽力而为）
        return str(path)

    # R-10：chunk_seq 去重游标持久化——`<id>.log` 旁 `<id>.seq` sidecar 存已收 max chunk_seq。
    # server 重启后 _deploy_log_seq 内存丢失，daemon 重连按 at-least-once 重发未 ack 的 deploy.log
    # 帧（chunk_seq 从已收处），无持久游标则 last=None → 旧行被重复追加。sidecar 让重启后仍能去重。
    def _deploy_log_seq_file(self, deployment_id: str) -> Path:
        return self._deploy_log_dir / f"{deployment_id}.seq"

    def _recover_deploy_log_seq(self, deployment_id: str) -> int | None:
        """内存无游标时（如 server 重启）从 sidecar 恢复已收 max chunk_seq；缺失/损坏 → None。"""
        try:
            return int(self._deploy_log_seq_file(deployment_id).read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None

    def _persist_deploy_log_seq(self, deployment_id: str, chunk_seq: int) -> None:
        """推进游标同刻落 sidecar（在日志追加之后调用；落盘失败尽力而为不阻断）。"""
        try:
            self._deploy_log_dir.mkdir(parents=True, exist_ok=True)
            self._deploy_log_seq_file(deployment_id).write_text(str(chunk_seq), encoding="utf-8")
        except OSError:
            pass

    def _delete_deploy_log_seq(self, deployment_id: str) -> None:
        """终态/fail-close 后清 sidecar（对齐内存游标 pop——部署已终结，无需再去重）。"""
        with contextlib.suppress(OSError):
            self._deploy_log_seq_file(deployment_id).unlink(missing_ok=True)

    def _report_deploy_log(self, conn: DaemonConnection, data: DeployLogReportData) -> None:
        """deploy.log（契约 D §7，需 ack）：chunk_seq 去重 + 落盘 + queued→running 条件 UPDATE +
        订阅制广播 deployment.log（+ 首条推进时 deployment.updated 全量广播）。

        chunk_seq 去重（按已收 max）：deploy.log 是 ack 类正常不重发；去重只防重连窗口重发未 ack
        帧（daemon 重启后 chunk_seq 从 0 重计，但重启走对账 #10 fail-close，不重放旧日志）。
        收到任意 deploy.log 且行 status='queued' → 隐含起跑：条件 UPDATE 置 running。"""
        last = self._deploy_log_seq.get(data.deployment_id)
        if last is None:  # R-10：server 重启后内存游标丢失 → 从 sidecar 恢复
            last = self._recover_deploy_log_seq(data.deployment_id)
        if last is not None and data.chunk_seq <= last:
            return  # 重连窗口/重启后重发已收帧：按已收 max 去重（游标内存丢失则 sidecar 兜底）
        log_path = str(self._deploy_log_file(data.deployment_id))  # 纯路径，无 I/O（落盘在提交后）
        with gateway_tx(self._engine, self._bus) as tx:
            row = (
                tx.conn.execute(
                    select(_DEPLOYMENT.c.id, _DEPLOYMENT.c.log_path).where(
                        _DEPLOYMENT.c.id == data.deployment_id
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                return  # 未知部署（跨机越界/已清）：不落盘不广播不推进游标
            if row["log_path"] is None:  # 首次写回绝对路径（log_path 非状态列，无条件 UPDATE）
                tx.conn.execute(
                    update(_DEPLOYMENT)
                    .where(_DEPLOYMENT.c.id == data.deployment_id)
                    .values(log_path=log_path)
                )
            promoted = tx.conn.execute(
                update(_DEPLOYMENT)
                .where(
                    _DEPLOYMENT.c.id == data.deployment_id,
                    _DEPLOYMENT.c.status == "queued",
                )
                .values(status="running", started_at=now_iso())
            ).rowcount
            if promoted:
                self._emit_deployment_updated(tx, data.deployment_id)
            # 订阅制广播（ws/hub._dispatch 按 deploy_log_subs 过滤 deployment_id）。
            tx.emit(
                EventType.DEPLOYMENT_LOG,
                None,
                {
                    "deployment_id": data.deployment_id,
                    "chunk_seq": data.chunk_seq,
                    "lines": data.lines,
                },
            )
        # **提交后**才落盘 + 推进内存去重游标（复审 CONFIRMED 双修）：gateway_tx 若回滚（如
        # SQLITE_BUSY），未推进游标 → daemon 重发不被误判重复而吞（防丢帧），且未落盘 → 不产生重复
        # 行。走到此处 = 事务已成功提交（row 为 None 已早返，异常已由 with 抛出）。
        self._append_deploy_log_file(data.deployment_id, data.lines)
        self._deploy_log_seq[data.deployment_id] = data.chunk_seq
        self._persist_deploy_log_seq(data.deployment_id, data.chunk_seq)  # R-10：游标落 sidecar

    def _report_deploy_finished(self, conn: DaemonConnection, data: DeployFinishedData) -> None:
        """deploy.finished（契约 D §7，需 ack）：条件 UPDATE 落终态 + deployment.updated 全量广播 +
        结果卡发 project 全部绑定频道各一条（mention 触发者）+ 部署完成诊断。

        条件 UPDATE `WHERE status IN (queued,running)`：rowcount=0（已终态/重复/未知）→ noop 不广播
        （幂等，daemon 重发终态帧安全）。"""
        with gateway_tx(self._engine, self._bus) as tx:
            res = tx.conn.execute(
                update(_DEPLOYMENT)
                .where(
                    _DEPLOYMENT.c.id == data.deployment_id,
                    _DEPLOYMENT.c.status.in_(_DEPLOYMENT_ACTIVE),
                )
                .values(
                    status=data.status,
                    exit_code=data.exit_code,
                    url=data.url,
                    finished_at=now_iso(),
                )
            )
            if res.rowcount == 0:
                return  # 已终态/重复/未知：CAS 未命中 → 幂等 noop（不广播）
            row = dict(
                tx.conn.execute(select(_DEPLOYMENT).where(_DEPLOYMENT.c.id == data.deployment_id))
                .mappings()
                .one()
            )
            tx.emit(EventType.DEPLOYMENT_UPDATED, None, {"deployment": deployment_public(row)})
            self._deploy_log_seq.pop(data.deployment_id, None)  # 终态后清去重游标
            self._delete_deploy_log_seq(data.deployment_id)  # R-10：同清 sidecar
            self._post_deployment_cards(tx, row, body=_deploy_result_body(row))
            self._append_deploy_diagnostic(tx, row)

    def _emit_deployment_updated(self, tx: Any, deployment_id: str) -> None:
        """回读部署行 → 全量广播 deployment.updated（部署为工作区级实体，channel_id=None）。"""
        row = (
            tx.conn.execute(select(_DEPLOYMENT).where(_DEPLOYMENT.c.id == deployment_id))
            .mappings()
            .first()
        )
        if row is None:
            return
        tx.emit(EventType.DEPLOYMENT_UPDATED, None, {"deployment": deployment_public(dict(row))})

    def _post_deployment_cards(self, tx: Any, row: dict[str, Any], *, body: str) -> None:
        """结果卡（裁决 #13）：project 每个绑定频道各一条系统消息（card_kind=deployment,
        card_ref=deployment_id），mention 触发者（success/failed 分色由前端）。"""
        channel_ids = list(
            tx.conn.execute(
                select(_CHANNEL_PROJECT.c.channel_id).where(
                    _CHANNEL_PROJECT.c.project_id == row["project_id"]
                )
            ).scalars()
        )
        for channel_id in channel_ids:
            self._post_system_message(
                tx,
                workspace_id=row["workspace_id"],
                channel_id=channel_id,
                body=body,
                mention_member_ids=(row["triggered_by_member_id"],),
                card_kind=CardKind.DEPLOYMENT.value,
                card_ref=row["id"],
            )

    def _append_deploy_diagnostic(self, tx: Any, row: dict[str, Any]) -> None:
        """FR-12：deploy.finished 诊断（触发者为 Agent 时广播，_emit_agent_diagnostic 单点）。"""
        self._emit_agent_diagnostic(
            tx,
            workspace_id=row["workspace_id"],
            diag_type=_DEPLOY_DIAG_FINISHED,
            payload={
                "deployment_id": row["id"],
                "status": row["status"],
                "exit_code": row["exit_code"],
                "url": row["url"],
            },
            member_id=row["triggered_by_member_id"],
        )

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
                tx.conn.execute(select(_COMPUTER).where(_COMPUTER.c.id == conn.computer_id))
                .mappings()
                .first()
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
            # 批量预取（K7）：已存在 id 集 + thread_root_id→task_id 归属映射各一次查完，替代逐事件
            # 两查。`seen` 由已存在集起步、每插一行补记——批内重复 id 与旧「插后再查」判重逐字等价。
            ev_ids = [ev.id for ev in d.events]
            seen: set[str] = (
                {r[0] for r in tx.conn.execute(select(_USAGE.c.id).where(_USAGE.c.id.in_(ev_ids)))}
                if ev_ids
                else set()
            )
            root_ids = {ev.thread_root_id for ev in d.events if ev.thread_root_id is not None}
            attribution: dict[str, str] = {}
            if root_ids:
                for row in tx.conn.execute(
                    select(_TASK.c.root_message_id, _TASK.c.id).where(
                        _TASK.c.root_message_id.in_(root_ids)
                    )
                ):
                    attribution.setdefault(row[0], row[1])  # 唯一根消息，至多一行
            for ev in d.events:
                if ev.id in seen:
                    continue  # 重传去重（铁律 5）——含批内重复 id
                seen.add(ev.id)
                # 归属富化（契约 E §7.4）：thread_root_id 命中 tasks.root_message_id → task_id。
                # 三路：无提示→None；有提示无匹配→None；命中→task.id。
                task_id: str | None = None
                if ev.thread_root_id is not None:
                    task_id = attribution.get(ev.thread_root_id)
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
            # DEDAG：画布 gating 随图退役——过滤面只剩 worktree path fail-closed 截断。
            recipients = self._channel_agent_recipients(c, channel_id)
        for agent_id, computer_id in recipients:
            conn = self._conns.get(computer_id)
            if conn is None:
                continue  # daemon 离线 → 积压，重连对账 #3 补
            status = conn.present.get(agent_id)
            if status not in _DELIVERABLE:
                continue  # offline/starting/error → 不投不唤醒（§8.2）
            with self._engine.connect() as c:
                # 单次过滤（M6 review F6/效率）：可投前缀既做「本消息是否已可投」判定（唤醒
                # 门），也做「有没有东西可冲」判定（busy 冲洗门）。本消息被扣（不在前缀）只
                # 取消**以它为由的唤醒**，不取消前缀冲洗——否则前缀里早先投递失败/被扣后解锁
                # 的消息要等 60s 对账才走（旧版对 busy 无条件冲洗，此语义不得收窄）。
                prefix, _ = self._filter_agent_delivery(
                    c, self._backlog(c, agent_id, channel_id), agent_id
                )
                if not prefix:
                    continue  # 首条即被扣或无积压：无可投面，解锁后由触发/对账再评
                in_prefix = message_id in {item["id"] for item in prefix}
                reason = self._compute_trigger(c, msg, channel, agent_id) if in_prefix else None
            if status == AgentStatus.BUSY.value:
                await self._deliver_backlog(conn, agent_id, channel_id)  # 直投（§8.2）
            elif reason is not None:  # idle + 触发命中 → wake + deliver
                with self._engine.connect() as c:
                    refs = self._wake_refs(c, reason=reason, agent_id=agent_id, messages=[msg])
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
        """投递过滤（DEDAG：画布 gating 退役，仅剩 worktree path fail-closed「截断」）。

        **worktree path 未就绪（delivery_waits_for_directory）→ 截断**：被扣消息是 activation 载
        体、无替代唤醒面，仍须只投连续前缀（daemon 按频道最大 id 去重，越过它先投 later 会让解
        锁后早消息 noop 漏投），遇首个即停（#7 权衡：延迟不丢）。"""
        deliver: list[dict[str, Any]] = []
        watermark: str | None = None
        for message in raw:
            if worktree_service.delivery_waits_for_directory(
                c, agent_member_id=agent_id, message=message
            ):
                break  # worktree path 未就绪 → 截断其后全部（无替代唤醒面，#7 延迟不丢）
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

        await self.send_instr(conn, agent_id, InstrType.MESSAGE_DELIVER, prepare=_prepare)

    def _compute_trigger(
        self, c: Connection, msg: dict[str, Any], channel: dict[str, Any], agent_id: str
    ) -> WakeReason | None:
        """唤醒触发判定（契约 D §8.2）：DM 恒触发 / @mention / reminder 锚点系统消息视同 mention。

        对账 #3 的"无触发→静默积压"要求普通频道非 @ 消息**不**唤醒——故只有 DM、@mention、
        reminder 锚点（system+mention）构成唤醒触发；其余进静默积压随下次唤醒随批投递。
        """
        if msg.get("author_member_id") == agent_id:
            return None  # 不因自己发的消息被唤醒
        mentioned = (
            c.execute(
                select(_MENTION.c.member_id).where(
                    _MENTION.c.message_id == msg["id"], _MENTION.c.member_id == agent_id
                )
            ).first()
            is not None
        )
        if (
            worktree_service.activation_context(c, agent_member_id=agent_id, message=msg)
            is not None
        ):
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
        # DEDAG：node_id ref 随画布退役（WakeRefs.node_id 字段保留于契约 D，恒 None）。
        del c, reason, agent_id
        return WakeRefs(message_ids=[message["id"] for message in messages], node_id=None)

    def _channel_agent_recipients(self, c: Connection, channel_id: str) -> list[tuple[str, str]]:
        rows = c.execute(
            select(_AGENT.c.member_id, _AGENT.c.computer_id)
            .select_from(
                _CHANNEL_MEMBER.join(_MEMBER, _CHANNEL_MEMBER.c.member_id == _MEMBER.c.id).join(
                    _AGENT, _AGENT.c.member_id == _MEMBER.c.id
                )
            )
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
        """画布/任务提交后低延迟扫描；上游 done 解锁无需等 60s 周期。

        单表 EXISTS 短路（M6 review 效率）：本扫描挂在每条 message.created 的投递前沿上，
        无 writes_code 任务的纯聊天频道不必每条消息付 4 表 join + 逐候选画布推导。"""
        with self._engine.connect() as c:
            has_code_task = c.execute(
                select(_TASK.c.id)
                .where(
                    _TASK.c.channel_id == channel_id,
                    _TASK.c.writes_code.is_(True),
                    _TASK.c.project_id.is_not(None),
                    _TASK.c.status.notin_(worktree_service.TERMINAL_TASK_STATUSES),
                )
                .limit(1)
            ).first()
            if has_code_task is None:
                return
            plans = worktree_service.ensure_plans(c, channel_id=channel_id)
        for plan in plans:
            await self._ensure_worktree(plan.task_id)

    async def _ensure_worktree(self, task_id: str, *, revalidate: bool = False) -> bool:
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
                if existing is not None and existing[1] == "cleaned":
                    # cleaned 行视同无树（M6 review F3）：reopen 任务经 ensure 派生重建，
                    # daemon 幂等，apply_status 把行 upsert 回 active。
                    plans = worktree_service.ensure_plans(c, task_id=task_id)
                elif existing is not None:
                    already = bool(existing[0]) and existing[1] in {"active", "conflicted"}
                    if not revalidate or existing[1] != "active":
                        return already
                    # 复验：既有 active 行 → 用 revalidation_plans 重下发（ensure_plans 会因 task_id
                    # 非空排除该行，故须专用 plan 构造器）。
                    plans = worktree_service.revalidation_plans(c, task_id=task_id)
                else:
                    plans = worktree_service.ensure_plans(c, task_id=task_id)
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
                # 锁内单任务复核（TOCTOU 纪律保留）；task_id 过滤免 K+1 次全局推导。
                due = any(
                    item.task_id == task_id
                    for item in worktree_service.cleanup_plans(
                        c, computer_id=computer_id, task_id=task_id
                    )
                )
            if not due:
                return False
            conn = self._conns.get(computer_id)
            if conn is None:
                return False
            # 回收触发③cleanup 前置（B §13.1 / FR-11.1）：删 worktree 前先回收其上活跃预览
            # （dev server 子进程持有工作树目录句柄，win32 未回收 → cleanup rmtree 失败）。
            await self._recycle_task_preview(task_id)
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
            task = (
                tx.conn.execute(
                    select(
                        _TASK.c.owner_member_id,
                        _TASK.c.channel_id,
                        _TASK.c.workspace_id,
                        _TASK.c.title,
                    ).where(_TASK.c.id == task_id)
                )
                .mappings()
                .first()
            )
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
                        func.json_extract(_DIAG.c.payload, "$.result") == AckResult.FAILED.value,
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
                tx.conn.execute(select(_WORKTREE).where(_WORKTREE.c.task_id == task_id))
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
            if (
                tx.conn.execute(
                    select(_AGENT.c.member_id).where(_AGENT.c.member_id == owner)
                ).first()
                is None
            ):
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
        deliver_plans: list[
            tuple[str, str, list[dict[str, Any]], str | None, WakeReason | None]
        ] = []
        with gateway_tx(self._engine, self._bus) as tx:
            ensure_task_ids = [
                plan.task_id
                for plan in worktree_service.ensure_plans(tx.conn, computer_id=conn.computer_id)
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
                for plan in worktree_service.cleanup_plans(tx.conn, computer_id=conn.computer_id)
            ]
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
                channel_row = (
                    c.execute(select(_CHANNEL).where(_CHANNEL.c.id == channel_id)).mappings().one()
                )
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
        # 对账 #9 预览纠偏（契约 D §4.4 v1.0.5）：reconnect 握手以 hello 预览进程表快照逐会话对账
        # （存活者 survive WS jitter，真重启 fail-close，裁决 #11 不自动重拉）；周期只收 starting
        # 超时。
        await self._reconcile_previews(conn, on_reconnect=revalidate_worktrees)
        # 对账 #10 部署纠偏（契约 D §4.4；铁律 3）：reconnect 真重启时 running→fail-closed（副作用
        # 不可重放，不自动重跑）/ queued 安全重发；周期与同 nonce jitter 不动。
        await self._reconcile_deployments(conn, on_reconnect=revalidate_worktrees)

    def _backlog_trigger(
        self, c: Connection, backlog: list[dict[str, Any]], channel: dict[str, Any], agent_id: str
    ) -> WakeReason | None:
        for m in backlog:
            # DEDAG：画布 gating 退役；worktree path 未就绪仍**截断**（与 _filter_agent_delivery
            # 同语义）。两调用点均传已过滤 deliver，此为语义对齐防御。
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
            "[system → 仅你可见] 契约起草请求：请为下列任务起草契约，并用 coagentia 的 "
            "submit_task_contract 工具提交（不是打 REST）。\n"
            f"task_id={task_id}\nkind={kind.value}\n"
            "必填字段见该工具描述；字段不符会以 422 携逐字段清单退回，按清单补齐重投即可。"
        )
        data = MessageInjectData(
            agent_member_id=agent_member_id,
            body=body,
            source=InjectSource(kind=InjectKind.CONTRACT_DRAFT_REQUEST, ref=task_id),
            diagnostic_type="agent.tool_call",
        )
        ack = self._run_sync(self.send_instr(conn, agent_member_id, InstrType.MESSAGE_INJECT, data))
        return ack.result.value

    def agent_daemon_online(self, agent_member_id: str) -> bool:
        """快检 Agent 宿主 daemon 是否有活跃连接（只查 DB 行 + 进程内连接表，不发帧不等 ack）。

        decompose 预检用（CR-M8-1）：离线的常见路径在写事务开始前即 503，「不落库」语义保留；
        注入本体则挪到 tx.after_commit 提交后投递。"""
        try:
            self._require_conn_for_agent(agent_member_id)
        except DaemonOffline:
            return False
        return True

    def inject_guard_feedback(
        self, agent_member_id: str, body: str, *, ref: str | None = None
    ) -> str:
        """护栏反馈定向直投（G3/G4；契约 D §5.2 InjectKind.GUARD_FEEDBACK）。

        discard 告知「草稿已被丢弃」、reevaluate 告知「已触发重评估」共用（M4 护栏保留面；
        DEDAG 批 quality 域退役时误删本方法致 discard 直投 AttributeError，修复回归）。
        离线（无活跃 daemon 连接）→ DaemonOffline（REST 层收敛 503）；在线则 send_instr 同步
        等 ack，返回 ack.result。inject 只发帧、不动 read_positions（S1 语义），故与未提交
        REST 写事务无写锁死锁（裁决 9）。
        """
        conn, _agent = self._require_conn_for_agent(agent_member_id)
        data = MessageInjectData(
            agent_member_id=agent_member_id,
            body=body,
            source=InjectSource(kind=InjectKind.GUARD_FEEDBACK, ref=ref),
            diagnostic_type="agent.tool_call",
        )
        ack = self._run_sync(self.send_instr(conn, agent_member_id, InstrType.MESSAGE_INJECT, data))
        return ack.result.value

    def inject_onboarding_greeting(self, agent_member_id: str, *, ref: str | None = None) -> str:
        """L11 新 Agent 入职问候一次性直投（PRD FR-1.4；InjectKind.SYSTEM）。

        离线（无活跃 daemon 连接）→ DaemonOffline（调用方 best-effort 吞——问候是锦上添花，
        不阻断上线）。调用纪律：经 tx.after_commit 提交后调用、不跨持锁
        事务（真 claude 适配器回 ack 前先写 agent.status，跨未提交写锁会自死锁）。幂等由调用方
        的 diagnostic 标记保证（本方法只管发帧）。"""
        conn, _agent = self._require_conn_for_agent(agent_member_id)
        body = (
            "[system → 仅你可见] 欢迎加入这个工作区！请到 #all 频道用工作区主导语言"
            "（观察 #all 已有消息判断该用哪种语言）发一条简短的自我介绍问候，并阅读 #all 的历史"
            "消息了解当前协作上下文以便自我融入。这是一次性的入职提示，无需重复。"
        )
        data = MessageInjectData(
            agent_member_id=agent_member_id,
            body=body,
            source=InjectSource(kind=InjectKind.SYSTEM, ref=ref),
            diagnostic_type="agent.tool_call",
        )
        ack = self._run_sync(self.send_instr(conn, agent_member_id, InstrType.MESSAGE_INJECT, data))
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
            held = c.execute(select(_HELD).where(_HELD.c.id == held_id)).mappings().first()
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
                self._force_start_deliver(conn, owner_member_id, channel_id, task_id=task_id)
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
            if not await self._ensure_worktree(task_id):
                return
            await self._force_start_deliver(conn, agent_id, channel_id, task_id=task_id)
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
        """force-start 直投（人工「本次」放行/nudge）：wake + deliver 一次。

        **不带 deliver_meta**：本方法经 _run_sync 在 force-start 路由的写事务尚未提交时同步调用；
        若 deliver ack 回写 read_positions 会与该未提交写事务争 SQLite 写锁（busy_timeout 内阻塞
        事件循环 → 死锁）。故 override 投递不推进 read_position——本条消息仍留积压，解除阻塞后正常
        投递再推进（at-least-once + daemon 频道去重覆盖重叠）。积压读的是已提交态，未含 route 尚未
        提交的锚点系统消息，符合 override 语义。DEDAG：node_id ref 随画布退役（恒 None）。
        """
        del task_id
        with self._engine.connect() as c:
            backlog = self._backlog(c, agent_id, channel_id)
            backlog = worktree_service.inject_directory_context(
                c, agent_member_id=agent_id, messages=backlog
            )
        await self.send_instr(
            conn,
            agent_id,
            InstrType.AGENT_WAKE,
            AgentWakeData(
                agent_member_id=agent_id,
                reason=WakeReason.CANVAS_ACTIVATION,
                refs=WakeRefs(message_ids=[m["id"] for m in backlog], node_id=None),
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

    def query_git_diff(self, *, computer_id: str, query: GitDiffQuery) -> dict[str, Any]:
        conn = self._conns.get(computer_id)
        if conn is None:
            raise DaemonOffline("daemon 离线")
        reply = self._run_sync(self.send_query(conn, QueryType.GIT_DIFF, query))
        error = reply.get("error")
        if isinstance(error, str):
            # daemon 回帧（在线）只是 git 查询失败（坏 base ref 等）→ 4xx，非 503（#5）。
            raise GitQueryError(error)
        return reply

    # -------------------------------------------------------- PS-WT 只读代理 + 清理（B §4.11 扩）

    def query_fs_tree(self, *, computer_id: str, query: FsTreeQuery) -> dict[str, Any]:
        """computer 级目录浏览只读代理（PS-WT；契约 D §6 FS_TREE）。仿 query_git_diff：无连接/
        超时 → DaemonOffline（REST 收敛 503）。单条目权限/IO 异常在 daemon 侧逐条 denied/跳过，
        不似 git.diff 存在「在线但查询失败」的 4xx 语义，故此处不引 GitQueryError。"""
        conn = self._conns.get(computer_id)
        if conn is None:
            raise DaemonOffline("daemon 离线")
        return self._run_sync(self.send_query(conn, QueryType.FS_TREE, query))

    def scan_worktrees(
        self, computer_ids: Iterable[str]
    ) -> dict[str, tuple[str, list[dict[str, Any]] | None]]:
        """管理台 live=1 多机并发扫描 worktrees_dir（PS-WT；契约 D §6 WORKTREE_SCAN）。

        逐机独立降级，一台掉线/超时不拖垮全部（各自 gather 结果）：无活跃连接 → ('offline', None)；
        查询超时或连接失效 → ('timeout', None)；成功 → ('ok', entries)。并发发帧故总耗时≈单机
        查询超时，不随机器数线性放大。"""
        return self._run_sync(self._scan_worktrees_multi(list(computer_ids)))

    async def _scan_worktrees_multi(
        self, computer_ids: list[str]
    ) -> dict[str, tuple[str, list[dict[str, Any]] | None]]:
        async def _one(cid: str) -> tuple[str, tuple[str, list[dict[str, Any]] | None]]:
            conn = self._conns.get(cid)
            if conn is None:
                return cid, ("offline", None)
            try:
                reply = await self.send_query(conn, QueryType.WORKTREE_SCAN, WorktreeScanQuery())
            except DaemonOffline:
                return cid, ("timeout", None)
            entries = reply.get("entries") if isinstance(reply, dict) else None
            return cid, ("ok", entries if isinstance(entries, list) else [])

        results = await asyncio.gather(*(_one(cid) for cid in computer_ids))
        return dict(results)

    def dispatch_worktree_cleanup(
        self, *, computer_id: str, task_id: str, project_id: str | None = None
    ) -> str:
        """管理台清理端点同步下发 WORKTREE_CLEANUP 并等 daemon ack（三段式中段：调用方此刻**不得
        持写事务**，仅读门校验后下发；CR-M8 同族「跨进程同步等待不得跨持锁事务」）。

        无连接/超时 → DaemonOffline（REST 收敛 503，DB 未动无幽灵态）。返回 ack.result：
        'done'/'noop'（目录已不存在=幂等成功，登记态照常推进）/'failed'（win32 文件锁等，调用方
        据此不推进登记）。project_id 供孤儿清理（DB 无登记行 → daemon 自拼 worktrees_dir 内路径）；
        常规登记清理传 None 走 daemon 既有 task_id 反查（契约 D WorktreeCleanupData 语义）。"""
        conn = self._conns.get(computer_id)
        if conn is None:
            raise DaemonOffline("daemon 离线")
        ack = self._run_sync(
            self.send_instr(
                conn,
                f"worktree:{task_id}",
                InstrType.WORKTREE_CLEANUP,
                WorktreeCleanupData(task_id=task_id, project_id=project_id),
            )
        )
        if ack is None:
            raise DaemonOffline("worktree cleanup 未获 ack")
        return ack.result.value

    def finalize_console_cleanup(self, *, task_id: str, computer_id: str) -> dict[str, Any] | None:
        """管理台登记清理成功后条件 UPDATE + 广播（三段式③；另开 gateway_tx，不复用请求读快照，
        避免 pysqlite 读快照与并发写的非串行化冲突）。

        幂等收敛（对齐 _converge_worktree_cleaned）：worktree.status 是异步上报（L4a），daemon 报
        cleaned 与本 CAS 谁先到不定序——行已 cleaned（上报先到或并发已清）→ 不重复广播直接返回
        当前行；行 merged/conflicted → CAS（WHERE status IN merged/conflicted）收敛 cleaned +
        emit worktree.updated；行不存在 → None（调用方 404）。"""
        with gateway_tx(self._engine, self._bus) as tx:
            row = (
                tx.conn.execute(select(_WORKTREE).where(_WORKTREE.c.task_id == task_id))
                .mappings()
                .first()
            )
            if row is None:
                return None
            if row["status"] == WorktreeStatus.CLEANED.value:
                return dict(row)  # 幂等：已清（上报/并发），不重复广播
            ts = now_iso()
            tx.conn.execute(
                update(_WORKTREE)
                .where(
                    _WORKTREE.c.task_id == task_id,
                    _WORKTREE.c.status.in_(
                        [WorktreeStatus.MERGED.value, WorktreeStatus.CONFLICTED.value]
                    ),
                )
                .values(status=WorktreeStatus.CLEANED, cleaned_at=row["cleaned_at"] or ts)
            )
            new_row = dict(
                tx.conn.execute(select(_WORKTREE).where(_WORKTREE.c.task_id == task_id))
                .mappings()
                .one()
            )
            channel_id = tx.conn.execute(
                select(_TASK.c.channel_id).where(_TASK.c.id == task_id)
            ).scalar()
            tx.emit(
                EventType.WORKTREE_UPDATED,
                channel_id,
                {"worktree": worktree_public(new_row)},
            )
            return new_row

    # ---------------------------------------------------------------- 预览下发桥（M7 K3；B §13.1）

    def preview_daemon_online(self, computer_id: str) -> bool:
        """预览 POST 的 503 门（B §13.1）：目标 Computer 无活跃 daemon 连接 → 离线。"""
        return self._conns.get(computer_id) is not None

    def request_preview_start(
        self, *, computer_id: str, task_id: str, data: PreviewStartData
    ) -> None:
        """REST POST /preview 下发 preview.start（裁决 8 ensure）：由路由的 `tx.after_commit`
        在 **starting 行提交后**调用（deps.get_tx 提交后按序执行 after_commit 回调）——本方法只做
        线程→事件循环的下发调度，不 `_run_sync` 阻塞等 ack。

        提交后调用的硬保证：starting 行已落库 → daemon 起进程健康检查（≥0.5s）后的 running 帧其
        CAS（WHERE status='starting'）必命中已提交行，杜绝「running 先于建行提交」丢帧窗口
        （DEV-PLAN §2 CAS 纪律）。503 门已由路由前置 preview_daemon_online 把守；下发瞬时 daemon
        离线则 best-effort 静默（行留 starting，对账 #9 starting 超时兜底 fail-closed）。"""
        loop = self._loop
        if loop is None:
            return
        loop.call_soon_threadsafe(
            self._spawn,
            self._dispatch_preview_instr(computer_id, task_id, InstrType.PREVIEW_START, data),
        )

    def request_preview_stop(
        self, *, computer_id: str, task_id: str, preview_session_id: str
    ) -> None:
        """REST DELETE /preview 下发 preview.stop（回收）：同 start 提交后异步下发；离线 best-effort
        （recycled 由 daemon 上报确认，离线则待重连对账 #9 / idle 兜底）。"""
        loop = self._loop
        if loop is None:
            return
        loop.call_soon_threadsafe(
            self._spawn,
            self._dispatch_preview_instr(
                computer_id,
                task_id,
                InstrType.PREVIEW_STOP,
                PreviewStopData(preview_session_id=preview_session_id),
            ),
        )

    async def _dispatch_preview_instr(
        self, computer_id: str, task_id: str, itype: InstrType, data: Any
    ) -> None:
        """preview.start/stop 底座下发（同任务串行锁 `preview:{task_id}`）：离线/断连 best-effort
        静默（判定归 server，daemon 只执行；失败面由对账兜底，不发明事件）。"""
        conn = self._conns.get(computer_id)
        if conn is None:
            return
        with contextlib.suppress(DaemonOffline):
            await self.send_instr(conn, f"preview:{task_id}", itype, data)

    # ---------------------------------------------------------------- 部署下发桥（M7b K4）

    # ------------------------------------------------------- 任务级 merge（DEDAG B v1.6 §14）

    def merge_running_for_project(self, project_id: str) -> bool:
        """同 Project 是否已有进行中的任务级合并（409 串行判定，沿 deploy 先例）。"""
        with self._pending_lock:
            return any(p[1] == project_id for p in self._merge_pending.values())

    def request_task_merge(self, plan: merge_domain.TaskMergePlan) -> None:
        """merge 端点 tx.after_commit 提交后调用（铁律 4）：登记 pending + 异步下发。"""
        loop = self._loop
        if loop is None:
            return
        with self._pending_lock:
            self._merge_pending[plan.task_id] = (plan.computer_id, plan.project_id)
        loop.call_soon_threadsafe(self._spawn, self._dispatch_task_merge(plan))

    async def _dispatch_task_merge(self, plan: merge_domain.TaskMergePlan) -> None:
        """下发 WORKTREE_MERGE 指令（lock_key=project 串行，同系统节点时代口径）。

        离线/ack 失败 → fail_merge 留痕 + 清 pending（人工重触发即 retry）。"""
        conn = self._conns.get(plan.computer_id)
        if conn is None:
            self._fail_task_merge(plan.task_id, "daemon 离线，合并未下发")
            return
        try:
            ack = await self.send_instr(
                conn,
                f"project:{plan.project_id}",
                InstrType.WORKTREE_MERGE,
                plan.data,
            )
        except DaemonOffline:
            self._fail_task_merge(plan.task_id, "daemon 离线，合并未下发")
            return
        if ack is not None and ack.result == AckResult.FAILED:
            error = ack.error.model_dump(mode="json") if ack.error is not None else None
            self._fail_task_merge(plan.task_id, f"daemon 指令失败：{error}")

    def _fail_task_merge(self, task_id: str, reason: str) -> None:
        with gateway_tx(self._engine, self._bus) as tx:
            merge_domain.fail_merge(tx, task_id=task_id, reason=reason)
        with self._pending_lock:
            self._merge_pending.pop(task_id, None)

    def request_deploy_run(self, *, computer_id: str, data: DeployRunData) -> None:
        """REST POST /deployments 下发 deploy.run：由路由的 tx.after_commit 在 **queued 行提交后**
        调用（铁律 4）——daemon 起进程后的 running/finished 帧其 CAS 必命中已提交行。503 门已由路由
        前置 preview_daemon_online 把守；下发瞬时 daemon 离线则 best-effort 静默（行留 queued，对账
        #10 reconnect 安全重发）。"""
        loop = self._loop
        if loop is None:
            return
        loop.call_soon_threadsafe(self._spawn, self._dispatch_deploy_instr(computer_id, data))

    async def _dispatch_deploy_instr(self, computer_id: str, data: DeployRunData) -> None:
        """deploy.run 底座下发（同部署串行锁 `deploy:{deployment_id}`）：离线/断连 best-effort 静默
        （判定归 server，daemon 只执行；queued 未 ack 由对账 #10 安全重发）。"""
        conn = self._conns.get(computer_id)
        if conn is None:
            return
        with contextlib.suppress(DaemonOffline):
            await self.send_instr(conn, f"deploy:{data.deployment_id}", InstrType.DEPLOY_RUN, data)

    async def _reconcile_deployments(self, conn: DaemonConnection, *, on_reconnect: bool) -> None:
        """对账 #10 部署纠偏（契约 D §4.4；铁律 3 副作用可重放性分野）：**只在 reconnect 且真重启
        （boot_nonce 变化/缺失）时处置**。

        - 周期（on_reconnect=False）：不动（同进程帧可达，deploy.run 仍在跑或终态帧可达）。
        - reconnect 但同 nonce（WS jitter）：不动（daemon 进程还在，命令仍在跑）。
        - reconnect 且真重启（daemon_restarted）：
          ① 本机 project 的 running 部署 → **fail-closed**（命令跑了一半 daemon 死了，外部副作用
             不可重放）：条件 UPDATE running→failed(exit_code=NULL) + 广播 deployment.updated +
             结果卡 @触发者「请人工核实」+ 诊断。**不重跑**。
          ② 本机 project 的 queued 部署（未 ack 未开跑）→ **安全重发** deploy.run（daemon 已在跑/
             终态则 noop + 重发终态；未开跑则正常起跑）。
        """
        if not on_reconnect or not conn.daemon_restarted:
            return
        # 快路径：本机无活跃部署（绝大多数握手的常态）→ 免开 gateway_tx（省事件 flush 机器 +
        # 调度扰动）。轻量 connect 只读探一行，无则直接返回。
        with self._engine.connect() as probe:
            has_active = probe.execute(
                select(_DEPLOYMENT.c.id)
                .select_from(_DEPLOYMENT.join(_PROJECT, _PROJECT.c.id == _DEPLOYMENT.c.project_id))
                .where(
                    _PROJECT.c.computer_id == conn.computer_id,
                    _DEPLOYMENT.c.status.in_(_DEPLOYMENT_ACTIVE),
                )
                .limit(1)
            ).first()
        if has_active is None:
            return
        redispatch: list[DeployRunData] = []
        failed_ids: list[str] = []
        with gateway_tx(self._engine, self._bus) as tx:
            rows = (
                tx.conn.execute(
                    select(
                        _DEPLOYMENT.c.id,
                        _DEPLOYMENT.c.status,
                        _DEPLOYMENT.c.branch,
                        _DEPLOYMENT.c.commit_hash,
                        _DEPLOYMENT.c.command,
                        _PROJECT.c.repo_path,
                    )
                    .select_from(
                        _DEPLOYMENT.join(_PROJECT, _PROJECT.c.id == _DEPLOYMENT.c.project_id)
                    )
                    .where(
                        _PROJECT.c.computer_id == conn.computer_id,
                        _DEPLOYMENT.c.status.in_(_DEPLOYMENT_ACTIVE),
                    )
                )
                .mappings()
                .all()
            )
            for row in rows:
                if row["status"] == "running":
                    res = tx.conn.execute(
                        update(_DEPLOYMENT)
                        .where(
                            _DEPLOYMENT.c.id == row["id"],
                            _DEPLOYMENT.c.status == "running",
                        )
                        .values(status="failed", exit_code=None, finished_at=now_iso())
                    )
                    if res.rowcount:
                        failed_ids.append(row["id"])
                else:  # queued：未 ack 未开跑 → 安全重发（副作用未发生）
                    redispatch.append(
                        DeployRunData(
                            deployment_id=row["id"],
                            repo_path=row["repo_path"],
                            command=row["command"],
                            branch=row["branch"],
                            commit_hash=row["commit_hash"],
                        )
                    )
            for dep_id in failed_ids:
                frow = dict(
                    tx.conn.execute(select(_DEPLOYMENT).where(_DEPLOYMENT.c.id == dep_id))
                    .mappings()
                    .one()
                )
                tx.emit(
                    EventType.DEPLOYMENT_UPDATED,
                    None,
                    {"deployment": deployment_public(frow)},
                )
                self._deploy_log_seq.pop(dep_id, None)
                self._delete_deploy_log_seq(dep_id)  # R-10：fail-close 后同清 sidecar
                self._post_deployment_cards(tx, frow, body=_DEPLOY_RESTART_CARD_BODY)
                self._append_deploy_diagnostic(tx, frow)
        # 事务外下发重发指令（daemon 幂等：已在跑/终态 → noop + 重发终态）。
        for data in redispatch:
            await self._dispatch_deploy_instr(conn.computer_id, data)

    async def _recycle_task_preview(self, task_id: str) -> None:
        """回收单任务活跃预览（触发②任务终态 / 触发③cleanup 前置共用）：下发 preview.stop。

        判定归 server（此处判「该任务有活跃预览」），执行归 daemon（recycled 经 preview.status 上报
        确认，行留存供诊断——不在此改态）。"""
        with self._engine.connect() as c:
            row = (
                c.execute(
                    select(_PREVIEW.c.id, _PROJECT.c.computer_id)
                    .select_from(
                        _PREVIEW.join(_WORKTREE, _WORKTREE.c.id == _PREVIEW.c.worktree_id).join(
                            _PROJECT, _PROJECT.c.id == _WORKTREE.c.project_id
                        )
                    )
                    .where(
                        _PREVIEW.c.task_id == task_id,
                        _PREVIEW.c.status.in_(_PREVIEW_ACTIVE),
                    )
                    .order_by(_PREVIEW.c.started_at.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
        if row is None:
            return
        await self._dispatch_preview_instr(
            row["computer_id"],
            task_id,
            InstrType.PREVIEW_STOP,
            PreviewStopData(preview_session_id=row["id"]),
        )

    async def _run_preview_recycle_scan(self) -> None:
        """回收触发①idle（B §13.1 / FR-11.1）：活跃预览 last_active_at 超 projects.preview_idle_min
        → 下发 preview.stop。判定（阈值）归 server，daemon 执行。逐项阈值随 Project 不同，故 Python
        侧按行判（活跃预览数有界）。"""
        now = datetime.now(UTC)
        with self._engine.connect() as c:
            rows = (
                c.execute(
                    select(
                        _PREVIEW.c.id,
                        _PREVIEW.c.task_id,
                        _PREVIEW.c.started_at,
                        _PREVIEW.c.last_active_at,
                        _PROJECT.c.computer_id,
                        _PROJECT.c.preview_idle_min,
                    )
                    .select_from(
                        _PREVIEW.join(_WORKTREE, _WORKTREE.c.id == _PREVIEW.c.worktree_id).join(
                            _PROJECT, _PROJECT.c.id == _WORKTREE.c.project_id
                        )
                    )
                    .where(_PREVIEW.c.status.in_(_PREVIEW_ACTIVE))
                )
                .mappings()
                .all()
            )
        due: list[tuple[str, str, str]] = []
        for row in rows:
            # 仅 NULL 用默认 30；显式 0 立即回收，`or 30` 会把 0 误当 30（code-review 修）。
            configured = row["preview_idle_min"]
            idle_min = configured if configured is not None else 30
            anchor = row["last_active_at"] or row["started_at"]
            if (now - datetime.fromisoformat(anchor)).total_seconds() > idle_min * 60:
                due.append((row["computer_id"], row["task_id"], row["id"]))
        for computer_id, task_id, preview_id in due:
            await self._dispatch_preview_instr(
                computer_id,
                task_id,
                InstrType.PREVIEW_STOP,
                PreviewStopData(preview_session_id=preview_id),
            )

    async def _reconcile_previews(self, conn: DaemonConnection, *, on_reconnect: bool) -> None:
        """对账 #9 预览纠偏（契约 D §4.4 v1.0.5；裁决 #11）：**从 DB 事实 × hello 预览进程表推导**，
        不自动重拉。

        - 周期（on_reconnect=False）：仅 starting 超时（连接仍在但迟迟未收 preview.status）置
          failed；running 行由 daemon 存活监控上报 failed，周期不动（连接在=帧可达）。
        - reconnect 握手（on_reconnect=True）：以 hello 预览进程表快照逐会话对账——
          ① **快照重放**：终态/running 条目走 preview.status 同一 CAS 落库（恢复断连期 best-effort
            丢失的上报：starting 行被 running 条目推进携 port、failed/recycled 补终态）；已一致则
            rowcount=0 幂等 noop——**存活预览 survive WS jitter，不再全量 fail-close**；
          ② 快照无条目的活跃行 fail-close：boot_nonce 变化/缺失（真重启，子进程必死/成孤儿，
            K2-cal §3.4）→ 'daemon restarted'；nonce 未变（start 指令断连期丢失）→
            'preview process lost'。等人再点 POST ensure 重建（不引入无人观察的常驻进程）；
          ③ **反向泄漏防护**：快照中活跃（starting/running）但 DB 行已非活跃（断连期 server 已
            fail-close/回收，如 starting 超时）→ 下发 preview.stop 杀进程+释放端口；
          ④ 回收触发②补扫：存活行的任务已终态（断连期 stop 下发丢失）→ 下发 preview.stop。

        状态机边写一律条件 UPDATE（起态门 CAS）：竞败/已被 preview.status 推进则 rowcount=0 不覆盖。
        """
        if not on_reconnect:
            self._fail_previews_starting_timeout(conn.computer_id)
            return
        # ① 快照重放（复用 preview.status 处置：CAS + 广播 + 诊断；starting 条目无事实可推进）。
        for entry in conn.hello_previews.values():
            if entry.status != "starting":
                self._report_preview_status(conn, entry)
        reason = (
            _PREVIEW_FAIL_DAEMON_RESTARTED if conn.daemon_restarted else _PREVIEW_FAIL_PROCESS_LOST
        )
        stop_targets: list[tuple[str, str]] = []  # (task_id 串行锁键, preview_session_id)
        with gateway_tx(self._engine, self._bus) as tx:
            rows = (
                tx.conn.execute(
                    select(_PREVIEW.c.id, _PREVIEW.c.task_id, _TASK.c.status.label("task_status"))
                    .select_from(
                        _PREVIEW.join(_WORKTREE, _WORKTREE.c.id == _PREVIEW.c.worktree_id)
                        .join(_PROJECT, _PROJECT.c.id == _WORKTREE.c.project_id)
                        .join(_TASK, _TASK.c.id == _PREVIEW.c.task_id)
                    )
                    .where(
                        _PROJECT.c.computer_id == conn.computer_id,
                        _PREVIEW.c.status.in_(_PREVIEW_ACTIVE),
                    )
                )
                .mappings()
                .all()
            )
            failed_ids: list[str] = []
            for row in rows:
                entry = conn.hello_previews.get(row["id"])
                if entry is None or entry.status not in ("starting", "running"):
                    # ② 无活条目（终态条目在 ① 已推进，此处兜防御）：fail-close。
                    res = tx.conn.execute(
                        update(_PREVIEW)
                        .where(
                            _PREVIEW.c.id == row["id"],
                            _PREVIEW.c.status.in_(_PREVIEW_ACTIVE),
                        )
                        .values(status="failed", fail_log_tail=reason)
                    )
                    if res.rowcount:
                        failed_ids.append(row["id"])
                elif row["task_status"] in (TaskStatus.DONE.value, TaskStatus.CLOSED.value):
                    stop_targets.append((row["task_id"], row["id"]))  # ④ 终态补回收
            for preview_id in failed_ids:
                # 广播 preview.updated + 落 preview.failed 诊断（FR-11.3；对账置 failed 同口径）。
                self._emit_preview_updated(tx, preview_id, diag_type=_PREVIEW_DIAG_FAILED)
            # ③ 反向泄漏防护：快照活跃条目，其行不在本机活跃集（已 failed/recycled/不存在）。
            active_ids = {row["id"] for row in rows}
            for entry in conn.hello_previews.values():
                if (
                    entry.status not in ("starting", "running")
                    or entry.preview_session_id in active_ids
                ):
                    continue
                orphan = tx.conn.execute(
                    select(_PREVIEW.c.task_id).where(_PREVIEW.c.id == entry.preview_session_id)
                ).first()
                lock_key = orphan[0] if orphan is not None else entry.preview_session_id
                stop_targets.append((lock_key, entry.preview_session_id))
        # 事务外下发（daemon 幂等：已停/未知 → noop；recycled 上报对非活跃行 CAS noop 不覆盖终态）。
        for task_key, preview_id in stop_targets:
            await self._dispatch_preview_instr(
                conn.computer_id,
                task_key,
                InstrType.PREVIEW_STOP,
                PreviewStopData(preview_session_id=preview_id),
            )

    def _fail_previews_starting_timeout(self, computer_id: str) -> None:
        """对账 #9 周期分支：starting 超 preview_starting_timeout_sec 未收 preview.status →
        failed。"""
        threshold_iso = format_iso(
            datetime.now(UTC) - timedelta(seconds=self.preview_starting_timeout_sec)
        )
        with gateway_tx(self._engine, self._bus) as tx:
            rows = (
                tx.conn.execute(
                    select(_PREVIEW.c.id)
                    .select_from(
                        _PREVIEW.join(_WORKTREE, _WORKTREE.c.id == _PREVIEW.c.worktree_id).join(
                            _PROJECT, _PROJECT.c.id == _WORKTREE.c.project_id
                        )
                    )
                    .where(
                        _PROJECT.c.computer_id == computer_id,
                        _PREVIEW.c.status == "starting",
                        _PREVIEW.c.started_at < threshold_iso,
                    )
                )
                .mappings()
                .all()
            )
            failed_ids: list[str] = []
            for row in rows:
                res = tx.conn.execute(
                    update(_PREVIEW)
                    .where(_PREVIEW.c.id == row["id"], _PREVIEW.c.status == "starting")
                    .values(status="failed", fail_log_tail=_PREVIEW_FAIL_STARTING_TIMEOUT)
                )
                if res.rowcount:
                    failed_ids.append(row["id"])
            for preview_id in failed_ids:
                self._emit_preview_updated(tx, preview_id, diag_type=_PREVIEW_DIAG_FAILED)

    def _require_conn_for_agent(self, agent_id: str) -> tuple[DaemonConnection, dict[str, Any]]:
        with self._engine.connect() as c:
            agent = (
                c.execute(
                    select(_AGENT, _MEMBER.c.name)
                    .select_from(_AGENT.join(_MEMBER, _AGENT.c.member_id == _MEMBER.c.id))
                    .where(_AGENT.c.member_id == agent_id)
                )
                .mappings()
                .first()
            )
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
        card_kind: str | None = None,
        card_ref: str | None = None,
    ) -> str:
        """插一条 durable 系统消息（author=NULL, kind=SYSTEM）+ 可选 @mention 行 + emit。

        reminder 触发 / 沉默提醒 / 沉默升级 / 部署结果卡四处共用，避免 insert(_MSG)+mention+回读+
        emit 骨架多份漂移（§8.2：系统消息 + mention 对目标 Agent 视同唤醒触发）。card_kind/card_ref
        非空则落卡片列（部署结果卡 card_kind='deployment'）。返回 msg_id。
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
                card_kind=card_kind,
                card_ref=card_ref,
                body=body,
                created_at=ts,
            )
        )
        for member_id in mention_member_ids:
            tx.conn.execute(insert(_MENTION).values(message_id=msg_id, member_id=member_id))
        msg_row = models.row_dict(
            tx.conn.execute(select(_MSG).where(_MSG.c.id == msg_id)).mappings().first()
        )
        tx.emit(EventType.MESSAGE_CREATED, channel_id, {"message": message_public(msg_row)})
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
            due = (
                tx.conn.execute(
                    select(_REMINDER).where(
                        _REMINDER.c.status == "active", _REMINDER.c.next_fire_at <= now
                    )
                )
                .mappings()
                .all()
            )
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
            due = (
                tx.conn.execute(
                    select(_HELD).where(
                        _HELD.c.status == HeldDraftStatus.HELD.value,
                        _HELD.c.next_reeval_at <= now,
                        _HELD.c.escalated_at.is_(None),  # 升级后停自动（裁决 6）
                    )
                )
                .mappings()
                .all()
            )
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
                    tx.conn.execute(select(_HELD).where(_HELD.c.id == held["id"]))
                    .mappings()
                    .first()
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
            channels = (
                {
                    c["id"]: dict(c)
                    for c in tx.conn.execute(
                        select(_CHANNEL).where(_CHANNEL.c.id.in_(channel_ids))
                    ).mappings()
                }
                if channel_ids
                else {}
            )
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
        ev = (
            c.execute(
                select(
                    func.max(
                        case(
                            (
                                _TASK_EVENT.c.kind.notin_(silence_logic.SELF_EXCITE_EVENT_KINDS),
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
            )
            .mappings()
            .first()
        )
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

    def _reminder_targets(self, c: Connection, task: dict[str, Any]) -> list[dict[str, Any]]:
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

    def _channel_human_members(self, c: Connection, channel_id: str) -> list[dict[str, Any]]:
        rows = c.execute(
            select(_MEMBER.c.id, _MEMBER.c.name, _MEMBER.c.kind)
            .select_from(_CHANNEL_MEMBER.join(_MEMBER, _CHANNEL_MEMBER.c.member_id == _MEMBER.c.id))
            .where(
                _CHANNEL_MEMBER.c.channel_id == channel_id,
                _MEMBER.c.kind == MemberKind.HUMAN,
                _MEMBER.c.removed_at.is_(None),
            )
        ).mappings()
        return [dict(r) for r in rows]

    def _emit_silence_reminder(self, tx: Any, task: dict[str, Any], threshold_h: int) -> None:
        """第一次提醒：锚点线程系统消息（@目标）+ message_mentions（@Agent 触发唤醒）+
        task_events(reminder_sent)。mention 行是唤醒事实源（_compute_trigger 视 system+mention
        为 REMINDER），故所有目标（含人类，用于渲染）统一插行。"""
        targets = self._reminder_targets(tx.conn, task)
        mention_txt = " ".join(f"@{t['name']}" for t in targets)
        suffix = f"：{mention_txt}" if mention_txt else "。"
        body = f"沉默提醒：任务「{task['title']}」已超过 {threshold_h} 小时无进展，请跟进{suffix}"
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
        body = f"沉默升级：任务「{task['title']}」经提醒后仍无进展，需人类成员处理{suffix}"
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

    async def _preview_recycle_loop(self) -> None:
        """回收触发①idle 周期扫描（M7 K3；挂同一调度器心智，勿另起独立调度器）：按
        preview_recycle_interval 扫活跃预览 last_active_at 超 idle_min → 下发 preview.stop。"""
        while True:
            await asyncio.sleep(self.preview_recycle_interval)
            with contextlib.suppress(Exception):
                await self._run_preview_recycle_scan()

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
                    tx.conn.execute(select(_COMPUTER).where(_COMPUTER.c.id == conn.computer_id))
                    .mappings()
                    .first()
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
