"""真 server（A3）专属测试：bootstrap 冷启动、事件总线、文件 GC、幂等、真业务逻辑。

契约一致性的形状/错误双跑见 test_conformance_dual.py（对 [mock, 真 server] 参数化）。
本文件覆盖 mock 无法覆盖的真 DB 持久化 + 事务后发射 + staging/GC 行为。
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

from coagentia_contracts import entities, rest
from coagentia_contracts.ws import EventType
from coagentia_server.app import create_app
from coagentia_server.db import models
from coagentia_server.events import PendingEvent
from coagentia_server.files import FileStore
from coagentia_server.files.gc import run_gc
from coagentia_server.routes.workspace import EMPTY_CANVAS_HASH
from fastapi.testclient import TestClient
from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Engine

BUILD_CHANNEL = "build"
PAT = "Pat"
AGENT_TEST_KEY = "cak_rest_agent_test"


def _channel(client: TestClient, name: str) -> dict:
    return next(c for c in client.get("/api/channels").json()["items"] if c["name"] == name)


def _member(client: TestClient, name: str) -> dict:
    return next(m for m in client.get("/api/members").json() if m["name"] == name)


def _agent_headers(engine: Engine, member_id: str) -> dict[str, str]:
    """给 seed Agent 所属 Computer 注入已知测试 key，返回契约 B §2 双头。"""
    digest = hashlib.sha256(AGENT_TEST_KEY.encode()).hexdigest()
    with engine.begin() as conn:
        computer_id = conn.execute(
            select(models.Agent.__table__.c.computer_id).where(
                models.Agent.__table__.c.member_id == member_id
            )
        ).scalar_one()
        conn.execute(
            update(models.Computer.__table__)
            .where(models.Computer.__table__.c.id == computer_id)
            .values(api_key_hash=digest)
        )
    return {
        "Authorization": f"Bearer {AGENT_TEST_KEY}",
        "X-Acting-Member": member_id,
    }


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


def test_mention_ends_at_chinese_punctuation(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    build = _channel(server_client, BUILD_CHANNEL)
    r = server_client.post(
        f"/api/channels/{build['id']}/messages",
        json={"body": "@Pat，让他继续处理"},
    )
    mid = r.json()["message"]["id"]
    pat = _member(server_client, PAT)
    with seeded_engine.connect() as conn:
        mentioned = conn.execute(
            select(models.MessageMention.__table__.c.member_id).where(
                models.MessageMention.__table__.c.message_id == mid
            )
        ).scalars().all()
    assert mentioned == [pat["id"]]


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
        headers=_agent_headers(seeded_engine, pat["id"]),
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


# ------------------------------------------------------------ 循环 Reminder + LoopContract（F4）


def _loop_contract(cadence: str = "PT1H") -> dict:
    """最小合法 LoopContractBody（PRD §4.3 v1）。"""
    return {
        "version": "coagentia.loop-contract.v1",
        "cadence": cadence,
        "verification": ["每次输出附校验命令"],
        "budget": {"max_retries": 1, "max_runtime_min": 10},
        "tools": [],
        "escalation": "连续两次失败拉创建者",
    }


def test_recurring_reminder_creates_linked_loop_contract(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """recurring + 内联 loop_contract → 同事务建 task_contracts 挂接行 + 回填 loop_contract_id。"""
    pat = _member(server_client, PAT)
    build = _channel(server_client, BUILD_CHANNEL)
    r = server_client.post(
        "/api/reminders",
        json={
            "kind": "recurring",
            "cadence": "PT1H",
            "anchor_channel_id": build["id"],
            "loop_contract": _loop_contract("PT1H"),
        },
        headers=_agent_headers(seeded_engine, pat["id"]),
    )
    assert r.status_code == 201, r.text
    rem = entities.ReminderPublic.model_validate(r.json())
    assert rem.kind == "recurring" and rem.status == "active"
    assert rem.loop_contract_id is not None  # 回填成功
    # recurring 首次触发在建后一个 interval（非建即触发——code-review 修）。
    assert rem.next_fire_at > rem.created_at

    tc = models.TaskContract.__table__
    with seeded_engine.connect() as conn:
        row = conn.execute(
            select(tc).where(tc.c.reminder_id == rem.id)
        ).mappings().one()
    # XOR 满足（reminder_id 非空、task_id 空）+ kind/version/挂接 id 对齐。
    assert row["task_id"] is None
    assert row["reminder_id"] == rem.id
    assert row["kind"] == "loop_contract"
    assert row["version"] == "coagentia.loop-contract.v1"
    assert row["id"] == rem.loop_contract_id
    assert row["body"]["cadence"] == "PT1H"


def test_recurring_reminder_without_loop_contract_422(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    pat = _member(server_client, PAT)
    build = _channel(server_client, BUILD_CHANNEL)
    r = server_client.post(
        "/api/reminders",
        json={"kind": "recurring", "cadence": "PT1H", "anchor_channel_id": build["id"]},
        headers=_agent_headers(seeded_engine, pat["id"]),
    )
    assert r.status_code == 422
    err = rest.ErrorResponse.model_validate(r.json())
    assert err.error.code is rest.ErrorCode.VALIDATION_FAILED and err.error.rule == "D1-L2"
    assert err.error.details == {"missing": ["loop_contract"]}


def test_once_reminder_with_loop_contract_422(
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
            "loop_contract": _loop_contract("PT1H"),
        },
        headers=_agent_headers(seeded_engine, pat["id"]),
    )
    assert r.status_code == 422
    err = rest.ErrorResponse.model_validate(r.json())
    assert err.error.code is rest.ErrorCode.VALIDATION_FAILED


def test_recurring_reminder_cadence_mismatch_422(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """reminders.cadence 与 loop_contract.cadence 不一致 → 422（B §10.6 创建时校验一致）。"""
    pat = _member(server_client, PAT)
    build = _channel(server_client, BUILD_CHANNEL)
    r = server_client.post(
        "/api/reminders",
        json={
            "kind": "recurring",
            "cadence": "PT1H",
            "anchor_channel_id": build["id"],
            "loop_contract": _loop_contract("PT2H"),  # 契约侧不同
        },
        headers=_agent_headers(seeded_engine, pat["id"]),
    )
    assert r.status_code == 422
    err = rest.ErrorResponse.model_validate(r.json())
    assert err.error.code is rest.ErrorCode.VALIDATION_FAILED


def test_recurring_reminder_invalid_cadence_422(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """cadence 既非合法 interval 也非合法 cron（这里 4 段 cron）→ 422（B §11.5 值域单点）。"""
    pat = _member(server_client, PAT)
    build = _channel(server_client, BUILD_CHANNEL)
    r = server_client.post(
        "/api/reminders",
        json={
            "kind": "recurring",
            "cadence": "0 9 * *",  # 4 段：非 interval、非五段 cron
            "anchor_channel_id": build["id"],
            "loop_contract": _loop_contract("0 9 * *"),  # 一致但值域非法
        },
        headers=_agent_headers(seeded_engine, pat["id"]),
    )
    assert r.status_code == 422
    err = rest.ErrorResponse.model_validate(r.json())
    assert err.error.code is rest.ErrorCode.VALIDATION_FAILED
    assert err.error.details == {"field": "cadence"}


def test_recurring_reminder_accepts_cron_cadence(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """recurring 接受 cron 五段式（M5，B §11.5）：201 + next_fire_at 经 cadence 单点算出。"""
    from coagentia_server.reminders import cadence as cadence_svc

    pat = _member(server_client, PAT)
    build = _channel(server_client, BUILD_CHANNEL)
    r = server_client.post(
        "/api/reminders",
        json={
            "kind": "recurring",
            "cadence": "0 9 * * *",  # 每日 09:00（本地时区）
            "anchor_channel_id": build["id"],
            "loop_contract": _loop_contract("0 9 * * *"),
        },
        headers=_agent_headers(seeded_engine, pat["id"]),
    )
    assert r.status_code == 201, r.text
    rem = entities.ReminderPublic.model_validate(r.json())
    assert rem.kind == "recurring" and rem.status == "active"
    assert rem.loop_contract_id is not None
    # 建即算 next_fire_at = 创建时刻之后首个命中；与单点重算逐字节一致（证明端点走单点）。
    assert rem.next_fire_at > rem.created_at
    assert rem.next_fire_at == cadence_svc.initial_fire(rem.created_at, "0 9 * * *")


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


def test_message_files_derived_field_across_read_surfaces(
    server_client: TestClient,
) -> None:
    """契约 A v1.0.4：消息读面自带派生 files（响应/列表/线程/搜索），与 channelFiles
    分页无关——旧文件附件卡不再受首页 ≤50 截断（M2 挂账）。"""
    build = _channel(server_client, BUILD_CHANNEL)
    meta = server_client.post(
        "/api/files", files={"file": ("spec.md", b"# spec", "text/markdown")}
    ).json()

    r = server_client.post(
        f"/api/channels/{build['id']}/messages",
        json={"body": "附件在此 attachprobe", "file_ids": [meta["id"]]},
    )
    assert r.status_code == 201
    created = r.json()["message"]
    assert [f["id"] for f in created["files"]] == [meta["id"]]
    assert "stored_path" not in created["files"][0]  # FilePublic 剔内部列

    # 列表读面：带附件消息 files 非空；无附件消息 files == []（已附着，非 None）
    page = server_client.get(f"/api/channels/{build['id']}/messages").json()
    by_id = {m["id"]: m for m in page["items"]}
    assert [f["id"] for f in by_id[created["id"]]["files"]] == [meta["id"]]
    assert all(m["files"] == [] for m in page["items"] if m["id"] != created["id"])

    # 线程读面：根消息在 thread 端点同样附着
    thread = server_client.get(f"/api/messages/{created['id']}/thread").json()
    assert [f["id"] for f in thread["items"][0]["files"]] == [meta["id"]]

    # 搜索命中读面
    hits = server_client.get("/api/search", params={"q": "attachprobe"}).json()
    assert any(
        [f["id"] for f in h["message"]["files"]] == [meta["id"]]
        for h in hits["messages"]
    )


def test_file_bind_rolls_back_to_staging_when_later_file_is_invalid(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    build = _channel(server_client, BUILD_CHANNEL)
    upload = server_client.post(
        "/api/files", files={"file": ("keep.md", b"keep", "text/markdown")}
    ).json()
    missing_id = "01K0MMBR0000000000000000ZZ"
    response = server_client.post(
        f"/api/channels/{build['id']}/messages",
        json={"body": "must rollback", "file_ids": [upload["id"], missing_id]},
    )
    assert response.status_code == 404

    store = server_client.app.state.file_store
    assert (store.staging_dir / upload["id"]).read_bytes() == b"keep"
    assert (store.staging_dir / f"{upload['id']}.json").is_file()
    assert not (store.files_dir / upload["id"]).exists()
    with seeded_engine.connect() as conn:
        assert conn.execute(
            select(func.count())
            .select_from(models.Message.__table__)
            .where(models.Message.__table__.c.body == "must rollback")
        ).scalar_one() == 0


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


def _insert_held(engine: Engine, *, upload_id: str, status: str) -> str:
    """插一行引用 `upload_id` 的 held 草稿（GC 豁免测试用）；FK 取 seed 已有的 ws/agent/channel。"""
    from coagentia_server.ledger.service import new_ulid, now_iso

    _HELD = models.HeldDraft.__table__
    with engine.begin() as c:
        ws_id = c.execute(select(models.Workspace.__table__.c.id)).scalar_one()
        agent_id = c.execute(select(models.Agent.__table__.c.member_id)).scalars().first()
        channel_id = c.execute(select(models.Channel.__table__.c.id)).scalars().first()
        held_id = new_ulid()
        c.execute(
            insert(_HELD).values(
                id=held_id,
                workspace_id=ws_id,
                agent_member_id=agent_id,
                channel_id=channel_id,
                thread_root_id=None,
                draft_body="草稿",
                file_ids=[upload_id],
                as_task=None,
                reasons={"unread_message_ids": [], "total_unread": 0},
                status=status,
                held_count=1,
                next_reeval_at=now_iso(),
                escalated_at=None,
                created_at=now_iso(),
            )
        )
    return held_id


def _stale_stage(server_client: TestClient, store: FileStore, name: str = "held.md") -> str:
    """上传一个 staging 文件并把 mtime 回拨到 25h 前（超 24h GC 门限）。返回 upload_id。"""
    fid = server_client.post(
        "/api/files", files={"file": (name, b"held-body", "text/markdown")}
    ).json()["id"]
    stale = time.time() - 25 * 3600
    os.utime(store.staging_dir / fid, (stale, stale))
    return fid


def test_gc_exempts_active_held_referenced_staging(
    server_client: TestClient, seeded_engine: Engine, tmp_path: Path
) -> None:
    """契约 D §9.2 v1.0.1：活动 held 行（held/reevaluating）引用的超 24h staging 文件豁免删除。"""
    store = FileStore(tmp_path / "data")
    _HELD = models.HeldDraft.__table__
    for status in ("held", "reevaluating"):
        fid = _stale_stage(server_client, store, name=f"{status}.md")
        held_id = _insert_held(seeded_engine, upload_id=fid, status=status)
        assert run_gc(seeded_engine, store) == 0  # 被活动 held 引用 → 不删
        assert store.is_staged(fid)
        # 清掉活动行（避下轮 uq_held_drafts_active 冲突）+ 其 staging 文件（避成下轮真孤儿）。
        with seeded_engine.begin() as c:
            c.execute(_HELD.delete().where(_HELD.c.id == held_id))
        store.delete_staged(fid)


def test_gc_reclaims_after_held_terminal(
    server_client: TestClient, seeded_engine: Engine, tmp_path: Path
) -> None:
    """held 进终态（discarded）后不再豁免 → 下轮 GC 回收其引用的 staging 文件。"""
    store = FileStore(tmp_path / "data")
    fid = _stale_stage(server_client, store)
    held_id = _insert_held(seeded_engine, upload_id=fid, status="held")
    assert run_gc(seeded_engine, store) == 0 and store.is_staged(fid)  # 活动期豁免

    _HELD = models.HeldDraft.__table__
    with seeded_engine.begin() as c:
        c.execute(update(_HELD).where(_HELD.c.id == held_id).values(status="discarded"))
    assert run_gc(seeded_engine, store) == 1  # 终态后回收
    assert not store.is_staged(fid)


# ---------------------------------------------------------------- 路由回归（code-review #4/#8/#10）


def test_open_dm_with_self_rejected(server_client: TestClient) -> None:
    """#4：与自己建 DM → 422 而非 PK 冲突 500。"""
    owner = _member(server_client, "Memcyo")
    r = server_client.post("/api/dms", json={"member_id": owner["id"]})
    assert r.status_code == 422
    assert (
        rest.ErrorResponse.model_validate(r.json()).error.code
        is rest.ErrorCode.VALIDATION_FAILED
    )


