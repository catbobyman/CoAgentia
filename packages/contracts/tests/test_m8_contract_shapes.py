"""M8 契约登记的聚焦形状测试（L0 存留面）：汇总协调状态实体、rule 目录。

画布面（UpstreamPolicy 两档 / CanvasNodePublic / NodeCreate.upstream_node_ids / NodePatch.
upstream_policy）用例随 DEDAG v1.6 退役删除；summary_runs 表冻结不删，实体形状仍钉。
"""

from coagentia_contracts import constants, entities, rest

U1 = "01KX0000000000000000000001"
U2 = "01KX0000000000000000000002"
U3 = "01KX0000000000000000000003"
TS = "2026-07-14T12:00:00.000Z"


def test_summary_run_roundtrip() -> None:
    """SummaryRun（冻结表实体，§6.4）：三计数默认 0、指纹/阻断可空、直挂 workspace_id。"""
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


def test_rule_codes_catalog_well_formed() -> None:
    """rule 目录无重复；错误码目录 32（DEDAG 零新增，退役域错误码暂保留枚举成员）。"""
    assert len(constants.RULE_CODES) == len(set(constants.RULE_CODES))  # 无重复
    assert len({c.value for c in rest.ErrorCode}) == 32  # DEDAG 后错误码目录仍 32
