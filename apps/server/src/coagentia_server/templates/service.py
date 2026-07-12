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

from coagentia_contracts.constants import OPID_TMPL_PREFIX
from coagentia_contracts.entities import (
    LandingBatchRow,
    TaskPlanBody,
    TemplateBody,
    TemplateEdge,
    TemplateNode,
    TemplateRole,
)
from coagentia_contracts.enums import (
    CanvasNodeKind,
    ContractKind,
    LandingBatchKind,
    MemberKind,
    MemberRole,
    MessageKind,
    TaskLevel,
)
from coagentia_contracts.kernel.fingerprint import fingerprint
from coagentia_contracts.kernel.graph import detect_cycle
from coagentia_contracts.ws import EventType
from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError

from coagentia_server.api import ApiError
from coagentia_server.canvas import service as canvas_service
from coagentia_server.contracts import service as contracts_service
from coagentia_server.db import models
from coagentia_server.guard import service as guard_service
from coagentia_server.ledger import service
from coagentia_server.routes.serialize import (
    canvas_edge_public,
    canvas_node_public,
    message_public,
    task_contract_public,
)
from coagentia_server.tasks import service as tasks_service
from coagentia_server.templates import builtin

_TEMPLATE = models.tbl(models.Template)
_TASK = models.tbl(models.Task)
_MEMBER = models.tbl(models.Member)
_WORKSPACE = models.tbl(models.Workspace)
_MSG = models.tbl(models.Message)
_EDGE = models.tbl(models.CanvasEdge)
_PROJECT = models.tbl(models.Project)
_CHANNEL_PROJECT = models.tbl(models.ChannelProject)

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
                writes_code=bool(task.get("writes_code", False)),
                project_id=task.get("project_id"),
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


# ---------------------------------------------------------------- 治理（J11；B §12.11）


def update_template_metadata(
    conn: Connection, template_id: str, *, name: str | None, description: str | None
) -> dict[str, Any]:
    """模板元数据 PATCH（B §12.11）：仅更新提供的 name/description（None = 该字段不动），回读整行。

    结构（body）不可改——改结构走「重新存为模板」（B §4.12）；builtin 拦截与 404 由 route 前置。
    templates 表 name 无唯一约束（工作区级小表），故改名不触约束冲突（重名契约未定义，放行）。
    """
    values: dict[str, Any] = {}
    if name is not None:
        values["name"] = name
    if description is not None:
        values["description"] = description
    if values:
        conn.execute(
            update(_TEMPLATE).where(_TEMPLATE.c.id == template_id).values(**values)
        )
    row = conn.execute(select(_TEMPLATE).where(_TEMPLATE.c.id == template_id)).mappings().first()
    assert row is not None
    return dict(row)


def delete_template(conn: Connection, template_id: str) -> None:
    """模板 DELETE（B §12.11）：物理删行；builtin 拦截与 404 由 route 前置。

    历史落地批引用不阻删——landing_batches.source_ref 留 id 非 FK（账本自足），删模板后既有落地批
    仍完整可查，无级联删除。
    """
    conn.execute(delete(_TEMPLATE).where(_TEMPLATE.c.id == template_id))


# ---------------------------------------------------------------- 实例化事务器（H6；B §11.2）


def fetch_template(conn: Connection, template_id: str) -> dict[str, Any] | None:
    """单模板行（404 兜底在 route）；body 为 JSON 列（dict），caller 按需 TemplateBody 解析。"""
    row = (
        conn.execute(select(_TEMPLATE).where(_TEMPLATE.c.id == template_id)).mappings().first()
    )
    return dict(row) if row is not None else None


def missing_role_mappings(body: TemplateBody, role_mapping: dict[str, Any]) -> list[str]:
    """role_mapping 全覆盖校验（B §11.2 #1）：body.roles 每个 placeholder 须在 role_mapping。

    返回缺失占位名（保 roles 顺序）；route 据此 422 VALIDATION_FAILED，details.missing 列之。
    值可为 None（该角色节点落地为无 owner「待认领」），仅键缺失才算未覆盖。
    """
    return [r.placeholder for r in body.roles if r.placeholder not in role_mapping]


