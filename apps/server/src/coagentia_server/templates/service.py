"""模板域服务层（契约 B §11.1 / A §4.10；M5b H5）：画布快照序列化 + 校验 + 列表 + builtin upsert。

范式仿 canvas/service.py、contracts/service.py：本层消费 contracts 包的 `TemplateBody` 嵌套模型与
图内核（纪律 7 单一事实源——环检测 = kernel.graph.detect_cycle）。序列化只读画布（无写）；校验统一
在 `validate_template_body`（route 与 builtin upsert 共用的唯一执法点）。序列化统一用 contracts 模型
构造，落库时 `TemplateBody.model_dump(mode="json")` 转 JSON 列。

node key 生成规则（H6 实例化要用 key 映射）：序列化时按入选 task 节点顺序生成 `n{idx}`（模板内
唯一，仅需在本模板内唯一即可——H6 幂等键 `tmpl:<batch_id>:<node_key>` 以此为命名空间）；builtin
的 key 为语义命名（见 templates/builtin.py）。
"""

from __future__ import annotations

from typing import Any

from coagentia_contracts.entities import (
    TaskPlanBody,
    TemplateBody,
    TemplateEdge,
    TemplateNode,
    TemplateRole,
)
from coagentia_contracts.enums import CanvasNodeKind, ContractKind, MemberKind, MemberRole
from coagentia_contracts.kernel.graph import detect_cycle
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection, Engine

from coagentia_server.api import ApiError
from coagentia_server.canvas import service as canvas_service
from coagentia_server.contracts import service as contracts_service
from coagentia_server.db import models
from coagentia_server.ledger import service
from coagentia_server.templates import builtin

_TEMPLATE = models.tbl(models.Template)
_TASK = models.tbl(models.Task)
_MEMBER = models.tbl(models.Member)
_WORKSPACE = models.tbl(models.Workspace)

# 无 owner 节点的默认占位名（B §11.1 / A §4.10「无 owner 归待认领」）。
UNCLAIMED_PLACEHOLDER = "待认领"


# ---------------------------------------------------------------- 草稿层检测


def has_draft_layer(conn: Connection, channel_id: str) -> bool:
    """草稿层 = 拆解 proposals 的活动行（草稿态节点未落正式画布，交互 §6.9）。

    **M5 无 proposals 落地**（proposals 表归 M6，本仓尚未建表——models.py 无 Proposal ORM）：故
    草稿层恒空，存为模板的 409 约束（B §11.1 #2 二值）实际只由「无正式 task 节点」触发。保留本
    函数占位该二值约束的结构，M6 建 proposals 后在此接真（按 channel_id 查活动提案行）。
    """
    _ = (conn, channel_id)  # M5 恒无草稿层（proposals 表归 M6）——占位保留二值约束结构。
    return False


# ---------------------------------------------------------------- 序列化（画布快照 → TemplateBody）


def serialize_canvas_to_body(
    conn: Connection,
    channel_id: str,
    *,
    role_placeholders: dict[str, str] | None = None,
    include_node_ids: list[str] | None = None,
) -> TemplateBody | None:
    """读目标频道画布快照 → `TemplateBody`（B §11.1 / A §4.10 提取规则）。

    - 仅 **task 类节点**（kind='agent'；system 节点滤除）；`include_node_ids` 缺省 = 全部 task 节；
    - 角色占位默认按节点 owner **去重**（owner member_id → 其成员名为默认占位名），无 owner 归
      「待认领」；`role_placeholders`（{member_id: 占位名}）覆盖默认占位名；
    - `plan_skeleton` 取该任务当前活动 TaskPlan 契约 body（无则 None）；
    - **pos 不入**（布局非结构，TemplateNode 无坐标字段天然满足）；edges 仅保留两端都在入选 task
      节点内的边（system 节点相关边天然剔除）；
    - node key 按入选顺序生成 `n{idx}`（模板内唯一，H6 映射/连边引用）。

    无画布 / DM 频道 → None（caller 转 409 TEMPLATE_CANVAS_NOT_READY）。
    """
    canvas = canvas_service.fetch_canvas_by_channel(conn, channel_id)
    if canvas is None:
        return None
    overrides = role_placeholders or {}

    nodes = canvas_service.fetch_nodes(conn, canvas["id"])
    task_nodes = [
        n for n in nodes if n["kind"] == CanvasNodeKind.AGENT and n["task_id"] is not None
    ]
    if include_node_ids is not None:
        wanted = set(include_node_ids)
        task_nodes = [n for n in task_nodes if n["id"] in wanted]

    # 批取入选任务行（owner 去重 + 标题），再批取 owner 成员名（默认占位名）——免 N+1。
    task_ids = [n["task_id"] for n in task_nodes]
    task_rows: dict[str, dict[str, Any]] = {}
    if task_ids:
        rows = conn.execute(select(_TASK).where(_TASK.c.id.in_(task_ids))).mappings()
        task_rows = {r["id"]: dict(r) for r in rows}
    owner_ids = {
        t["owner_member_id"]
        for t in task_rows.values()
        if t["owner_member_id"] is not None
    }
    member_names = _member_names(conn, owner_ids)

    node_key: dict[str, str] = {}  # node_id → 模板 key（连边引用）
    template_nodes: list[TemplateNode] = []
    role_order: list[str] = []  # 占位名去重保序
    for idx, n in enumerate(task_nodes):
        key = f"n{idx}"
        node_key[n["id"]] = key
        task = task_rows.get(n["task_id"], {})
        placeholder = _placeholder_for(task.get("owner_member_id"), overrides, member_names)
        if placeholder not in role_order:
            role_order.append(placeholder)
        template_nodes.append(
            TemplateNode(
                key=key,
                title=task.get("title", ""),
                role=placeholder,
                plan_skeleton=_plan_skeleton(conn, n["task_id"]),
            )
        )

    template_edges = [
        TemplateEdge(from_key=node_key[a], to_key=node_key[b])
        for a, b in canvas_service.edge_pairs(conn, canvas["id"])
        if a in node_key and b in node_key
    ]
    roles = [TemplateRole(placeholder=p) for p in role_order]
    return TemplateBody(
        nodes=template_nodes, edges=template_edges, roles=roles, briefing=""
    )


