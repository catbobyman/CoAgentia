"""DEDAG 批 N3：Orchestrator builtin 角色模板数据的内容不变量单测（03 §3.1 / PRD FR-9′）。

只断言数据内容（不建表 / 不接 upsert / 不消费——归波 2）：key 恒 orchestrator、委派模式五步
（理解需求 → create_task 派活 → 盯交付 → trigger_merge 指挥合并 → 汇总报告）+ 边界/质量两节
关键句逐条、规模判断表四行信号短语（「宁欠拆不过拆」保留）、退役话术（画布/<control>/提案/
O8/O9）零残留、description_prefill 非空、AgentRoleTemplateRow 同形。
"""

from __future__ import annotations

from coagentia_contracts import constants
from coagentia_contracts.entities import AgentRoleTemplateRow
from coagentia_server.ledger import service
from coagentia_server.orchestration import role_templates


def _sections_text() -> str:
    return "\n".join(s["text"] for s in role_templates.build_orchestrator_prompt_sections())


def test_key_is_orchestrator() -> None:
    """key 恒 'orchestrator'（波 2 upsert 幂等键 / NO_ORCHESTRATOR 前端预选据此）。"""
    assert role_templates.ORCHESTRATOR_ROLE_KEY == "orchestrator"


def test_display_constants_single_sourced_from_contracts() -> None:
    """单源迁移（纪律 7）：key/name/description_prefill 的定义处 = contracts constants.py，
    server 侧仅别名引用（语义不变）。此断言锁死迁移，防日后在 server 侧重新硬编码分叉。"""
    assert role_templates.ORCHESTRATOR_ROLE_KEY is constants.ORCHESTRATOR_ROLE_TEMPLATE_KEY
    assert role_templates.ORCHESTRATOR_ROLE_NAME is constants.ORCHESTRATOR_ROLE_TEMPLATE_NAME
    assert (
        role_templates.ORCHESTRATOR_DESCRIPTION_PREFILL
        is constants.ORCHESTRATOR_ROLE_TEMPLATE_DESCRIPTION_PREFILL
    )


def test_prompt_sections_serializable_shape() -> None:
    """prompt_sections = [{section, text}]（可序列化 JSON，与 JsonValue 字段同形）。"""
    sections = role_templates.build_orchestrator_prompt_sections()
    assert isinstance(sections, list) and sections
    for s in sections:
        assert set(s) == {"section", "text"}
        assert isinstance(s["section"], str) and s["section"]
        assert isinstance(s["text"], str) and s["text"]


def test_delegation_five_steps_each_present() -> None:
    """FR-9′ 委派模式五步 + 边界/质量两节逐条关键句包含（第 1..7 条各一断言）。"""
    section_names = {s["section"] for s in role_templates.build_orchestrator_prompt_sections()}
    for name in (
        "understand_requirement",
        "dispatch_tasks",
        "watch_delivery",
        "command_merge",
        "summary_report",
        "boundaries",
        "quality_signal",
    ):
        assert name in section_names
    text = _sections_text()
    assert "理解需求" in text  # 第 1 条
    assert "一条任务一次 create_task 调用" in text  # 第 2 条
    assert "卡住或长期沉默的任务在群里 @ 负责人" in text  # 第 3 条
    assert "用 trigger_merge 触发合并" in text  # 第 4 条
    assert "未覆盖范围逐条如实标注" in text  # 第 5 条
    assert "结构编排已退役，一切经工具与群聊" in text  # 第 6 条（边界）
    assert "按错误清单逐条修复，不要辩解" in text  # 第 6 条（修复姿态）
    assert "沉淀进你的 MEMORY.md" in text  # 第 7 条