def channel_has_canvas(conn: Connection, channel_id: str) -> bool:
    """目标频道是否有画布（实例化前置校验用；无 → route 于 reserve 前 404）。

    幂等 reserve（record 走 SAVEPOINT，写入未必随外层回滚撤销）之后不得再抛可失败错误，否则残留
    悬挂 op_id 指向未建的批；故把「无画布 404」前移到 reserve 前，失败即不留 op_id、原键可重试。
    """
    return canvas_service.fetch_canvas_by_channel(conn, channel_id) is not None


def unknown_role_members(conn: Connection, role_mapping: dict[str, Any]) -> list[str]:
    """role_mapping 非空值中不在册活动成员的 member id（B §11.2；落库前 422 拒，防 FK 500）。

    格式合法但不存在的 member id 会流入 create_task.created_by / mention_ids，触发未捕获的
    FK IntegrityError（500 + 回滚）；此处一次批查活动成员（removed_at IS NULL），返回未解析的
    id（保序去重），route 据此 422 VALIDATION_FAILED，details.unknown 列之。
    """
    wanted = _dedup([mid for mid in role_mapping.values() if mid is not None])
    if not wanted:
        return []
    live = set(
        conn.execute(
            select(_MEMBER.c.id).where(_MEMBER.c.id.in_(wanted), _MEMBER.c.removed_at.is_(None))
        ).scalars()
    )
    return [mid for mid in wanted if mid not in live]


def unavailable_code_projects(
    conn: Connection, body: TemplateBody, channel_id: str
) -> list[str]:
    """返回代码节点中不存在或未绑定目标频道的 Project id（保节点顺序去重）。

    实例化不做 project 重映射；每个 `writes_code` 节点沿用模板中的 project_id。一次批查
    Project 与 channel_projects 关系，在幂等 reserve 和任何落地副作用之前完成全量复核。
    """
    project_ids = _dedup(
        [node.project_id for node in body.nodes if node.writes_code and node.project_id is not None]
    )
    if not project_ids:
        return []
    available = set(
        conn.execute(
            select(_PROJECT.c.id)
            .join(_CHANNEL_PROJECT, _CHANNEL_PROJECT.c.project_id == _PROJECT.c.id)
            .where(
                _PROJECT.c.id.in_(project_ids),
                _CHANNEL_PROJECT.c.channel_id == channel_id,
            )
        ).scalars()
    )
    return [project_id for project_id in project_ids if project_id not in available]


def _dedup(items: list[str]) -> list[str]:
    """保序去重（briefing @mention 目标可能多角色映射到同一 Agent）。"""
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _layout_positions(body: TemplateBody) -> dict[str, tuple[int, int]]:
    """按最长路径分层为模板节点算画布坐标（左→右分层，同层纵向排开）。

    否则实例化的节点全落原点 (0,0) 相互堆叠、画布只见一个节点。镜像向导 DAG 缩略图的 Kahn
    分层（lib/templates.TemplateDagThumb）；body 已由 validate_template_body 保证无环。
    """
    keys = [n.key for n in body.nodes]
    adj: dict[str, list[str]] = {k: [] for k in keys}
    indeg: dict[str, int] = {k: 0 for k in keys}
    for e in body.edges:
        if e.from_key in adj and e.to_key in indeg:
            adj[e.from_key].append(e.to_key)
            indeg[e.to_key] += 1
    depth = {k: 0 for k in keys}
    ind = dict(indeg)
    queue = [k for k in keys if ind[k] == 0]
    i = 0
    while i < len(queue):
        k = queue[i]
        i += 1
        for m in adj[k]:
            depth[m] = max(depth[m], depth[k] + 1)
            ind[m] -= 1
            if ind[m] == 0:
                queue.append(m)
    dx, dy = 260, 140
    row_count: dict[int, int] = {}
    pos: dict[str, tuple[int, int]] = {}
    for k in keys:  # 保 body.nodes 顺序 → 同层稳定纵向排开
        d = depth[k]
        r = row_count.get(d, 0)
        row_count[d] = r + 1
        pos[k] = (d * dx, r * dy)
    return pos


