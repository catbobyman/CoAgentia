"""契约 A 对照测试：文档说有什么表/字段，代码就必须恰好有什么。

manifest 逐字段转录自 01-实体表与数据模型.md §4（转录时间 2026-07-09，契约 A v1.0）。
注：messages_fts 是 FTS5 虚表（存储面，无线上形状），不在此列。
"""

from coagentia_contracts import entities
from pydantic import BaseModel

TABLES: dict[str, tuple[type[BaseModel], set[str]]] = {
    # ---- 4.1 身份与基座
    "workspaces": (entities.WorkspaceRow, {
        "id", "name", "slug", "attachment_max_mb", "onboarding_greeting", "ui_theme",
        "notif_desktop", "notif_sound", "setup_state", "created_at",
    }),
    "computers": (entities.ComputerRow, {
        "id", "workspace_id", "name", "os", "arch", "daemon_version", "api_key_hash",
        "detected_runtimes", "status", "last_seen_at", "created_at",
    }),
    "members": (entities.MemberRow, {
        "id", "workspace_id", "kind", "name", "role", "removed_at", "created_at",
    }),
    "agents": (entities.AgentRow, {
        "member_id", "computer_id", "runtime", "model", "description", "home_path",
        "status", "created_by_member_id",
    }),
    "agent_skills": (entities.AgentSkillRow, {
        "agent_member_id", "skill", "granted_by_member_id", "granted_at",
    }),
    "agent_role_templates": (entities.AgentRoleTemplateRow, {
        "id", "key", "name", "description_prefill", "prompt_sections", "builtin",
    }),
    # ---- 4.2 会话面
    "channels": (entities.ChannelRow, {
        "id", "workspace_id", "kind", "name", "description", "is_private", "dm_key",
        "archived_at", "joint_ref", "next_task_number", "remind_todo_h", "remind_inprog_h",
        "remind_review_h", "remind_escalation", "held_reeval_min", "held_escalate_n",
        "decomp_mode", "decomp_node_limit", "orch_escalation", "created_at",
    }),
    "channel_members": (entities.ChannelMemberRow, {"channel_id", "member_id", "joined_at"}),
    "channel_notification_settings": (entities.ChannelNotificationSettingRow, {
        "channel_id", "member_id", "mode",
    }),
    "messages": (entities.MessageRow, {
        "id", "workspace_id", "channel_id", "thread_root_id", "author_member_id", "kind",
        "card_kind", "card_ref", "body", "created_at",
    }),
    "message_mentions": (entities.MessageMentionRow, {"message_id", "member_id"}),
    "message_task_refs": (entities.MessageTaskRefRow, {"message_id", "task_id"}),
    "files": (entities.FileRow, {
        "id", "workspace_id", "message_id", "channel_id", "name", "mime", "size_bytes",
        "sha256", "stored_path", "created_at",
    }),
    "read_positions": (entities.ReadPositionRow, {
        "member_id", "channel_id", "last_read_message_id", "last_read_at",
    }),
    "activity_items": (entities.ActivityItemRow, {
        "id", "workspace_id", "member_id", "kind", "channel_id", "message_id", "task_id",
        "created_at", "done_at",
    }),
    # ---- 4.3 任务与契约
    "tasks": (entities.TaskRow, {
        "id", "workspace_id", "channel_id", "number", "root_message_id", "title", "status",
        "owner_member_id", "level", "created_by_member_id", "silence_override_h",
        "status_changed_at", "created_at",
    }),
    "task_events": (entities.TaskEventRow, {
        "seq", "task_id", "kind", "from_status", "to_status", "actor_member_id", "created_at",
    }),
    "task_contracts": (entities.TaskContractRow, {
        "id", "workspace_id", "task_id", "reminder_id", "kind", "version", "body", "revision",
        "superseded_at", "created_by_member_id", "created_at",
    }),
    # ---- 4.4 画布
    "canvases": (entities.CanvasRow, {
        "id", "workspace_id", "channel_id", "baseline_version", "baseline_hash", "updated_at",
    }),
    "canvas_nodes": (entities.CanvasNodeRow, {
        "id", "canvas_id", "kind", "task_id", "is_summary", "system_action", "command",
        "system_status", "pos_x", "pos_y", "created_at",
    }),
    "canvas_edges": (entities.CanvasEdgeRow, {"id", "canvas_id", "from_node_id", "to_node_id"}),
    # ---- 4.5 护栏与提醒
    "held_drafts": (entities.HeldDraftRow, {
        "id", "workspace_id", "agent_member_id", "channel_id", "thread_root_id", "draft_body",
        "reasons", "status", "held_count", "next_reeval_at", "escalated_at",
        "resolved_by_member_id", "resolved_at", "resolution", "created_at",
    }),
    "reminders": (entities.ReminderRow, {
        "id", "workspace_id", "agent_member_id", "kind", "cadence", "anchor_channel_id",
        "anchor_message_id", "anchor_task_id", "loop_contract_id", "next_fire_at", "status",
        "cancelled_by_member_id", "created_at",
    }),
    # ---- 4.6 可观测性
    "diagnostic_events": (entities.DiagnosticEventRow, {
        "seq", "workspace_id", "agent_member_id", "type", "channel_id", "task_id", "batch_id",
        "payload", "created_at",
    }),
    "token_usage_events": (entities.TokenUsageEventRow, {
        "id", "workspace_id", "agent_member_id", "task_id", "channel_id", "input_tokens",
        "output_tokens", "cache_read_tokens", "cache_write_tokens", "source_session",
        "reported_at",
    }),
    # ---- 4.7 账本与落地批次
    "landing_batches": (entities.LandingBatchRow, {
        "id", "workspace_id", "channel_id", "kind", "content_hash", "source_ref",
        "confirmed_by", "status", "created_at", "done_at",
    }),
    "ledger_entries": (entities.LedgerEntryRow, {
        "seq", "op_id", "request_hash", "batch_id", "actor_member_id", "kind", "payload",
        "created_at",
    }),
    # ---- 4.8 编排
    "proposals": (entities.ProposalRow, {
        "id", "workspace_id", "channel_id", "source_task_id", "kind", "revision", "status",
        "body", "proposal_hash", "base_hash", "landed_hash", "adjustments", "repair_count",
        "proposed_by_member_id", "created_at", "updated_at",
    }),
    # ---- 4.9 交付链路
    "projects": (entities.ProjectRow, {
        "id", "workspace_id", "name", "repo_path", "dev_command", "deploy_command",
        "preview_idle_min", "worktree_keep_days", "created_at",
    }),
    "channel_projects": (entities.ChannelProjectRow, {"channel_id", "project_id"}),
    "worktrees": (entities.WorktreeRow, {
        "id", "workspace_id", "project_id", "task_id", "branch", "path", "status",
        "created_at", "merged_at", "cleaned_at",
    }),
    "preview_sessions": (entities.PreviewSessionRow, {
        "id", "workspace_id", "task_id", "worktree_id", "port", "status", "started_at",
        "last_active_at", "recycled_at",
    }),
    "deployments": (entities.DeploymentRow, {
        "id", "workspace_id", "project_id", "triggered_by_member_id", "branch", "commit_hash",
        "command", "status", "exit_code", "url", "log_path", "token_summary", "started_at",
        "finished_at",
    }),
    # ---- 4.10 模板
    "templates": (entities.TemplateRow, {
        "id", "workspace_id", "name", "description", "body", "builtin",
        "created_by_member_id", "created_at",
    }),
}


