"""契约域真 server 专属测试（M3a E2/E3）：提交/修订链、kind↔schema 校验、request-draft 直投、
T7 流转门（HANDOFF_INCOMPLETE）、P-2 升格（level l1→l2）。

mock 无业务逻辑（纪律 4），本文件全部用例只在真 server 断言；形状双跑
（GET /tasks/{id}/contracts 恒空）见 test_conformance_dual.py。
"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
from coagentia_contracts import rest
from coagentia_contracts.enums import ReviewVerdict
from coagentia_contracts.ws import EventType
from coagentia_server.computers import DaemonOffline
from coagentia_server.db import models
from coagentia_server.events import PendingEvent
from coagentia_server.ledger import service as ledger_service
from fastapi.testclient import TestClient
from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

BUILD = "build"
AGENT_TEST_KEY = "cak_rest_agent_test"


# ---------------------------------------------------------------- 取数助手


def _agent_headers(engine: Engine, member_id: str) -> dict[str, str]:
    """给 seed Agent 所属 Computer 注入已知测试 key，返回契约 B §2 双头（同 test_tasks）。"""
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
    return {"Authorization": f"Bearer {AGENT_TEST_KEY}", "X-Acting-Member": member_id}


def _channel(client: TestClient, name: str) -> dict:
    return next(c for c in client.get("/api/channels").json()["items"] if c["name"] == name)


def _member(client: TestClient, name: str) -> dict:
    return next(m for m in client.get("/api/members").json() if m["name"] == name)


def _new_task(client: TestClient, channel_id: str, title: str = "t") -> dict:
    r = client.post(
        f"/api/channels/{channel_id}/messages", json={"body": "b", "as_task": {"title": title}}
    )
    assert r.status_code == 201, r.text
    return r.json()["task"]


def _task_plan_body(goal: str = "让用户一键导出报表") -> dict[str, Any]:
    return {
        "goal": goal,
        "acceptance_criteria": [
            {
                "id": "ac1",
                "statement": "点击导出按钮生成 CSV",
                "verify_by": "command",
                "verify_ref": "pytest tests/test_export.py",
            }
        ],
        "defaults_decided": [],
        "out_of_scope": [],
    }


def _handoff_body(
    *,
    from_member: str,
    to_member: str,
    deliverables: list[dict[str, Any]] | None = None,
    evidence: list[dict[str, Any]] | None = None,
    review_verdict: str | None = None,
) -> dict[str, Any]:
    body = {
        "from_member": from_member,
        "to_member": to_member,
        "deliverables": deliverables or [],
        "evidence": evidence or [],
        "open_risks": [],
        "verify_plan": "复核方式：按验收标准逐条跑一遍命令",
    }
    if review_verdict is not None:
        body["review_verdict"] = review_verdict
    return body


def _add_channel_human(engine: Engine, *, workspace_id: str, channel_id: str, name: str) -> str:
    member_id = ledger_service.new_ulid()
    with engine.begin() as conn:
        conn.execute(
            insert(models.Member.__table__).values(
                id=member_id,
                workspace_id=workspace_id,
                kind="human",
                name=name,
                role="member",
                removed_at=None,
                created_at=ledger_service.now_iso(),
            )
        )
        conn.execute(
            insert(models.ChannelMember.__table__).values(
                channel_id=channel_id,
                member_id=member_id,
                joined_at=ledger_service.now_iso(),
            )
        )
    return member_id


def _set_status(client: TestClient, task_id: str, to: str, headers: dict[str, str] | None = None):
    return client.post(f"/api/tasks/{task_id}/status", json={"to": to}, headers=headers)


# ---------------------------------------------------------------- 提交 + 修订链


def test_submit_and_revision_chain(server_client: TestClient) -> None:
    client = server_client
    build = _channel(client, BUILD)
    task = _new_task(client, build["id"])

    r1 = client.post(
        f"/api/tasks/{task['id']}/contracts",
        json={"kind": "task_plan", "body": _task_plan_body("目标 A")},
    )
    assert r1.status_code == 201, r1.text
    c1 = r1.json()
    assert c1["revision"] == 1
    assert c1["superseded_at"] is None
    assert c1["kind"] == "task_plan"
    assert c1["task_id"] == task["id"]
    assert c1["version"] == "coagentia.task-plan.v1"

    r2 = client.post(
        f"/api/tasks/{task['id']}/contracts",
        json={"kind": "task_plan", "body": _task_plan_body("目标 B（修订）")},
    )
    assert r2.status_code == 201, r2.text
    c2 = r2.json()
    assert c2["revision"] == 2
    assert c2["superseded_at"] is None
    assert c2["id"] != c1["id"]

    rows = client.get(f"/api/tasks/{task['id']}/contracts").json()
    assert len(rows) == 2
    active = [r for r in rows if r["superseded_at"] is None]
    assert len(active) == 1, rows  # 恰一活动行
    assert active[0]["id"] == c2["id"]
    superseded = [r for r in rows if r["superseded_at"] is not None]
    assert len(superseded) == 1
    assert superseded[0]["id"] == c1["id"]
    assert superseded[0]["revision"] == 1


@pytest.mark.parametrize("verdict", [item.value for item in ReviewVerdict])
def test_handoff_review_verdict_roundtrips_on_submit_and_revision(
    server_client: TestClient, seeded_engine: Engine, verdict: str
) -> None:
    """B §12.10：四值经真提交、修订、REST 回读与 JSON 列持久化均原样保留。"""
    build = _channel(server_client, BUILD)
    task = _new_task(server_client, build["id"], f"verdict-{verdict}")
    owner = _member(server_client, "Memcyo")
    pat = _member(server_client, "Pat")
    body = _handoff_body(from_member=pat["id"], to_member=owner["id"], review_verdict=verdict)

    first = server_client.post(
        f"/api/tasks/{task['id']}/contracts", json={"kind": "task_handoff", "body": body}
    )
    assert first.status_code == 201, first.text
    assert first.json()["body"]["review_verdict"] == verdict

    revised_body = {**body, "verify_plan": f"{body['verify_plan']}（修订）"}
    revised = server_client.post(
        f"/api/tasks/{task['id']}/contracts",
        json={"kind": "task_handoff", "body": revised_body},
    )
    assert revised.status_code == 201, revised.text
    assert revised.json()["revision"] == 2
    assert revised.json()["body"]["review_verdict"] == verdict

    rows = server_client.get(f"/api/tasks/{task['id']}/contracts").json()
    assert [row["body"]["review_verdict"] for row in rows] == [verdict, verdict]
    with seeded_engine.connect() as conn:
        stored = list(
            conn.execute(
                select(models.TaskContract.__table__.c.body)
                .where(models.TaskContract.__table__.c.task_id == task["id"])
                .order_by(models.TaskContract.__table__.c.revision)
            ).scalars()
        )
    assert [item["review_verdict"] for item in stored] == [verdict, verdict]


def test_handoff_review_verdict_missing_and_legacy_json_remain_readable(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """可选字段向后兼容：缺省提交为 null；旧 JSON 完全缺键时读取与 T7 消费均不崩。"""
    build = _channel(server_client, BUILD)
    task = _new_task(server_client, build["id"], "legacy-handoff")
    owner = _member(server_client, "Memcyo")
    pat = _member(server_client, "Pat")
    complete = _handoff_body(
        from_member=pat["id"],
        to_member=owner["id"],
        deliverables=[{"path": "D:/artifact.txt", "kind": "file"}],
        evidence=[{"type": "test", "ref": "pytest -q", "conclusion": "passed"}],
    )
    posted = server_client.post(
        f"/api/tasks/{task['id']}/contracts",
        json={"kind": "task_handoff", "body": complete},
    )
    assert posted.status_code == 201, posted.text
    assert posted.json()["body"]["review_verdict"] is None

    legacy_body = dict(posted.json()["body"])
    legacy_body.pop("review_verdict")
    with seeded_engine.begin() as conn:
        conn.execute(
            update(models.TaskContract.__table__)
            .where(models.TaskContract.__table__.c.id == posted.json()["id"])
            .values(body=legacy_body)
        )
    active = server_client.get(f"/api/tasks/{task['id']}/contracts").json()[0]
    assert "review_verdict" not in active["body"]
    # L2 的 T7 门仍只检查 deliverables/evidence，旧 handoff 可正常消费。
    assert server_client.patch(f"/api/tasks/{task['id']}", json={"level": "l2"}).status_code == 200
    assert _set_status(server_client, task["id"], "in_progress").status_code == 200
    assert _set_status(server_client, task["id"], "in_review").status_code == 200


def test_needs_human_notifies_all_channel_humans_once_per_material_revision(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """needs_human 落任务线程系统消息、仅 @人类并广播；相同重试不重复喊人。"""
    build = _channel(server_client, BUILD)
    task = _new_task(server_client, build["id"], "需要架构裁决")
    owner = _member(server_client, "Memcyo")
    pat = _member(server_client, "Pat")
    zoe = _add_channel_human(
        seeded_engine,
        workspace_id=task["workspace_id"],
        channel_id=build["id"],
        name="Zoe",
    )
    body = _handoff_body(
        from_member=pat["id"],
        to_member=owner["id"],
        review_verdict=ReviewVerdict.NEEDS_HUMAN.value,
    )
    events: list[PendingEvent] = []
    server_client.app.state.bus.subscribe(events.append)

    first = server_client.post(
        f"/api/tasks/{task['id']}/contracts", json={"kind": "task_handoff", "body": body}
    )
    assert first.status_code == 201, first.text

    msg = models.Message.__table__
    mention = models.MessageMention.__table__

    def notices() -> list[dict[str, Any]]:
        with seeded_engine.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    select(msg).where(
                        msg.c.thread_root_id == task["root_message_id"],
                        msg.c.kind == "system",
                        msg.c.body.like("评审需人裁决：%"),
                    )
                ).mappings()
            ]

    first_notices = notices()
    assert len(first_notices) == 1
    assert f"task #{task['number']}" in first_notices[0]["body"]
    assert task["title"] in first_notices[0]["body"]
    assert "review_verdict=needs_human" in first_notices[0]["body"]
    with seeded_engine.connect() as conn:
        mention_ids = set(
            conn.execute(
                select(mention.c.member_id).where(mention.c.message_id == first_notices[0]["id"])
            ).scalars()
        )
        agent_mentions = conn.execute(
            select(func.count())
            .select_from(
                mention.join(
                    models.Member.__table__, mention.c.member_id == models.Member.__table__.c.id
                )
            )
            .where(
                mention.c.message_id == first_notices[0]["id"],
                models.Member.__table__.c.kind == "agent",
            )
        ).scalar_one()
    assert mention_ids == {owner["id"], zoe}
    assert agent_mentions == 0
    broadcasts = [
        event
        for event in events
        if event.type is EventType.MESSAGE_CREATED
        and event.data["message"]["id"] == first_notices[0]["id"]
    ]
    assert len(broadcasts) == 1

    # 客户端丢响应后按同 payload 重试：虽形成既有修订链，但不重复喊人。
    duplicate = server_client.post(
        f"/api/tasks/{task['id']}/contracts", json={"kind": "task_handoff", "body": body}
    )
    assert duplicate.status_code == 201 and duplicate.json()["revision"] == 2
    assert len(notices()) == 1

    # 真正修改 handoff 后仍为 needs_human：这是新裁决请求，应再落一次通知。
    changed = {**body, "open_risks": ["需要 owner 在两个方案间拍板"]}
    revision = server_client.post(
        f"/api/tasks/{task['id']}/contracts",
        json={"kind": "task_handoff", "body": changed},
    )
    assert revision.status_code == 201 and revision.json()["revision"] == 3
    assert len(notices()) == 2


def test_task_detail_reflects_active_contracts(server_client: TestClient) -> None:
    client = server_client
    build = _channel(client, BUILD)
    task = _new_task(client, build["id"])
    client.post(
        f"/api/tasks/{task['id']}/contracts",
        json={"kind": "task_plan", "body": _task_plan_body()},
    )
    detail = client.get(f"/api/tasks/{task['id']}").json()
    assert len(detail["contracts"]) == 1
    assert detail["contracts"][0]["kind"] == "task_plan"


# ---------------------------------------------------------------- kind≠schema 拒


def test_kind_schema_mismatch_rejected(server_client: TestClient) -> None:
    client = server_client
    build = _channel(client, BUILD)
    task = _new_task(client, build["id"])
    owner = _member(client, "Memcyo")
    pat = _member(client, "Pat")

    # kind=task_plan 但 body 是 handoff 形状（缺 goal/acceptance_criteria）
    r = client.post(
        f"/api/tasks/{task['id']}/contracts",
        json={
            "kind": "task_plan",
            "body": _handoff_body(from_member=pat["id"], to_member=owner["id"]),
        },
    )
    assert r.status_code == 422, r.text
    err = rest.ErrorResponse.model_validate(r.json())
    assert err.error.code is rest.ErrorCode.VALIDATION_FAILED
    assert err.error.details is not None


# ---------------------------------------------------------------- request-draft 直投


class _SpyHub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def inject_contract_draft_request(
        self, *, agent_member_id: str, task_id: str, kind: Any
    ) -> str:
        kind_value = kind.value if hasattr(kind, "value") else kind
        self.calls.append((agent_member_id, task_id, kind_value))
        return "done"


class _OfflineHub:
    def inject_contract_draft_request(
        self, *, agent_member_id: str, task_id: str, kind: Any
    ) -> str:
        raise DaemonOffline("离线桩：无活跃 daemon 连接")


def test_request_draft_injects_frame(server_client: TestClient) -> None:
    client = server_client
    build = _channel(client, BUILD)
    task = _new_task(client, build["id"])
    pat = _member(client, "Pat")

    spy = _SpyHub()
    client.app.state.daemon_hub = spy
    r = client.post(
        f"/api/tasks/{task['id']}/contracts/request-draft",
        json={"kind": "task_plan", "agent_member_id": pat["id"]},
    )
    assert r.status_code == 202, r.text
    assert spy.calls == [(pat["id"], task["id"], "task_plan")]


def test_request_draft_daemon_offline(server_client: TestClient) -> None:
    client = server_client
    build = _channel(client, BUILD)
    task = _new_task(client, build["id"])
    pat = _member(client, "Pat")

    client.app.state.daemon_hub = _OfflineHub()
    r = client.post(
        f"/api/tasks/{task['id']}/contracts/request-draft",
        json={"kind": "task_plan", "agent_member_id": pat["id"]},
    )
    assert r.status_code == 503, r.text
    err = rest.ErrorResponse.model_validate(r.json())
    assert err.error.code is rest.ErrorCode.DAEMON_OFFLINE


def test_request_draft_unknown_agent_404(server_client: TestClient) -> None:
    client = server_client
    build = _channel(client, BUILD)
    task = _new_task(client, build["id"])
    r = client.post(
        f"/api/tasks/{task['id']}/contracts/request-draft",
        json={"kind": "task_plan", "agent_member_id": "01K0MMBR000000000000009999"},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------- T7 流转门（HANDOFF_INCOMPLETE）


def _l2_in_progress_task(client: TestClient, build_channel_id: str) -> dict:
    """建任务 → 升格 l1→l2（P-2）→ 流转到 in_progress（T7 只在 to==in_review 时触发）。"""
    task = _new_task(client, build_channel_id)
    r = client.patch(f"/api/tasks/{task['id']}", json={"level": "l2"})
    assert r.status_code == 200, r.text
    assert r.json()["level"] == "l2"
    r = _set_status(client, task["id"], "in_progress")
    assert r.status_code == 200, r.text
    return task


def test_t7_blocks_in_review_without_handoff(server_client: TestClient) -> None:
    client = server_client
    build = _channel(client, BUILD)
    task = _l2_in_progress_task(client, build["id"])

    r = _set_status(client, task["id"], "in_review")
    assert r.status_code == 422, r.text
    err = rest.ErrorResponse.model_validate(r.json())
    assert err.error.code is rest.ErrorCode.HANDOFF_INCOMPLETE
    assert err.error.rule == "T7"
    assert err.error.details["missing"] == ["deliverables", "evidence"]
    # B5：422 携补齐 hint（指向 submit_task_contract 工具，Agent 据此自愈）
    assert "submit_task_contract" in err.error.details["hint"]


def test_t7_blocks_in_review_with_partial_handoff(server_client: TestClient) -> None:
    client = server_client
    build = _channel(client, BUILD)
    task = _l2_in_progress_task(client, build["id"])
    owner = _member(client, "Memcyo")
    pat = _member(client, "Pat")

    body = _handoff_body(
        from_member=pat["id"],
        to_member=owner["id"],
        deliverables=[{"path": "/tmp/report.csv", "kind": "file"}],
        evidence=[],  # 故意留空
    )
    r = client.post(
        f"/api/tasks/{task['id']}/contracts", json={"kind": "task_handoff", "body": body}
    )
    assert r.status_code == 201, r.text

    r = _set_status(client, task["id"], "in_review")
    assert r.status_code == 422, r.text
    err = rest.ErrorResponse.model_validate(r.json())
    assert err.error.code is rest.ErrorCode.HANDOFF_INCOMPLETE
    assert err.error.details["missing"] == ["evidence"]
    assert "submit_task_contract" in err.error.details["hint"]


def test_t7_allows_in_review_with_complete_handoff(server_client: TestClient) -> None:
    client = server_client
    build = _channel(client, BUILD)
    task = _l2_in_progress_task(client, build["id"])
    owner = _member(client, "Memcyo")
    pat = _member(client, "Pat")

    body = _handoff_body(
        from_member=pat["id"],
        to_member=owner["id"],
        deliverables=[{"path": "/tmp/report.csv", "kind": "file"}],
        evidence=[{"type": "test", "ref": "pytest -q", "conclusion": "全绿"}],
    )
    r = client.post(
        f"/api/tasks/{task['id']}/contracts", json={"kind": "task_handoff", "body": body}
    )
    assert r.status_code == 201, r.text

    r = _set_status(client, task["id"], "in_review")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "in_review"


def test_t7_l1_task_bypasses_gate(server_client: TestClient) -> None:
    """l1 任务（M2 存量全 l1）不进 T7 分支——零回归。"""
    client = server_client
    build = _channel(client, BUILD)
    task = _new_task(client, build["id"])
    r = _set_status(client, task["id"], "in_progress")
    assert r.status_code == 200, r.text
    r = _set_status(client, task["id"], "in_review")
    assert r.status_code == 200, r.text  # 无契约也直通


def test_t7_blocks_agent_actor_same_as_human(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """Agent 主体（X-Acting-Member）经 status 端点同样被 T7 拒——同端点自动同规则。"""
    client = server_client
    build = _channel(client, BUILD)
    task = _l2_in_progress_task(client, build["id"])
    pat = _member(client, "Pat")
    headers = _agent_headers(seeded_engine, pat["id"])

    r = _set_status(client, task["id"], "in_review", headers=headers)
    assert r.status_code == 422, r.text
    err = rest.ErrorResponse.model_validate(r.json())
    assert err.error.code is rest.ErrorCode.HANDOFF_INCOMPLETE
    assert err.error.rule == "T7"


# ---------------------------------------------------------------- P-2 升格


def test_level_upgrade_l1_to_l2(server_client: TestClient) -> None:
    client = server_client
    build = _channel(client, BUILD)
    task = _new_task(client, build["id"])
    assert task["level"] == "l1"

    r = client.patch(f"/api/tasks/{task['id']}", json={"level": "l2"})
    assert r.status_code == 200, r.text
    assert r.json()["level"] == "l2"

    final = client.get(f"/api/tasks/{task['id']}").json()["task"]
    assert final["level"] == "l2"


def test_level_downgrade_rejected(server_client: TestClient) -> None:
    client = server_client
    build = _channel(client, BUILD)
    task = _new_task(client, build["id"])
    client.patch(f"/api/tasks/{task['id']}", json={"level": "l2"})

    r = client.patch(f"/api/tasks/{task['id']}", json={"level": "l1"})
    assert r.status_code == 422, r.text
    err = rest.ErrorResponse.model_validate(r.json())
    assert err.error.code is rest.ErrorCode.TASK_TRANSITION_INVALID
    assert err.error.rule == "D1"
    assert err.error.details == {"from": "l2", "to": "l1"}

    # 拒绝不改变库内 level（仍 l2）
    final = client.get(f"/api/tasks/{task['id']}").json()["task"]
    assert final["level"] == "l2"


def test_level_idempotent_same_value(server_client: TestClient) -> None:
    client = server_client
    build = _channel(client, BUILD)
    task = _new_task(client, build["id"])

    r = client.patch(f"/api/tasks/{task['id']}", json={"level": "l1"})  # l1->l1
    assert r.status_code == 200, r.text
    assert r.json()["level"] == "l1"

    client.patch(f"/api/tasks/{task['id']}", json={"level": "l2"})
    r = client.patch(f"/api/tasks/{task['id']}", json={"level": "l2"})  # l2->l2
    assert r.status_code == 200, r.text
    assert r.json()["level"] == "l2"


# ---------------------------------------------------------------- review 修复回归


def test_loop_contract_kind_rejected_on_task(server_client: TestClient) -> None:
    """loop_contract 属 Reminder 域（M4），不可挂 Task——POST /tasks/{id}/contracts 端点门拒。"""
    client = server_client
    build = _channel(client, BUILD)
    task = _new_task(client, build["id"])
    r = client.post(
        f"/api/tasks/{task['id']}/contracts",
        json={
            "kind": "loop_contract",
            "body": {
                "cadence": "0 9 * * *",
                "verification": ["每次校验输出非空"],
                "budget": {"max_retries": 1, "max_runtime_min": 30},
                "tools": [],
                "escalation": "无进展则拉人",
            },
        },
    )
    assert r.status_code == 422, r.text
    err = rest.ErrorResponse.model_validate(r.json())
    assert err.error.code is rest.ErrorCode.VALIDATION_FAILED
    assert err.error.details == {"kind": "loop_contract"}


def test_promotion_to_l2_while_in_review_requires_handoff(server_client: TestClient) -> None:
    """T7 不变量守护：l1 任务先置 in_review（l1 无 T7）再升 l2，不得绕过 handoff 完备性。"""
    client = server_client
    build = _channel(client, BUILD)
    task = _new_task(client, build["id"])
    # l1 → in_progress → in_review（l1 直通，无 T7）
    assert _set_status(client, task["id"], "in_progress").status_code == 200
    assert _set_status(client, task["id"], "in_review").status_code == 200
    # 此刻升 l2：任务已 in_review 且无 handoff → 被 T7 守护拒（否则造出 l2+in_review 无交接态）
    r = client.patch(f"/api/tasks/{task['id']}", json={"level": "l2"})
    assert r.status_code == 422, r.text
    err = rest.ErrorResponse.model_validate(r.json())
    assert err.error.code is rest.ErrorCode.HANDOFF_INCOMPLETE
    assert err.error.rule == "T7"
    assert err.error.details["missing"] == ["deliverables", "evidence"]
    assert "submit_task_contract" in err.error.details["hint"]
    # 拒绝不改库内 level（仍 l1）
    assert client.get(f"/api/tasks/{task['id']}").json()["task"]["level"] == "l1"


def test_promotion_to_l2_while_in_review_allowed_with_handoff(server_client: TestClient) -> None:
    """对照组：in_review 任务若已有齐备 handoff，升 l2 放行。"""
    client = server_client
    build = _channel(client, BUILD)
    task = _new_task(client, build["id"])
    owner = _member(client, "Memcyo")
    pat = _member(client, "Pat")
    client.post(
        f"/api/tasks/{task['id']}/contracts",
        json={
            "kind": "task_handoff",
            "body": _handoff_body(
                from_member=pat["id"],
                to_member=owner["id"],
                deliverables=[{"path": "/tmp/r.csv", "kind": "file"}],
                evidence=[{"type": "test", "ref": "pytest", "conclusion": "绿"}],
            ),
        },
    )
    assert _set_status(client, task["id"], "in_progress").status_code == 200
    assert _set_status(client, task["id"], "in_review").status_code == 200
    r = client.patch(f"/api/tasks/{task['id']}", json={"level": "l2"})
    assert r.status_code == 200, r.text
    assert r.json()["level"] == "l2"


def test_duplicate_active_contract_rejected_by_db(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """修订链 DB 兜底：同 (task_id, kind) 第二个活动行（superseded_at IS NULL）被分区唯一索引拒。

    杜绝并发提交造出两活动行、令 T7 门读到不确定的活动 handoff（review 修复的机读守门）。
    """
    client = server_client
    build = _channel(client, BUILD)
    task = _new_task(client, build["id"])
    owner = _member(client, "Memcyo")
    tc = models.TaskContract.__table__

    def _row(row_id: str) -> dict[str, Any]:
        return {
            "id": row_id,
            "workspace_id": task["workspace_id"],
            "task_id": task["id"],
            "reminder_id": None,
            "kind": "task_plan",
            "version": "coagentia.task-plan.v1",
            "body": _task_plan_body(),
            "revision": 1,
            "superseded_at": None,
            "created_by_member_id": owner["id"],
            "created_at": "2026-07-10T00:00:00.000Z",
        }

    with seeded_engine.begin() as conn:
        conn.execute(tc.insert().values(_row("01KX000000000000000000001A")))
    with pytest.raises(IntegrityError):  # noqa: PT012
        with seeded_engine.begin() as conn:  # 第二个活动同 (task_id, kind) → 违反分区唯一
            conn.execute(tc.insert().values(_row("01KX000000000000000000001B")))
