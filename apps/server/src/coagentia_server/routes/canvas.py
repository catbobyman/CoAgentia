"""画布结构 REST 端点（契约 B §4.9 / 契约 A §6；M3b E4）：快照读 + 节点/边 CRUD + 布局。

范式照抄 routes/tasks.py（router 前缀 /api、acting_member 身份、ApiError 报错、tx.emit 广播、
提交后按序发射）；图内核（环检测）与基线指纹集中在 canvas/service.py + contracts.kernel
（纪律 7 单一事实源）。每画布结构写在同一 tx 事务内「读基线 → 校验 → 写 → bump」一次完成
（SQLite 单写者即每库串行）；坐标写（layout PUT）不推进基线（契约 A §6）。

retry（POST /canvas-nodes/{id}/retry）属 M6 系统节点重跑，本里程碑不实现（ENDPOINTS_M3 登记但
E4 不 serve——一致性测试显式注明为 M6 缺口，M2 C4 先例）。
"""

from __future__ import annotations

from typing import Any

from coagentia_contracts import rest
from coagentia_contracts.enums import (
    CanvasNodeKind,
    ContractKind,
    MessageKind,
    SystemAction,
    SystemNodeStatus,
    TaskLevel,
)
from coagentia_contracts.kernel.graph import detect_cycle
from coagentia_contracts.ws import EventType
from fastapi import APIRouter, Depends, Request
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError

from coagentia_server.api import ApiError
from coagentia_server.canvas import service as canvas_service
from coagentia_server.contracts import service as contracts_service
from coagentia_server.db import models
from coagentia_server.deps import Tx, acting_member, get_tx
from coagentia_server.ledger import service
from coagentia_server.routes.serialize import (
    canvas_edge_public,
    canvas_node_public,
    canvas_public,
    message_public,
    task_contract_public,
    task_public,
)
from coagentia_server.tasks import service as tasks_service

router = APIRouter(prefix="/api", tags=["canvas"])

_CANVAS = models.tbl(models.Canvas)
_NODE = models.tbl(models.CanvasNode)
_MSG = models.tbl(models.Message)
_CHANNEL = models.tbl(models.Channel)
_TASK = models.tbl(models.Task)
_PROJECT = models.tbl(models.Project)
_CHANNEL_PROJECT = models.tbl(models.ChannelProject)


# ---------------------------------------------------------------- 共用门


def _writable_canvas(tx: Tx, canvas_id: str) -> dict[str, Any]:
    """定位画布（404 兜底）+ 归档频道写门（契约 B §7：归档 → 一切写端点 CHANNEL_ARCHIVED）。"""
    canvas = canvas_service.require_canvas(tx.conn, canvas_id)
    channel = (
        tx.conn.execute(select(_CHANNEL).where(_CHANNEL.c.id == canvas["channel_id"]))
        .mappings()
        .first()
    )
    if channel is not None and channel["archived_at"] is not None:
        raise ApiError(
            409, rest.ErrorCode.CHANNEL_ARCHIVED, "归档频道不可修改画布", rule="FR-1.3"
        )
    return canvas


def _emit_baseline(tx: Tx, canvas: dict[str, Any]) -> tuple[int, str]:
    """重算快照 → 变则 bump + emit canvas.baseline_advanced；返回最新 (version, hash)。"""
    version, hash_, changed = canvas_service.advance_baseline(tx, canvas["id"])
    if changed:
        tx.emit(
            EventType.CANVAS_BASELINE_ADVANCED,
            canvas["channel_id"],
            {
                "canvas_id": canvas["id"],
                "baseline_version": version,
                "baseline_hash": hash_,
            },
        )
    return version, hash_


# ---------------------------------------------------------------- 快照读


