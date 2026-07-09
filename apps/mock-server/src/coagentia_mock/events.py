"""WS 广播枢纽：信封组装（契约 C §3 四要素）+ 连接管理 + 心跳应答。"""

import asyncio
from typing import Any

from coagentia_contracts import ws as ws_contract
from fastapi import WebSocket

from coagentia_mock.state import Store, new_id, now_ts


def build_key(etype: str, data: dict[str, Any]) -> str:
    """幂等键 <实体>:<id>:<单调标记>（契约 C §3）。"""
    for field in ("message", "task", "channel", "member", "computer", "workspace",
                  "draft", "reminder", "deployment", "proposal", "item", "agent"):
        if field in data and isinstance(data[field], dict) and "id" in data[field]:
            entity = data[field]
            marker = entity.get("updated_at") or entity.get("status_changed_at") \
                or entity.get("created_at") or now_ts()
            return f"{field}:{entity['id']}:{marker}"
    if "member_id" in data:
        return f"{etype}:{data['member_id']}:{now_ts()}"
    return f"{etype}:{now_ts()}"


class Hub:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.conns: dict[WebSocket, int] = {}  # conn -> 连接内 seq（契约 C §3）

    async def attach(self, sock: WebSocket) -> None:
        await sock.accept()
        self.conns[sock] = 0
        await self._send(sock, ws_contract.EventType.SYS_HELLO, None, {
            "protocol_v": ws_contract.PROTOCOL_V,
            "server_version": "mock-0.1.0",
            "workspace_id": self.store.workspace["id"],
            "conn_id": new_id(),
            "heartbeat_sec": ws_contract.HEARTBEAT_SEC,
        })

    def detach(self, sock: WebSocket) -> None:
        self.conns.pop(sock, None)

    async def pong(self, sock: WebSocket) -> None:
        await self._send(sock, ws_contract.EventType.SYS_PONG, None, {})

    async def broadcast(self, etype: ws_contract.EventType, channel_id: str | None,
                        data: dict[str, Any]) -> None:
        for sock in list(self.conns):
            try:
                await self._send(sock, etype, channel_id, data)
            except Exception:
                self.detach(sock)

    async def _send(self, sock: WebSocket, etype: ws_contract.EventType,
                    channel_id: str | None, data: dict[str, Any]) -> None:
        self.conns[sock] = self.conns.get(sock, 0) + 1
        envelope = {
            "v": ws_contract.PROTOCOL_V,
            "seq": self.conns[sock],
            "type": etype.value,
            "workspace_id": self.store.workspace["id"],
            "channel_id": channel_id,
            "key": build_key(etype.value, data),
            "at": now_ts(),
            "data": data,
        }
        await sock.send_json(envelope)

    async def play_timeline(self) -> None:
        """脚本化事件回放（M6 验证的驱动源）：先改状态再广播。"""
        elapsed = 0
        for entry in self.store.timeline:
            await asyncio.sleep(max(0, entry["delay_ms"] - elapsed) / 1000)
            elapsed = entry["delay_ms"]
            self.store.apply_timeline_event(entry)
            await self.broadcast(
                ws_contract.EventType(entry["type"]), entry.get("channel_id"), entry["data"]
            )
