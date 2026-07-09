"""WS 传输抽象（契约 D §2 接入）：Transport 协议 + websockets 真实现。

client 只依赖 send/recv/close 三方法 → 单元测试可注入内存传输（RecordingTransport），
集成测试用 websockets_connect 连真 server /api/daemon/ws。连接关闭统一抛 TransportClosed
（client 据此进入指数退避重连）。
"""

from __future__ import annotations

import json
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

import websockets
from coagentia_contracts.daemon import DAEMON_WS_PATH
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed


class TransportClosed(Exception):
    """传输已关闭（连接断开 / 主动关闭）——client 据此重连。"""


class Transport(Protocol):
    async def send(self, frame: dict[str, Any]) -> None: ...

    async def recv(self) -> dict[str, Any]: ...

    async def close(self) -> None: ...


class WebsocketsTransport:
    """websockets 客户端连接包装（JSON 文本帧，契约 D §3）。"""

    def __init__(self, ws: ClientConnection) -> None:
        self._ws = ws

    async def send(self, frame: dict[str, Any]) -> None:
        try:
            await self._ws.send(json.dumps(frame))
        except ConnectionClosed as exc:
            raise TransportClosed(str(exc)) from exc

    async def recv(self) -> dict[str, Any]:
        try:
            raw = await self._ws.recv()
        except ConnectionClosed as exc:
            raise TransportClosed(str(exc)) from exc
        if isinstance(raw, bytes):
            raw = raw.decode()
        return json.loads(raw)

    async def close(self) -> None:
        try:
            await self._ws.close()
        except (ConnectionClosed, OSError):
            pass


def server_url_to_ws(server_url: str) -> str:
    """http(s)://host:port → ws(s)://host:port/api/daemon/ws（契约 D §2 WS 端点）。"""
    parts = urlsplit(server_url)
    scheme = "wss" if parts.scheme in ("https", "wss") else "ws"
    return urlunsplit((scheme, parts.netloc, DAEMON_WS_PATH, "", ""))


async def websockets_connect(server_url: str, api_key: str) -> WebsocketsTransport:
    """连 /api/daemon/ws，携带 Authorization: Bearer（daemon 是真客户端，契约 D §2）。"""
    ws_url = server_url_to_ws(server_url)
    ws = await connect(
        ws_url,
        additional_headers={"Authorization": f"Bearer {api_key}"},
        max_size=None,
        open_timeout=10,
    )
    return WebsocketsTransport(ws)


__all__ = [
    "ConnectionClosed",
    "Transport",
    "TransportClosed",
    "WebsocketsTransport",
    "server_url_to_ws",
    "websockets",
    "websockets_connect",
]
