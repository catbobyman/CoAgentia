"""A5 daemon 网关测试桩：受控最小库场景构造器 + 假 daemon（驱动 /api/daemon/ws 网关侧）。

不依赖 seed（避免多 Agent 噪声）：每个测试用 Env 精确插入 workspace/computer/agents/
channels/messages，再用 StubDaemon 连 /api/daemon/ws 发 hello、收指令、回 ack/reply/report。

实体 ID 用真 ledger.new_ulid（每次 +2ms 保证毫秒单调 → 字典序即时序，与 reminder 锚点等
运行期真 ULID 一致排序）；workspace/computer/owner 用固定合法 ULID。
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from coagentia_server.db import models
from coagentia_server.ledger.service import new_ulid, now_iso
from sqlalchemy import func, insert, select
from sqlalchemy.engine import Engine

TEST_KEY = "cak_testdaemonkey"
KEY_HASH = hashlib.sha256(TEST_KEY.encode()).hexdigest()
AUTH = {"Authorization": f"Bearer {TEST_KEY}"}

# 固定合法 ULID（crockford，无 I/L/O/U）。
_WS_ID = "01K5WKSP00000000000000000A"
_COMP_ID = "01K5CMPT00000000000000000A"
_OWNER_ID = "01K5HMAN00000000000000000A"


def nid() -> str:
    """毫秒单调真 ULID（+2ms 保证字典序即插入序）。"""
    time.sleep(0.002)
    return new_ulid()


class Env:
    """受控最小库场景构造器（迁移后空库 → 精确插入）。"""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.ws_id = _WS_ID
        self.comp_id = _COMP_ID
        self.owner_id = _OWNER_ID
        with engine.begin() as c:
            c.execute(
                insert(models.Workspace.__table__).values(
                    id=self.ws_id, name="T", slug="t", created_at=now_iso()
                )
            )
            c.execute(
                insert(models.Computer.__table__).values(
                    id=self.comp_id,
                    workspace_id=self.ws_id,
                    name="Rig",
                    api_key_hash=KEY_HASH,
                    status="offline",
                    created_at=now_iso(),
                )
            )
            c.execute(
                insert(models.Member.__table__).values(
                    id=self.owner_id,
                    workspace_id=self.ws_id,
                    kind="human",
                    name="Owner",
                    role="owner",
                    created_at=now_iso(),
                )
            )

    def add_agent(
        self, name: str, status: str, *, model: str = "m", runtime: str = "claude_code"
    ) -> str:
        mid = nid()
        with self.engine.begin() as c:
            c.execute(
                insert(models.Member.__table__).values(
                    id=mid,
                    workspace_id=self.ws_id,
                    kind="agent",
                    name=name,
                    role="member",
                    created_at=now_iso(),
                )
            )
            c.execute(
                insert(models.Agent.__table__).values(
                    member_id=mid,
                    computer_id=self.comp_id,
                    runtime=runtime,
                    model=model,
                    description="",
                    home_path=f"~/.coagentia/agents/{mid}",
                    status=status,
                    created_by_member_id=self.owner_id,
                )
            )
        return mid

    def add_channel(
        self, *, kind: str = "channel", name: str | None = None, dm_key: str | None = None
    ) -> str:
        cid = nid()
        with self.engine.begin() as c:
            c.execute(
                insert(models.Channel.__table__).values(
                    id=cid,
                    workspace_id=self.ws_id,
                    kind=kind,
                    name=name,
                    dm_key=dm_key,
                    created_at=now_iso(),
                )
            )
        return cid

    def join(self, channel_id: str, member_id: str) -> None:
        with self.engine.begin() as c:
            c.execute(
                insert(models.ChannelMember.__table__).values(
                    channel_id=channel_id, member_id=member_id, joined_at=now_iso()
                )
            )

    def add_message(
        self,
        channel_id: str,
        *,
        author: str | None = None,
        kind: str = "user",
        body: str = "x",
        mentions: tuple[str, ...] = (),
    ) -> str:
        mid = nid()
        with self.engine.begin() as c:
            c.execute(
                insert(models.Message.__table__).values(
                    id=mid,
                    workspace_id=self.ws_id,
                    channel_id=channel_id,
                    thread_root_id=None,
                    author_member_id=author,
                    kind=kind,
                    body=body,
                    created_at=now_iso(),
                )
            )
            for m in mentions:
                c.execute(
                    insert(models.MessageMention.__table__).values(message_id=mid, member_id=m)
                )
        return mid

    def set_read(self, member_id: str, channel_id: str, message_id: str) -> None:
        with self.engine.begin() as c:
            c.execute(
                insert(models.ReadPosition.__table__).values(
                    member_id=member_id,
                    channel_id=channel_id,
                    last_read_message_id=message_id,
                    last_read_at=now_iso(),
                )
            )

    def read_position(self, member_id: str, channel_id: str) -> str | None:
        with self.engine.connect() as c:
            row = c.execute(
                select(models.ReadPosition.__table__.c.last_read_message_id).where(
                    models.ReadPosition.__table__.c.member_id == member_id,
                    models.ReadPosition.__table__.c.channel_id == channel_id,
                )
            ).first()
        return row[0] if row else None

    def add_reminder(
        self,
        agent_id: str,
        channel_id: str,
        *,
        next_fire_at: str,
        status: str = "active",
        kind: str = "once",
        cadence: str | None = None,
    ) -> str:
        rid = nid()
        # once：cadence 默认 = 锚点 ISO 时刻；recurring：cadence = interval（如 PT1H），须传。
        cadence = cadence if cadence is not None else next_fire_at
        loop_contract_id: str | None = None
        with self.engine.begin() as c:
            if kind == "recurring":
                # recurring 的 reminders CHECK 要求 loop_contract_id 非空 → 先落挂接契约行。
                loop_contract_id = nid()
                c.execute(
                    insert(models.TaskContract.__table__).values(
                        id=loop_contract_id,
                        workspace_id=self.ws_id,
                        task_id=None,
                        reminder_id=rid,
                        kind="loop_contract",
                        version="coagentia.loop-contract.v1",
                        body={
                            "version": "coagentia.loop-contract.v1",
                            "cadence": cadence,
                            "verification": ["v"],
                            "budget": {"max_retries": 1, "max_runtime_min": 10},
                            "tools": [],
                            "escalation": "拉人",
                        },
                        revision=1,
                        created_by_member_id=agent_id,
                        created_at=now_iso(),
                    )
                )
            c.execute(
                insert(models.Reminder.__table__).values(
                    id=rid,
                    workspace_id=self.ws_id,
                    agent_member_id=agent_id,
                    kind=kind,
                    cadence=cadence,
                    anchor_channel_id=channel_id,
                    loop_contract_id=loop_contract_id,
                    next_fire_at=next_fire_at,
                    status=status,
                    created_at=now_iso(),
                )
            )
        return rid

    def reminder_next_fire_at(self, reminder_id: str) -> str:
        with self.engine.connect() as c:
            return c.execute(
                select(models.Reminder.__table__.c.next_fire_at).where(
                    models.Reminder.__table__.c.id == reminder_id
                )
            ).scalar_one()

    def reminder_status(self, reminder_id: str) -> str:
        with self.engine.connect() as c:
            return c.execute(
                select(models.Reminder.__table__.c.status).where(
                    models.Reminder.__table__.c.id == reminder_id
                )
            ).scalar_one()

    def usage_count(self) -> int:
        with self.engine.connect() as c:
            return c.execute(
                select(func.count()).select_from(models.TokenUsageEvent.__table__)
            ).scalar_one()

    def diag_count(self) -> int:
        with self.engine.connect() as c:
            return c.execute(
                select(func.count()).select_from(models.DiagnosticEvent.__table__)
            ).scalar_one()

    def system_message_count(self, channel_id: str) -> int:
        with self.engine.connect() as c:
            return c.execute(
                select(func.count())
                .select_from(models.Message.__table__)
                .where(
                    models.Message.__table__.c.channel_id == channel_id,
                    models.Message.__table__.c.kind == "system",
                )
            ).scalar_one()

    def computer_status(self) -> str:
        with self.engine.connect() as c:
            return c.execute(
                select(models.Computer.__table__.c.status).where(
                    models.Computer.__table__.c.id == self.comp_id
                )
            ).scalar_one()

    def detected_runtimes(self) -> list[dict[str, Any]]:
        with self.engine.connect() as c:
            return c.execute(
                select(models.Computer.__table__.c.detected_runtimes).where(
                    models.Computer.__table__.c.id == self.comp_id
                )
            ).scalar_one()

    def agent_status(self, member_id: str) -> str:
        with self.engine.connect() as c:
            return c.execute(
                select(models.Agent.__table__.c.status).where(
                    models.Agent.__table__.c.member_id == member_id
                )
            ).scalar_one()


# ------------------------------------------------------------ 帧构造 + StubDaemon


def hello_frame(
    agents: list[tuple[str, str]],
    *,
    version: str = "0.1.0",
    os: str = "linux",
    arch: str = "x64",
    runtimes: list[dict[str, Any]] | None = None,
    v: int = 1,
    boot_nonce: str | None = None,
    previews: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "daemon_version": version,
        "os": os,
        "arch": arch,
        "detected_runtimes": runtimes or [],
        "agents": [{"agent_member_id": a, "status": s} for a, s in agents],
        "buffered": {"diagnostics": 0, "usage": 0},
    }
    # v1.0.5 可选字段：缺省不带（模拟旧 daemon → server 按重启口径全量 fail-close）。
    if boot_nonce is not None:
        data["boot_nonce"] = boot_nonce
    if previews is not None:
        data["previews"] = previews
    return {
        "v": v,
        "kind": "report",
        "frame_id": new_ulid(),
        "type": "hello",
        "at": now_iso(),
        "data": data,
    }


class StubDaemon:
    """假 daemon：包裹 TestClient websocket，收发契约 D 帧。"""

    def __init__(self, ws: Any) -> None:
        self.ws = ws

    def send(self, frame: dict[str, Any]) -> None:
        self.ws.send_json(frame)

    def recv(self) -> dict[str, Any]:
        return self.ws.receive_json()

    def hello(self, agents: list[tuple[str, str]], **kw: Any) -> None:
        self.send(hello_frame(agents, **kw))

    def recv_hello_ack(self) -> dict[str, Any]:
        f = self.recv()
        assert f["kind"] == "ack", f
        assert f["data"]["protocol_v"] == 1
        return f

    def recv_instr(self) -> dict[str, Any]:
        f = self.recv()
        assert f["kind"] == "instr", f
        return f

    def ack(self, frame: dict[str, Any], result: str = "done") -> None:
        self.send({"v": 1, "kind": "ack", "ref": frame["frame_id"], "result": result})

    def reply(self, frame: dict[str, Any], data: dict[str, Any]) -> None:
        self.send({"v": 1, "kind": "reply", "ref": frame["frame_id"], "data": data})

    def report(self, rtype: str, data: dict[str, Any]) -> str:
        fid = new_ulid()
        self.send(
            {
                "v": 1,
                "kind": "report",
                "frame_id": fid,
                "type": rtype,
                "at": now_iso(),
                "data": data,
            }
        )
        return fid

    def ping(self) -> None:
        self.send({"v": 1, "kind": "ping"})

    def recv_pong(self) -> dict[str, Any]:
        f = self.recv()
        assert f["kind"] == "pong", f
        return f

    def sync(self) -> None:
        """屏障：ping→pong 往返确保此前所有下行帧与 ack 副作用已被网关顺序处理。"""
        self.ping()
        self.recv_pong()


def drain_revalidation(daemon: StubDaemon, *, count: int = 1) -> list[dict[str, Any]]:
    """消费 reconnect 握手复验下发的 worktree.ensure 并 ack（#3）。

    握手前已 seed 的 active worktree 行会在 reconcile(revalidate_worktrees=True) 里逐行重下发
    ensure，且严格先于 cleanup/wake/deliver 等后续帧（同一 reconcile 协程顺序 await）；凡此类
    测试须先消费并 ack 掉这批帧——不 ack 会让 hub 等 ack 直到超时，拖慢并打乱帧序。count=
    握手时 active 且任务未终态的 worktree 行数。返回帧列表供进一步断言。"""
    frames: list[dict[str, Any]] = []
    for _ in range(count):
        frame = daemon.recv_instr()
        assert frame["type"] == "worktree.ensure", frame
        daemon.ack(frame, "done")
        frames.append(frame)
    return frames