def _instantiate_node(
    tx: Any,
    *,
    node: TemplateNode,
    canvas: dict[str, Any],
    workspace_id: str,
    channel_id: str,
    created_by: str,
    op_id: str,
    node_hash: str,
    batch_id: str,
    owner_id: str,
    pos_x: int = 0,
    pos_y: int = 0,
) -> tuple[str, dict[str, Any]]:
    """新建单节点的 create_node 全链（照 canvas.py:create_node）→ 返回 (node_id, task_row)。

    ① 锚点系统消息（author=None/kind=SYSTEM）→ ② create_task(L2) → ③ 可选 TaskPlan 契约 →
    ④ 插 agent 节点 → ⑤ 按序广播 message→task→(contract)→node_added → ⑥ 账本登记（M6 replay
    复用：payload 携 node_id/task_id/message_id，request_hash=节点定义指纹）。
    """
    conn = tx.conn
    ts = service.now_iso()
    anchor_id = service.new_ulid()
    anchor_body = node.title.strip() or "模板 Agent 节点"
    conn.execute(
        insert(_MSG).values(
            id=anchor_id,
            workspace_id=workspace_id,
            channel_id=channel_id,
            thread_root_id=None,
            author_member_id=None,
            kind=MessageKind.SYSTEM,
            card_kind=None,
            card_ref=None,
            body=anchor_body,
            created_at=ts,
        )
    )
    task_row = tasks_service.create_task(
        tx,
        workspace_id=workspace_id,
        channel_id=channel_id,
        root_message_id=anchor_id,
        created_by=created_by,
        title=node.title,
        source_body=anchor_body,
        level=TaskLevel.L2,
        project_id=node.project_id,
        writes_code=node.writes_code,
    )
    contract_pub: dict[str, Any] | None = None
    if node.plan_skeleton is not None:
        contract_row, _ = contracts_service.submit_contract(
            tx,
            task_id=task_row["id"],
            workspace_id=workspace_id,
            kind=ContractKind.TASK_PLAN,
            body_dict=node.plan_skeleton.model_dump(mode="json"),
            created_by=created_by,
        )
        contract_pub = task_contract_public(contract_row)
    node_row = canvas_service.insert_node(
        conn,
        canvas_id=canvas["id"],
        kind=CanvasNodeKind.AGENT,
        task_id=task_row["id"],
        is_summary=False,
        system_action=None,
        command=None,
        system_status=None,
        pos_x=pos_x,
        pos_y=pos_y,
        created_at=ts,
    )
    # 按序广播：message → task →(contract)→ node_added（baseline 批末统一 bump，见 caller）。
    anchor_row = models.row_dict(
        conn.execute(select(_MSG).where(_MSG.c.id == anchor_id)).mappings().first()
    )
    tx.emit(EventType.MESSAGE_CREATED, channel_id, {"message": message_public(anchor_row, [])})
    tasks_service.emit_task_created(tx, task_row)
    if contract_pub is not None:
        tx.emit(EventType.TASK_CONTRACT_CREATED, channel_id, {"contract": contract_pub})
    tx.emit(EventType.CANVAS_NODE_ADDED, channel_id, {"node": canvas_node_public(node_row)})
    # 账本登记（幂等键 tmpl:<batch_id>:<node_key>；M6 replay 引擎据此跳过已落地节点）。
    service.record(
        conn,
        op_id,
        "create_node",
        {
            "node_key": node.key,
            "node_id": node_row["id"],
            "task_id": task_row["id"],
            "message_id": anchor_id,
        },
        request_hash=node_hash,
        batch_id=batch_id,
        actor=owner_id,
    )
    return node_row["id"], task_row


