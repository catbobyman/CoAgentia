"""契约一致性**双跑**（纪律 3）：把 M1 适用的形状/错误断言参数化为对
[mock app, 真 server app] 双跑——两实现对同一契约形状零偏差。

- 真 server fixture = create_app(注入临时库 alembic upgrade + seed) + TestClient；
- mock fixture = 既有 mock app + /__mock/reset 复位；
- M2/mock-only（GET /api/tasks、/__mock/play 时间线回放）不在本文件双跑；
- WS 双跑（A4 补齐）：hello/ping-pong/写端点广播的信封与 seq 语义对 [mock, 真 server] 零偏差；
  真 server 首连额外发 owner online（契约 C §2），故断言按目标事件类型收敛（drain_until）。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from coagentia_contracts import daemon, entities, rest, ws
from coagentia_server.app import create_app
from coagentia_server.db.engine import make_engine, sqlite_url
from coagentia_server.db.seed import seed_database
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"

DualClient = tuple[str, TestClient]


@pytest.fixture(params=["mock", "real"])
def dual(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[DualClient]:
    if request.param == "mock":
        from coagentia_mock.app import app as mock_app

        client = TestClient(mock_app)
        client.post("/__mock/reset")  # 复位 store，测试间隔离
        yield "mock", client
    else:
        url = sqlite_url(tmp_path / "conf.db")
        cfg = Config(str(ALEMBIC_INI))
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, "head")
        engine = make_engine(url=url)
        seed_database(engine)
        app = create_app(engine=engine, data_root=tmp_path / "data")
        with TestClient(app) as client:
            yield "real", client
        engine.dispose()


def _build_channel(client: TestClient) -> dict:
    return next(c for c in client.get("/api/channels").json()["items"] if c["name"] == "build")


def _dm(client: TestClient) -> dict:
    return next(c for c in client.get("/api/channels").json()["items"] if c["kind"] == "dm")


def _member(client: TestClient, name: str) -> dict:
    return next(m for m in client.get("/api/members").json() if m["name"] == name)


# ---------------------------------------------------------------- 形状（读端点）


def test_workspace_shape(dual: DualClient) -> None:
    _, client = dual
    entities.WorkspacePublic.model_validate(client.get("/api/workspace").json())


def test_computers_shape_and_sensitive_column_stripped(dual: DualClient) -> None:
    _, client = dual
    for c in client.get("/api/computers").json():
        entities.ComputerPublic.model_validate(c)
        assert "api_key_hash" not in c  # 敏感列剔除（契约 A §8.2）


def test_members_shape(dual: DualClient) -> None:
    _, client = dual
    for m in client.get("/api/members").json():
        entities.MemberPublic.model_validate(m)


def test_presence_shape(dual: DualClient) -> None:
    _, client = dual
    rest.PresenceSnapshot.model_validate(client.get("/api/presence").json())


def test_channels_snapshot_with_read_positions(dual: DualClient) -> None:
    _, client = dual
    snap = rest.ChannelsSnapshot.model_validate(client.get("/api/channels").json())
    assert snap.read_positions, "自身 read-position 随 GET /channels 附带（契约 B §6）"


def test_messages_page_shape(dual: DualClient) -> None:
    _, client = dual
    build = _build_channel(client)
    page = rest.Page[entities.MessagePublic].model_validate(
        client.get(f"/api/channels/{build['id']}/messages").json()
    )
    assert page.items, "P1 消息流不为空"


def test_agent_detail_shape(dual: DualClient) -> None:
    _, client = dual
    pat = _member(client, "Pat")
    entities.AgentPublic.model_validate(client.get(f"/api/agents/{pat['id']}").json())


# ---------------------------------------------------------------- 错误路径（形状零偏差）


def test_task_in_dm(dual: DualClient) -> None:
    _, client = dual
    dm = _dm(client)
    r = client.post(
        f"/api/channels/{dm['id']}/messages", json={"body": "x", "as_task": {"title": "t"}}
    )
    assert r.status_code == 422
    err = rest.ErrorResponse.model_validate(r.json())
    assert err.error.code is rest.ErrorCode.TASK_IN_DM
    assert err.error.rule == "FR-5.1"


def test_r1_agent_never_owner(dual: DualClient) -> None:
    _, client = dual
    pat = _member(client, "Pat")
    r = client.patch(f"/api/members/{pat['id']}", json={"role": "owner"})
    assert r.status_code == 403
    err = rest.ErrorResponse.model_validate(r.json())
    assert err.error.code is rest.ErrorCode.PERMISSION_DENIED and err.error.rule == "R1"


def test_reminder_recurring_requires_loop_contract(dual: DualClient) -> None:
    _, client = dual
    ch = client.get("/api/channels").json()["items"][0]
    r = client.post(
        "/api/reminders",
        json={"kind": "recurring", "cadence": "0 9 * * *", "anchor_channel_id": ch["id"]},
    )
    assert r.status_code == 422
    err = rest.ErrorResponse.model_validate(r.json())
    assert err.error.code is rest.ErrorCode.VALIDATION_FAILED and err.error.rule == "D1-L2"


def test_not_top_level_message_as_task(dual: DualClient) -> None:
    _, client = dual
    build = _build_channel(client)
    r = client.post(
        f"/api/channels/{build['id']}/messages",
        json={"body": "x", "thread_root_id": build["id"], "as_task": {"title": "t"}},
    )
    assert r.status_code == 422
    err = rest.ErrorResponse.model_validate(r.json())
    assert err.error.code is rest.ErrorCode.NOT_TOP_LEVEL_MESSAGE and err.error.rule == "T3"


# ---------------------------------------------------------------- 文件 staging 往返


def test_file_staging_roundtrip(dual: DualClient) -> None:
    _, client = dual
    r = client.post("/api/files", files={"file": ("spec.md", b"# hi", "text/markdown")})
    assert r.status_code == 201
    meta = entities.FilePublic.model_validate(r.json())
    assert meta.message_id is None  # staging 态（契约 D §9.2）
    assert client.get(f"/api/files/{meta.id}/content").content == b"# hi"


# ---------------------------------------------------------------- 消息发送形状（写端点）


def test_post_message_shape(dual: DualClient) -> None:
    _, client = dual
    build = _build_channel(client)
    r = client.post(f"/api/channels/{build['id']}/messages", json={"body": "契约即形状。"})
    assert r.status_code == 201
    created = rest.MessageCreated.model_validate(r.json())
    assert created.task is None  # 无 as_task → task=null


# ---------------------------------------------------------------- 任务域形状（M2 双跑）


def test_as_task_creates_task(dual: DualClient) -> None:
    _, client = dual
    build = _build_channel(client)
    r = client.post(
        f"/api/channels/{build['id']}/messages",
        json={"body": "修一下登录 bug", "as_task": {"title": "登录 bug"}},
    )
    assert r.status_code == 201
    created = rest.MessageCreated.model_validate(r.json())
    assert created.task is not None
    assert created.task.number >= 1
    assert created.task.status is entities.TaskStatus.TODO
    assert created.task.root_message_id == created.message.id


def test_convert_message_to_task_shape(dual: DualClient) -> None:
    _, client = dual
    build = _build_channel(client)
    msg = client.post(
        f"/api/channels/{build['id']}/messages", json={"body": "# 顶级消息\n正文"}
    ).json()["message"]
    r = client.post(f"/api/messages/{msg['id']}/task", json={})
    assert r.status_code == 201
    task = entities.TaskPublic.model_validate(r.json())
    assert task.root_message_id == msg["id"]


def test_get_tasks_shape(dual: DualClient) -> None:
    _, client = dual
    build = _build_channel(client)
    client.post(
        f"/api/channels/{build['id']}/messages",
        json={"body": "任务列表形状", "as_task": {"title": "t"}},
    )
    page = rest.Page[entities.TaskPublic].model_validate(client.get("/api/tasks").json())
    assert page.items, "至少一条任务"
    # channel 过滤面
    filtered = rest.Page[entities.TaskPublic].model_validate(
        client.get("/api/tasks", params={"channel_id": build["id"]}).json()
    )
    assert all(t.channel_id == build["id"] for t in filtered.items)


def test_task_detail_shape(dual: DualClient) -> None:
    _, client = dual
    build = _build_channel(client)
    created = client.post(
        f"/api/channels/{build['id']}/messages",
        json={"body": "详情形状", "as_task": {"title": "t"}},
    ).json()
    detail = rest.TaskDetail.model_validate(
        client.get(f"/api/tasks/{created['task']['id']}").json()
    )
    assert detail.contracts == []  # 新建任务尚无契约（真 server）/ mock 恒空
    assert detail.usage.events == 0  # 无 usage 富化 → 0（优雅缺席）
    assert detail.task.project_id is None
    assert detail.task.writes_code is False
    assert detail.worktree is None


def test_task_contracts_empty_shape(dual: DualClient) -> None:
    """GET /tasks/{id}/contracts（M3a E2）：新建任务无契约 → 200 空列表，双跑形状零偏差。

    POST /tasks/{id}/contracts、T7、request-draft 是真 server 独有逻辑（mock 无业务，纪律 4），
    不在本文件双跑——见 test_contracts.py。
    """
    _, client = dual
    build = _build_channel(client)
    created = client.post(
        f"/api/channels/{build['id']}/messages",
        json={"body": "契约形状", "as_task": {"title": "t"}},
    ).json()
    r = client.get(f"/api/tasks/{created['task']['id']}/contracts")
    assert r.status_code == 200
    items = TypeAdapter(list[entities.TaskContractPublic]).validate_python(r.json())
    assert items == []


def test_task_created_broadcast(dual: DualClient) -> None:
    _, client = dual
    build = _build_channel(client)
    with client.websocket_connect("/api/ws") as sock:
        hello = _envelope_of(sock.receive_json())
        r = client.post(
            f"/api/channels/{build['id']}/messages",
            json={"body": "广播序", "as_task": {"title": "t"}},
        )
        assert r.status_code == 201
        # message.created 先于 task.created（B §9.4 严格提交序）。
        created = _drain_until(sock, ws.EventType.MESSAGE_CREATED, hello.seq)
        task_created = _drain_until(sock, ws.EventType.TASK_CREATED, created.seq)
        assert task_created.data["task"]["id"] == r.json()["task"]["id"]


# ------------------------------------------------------ files / search / activity 形状（C4 双跑）


def test_channel_files_page_shape(dual: DualClient) -> None:
    _, client = dual
    build = _build_channel(client)
    rest.Page[entities.FilePublic].model_validate(
        client.get(f"/api/channels/{build['id']}/files").json()
    )


def test_search_response_shape(dual: DualClient) -> None:
    _, client = dual
    rest.SearchResponse.model_validate(
        client.get("/api/search", params={"q": "build"}).json()
    )


def test_activity_page_shape(dual: DualClient) -> None:
    _, client = dual
    rest.Page[entities.ActivityItemPublic].model_validate(
        client.get("/api/activity").json()
    )


# ---- 真 server「目录 vs 实 serve」一致性（M2 C4 先例）


def _served(client: TestClient) -> set[tuple[str, str]]:
    spec = client.get("/openapi.json").json()
    out: set[tuple[str, str]] = set()
    for path, methods in spec["paths"].items():
        for method in methods:
            out.add((method.upper(), _norm(path.removeprefix("/api"))))
    return out


def _norm(path: str) -> str:
    import re

    return re.sub(r"\{[^}]+\}", "{}", path)


# ---- M4 护栏三键：目录（ENDPOINTS_M4）↔ 真 server 实 serve 对账（M2/M3 先例）


def test_held_drafts_endpoints_served(server_client: TestClient) -> None:
    """§4.14 护栏四端点（GET /held-drafts + release/discard/reevaluate）全被真 server serve。"""
    served = _served(server_client)
    missing = [(m, p) for m, p in rest.ENDPOINTS_M4 if (m, _norm(p)) not in served]
    assert not missing, f"M4 护栏端点未 serve: {missing}"


def test_held_drafts_list_shape(dual: DualClient) -> None:
    """GET /held-drafts（B §4.14）：mock 恒空、真 server 冷库亦空——两实现读形状零偏差。"""
    _, client = dual
    page = rest.Page[entities.HeldDraftPublic].model_validate(
        client.get("/api/held-drafts", params={"status": "held"}).json()
    )
    assert page.items == [] and page.next_cursor is None


# ---------------------------------------------------------------- M5 契约登记（H0）
#
# H0 只登记契约面：mock serve 全部 M5 端点（形状源喂 OpenAPI→rest.ts），ChannelsSnapshot 扩
# notification_settings 第三字段。真 server serve 与逐端点行为双跑（通知 mode 门）归实现模块
# （H3）自有测试文件——H0 不在此断言真 server serve。


def test_mock_covers_m5_endpoints() -> None:
    """mock 形状源 serve 全部 M5 端点（§4.5 通知设置二；模板三随 DEDAG 退役）——喂
    OpenAPI→rest.ts。"""
    from coagentia_mock.app import app as mock_app

    served = _served(TestClient(mock_app))
    missing = [(m, p) for m, p in rest.ENDPOINTS_M5 if (m, _norm(p)) not in served]
    assert not missing, f"mock 未 serve M5 端点: {missing}"


def test_channels_snapshot_notification_settings_field(dual: DualClient) -> None:
    """ChannelsSnapshot 扩第三字段 notification_settings（§11.4 #5）：H0 字段就位默认空——双跑形状
    零偏差（mock/真 server 冷态均无本人非默认行 → []；H3 落 mute/mentions 后填充）。"""
    _, client = dual
    snap = rest.ChannelsSnapshot.model_validate(client.get("/api/channels").json())
    assert snap.notification_settings == []


# ---------------------------------------------------------------- M6 契约登记（J0）


def test_mock_covers_m6_endpoints() -> None:
    """mock 形状源 serve M6 目录端点（Project 7；编排/retry/模板治理随 DEDAG 退役）。"""
    from coagentia_mock.app import app as mock_app

    served = _served(TestClient(mock_app))
    missing = [(m, p) for m, p in rest.ENDPOINTS_M6 if (m, _norm(p)) not in served]
    assert not missing, f"mock 未 serve M6 端点: {missing}"


# ---------------------------------------------------------------- M7 契约登记（K0）
#
# K0 只登记契约面：mock serve 全部 M7 七端点（形状源喂 OpenAPI→rest.ts）。真 server serve 与
# 逐端点行为双跑（预览 ensure+touch/回收调度、部署 409 不排队、usage 三层聚合）归实现模块
# （K3/K4/K6）各自测试——K0 不在此断言真 server serve（块 a/b 期间尚未实现，同 M5/M6 J0 先例）。


def test_mock_covers_m7_endpoints() -> None:
    """mock 形状源 serve 全部 M7 七端点（§13 预览 3 + 部署 3 + 成本 1）——喂 OpenAPI→rest.ts。"""
    from coagentia_mock.app import app as mock_app

    served = _served(TestClient(mock_app))
    missing = [(m, p) for m, p in rest.ENDPOINTS_M7 if (m, _norm(p)) not in served]
    assert not missing, f"mock 未 serve M7 端点: {missing}"


def test_mock_project_diff_shapes() -> None:
    """J0 只验 OpenAPI 形状；Project 校验与 git diff 均不在 mock 实现。"""
    from coagentia_mock.app import app as mock_app

    client = TestClient(mock_app)
    client.post("/__mock/reset")

    projects = TypeAdapter(list[entities.ProjectPublic]).validate_python(
        client.get("/api/projects").json()
    )
    assert projects and projects[0].channel_ids

    task = client.get("/api/tasks").json()["items"][0]
    diff = daemon.DiffPayload.model_validate(
        client.get(f"/api/tasks/{task['id']}/diff").json()
    )
    assert diff.files and diff.files[0].path == "README.md"


_PROJECTS_J2 = [
    ("GET", "/projects"),
    ("POST", "/projects"),
    ("PATCH", "/projects/{project_id}"),
    ("DELETE", "/projects/{project_id}"),
    ("POST", "/channels/{channel_id}/projects"),
    ("DELETE", "/channels/{channel_id}/projects/{project_id}"),
]


def test_projects_j2_endpoints_served(server_client: TestClient) -> None:
    """J2 真 server serve Project CRUD 与频道绑定六端点（Diff 归 J4）。"""
    served = _served(server_client)
    missing = [
        (method, path)
        for method, path in _PROJECTS_J2
        if (method, _norm(path)) not in served
    ]
    assert not missing, f"J2 Project 端点未 serve: {missing}"


def test_project_crud_and_binding_shapes(dual: DualClient, tmp_path: Path) -> None:
    """Project 六端点对 mock/真 server 双跑同一组契约形状。"""
    import subprocess

    label, client = dual
    repo = tmp_path / f"project-shape-{label}"
    repo.mkdir(exist_ok=True)
    subprocess.run(
        ["git", "init", "--quiet"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    listed = TypeAdapter(list[entities.ProjectPublic]).validate_python(
        client.get("/api/projects").json()
    )
    assert all(isinstance(project.channel_ids, list) for project in listed)

    computer = client.get("/api/computers").json()[0]
    created_response = client.post(
        "/api/projects",
        json={"name": "Shape Project", "repo_path": str(repo), "computer_id": computer["id"]},
    )
    assert created_response.status_code == 201, created_response.text
    created = entities.ProjectPublic.model_validate(created_response.json())

    patched_response = client.patch(
        f"/api/projects/{created.id}", json={"name": "Patched", "worktree_keep_days": 5}
    )
    assert patched_response.status_code == 200, patched_response.text
    assert entities.ProjectPublic.model_validate(patched_response.json()).name == "Patched"

    build = _build_channel(client)
    bind_response = client.post(
        f"/api/channels/{build['id']}/projects", json={"project_id": created.id}
    )
    assert bind_response.status_code == 201, bind_response.text
    binding = entities.ChannelProjectPublic.model_validate(bind_response.json())
    assert binding.channel_id == build["id"] and binding.project_id == created.id

    assert client.delete(
        f"/api/channels/{build['id']}/projects/{created.id}"
    ).status_code == 204
    assert client.delete(f"/api/projects/{created.id}").status_code == 204


# ---------------------------------------------------------------- WS 信封与广播（A4 双跑）


def _envelope_of(raw: dict) -> ws.Envelope:
    env = ws.Envelope.model_validate(raw)
    ws.EVENT_PAYLOADS[env.type].model_validate(env.data)  # payload 逐帧过契约模型
    return env


def _drain_until(sock, target: ws.EventType, last_seq: int) -> ws.Envelope:
    """读到 target 类型帧为止，逐帧校验信封 + seq 连接内单调（契约 C §3）。"""
    for _ in range(10):
        env = _envelope_of(sock.receive_json())
        assert env.seq > last_seq, (env.seq, last_seq)
        last_seq = env.seq
        if env.type is target:
            return env
    raise AssertionError(f"未在限帧内收到 {target}")


def test_ws_hello_ping_broadcast(dual: DualClient) -> None:
    _, client = dual
    build = _build_channel(client)
    with client.websocket_connect("/api/ws") as sock:
        hello = _envelope_of(sock.receive_json())
        assert hello.type is ws.EventType.SYS_HELLO and hello.seq == 1

        sock.send_json({"type": "ping"})
        pong = _drain_until(sock, ws.EventType.SYS_PONG, hello.seq)

        r = client.post(f"/api/channels/{build['id']}/messages", json={"body": "契约即形状。"})
        assert r.status_code == 201
        created = _drain_until(sock, ws.EventType.MESSAGE_CREATED, pong.seq)
        assert created.channel_id == build["id"]
        assert created.data["message"]["id"] == r.json()["message"]["id"]
