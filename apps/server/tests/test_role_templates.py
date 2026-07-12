"""J11 波 1：Orchestrator builtin 角色模板数据的内容不变量单测（03 §3.1 / 拆解设计 §12/§13.1）。

只断言数据内容（不建表 / 不接 upsert / 不消费——归波 2）：key 恒 orchestrator、拆解设计 §13.1 七条
骨架关键句逐条、§12 规模判断表四行信号短语原文、schema 版本串、description_prefill 非空、
AgentRoleTemplateRow 同形。
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


def test_seven_skeleton_items_each_present() -> None:
    """拆解设计 §13.1 七条编号骨架逐条关键句包含（第 1..7 条各一断言）。"""
    section_names = {s["section"] for s in role_templates.build_orchestrator_prompt_sections()}
    for name in (
        "scale_judgment",
        "decompose_nodes",
        "edges",
        "suggested_owner",
        "writes_code",
        "output_protocol",
        "repair",
    ):
        assert name in section_names
    text = _sections_text()
    assert "先判断规模" in text  # 第 1 条
    assert "节点数 2..<上限>" in text  # 第 2 条
    assert "只画真实的时序/数据依赖" in text  # 第 3 条
    assert "suggested_owner 从频道成员里选" in text  # 第 4 条
    assert "writes_code: true 并指定 project" in text and "必须给 merge_plan" in text  # 第 5 条
    assert "唯一的 <control> 块" in text  # 第 6 条（输出协议）
    assert "按错误清单逐条修复，不要辩解" in text  # 第 7 条


def test_scale_table_four_signals_verbatim() -> None:
    """§12 规模判断表四行信号短语原文（信号/倾向两列 + 「宁欠拆不过拆」理由句）。"""
    text = _sections_text()
    assert "单一交付物 + 单一技能域 + 一个 Agent 一次会话可完成" in text
    assert (
        "需要 ≥2 个不同角色/技能域（如实现+评审）；或交付物 ≥2 个独立可验收单元；"
        "或存在天然串/并行阶段"
        in text
    )
    assert "需求含糊、无法列出 ≥3 条可证伪 AC" in text
    assert "宁欠拆不过拆" in text
    # 倾向列并存 + 理由句。
    assert "single_task" in text and "decompose" in text
    assert "过拆的回收成本高" in text


def test_schema_version_string() -> None:
    """输出协议 schema 版本串 = coagentia.decomposition.v1（与 kernel/golden 单源一致）。"""
    assert role_templates.DECOMPOSITION_SCHEMA_VERSION == "coagentia.decomposition.v1"
    assert "coagentia.decomposition.v1" in _sections_text()


def test_description_prefill_non_empty() -> None:
    """成员级原创话术留 TODO（波 4 定稿），但骨架占位非空。"""
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
