"""H5：模板域保存/列表/builtin（契约 B §11.1 / A §4.10）真 server 业务逻辑。

覆盖保存逐路径（序列化仅 task 节点 / 占位 owner 去重 / role_placeholders 覆盖 / plan_skeleton
带走 / pos 不入 / include_node_ids 子集 / 仅入选边）、约束 409 二值、无环与引用校验红例、builtin
启动幂等 upsert、列表 builtin 置前。契约形状双跑在 test_conformance_dual.py；本文件断言真 server
独有业务（纪律 4）。
"""

from __future__ import annotations

from typing import Any

import pytest
from coagentia_contracts import entities, rest
from coagentia_contracts.entities import (
    TemplateBody,
    TemplateEdge,
    TemplateNode,
    TemplateRole,
)
from coagentia_contracts.enums import LandingBatchStatus
from coagentia_server.api import ApiError
from coagentia_server.canvas import service as canvas_service
from coagentia_server.db import models
from coagentia_server.ledger import service
from coagentia_server.templates import builtin
from coagentia_server.templates import service as templates_service
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

_TASK = models.tbl(models.Task)
_MEMBER = models.tbl(models.Member)
_TEMPLATE = models.tbl(models.Template)
_MSG = models.tbl(models.Message)
_MENTION = models.tbl(models.MessageMention)
_BATCH = models.tbl(models.LandingBatch)
_LEDGER = models.tbl(models.LedgerEntry)

_TASK_PLAN = {
    "goal": "让用户能登录",
    "acceptance_criteria": [
        {
            "id": "a1",
            "statement": "输入正确凭证可进主页",
            "verify_by": "command",
            "verify_ref": "pytest -k login",
        }
    ],
}


# ---------------------------------------------------------------- 夹具辅助


def _build_channel(client: TestClient) -> dict[str, Any]:
    return next(c for c in client.get("/api/channels").json()["items"] if c["name"] == "build")


def _canvas_id(client: TestClient, channel: dict[str, Any]) -> str:
    return client.get(f"/api/channels/{channel['id']}/canvas").json()["canvas"]["id"]