def test_scale_table_four_signals_verbatim() -> None:
    """规模判断表四行信号短语（§12 精神保留 + 「宁欠拆不过拆」理由句改任务口径）。"""
    text = _sections_text()
    assert "单一交付物 + 单一技能域 + 一个 Agent 一次会话可完成" in text
    assert (
        "需要 ≥2 个不同角色/技能域（如实现+评审）；或交付物 ≥2 个独立可验收单元；"
        "或存在天然串/并行阶段"
        in text
    )
    assert "需求含糊、无法列出 ≥3 条可证伪" in text
    assert "宁欠拆不过拆" in text
    # 倾向列并存 + 理由句（画布口径 single_task/decompose 已退役，改任务口径）。
    assert "只派一条任务" in text and "拆成多条任务逐个派" in text
    assert "过派的回收成本高" in text


def test_dispatch_section_tool_semantics() -> None:
    """第 2 条钉契约 E v1.7 create_task 语义：title 动词开头、验收可证伪、@ 建议负责人
    即唤醒（建议不锁定，认领仍走 claim 防重——O4 语义延续）、writes_code + project_id。"""
    text = _sections_text()
    assert "title 动词开头" in text
    assert "必须可证伪" in text and "禁止形容词" in text
    assert "@ 即唤醒" in text
    assert "认领仍走 claim 防重" in text
    assert "writes_code: true 并携频道绑定的 project_id" in text


def test_delivery_wake_discipline_present() -> None:
    """R2 实测教训（2026-07-19）：交付消息不 @ 协调者 → in_review 停滞 76 分钟需人类 nudge。
    第 2 条须要求任务 body 写明「交付 @ 你」；第 3 条须点破协调者非常驻的唤醒前提。"""
    text = _sections_text()
    assert "完成置 in_review 后在频道发交付消息" in text  # 第 2 条派活侧
    assert "你只有被 @ 才会被唤醒验收" in text
    assert "你不常驻在线" in text  # 第 3 条盯交付侧
    assert "停在 in_review 等人来救" in text


def test_merge_section_semantics() -> None:
    """第 4 条钉契约 B v1.6 §14 任务级合并语义：done 且经确认才触发、与人类 UI 同端点同权、
    同项目逐个串行（并发被 409 拒）、结果被动触达不轮询、冲突任务自动派回原负责人。"""
    text = _sections_text()
    assert "任务 done 且交付经确认后" in text
    assert "同端点同权" in text
    assert "逐个串行合并" in text and "409" in text
    assert "被动触达，不要轮询" in text
    assert "「解决冲突」任务派回" in text and "原负责人" in text


def test_retired_canvas_vocabulary_absent() -> None:
    """DEDAG 退役零残留：画布/<control>/提案/落地/汇总执行域(O8)/O9/delta/decompose 等
    旧编排话术不得出现在任何章节（防旧话术回流）。"""
    text = _sections_text()
    for banned in (
        "画布",
        "<control>",
        "提案",
        "落地",
        "O8",
        "O9",
        "delta",
        "decompose",
        "single_task",
        "节点",
        "汇总输入摘要",
        "replan",
    ):
        assert banned not in text, f"退役词残留: {banned}"
    # 退役 schema 版本串常量已随 <control> 通道删除。
    assert not hasattr(role_templates, "DECOMPOSITION_SCHEMA_VERSION")
    assert not hasattr(role_templates, "DECOMPOSITION_DELTA_SCHEMA_VERSION")


def test_description_prefill_non_empty() -> None:
    """成员级 description_prefill（contracts 常量单源）非空。"""
    assert role_templates.ORCHESTRATOR_DESCRIPTION_PREFILL.strip()


def test_build_role_template_row_shape() -> None:
    """build_orchestrator_role_template 产出 AgentRoleTemplateRow 同形：key/builtin/name/prompt。"""
    row = role_templates.build_orchestrator_role_template(role_template_id=service.new_ulid())
    assert isinstance(row, AgentRoleTemplateRow)
    assert row.key == "orchestrator"
    assert row.builtin is True
    assert row.name == role_templates.ORCHESTRATOR_ROLE_NAME
    assert row.description_prefill.strip()
    assert row.prompt_sections == role_templates.build_orchestrator_prompt_sections()
