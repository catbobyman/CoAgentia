"""真 server（A3）专属测试：bootstrap 冷启动、事件总线、文件 GC、幂等、真业务逻辑。

契约一致性的形状/错误双跑见 test_conformance_dual.py（对 [mock, 真 server] 参数化）。
本文件覆盖 mock 无法覆盖的真 DB 持久化 + 事务后发射 + staging/GC 行为。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from coagentia_contracts import entities, rest
from coagentia_contracts.ws import EventType
from coagentia_server.db import models
from coagentia_server.events import PendingEvent
from coagentia_server.files import FileStore
from coagentia_server.files.gc import run_gc
from coagentia_server.routes.workspace import EMPTY_CANVAS_HASH
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

BUILD_CHANNEL = "build"
PAT = "Pat"


def _channel(client: TestClient, name: str) -> dict:
    return next(c for c in client.get("/api/channels").json()["items"] if c["name"] == name)


def _member(client: TestClient, name: str) -> dict:
    return next(m for m in client.get("/api/members").json() if m["name"] == name)


# ---------------------------------------------------------------- bootstrap 冷启动


def test_bootstrap_creates_workspace_owner_channel_canvas(
    empty_server_client: TestClient, migrated_engine: Engine
) -> None:
    c = empty_server_client
    assert c.get("/api/workspace").status_code == 404  # 未 bootstrap

    r = c.post("/api/workspace", json={"name": "Acme", "slug": "acme"})
    assert r.status_code == 201
    entities.WorkspacePublic.model_validate(r.json())

    members = c.get("/api/members").json()
    assert len(members) == 1 and members[0]["role"] == "owner" and members[0]["kind"] == "human"

    channels = c.get("/api/channels").json()["items"]
    assert [ch["name"] for ch in channels] == ["all"]

    # #all 有一张空画布，baseline_hash = 空快照指纹（契约 A §6，非 NULL）。
    with migrated_engine.connect() as conn:
        h = conn.execute(select(models.Canvas.__table__.c.baseline_hash)).scalar_one()
    assert h == EMPTY_CANVAS_HASH


def test_bootstrap_twice_conflicts(empty_server_client: TestClient) -> None:
    c = empty_server_client
    assert c.post("/api/workspace", json={"name": "A", "slug": "a"}).status_code == 201
    r = c.post("/api/workspace", json={"name": "A", "slug": "a"})
    assert r.status_code == 409
    assert rest.ErrorResponse.model_validate(r.json()).error.code is rest.ErrorCode.WORKSPACE_EXISTS


def test_patch_workspace_broadcasts(server_client: TestClient) -> None:
    events: list[PendingEvent] = []
    server_client.app.state.bus.subscribe(events.append)
    r = server_client.patch("/api/workspace", json={"ui_theme": "light"})
    assert r.status_code == 200 and r.json()["ui_theme"] == "light"
    assert any(e.type is EventType.WORKSPACE_UPDATED for e in events)


# ---------------------------------------------------------------- 事件总线（提交后发射）


def test_bus_emits_message_created_after_commit(server_client: TestClient) -> None:
    events: list[PendingEvent] = []
    server_client.app.state.bus.subscribe(events.append)
    build = _channel(server_client, BUILD_CHANNEL)

    r = server_client.post(f"/api/channels/{build['id']}/messages", json={"body": "契约即形状。"})
    assert r.status_code == 201
    msg_events = [e for e in events if e.type is EventType.MESSAGE_CREATED]
    assert len(msg_events) == 1
    ev = msg_events[0]
    assert ev.channel_id == build["id"]
    assert ev.data["message"]["id"] == r.json()["message"]["id"]


def test_bus_no_emit_on_rejected_write(server_client: TestClient) -> None:
    """归档频道写入被拒 → 事务回滚 → 不发射事件（契约 C §1.4）。"""
    build = _channel(server_client, BUILD_CHANNEL)
    server_client.post(f"/api/channels/{build['id']}/archive")
    events: list[PendingEvent] = []
    server_client.app.state.bus.subscribe(events.append)
    r = server_client.post(f"/api/channels/{build['id']}/messages", json={"body": "x"})
    assert r.status_code == 409
    assert not [e for e in events if e.type is EventType.MESSAGE_CREATED]


# ---------------------------------------------------------------- 真业务逻辑：@mention 落库


def test_mention_persisted(server_client: TestClient, seeded_engine: Engine) -> None:
    build = _channel(server_client, BUILD_CHANNEL)
    r = server_client.post(f"/api/channels/{build['id']}/messages", json={"body": "hey @Pat 看下"})
    mid = r.json()["message"]["id"]
    with seeded_engine.connect() as conn:
        rows = conn.execute(
            select(models.MessageMention.__table__.c.member_id).where(
                models.MessageMention.__table__.c.message_id == mid
            )
        ).scalars().all()
    pat = _member(server_client, PAT)
    assert pat["id"] in rows


# ---------------------------------------------------------------- 幂等（复用 A2 账本）


def test_idempotency_same_key_same_body_returns_first(server_client: TestClient) -> None:
    build = _channel(server_client, BUILD_CHANNEL)
    h = {"Idempotency-Key": "op-1"}
    url = f"/api/channels/{build['id']}/messages"
    r1 = server_client.post(url, json={"body": "once"}, headers=h)
    r2 = server_client.post(url, json={"body": "once"}, headers=h)
    assert r1.status_code == r2.status_code == 201
    assert r1.json()["message"]["id"] == r2.json()["message"]["id"]


def test_idempotency_same_key_diff_body_conflicts(server_client: TestClient) -> None:
    build = _channel(server_client, BUILD_CHANNEL)
    h = {"Idempotency-Key": "op-2"}
    url = f"/api/channels/{build['id']}/messages"
    server_client.post(url, json={"body": "a"}, headers=h)
    r = server_client.post(url, json={"body": "b"}, headers=h)
    assert r.status_code == 409
    err = rest.ErrorResponse.model_validate(r.json())
    assert err.error.code is rest.ErrorCode.IDEMPOTENCY_MISMATCH


# ---------------------------------------------------------------- daemon 依赖端点 503


def test_lifecycle_requires_daemon(server_client: TestClient) -> None:
    pat = _member(server_client, PAT)
    r = server_client.post(f"/api/agents/{pat['id']}/lifecycle", json={"action": "start"})
    assert r.status_code == 503
    assert rest.ErrorResponse.model_validate(r.json()).error.code is rest.ErrorCode.DAEMON_OFFLINE


def test_home_endpoints_require_daemon(server_client: TestClient) -> None:
    pat = _member(server_client, PAT)
    assert server_client.get(f"/api/agents/{pat['id']}/home/tree").status_code == 503
    assert server_client.get(
        f"/api/agents/{pat['id']}/home/file", params={"path": "MEMORY.md"}
    ).status_code == 503


# ---------------------------------------------------------------- POST /agents 真建行


def test_create_agent_persists_member_and_agent(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    events: list[PendingEvent] = []
    server_client.app.state.bus.subscribe(events.append)
    r = server_client.post(
        "/api/agents",
        json={
            "computer_id": "01K0CMPT000000000000000001",
            "name": "Vega",
            "runtime": "claude_code",
            "model": "claude-sonnet",
        },
    )
    assert r.status_code == 201
    agent = entities.AgentPublic.model_validate(r.json())
    with seeded_engine.connect() as conn:
        m = conn.execute(
            select(models.Member.__table__).where(models.Member.__table__.c.id == agent.member_id)
        ).mappings().first()
        a = conn.execute(
            select(models.Agent.__table__).where(
                models.Agent.__table__.c.member_id == agent.member_id
            )
        ).mappings().first()
    assert m is not None and m["kind"] == "agent" and m["role"] == "member"
    assert a is not None and a["computer_id"] == "01K0CMPT000000000000000001"
    assert any(e.type is EventType.MEMBER_CREATED for e in events)


# ---------------------------------------------------------------- 诊断真读（可空）


def test_diagnostics_empty_page_shape(server_client: TestClient) -> None:
    pat = _member(server_client, PAT)
    r = server_client.get(f"/api/agents/{pat['id']}/diagnostics")
    assert r.status_code == 200
    page = rest.Page[entities.DiagnosticEventPublic].model_validate(r.json())
    assert page.items == [] and page.next_cursor is None


def test_reminder_cancel_writes_system_message_and_diagnostic(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    pat = _member(server_client, PAT)
    build = _channel(server_client, BUILD_CHANNEL)
    r = server_client.post(
        "/api/reminders",
        json={
            "kind": "once",
            "cadence": "2026-07-10T09:00:00.000Z",
            "anchor_channel_id": build["id"],
        },
        headers={"X-Acting-Member": pat["id"]},
    )
    rid = r.json()["id"]
    before = server_client.get(f"/api/channels/{build['id']}/messages").json()["items"]
    assert server_client.delete(f"/api/reminders/{rid}").status_code == 204
    after = server_client.get(f"/api/channels/{build['id']}/messages").json()["items"]
    assert len(after) == len(before) + 1  # 锚点系统消息
    with seeded_engine.connect() as conn:
        n = conn.execute(
            select(func.count()).select_from(models.DiagnosticEvent.__table__)
        ).scalar_one()
    assert n >= 1  # 取消留痕


# ---------------------------------------------------------------- 文件 staging + GC


def test_file_staging_then_bind_moves_to_files_dir(
    server_client: TestClient, seeded_engine: Engine, tmp_path: Path
) -> None:
    build = _channel(server_client, BUILD_CHANNEL)
    r = server_client.post("/api/files", files={"file": ("a.md", b"# hi", "text/markdown")})
    meta = entities.FilePublic.model_validate(r.json())
    assert meta.message_id is None  # staging 态（契约 D §9.2）

    r2 = server_client.post(
        f"/api/channels/{build['id']}/messages",
        json={"body": "见附件", "file_ids": [meta.id]},
    )
    assert r2.status_code == 201
    with seeded_engine.connect() as conn:
        row = conn.execute(
            select(models.File.__table__).where(models.File.__table__.c.id == meta.id)
        ).mappings().first()
    assert row is not None and row["message_id"] is not None
    assert row["stored_path"] == f"files/{meta.id}"
    assert server_client.get(f"/api/files/{meta.id}/content").content == b"# hi"


def test_file_too_large_rejected(server_client: TestClient) -> None:
    # attachment_max_mb 缺省 200MB；调小工作区上限后再传超限。
    server_client.patch("/api/workspace", json={"attachment_max_mb": 0})
    r = server_client.post(
        "/api/files", files={"file": ("big.bin", b"x", "application/octet-stream")}
    )
    assert r.status_code == 413
    assert rest.ErrorResponse.model_validate(r.json()).error.code is rest.ErrorCode.FILE_TOO_LARGE


def test_gc_deletes_orphan_and_writes_diagnostic(
    server_client: TestClient, seeded_engine: Engine, tmp_path: Path
) -> None:
    r = server_client.post("/api/files", files={"file": ("stale.md", b"old", "text/markdown")})
    fid = r.json()["id"]
    store = FileStore(tmp_path / "data")
    stale = time.time() - 25 * 3600
    os.utime(store.staging_dir / fid, (stale, stale))

    deleted = run_gc(seeded_engine, store)
    assert deleted == 1 and not store.is_staged(fid)
    with seeded_engine.connect() as conn:
        n = conn.execute(
            select(func.count())
            .select_from(models.DiagnosticEvent.__table__)
            .where(models.DiagnosticEvent.__table__.c.type == "system.file_gc")
        ).scalar_one()
    assert n == 1


def test_gc_keeps_fresh_staging(
    server_client: TestClient, seeded_engine: Engine, tmp_path: Path
) -> None:
    r = server_client.post("/api/files", files={"file": ("fresh.md", b"new", "text/markdown")})
    fid = r.json()["id"]
    store = FileStore(tmp_path / "data")
    assert run_gc(seeded_engine, store) == 0
    assert store.is_staged(fid)