def _placeholder_for(
    owner_id: str | None, overrides: dict[str, str], member_names: dict[str, str]
) -> str:
    """节点占位名：无 owner → 待认领；有 override → 用之；否则默认 = owner 成员名。"""
    if owner_id is None:
        return UNCLAIMED_PLACEHOLDER
    if owner_id in overrides:
        return overrides[owner_id]
    return member_names.get(owner_id, owner_id)  # 无名兜底用 id（不应发生）


def _member_names(conn: Connection, member_ids: set[str]) -> dict[str, str]:
    if not member_ids:
        return {}
    rows = conn.execute(
        select(_MEMBER.c.id, _MEMBER.c.name).where(_MEMBER.c.id.in_(member_ids))
    ).all()
    return {r[0]: r[1] for r in rows}


def _plan_skeleton(conn: Connection, task_id: str) -> TaskPlanBody | None:
    """该任务当前活动 TaskPlan 契约 body → TaskPlanBody（无则 None）。"""
    row = contracts_service.active_contract(conn, task_id, ContractKind.TASK_PLAN)
    if row is None:
        return None
    body = row["body"]
    if not isinstance(body, dict):
        return None
    return TaskPlanBody.model_validate(body)


# ---------------------------------------------------------------- 校验（route+builtin 共用执法点）


def validate_template_body(body: TemplateBody) -> None:
    """TemplateBody 引用一致性 + 无环校验（B §11.1 #4；纪律 7 唯一执法点）。

    - node key 模板内唯一（H6 映射前提）；
    - node.role ∈ roles.placeholder；edge.from_key/to_key ∈ nodes.key（违反 → 422
      VALIDATION_FAILED，details.field 指明字段路径）；
    - detect_cycle（复用 kernel/graph，纪律 7）成环 → 422 GRAPH_CYCLE，details.cycle。
    """
    from coagentia_contracts import rest

    node_keys = {n.key for n in body.nodes}
    if len(node_keys) != len(body.nodes):
        raise ApiError(
            422,
            rest.ErrorCode.VALIDATION_FAILED,
            "模板节点 key 必须模板内唯一",
            rule="B§11.1",
            details={"field": "nodes.key"},
        )
    placeholders = {r.placeholder for r in body.roles}
    for n in body.nodes:
        if n.role not in placeholders:
            raise ApiError(
                422,
                rest.ErrorCode.VALIDATION_FAILED,
                f"节点 role「{n.role}」未在 roles 占位表中登记",
                rule="B§11.1",
                details={"field": "nodes.role", "value": n.role},
            )
    for e in body.edges:
        for endpoint, field in ((e.from_key, "edges.from_key"), (e.to_key, "edges.to_key")):
            if endpoint not in node_keys:
                raise ApiError(
                    422,
                    rest.ErrorCode.VALIDATION_FAILED,
                    f"边端点「{endpoint}」不在 nodes.key 中",
                    rule="B§11.1",
                    details={"field": field, "value": endpoint},
                )
    cycle = detect_cycle(
        sorted(node_keys), [(e.from_key, e.to_key) for e in body.edges]
    )
    if cycle is not None:
        raise ApiError(
            422,
            rest.ErrorCode.GRAPH_CYCLE,
            "模板 DAG 不得成环",
            rule="V9",
            details={"cycle": cycle},
        )


