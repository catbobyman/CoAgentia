"""A7 适配器测试底座：RecordingSink + FakeProc/spawn 注入 + 帧构造。"""

from __future__ import annotations

import asyncio
import itertools
import json
from typing import Any

from coagentia_contracts.enums import AgentStatus

_ULID_SEQ = itertools.count(1)


def seq_ulid() -> str:
    """确定性 ULID 桩（测试内去重/断言用；26 位 Crockford 合法形状）。"""
    n = next(_ULID_SEQ)
    return "01K5TEST" + f"{n:018d}"[-18:]


class RecordingSink:
    """AdapterSink 记录器：四类回调全量收集，供断言。"""

    def __init__(self) -> None:
        self.status: list[tuple[str, AgentStatus, str | None]] = []
        self.activity: list[tuple[str, str]] = []
        self.usage: list[Any] = []
        self.diagnostics: list[Any] = []

    async def on_status_changed(
        self, agent_member_id: str, status: AgentStatus, error_detail: str | None = None
    ) -> None:
        self.status.append((agent_member_id, status, error_detail))

    async def on_activity(self, agent_member_id: str, detail: str) -> None:
        self.activity.append((agent_member_id, detail))

    def on_usage(self, event: Any) -> None:
        self.usage.append(event)

    def on_diagnostic(self, event: Any) -> None:
        self.diagnostics.append(event)

    # ---- 便捷视图 ----
    def statuses(self) -> list[AgentStatus]:
        return [s for _, s, _ in self.status]

    def diag_types(self) -> list[str]:
        return [d.type for d in self.diagnostics]


class FakeStdin:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.chunks.append(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def lines(self) -> list[str]:
        return b"".join(self.chunks).decode("utf-8").splitlines()


class FakeStdout:
    def __init__(self) -> None:
        self._q: asyncio.Queue[bytes] = asyncio.Queue()

    async def readline(self) -> bytes:
        return await self._q.get()

    def push(self, obj: dict[str, Any]) -> None:
        self._q.put_nowait((json.dumps(obj) + "\n").encode("utf-8"))

    def push_raw(self, line: bytes) -> None:
        self._q.put_nowait(line)

    def eof(self) -> None:
        self._q.put_nowait(b"")


class FakeProc:
    """asyncio 子进程桩：测试推 stdout 帧、控制退出。"""

    def __init__(self) -> None:
        self.stdin = FakeStdin()
        self.stdout = FakeStdout()
        self.returncode: int | None = None
        self.pid = 4242
        self._exited = asyncio.Event()
        self.argv: list[str] = []
        self.cwd = ""
        self.env: dict[str, str] = {}

    def terminate(self) -> None:
        if self.returncode is None:
            self.returncode = -15
        self._exited.set()

    def kill(self) -> None:
        self.returncode = -9
        self._exited.set()

    async def wait(self) -> int:
        await self._exited.wait()
        return self.returncode if self.returncode is not None else 0

    def finish(self, code: int = 0) -> None:
        """进程退出：EOF stdout + 置 returncode（触发读循环结束 → on_exit）。"""
        self.returncode = code
        self.stdout.eof()
        self._exited.set()


class SpawnRecorder:
    """spawn 注入：记录每次拉起的 FakeProc。"""

    def __init__(self) -> None:
        self.procs: list[FakeProc] = []

    async def __call__(
        self, argv: list[str], cwd: str, env: dict[str, str]
    ) -> FakeProc:
        p = FakeProc()
        p.argv, p.cwd, p.env = argv, cwd, env
        self.procs.append(p)
        return p


# ---- stream-json 帧构造（锚定 CLI 2.1.205 真机形状） ----


def f_init(
    session_id: str = "11111111-2222-3333-4444-555555555555", model: str = "claude-opus-4-8"
) -> dict[str, Any]:
    return {
        "type": "system",
        "subtype": "init",
        "session_id": session_id,
        "model": model,
        "tools": ["Bash", "Read"],
        "mcp_servers": [{"name": "coagentia", "status": "connected"}],
    }


def f_stream(event_type: str, **event: Any) -> dict[str, Any]:
    ev = {"type": event_type, **event}
    return {"type": "stream_event", "event": ev, "session_id": "s"}


def f_block_start(
    block_type: str,
    *,
    name: str | None = None,
    block_id: str | None = None,
    tool_input: dict | None = None,
) -> dict[str, Any]:
    cb: dict[str, Any] = {"type": block_type}
    if name is not None:
        cb["name"] = name
    if block_id is not None:
        cb["id"] = block_id
    if tool_input is not None:
        cb["input"] = tool_input
    return f_stream("content_block_start", content_block=cb)


def f_assistant(
    text: str = "", tool_uses: list[dict] | None = None, stop_reason: str = "end_turn"
) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    if text:
        content.append({"type": "text", "text": text})
    for tu in tool_uses or []:
        content.append({"type": "tool_use", **tu})
    return {
        "type": "assistant",
        "message": {"content": content, "stop_reason": stop_reason, "model": "m", "id": "msg_1"},
        "session_id": "s",
    }


def f_result(
    *,
    subtype: str = "success",
    is_error: bool = False,
    input_tokens: int = 100,
    output_tokens: int = 20,
    cache_read: int = 5,
    cache_write: int = 3,
) -> dict[str, Any]:
    return {
        "type": "result",
        "subtype": subtype,
        "is_error": is_error,
        "result": "done",
        "session_id": "s",
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_write,
        },
        "total_cost_usd": 0.01,
    }
