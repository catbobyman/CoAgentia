"""daemon 单测辅助：内存传输（RecordingTransport）+ 帧构造 + client 装配。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from coagentia_daemon.adapter import FakeAdapter
from coagentia_daemon.buffer import TelemetryBuffer
from coagentia_daemon.client import DaemonClient
from coagentia_daemon.paths import DataPaths
from coagentia_daemon.transport import TransportClosed
from coagentia_daemon.util import new_ulid, now_iso

_CLOSE = object()

# 集成/握手用固定合法 ULID（与 conftest.IntegrationEnv 对齐）。
_HELLO_ACK_COMPUTER = "01K5CMPT00000000000000000A"
_HELLO_ACK_WORKSPACE = "01K5WKSP00000000000000000A"


async def fake_runner(argv: list[str]) -> tuple[int, str, str]:
    """探测桩：免真 claude 子进程。"""
    return 0, "2.1.205 (Claude Code)", ""


async def until(pred: Any, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if pred():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met within timeout")


class RecordingTransport:
    """内存传输：记录 daemon 上行帧 + 可注入下行帧（无需真 socket / server）。"""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self._incoming: asyncio.Queue[Any] = asyncio.Queue()
        self.closed = False

    async def send(self, frame: dict[str, Any]) -> None:
        if self.closed:
            raise TransportClosed("closed")
        self.sent.append(frame)

    async def recv(self) -> dict[str, Any]:
        item = await self._incoming.get()
        if item is _CLOSE:
            raise TransportClosed("closed")
        return item

    async def close(self) -> None:
        self.closed = True
        self._incoming.put_nowait(_CLOSE)

    # ---- 注入下行 / 过滤上行 ----
    def feed(self, frame: dict[str, Any]) -> None:
        self._incoming.put_nowait(frame)

    def acks(self) -> list[dict[str, Any]]:
        return [f for f in self.sent if f.get("kind") == "ack"]

    def reports(self, rtype: str | None = None) -> list[dict[str, Any]]:
        out = [f for f in self.sent if f.get("kind") == "report"]
        return [f for f in out if f.get("type") == rtype] if rtype else out

    def last_ack(self) -> dict[str, Any]:
        return self.acks()[-1]


class AutoAckTransport(RecordingTransport):
    """RecordingTransport + 自动应答 hello→hello_ack、ping→pong（免真 server 的握手驱动）。"""

    def __init__(self, *, heartbeat_sec: int = 25) -> None:
        super().__init__()
        self.heartbeat_sec = heartbeat_sec

    async def send(self, frame: dict[str, Any]) -> None:
        await super().send(frame)
        if frame.get("kind") == "report" and frame.get("type") == "hello":
            self.feed(
                {
                    "v": 1,
                    "kind": "ack",
                    "ref": frame["frame_id"],
                    "result": "done",
                    "data": {
                        "protocol_v": 1,
                        "server_version": "test",
                        "computer_id": _HELLO_ACK_COMPUTER,
                        "workspace_id": _HELLO_ACK_WORKSPACE,
                        "heartbeat_sec": self.heartbeat_sec,
                    },
                }
            )
        elif frame.get("kind") == "ping":
            self.feed({"v": 1, "kind": "pong"})


def make_client(
    tmp_path: Path,
    *,
    adapter: FakeAdapter | None = None,
    transport: RecordingTransport | None = None,
    runner: Any = None,
    **kw: Any,
) -> tuple[DaemonClient, FakeAdapter, RecordingTransport | None]:
    adapter = adapter or FakeAdapter()
    paths = DataPaths(tmp_path / "root")
    paths.ensure_dirs()
    buffer = TelemetryBuffer(paths)
    client = DaemonClient(
        server_url="http://127.0.0.1:0",
        api_key="cak_test",
        adapter=adapter,
        buffer=buffer,
        paths=paths,
        os_name="linux",
        arch="x64",
        runner=runner,
        **kw,
    )
    if transport is not None:
        client._transport = transport
    return client, adapter, transport


# ---- 帧构造 ----

def boot_data(tmp_path: Path, agent_id: str | None = None, name: str = "A") -> dict[str, Any]:
    aid = agent_id or new_ulid()
    return {
        "agent_member_id": aid,
        "name": name,
        "runtime": "claude_code",
        "model": "claude-opus-4-8",
        "home_path": str(tmp_path / "home" / aid),
        "skills": [],
    }


def instr(itype: str, data: dict[str, Any], frame_id: str | None = None) -> dict[str, Any]:
    return {
        "v": 1,
        "kind": "instr",
        "frame_id": frame_id or new_ulid(),
        "type": itype,
        "at": now_iso(),
        "data": data,
    }


def message_public(
    channel_id: str, workspace_id: str | None = None, body: str = "hi"
) -> dict[str, Any]:
    return {
        "id": new_ulid(),
        "workspace_id": workspace_id or new_ulid(),
        "channel_id": channel_id,
        "thread_root_id": None,
        "author_member_id": None,
        "kind": "user",
        "card_kind": None,
        "card_ref": None,
        "body": body,
        "created_at": now_iso(),
    }


def usage_event(agent_id: str, event_id: str | None = None) -> dict[str, Any]:
    return {
        "id": event_id or new_ulid(),
        "agent_member_id": agent_id,
        "channel_id": None,
        "thread_root_id": None,
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "source_session": "sess-1",
        "reported_at": now_iso(),
    }
