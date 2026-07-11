"""工程三角 builtin 模板常量（契约 B §11.1 / A §4.10；FR-7.1）。

工程三角 = **6 节点线性 DAG**：需求框定 → 评审门 → 实现契约 → TDD 实现 → 独立验收 → 人类终审；
**4 角色占位**：产品负责人 / 实现工程师（doer，③④）/ 评审工程师（checker，②⑤）/ 人类负责人。

checker ≠ doer 与「通过 / 降级 / 退回 / 需人裁决」评审结论都以 `roles.description` + `briefing`
**话术承载**——FR-7.5 评审结论枚举 schema 化归 M6（owner 拍板 2026-07-11，裁决 2），本里程碑不做。

用 contracts 的 `TemplateBody`/`TaskPlanBody` 构造（形状单源在 contracts 包，纪律 7；server 侧只填
内容，避免改 contracts 触发 `pnpm gen` churn）。启动 upsert 见 `templates.service.upsert_builtin_
templates`——以 name 为幂等键，重启不重复、随版本迭代改 body（不走迁移数据行，A §4.10 裁决）。
"""

from __future__ import annotations

from coagentia_contracts.entities import (
    AcceptanceCriterion,
    TaskPlanBody,
    TemplateBody,
    TemplateEdge,
    TemplateNode,
    TemplateRole,
)
from coagentia_contracts.enums import VerifyBy

# 幂等键与展示字段（upsert 以 name 为键，A §4.10）。
BUILTIN_TRIANGLE_NAME = "工程三角"
BUILTIN_TRIANGLE_DESCRIPTION = (
    "需求框定 → 评审门 → 实现契约 → TDD 实现 → 独立验收 → 人类终审：可复制的工程流水线"
    "（评审与实现默认异 runtime 互审，checker ≠ doer）。"
)

# 4 角色占位名（实现=doer ③④、评审=checker ②⑤——checker ≠ doer 由话术承载）。
_ROLE_PM = "产品负责人"
_ROLE_DOER = "实现工程师"
_ROLE_CHECKER = "评审工程师"
_ROLE_HUMAN = "人类负责人"

# 评审结论四态话术（FR-7.5 枚举归 M6，此处以自然语言承载于 description/briefing）。
_REVIEW_VERDICTS = "通过 / 降级 / 退回 / 需人裁决"


def _skeleton(
    goal: str, ac_id: str, statement: str, verify_by: VerifyBy, verify_ref: str
) -> TaskPlanBody:
    """合法 plan_skeleton（TaskPlanBody 校验 goal + ≥1 AC；builtin 每节点满足）。"""
    return TaskPlanBody(
        goal=goal,
        acceptance_criteria=[
            AcceptanceCriterion(
                id=ac_id, statement=statement, verify_by=verify_by, verify_ref=verify_ref
            )
        ],
    )


def build_triangle_body() -> TemplateBody:
    """构造工程三角 `TemplateBody`（6 节点线性 DAG + 4 角色占位 + briefing 简报话术）。

    每个 task 节点带合法 plan_skeleton（实例化时作 L2 TaskPlan 初稿）；node key 语义命名、模板内
    唯一（H6 实例化按 key 映射/连边引用）。
    """
    nodes = [
        TemplateNode(
            key="req_framing",
            title="需求框定",
            role=_ROLE_PM,
            plan_skeleton=_skeleton(
                "把用户诉求框定为可验收的目标与范围",
                "ac-framing",
                "需求文档列明目标、范围与非目标，评审门可据此把关",
                VerifyBy.MANUAL,
                "评审门确认需求框定完整",
            ),
        ),
        TemplateNode(
            key="review_gate",
            title="评审门",
            role=_ROLE_CHECKER,
            plan_skeleton=_skeleton(
                "对需求框定独立把关并给出评审结论",
                "ac-gate",
                f"评审结论用 {_REVIEW_VERDICTS} 话术明确表述",
                VerifyBy.MANUAL,
                "评审记录含四态结论之一",
            ),
        ),
        TemplateNode(
            key="impl_contract",
            title="实现契约",
            role=_ROLE_DOER,
            plan_skeleton=_skeleton(
                "为实现立下 TaskPlan 契约（目标 + 验收标准）",
                "ac-contract",
                "实现契约含可证伪验收标准，供 TDD 与独立验收共用",
                VerifyBy.INSPECT,
                "契约卡含 ≥1 条验收标准",
            ),
        ),
        TemplateNode(
            key="tdd_impl",
            title="TDD 实现",
            role=_ROLE_DOER,
            plan_skeleton=_skeleton(
                "以测试先行方式实现并自测通过",
                "ac-tdd",
                "实现附测试且测试全绿",
                VerifyBy.COMMAND,
                "运行测试套件全部通过",
            ),
        ),
        TemplateNode(
            key="acceptance",
            title="独立验收",
            role=_ROLE_CHECKER,
            plan_skeleton=_skeleton(
                "独立复核交付是否满足实现契约验收标准（checker ≠ doer）",
                "ac-accept",
                f"逐条核对验收标准并给出 {_REVIEW_VERDICTS} 结论",
                VerifyBy.MANUAL,
                "独立验收记录逐条比对验收标准",
            ),
        ),
        TemplateNode(
            key="human_final",
            title="人类终审",
            role=_ROLE_HUMAN,
            plan_skeleton=_skeleton(
                "人类对交付做最终裁决与合并",
                "ac-final",
                "人类确认交付并将任务置 done",
                VerifyBy.MANUAL,
                "人类终审确认并合并交付",
            ),
        ),
    ]
    # 线性依次相连（无环——detect_cycle 校验通过）。
    edges = [
        TemplateEdge(from_key="req_framing", to_key="review_gate"),
        TemplateEdge(from_key="review_gate", to_key="impl_contract"),
        TemplateEdge(from_key="impl_contract", to_key="tdd_impl"),
        TemplateEdge(from_key="tdd_impl", to_key="acceptance"),
        TemplateEdge(from_key="acceptance", to_key="human_final"),
    ]
    roles = [
        TemplateRole(
            placeholder=_ROLE_PM,
            description="把用户诉求框定为可验收的目标与范围；输出需求框定，交评审门把关。",
        ),
        TemplateRole(
            placeholder=_ROLE_DOER,
            description="落地实现（doer）：先立实现契约，再 TDD 实现，交独立验收。",
        ),
        TemplateRole(
            placeholder=_ROLE_CHECKER,
            description=(
                "独立评审，checker ≠ doer（评审者不得是实现者）；评审结论用 "
                f"{_REVIEW_VERDICTS} 话术承载（FR-7.5 结论枚举归 M6）。"
            ),
        ),
        TemplateRole(
            placeholder=_ROLE_HUMAN,
            description="人类终审：对交付做最终裁决与合并（done）。",
        ),
    ]
    briefing = (
        "本频道由「工程三角」模板实例化：产品负责人框定需求 → 评审门把关 → 实现工程师立契约并 "
        "TDD 实现 → 评审工程师独立验收 → 人类终审。评审与实现默认异 runtime 互审（checker ≠ "
        f"doer）；评审结论用 {_REVIEW_VERDICTS} 话术承载。"
    )
    return TemplateBody(nodes=nodes, edges=edges, roles=roles, briefing=briefing)