@router.get("/channels/{channel_id}/canvas", response_model=rest.CanvasDetail)
def get_canvas(channel_id: str, tx: Tx = Depends(get_tx)) -> Any:
    """CanvasDetail（画布头 + 全节点 + 全边）；无画布频道（DM）→ 404（mock 形状源一致）。"""
    canvas = canvas_service.fetch_canvas_by_channel(tx.conn, channel_id)
    if canvas is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "频道无画布")
    return {
        "canvas": canvas_public(canvas),
        "nodes": [canvas_node_public(n) for n in canvas_service.fetch_nodes(tx.conn, canvas["id"])],
        "edges": [canvas_edge_public(e) for e in canvas_service.fetch_edges(tx.conn, canvas["id"])],
    }


# ---------------------------------------------------------------- 节点：增


@router.post(
    "/canvases/{canvas_id}/nodes", response_model=rest.CanvasMutation, status_code=201
)
def create_node(
    canvas_id: str, body: rest.NodeCreate, request: Request, tx: Tx = Depends(get_tx)
) -> Any:
    """新增画布节点（B §4.9）。

    kind='agent'：系统代发锚点消息（thread_root_id=None, author=None, kind=system）→ create_task
    (level=L2, root_message_id=锚点)（第三建任务途径，引用不是副本 C8）→ 若带 task_plan 则提交
    TaskPlan 契约（复用 contracts/service）→ 插 agent 节点；按序广播 message.created → task.created
    →（有 task_plan 则 task_contract.created）→ canvas.node_added → canvas.baseline_advanced。

    kind='system'：system_action 必填（W8），action='check' 须附 command（V14）；插 system 节点
    （system_status=idle）；广播 canvas.node_added → canvas.baseline_advanced。
    """
    canvas = _writable_canvas(tx, canvas_id)
    me = acting_member(request, tx.conn)
    ts = service.now_iso()

    if body.kind == CanvasNodeKind.AGENT:
        if body.writes_code and body.project_id is None:
            raise ApiError(
                422,
                rest.ErrorCode.VALIDATION_FAILED,
                "writes_code 任务必须选择已绑定 Project",
                rule="W2",
                details={"field": "project_id"},
            )
        if body.project_id is not None:
            bound = tx.conn.execute(
                select(_PROJECT.c.id)
                .select_from(
                    _PROJECT.join(
                        _CHANNEL_PROJECT,
                        _CHANNEL_PROJECT.c.project_id == _PROJECT.c.id,
                    )
                )
                .where(
                    _PROJECT.c.id == body.project_id,
                    _PROJECT.c.workspace_id == canvas["workspace_id"],
                    _CHANNEL_PROJECT.c.channel_id == canvas["channel_id"],
                )
            ).first()
            if bound is None:
                raise ApiError(
                    422,
                    rest.ErrorCode.VALIDATION_FAILED,
                    "Project 未绑定当前频道",
                    rule="W2",
                    details={"field": "project_id"},
                )
        # 1) 锚点系统消息（create_task 要求 root_message_id UNIQUE NOT NULL）。
        anchor_id = service.new_ulid()
        anchor_body = body.title.strip() or "画布 Agent 节点"
        tx.conn.execute(
            insert(_MSG).values(
                id=anchor_id,
                workspace_id=canvas["workspace_id"],
                channel_id=canvas["channel_id"],
                thread_root_id=None,
                author_member_id=None,
                kind=MessageKind.SYSTEM,
                card_kind=None,
                card_ref=None,
                body=anchor_body,
                created_at=ts,
            )
        )
        # 2) 建 L2 任务（节点即正式立项）。
        task_row = tasks_service.create_task(
            tx,
            workspace_id=canvas["workspace_id"],
            channel_id=canvas["channel_id"],
            root_message_id=anchor_id,
            created_by=me["id"],
            title=body.title,
            source_body=anchor_body,
            level=TaskLevel.L2,
            project_id=body.project_id,
            writes_code=body.writes_code,
        )
        # 3) 可选 TaskPlan 契约（body 已由 rest.NodeCreate.task_plan 过 TaskPlanBody 校验）。
        contract_pub: dict[str, Any] | None = None
        if body.task_plan is not None:
            contract_row, _ = contracts_service.submit_contract(
                tx,
                task_id=task_row["id"],
                workspace_id=canvas["workspace_id"],
                kind=ContractKind.TASK_PLAN,
                body_dict=body.task_plan.model_dump(mode="json"),
                created_by=me["id"],
            )
            contract_pub = task_contract_public(contract_row)
        # 4) agent 节点（task_id 引用；CHECK ck_canvas_nodes_agent_needs_task 兜底）。
        node = canvas_service.insert_node(
            tx.conn,
            canvas_id=canvas_id,
            kind=CanvasNodeKind.AGENT,
            task_id=task_row["id"],
            is_summary=False,
            system_action=None,
            command=None,
            system_status=None,
            pos_x=0,
            pos_y=0,
            created_at=ts,
        )
        # 5) 按序广播：message → task →(contract)→ node → baseline。
        tx.emit(
            EventType.MESSAGE_CREATED,
            canvas["channel_id"],
            {"message": message_public(_fetch_msg(tx, anchor_id), [])},
        )
        tasks_service.emit_task_created(tx, task_row)
        if contract_pub is not None:
            # 契约随节点落地即广播（否则任务详情契约卡须刷新才现——与 contracts 端点口径一致）。
            tx.emit(
                EventType.TASK_CONTRACT_CREATED, canvas["channel_id"], {"contract": contract_pub}
            )
    else:  # kind='system'
        if body.writes_code or body.project_id is not None:
            raise ApiError(
                422,
                rest.ErrorCode.VALIDATION_FAILED,
                "系统节点不可携带任务交付字段",
                rule="W2",
                details={"fields": ["writes_code", "project_id"]},
            )
        if body.system_action is None:
            raise ApiError(
                422,
                rest.ErrorCode.VALIDATION_FAILED,
                "系统节点必须指定 system_action",
                rule="W8",
                details={"field": "system_action"},
            )
        if body.system_action == SystemAction.CHECK and not (body.command or "").strip():
            raise ApiError(
                422,
                rest.ErrorCode.VALIDATION_FAILED,
                "check 系统节点必须附 command",
                rule="V14",
                details={"field": "command"},
            )
        node = canvas_service.insert_node(
            tx.conn,
            canvas_id=canvas_id,
            kind=CanvasNodeKind.SYSTEM,
            task_id=None,
            is_summary=False,
            system_action=body.system_action,
            command=body.command,
            system_status=SystemNodeStatus.IDLE,
            pos_x=0,
            pos_y=0,
            created_at=ts,
        )

    node_pub = canvas_node_public(node)
    tx.emit(EventType.CANVAS_NODE_ADDED, canvas["channel_id"], {"node": node_pub})
    version, hash_ = _emit_baseline(tx, canvas)
    return {"baseline_version": version, "baseline_hash": hash_, "node": node_pub}


