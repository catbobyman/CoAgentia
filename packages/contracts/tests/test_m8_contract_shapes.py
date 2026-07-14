"""M8 契约登记的聚焦形状测试（L0）：W9 放行档、汇总协调状态、原子建边入参、rule 目录。

契约 A v1.0.12（summary_runs + canvas_nodes.upstream_policy）/ B v1.5.1
（NodeCreate.upstream_node_ids + replan 403 rule=O8，错误码目录仍 29）。
迁移 0012 与内核 W9 双档语义归 M8b L7，本文件只钉形状。
"""

from coagentia_contracts import constants, entities, rest
from coagentia_contracts.enums import CanvasNodeKind, SystemAction, UpstreamPolicy

U1 = "01KX0000000000000000000001"
U2 = "01KX0000000000000000000002"
U3 = "01KX0000000000000000000003"
U4 = "01KX0000000000000000000004"
TS = "2026-07-14T12:00:00.000Z"


def test_upstream_policy_values() -> None:
    """W9 两档可枚举：strict（现状）/ partial（终态即放行）。"""
    assert {p.value for p in UpstreamPolicy} == {"strict", "partial"}


def test_canvas_node_upstream_policy_defaults_strict() -> None:
    """canvas_nodes.upstream_policy 默认 strict——旧载荷（无该键）行为不变（向后兼容）。"""
    legacy = entities.CanvasNodePublic.model_validate({
        "id": U1, "canvas_id": U2, "kind": "agent", "task_id": U3, "created_at": TS,
    })
    assert legacy.upstream_policy is UpstreamPolicy.STRICT

    summary = entities.CanvasNodePublic.model_validate({
        "id": U1, "canvas_id": U2, "kind": "agent", "task_id": U3, "is_summary": True,
        "upstream_policy": "partial", "created_at": TS,
    })
    assert summary.upstream_policy is UpstreamPolicy.PARTIAL and summary.is_summary is True


def test_summary_run_roundtrip() -> None:
    """SummaryRun（O8 协调状态，§6.4）：三计数默认 0、指纹/阻断可空、直挂 workspace_id。"""
    fresh = entities.SummaryRunPublic.model_validate({
        "task_id": U1, "canvas_id": U2, "workspace_id": U3, "created_at": TS, "updated_at": TS,
    })
    assert fresh.round_count == 0 and fresh.stall_count == 0 and fresh.replan_used == 0
    assert fresh.last_fingerprint is None and fresh.blocked_at is None

    blocked = entities.SummaryRunPublic.model_validate({
        "task_id": U1, "canvas_id": U2, "workspace_id": U3,
        "round_count": 8, "stall_count": 3, "replan_used": 1,
        "last_fingerprint": "a" * 64, "blocked_at": TS, "created_at": TS, "updated_at": TS,
    })
    assert blocked.round_count == 8 and blocked.replan_used == 1
    assert blocked.blocked_at is not None and blocked.last_fingerprint == "a" * 64


def test_node_create_upstream_node_ids_optional() -> None:
    """NodeCreate.upstream_node_ids（L1 方案 A）：缺省 None（现状路径）；给定则原子建入边。"""
    plain = rest.NodeCreate.model_validate({
        "title": "Merge", "kind": "system", "system_action": "merge",
    })
    assert plain.kind is CanvasNodeKind.SYSTEM and plain.upstream_node_ids is None

    with_upstream = rest.NodeCreate.model_validate({
        "title": "Merge", "kind": "system", "system_action": "merge",
        "upstream_node_ids": [U1, U2],
    })
    assert with_upstream.system_action is SystemAction.MERGE
    assert with_upstream.upstream_node_ids == [U1, U2]


def test_rule_codes_carry_o8_o9() -> None:
    """rule 字段值域含 O8（M8 replan 超额）+ O9（M6b Agent 结构变更，v1.0.12 回填）；
    错误码目录不因 rule 扩容——rule 是 403 的正交维度，非 ErrorCode（仍 29）。"""
    assert "O8" in constants.RULE_CODES and "O9" in constants.RULE_CODES
    assert len(constants.RULE_CODES) == len(set(constants.RULE_CODES))  # 无重复
    assert len({c.value for c in rest.ErrorCode}) == 29  # 错误码目录不变