def test_open_dm_nonexistent_member_404(server_client: TestClient) -> None:
    """#4：member_id 指向不存在成员 → 404 而非 FK 500。"""
    r = server_client.post("/api/dms", json={"member_id": "01K0MMBR0000000000000000ZZ"})
    assert r.status_code == 404
    assert rest.ErrorResponse.model_validate(r.json()).error.code is rest.ErrorCode.NOT_FOUND


def test_agent_impersonation_without_bearer_is_rejected(server_client: TestClient) -> None:
    pat = _member(server_client, PAT)
    build = _channel(server_client, BUILD_CHANNEL)
    r = server_client.post(
        f"/api/channels/{build['id']}/messages",
        json={"body": "forged"},
        headers={"X-Acting-Member": pat["id"]},
    )
    assert r.status_code == 403
    assert (
        rest.ErrorResponse.model_validate(r.json()).error.code
        is rest.ErrorCode.PERMISSION_DENIED
    )


def test_authenticated_agent_is_attributed(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    pat = _member(server_client, PAT)
    build = _channel(server_client, BUILD_CHANNEL)
    r = server_client.post(
        f"/api/channels/{build['id']}/messages",
        json={"body": "signed"},
        headers=_agent_headers(seeded_engine, pat["id"]),
    )
    assert r.status_code == 201
    assert r.json()["message"]["author_member_id"] == pat["id"]


def test_removed_acting_member_is_rejected(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """已删除 Agent 即使带原 Computer key 也不能行为，更不能静默回退 Owner。"""
    pat = _member(server_client, PAT)
    headers = _agent_headers(seeded_engine, pat["id"])
    assert server_client.delete(f"/api/agents/{pat['id']}").status_code == 204
    build = _channel(server_client, BUILD_CHANNEL)
    r = server_client.post(
        f"/api/channels/{build['id']}/messages",
        json={"body": "ghost?"},
        headers=headers,
    )
    assert r.status_code == 403


def test_message_rejects_unpaired_surrogate(server_client: TestClient) -> None:
    build = _channel(server_client, BUILD_CHANNEL)
    r = server_client.post(
        f"/api/channels/{build['id']}/messages",
        content=b'{"body":"bad\\ud800"}',
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 422
    assert (
        rest.ErrorResponse.model_validate(r.json()).error.code
        is rest.ErrorCode.VALIDATION_FAILED
    )


def test_server_serves_spa_and_keeps_api_routes(
    seeded_engine: Engine, tmp_path: Path
) -> None:
    dist = tmp_path / "web-dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<h1>CoAgentia test</h1>", encoding="utf-8")
    (assets / "app.js").write_text("export {};", encoding="utf-8")
    app = create_app(
        engine=seeded_engine,
        data_root=tmp_path / "static-data",
        web_dist=dist,
    )
    with TestClient(app) as client:
        assert "CoAgentia test" in client.get("/").text
        assert "CoAgentia test" in client.get("/computers").text
        assert client.get("/api/workspace").headers["content-type"].startswith(
            "application/json"
        )
        assert client.get("/assets/app.js").headers["content-type"].startswith(
            "application/javascript"
        )


def test_delete_channel_with_messages_conflicts(server_client: TestClient) -> None:
    """#10：含消息的频道硬删 → 409 CHANNEL_NOT_EMPTY 而非 FK 500。"""
    build = _channel(server_client, BUILD_CHANNEL)
    server_client.post(f"/api/channels/{build['id']}/messages", json={"body": "留痕"})
    r = server_client.delete(f"/api/channels/{build['id']}")
    assert r.status_code == 409
    assert (
        rest.ErrorResponse.model_validate(r.json()).error.code
        is rest.ErrorCode.CHANNEL_NOT_EMPTY
    )


def test_delete_empty_channel_succeeds(server_client: TestClient) -> None:
    """#10 反向：无消息的频道仍可硬删（不误伤空频道）。"""
    r = server_client.post("/api/channels", json={"name": "temp-empty", "member_ids": []})
    assert r.status_code == 201
    cid = r.json()["id"]
    assert server_client.delete(f"/api/channels/{cid}").status_code == 204


def test_post_message_bad_thread_root_id_404_not_500(server_client: TestClient) -> None:
    """坏 thread_root_id（非消息 id）→ 404 而非 messages FK 500（A8 live 实测暴露）。"""
    build = _channel(server_client, BUILD_CHANNEL)
    pat = _member(server_client, PAT)
    r = server_client.post(
        f"/api/channels/{build['id']}/messages",
        json={"body": "reply", "thread_root_id": pat["id"]},  # member id 冒充 message id
    )
    assert r.status_code == 404
    assert rest.ErrorResponse.model_validate(r.json()).error.code is rest.ErrorCode.NOT_FOUND


def test_post_message_nested_thread_rejected(server_client: TestClient) -> None:
    """thread_root_id 指向非顶级消息 → 422 NOT_TOP_LEVEL_MESSAGE（契约 A：线程不可嵌套）。"""
    build = _channel(server_client, BUILD_CHANNEL)
    root = server_client.post(
        f"/api/channels/{build['id']}/messages", json={"body": "root"}
    ).json()["message"]
    reply = server_client.post(
        f"/api/channels/{build['id']}/messages",
        json={"body": "reply", "thread_root_id": root["id"]},
    ).json()["message"]
    r = server_client.post(  # 对非顶级 reply 再挂线程 → 拒绝
        f"/api/channels/{build['id']}/messages",
        json={"body": "nested", "thread_root_id": reply["id"]},
    )
    assert r.status_code == 422
    assert (
        rest.ErrorResponse.model_validate(r.json()).error.code
        is rest.ErrorCode.NOT_TOP_LEVEL_MESSAGE
    )