def _land_edges(
    tx: Any, canvas: dict[str, Any], body: TemplateBody, key_to_node_id: dict[str, str]
) -> None:
    """逐 TemplateEdge → canvas_edge（照 canvas.py:create_edge：无环兜底 + triplet 唯一幂等）。

    模板保存时已校验无环，故此处 detect_cycle 是落地兜底（成环 → 422 GRAPH_CYCLE）；每边
    SAVEPOINT 插入撞 triplet 唯一（replay/重复）→ 视同幂等，不重复 emit。
    """
    from coagentia_contracts import rest

    conn = tx.conn
    canvas_id = canvas["id"]
    channel_id = canvas["channel_id"]
    new_pairs = [(key_to_node_id[e.from_key], key_to_node_id[e.to_key]) for e in body.edges]
    if not new_pairs:
        return
    all_ids = canvas_service.node_ids(conn, canvas_id)
    existing_pairs = canvas_service.edge_pairs(conn, canvas_id)
    cycle = detect_cycle(all_ids, [*existing_pairs, *new_pairs])
    if cycle is not None:
        raise ApiError(
            422,
            rest.ErrorCode.GRAPH_CYCLE,
            "模板实例化连边会形成环",
            rule="V9",
            details={"cycle": cycle},
        )
    for from_id, to_id in new_pairs:
        edge_id = service.new_ulid()
        try:
            with conn.begin_nested():  # SAVEPOINT：triplet 唯一并发/replay 兜底
                conn.execute(
                    insert(_EDGE).values(
                        id=edge_id,
                        canvas_id=canvas_id,
                        from_node_id=from_id,
                        to_node_id=to_id,
                    )
                )
        except IntegrityError:
            continue  # 同 (canvas, from, to) 已存在 → 幂等跳过，不重复 emit
        edge_row = models.row_dict(
            conn.execute(select(_EDGE).where(_EDGE.c.id == edge_id)).mappings().first()
        )
        tx.emit(EventType.CANVAS_EDGE_ADDED, channel_id, {"edge": canvas_edge_public(edge_row)})