# ---------------------------------------------------------------- 节点：改


@router.patch("/canvases/{canvas_id}/nodes/{node_id}", response_model=rest.CanvasMutation)
def patch_node(
    canvas_id: str,
    node_id: str,
    body: rest.NodePatch,
    request: Request,
    tx: Tx = Depends(get_tx),
) -> Any:
    """改节点标题 / check 命令（B §4.9）。

    title：agent 节点标题即所引用任务标题（节点无 title 列，引用不是副本 C8）——改写 tasks.title
    并广播 task.updated 使看板/画布同步；system 节点无标题落点故忽略（owner 改派走 tasks/assign）。
    command：改 canvas_nodes.command（参与基线快照）。快照指纹变则 bump + baseline_advanced；
    总是广播 canvas.node_updated。
    """
    canvas = _writable_canvas(tx, canvas_id)
    node = canvas_service.fetch_node(tx.conn, canvas_id, node_id)
    if node is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "画布节点不存在")
    acting_member(request, tx.conn)  # 身份校验（裁决 7 全员可用，无角色门）
    changes = body.model_dump(exclude_unset=True)

    if "command" in changes:
        # V14 复校（与 create_node 同门）：check 系统节点的 command 不得被清空/置 null。
        if (
            node["kind"] == CanvasNodeKind.SYSTEM
            and node["system_action"] == SystemAction.CHECK
            and not (changes["command"] or "").strip()
        ):
            raise ApiError(
                422,
                rest.ErrorCode.VALIDATION_FAILED,
                "check 系统节点必须附 command",
                rule="V14",
                details={"field": "command"},
            )
        tx.conn.execute(
            _NODE.update().where(_NODE.c.id == node_id).values(command=changes["command"])
        )
    if changes.get("title") is not None and node["kind"] == CanvasNodeKind.AGENT:
        tx.conn.execute(
            _TASK.update().where(_TASK.c.id == node["task_id"]).values(title=changes["title"])
        )
        task_row = tasks_service.fetch_task(tx.conn, node["task_id"])
        # PATCH 不写 task_events；task.updated 且 change=None（契约 C §6.4 放宽，同 patch_task）。
        tx.emit(
            EventType.TASK_UPDATED,
            canvas["channel_id"],
            {"task": task_public(task_row), "change": None},
        )

    fresh = canvas_service.fetch_node(tx.conn, canvas_id, node_id)
    assert fresh is not None
    node_pub = canvas_node_public(fresh)
    tx.emit(EventType.CANVAS_NODE_UPDATED, canvas["channel_id"], {"node": node_pub})
    version, hash_ = _emit_baseline(tx, canvas)
    return {"baseline_version": version, "baseline_hash": hash_, "node": node_pub}


