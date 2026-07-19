"""mock server 契约一致性测试（M4 验证；DEDAG v1.6 升级为全清单对照）：

1. OpenAPI 恰好 serve 契约 B 端点清单全集（ENDPOINTS_M1..M7/PSWT/DEDAG，不多不少）；
2. 每个读端点的响应能被 contracts 响应模型反向校验；
3. 代表性拒绝路径的错误形状（TASK_IN_DM 等）；
4. WS：hello / ping-pong / 信封校验 / seq 单调 / 写端点广播；
5. 时间线回放：事件到达且 REST 状态同步（契约 C 铁律 1）。
"""

from typing import Any

import pytest
from coagentia_contracts import entities, rest, ws
from coagentia_mock.app import app
from fastapi.testclient import TestClient

# 契约端点清单全集（DEDAG v1.6 后的现行面）——mock 必须 serve 全集、不多不少。
ALL_ENDPOINTS: tuple[tuple[str, str], ...] = (
    rest.ENDPOINTS_M1 + rest.ENDPOINTS_M2 + rest.ENDPOINTS_M3 + rest.ENDPOINTS_M4
    + rest.ENDPOINTS_M5 + rest.ENDPOINTS_M6 + rest.ENDPOINTS_M7
    + rest.ENDPOINTS_PSWT + rest.ENDPOINTS_DEDAG
)


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def test_openapi_serves_contract_endpoints_exactly(client: TestClient) -> None:
    """清单全集对照（不多不少）：/__mock/* 控制面与 WS 不属契约，不参与对照。"""
    spec = client.get("/openapi.json").json()
    served = {
        (method.upper(), normalize(path.removeprefix("/api")))
        for path, methods in spec["paths"].items()
        if path.startswith("/api")
        for method in methods
    }
    expected = {(m, normalize(p)) for m, p in ALL_ENDPOINTS}
    missing = sorted(expected - served)
    extra = sorted(served - expected)
    assert not missing, f"mock 未实现的契约端点: {missing}"
    assert not extra, f"mock 多出清单外端点（退役域残留?）: {extra}"


def normalize(path: str) -> str:
    """路径参数名不参与对照（{id} 与 {computer_id} 等价）。"""
    import re

    return re.sub(r"\{[^}]+\}", "{}", path)


def test_read_endpoints_validate_against_contracts(client: TestClient) -> None:
    entities.WorkspacePublic.model_validate(client.get("/api/workspace").json())
    for c in client.get("/api/computers").json():
        entities.ComputerPublic.model_validate(c)
        assert "api_key_hash" not in c  # 敏感列剔除（契约 A §8.2）
    for m in client.get("/api/members").json():
        entities.MemberPublic.model_validate(m)
    rest.PresenceSnapshot.model_validate(client.get("/api/presence").json())
    snapshot = rest.ChannelsSnapshot.model_validate(client.get("/api/channels").json())
    assert snapshot.read_positions, "自身 read-position 随 GET /channels 附带（契约 B §6）"
    channels = client.get("/api/channels").json()["items"]
    build = next(c for c in channels if c["name"] == "build")
    page = client.get(f"/api/channels/{build['id']}/messages").json()
    assert page["items"], "P1 消息流不能为空"
    for msg in page["items"]:
        entities.MessagePublic.model_validate(msg)
    for t in client.get("/api/tasks", params={"channel_id": build["id"]}).json()["items"]:
        entities.TaskPublic.model_validate(t)


def test_held_drafts_list_shape(client: TestClient) -> None:
    """M4 护栏被扣草稿清单（§4.14）：mock 形状源回空页，Page[HeldDraftPublic] 反向校验。"""
    page = rest.Page[entities.HeldDraftPublic].model_validate(
        client.get("/api/held-drafts", params={"status": "held"}).json()
    )
    assert page.items == []


def test_held_draft_intervention_shapes(client: TestClient) -> None:
    """M4 三键干预（§4.14）：release/discard/reevaluate 的响应形状反向校验。"""
    held_id = "0" * 26  # Ulid 模式合法即可（mock 形状源不校验存在性）
    released = rest.HeldDraftReleaseResponse.model_validate(
        client.post(f"/api/held-drafts/{held_id}/release").json()
    )
    assert released.held_draft.status == "released"
    discarded = rest.HeldDraftResponse.model_validate(
        client.post(f"/api/held-drafts/{held_id}/discard").json()
    )
    assert discarded.held_draft.status == "discarded"
    reevaluating = rest.HeldDraftResponse.model_validate(
        client.post(f"/api/held-drafts/{held_id}/reevaluate").json()
    )
    assert reevaluating.held_draft.status == "reevaluating"


def test_task_contract_write_shapes(client: TestClient) -> None:
    """M3 契约提交/请求起草/force-start：mock 形状源的写端点响应反向校验。"""
    task = client.get("/api/tasks").json()["items"][0]
    r = client.post(f"/api/tasks/{task['id']}/contracts",
                    json={"kind": "task_plan", "body": {"goal": "x"}})
    assert r.status_code == 201
    contract = entities.TaskContractPublic.model_validate(r.json())
    assert contract.task_id == task["id"] and contract.version == "coagentia.task-plan.v1"
    agent = next(m for m in client.get("/api/members").json() if m["kind"] == "agent")
    r = client.post(f"/api/tasks/{task['id']}/contracts/request-draft",
                    json={"kind": "task_plan", "agent_member_id": agent["id"]})
    assert r.status_code == 202 and r.json() == {"status": "accepted"}
    entities.TaskPublic.model_validate(
        client.post(f"/api/tasks/{task['id']}/force-start").json()
    )


