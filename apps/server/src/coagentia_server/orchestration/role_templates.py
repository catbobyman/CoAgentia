"""Orchestrator 内置角色模板数据（03-接入架构建议 §3.1「Orchestrator = 数据不是代码」）。

判断层接入即一条 builtin 角色模板记录：`description_prefill` + 拆解章节 prompt（拆解设计 §13.1
七条骨架）+ 规模判断表注入位（§12 四行原文）。daemon/适配器零专属代码——Orchestrator 与任意
Agent 走同一创建/唤醒/投递路径。

**本文件只做数据**：建表随 0009（波 2）、启动 upsert 在 app 装配、创建向导预选在 POST /agents 消费。
笔法照 templates/builtin.py：常量 + 构造函数产出内容，`id` 由 upsert 落库时赋。prompt_sections 定为
可序列化 JSON（[{section, text}]），与 AgentRoleTemplateRow.prompt_sections（JsonValue）同形。
**话术定稿态（J11，阶段 4）**：§13.1 七条 + 第 8 条 delta 通道增补（O9/base 自愈承诺与 delta.py
的 DELTA_BASE_MISMATCH hint 互为兑现）；真 LLM 试拆解校准随 J12 实机 verify 回写。
"""

from __future__ import annotations

from typing import cast

from coagentia_contracts.constants import (
    ORCHESTRATOR_ROLE_TEMPLATE_DESCRIPTION_PREFILL,
    ORCHESTRATOR_ROLE_TEMPLATE_KEY,
    ORCHESTRATOR_ROLE_TEMPLATE_NAME,
)
from coagentia_contracts.entities import AgentRoleTemplateRow
from pydantic import JsonValue
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

# 幂等键与展示字段（波 2 upsert 以 key 为幂等键——key UNIQUE，A/03 §3.1）。
# 单源迁移（纪律 7）：key/name/description_prefill 定义处已迁至 contracts constants.py，此处仅
# 别名引用（保持既有 ORCHESTRATOR_ROLE_KEY/NAME/DESCRIPTION_PREFILL 名不变，语义不变）；
# prompt_sections 等生成内容仍留本文件。
ORCHESTRATOR_ROLE_KEY = ORCHESTRATOR_ROLE_TEMPLATE_KEY
ORCHESTRATOR_ROLE_NAME = ORCHESTRATOR_ROLE_TEMPLATE_NAME
ORCHESTRATOR_DESCRIPTION_PREFILL = ORCHESTRATOR_ROLE_TEMPLATE_DESCRIPTION_PREFILL

# 拆解 schema 版本串（输出协议第 6 条；与 contracts kernel/golden 单源一致，勿改字面）。
DECOMPOSITION_SCHEMA_VERSION = "coagentia.decomposition.v1"
# delta schema 版本串（第 8 条增量通道；与 constants.SCHEMA_DECOMPOSITION_DELTA_V1 同值——此处
# 保持字面量体例与上行一致，单测钉住两处不漂移）。
DECOMPOSITION_DELTA_SCHEMA_VERSION = "coagentia.decomposition-delta.v1"

# §12 规模判断表四行原文（信号 → 倾向；「宁欠拆不过拆」理由句在第四行）——逐行注入第 1 条。
# 信号/倾向两列完整保留；长行以隐式字符串拼接换行，运行期值与原文逐字一致。
_SCALE_TABLE_ROWS = (
    "- 信号：单一交付物 + 单一技能域 + 一个 Agent 一次会话可完成 → 倾向：single_task。",
    "- 信号：需要 ≥2 个不同角色/技能域（如实现+评审）；或交付物 ≥2 个独立可验收单元；"
    "或存在天然串/并行阶段 → 倾向：decompose。",
    "- 信号：需求含糊、无法列出 ≥3 条可证伪 AC → 倾向：不出提案——先在线程里向需求方提"
    "澄清问题（这也是合法产出）。",
    "- 信号：不确定 → 倾向：宁欠拆不过拆，选 single_task。理由：欠拆可用增量变更补节点，"
    "过拆的回收成本高（Close 一串任务 + 已开工 Agent）。",
)