def test_table_count() -> None:
    """契约 A §4 实际定义 34 张表（头表"31 表"为统计笔误，见收口报告）。"""
    assert len(TABLES) == 34


def test_every_table_fields_match_contract() -> None:
    mismatches: list[str] = []
    for table, (model, expected) in TABLES.items():
        actual = set(model.model_fields)
        if actual != expected:
            missing = expected - actual
            extra = actual - expected
            mismatches.append(f"{table}: missing={sorted(missing)} extra={sorted(extra)}")
    assert not mismatches, "\n".join(mismatches)


def test_defaults_match_contract() -> None:
    """契约 A 标注的默认值抽查（阈值组是产品语义，错了 UI 直接错）。"""
    ch = entities.ChannelRow.model_fields
    assert ch["remind_todo_h"].default == 24
    assert ch["remind_inprog_h"].default == 12
    assert ch["remind_review_h"].default == 24
    assert ch["held_reeval_min"].default == 5
    assert ch["held_escalate_n"].default == 3
    assert ch["decomp_node_limit"].default == 12
    assert ch["next_task_number"].default == 1
    ws = entities.WorkspaceRow.model_fields
    assert ws["attachment_max_mb"].default == 200
    pj = entities.ProjectRow.model_fields
    assert pj["preview_idle_min"].default == 30
    assert pj["worktree_keep_days"].default == 7
    assert entities.CanvasRow.model_fields["baseline_version"].default == 0
    assert entities.HeldDraftRow.model_fields["held_count"].default == 1
    assert entities.ProposalRow.model_fields["repair_count"].default == 0