def instantiate_template(
    tx: Any,
    *,
    template_row: dict[str, Any],
    channel_id: str,
    role_mapping: dict[str, str | None],
    owner_id: str,
    batch_id: str,
) -> tuple[LandingBatchRow, list[dict[str, Any]]]:
    """模板实例化单事务编排（B §11.2；全仓首个 landing batch 消费者）。

    落地批 create_batch → 逐 TemplateNode 走 create_node 全链（幂等键 tmpl:<batch_id>:<node_key>
    三态：hit 复用不重建、new 建链）→ 逐 TemplateEdge 连边 → briefing 系统消息 @映射角色（唤醒
    信号）→ baseline 批末统一 bump → mark_done。role_mapping[node.role] 为 None → 该任务
    created_by 退回 owner（节点无 owner「待认领」，create_task 起始 owner 恒 None）。返回
    (done 态批行, 任务行表)。
    """
    conn = tx.conn
    body = TemplateBody.model_validate(template_row["body"])
    # 引用一致性 + 无环兜底（纪律 7 唯一执法点也守实例化路径）：所有写入路径存前已校验，此处是
    # 防御——悬挂 edge key 否则会在 _land_edges 的 key_to_node_id[...] 抛 KeyError（500）而非 422。
    validate_template_body(body)
    canvas = canvas_service.fetch_canvas_by_channel(conn, channel_id)
    if canvas is None:
        from coagentia_contracts import rest

        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "目标频道无画布，无法实例化")
    workspace_id = canvas["workspace_id"]

    # (a) 落地批（幂等键命名空间锚；content_hash = 模板 body 指纹，source_ref = 模板 id）。
    content_hash = fingerprint(template_row["body"])
    service.create_batch(
        conn,
        workspace_id=workspace_id,
        channel_id=channel_id,
        kind=LandingBatchKind.TMPL,
        content_hash=content_hash,
        source_ref=template_row["id"],
        confirmed_by=owner_id,
        batch_id=batch_id,
    )

    prefix = OPID_TMPL_PREFIX.format(batch_id=batch_id)
    key_to_node_id: dict[str, str] = {}
    task_rows: list[dict[str, Any]] = []
    positions = _layout_positions(body)  # 分层坐标，防节点堆叠原点

    # (c) 逐 TemplateNode：三态幂等（hit 跳过复用既有 node/task；new 建 create_node 全链）。
    for node in body.nodes:
        op_id = prefix + node.key
        node_hash = fingerprint(node.model_dump(mode="json"))
        look = service.lookup(conn, op_id, node_hash)
        if look["status"] == "hit":  # replay 前段命中：复用账本记录的 node/task，不重建
            payload = look["entry"].payload
            key_to_node_id[node.key] = payload["node_id"]
            task_rows.append(tasks_service.fetch_task(conn, payload["task_id"]))
            continue
        if look["status"] == "mismatch":  # 同键异指纹（模板漂移）→ fail-closed 停批
            # ⚠️ 不 inline 写：REST 落地事务器抛 ApiError 会令 get_tx 回滚，inline fail-closed 随之
            # 撤销（M5b 挂账缺陷）。改抛 LedgerFailClosed 携批元数据，由 app 层异常处理器在回滚后
            # 经 persist_fail_closed 独立连接落盘（契约 B §12.5 #4）。批行此刻仅存于未提交事务，
            # 回滚即消失——故携其快照供独立连接 upsert 重建为 fail_closed。
            batch_row = service._fetch_batch(conn, batch_id)
            assert batch_row is not None
            raise service.LedgerFailClosed(
                batch_row, reason="template node fingerprint mismatch"
            )
        created_by = role_mapping.get(node.role) or owner_id
        px, py = positions.get(node.key, (0, 0))
        node_id, task_row = _instantiate_node(
            tx,
            node=node,
            canvas=canvas,
            workspace_id=workspace_id,
            channel_id=channel_id,
            created_by=created_by,
            op_id=op_id,
            node_hash=node_hash,
            batch_id=batch_id,
            owner_id=owner_id,
            pos_x=px,
            pos_y=py,
        )
        key_to_node_id[node.key] = node_id
        task_rows.append(task_row)

    # (d) 逐 TemplateEdge → canvas_edge（key → node_id 映射，无环兜底 + triplet 幂等）。
    _land_edges(tx, canvas, body, key_to_node_id)

    # (e) briefing 系统消息 @角色：mention = 映射出的非 null member_id 去重（= 唤醒目标 Agent，
    #     hub 读 message_mentions → WakeReason.MENTION 即开工信号）。复用 guard._post_system_
    #     message（durable 系统消息 + message_mentions 行 by member_id + emit message.created）。
    mention_ids = _dedup([mid for mid in role_mapping.values() if mid is not None])
    # briefing 既空又无唤醒目标（用户存的模板 briefing 恒空 + 全 null 映射）→ 跳过发消息，免落
    # 一条空正文零 mention 的系统噪声消息；有话术或有 @角色 才发（后者为唤醒载体）。
    if body.briefing.strip() or mention_ids:
        guard_service._post_system_message(
            tx,
            workspace_id=workspace_id,
            channel_id=channel_id,
            body=body.briefing,
            thread_root_id=None,
            mention_member_ids=mention_ids,
            created_at=service.now_iso(),
        )

    # (f) baseline 批末统一 bump（避免逐节点 version 抖动）+ mark_done（批 :done 事实源 S4）。
    version, hash_, changed = canvas_service.advance_baseline(tx, canvas["id"])
    if changed:
        tx.emit(
            EventType.CANVAS_BASELINE_ADVANCED,
            channel_id,
            {
                "canvas_id": canvas["id"],
                "baseline_version": version,
                "baseline_hash": hash_,
            },
        )
    service.mark_done(conn, batch_id)
    batch = service._fetch_batch(conn, batch_id)
    assert batch is not None
    return batch, task_rows


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
    "channel_has_canvas",
    "delete_template",
    "fetch_template",
    "fetch_templates",
    "has_draft_layer",
    "insert_template",
    "instantiate_template",
    "missing_role_mappings",
    "serialize_canvas_to_body",
    "unknown_role_members",
    "unavailable_code_projects",
    "update_template_metadata",
    "upsert_builtin_templates",
    "validate_template_body",
]