def test_task_merge_accepted_shape(client: TestClient) -> None:
    """DEDAG 任务级 merge（B v1.6 §14）：202 受理形状；任务不存在 404 NOT_FOUND。"""
    task = client.get("/api/tasks").json()["items"][0]
    r = client.post(f"/api/tasks/{task['id']}/merge")
    assert r.status_code == 202
    accepted = rest.TaskMergeAccepted.model_validate(r.json())
    assert accepted.task_id == task["id"] and accepted.status == "accepted"
    missing = client.post(f"/api/tasks/{'0' * 26}/merge")
    assert missing.status_code == 404
    err = rest.ErrorResponse.model_validate(missing.json())
    assert err.error.code is rest.ErrorCode.NOT_FOUND


def test_agent_detail_shapes(client: TestClient) -> None:
    members = client.get("/api/members").json()
    pat = next(m for m in members if m["name"] == "Pat")
    entities.AgentPublic.model_validate(client.get(f"/api/agents/{pat['id']}").json())
    tree = client.get(f"/api/agents/{pat['id']}/home/tree").json()
    assert {e["name"] for e in tree["entries"]} >= {"MEMORY.md", "notes"}


def test_error_shape_task_in_dm(client: TestClient) -> None:
    dm = next(c for c in client.get("/api/channels").json()["items"] if c["kind"] == "dm")
    r = client.post(f"/api/channels/{dm['id']}/messages",
                    json={"body": "x", "as_task": {"title": "t"}})
    assert r.status_code == 422
    err = rest.ErrorResponse.model_validate(r.json())
    assert err.error.code is rest.ErrorCode.TASK_IN_DM
    assert err.error.rule == "FR-5.1"


def test_error_shape_r1_agent_never_owner(client: TestClient) -> None:
    pat = next(m for m in client.get("/api/members").json() if m["name"] == "Pat")
    r = client.patch(f"/api/members/{pat['id']}", json={"role": "owner"})
    assert r.status_code == 403
    err = rest.ErrorResponse.model_validate(r.json())
    assert err.error.code is rest.ErrorCode.PERMISSION_DENIED and err.error.rule == "R1"


def test_reminder_recurring_requires_loop_contract(client: TestClient) -> None:
    ch = client.get("/api/channels").json()["items"][0]
    r = client.post("/api/reminders",
                    json={"kind": "recurring", "cadence": "0 9 * * *",
                          "anchor_channel_id": ch["id"]})
    assert r.status_code == 422
    assert r.json()["error"]["rule"] == "D1-L2"


def test_file_staging_roundtrip(client: TestClient) -> None:
    r = client.post("/api/files", files={"file": ("spec.md", b"# hi", "text/markdown")})
    assert r.status_code == 201
    meta = entities.FilePublic.model_validate(r.json())
    assert meta.message_id is None  # staging 态（契约 D §9.2）
    assert client.get(f"/api/files/{meta.id}/content").content == b"# hi"


def envelope_of(raw: dict[str, Any]) -> ws.Envelope:
    env = ws.Envelope.model_validate(raw)
    payload_model = ws.EVENT_PAYLOADS[env.type]
    payload_model.model_validate(env.data)
    return env


def test_ws_hello_ping_broadcast_and_timeline(client: TestClient) -> None:
    channels = client.get("/api/channels").json()["items"]
    build = next(c for c in channels if c["name"] == "build")
    with client.websocket_connect("/api/ws") as sock:
        hello = envelope_of(sock.receive_json())
        assert hello.type is ws.EventType.SYS_HELLO and hello.seq == 1

        sock.send_json({"type": "ping"})
        pong = envelope_of(sock.receive_json())
        assert pong.type is ws.EventType.SYS_PONG and pong.seq == 2

        # 写端点 → 广播回到发起端（乐观 UI 的确认帧，契约 C §5）
        r = client.post(f"/api/channels/{build['id']}/messages", json={"body": "契约即形状。"})
        assert r.status_code == 201
        created = envelope_of(sock.receive_json())
        assert created.type is ws.EventType.MESSAGE_CREATED
        assert created.channel_id == build["id"] and created.seq == 3

        # 时间线回放：5 事件逐个到达且 seq 单调、payload 全过模型
        assert client.post("/__mock/play").status_code == 202
        seqs = [created.seq]
        types: list[ws.EventType] = []
        for _ in range(5):
            env = envelope_of(sock.receive_json())
            seqs.append(env.seq)
            types.append(env.type)
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
        assert types == [
            ws.EventType.AGENT_ACTIVITY, ws.EventType.MESSAGE_CREATED,
            ws.EventType.TASK_UPDATED, ws.EventType.PRESENCE_CHANGED,
            ws.EventType.TOKEN_USAGE_REPORTED,
        ]

    # 铁律 1：WS 达到的状态必须能靠 REST 重建——task #1 现在应是 in_review
    tasks = client.get("/api/tasks", params={"channel_id": build["id"]}).json()["items"]
    task1 = next(t for t in tasks if t["number"] == 1)
    assert task1["status"] == "in_review"
    presence = client.get("/api/presence").json()["items"]
    hank = next(m for m in client.get("/api/members").json() if m["name"] == "Hank")
    assert next(p for p in presence if p["member_id"] == hank["id"])["status"] == "idle"
