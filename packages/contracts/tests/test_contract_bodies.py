"""M3a 契约 body 模型校验（PRD §4.3 v1）：提交门约束 + kind↔schema 映射完整性。

这些约束原本只在 apps/server 集成测试里经"永远合法的 fixture"间接覆盖——本文件直接钉死
边界（空 AC 拒、version Literal 拦、handoff 空列表放行由 T7 执法），防止误删 min_length/
打错 version 时全套测试静默放行（review 修复：契约层单测缺口）。
"""

from __future__ import annotations

import pytest
from coagentia_contracts import rest
from coagentia_contracts.constants import TASK_CONTRACT_KINDS
from coagentia_contracts.enums import ContractKind
from pydantic import ValidationError


def _plan(**over: object) -> dict:
    base: dict = {
        "goal": "让用户一键导出",
        "acceptance_criteria": [
            {"id": "ac1", "statement": "点击导出生成 CSV", "verify_by": "command",
             "verify_ref": "pytest"},
        ],
    }
    base.update(over)
    return base


_ULID_A = "01KX00000000000000000000MA"
_ULID_B = "01KX00000000000000000000MB"


def _handoff(**over: object) -> dict:
    base: dict = {"from_member": _ULID_A, "to_member": _ULID_B, "verify_plan": "逐条复核"}
    base.update(over)
    return base


def test_task_plan_requires_at_least_one_ac() -> None:
    """acceptance_criteria ≥1 是提交门（计划天然需 AC）——空列表拒。"""
    rest.TaskPlanBody.model_validate(_plan())  # 合法
    with pytest.raises(ValidationError):
        rest.TaskPlanBody.model_validate(_plan(acceptance_criteria=[]))


def test_task_plan_version_literal_pins_schema() -> None:
    """version 是定值 Literal——打错版本号即拒（kind↔schema 锚点）。"""
    ok = rest.TaskPlanBody.model_validate(_plan())
    assert ok.version == "coagentia.task-plan.v1"
    with pytest.raises(ValidationError):
        rest.TaskPlanBody.model_validate(_plan(version="coagentia.task-plan.v2"))


def test_task_plan_rejects_bad_verify_by() -> None:
    """verify_by 值域 = VerifyBy 枚举——非法值拒。"""
    with pytest.raises(ValidationError):
        rest.TaskPlanBody.model_validate(_plan(
            acceptance_criteria=[
                {"id": "ac1", "statement": "s", "verify_by": "vibes", "verify_ref": "r"},
            ]
        ))


def test_task_handoff_allows_empty_lists_gate_is_t7() -> None:
    """deliverables/evidence 提交期允许空（可增量起草）——"非空"由 T7 流转门执法而非 body 门。"""
    ho = rest.TaskHandoffBody.model_validate(_handoff())
    assert ho.deliverables == [] and ho.evidence == []


def test_contract_body_models_cover_all_kinds() -> None:
    """CONTRACT_BODY_MODELS 覆盖 ContractKind 全集——防新增 kind 漏配映射（submit 端点兜底分支）。"""
    assert set(rest.CONTRACT_BODY_MODELS) == set(ContractKind)


def test_task_contract_kinds_exclude_loop_contract() -> None:
    """任务契约仅 TaskPlan/TaskHandoff；loop_contract 属 Reminder 域（D1-L2，M4）。"""
    assert TASK_CONTRACT_KINDS == frozenset(
        {ContractKind.TASK_PLAN, ContractKind.TASK_HANDOFF}
    )
    assert ContractKind.LOOP_CONTRACT not in TASK_CONTRACT_KINDS
