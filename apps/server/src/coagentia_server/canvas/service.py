"""画布域服务层（契约 B §4.9 / 契约 A §6；M3b E4）：画布/节点/边读、基线快照与推进。

范式仿 tasks/service.py、contracts/service.py：本层只消费 contracts 包的图内核/指纹（纪律 7
单一事实源——环检测=kernel.graph.detect_cycle、快照指纹=kernel.fingerprint.fingerprint），
序列化统一走 routes/serialize.py（本层只吐 DB 行 dict）。

给 E5 的复用面（blocked 推导要挂在其上）：
- `fetch_nodes` / `fetch_edges`：按 id / (from,to) 升序的全量行；
- `node_ids` / `edge_pairs`：图内核输入的窄化形状（list[str] / list[tuple[str,str]]）——
  E5 的 derive_blocked(node_ids, edge_pairs, satisfied) 直接吃这两个，无需重查；
- `require_canvas`：canvas 行（404 兜底），E5 force-start 也要先定位画布。
"""

from __future__ import annotations

from typing import Any

from coagentia_contracts.enums import CanvasNodeKind, SystemNodeStatus, TaskStatus
from coagentia_contracts.kernel.fingerprint import fingerprint
from coagentia_contracts.kernel.graph import derive_blocked
from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Connection

from coagentia_server.api import ApiError
from coagentia_server.db import models
from coagentia_server.ledger import service

# rest.ErrorCode 只在报错点用；此处避免与 contracts.rest 循环耦合，import 局部化到函数。

_CANVAS = models.tbl(models.Canvas)
_NODE = models.tbl(models.CanvasNode)
_EDGE = models.tbl(models.CanvasEdge)
_TASK = models.tbl(models.Task)


# ---------------------------------------------------------------- 读


def fetch_canvas(conn: Connection, canvas_id: str) -> dict[str, Any] | None:
    row = conn.execute(select(_CANVAS).where(_CANVAS.c.id == canvas_id)).mappings().first()
    return dict(row) if row is not None else None


def fetch_canvas_by_channel(conn: Connection, channel_id: str) -> dict[str, Any] | None:
    """每频道恰一画布（canvases.channel_id UNIQUE）；DM 等无画布频道返回 None。"""
    row = (
        conn.execute(select(_CANVAS).where(_CANVAS.c.channel_id == channel_id))
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def require_canvas(conn: Connection, canvas_id: str) -> dict[str, Any]:
    from coagentia_contracts import rest

    canvas = fetch_canvas(conn, canvas_id)
    if canvas is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "画布不存在")
    return canvas


def fetch_nodes(conn: Connection, canvas_id: str) -> list[dict[str, Any]]:
    """本画布全部节点行，按 id 升序（快照确定性 + 读端点稳定序）。"""
    rows = conn.execute(
        select(_NODE).where(_NODE.c.canvas_id == canvas_id).order_by(_NODE.c.id)
    ).mappings()
    return [dict(r) for r in rows]