def build_orchestrator_prompt_sections() -> list[dict[str, str]]:
    """拆解章节 prompt（拆解设计 §13.1 七条骨架逐条落实）。

    第 1 条的规模判断依据 = §12 规模判断表**四行原文注入**（信号/倾向两列完整保留，含「宁欠拆不过
    拆」理由句）；第 6 条 = 输出协议（唯一 <control> 块 + schema 版本串）；第 7 条 = 被退回按错误
    清单修复不辩解。返回 [{section, text}]（可序列化 JSON，与 prompt_sections 字段同形）。
    """
    scale_text = (
        "1. 先判断规模（single_task / decompose / 先澄清），"
        "依据以下确定性倾向规则（§12 规模判断表）：\n" + "\n".join(_SCALE_TABLE_ROWS)
    )
    return [
        {
            # 引言：判断归模型、控制归代码。
            "section": "role",
            "text": (
                "你是本频道的协调者（Orchestrator）。收到拆解请求时，把「一句话需求」拆成可执行的"
                "任务 DAG——判断归你、校验/确认/落地/幂等/留痕归引擎；你只产出提案 JSON，一切由"
                "代码裁决。"
            ),
        },
        {
            # 第 1 条：先判断规模，依据 = §12 规模判断表四行原文（信号 → 倾向，含理由句）。
            "section": "scale_judgment",
            "text": scale_text,
        },
        {
            # 第 2 条：decompose 时节点数上限与 TaskPlan 骨架（AC 可证伪、禁形容词）。
            "section": "decompose_nodes",
            "text": (
                "2. decompose 时：节点数 2..<上限>（上限由频道配置注入）；每个 agent 节点写 "
                "TaskPlan 骨架——goal 用用户视角一句话，acceptance_criteria 每条必须可证伪"
                "（能用命令验证的写 verify_by: command 并给出命令），禁止形容词。"
            ),
        },
        {
            # 第 3 条：依赖边只画真实时序/数据依赖，能并行不串行。
            "section": "edges",
            "text": "3. 依赖边只画真实的时序/数据依赖；能并行的不要串行。",
        },
        {
            # 第 4 条：suggested_owner 从频道成员选、无合适人选置 null。
            "section": "suggested_owner",
            "text": "4. suggested_owner 从频道成员里选，说明理由；没有合适人选就置 null。",
        },
        {
            # 第 5 条：会改代码的节点标 writes_code 并指定 project，必给 merge_plan。
            "section": "writes_code",
            "text": "5. 会改代码的节点标 writes_code: true 并指定 project；此时必须给 merge_plan。",
        },
        {
            # 第 6 条：输出协议——唯一 <control> 块 + schema 版本串（机读 JSON 只在此块内）。
            "section": "output_protocol",
            "text": (
                "6. 输出协议：正文写给人看的拆解说明；机器可读 JSON 放在唯一的 <control> 块中，"
                f"schema 为 {DECOMPOSITION_SCHEMA_VERSION}。<control> 块之外不得出现 JSON。"
            ),
        },
        {
            # 第 7 条：被退回按错误清单修复，不辩解。
            "section": "repair",
            "text": "7. 你的提案会被系统校验并需人类确认；被退回时按错误清单逐条修复，不要辩解。",
        },
        {
            # 第 8 条（J11 定稿增补，拆解设计 §11/O9）：落地后的结构变更唯一通道 = delta 提案。
            # base 自愈：校验反馈的 DELTA_BASE_MISMATCH hint 携当前基线值（delta.py），修复循环
            # 一轮即可对齐——话术如实承诺这一点，不要求模型预知基线。
            "section": "delta_changes",
            "text": (
                "8. 落地后如需结构变更（增删节点、改依赖），不要直接改画布——Agent 直接编辑会被"
                "拒绝（O9）。在原 source 任务线程内发含唯一 <control> 块的增量提案，schema 为 "
                f"{DECOMPOSITION_DELTA_SCHEMA_VERSION}，字段：version、base（画布当前基线指纹）、"
                "operations（add_node/remove_node/add_edge/remove_edge，删除引用画布节点 id）、"
                "reason（变更理由）。base 不确定时照常提交：校验反馈会携当前基线值，按错误清单"
                "修正一轮即可。删除进行中的节点会被拒（先 Close 任务再删）。"
            ),
        },
    ]


def build_orchestrator_role_template(*, role_template_id: str) -> AgentRoleTemplateRow:
    """构造 Orchestrator builtin 角色模板行（AgentRoleTemplateRow 同形；接线归波 2）。

    `role_template_id` 由波 2 的 upsert 在落库时赋（同 templates.build_triangle_body 不含 id、insert
    时赋 id 的笔法）；本函数供波 2 落库与内容不变量单测复用同一份权威内容。key 恒 'orchestrator'、
    builtin=True。
    """
    return AgentRoleTemplateRow(
        id=role_template_id,
        key=ORCHESTRATOR_ROLE_KEY,
        name=ORCHESTRATOR_ROLE_NAME,
        description_prefill=ORCHESTRATOR_DESCRIPTION_PREFILL,
        # list[dict[str,str]] 是合法 JSON 值；JsonValue 递归联合下 list 不变，需 cast 显式收窄。
        prompt_sections=cast(JsonValue, build_orchestrator_prompt_sections()),
        builtin=True,
    )


def upsert_builtin_role_templates(engine: Engine) -> None:
    """server 启动 upsert 内置 Orchestrator 角色（A §4.1；重启幂等、随版迭代）。

    幂等键 = key='orchestrator'（UNIQUE）：存在则更新 name/description_prefill/prompt_sections
    （随版本迭代改内容，不走迁移数据行——同 templates.upsert_builtin_templates 体例）；不存在则
    插入并赋 id。**全局字典表无 workspace_id**，故与 workspace 数无关（空库亦正常执行一次）。
    权威内容单源 = build_orchestrator_role_template（同一函数供 upsert 落库与内容不变量单测复用）。
    """
    # 延迟 import 避免 orchestration 包在 db 层未装配时的环依赖（同 templates.service 体例）。
    from coagentia_server.db import models
    from coagentia_server.ledger.service import new_ulid

    role_table = models.tbl(models.AgentRoleTemplate)
    template = build_orchestrator_role_template(role_template_id=new_ulid())
    payload = template.model_dump(mode="json")
    with engine.begin() as conn:
        existing = conn.execute(
            select(role_table.c.id).where(role_table.c.key == ORCHESTRATOR_ROLE_KEY)
        ).scalar()
        if existing is None:
            conn.execute(insert(role_table).values(payload))
        else:
            conn.execute(
                update(role_table)
                .where(role_table.c.id == existing)
                .values(
                    name=payload["name"],
                    description_prefill=payload["description_prefill"],
                    prompt_sections=payload["prompt_sections"],
                    builtin=payload["builtin"],
                )
            )


__all__ = [
    "DECOMPOSITION_DELTA_SCHEMA_VERSION",
    "DECOMPOSITION_SCHEMA_VERSION",
    "ORCHESTRATOR_DESCRIPTION_PREFILL",
    "ORCHESTRATOR_ROLE_KEY",
    "ORCHESTRATOR_ROLE_NAME",
    "build_orchestrator_prompt_sections",
    "build_orchestrator_role_template",
    "upsert_builtin_role_templates",
]