# ---------------------------------------------------------------- 节点：删


@router.delete("/canvases/{canvas_id}/nodes/{node_id}", response_model=rest.CanvasMutation)
def delete_node(canvas_id: str, node_id: str, tx: Tx = Depends(get_tx)) -> Any:
    """删节点 = 解除引用**不删任务**（C8）：删关联边（各 emit edge_removed）→ 删节点 →
    emit node_removed{node_id} → bump + baseline_advanced。"""
    canvas = _writable_canvas(tx, canvas_id)
    node = canvas_service.fetch_node(tx.conn, canvas_id, node_id)
    if node is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "画布节点不存在")

    for edge in canvas_service.incident_edges(tx.conn, canvas_id, node_id):
        canvas_service.delete_edge(tx.conn, edge["id"])
        tx.emit(EventType.CANVAS_EDGE_REMOVED, canvas["channel_id"], {"edge_id": edge["id"]})
    canvas_service.delete_node(tx.conn, node_id)
    tx.emit(EventType.CANVAS_NODE_REMOVED, canvas["channel_id"], {"node_id": node_id})
    version, hash_ = _emit_baseline(tx, canvas)
    return {"baseline_version": version, "baseline_hash": hash_, "node": None}


# ---------------------------------------------------------------- 边：增


@router.post(
    "/canvases/{canvas_id}/edges", response_model=rest.CanvasMutation, status_code=201
)
def create_edge(canvas_id: str, body: rest.EdgeCreate, tx: Tx = Depends(get_tx)) -> Any:
    """连边（B §4.9）：校验 from/to 属本画布 → 现有边 + 新边跑 detect_cycle，成环 422
    GRAPH_CYCLE（自环即单节点环）→ SAVEPOINT 插入（triplet 唯一兜底，重复视同幂等回既有边）→
    emit edge_added → bump + baseline_advanced。"""
    canvas = _writable_canvas(tx, canvas_id)
    ids = set(canvas_service.node_ids(tx.conn, canvas_id))
    for endpoint in (body.from_node_id, body.to_node_id):
        if endpoint not in ids:
            raise ApiError(404, rest.ErrorCode.NOT_FOUND, "边端点节点不属于本画布")

    pairs = canvas_service.edge_pairs(tx.conn, canvas_id)
    cycle = detect_cycle(list(ids), [*pairs, (body.from_node_id, body.to_node_id)])
    if cycle is not None:
        raise ApiError(
            422,
            rest.ErrorCode.GRAPH_CYCLE,
            "连边会形成环，画布须保持 DAG",
            rule="V9",
            details={"cycle": cycle},
        )

    edge_id = service.new_ulid()
    edge_tbl = models.tbl(models.CanvasEdge)
    try:
        with tx.conn.begin_nested():  # SAVEPOINT：triplet 唯一并发兜底（范式同 contracts 修订链）
            tx.conn.execute(
                insert(edge_tbl).values(
                    id=edge_id,
                    canvas_id=canvas_id,
                    from_node_id=body.from_node_id,
                    to_node_id=body.to_node_id,
                )
            )
    except IntegrityError:
        # 同 (canvas, from, to) 已存在（重复连边）——幂等回既有边，结构未变故基线不推进。
        existing = (
            tx.conn.execute(
                select(edge_tbl).where(
                    edge_tbl.c.canvas_id == canvas_id,
                    edge_tbl.c.from_node_id == body.from_node_id,
                    edge_tbl.c.to_node_id == body.to_node_id,
                )
            )
            .mappings()
            .first()
        )
        if existing is None:  # 非 triplet 冲突（防御：其它完整性错误不吞）
            raise
        return {
            "baseline_version": canvas["baseline_version"],
            "baseline_hash": canvas["baseline_hash"],
            "edge": canvas_edge_public(dict(existing)),
        }

    edge_row = tx.conn.execute(select(edge_tbl).where(edge_tbl.c.id == edge_id)).mappings().first()
    assert edge_row is not None
    edge_pub = canvas_edge_public(dict(edge_row))
    tx.emit(EventType.CANVAS_EDGE_ADDED, canvas["channel_id"], {"edge": edge_pub})
    version, hash_ = _emit_baseline(tx, canvas)
    return {"baseline_version": version, "baseline_hash": hash_, "edge": edge_pub}


