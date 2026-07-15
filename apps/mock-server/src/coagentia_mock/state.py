"""内存态 store：加载 fixtures，提供最小写操作（发消息/已读/时间线回放的状态应用）。

mock 纪律（M1-HANDOFF §3）：只验形状不做业务——无 freshness、无 gating、无权限矩阵全量，
仅实现拒绝路径中"形状可验证"的代表性几条（TASK_IN_DM / NAME_TAKEN / CHANNEL_ARCHIVED / R1）。
"""

import json
from pathlib import Path
from typing import Any

from ulid import ULID

FIXTURES = Path(__file__).parents[4] / "packages" / "fixtures"


def now_ts() -> str:
    """ISO-8601 Z 毫秒（契约 A §1）。"""
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + (
        f"{datetime.now(UTC).microsecond // 1000:03d}Z"
    )


def new_id() -> str:
    return str(ULID())


class Store:
    def __init__(self) -> None:
        seed = json.loads((FIXTURES / "seed.json").read_text(encoding="utf-8"))
        self.workspace: dict[str, Any] = seed["workspace"]
        self.computers: list[dict[str, Any]] = seed["computers"]
        self.members: list[dict[str, Any]] = seed["members"]
        self.agents: list[dict[str, Any]] = seed["agents"]
        self.channels: list[dict[str, Any]] = seed["channels"]
        self.channel_members: list[dict[str, Any]] = seed["channel_members"]
        self.messages: list[dict[str, Any]] = seed["messages"]
        self.message_mentions: list[dict[str, Any]] = seed["message_mentions"]
        self.tasks: list[dict[str, Any]] = seed["tasks"]
        self.canvases: list[dict[str, Any]] = seed["canvases"]
        self.read_positions: list[dict[str, Any]] = seed["read_positions"]
        self.token_usage_events: list[dict[str, Any]] = seed["token_usage_events"]
        self.presence: list[dict[str, Any]] = seed["presence"]
        self.skills: dict[str, list[dict[str, Any]]] = {a["member_id"]: [] for a in self.agents}
        self.reminders: list[dict[str, Any]] = []
        self.files: dict[str, dict[str, Any]] = {}  # id -> {"meta": FilePublic dict, "bytes": b}
        self.timeline: list[dict[str, Any]] = json.loads(
            (FIXTURES / "timeline.json").read_text(encoding="utf-8")
        )

    # ---------------- 查找

    def member(self, member_id: str) -> dict[str, Any] | None:
        return next((m for m in self.members if m["id"] == member_id), None)

    def channel(self, channel_id: str) -> dict[str, Any] | None:
        return next((c for c in self.channels if c["id"] == channel_id), None)

    def canvas(self, channel_id: str) -> dict[str, Any] | None:
        return next((c for c in self.canvases if c["channel_id"] == channel_id), None)

    def agent(self, member_id: str) -> dict[str, Any] | None:
        return next((a for a in self.agents if a["member_id"] == member_id), None)

    def channel_messages(self, channel_id: str) -> list[dict[str, Any]]:
        return sorted(
            (m for m in self.messages if m["channel_id"] == channel_id),
            key=lambda m: (m["created_at"], m["id"]),
        )

    # ---------------- 写操作（服务端语义的最小可信实现）

    def resolve_mentions(self, message_id: str, body: str) -> list[str]:
        """@名字 纯文本服务端解析（FR-4.3）：发送时解析一次落 message_mentions。"""
        hit: list[str] = []
        lowered = body.lower()
        for m in self.members:
            if f"@{m['name'].lower()}" in lowered:
                self.message_mentions.append(
                    {"message_id": message_id, "member_id": m["id"]}
                )
                hit.append(m["id"])
        return hit

    def append_message(
        self,
        channel_id: str,
        author_member_id: str | None,
        body: str,
        thread_root_id: str | None = None,
        kind: str = "user",
    ) -> dict[str, Any]:
        row = {
            "id": new_id(),
            "workspace_id": self.workspace["id"],
            "channel_id": channel_id,
            "thread_root_id": thread_root_id,
            "author_member_id": author_member_id,
            "kind": kind,
            "card_kind": None,
            "card_ref": None,
            "body": body,
            "created_at": now_ts(),
        }
        self.messages.append(row)
        self.resolve_mentions(row["id"], body)
        return row

    def create_task(self, channel_id: str, root_message_id: str, title: str,
                    creator_id: str) -> dict[str, Any]:
        ch = self.channel(channel_id)
        assert ch is not None
        number = ch["next_task_number"]
        ch["next_task_number"] = number + 1
        at = now_ts()
        row = {
            "id": new_id(), "workspace_id": self.workspace["id"], "channel_id": channel_id,
            "number": number, "root_message_id": root_message_id, "title": title,
            "status": "todo", "owner_member_id": None, "level": "l1",
            "project_id": None, "writes_code": False,
            "created_by_member_id": creator_id, "silence_override_h": None,
            "status_changed_at": at, "created_at": at,
        }
        self.tasks.append(row)
        return row

    def set_read_position(self, member_id: str, channel_id: str,
                          last_read_message_id: str) -> dict[str, Any]:
        row = next((r for r in self.read_positions
                    if r["member_id"] == member_id and r["channel_id"] == channel_id), None)
        if row is None:
            row = {"member_id": member_id, "channel_id": channel_id,
                   "last_read_message_id": last_read_message_id, "last_read_at": now_ts()}
            self.read_positions.append(row)
        else:
            row["last_read_message_id"] = last_read_message_id
            row["last_read_at"] = now_ts()
        return row

    def apply_timeline_event(self, entry: dict[str, Any]) -> None:
        """时间线事件先改状态再广播——REST 重同步与 WS 通知面保持一致（契约 C 铁律 1）。"""
        etype, data = entry["type"], entry["data"]
        if etype == "message.created":
            if not any(m["id"] == data["message"]["id"] for m in self.messages):
                self.messages.append(data["message"])
        elif etype == "task.updated":
            for i, t in enumerate(self.tasks):
                if t["id"] == data["task"]["id"]:
                    self.tasks[i] = data["task"]
        elif etype == "presence.changed":
            for p in self.presence:
                if p["member_id"] == data["member_id"]:
                    p["status"] = data["status"]
                    if data["status"] != "busy":
                        p["busy_detail"] = None
        elif etype == "agent.activity":
            for p in self.presence:
                if p["member_id"] == data["member_id"]:
                    p["busy_detail"] = data["detail"]
