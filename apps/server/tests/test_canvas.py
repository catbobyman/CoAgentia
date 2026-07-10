"""画布结构端点（M3b E4）真 server 业务逻辑：节点/边 CRUD、环检测、基线快照指纹、WS 事件序。

契约形状的 mock+real 双跑在 test_conformance_dual.py；本文件断言真 server 独有业务
（建 L2 任务 + 锚点消息、C8 删节点保任务、DAG 环检测、layout 不 bump、快照与 kernel 一致）。
"""

from __future__ import annotations

from typing import Any

import pytest
from coagentia_contracts import entities, rest, ws
from coagentia_contracts.kernel.fingerprint import fingerprint
from fastapi.testclient import TestClient

# ---------------------------------------------------------------- 夹具辅助


def _build_channel(client: TestClient) -> dict[str, Any]:
    return next(c for c in client.get("/api/channels").json()["items"] if c["name"] == "build")


def _canvas_id(client: TestClient, channel: dict[str, Any]) -> str:
    return client.get(f"/api/channels/{channel['id']}/canvas").json()["canvas"]["id"]


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


def _recompute_hash(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> str:
    """按契约 A §6 规范快照口径独立重算指纹（不含 pos_x/pos_y）——与 server/kernel 对照。"""
    snap = {
        "nodes": sorted(
            (
                {
                    "id": n["id"],
                    "kind": n["kind"],
                    "task_id": n["task_id"],
                    "is_summary": n["is_summary"],
                    "system_action": n["system_action"],
                    "command": n["command"],
                }
                for n in nodes
            ),
            key=lambda e: e["id"],
        ),
        "edges": sorted(
            ({"from": e["from_node_id"], "to": e["to_node_id"]} for e in edges),
            key=lambda e: (e["from"], e["to"]),
        ),
    }
    return fingerprint(snap)


# ---------------------------------------------------------------- 读 / 空基线


def test_empty_canvas_baseline_is_empty_snapshot_fingerprint(server_client: TestClient) -> None:
    build = _build_channel(server_client)
    detail = rest.CanvasDetail.model_validate(
        server_client.get(f"/api/channels/{build['id']}/canvas").json()
    )
    assert detail.nodes == [] and detail.edges == []
    assert detail.canvas.baseline_version == 0
    assert detail.canvas.baseline_hash == fingerprint({"nodes": [], "edges": []})


# ---------------------------------------------------------------- agent 节点：建任务 + 锚点 + L2


def test_agent_node_creates_l2_task_with_anchor_and_plan(server_client: TestClient) -> None:
    build = _build_channel(server_client)
    canvas_id = _canvas_id(server_client, build)

    r = server_client.post(
        f"/api/canvases/{canvas_id}/nodes",
        json={"title": "实现登录", "kind": "agent", "task_plan": _TASK_PLAN},
    )
    assert r.status_code == 201
    mut = rest.CanvasMutation.model_validate(r.json())
    assert mut.node is not None and mut.node.kind is entities.CanvasNodeKind.AGENT
    assert mut.node.task_id is not None
    assert mut.baseline_version == 1  # 首个结构写推进基线

    # 任务落库为 L2、标题取节点 title、且带一条 TaskPlan 契约
    detail = rest.TaskDetail.model_validate(
        server_client.get(f"/api/tasks/{mut.node.task_id}").json()
    )
    assert detail.task.level is entities.TaskLevel.L2
    assert detail.task.title == "实现登录"
    assert detail.task.status is entities.TaskStatus.TODO
    assert [c.kind for c in detail.contracts] == [entities.ContractKind.TASK_PLAN]

    # 锚点系统消息：author=None、kind=system、root_message_id 指向它、UNIQUE
    assert detail.task.root_message_id is not None
    msgs = server_client.get(f"/api/channels/{build['id']}/messages").json()["items"]
    anchor = next(m for m in msgs if m["id"] == detail.task.root_message_id)
    assert anchor["author_member_id"] is None and anchor["kind"] == "system"


def test_agent_node_without_plan_creates_task_no_contract(server_client: TestClient) -> None:
    build = _build_channel(server_client)
    canvas_id = _canvas_id(server_client, build)
    r = server_client.post(
        f"/api/canvases/{canvas_id}/nodes", json={"title": "无计划节点", "kind": "agent"}
    )
    assert r.status_code == 201
    task_id = r.json()["node"]["task_id"]
    detail = server_client.get(f"/api/tasks/{task_id}").json()
    assert detail["contracts"] == []


# ---------------------------------------------------------------- system 节点


def test_system_node_action_and_check_command_gates(server_client: TestClient) -> None:
    build = _build_channel(server_client)
    canvas_id = _canvas_id(server_client, build)

    # 缺 system_action → 422 W8
    r0 = server_client.post(
        f"/api/canvases/{canvas_id}/nodes", json={"title": "系统", "kind": "system"}
    )
    assert r0.status_code == 422
    assert rest.ErrorResponse.model_validate(r0.json()).error.rule == "W8"

    # check 缺 command → 422 V14
    r1 = server_client.post(
        f"/api/canvases/{canvas_id}/nodes",
        json={"title": "检查", "kind": "system", "system_action": "check"},
    )
    assert r1.status_code == 422
    assert rest.ErrorResponse.model_validate(r1.json()).error.rule == "V14"

    # merge 节点无需 command，落 idle
    r2 = server_client.post(
        f"/api/canvases/{canvas_id}/nodes",
        json={"title": "合并", "kind": "system", "system_action": "merge"},
    )
    assert r2.status_code == 201
    node = rest.CanvasMutation.model_validate(r2.json()).node
    assert node is not None
    assert node.task_id is None
    assert node.system_action is entities.SystemAction.MERGE
    assert node.system_status is entities.SystemNodeStatus.IDLE

    # check + command 合法
    r3 = server_client.post(
        f"/api/canvases/{canvas_id}/nodes",
        json={"title": "检查", "kind": "system", "system_action": "check", "command": "pytest -q"},
    )
    assert r3.status_code == 201
    assert r3.json()["node"]["command"] == "pytest -q"


# ---------------------------------------------------------------- 删节点：保任务 + 删关联边（C8）


def test_delete_node_keeps_task_and_drops_incident_edges(server_client: TestClient) -> None:
    build = _build_channel(server_client)
    canvas_id = _canvas_id(server_client, build)
    a = server_client.post(
        f"/api/canvases/{canvas_id}/nodes", json={"title": "A", "kind": "agent"}
    ).json()["node"]
    b = server_client.post(
        f"/api/canvases/{canvas_id}/nodes",
        json={"title": "B", "kind": "system", "system_action": "merge"},
    ).json()["node"]
    server_client.post(
        f"/api/canvases/{canvas_id}/edges",
        json={"from_node_id": a["id"], "to_node_id": b["id"]},
    )

    r = server_client.delete(f"/api/canvases/{canvas_id}/nodes/{a['id']}")
    assert r.status_code == 200
    # 任务不随节点删除（引用不是副本 C8）
    assert server_client.get(f"/api/tasks/{a['task_id']}").status_code == 200
    # 关联边被连带解除
    detail = server_client.get(f"/api/channels/{build['id']}/canvas").json()
    assert [n["id"] for n in detail["nodes"]] == [b["id"]]
    assert detail["edges"] == []


# ---------------------------------------------------------------- 边环检测（DAG 守护）


def _mk_nodes(client: TestClient, canvas_id: str, n: int) -> list[dict[str, Any]]:
    out = []
    for i in range(n):
        out.append(
            client.post(
                f"/api/canvases/{canvas_id}/nodes",
                json={"title": f"N{i}", "kind": "agent"},
            ).json()["node"]
        )
    return out


def test_self_loop_rejected_as_cycle(server_client: TestClient) -> None:
    build = _build_channel(server_client)
    canvas_id = _canvas_id(server_client, build)
    (a,) = _mk_nodes(server_client, canvas_id, 1)
    r = server_client.post(
        f"/api/canvases/{canvas_id}/edges", json={"from_node_id": a["id"], "to_node_id": a["id"]}
    )
    assert r.status_code == 422
    err = rest.ErrorResponse.model_validate(r.json())
    assert err.error.code is rest.ErrorCode.GRAPH_CYCLE
    assert err.error.details == {"cycle": [a["id"]]}


def test_two_node_cycle_rejected(server_client: TestClient) -> None:
    build = _build_channel(server_client)
    canvas_id = _canvas_id(server_client, build)
    a, b = _mk_nodes(server_client, canvas_id, 2)
    assert (
        server_client.post(
            f"/api/canvases/{canvas_id}/edges",
            json={"from_node_id": a["id"], "to_node_id": b["id"]},
        ).status_code
        == 201
    )
    r = server_client.post(
        f"/api/canvases/{canvas_id}/edges", json={"from_node_id": b["id"], "to_node_id": a["id"]}
    )
    assert r.status_code == 422
    assert rest.ErrorResponse.model_validate(r.json()).error.code is rest.ErrorCode.GRAPH_CYCLE


def test_three_node_and_indirect_cycle_rejected_dag_allowed(server_client: TestClient) -> None:
    build = _build_channel(server_client)
    canvas_id = _canvas_id(server_client, build)
    a, b, c, d = _mk_nodes(server_client, canvas_id, 4)
    # 合法 DAG：a→b→c，a→d，d→c（钻石，无环）全部放行
    for fr, to in [(a, b), (b, c), (a, d), (d, c)]:
        assert (
            server_client.post(
                f"/api/canvases/{canvas_id}/edges",
                json={"from_node_id": fr["id"], "to_node_id": to["id"]},
            ).status_code
            == 201
        )
    # 间接成环 c→a（c 可达 a 的后继链）→ 拒
    r = server_client.post(
        f"/api/canvases/{canvas_id}/edges", json={"from_node_id": c["id"], "to_node_id": a["id"]}
    )
    assert r.status_code == 422
    assert rest.ErrorResponse.model_validate(r.json()).error.code is rest.ErrorCode.GRAPH_CYCLE


def test_edge_endpoint_must_belong_to_canvas(server_client: TestClient) -> None:
    build = _build_channel(server_client)
    canvas_id = _canvas_id(server_client, build)
    (a,) = _mk_nodes(server_client, canvas_id, 1)
    r = server_client.post(
        f"/api/canvases/{canvas_id}/edges",
        json={"from_node_id": a["id"], "to_node_id": "01K0AAAAAAAAAAAAAAAAAAAAAA"},
    )
    assert r.status_code == 404


def test_duplicate_edge_is_idempotent(server_client: TestClient) -> None:
    build = _build_channel(server_client)
    canvas_id = _canvas_id(server_client, build)
    a, b = _mk_nodes(server_client, canvas_id, 2)
    first = server_client.post(
        f"/api/canvases/{canvas_id}/edges", json={"from_node_id": a["id"], "to_node_id": b["id"]}
    )
    assert first.status_code == 201
    base = first.json()["baseline_version"]
    dup = server_client.post(
        f"/api/canvases/{canvas_id}/edges", json={"from_node_id": a["id"], "to_node_id": b["id"]}
    )
    # 重复连边幂等回既有边，结构未变故基线不推进
    assert dup.json()["edge"]["id"] == first.json()["edge"]["id"]
    assert dup.json()["baseline_version"] == base


# ---------------------------------------------------------------- layout 不 bump


def test_layout_put_does_not_advance_baseline(server_client: TestClient) -> None:
    build = _build_channel(server_client)
    canvas_id = _canvas_id(server_client, build)
    (a,) = _mk_nodes(server_client, canvas_id, 1)
    before = server_client.get(f"/api/channels/{build['id']}/canvas").json()["canvas"]
    r = server_client.put(
        f"/api/canvases/{canvas_id}/layout",
        json={"positions": [{"node_id": a["id"], "x": 12.5, "y": 34.0}]},
    )
    assert r.status_code == 200
    mut = rest.CanvasMutation.model_validate(r.json())
    assert mut.baseline_version == before["baseline_version"]
    assert mut.baseline_hash == before["baseline_hash"]
    assert mut.node is None and mut.edge is None
    # 坐标已落库
    node = next(
        n
        for n in server_client.get(f"/api/channels/{build['id']}/canvas").json()["nodes"]
        if n["id"] == a["id"]
    )
    assert node["pos_x"] == 12.5 and node["pos_y"] == 34.0


# ---------------------------------------------------------------- 基线快照与 kernel 一致且确定


def test_baseline_hash_matches_kernel_and_is_deterministic(server_client: TestClient) -> None:
    build = _build_channel(server_client)
    canvas_id = _canvas_id(server_client, build)
    a, b = _mk_nodes(server_client, canvas_id, 2)
    server_client.post(
        f"/api/canvases/{canvas_id}/nodes",
        json={"title": "chk", "kind": "system", "system_action": "check", "command": "make test"},
    )
    server_client.post(
        f"/api/canvases/{canvas_id}/edges", json={"from_node_id": a["id"], "to_node_id": b["id"]}
    )
    snap = server_client.get(f"/api/channels/{build['id']}/canvas").json()
    # server 存的 baseline_hash == 独立按契约 A §6 规范重算的指纹
    assert snap["canvas"]["baseline_hash"] == _recompute_hash(snap["nodes"], snap["edges"])
    # 确定性：坐标变更（layout）不改基线指纹
    server_client.put(
        f"/api/canvases/{canvas_id}/layout",
        json={"positions": [{"node_id": a["id"], "x": 99.0, "y": 88.0}]},
    )
    snap2 = server_client.get(f"/api/channels/{build['id']}/canvas").json()
    assert snap2["canvas"]["baseline_hash"] == snap["canvas"]["baseline_hash"]


# ---------------------------------------------------------------- 归档频道拒写


def test_archived_channel_rejects_canvas_writes(server_client: TestClient) -> None:
    build = _build_channel(server_client)
    canvas_id = _canvas_id(server_client, build)
    server_client.post(f"/api/channels/{build['id']}/archive")
    r = server_client.post(
        f"/api/canvases/{canvas_id}/nodes", json={"title": "x", "kind": "agent"}
    )
    assert r.status_code == 409
    err = rest.ErrorResponse.model_validate(r.json())
    assert err.error.code is rest.ErrorCode.CHANNEL_ARCHIVED


# ---------------------------------------------------------------- PATCH 节点


def test_patch_node_command_bumps_baseline_and_retitles_task(server_client: TestClient) -> None:
    build = _build_channel(server_client)
    canvas_id = _canvas_id(server_client, build)
    sys_node = server_client.post(
        f"/api/canvases/{canvas_id}/nodes",
        json={"title": "检查", "kind": "system", "system_action": "check", "command": "old"},
    ).json()["node"]
    base = server_client.get(f"/api/channels/{build['id']}/canvas").json()["canvas"][
        "baseline_version"
    ]
    r = server_client.patch(
        f"/api/canvases/{canvas_id}/nodes/{sys_node['id']}", json={"command": "new-cmd"}
    )
    assert r.status_code == 200
    mut = rest.CanvasMutation.model_validate(r.json())
    assert mut.node is not None and mut.node.command == "new-cmd"
    assert mut.baseline_version == base + 1  # command 参与快照 → 指纹变 → bump

    # agent 节点 PATCH title → 改写所引用任务标题
    agent = server_client.post(
        f"/api/canvases/{canvas_id}/nodes", json={"title": "原名", "kind": "agent"}
    ).json()["node"]
    server_client.patch(
        f"/api/canvases/{canvas_id}/nodes/{agent['id']}", json={"title": "改名后"}
    )
    assert server_client.get(f"/api/tasks/{agent['task_id']}").json()["task"]["title"] == "改名后"


def test_patch_check_node_command_cannot_be_cleared(server_client: TestClient) -> None:
    """V14 复校（code-review #2）：check 系统节点 PATCH command=null/空 → 422，不落无效态。"""
    build = _build_channel(server_client)
    canvas_id = _canvas_id(server_client, build)
    node = server_client.post(
        f"/api/canvases/{canvas_id}/nodes",
        json={"title": "检查", "kind": "system", "system_action": "check", "command": "pytest"},
    ).json()["node"]
    for bad in (None, "", "   "):
        r = server_client.patch(
            f"/api/canvases/{canvas_id}/nodes/{node['id']}", json={"command": bad}
        )
        assert r.status_code == 422, bad
        assert r.json()["error"]["rule"] == "V14"
    # 库里 command 仍为原值（拒绝未改写）。
    fresh = server_client.get(f"/api/channels/{build['id']}/canvas").json()["nodes"]
    assert next(n for n in fresh if n["id"] == node["id"])["command"] == "pytest"


# ---------------------------------------------------------------- WS 事件序


def _envelope_of(raw: dict[str, Any]) -> ws.Envelope:
    env = ws.Envelope.model_validate(raw)
    ws.EVENT_PAYLOADS[env.type].model_validate(env.data)
    return env


def _drain_types(sock: Any, count: int, last_seq: int) -> list[ws.EventType]:
    types: list[ws.EventType] = []
    for _ in range(count + 6):  # 容真 server 首连 owner online 等噪声帧
        env = _envelope_of(sock.receive_json())
        assert env.seq > last_seq
        last_seq = env.seq
        if env.type.value.startswith(("canvas.", "message.", "task")):
            types.append(env.type)
            if len(types) >= count:
                break
    return types


def test_agent_node_broadcast_sequence(server_client: TestClient) -> None:
    build = _build_channel(server_client)
    canvas_id = _canvas_id(server_client, build)
    with server_client.websocket_connect("/api/ws") as sock:
        hello = _envelope_of(sock.receive_json())
        r = server_client.post(
            f"/api/canvases/{canvas_id}/nodes",
            json={"title": "带计划节点", "kind": "agent", "task_plan": _TASK_PLAN},
        )
        assert r.status_code == 201
        seen = _drain_types(sock, 5, hello.seq)
    assert seen == [
        ws.EventType.MESSAGE_CREATED,
        ws.EventType.TASK_CREATED,
        ws.EventType.TASK_CONTRACT_CREATED,
        ws.EventType.CANVAS_NODE_ADDED,
        ws.EventType.CANVAS_BASELINE_ADVANCED,
    ]


def test_delete_node_broadcast_sequence(server_client: TestClient) -> None:
    build = _build_channel(server_client)
    canvas_id = _canvas_id(server_client, build)
    a, b = _mk_nodes(server_client, canvas_id, 2)
    server_client.post(
        f"/api/canvases/{canvas_id}/edges", json={"from_node_id": a["id"], "to_node_id": b["id"]}
    )
    with server_client.websocket_connect("/api/ws") as sock:
        hello = _envelope_of(sock.receive_json())
        server_client.delete(f"/api/canvases/{canvas_id}/nodes/{a['id']}")
        seen = _drain_types(sock, 3, hello.seq)
    # 先删关联边、再删节点、末推进基线
    assert seen == [
        ws.EventType.CANVAS_EDGE_REMOVED,
        ws.EventType.CANVAS_NODE_REMOVED,
        ws.EventType.CANVAS_BASELINE_ADVANCED,
    ]


@pytest.mark.parametrize("kind", ["nonexistent-canvas"])
def test_unknown_canvas_returns_404(server_client: TestClient, kind: str) -> None:
    r = server_client.post(
        "/api/canvases/01K0MISSINGCANVAS0000000000/nodes",
        json={"title": "x", "kind": "agent"},
    )
    assert r.status_code == 404
