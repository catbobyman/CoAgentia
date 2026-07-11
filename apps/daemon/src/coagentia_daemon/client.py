"""DaemonClient：契约 D daemon 侧本体（连接生命周期 / 握手 / 指令幂等消费 / 上报 / 缓冲重传）。

一条连接的生命：connect → hello → hello_ack → 并发跑 {reader, heartbeat, flush} → 断连 →
指数退避重连（1s→2s→…→30s 封顶，无限重试）。指令按自然键幂等（委托 adapter），frame_id 短窗
去重为加速器；需 ack 类遥测经 TelemetryBuffer 落盘、断连后重传（ULID 不虚增）。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from coagentia_contracts.daemon import (
    DAEMON_PROTOCOL_V,
    AckResult,
    AgentStatusChangedData,
    DaemonAgentActivityData,
    DaemonHelloAckData,
    DaemonHelloData,
    DiagnosticEventIn,
    FrameKind,
    HomeFileBinaryReply,
    HomeFileQuery,
    HomeFileTextReply,
    HomeTreeEntry,
    HomeTreeQuery,
    HomeTreeReply,
    InstrType,
    QueryType,
    ReportFrame,
    ReportType,
    RuntimesDetectedData,
    TokenUsageEventIn,
    WorktreeStatusData,
)
from coagentia_contracts.entities import DetectedRuntime
from coagentia_contracts.enums import AgentStatus

from coagentia_daemon import __version__
from coagentia_daemon.adapter import RuntimeAdapter
from coagentia_daemon.buffer import TelemetryBuffer
from coagentia_daemon.git import GitWorktreeManager
from coagentia_daemon.handlers import HANDLERS
from coagentia_daemon.paths import DataPaths
from coagentia_daemon.probe import CommandRunner, probe_runtimes
from coagentia_daemon.transport import Transport, TransportClosed, websockets_connect
from coagentia_daemon.util import new_ulid, now_iso

BACKOFF_START = 1.0
BACKOFF_CAP = 30.0
_DIAG_BATCH = 50  # 契约 D §7：diagnostics ≤50 条/批
_USAGE_BATCH = 500
_HOME_FILE_MAX = 1024 * 1024  # 契约 D §6：home.file 文本上限 1MB
_ACTIVITY_PRETHROTTLE = 0.25  # 契约 D §7：daemon 可 ≥250ms 预节流省带宽
_FRAME_DEDUP_WINDOW = 2048
_STATUS_REPLAY_INSTRS = frozenset(
    {
        InstrType.WORKTREE_ENSURE,
        InstrType.WORKTREE_MERGE,
        InstrType.WORKTREE_CLEANUP,
    }
)

ConnectFn = Callable[[str, str], "Any"]  # (server_url, api_key) -> Awaitable[Transport]


def next_backoff(current: float, cap: float = BACKOFF_CAP) -> float:
    """指数退避下一值（契约 D §2：1→2→4→…→30 封顶）。"""
    return min(current * 2, cap)


class DaemonClient:
    def __init__(
        self,
        *,
        server_url: str,
        api_key: str,
        adapter: RuntimeAdapter,
        buffer: TelemetryBuffer,
        paths: DataPaths,
        os_name: str,
        arch: str,
        daemon_version: str = __version__,
        connect_fn: ConnectFn | None = None,
        runner: CommandRunner | None = None,
        heartbeat_sec: float = 25.0,
        pong_timeout: float = 10.0,
        ack_timeout: float = 10.0,
        backoff_start: float = BACKOFF_START,
        backoff_cap: float = BACKOFF_CAP,
    ) -> None:
        self.server_url = server_url
        self.api_key = api_key
        self.adapter = adapter
        self.buffer = buffer
        self.paths = paths
        self.os_name = os_name
        self.arch = arch
        self.daemon_version = daemon_version
        self._connect_fn = connect_fn or websockets_connect
        self._runner = runner
        self.heartbeat_sec = heartbeat_sec
        self.pong_timeout = pong_timeout
        self.ack_timeout = ack_timeout
        self._backoff_start = backoff_start
        self._backoff_cap = backoff_cap

        self.adapter.bind(self)  # AdapterSink = self
        self.git = GitWorktreeManager(paths)

        self._transport: Transport | None = None
        self._send_lock = asyncio.Lock()
        self._detected_runtimes: list[DetectedRuntime] = []
        self.hello_ack: DaemonHelloAckData | None = None

        self.connected = asyncio.Event()
        self._pong_event = asyncio.Event()
        self._flush_event = asyncio.Event()
        self._report_acks: dict[str, asyncio.Future] = {}
        self._recent_frames: deque[str] = deque(maxlen=_FRAME_DEDUP_WINDOW)
        self._recent_frame_set: set[str] = set()
        self._activity_last: dict[str, float] = {}
        self._stopped = False
        self._was_connected = False

    # ---------------------------------------------------------------- 主循环（重连）

    async def run(self) -> None:
        """无限重连主循环（契约 D §2：无人值守，永不放弃）。"""
        backoff = self._backoff_start
        while not self._stopped:
            try:
                transport = await self._connect_fn(self.server_url, self.api_key)
            except Exception as exc:  # noqa: BLE001 — 连接失败一律退避重试
                self._log(f"connect failed: {exc!r}")
                await asyncio.sleep(backoff)
                backoff = next_backoff(backoff, self._backoff_cap)
                continue
            self._was_connected = False
            try:
                await self._serve(transport)
            except TransportClosed:
                pass
            except Exception as exc:  # noqa: BLE001
                self._log(f"serve error: {exc!r}")
            finally:
                await self._safe_close(transport)
                self.connected.clear()
                self._transport = None
                self._fail_pending("connection closed")
            if self._was_connected:
                backoff = self._backoff_start  # 成功连过一轮 → 退避复位
            else:
                await asyncio.sleep(backoff)
                backoff = next_backoff(backoff, self._backoff_cap)

    def stop(self) -> None:
        self._stopped = True

    # ---------------------------------------------------------------- 一条连接的服务

    async def _serve(self, transport: Transport) -> None:
        self._transport = transport
        if not self._detected_runtimes:
            self._detected_runtimes = await probe_runtimes(self._runner)
        hello_frame_id = await self._send_hello()
        ack = await transport.recv()  # 握手第 3 步：hello_ack（借 ack 信封，契约 D §4.1）
        self._apply_hello_ack(ack, hello_frame_id)
        self._was_connected = True
        self.connected.set()
        self._flush_event.set()  # 重连即重传离线期缓冲（契约 D §4.1 第 5 步）

        reader = asyncio.create_task(self._reader(transport))
        heartbeat = asyncio.create_task(self._heartbeat(transport))
        flush = asyncio.create_task(self._flush_loop(transport))
        try:
            await reader  # reader 结束/抛 TransportClosed = 连接终结
        finally:
            for t in (heartbeat, flush):
                t.cancel()
            for t in (heartbeat, flush):
                with contextlib.suppress(asyncio.CancelledError, TransportClosed):
                    await t

    async def _send_hello(self) -> str:
        frame = ReportFrame(
            frame_id=new_ulid(),
            type=ReportType.HELLO,
            at=now_iso(),
            data=self.build_hello().model_dump(mode="json"),
        )
        await self._send(frame.model_dump(mode="json"))
        return frame.frame_id

    def build_hello(self) -> DaemonHelloData:
        """hello 载荷：真实进程表（adapter）+ 探测 runtime + 缓冲计数（契约 D §4.1）。"""
        return DaemonHelloData(
            daemon_version=self.daemon_version,
            os=self.os_name,
            arch=self.arch,
            detected_runtimes=self._detected_runtimes,
            agents=self.adapter.process_table(),
            buffered=self.buffer.counts(),
        )

    def _apply_hello_ack(self, ack: dict[str, Any], hello_frame_id: str) -> None:
        if ack.get("kind") != FrameKind.ACK or ack.get("ref") != hello_frame_id:
            raise TransportClosed(f"unexpected first frame (expected hello_ack): {ack!r}")
        self.hello_ack = DaemonHelloAckData.model_validate(ack["data"])
        if self.hello_ack.protocol_v != DAEMON_PROTOCOL_V:
            raise TransportClosed(f"protocol mismatch: {self.hello_ack.protocol_v}")
        self.heartbeat_sec = float(self.hello_ack.heartbeat_sec)  # 记 heartbeat_sec（契约 D §2）

    # ---------------------------------------------------------------- 收帧循环 + 分发

    async def _reader(self, transport: Transport) -> None:
        while True:
            frame = await transport.recv()  # TransportClosed 抛出 = 断连
            await self._dispatch(frame)

    async def _dispatch(self, frame: dict[str, Any]) -> None:
        kind = frame.get("kind")
        if kind == FrameKind.INSTR:
            await self.handle_instr(frame)
        elif kind == FrameKind.QUERY:
            await self.handle_query(frame)
        elif kind == FrameKind.ACK:
            self._resolve_report_ack(frame)
        elif kind == FrameKind.PONG:
            self._pong_event.set()
        elif kind == FrameKind.PING:
            with contextlib.suppress(TransportClosed):
                await self._send({"v": DAEMON_PROTOCOL_V, "kind": FrameKind.PONG.value})
        # reply / report: server→daemon 不发；忽略

    # ---------------------------------------------------------------- 指令消费（契约 D §5）

    async def handle_instr(self, frame: dict[str, Any]) -> None:
        frame_id = frame["frame_id"]
        itype = InstrType(frame["type"])
        if frame_id in self._recent_frame_set and itype not in _STATUS_REPLAY_INSTRS:
            # frame_id 短窗去重加速器：原帧重发 → 直接 noop（自然键幂等已保证无副作用）。
            await self._send_ack(frame_id, AckResult.NOOP, None)
            return
        handler = HANDLERS.get(itype)
        if handler is None:
            await self._send_ack(frame_id, AckResult.FAILED, None)
            return
        try:
            result, error = await handler(self, frame.get("data") or {})
        except Exception as exc:  # noqa: BLE001 — 处理器异常收敛为 failed（契约 D §3）
            self._log(f"instr {itype} failed: {exc!r}")
            from coagentia_contracts.daemon import FrameError

            result, error = AckResult.FAILED, FrameError(code="HANDLER_ERROR", message=str(exc))
        await self._send_ack(frame_id, result, error)
        # 只有 ack 成功写入传输后才做短窗记忆；否则重连重发必须重新走自然键处理器，
        # 让 worktree 等带状态指令能补报终态，而不是只回一个失真的 noop。
        self._remember_frame(frame_id)

    def _remember_frame(self, frame_id: str) -> None:
        if len(self._recent_frames) == self._recent_frames.maxlen:
            self._recent_frame_set.discard(self._recent_frames[0])
        self._recent_frames.append(frame_id)
        self._recent_frame_set.add(frame_id)

    async def _send_ack(self, ref: str, result: AckResult, error: Any) -> None:
        payload: dict[str, Any] = {
            "v": DAEMON_PROTOCOL_V,
            "kind": FrameKind.ACK.value,
            "ref": ref,
            "result": result.value,
        }
        if error is not None:
            payload["error"] = error.model_dump(mode="json")
        await self._send(payload)

    # ---------------------------------------------------------------- 查询代理（契约 D §6）

    async def handle_query(self, frame: dict[str, Any]) -> None:
        qtype = frame["type"]
        data = frame.get("data") or {}
        try:
            if qtype == QueryType.HOME_TREE:
                reply = self._home_tree(data)
            elif qtype == QueryType.HOME_FILE:
                reply = self._home_file(data)
            else:
                reply = {"error": "unsupported"}
        except Exception as exc:  # noqa: BLE001
            reply = {"error": str(exc)}
        await self._send(
            {
                "v": DAEMON_PROTOCOL_V,
                "kind": FrameKind.REPLY.value,
                "ref": frame["frame_id"],
                "data": reply,
            }
        )

    def _home_tree(self, data: dict[str, Any]) -> dict[str, Any]:
        q = HomeTreeQuery.model_validate(data)
        target = self._safe_join(q.agent_member_id, q.path)
        entries: list[HomeTreeEntry] = []
        if target is not None and target.is_dir():
            for child in sorted(target.iterdir(), key=lambda p: p.name):
                st = child.stat()
                entries.append(
                    HomeTreeEntry(
                        name=child.name,
                        kind="dir" if child.is_dir() else "file",
                        size_bytes=st.st_size,
                        mtime=_iso_from_mtime(st.st_mtime),
                    )
                )
        return HomeTreeReply(entries=entries).model_dump(mode="json")

    def _home_file(self, data: dict[str, Any]) -> dict[str, Any]:
        q = HomeFileQuery.model_validate(data)
        target = self._safe_join(q.agent_member_id, q.path)
        if target is None or not target.is_file():
            return HomeFileBinaryReply(size_bytes=0).model_dump(mode="json")
        raw = target.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return HomeFileBinaryReply(size_bytes=len(raw)).model_dump(mode="json")
        truncated = len(text) > _HOME_FILE_MAX
        return HomeFileTextReply(
            content=text[:_HOME_FILE_MAX], truncated=truncated
        ).model_dump(mode="json")

    def _safe_join(self, agent_member_id: str, path: str) -> Path | None:
        """path 规范化后必须在该 Agent home 之内（防 ../ 逃逸，契约 D §6）。"""
        home_str = self.adapter.home_path(agent_member_id)
        root = Path(home_str).expanduser() if home_str else self.paths.agent_home(agent_member_id)
        root = root.resolve()
        candidate = (root / path.lstrip("/\\")).resolve()
        if candidate == root or root in candidate.parents:
            return candidate
        return None

    # ---------------------------------------------------------- AdapterSink 上报（契约 D §7）

    async def on_status_changed(
        self, agent_member_id: str, status: AgentStatus, error_detail: str | None = None
    ) -> None:
        await self._report_best_effort(
            ReportType.AGENT_STATUS_CHANGED,
            AgentStatusChangedData(
                agent_member_id=agent_member_id, status=status, error_detail=error_detail
            ),
        )

    async def on_activity(self, agent_member_id: str, detail: str) -> None:
        now = time.monotonic()
        if now - self._activity_last.get(agent_member_id, 0.0) < _ACTIVITY_PRETHROTTLE:
            return
        self._activity_last[agent_member_id] = now
        await self._report_best_effort(
            ReportType.AGENT_ACTIVITY,
            DaemonAgentActivityData(agent_member_id=agent_member_id, detail=detail),
        )

    def on_usage(self, event: TokenUsageEventIn) -> None:
        self.buffer.append_usage(event)
        self._flush_event.set()

    def on_diagnostic(self, event: DiagnosticEventIn) -> None:
        self.buffer.append_diagnostic(event)
        self._flush_event.set()

    async def rescan_runtimes(self) -> None:
        """runtime.rescan：重探测 → 更新缓存 + runtimes.detected 上报（契约 D §5.3/§7）。"""
        self._detected_runtimes = await probe_runtimes(self._runner)
        await self._report_best_effort(
            ReportType.RUNTIMES_DETECTED, RuntimesDetectedData(runtimes=self._detected_runtimes)
        )

    async def report_worktree_status(self, data: WorktreeStatusData) -> None:
        """worktree 指令先上报现状，再由 handle_instr 发 ack（同 WS 内有序）。"""
        await self._report_best_effort(ReportType.WORKTREE_STATUS, data)

    async def _report_best_effort(self, rtype: ReportType, data: Any) -> None:
        """载状态类上报（无 ack）：连接可用即发，断连忽略（重连 hello 全量重报兜底）。"""
        frame = ReportFrame(
            frame_id=new_ulid(), type=rtype, at=now_iso(), data=data.model_dump(mode="json")
        )
        with contextlib.suppress(TransportClosed):
            await self._send(frame.model_dump(mode="json"))

    # ---------------------------------------------------------------- 缓冲重传（契约 D §7）

    async def _flush_loop(self, transport: Transport) -> None:
        while True:
            await self._flush_event.wait()
            self._flush_event.clear()
            with contextlib.suppress(TransportClosed):
                await self._flush_usage()
                await self._flush_diagnostics()

    async def _flush_usage(self) -> None:
        while self.buffer.has_usage():
            batch = self.buffer.peek_usage(_USAGE_BATCH)
            from coagentia_contracts.daemon import UsageBatchData

            ok = await self._report_awaited(ReportType.USAGE_BATCH, UsageBatchData(events=batch))
            if not ok:
                return  # 未 ack → 保留待重传（不虚增：ULID 不变，server 去重）
            self.buffer.ack_usage([e.id for e in batch])

    async def _flush_diagnostics(self) -> None:
        while self.buffer.has_diagnostics():
            batch = self.buffer.peek_diagnostics(_DIAG_BATCH)
            from coagentia_contracts.daemon import DiagnosticsBatchData

            ok = await self._report_awaited(
                ReportType.DIAGNOSTICS_BATCH, DiagnosticsBatchData(events=batch)
            )
            if not ok:
                return
            self.buffer.ack_diagnostics(len(batch))

    async def _report_awaited(self, rtype: ReportType, data: Any) -> bool:
        """缓冲重传类上报（需 ack）：发帧 → 等 server ack（超时/断连 → False）。"""
        frame_id = new_ulid()
        frame = ReportFrame(
            frame_id=frame_id, type=rtype, at=now_iso(), data=data.model_dump(mode="json")
        )
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._report_acks[frame_id] = fut
        try:
            await self._send(frame.model_dump(mode="json"))
            ack = await asyncio.wait_for(fut, timeout=self.ack_timeout)
        except (TimeoutError, TransportClosed):
            return False
        finally:
            self._report_acks.pop(frame_id, None)
        return ack.get("result") in (AckResult.DONE.value, AckResult.NOOP.value)

    def _resolve_report_ack(self, frame: dict[str, Any]) -> None:
        fut = self._report_acks.get(frame.get("ref", ""))
        if fut is not None and not fut.done():
            fut.set_result(frame)

    def _fail_pending(self, reason: str) -> None:
        for fut in list(self._report_acks.values()):
            if not fut.done():
                fut.set_exception(TransportClosed(reason))
        self._report_acks.clear()

    # ---------------------------------------------------------------- 心跳（契约 D §2）

    async def _heartbeat(self, transport: Transport) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_sec)
            self._pong_event.clear()
            with contextlib.suppress(TransportClosed):
                await self._send({"v": DAEMON_PROTOCOL_V, "kind": FrameKind.PING.value})
            try:
                await asyncio.wait_for(self._pong_event.wait(), timeout=self.pong_timeout)
            except TimeoutError:
                self._log("heartbeat: no pong → reconnect")
                await transport.close()  # 触发 reader TransportClosed → 重连
                return

    # ---------------------------------------------------------------- 底座

    async def _send(self, payload: dict[str, Any]) -> None:
        transport = self._transport
        if transport is None:
            raise TransportClosed("no transport")
        async with self._send_lock:
            await transport.send(payload)

    async def _safe_close(self, transport: Transport) -> None:
        with contextlib.suppress(Exception):
            await transport.close()

    def _log(self, message: str) -> None:
        line = f"{now_iso()} {message}\n"
        with contextlib.suppress(OSError):
            self.paths.daemon_dir.mkdir(parents=True, exist_ok=True)
            with open(self.paths.log_path, "a", encoding="utf-8") as f:
                f.write(line)


def _iso_from_mtime(mtime: float) -> str:
    dt = datetime.fromtimestamp(mtime, UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"
