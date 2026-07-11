"""DB 行 → 契约 *Public dict（形状零偏差；敏感/内部列剔除，契约 A §8.2）。

行映射来自 SQLAlchemy Core（Boolean→bool、JSON→dict/list、Text→str），字段名三处一致，
故绝大多数 Public==Row 直接透传；Computer 剔 api_key_hash、File 剔 stored_path。
经对应 *Public 模型 validate+dump 以保证形状（extra=forbid 会拒绝多余列）。
"""

from __future__ import annotations

from typing import Any

from coagentia_contracts import entities


def _dump(model: type, row: dict[str, Any]) -> dict[str, Any]:
    return model.model_validate(row).model_dump()


def workspace_public(row: dict[str, Any]) -> dict[str, Any]:
    return _dump(entities.WorkspacePublic, row)


def computer_public(row: dict[str, Any]) -> dict[str, Any]:
    clean = {k: v for k, v in row.items() if k != "api_key_hash"}
    return _dump(entities.ComputerPublic, clean)


def member_public(row: dict[str, Any]) -> dict[str, Any]:
    return _dump(entities.MemberPublic, row)


def agent_public(row: dict[str, Any]) -> dict[str, Any]:
    return _dump(entities.AgentPublic, row)


def agent_skill_public(row: dict[str, Any]) -> dict[str, Any]:
    return _dump(entities.AgentSkillPublic, row)


def channel_public(row: dict[str, Any]) -> dict[str, Any]:
    return _dump(entities.ChannelPublic, row)


def message_public(
    row: dict[str, Any], files: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """files = 读面派生附件（契约 A v1.0.4）：REST 消息读面传 []/列表，未附着面留 None。"""
    if files is not None:
        row = {**row, "files": files}
    return _dump(entities.MessagePublic, row)


def read_position_public(row: dict[str, Any]) -> dict[str, Any]:
    return _dump(entities.ReadPositionPublic, row)


def reminder_public(row: dict[str, Any]) -> dict[str, Any]:
    return _dump(entities.ReminderPublic, row)


def held_draft_public(row: dict[str, Any]) -> dict[str, Any]:
    """M4 被扣草稿（护栏 G1–G6）。JSON 列 reasons/file_ids/as_task 直接透传，即 HeldDraftPublic。"""
    return _dump(entities.HeldDraftPublic, row)


def file_public(row: dict[str, Any]) -> dict[str, Any]:
    clean = {k: v for k, v in row.items() if k != "stored_path"}
    return _dump(entities.FilePublic, clean)


def diagnostic_public(row: dict[str, Any]) -> dict[str, Any]:
    return _dump(entities.DiagnosticEventPublic, row)


def task_public(row: dict[str, Any]) -> dict[str, Any]:
    return _dump(entities.TaskPublic, row)


def task_event_public(row: dict[str, Any]) -> dict[str, Any]:
    return _dump(entities.TaskEventPublic, row)


def message_task_ref_public(row: dict[str, Any]) -> dict[str, Any]:
    return _dump(entities.MessageTaskRefPublic, row)


def activity_item_public(row: dict[str, Any]) -> dict[str, Any]:
    return _dump(entities.ActivityItemPublic, row)


def task_contract_public(row: dict[str, Any]) -> dict[str, Any]:
    return _dump(entities.TaskContractPublic, row)


def canvas_public(row: dict[str, Any]) -> dict[str, Any]:
    return _dump(entities.CanvasPublic, row)


def canvas_node_public(row: dict[str, Any]) -> dict[str, Any]:
    return _dump(entities.CanvasNodePublic, row)


def canvas_edge_public(row: dict[str, Any]) -> dict[str, Any]:
    return _dump(entities.CanvasEdgePublic, row)
