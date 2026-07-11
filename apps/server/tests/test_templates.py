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
from coagentia_server.api import ApiError
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
