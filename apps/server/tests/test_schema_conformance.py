"""核心一致性：alembic 建库后反射每张 M1 表，断言列名集合 == 对应 contracts *Row 字段集。

契约 A §8：字段名即 Pydantic 字段名即 SQLAlchemy 列名（snake_case 三处一致）。
"""

from __future__ import annotations

import pytest
from coagentia_contracts import entities
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

# 表名 → 对应 contracts *Row 模型（契约 A §5 M1 首行清单 17 张）
M1_TABLE_TO_ROW = {
    "workspaces": entities.WorkspaceRow,
    "computers": entities.ComputerRow,
    "members": entities.MemberRow,
    "agents": entities.AgentRow,
    "agent_skills": entities.AgentSkillRow,
    "channels": entities.ChannelRow,
    "channel_members": entities.ChannelMemberRow,
    "messages": entities.MessageRow,
    "message_mentions": entities.MessageMentionRow,
    "files": entities.FileRow,
    "read_positions": entities.ReadPositionRow,
    "reminders": entities.ReminderRow,
    "diagnostic_events": entities.DiagnosticEventRow,
    "token_usage_events": entities.TokenUsageEventRow,
    "ledger_entries": entities.LedgerEntryRow,
    "landing_batches": entities.LandingBatchRow,
    "canvases": entities.CanvasRow,
}


# 表名 → 对应 contracts *Row 模型（契约 A §5 M2 批次 4 张；messages_fts 虚表无 Row，不列）
M2_TABLE_TO_ROW = {
    "tasks": entities.TaskRow,
    "task_events": entities.TaskEventRow,
    "message_task_refs": entities.MessageTaskRefRow,
    "activity_items": entities.ActivityItemRow,
}


def _row_fields(model: type) -> set[str]:
    return set(model.model_fields)  # type: ignore[attr-defined]


def test_m1_batch_has_17_tables() -> None:
    assert len(M1_TABLE_TO_ROW) == 17


def test_m2_batch_has_4_tables() -> None:
    assert len(M2_TABLE_TO_ROW) == 4


@pytest.mark.parametrize("table_name", sorted(M1_TABLE_TO_ROW))
def test_columns_match_contract_row(migrated_engine: Engine, table_name: str) -> None:
    row_model = M1_TABLE_TO_ROW[table_name]
    reflected = {col["name"] for col in inspect(migrated_engine).get_columns(table_name)}
    assert reflected == _row_fields(row_model), (
        f"{table_name}: 反射列集与 {row_model.__name__} 字段集不一致 "
        f"（多 {reflected - _row_fields(row_model)} / 缺 {_row_fields(row_model) - reflected}）"
    )


@pytest.mark.parametrize("table_name", sorted(M2_TABLE_TO_ROW))
def test_m2_columns_match_contract_row(migrated_engine: Engine, table_name: str) -> None:
    row_model = M2_TABLE_TO_ROW[table_name]
    reflected = {col["name"] for col in inspect(migrated_engine).get_columns(table_name)}
    assert reflected == _row_fields(row_model), (
        f"{table_name}: 反射列集与 {row_model.__name__} 字段集不一致 "
        f"（多 {reflected - _row_fields(row_model)} / 缺 {_row_fields(row_model) - reflected}）"
    )