def _agent_node(
    client: TestClient, canvas_id: str, title: str, task_plan: dict[str, Any] | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {"title": title, "kind": "agent"}
    if task_plan is not None:
        payload["task_plan"] = task_plan
    r = client.post(f"/api/canvases/{canvas_id}/nodes", json=payload)
    assert r.status_code == 201, r.text
    return r.json()["node"]


def _system_node(client: TestClient, canvas_id: str) -> dict[str, Any]:
    r = client.post(
        f"/api/canvases/{canvas_id}/nodes", json={"title": "合并", "kind": "system",
                                                  "system_action": "merge"}
    )
    assert r.status_code == 201, r.text
    return r.json()["node"]


def _edge(client: TestClient, canvas_id: str, from_id: str, to_id: str) -> None:
    r = client.post(
        f"/api/canvases/{canvas_id}/edges",
        json={"from_node_id": from_id, "to_node_id": to_id},
    )
    assert r.status_code == 201, r.text


def _set_owner(engine: Engine, task_id: str, member_id: str | None) -> None:
    with engine.begin() as conn:
        conn.execute(
            update(_TASK).where(_TASK.c.id == task_id).values(owner_member_id=member_id)
        )


def _add_human(engine: Engine, workspace_id: str, name: str) -> str:
    mid = service.new_ulid()
    with engine.begin() as conn:
        conn.execute(
            insert(_MEMBER).values(
                id=mid,
                workspace_id=workspace_id,
                kind="human",
                name=name,
                role="member",
                removed_at=None,
                created_at=service.now_iso(),
            )
        )
    return mid


def _member(client: TestClient, name: str) -> dict[str, Any]:
    return next(m for m in client.get("/api/members").json() if m["name"] == name)


def _post_template(client: TestClient, channel_id: str, **extra: Any):
    payload = {"channel_id": channel_id, "name": "存的模板", **extra}
    return client.post("/api/templates", json=payload)


# ---------------------------------------------------------------- 序列化：仅 task 节点


def test_serialize_only_task_nodes(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """序列化仅取 task 类（agent）节点，system 节点滤除。"""
    build = _build_channel(server_client)
    canvas_id = _canvas_id(server_client, build)
    _agent_node(server_client, canvas_id, "实现登录")
    _system_node(server_client, canvas_id)  # system 节点应被滤除

    r = _post_template(server_client, build["id"])
    assert r.status_code == 201, r.text
    tpl = entities.TemplatePublic.model_validate(r.json())
    assert len(tpl.body.nodes) == 1
    assert tpl.body.nodes[0].title == "实现登录"
    assert tpl.builtin is False


def test_serialize_pos_not_included(server_client: TestClient) -> None:
    """pos 不入模板：TemplateNode 无坐标字段，且 layout 写坐标后序列化仍无坐标。"""
    build = _build_channel(server_client)
    canvas_id = _canvas_id(server_client, build)
    node = _agent_node(server_client, canvas_id, "带坐标的节点")
    server_client.put(
        f"/api/canvases/{canvas_id}/layout",
        json={"positions": [{"node_id": node["id"], "x": 42.0, "y": 99.0}]},
    )
    r = _post_template(server_client, build["id"])
    assert r.status_code == 201, r.text
    node_json = r.json()["body"]["nodes"][0]
    assert set(node_json.keys()) == {"key", "title", "role", "plan_skeleton"}
    assert "pos_x" not in node_json and "pos_y" not in node_json


# ---------------------------------------------------------------- 序列化：占位去重 / 覆盖


def test_placeholder_dedup_by_owner(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """两 agent 节点同 owner → 占位去重为一个 role；节点 role = owner 成员名。"""
    build = _build_channel(server_client)
    canvas_id = _canvas_id(server_client, build)
    pat = _member(server_client, "Pat")
    n1 = _agent_node(server_client, canvas_id, "任务一")
    n2 = _agent_node(server_client, canvas_id, "任务二")
    _set_owner(seeded_engine, n1["task_id"], pat["id"])
    _set_owner(seeded_engine, n2["task_id"], pat["id"])

    r = _post_template(server_client, build["id"])
    assert r.status_code == 201, r.text
    tpl = entities.TemplatePublic.model_validate(r.json())
    assert len(tpl.body.roles) == 1
    assert tpl.body.roles[0].placeholder == "Pat"
    assert {n.role for n in tpl.body.nodes} == {"Pat"}


def test_no_owner_unclaimed(server_client: TestClient) -> None:
    """无 owner 节点 → 占位归「待认领」。"""
    build = _build_channel(server_client)
    canvas_id = _canvas_id(server_client, build)
    _agent_node(server_client, canvas_id, "无主任务")  # create_task 起始 owner=None
    r = _post_template(server_client, build["id"])
    assert r.status_code == 201, r.text
    tpl = entities.TemplatePublic.model_validate(r.json())
    assert tpl.body.nodes[0].role == templates_service.UNCLAIMED_PLACEHOLDER
    assert tpl.body.roles[0].placeholder == templates_service.UNCLAIMED_PLACEHOLDER


def test_role_placeholders_override(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """role_placeholders {member_id: 占位名} 覆盖默认 owner 成员名占位。"""
    build = _build_channel(server_client)
    canvas_id = _canvas_id(server_client, build)
    pat = _member(server_client, "Pat")
    n1 = _agent_node(server_client, canvas_id, "实现任务")
    _set_owner(seeded_engine, n1["task_id"], pat["id"])

    r = _post_template(
        server_client, build["id"], role_placeholders={pat["id"]: "实现工程师"}
    )
    assert r.status_code == 201, r.text
    tpl = entities.TemplatePublic.model_validate(r.json())
    assert tpl.body.nodes[0].role == "实现工程师"
    assert {ro.placeholder for ro in tpl.body.roles} == {"实现工程师"}


# ---------------------------------------------------------------- 序列化：plan_skeleton 带走


def test_plan_skeleton_carried(server_client: TestClient) -> None:
    """节点带 TaskPlan 契约 → plan_skeleton 带走（goal + acceptance_criteria）。"""
    build = _build_channel(server_client)
    canvas_id = _canvas_id(server_client, build)
    _agent_node(server_client, canvas_id, "有计划的任务", task_plan=_TASK_PLAN)
    r = _post_template(server_client, build["id"])
    assert r.status_code == 201, r.text
    tpl = entities.TemplatePublic.model_validate(r.json())
    skeleton = tpl.body.nodes[0].plan_skeleton
    assert skeleton is not None
    assert skeleton.goal == "让用户能登录"
    assert len(skeleton.acceptance_criteria) == 1


def test_plan_skeleton_none_when_absent(server_client: TestClient) -> None:
    """节点无 TaskPlan 契约 → plan_skeleton = None。"""
    build = _build_channel(server_client)
    canvas_id = _canvas_id(server_client, build)
    _agent_node(server_client, canvas_id, "无计划的任务")
    r = _post_template(server_client, build["id"])
    assert r.status_code == 201, r.text
    tpl = entities.TemplatePublic.model_validate(r.json())
    assert tpl.body.nodes[0].plan_skeleton is None


# ---------------------------------------------------------------- 序列化：include / edges


def test_include_node_ids_subset(server_client: TestClient) -> None:
    """include_node_ids 缺省全部；显式子集只序列化入选节点。"""
    build = _build_channel(server_client)
    canvas_id = _canvas_id(server_client, build)
    n1 = _agent_node(server_client, canvas_id, "选中的")
    _agent_node(server_client, canvas_id, "落选的")
    r = _post_template(server_client, build["id"], include_node_ids=[n1["id"]])
    assert r.status_code == 201, r.text
    tpl = entities.TemplatePublic.model_validate(r.json())
    assert len(tpl.body.nodes) == 1 and tpl.body.nodes[0].title == "选中的"


def test_edges_between_task_nodes_only(server_client: TestClient) -> None:
    """边仅保留两端都入选 task 节点的；连向 system 节点的边天然剔除。"""
    build = _build_channel(server_client)
    canvas_id = _canvas_id(server_client, build)
    a = _agent_node(server_client, canvas_id, "上游")
    b = _agent_node(server_client, canvas_id, "下游")
    sysn = _system_node(server_client, canvas_id)
    _edge(server_client, canvas_id, a["id"], b["id"])  # 保留
    _edge(server_client, canvas_id, b["id"], sysn["id"])  # 剔除（system 端点）

    r = _post_template(server_client, build["id"])
    assert r.status_code == 201, r.text
    tpl = entities.TemplatePublic.model_validate(r.json())
    assert len(tpl.body.edges) == 1
    keys = {n.title: n.key for n in tpl.body.nodes}
    assert tpl.body.edges[0].from_key == keys["上游"]
    assert tpl.body.edges[0].to_key == keys["下游"]


# ---------------------------------------------------------------- 约束 409 二值


def test_409_no_task_nodes_empty_canvas(server_client: TestClient) -> None:
    """空画布（无正式 task 节点）→ 409 TEMPLATE_CANVAS_NOT_READY，reason=no_task_nodes。"""
    build = _build_channel(server_client)
    r = _post_template(server_client, build["id"])
    assert r.status_code == 409, r.text
    err = rest.ErrorResponse.model_validate(r.json())
    assert err.error.code is rest.ErrorCode.TEMPLATE_CANVAS_NOT_READY
    assert err.error.details == {"reason": "no_task_nodes"}


def test_409_only_system_nodes(server_client: TestClient) -> None:
    """仅 system 节点（无 task 节点）→ 同样 409（system 不算正式 task 节点）。"""
    build = _build_channel(server_client)
    canvas_id = _canvas_id(server_client, build)
    _system_node(server_client, canvas_id)
    r = _post_template(server_client, build["id"])
    assert r.status_code == 409, r.text
    assert (
        rest.ErrorResponse.model_validate(r.json()).error.code
        is rest.ErrorCode.TEMPLATE_CANVAS_NOT_READY
    )


def test_draft_layer_helper_is_empty_in_m5(seeded_engine: Engine) -> None:
    """草稿层二值约束的第二值：M5 无 proposals 落地 → has_draft_layer 恒 False（占位保结构）。"""
    build_id = "any-channel"
    with seeded_engine.connect() as conn:
        assert templates_service.has_draft_layer(conn, build_id) is False


# ---------------------------------------------------------------- 校验红例（无环 / 引用一致性）


def test_validate_cycle_red() -> None:
    """成环 TemplateBody → 422 GRAPH_CYCLE。"""
    body = TemplateBody(
        nodes=[
            TemplateNode(key="a", title="A", role="r"),
            TemplateNode(key="b", title="B", role="r"),
        ],
        edges=[TemplateEdge(from_key="a", to_key="b"), TemplateEdge(from_key="b", to_key="a")],
        roles=[TemplateRole(placeholder="r")],
    )
    with pytest.raises(ApiError) as exc:
        templates_service.validate_template_body(body)
    assert exc.value.status == 422
    assert exc.value.body.code is rest.ErrorCode.GRAPH_CYCLE


def test_validate_role_reference_red() -> None:
    """node.role 未在 roles 占位表 → 422 VALIDATION_FAILED（details.field=nodes.role）。"""
    body = TemplateBody(
        nodes=[TemplateNode(key="a", title="A", role="未登记")],
        edges=[],
        roles=[TemplateRole(placeholder="已登记")],
    )
    with pytest.raises(ApiError) as exc:
        templates_service.validate_template_body(body)
    assert exc.value.status == 422
    assert exc.value.body.code is rest.ErrorCode.VALIDATION_FAILED
    assert exc.value.body.details == {"field": "nodes.role", "value": "未登记"}


def test_validate_edge_reference_red() -> None:
    """edge 端点不在 nodes.key → 422 VALIDATION_FAILED（details.field=edges.to_key）。"""
    body = TemplateBody(
        nodes=[TemplateNode(key="a", title="A", role="r")],
        edges=[TemplateEdge(from_key="a", to_key="missing")],
        roles=[TemplateRole(placeholder="r")],
    )
    with pytest.raises(ApiError) as exc:
        templates_service.validate_template_body(body)
    assert exc.value.body.code is rest.ErrorCode.VALIDATION_FAILED
    assert exc.value.body.details == {"field": "edges.to_key", "value": "missing"}


def test_builtin_body_passes_validation() -> None:
    """工程三角 builtin body 过校验执法点（6 节点线性 DAG + 4 角色，引用一致、无环）。"""
    body = builtin.build_triangle_body()
    templates_service.validate_template_body(body)  # 不抛
    assert len(body.nodes) == 6
    assert len({r.placeholder for r in body.roles}) == 4
    assert all(n.plan_skeleton is not None for n in body.nodes)


# ---------------------------------------------------------------- builtin upsert 幂等 / 列表


def test_builtin_upsert_idempotent(seeded_engine: Engine) -> None:
    """启动 upsert 幂等：连调两次 → 该 workspace 恰一 builtin 行（重启不重复）。"""
    templates_service.upsert_builtin_templates(seeded_engine)
    templates_service.upsert_builtin_templates(seeded_engine)
    with seeded_engine.connect() as conn:
        rows = list(
            conn.execute(
                select(_TEMPLATE).where(
                    _TEMPLATE.c.name == builtin.BUILTIN_TRIANGLE_NAME,
                    _TEMPLATE.c.builtin == 1,
                )
            ).mappings()
        )
    assert len(rows) == 1
    tpl = entities.TemplatePublic.model_validate(dict(rows[0]))
    assert tpl.builtin is True and len(tpl.body.nodes) == 6


def test_builtin_upsert_skips_empty_db(migrated_engine: Engine) -> None:
    """空库（无 workspace）优雅跳过——不抛、不落行。"""
    templates_service.upsert_builtin_templates(migrated_engine)
    with migrated_engine.connect() as conn:
        assert conn.execute(select(_TEMPLATE)).first() is None


def test_list_builtin_first(server_client: TestClient) -> None:
    """GET /templates：builtin（工程三角，lifespan 已 upsert）置前；建用户模板后 builtin 仍首位。"""
    build = _build_channel(server_client)
    canvas_id = _canvas_id(server_client, build)
    _agent_node(server_client, canvas_id, "存模板用")
    _post_template(server_client, build["id"])

    items = server_client.get("/api/templates").json()
    parsed = [entities.TemplatePublic.model_validate(t) for t in items]
    assert parsed[0].builtin is True
    assert parsed[0].name == builtin.BUILTIN_TRIANGLE_NAME
    assert any(not t.builtin for t in parsed), "用户模板也在列表中"
    # builtin 全部排在非 builtin 之前
    flags = [t.builtin for t in parsed]
    assert flags == sorted(flags, reverse=True)


def test_created_template_persisted(server_client: TestClient) -> None:
    """POST /templates 落库后 GET 可见（工作区级资产）。"""
    build = _build_channel(server_client)
    canvas_id = _canvas_id(server_client, build)
    _agent_node(server_client, canvas_id, "持久化任务")
    created = _post_template(server_client, build["id"]).json()
    listed = server_client.get("/api/templates").json()
    assert any(t["id"] == created["id"] for t in listed)


# ---------------------------------------------------------------- H6 实例化（B §11.2）


def _research_channel(client: TestClient) -> dict[str, Any]:
    return next(
        c for c in client.get("/api/channels").json()["items"] if c["name"] == "research"
    )


def _canvas_detail(client: TestClient, channel_id: str) -> dict[str, Any]:
    return client.get(f"/api/channels/{channel_id}/canvas").json()


def _two_node_template(
    client: TestClient, engine: Engine, *, with_plan: bool = True
) -> dict[str, Any]:
    """在 build 频道建「上游→下游」两 agent 节点 + 一边，owner 分派 Pat/Hank、占位改名
    producer/consumer，存为模板并回模板 JSON。上游可带 plan_skeleton（实例化作 TaskPlan 初稿）。"""
    build = _build_channel(client)
    canvas_id = _canvas_id(client, build)
    pat = _member(client, "Pat")
    hank = _member(client, "Hank")
    up = _agent_node(client, canvas_id, "上游任务", task_plan=_TASK_PLAN if with_plan else None)
    down = _agent_node(client, canvas_id, "下游任务")
    _set_owner(engine, up["task_id"], pat["id"])
    _set_owner(engine, down["task_id"], hank["id"])
    _edge(client, canvas_id, up["id"], down["id"])
    r = _post_template(
        client, build["id"], role_placeholders={pat["id"]: "producer", hank["id"]: "consumer"}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _instantiate(
    client: TestClient,
    template_id: str,
    channel_id: str,
    role_mapping: dict[str, str | None],
    *,
    idempotency_key: str | None = None,
):
    headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
    return client.post(
        f"/api/templates/{template_id}/instantiate",
        json={"channel_id": channel_id, "role_mapping": role_mapping},
        headers=headers,
    )


def test_instantiate_end_to_end(server_client: TestClient, seeded_engine: Engine) -> None:
    """全覆盖实例化：节点/边/任务/TaskPlan 初稿/锚点消息齐全，落地批 done。"""
    tpl = _two_node_template(server_client, seeded_engine)
    research = _research_channel(server_client)
    rin = _member(server_client, "Rin")

    r = _instantiate(
        server_client, tpl["id"], research["id"], {"producer": rin["id"], "consumer": None}
    )
    assert r.status_code == 201, r.text
    result = rest.InstantiateResult.model_validate(r.json())

    # 落地批 done + done_at 写入（mark_done，S4）。
    assert result.batch.status is LandingBatchStatus.DONE
    assert result.batch.done_at is not None
    assert result.batch.source_ref == tpl["id"]

    # 两任务（L2）落地，标题带走。
    assert len(result.tasks) == 2
    titles = {t.title for t in result.tasks}
    assert titles == {"上游任务", "下游任务"}
    assert all(t.status is entities.TaskStatus.TODO for t in result.tasks)

    # 画布落 2 节点 1 边（gating 天然参与）。
    detail = _canvas_detail(server_client, research["id"])
    assert len(detail["nodes"]) == 2
    assert len(detail["edges"]) == 1

    # 上游任务带 TaskPlan 初稿（plan_skeleton 落地为契约）。
    up = next(t for t in result.tasks if t.title == "上游任务")
    contracts = server_client.get(f"/api/tasks/{up.id}/contracts").json()
    assert any(c["kind"] == "task_plan" for c in contracts)

    # 锚点消息齐全：每任务 root_message_id 指向本频道 system 消息。
    with seeded_engine.connect() as conn:
        for t in result.tasks:
            row = conn.execute(
                select(_MSG.c.kind, _MSG.c.channel_id).where(
                    _MSG.c.id == t.root_message_id
                )
            ).first()
            assert row is not None and row[0] == "system" and row[1] == research["id"]


def test_instantiate_missing_role_422(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """role_mapping 缺占位 → 422 VALIDATION_FAILED，details.missing 列缺失占位名。"""
    tpl = _two_node_template(server_client, seeded_engine)
    research = _research_channel(server_client)
    rin = _member(server_client, "Rin")
    r = _instantiate(server_client, tpl["id"], research["id"], {"producer": rin["id"]})
    assert r.status_code == 422, r.text
    err = rest.ErrorResponse.model_validate(r.json())
    assert err.error.code is rest.ErrorCode.VALIDATION_FAILED
    assert err.error.details == {"missing": ["consumer"]}


def test_instantiate_null_role_unclaimed(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """role_mapping 值 null → 该角色节点任务无 owner（待认领）。"""
    tpl = _two_node_template(server_client, seeded_engine, with_plan=False)
    research = _research_channel(server_client)
    rin = _member(server_client, "Rin")
    r = _instantiate(
        server_client, tpl["id"], research["id"], {"producer": rin["id"], "consumer": None}
    )
    assert r.status_code == 201, r.text
    result = rest.InstantiateResult.model_validate(r.json())
    consumer = next(t for t in result.tasks if t.title == "下游任务")
    assert consumer.owner_member_id is None  # null 映射 = 待认领


def test_instantiate_idempotent_same_key(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """同 Idempotency-Key 重试 → 恰一批、不重复建节点（回同一 batch/tasks）。"""
    tpl = _two_node_template(server_client, seeded_engine)
    research = _research_channel(server_client)
    rin = _member(server_client, "Rin")
    mapping: dict[str, str | None] = {"producer": rin["id"], "consumer": None}

    r1 = _instantiate(server_client, tpl["id"], research["id"], mapping, idempotency_key="k1")
    r2 = _instantiate(server_client, tpl["id"], research["id"], mapping, idempotency_key="k1")
    assert r1.status_code == 201 and r2.status_code == 201, (r1.text, r2.text)
    res1 = rest.InstantiateResult.model_validate(r1.json())
    res2 = rest.InstantiateResult.model_validate(r2.json())
    assert res1.batch.id == res2.batch.id
    assert {t.id for t in res1.tasks} == {t.id for t in res2.tasks}

    # 画布只落一批（2 节点），重放未重复建节点。
    detail = _canvas_detail(server_client, research["id"])
    assert len(detail["nodes"]) == 2
    assert len(detail["edges"]) == 1
    # 落地批恰一行。
    with seeded_engine.connect() as conn:
        batches = list(
            conn.execute(
                select(_BATCH.c.id).where(_BATCH.c.channel_id == research["id"])
            ).scalars()
        )
    assert len(batches) == 1


def test_instantiate_idempotency_mismatch_409(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """同 Idempotency-Key 不同请求体 → 409 IDEMPOTENCY_MISMATCH。"""
    tpl = _two_node_template(server_client, seeded_engine)
    research = _research_channel(server_client)
    rin = _member(server_client, "Rin")
    r1 = _instantiate(
        server_client, tpl["id"], research["id"],
        {"producer": rin["id"], "consumer": None}, idempotency_key="k2",
    )
    assert r1.status_code == 201, r1.text
    r2 = _instantiate(
        server_client, tpl["id"], research["id"],
        {"producer": None, "consumer": None}, idempotency_key="k2",
    )
    assert r2.status_code == 409, r2.text
    assert (
        rest.ErrorResponse.model_validate(r2.json()).error.code
        is rest.ErrorCode.IDEMPOTENCY_MISMATCH
    )


def test_instantiate_briefing_mentions_mapped_agents(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """briefing 系统消息 @映射角色：mention 行按 member_id 存在（= 唤醒目标 Agent）。"""
    tpl = _two_node_template(server_client, seeded_engine)
    research = _research_channel(server_client)
    rin = _member(server_client, "Rin")
    r = _instantiate(
        server_client, tpl["id"], research["id"], {"producer": rin["id"], "consumer": None}
    )
    assert r.status_code == 201, r.text
    with seeded_engine.connect() as conn:
        msg_ids = list(
            conn.execute(
                select(_MSG.c.id).where(
                    _MSG.c.channel_id == research["id"], _MSG.c.kind == "system"
                )
            ).scalars()
        )
        mentions = set(
            conn.execute(
                select(_MENTION.c.member_id).where(_MENTION.c.message_id.in_(msg_ids))
            ).scalars()
        )
    # producer→Rin 被 @；consumer→null 无 @（唯一 mention 目标 = Rin）。
    assert mentions == {rin["id"]}


def test_instantiate_downstream_blocked(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """实例化落的 edges 天然参与 derive_blocked：下游因上游未 done 而 blocked、上游不 blocked。"""
    tpl = _two_node_template(server_client, seeded_engine)
    research = _research_channel(server_client)
    rin = _member(server_client, "Rin")
    r = _instantiate(
        server_client, tpl["id"], research["id"], {"producer": rin["id"], "consumer": None}
    )
    assert r.status_code == 201, r.text
    result = rest.InstantiateResult.model_validate(r.json())
    up = next(t for t in result.tasks if t.title == "上游任务")
    down = next(t for t in result.tasks if t.title == "下游任务")
    with seeded_engine.connect() as conn:
        assert canvas_service.is_task_blocked(conn, down.id) is True
        assert canvas_service.is_task_blocked(conn, up.id) is False


def test_instantiate_ledger_marks_done_and_records_nodes(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """落地批 mark_done + 逐节点账本行（幂等键 tmpl:<batch_id>:<node_key>）齐全。"""
    tpl = _two_node_template(server_client, seeded_engine)
    research = _research_channel(server_client)
    rin = _member(server_client, "Rin")
    r = _instantiate(
        server_client, tpl["id"], research["id"], {"producer": rin["id"], "consumer": None}
    )
    assert r.status_code == 201, r.text
    batch_id = rest.InstantiateResult.model_validate(r.json()).batch.id
    with seeded_engine.connect() as conn:
        batch = conn.execute(
            select(_BATCH.c.status, _BATCH.c.done_at).where(_BATCH.c.id == batch_id)
        ).first()
        assert batch is not None and batch[0] == LandingBatchStatus.DONE.value
        assert batch[1] is not None
        node_ops = list(
            conn.execute(
                select(_LEDGER.c.op_id).where(
                    _LEDGER.c.batch_id == batch_id, _LEDGER.c.kind == "create_node"
                )
            ).scalars()
        )
    assert len(node_ops) == 2
    assert all(op.startswith(f"tmpl:{batch_id}:") for op in node_ops)


def test_instantiate_template_not_found_404(server_client: TestClient) -> None:
    """未知 template_id → 404 NOT_FOUND。"""
    research = _research_channel(server_client)
    r = _instantiate(server_client, "01K0NOPE000000000000000000", research["id"], {})
    assert r.status_code == 404, r.text
    assert rest.ErrorResponse.model_validate(r.json()).error.code is rest.ErrorCode.NOT_FOUND


def test_instantiate_builtin_triangle(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """builtin 工程三角实例化：6 节点线性 DAG + 4 角色全映射 → 6 任务 + 5 边 + 真 briefing。"""
    tri = next(t for t in server_client.get("/api/templates").json() if t["builtin"])
    research = _research_channel(server_client)
    owner = _member(server_client, "Memcyo")
    pat = _member(server_client, "Pat")
    hank = _member(server_client, "Hank")
    rin = _member(server_client, "Rin")
    agents = [pat["id"], hank["id"], rin["id"], owner["id"]]
    roles = [ro["placeholder"] for ro in tri["body"]["roles"]]
    mapping = {ro: agents[i] for i, ro in enumerate(roles)}

    r = _instantiate(server_client, tri["id"], research["id"], mapping)
    assert r.status_code == 201, r.text
    result = rest.InstantiateResult.model_validate(r.json())
    assert len(result.tasks) == 6
    detail = _canvas_detail(server_client, research["id"])
    assert len(detail["nodes"]) == 6
    assert len(detail["edges"]) == 5
    # 首节点（需求框定）为根不 blocked；下游节点 blocked。
    first = next(t for t in result.tasks if t.title == "需求框定")
    gate = next(t for t in result.tasks if t.title == "评审门")
    with seeded_engine.connect() as conn:
        assert canvas_service.is_task_blocked(conn, first.id) is False
        assert canvas_service.is_task_blocked(conn, gate.id) is True
