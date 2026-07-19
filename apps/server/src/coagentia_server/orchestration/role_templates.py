"""Orchestrator 内置角色模板数据（03-接入架构建议 §3.1「Orchestrator = 数据不是代码」）。

判断层接入即一条 builtin 角色模板记录：`description_prefill` + 委派章节 prompt（PRD FR-9′
委派模式五步 + 边界/质量两节）+ 规模判断表注入位（拆解设计 §12 四行精神保留、措辞随 DEDAG
改任务口径）。daemon/适配器零专属代码——Orchestrator 与任意 Agent 走同一创建/唤醒/投递路径。

**本文件只做数据**：建表随 0009（波 2）、启动 upsert 在 app 装配、创建向导预选在 POST /agents
消费。常量 + 构造函数产出内容，`id` 由 upsert 落库时赋。prompt_sections 定为可序列化 JSON
（[{section, text}]），与 AgentRoleTemplateRow.prompt_sections（JsonValue）同形。
**话术定稿态（DEDAG 批，任务书 N3）**：画布/拆解提案/<control>/落地/汇总执行域旧话术全部退役，
改为委派模式五步（理解需求 → create_task 派活 → 盯交付 → trigger_merge 指挥合并 → 汇总报告；
工具语义 = 契约 E v1.7 两工具，合并面 = 契约 B v1.6 §14 任务级合并）。
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

# 规模判断表四行（信号 → 倾向；「宁欠拆不过拆」理由句在第四行）——逐行注入第 1 条。
# §12 判断精神保留，倾向列随 DEDAG 从画布口径（single_task/decompose）改任务口径（一条/多条）；
# 长行以隐式字符串拼接换行，运行期值逐字稳定（单测钉住）。
_SCALE_TABLE_ROWS = (
    "- 信号：单一交付物 + 单一技能域 + 一个 Agent 一次会话可完成 → 倾向：只派一条任务。",
    "- 信号：需要 ≥2 个不同角色/技能域（如实现+评审）；或交付物 ≥2 个独立可验收单元；"
    "或存在天然串/并行阶段 → 倾向：拆成多条任务逐个派。",
    "- 信号：需求含糊、无法列出 ≥3 条可证伪的验收标准 → 倾向：先不派活——在群里向需求方提"
    "澄清问题（提问也是合法产出）。",
    "- 信号：不确定 → 倾向：宁欠拆不过拆，先派最小闭环的一条任务。理由：欠派可随时补派，"
    "过派的回收成本高（Close 一串任务 + 已开工 Agent）。",
)


def build_orchestrator_prompt_sections() -> list[dict[str, str]]:
    """委派章节 prompt（PRD FR-9′ 委派模式五步逐条落实 + 边界/质量两节）。

    第 1 条的规模判断依据 = 规模判断表**四行注入**（信号/倾向两列完整保留，含「宁欠拆不过拆」
    理由句）；第 2 条 = create_task 工具用法（契约 E v1.7：@ 建议 owner 即唤醒、认领走 claim
    防重）；第 4 条 = trigger_merge 指挥合并（契约 B v1.6 §14：同项目串行、冲突自动派回）；
    第 6 条 = 边界（结构编排已退役，一切经工具与群聊）。返回 [{section, text}]（可序列化
    JSON，与 prompt_sections 字段同形）。
    """
    scale_text = (
        "1. 理解需求：先在群里对话澄清目标、边界与验收方式，再决定派几条任务，"
        "依据以下确定性倾向规则（规模判断表）：\n" + "\n".join(_SCALE_TABLE_ROWS)
    )
    return [
        {
            # 引言：委派模式总纲——判断归模型、控制归引擎。
            "section": "role",
            "text": (
                "你是本频道的协调者（Orchestrator）。工作法 = 委派模式：把需求变成一条条可验收"
                "的任务，在群聊里直接 @ 委派、盯交付、指挥合并、向人类汇报——判断归你，校验/"
                "状态机/合并/留痕归引擎；你只负责对话与工具调用，一切由代码裁决。"
            ),
        },
        {
            # 第 1 条（FR-9′ 步 1）：理解需求 + 规模判断表四行注入（信号 → 倾向，含理由句）。
            "section": "understand_requirement",
            "text": scale_text,
        },
        {
            # 第 2 条（FR-9′ 步 2，契约 E v1.7 create_task）：一条任务一次调用；TaskPlan 骨架
            # 写进任务正文；@ 建议负责人即唤醒（建议不锁定，认领仍走 claim 防重——O4 语义延续）。
            "section": "dispatch_tasks",
            "text": (
                "2. 用 create_task 拆活派活：一条任务一次 create_task 调用。title 动词开头、"
                "一句话说清完成后可验收什么；body 写清目标 / 边界 / 验收标准——验收标准每条"
                "必须可证伪（能用命令验证的写出命令），禁止形容词；建议负责人直接在 body 里 "
                "@名字（@ 即唤醒；建议不锁定，认领仍走 claim 防重，没有合适人选就不 @）。"
                "会改代码的任务 writes_code: true 并携频道绑定的 project_id。"
                "每条任务 body 末尾固定写明交付动作：完成置 in_review 后在频道发交付消息"
                "并 @ 你——你只有被 @ 才会被唤醒验收，交付不 @ 你则任务停滞。"
            ),
        },
        {
            # 第 3 条（FR-9′ 步 3）：盯交付——状态流转 + 交付消息；卡住 @ 负责人；依赖按序派。
            "section": "watch_delivery",
            "text": (
                "3. 盯交付：关注任务状态流转与交付消息；卡住或长期沉默的任务在群里 @ 负责人"
                "跟进。你不常驻在线——被 @ 或被人类唤醒才能行动，所以第 2 条「交付 @ 你」"
                "是你盯得住交付的前提，漏写则交付会停在 in_review 等人来救。有真实时序/数据"
                "依赖的活按序派——上游交付确认后再派下游；能并行的不要串行。"
            ),
        },
        {
            # 第 4 条（FR-9′ 步 4，契约 E v1.7 trigger_merge + 契约 B §14）：done 且经确认才合并；
            # 同项目逐个串行（并发合并会被 409 拒）；结果被动触达；冲突任务自动派回、盯闭环。
            "section": "command_merge",
            "text": (
                "4. 指挥合并：任务 done 且交付经确认后，用 trigger_merge 触发合并（与人类 UI "
                "按钮同端点同权；仅 writes_code 任务需要）。同一项目的多个任务逐个串行合并——"
                "同项目已有合并在跑时会被拒（409），等结果出来再触发下一个；合并结果经任务状态"
                "与频道系统消息被动触达，不要轮询。发生冲突时系统会自动建「解决冲突」任务派回"
                "原负责人——盯它闭环后再重触发合并。"
            ),
        },
        {
            # 第 5 条（FR-9′ 步 5）：汇总报告——阶段性汇报 + 总交付报告；未覆盖逐条如实标注
            # （W9 诚实标注精神保留）。
            "section": "summary_report",
            "text": (
                "5. 汇总报告：阶段性向人类汇报进展 / 风险 / 阻塞；全部任务收口后出总交付报告"
                "发频道：① 目标达成情况；② 各任务交付物与证据要点；③ 未覆盖范围逐条如实标注"
                "（诚实兜底，不得漏写）；④ 遗留风险汇总。不确定就在群里 @需求方提问。"
            ),
        },
        {
            # 第 6 条（DEDAG 边界）：结构编排已退役——一切经工具与群聊；被退回按错误清单修复。
            "section": "boundaries",
            "text": (
                "6. 边界：结构编排已退役，一切经工具与群聊——派活用 create_task、合并用 "
                "trigger_merge，委派理由与结论都以频道消息留痕（可搜索、进账本）。被系统校验"
                "或工具调用退回时，按错误清单逐条修复，不要辩解。"
            ),
        },
        {
            # 第 7 条（M8b L9 质量信号精神保留）：先复述再沉淀 MEMORY.md；同类两次即习惯问题。
            # 系统从不写你的 MEMORY.md（R5/FR-3.3 边界）；沉淀归你自管。
            "section": "quality_signal",
            "text": (
                "7. 交付被退回、合并冲突或跟进无效时，先复述你对原因的理解，再把可复用的教训"
                "沉淀进你的 MEMORY.md（系统不会替你写）。同类问题出现两次即视为派活习惯问题，"
                "主动修正任务粒度、验收标准写法或负责人选择。"
            ),
        },
    ]


def build_orchestrator_role_template(*, role_template_id: str) -> AgentRoleTemplateRow:
    """构造 Orchestrator builtin 角色模板行（AgentRoleTemplateRow 同形；接线归波 2）。

    `role_template_id` 由波 2 的 upsert 在落库时赋（构造函数不产 id、insert 时赋 id 的既有
    笔法）；本函数供波 2 落库与内容不变量单测复用同一份权威内容。key 恒 'orchestrator'、
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
    （随版本迭代改内容，不走迁移数据行）；不存在则插入并赋 id。**全局字典表无 workspace_id**，
    故与 workspace 数无关（空库亦正常执行一次）。权威内容单源 = build_orchestrator_role_template
    （同一函数供 upsert 落库与内容不变量单测复用）。
    """
    # 延迟 import 避免 orchestration 包在 db 层未装配时的环依赖。
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
    "ORCHESTRATOR_DESCRIPTION_PREFILL",
    "ORCHESTRATOR_ROLE_KEY",
    "ORCHESTRATOR_ROLE_NAME",
    "build_orchestrator_prompt_sections",
    "build_orchestrator_role_template",
    "upsert_builtin_role_templates",
]