def fetch_node(conn: Connection, canvas_id: str, node_id: str) -> dict[str, Any] | None:
    """本画布内的单节点（跨画布 node_id 视同不存在——路径隔离）。"""
    row = (
        conn.execute(
            select(_NODE).where(_NODE.c.id == node_id, _NODE.c.canvas_id == canvas_id)
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def fetch_edges(conn: Connection, canvas_id: str) -> list[dict[str, Any]]:
    """本画布全部边行，按 (from, to) 升序。"""
    rows = conn.execute(
        select(_EDGE)
        .where(_EDGE.c.canvas_id == canvas_id)
        .order_by(_EDGE.c.from_node_id, _EDGE.c.to_node_id)
    ).mappings()
    return [dict(r) for r in rows]


def fetch_edge(conn: Connection, canvas_id: str, edge_id: str) -> dict[str, Any] | None:
    row = (
        conn.execute(
            select(_EDGE).where(_EDGE.c.id == edge_id, _EDGE.c.canvas_id == canvas_id)
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def node_ids(conn: Connection, canvas_id: str) -> list[str]:
    """图内核输入：本画布节点 id 列表（E5 derive_blocked 复用）。"""
    return list(
        conn.execute(
            select(_NODE.c.id).where(_NODE.c.canvas_id == canvas_id).order_by(_NODE.c.id)
        ).scalars()
    )


def edge_pairs(conn: Connection, canvas_id: str) -> list[tuple[str, str]]:
    """图内核输入：本画布边的 (from, to) 二元组列表（E5 derive_blocked / 环检测复用）。"""
    rows = conn.execute(
        select(_EDGE.c.from_node_id, _EDGE.c.to_node_id)
        .where(_EDGE.c.canvas_id == canvas_id)
        .order_by(_EDGE.c.from_node_id, _EDGE.c.to_node_id)
    ).all()
    return [(r[0], r[1]) for r in rows]


# ---------------------------------------------------------------- blocked 派生（M3b E5）


def _satisfied_nodes(conn: Connection, nodes: list[dict[str, Any]]) -> set[str]:
    """把上游"完成"语义折进 satisfied 节点集（derive_blocked 的 caller 决策，纪律 8）：
    kind=agent 且其 task.status=='done'、kind=system 且 system_status=='success' 即 satisfied。"""
    agent_task_ids = {
        n["task_id"]
        for n in nodes
        if n["kind"] == CanvasNodeKind.AGENT and n["task_id"] is not None
    }
    done: set[str] = set()
    if agent_task_ids:
        done = set(
            conn.execute(
                select(_TASK.c.id).where(
                    _TASK.c.id.in_(agent_task_ids), _TASK.c.status == TaskStatus.DONE
                )
            ).scalars()
        )
    satisfied: set[str] = set()
    for n in nodes:
        if n["kind"] == CanvasNodeKind.AGENT:
            if n["task_id"] in done:
                satisfied.add(n["id"])
        elif n["system_status"] == SystemNodeStatus.SUCCESS:
            satisfied.add(n["id"])
    return satisfied


def blocked_task_ids(conn: Connection) -> set[str]:
    """全画布派生 blocked 任务集（裁决 2：不落库，画布边 + 上游任务/系统状态实时推导）。

    逐画布载入节点/边 → 算 satisfied → contracts.kernel.graph.derive_blocked（E0b 图内核，
    权威在 server，纪律 8）→ blocked agent 节点映射回其 task_id（system 节点无任务，不入 gating
    集）。gating 只在投递层消费本集合；读面不塞派生字段（裁决 4，前端经画布快照 + 图内核自算）。
    """
    blocked: set[str] = set()
    # 先物化画布 id（避免迭代活动游标时在同连接发子查询）。
    canvas_ids = list(conn.execute(select(_CANVAS.c.id)).scalars())
    for canvas_id in canvas_ids:
        nodes = fetch_nodes(conn, canvas_id)
        if not nodes:
            continue
        satisfied = _satisfied_nodes(conn, nodes)
        blocked_nodes = derive_blocked(
            [n["id"] for n in nodes], edge_pairs(conn, canvas_id), satisfied
        )
        for n in nodes:
            if (
                n["id"] in blocked_nodes
                and n["kind"] == CanvasNodeKind.AGENT
                and n["task_id"] is not None
            ):
                blocked.add(n["task_id"])
    return blocked


def blocked_node_ids(conn: Connection, canvas_id: str) -> set[str]:
    """单画布全部 blocked 节点；系统节点触发器与 retry 共用既有 satisfied 语义。"""
    nodes = fetch_nodes(conn, canvas_id)
    if not nodes:
        return set()
    return derive_blocked(
        [n["id"] for n in nodes],
        edge_pairs(conn, canvas_id),
        _satisfied_nodes(conn, nodes),
    )


def is_task_blocked(conn: Connection, task_id: str) -> bool:
    """单任务 blocked 判定（裁决 2）：**只算该任务所在画布**，不扫全库（投递热路径省 I/O）。

    找 task 的 agent 节点 → 其画布 → 该画布 satisfied + derive_blocked（图内核，纪律 8）→ 判该
    节点是否 blocked。任务不在任何画布 → False。语义与 blocked_task_ids 逐图一致，仅范围收窄到
    唯一相关画布（O(1 画布) 取代 O(全库画布)）。
    """
    row = conn.execute(
        select(_NODE.c.id, _NODE.c.canvas_id).where(_NODE.c.task_id == task_id)
    ).first()
    if row is None:
        return False
    node_id, canvas_id = row[0], row[1]
    nodes = fetch_nodes(conn, canvas_id)
    satisfied = _satisfied_nodes(conn, nodes)
    blocked_nodes = derive_blocked(
        [n["id"] for n in nodes], edge_pairs(conn, canvas_id), satisfied
    )
    return node_id in blocked_nodes


def message_delivery_gated(conn: Connection, msg: dict[str, Any]) -> bool:
    """消息投递 gating（裁决 2）：msg 属 blocked 任务线程 → True（应压制唤醒/投递）。

    线程根 = msg.thread_root_id or msg.id；root_message_id 等于该根的任务若 blocked 则 gated。
    非任务线程消息（无匹配任务）→ False。先解析任务再算 blocked——非任务消息零派生成本，命中
    任务再走 is_task_blocked（仅该任务画布，投递每消息一算的热路径）。
    """
    root_id = msg.get("thread_root_id") or msg.get("id")
    if root_id is None:
        return False
    task_row = conn.execute(
        select(_TASK.c.id).where(_TASK.c.root_message_id == root_id)
    ).first()
    if task_row is None:
        return False
    return is_task_blocked(conn, task_row[0])


# ---------------------------------------------------------------- 基线快照与推进


def snapshot(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, Any]:
    """规范基线快照（契约 A §6）：节点取结构字段按 id 升序、边取 from/to 按 (from,to) 升序。

    **不含 pos_x/pos_y**（fingerprint 拒 float，且坐标不参与结构判定）；task_id/system_action/
    command 为 None 时由 fingerprint 的 null 剔除自然缺席（缺席 ≡ null，契约 A §2.2）。
    """
    node_entries = sorted(
        (
            {
                "id": n["id"],
                "kind": n["kind"],
                "task_id": n["task_id"],
                "is_summary": bool(n["is_summary"]),
                "system_action": n["system_action"],
                "command": n["command"],
            }
            for n in nodes
        ),
        key=lambda e: e["id"],
    )
    edge_entries = sorted(
        ({"from": e["from_node_id"], "to": e["to_node_id"]} for e in edges),
        key=lambda e: (e["from"], e["to"]),
    )
    return {"nodes": node_entries, "edges": edge_entries}


def compute_hash(conn: Connection, canvas_id: str) -> str:
    """当前 DB 态的基线指纹（契约 A §6 规范快照 → SHA-256）。"""
    return fingerprint(snapshot(fetch_nodes(conn, canvas_id), fetch_edges(conn, canvas_id)))


def advance_baseline(tx: Any, canvas_id: str) -> tuple[int, str, bool]:
    """重算快照指纹：变则原子 bump（version+1、写 hash/updated_at）返回 (新版, 新指纹, True)，
    未变则不动、返回当前 (版, 指纹, False)。

    单条 UPDATE…RETURNING 在语句内取写锁（仿 tasks/service.allocate_number），并发推进串行、
    不跳版；调用方须在本事务内「写结构 → advance_baseline」一次完成（SQLite 单写者即每库串行）。
    """
    new_hash = compute_hash(tx.conn, canvas_id)
    canvas = require_canvas(tx.conn, canvas_id)
    if new_hash == canvas["baseline_hash"]:
        return canvas["baseline_version"], canvas["baseline_hash"], False
    new_version = tx.conn.execute(
        update(_CANVAS)
        .where(_CANVAS.c.id == canvas_id)
        .values(
            baseline_version=_CANVAS.c.baseline_version + 1,
            baseline_hash=new_hash,
            updated_at=service.now_iso(),
        )
        .returning(_CANVAS.c.baseline_version)
    ).scalar_one()
    return new_version, new_hash, True


# ---------------------------------------------------------------- 写辅助


def insert_node(conn: Connection, **values: Any) -> dict[str, Any]:
    """插入节点并回读整行（新 id 由 caller 传入或此处生成）。"""
    node_id = values.get("id") or service.new_ulid()
    values["id"] = node_id
    conn.execute(insert(_NODE).values(**values))
    row = conn.execute(select(_NODE).where(_NODE.c.id == node_id)).mappings().first()
    assert row is not None
    return dict(row)


def delete_node(conn: Connection, node_id: str) -> None:
    conn.execute(delete(_NODE).where(_NODE.c.id == node_id))


def incident_edges(conn: Connection, canvas_id: str, node_id: str) -> list[dict[str, Any]]:
    """与 node_id 相关（作 from 或 to）的本画布边——删节点时须连带解除（C8）。"""
    rows = conn.execute(
        select(_EDGE).where(
            _EDGE.c.canvas_id == canvas_id,
            (_EDGE.c.from_node_id == node_id) | (_EDGE.c.to_node_id == node_id),
        )
    ).mappings()
    return [dict(r) for r in rows]


def delete_edge(conn: Connection, edge_id: str) -> None:
    conn.execute(delete(_EDGE).where(_EDGE.c.id == edge_id))


__all__ = [
    "advance_baseline",
    "blocked_task_ids",
    "blocked_node_ids",
    "compute_hash",
    "delete_edge",
    "delete_node",
    "edge_pairs",
    "fetch_canvas",
    "fetch_canvas_by_channel",
    "fetch_edge",
    "fetch_edges",
    "fetch_node",
    "fetch_nodes",
    "incident_edges",
    "insert_node",
    "is_task_blocked",
    "message_delivery_gated",
    "node_ids",
    "require_canvas",
    "snapshot",
]