# ---------------------------------------------------------------- 列表 / 落库


def fetch_templates(conn: Connection, workspace_id: str) -> list[dict[str, Any]]:
    """工作区级列表（B §11.1 #3）：builtin 置前（builtin desc），再按 created_at 升序。"""
    rows = conn.execute(
        select(_TEMPLATE)
        .where(_TEMPLATE.c.workspace_id == workspace_id)
        .order_by(_TEMPLATE.c.builtin.desc(), _TEMPLATE.c.created_at)
    ).mappings()
    return [dict(r) for r in rows]


def insert_template(
    conn: Connection,
    *,
    workspace_id: str,
    name: str,
    description: str,
    body: TemplateBody,
    created_by: str,
    builtin_flag: bool = False,
    template_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """落库一行模板并回读整行（body → JSON 列；builtin bool → 0/1）。"""
    tid = template_id or service.new_ulid()
    conn.execute(
        insert(_TEMPLATE).values(
            id=tid,
            workspace_id=workspace_id,
            name=name,
            description=description,
            body=body.model_dump(mode="json"),
            builtin=1 if builtin_flag else 0,
            created_by_member_id=created_by,
            created_at=created_at or service.now_iso(),
        )
    )
    row = conn.execute(select(_TEMPLATE).where(_TEMPLATE.c.id == tid)).mappings().first()
    assert row is not None
    return dict(row)


# ---------------------------------------------------------------- builtin 启动 upsert


def _owner_member_id(conn: Connection, workspace_id: str) -> str | None:
    """该 workspace 的 Owner 人类成员 id（builtin created_by；无则 None → 跳过该 workspace）。"""
    return conn.execute(
        select(_MEMBER.c.id)
        .where(
            _MEMBER.c.workspace_id == workspace_id,
            _MEMBER.c.kind == MemberKind.HUMAN,
            _MEMBER.c.role == MemberRole.OWNER,
        )
        .limit(1)
    ).scalar()


def upsert_builtin_templates(engine: Engine) -> None:
    """server 启动对每 workspace upsert 工程三角 builtin（B §11.1 #3；不可删改、重启幂等）。

    幂等键 = (workspace_id, name=工程三角, builtin=1)：存在则更新 description/body（随版本迭代改
    body，不走迁移数据行——A §4.10 裁决），不存在则插入。空库（无 workspace）优雅跳过（冷启动
    empty_server_client）；workspace 无 Owner 成员亦跳过（created_by FK 不可空）。builtin body 也
    过 validate_template_body 同一执法点（纪律 7）。
    """
    body = builtin.build_triangle_body()
    validate_template_body(body)
    body_json = body.model_dump(mode="json")
    with engine.begin() as conn:
        workspace_ids = list(conn.execute(select(_WORKSPACE.c.id)).scalars())
        for ws_id in workspace_ids:
            owner = _owner_member_id(conn, ws_id)
            if owner is None:
                continue
            existing = conn.execute(
                select(_TEMPLATE.c.id).where(
                    _TEMPLATE.c.workspace_id == ws_id,
                    _TEMPLATE.c.name == builtin.BUILTIN_TRIANGLE_NAME,
                    _TEMPLATE.c.builtin == 1,
                )
            ).scalar()
            if existing is None:
                conn.execute(
                    insert(_TEMPLATE).values(
                        id=service.new_ulid(),
                        workspace_id=ws_id,
                        name=builtin.BUILTIN_TRIANGLE_NAME,
                        description=builtin.BUILTIN_TRIANGLE_DESCRIPTION,
                        body=body_json,
                        builtin=1,
                        created_by_member_id=owner,
                        created_at=service.now_iso(),
                    )
                )
            else:
                conn.execute(
                    update(_TEMPLATE)
                    .where(_TEMPLATE.c.id == existing)
                    .values(
                        description=builtin.BUILTIN_TRIANGLE_DESCRIPTION, body=body_json
                    )
                )


__all__ = [
    "UNCLAIMED_PLACEHOLDER",
    "fetch_templates",
    "has_draft_layer",
    "insert_template",
    "serialize_canvas_to_body",
    "upsert_builtin_templates",
    "validate_template_body",
]