# ---------------------------------------------------------------- 边：删


@router.delete("/canvases/{canvas_id}/edges/{edge_id}", response_model=rest.CanvasMutation)
def delete_edge(canvas_id: str, edge_id: str, tx: Tx = Depends(get_tx)) -> Any:
    """删边 → emit edge_removed → bump + baseline_advanced。"""
    canvas = _writable_canvas(tx, canvas_id)
    edge = canvas_service.fetch_edge(tx.conn, canvas_id, edge_id)
    if edge is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "画布边不存在")
    canvas_service.delete_edge(tx.conn, edge_id)
    tx.emit(EventType.CANVAS_EDGE_REMOVED, canvas["channel_id"], {"edge_id": edge_id})
    version, hash_ = _emit_baseline(tx, canvas)
    return {"baseline_version": version, "baseline_hash": hash_, "edge": None}


# ---------------------------------------------------------------- 布局（不推进基线）


@router.put("/canvases/{canvas_id}/layout", response_model=rest.CanvasMutation)
def put_layout(canvas_id: str, body: rest.LayoutPut, tx: Tx = Depends(get_tx)) -> Any:
    """整批坐标覆盖（B §4.9）：pos_x/pos_y 不参与基线快照（契约 A §6）故**不 bump**；
    只更新本画布内节点、emit canvas.layout_updated；返回 CanvasMutation（基线不变）。"""
    canvas = _writable_canvas(tx, canvas_id)
    ids = set(canvas_service.node_ids(tx.conn, canvas_id))
    applied = [p for p in body.positions if p.node_id in ids]
    for p in applied:
        tx.conn.execute(
            _NODE.update()
            .where(_NODE.c.id == p.node_id, _NODE.c.canvas_id == canvas_id)
            .values(pos_x=p.x, pos_y=p.y)
        )
    tx.emit(
        EventType.CANVAS_LAYOUT_UPDATED,
        canvas["channel_id"],
        {
            "canvas_id": canvas_id,
            "positions": [{"node_id": p.node_id, "x": p.x, "y": p.y} for p in applied],
        },
    )
    return {
        "baseline_version": canvas["baseline_version"],
        "baseline_hash": canvas["baseline_hash"],
        "node": None,
        "edge": None,
    }


# ---------------------------------------------------------------- 局部辅助


def _fetch_msg(tx: Tx, message_id: str) -> dict[str, Any]:
    return models.row_dict(
        tx.conn.execute(select(_MSG).where(_MSG.c.id == message_id)).mappings().first()
    )
