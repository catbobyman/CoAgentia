"""SQLAlchemy 2.0 DeclarativeBase + M1 批次 17 张表（契约 A §4/§5）。

纪律（契约 A §8.1）：列名/类型逐字对齐 packages/contracts 的对应 *Row 模型
（字段名即列名，snake_case 三处一致）；枚举列 import contracts.enums 的 StrEnum，
以 SQLAlchemy Enum(values_callable=…) 存枚举 value，不重复定义字面量。

nullability 由 Mapped 类型推导（SA 2.0 惯用）：Mapped[str] → NOT NULL，
Mapped[str | None] → NULL。不可变表（messages/files/ledger_entries/diagnostic_events/
token_usage_events）的禁 UPDATE/DELETE 触发器由 Alembic 迁移 op.execute 落原生 SQL。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from coagentia_contracts.enums import (
    ActivityKind,
    AgentStatus,
    CanvasNodeKind,
    CardKind,
    ChannelKind,
    ComputerStatus,
    ContractKind,
    DecompMode,
    DeploymentStatus,
    HeldDraftStatus,
    HeldResolution,
    LandingBatchKind,
    LandingBatchStatus,
    MemberKind,
    MemberRole,
    MessageKind,
    NotificationMode,
    PreviewStatus,
    ProposalKind,
    ProposalStatus,
    ReminderKind,
    ReminderStatus,
    Runtime,
    SystemAction,
    SystemNodeStatus,
    TaskEventKind,
    TaskLevel,
    TaskStatus,
    UiTheme,
    UpstreamPolicy,
    WorktreeStatus,
)
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# ── 建表批次清单（契约 A §5 建表节奏）──────────────────────────────
# 迁移按批次冻结：0001 只建 M1_TABLES，0002 只建 M2_TABLES。
# 若用 Base.metadata.create_all(bind) 读实时全集，M2 模型入库后 0001 会连带
# 建出 M2 表，0002 再建即 "table already exists"（坑1）——故两迁移各自显式点名。
M1_TABLES: tuple[str, ...] = (
    "workspaces", "computers", "members", "agents", "agent_skills",
    "channels", "channel_members", "messages", "message_mentions", "files",
    "read_positions", "reminders", "diagnostic_events", "token_usage_events",
    "ledger_entries", "landing_batches", "canvases",
)
M2_TABLES: tuple[str, ...] = (
    "tasks", "task_events", "message_task_refs", "activity_items",
)
# M3（契约 A §4.3/§4.4）：task_contracts/canvas_nodes/canvas_edges 三表均可变
# （superseded_at/pos_x/pos_y 等列会被 UPDATE），故无对应 M3_IMMUTABLE_TABLES 常量。
M3_TABLES: tuple[str, ...] = ("task_contracts", "canvas_nodes", "canvas_edges")
# M4（契约 A §4.5）：held_drafts 一张表。**可变**——status/held_count/resolved_* 会 UPDATE
# （重评估重发 = 同行 held_count+1、status 回 held；三键干预写 resolved_*），故不进任何
# IMMUTABLE 集、不建禁 UPDATE/DELETE 触发器。0006 一次建齐（块 a 期间空置）。
M4_TABLES: tuple[str, ...] = ("held_drafts",)
# M5（契约 A §4.10/§4.2）：templates + channel_notification_settings 两张，0007 一次建齐。
# 均**可变**（templates 无 UPDATE 面但 builtin 启动 upsert 会改；notification_settings mode 会
# UPDATE）——不进 IMMUTABLE 集、无触发器。templates 块 a 期间空置（M3"迁移不拆两次"先例）。
M5_TABLES: tuple[str, ...] = ("templates", "channel_notification_settings")
# M6a（契约 A v1.0.8 §4.9）：三张交付表一次建齐；tasks 两列由 0008 增量补齐。
M6A_TABLES: tuple[str, ...] = ("projects", "channel_projects", "worktrees")
# M6b（契约 A v1.0.10 §4.8/§4.1）：proposals + agent_role_templates 两张新表一次建齐；
# agents.role_template_key 由 0009 增量补齐（第二例既有表加列，沿 0008 tasks 加列先例）。
M6B_TABLES: tuple[str, ...] = ("proposals", "agent_role_templates")
# M7a（契约 A v1.0.11 §4.9）：preview_sessions 一张，0010 一次建齐（纯新表，无既有表改动）。
M7A_TABLES: tuple[str, ...] = ("preview_sessions",)
# M7b（契约 A v1.5 / B §13.2）：deployments 一张，0011 一次建齐（纯新表，无既有表改动）。
M7B_TABLES: tuple[str, ...] = ("deployments",)
# M8（契约 A v1.0.12 §4.8/§6.4）：summary_runs 一张新表（O8 汇总协调状态）；canvas_nodes.
# upstream_policy 由 0012 增量补列（第三例既有表加列，沿 0008 tasks / 0009 agents 先例）。
# summary_runs **可变**（round/stall/replan 计数条件 UPDATE、blocked_at 置位/清空）——不进
# IMMUTABLE 集、无触发器。W9 内核双档 satisfied 与 landing 默认 partial 归 M8b L7 消费（本批仅落
# schema，列默认 strict 行为逐字节不变）。
M8_TABLES: tuple[str, ...] = ("summary_runs",)

# ── 不可变表触发器批次（契约 A §1 六表；坑2）────────────────────────
# task_events 在 0002 才建，故 0001 只能给 M1 的 5 张建触发器；0002 补第 6 张。
M1_IMMUTABLE_TABLES: tuple[str, ...] = (
    "messages", "files", "ledger_entries", "diagnostic_events", "token_usage_events",
)
M2_IMMUTABLE_TABLES: tuple[str, ...] = ("task_events",)
# 并集 = head 后应有触发器的全集（测试 test_upgrade_creates_immutable_triggers 遍历此集）。
IMMUTABLE_TABLES: tuple[str, ...] = M1_IMMUTABLE_TABLES + M2_IMMUTABLE_TABLES

_ULID = String(26)


class Base(DeclarativeBase):
    pass


def tbl(model: type[Base]) -> Table:
    """`Model.__table__` 的 Table 窄化（pyright 债批：declarative 把 __table__ 标注为
    FromClause，DML（insert/update/delete）与 Table API 需要 Table——本仓模型恒为实表）。"""
    table = model.__table__
    assert isinstance(table, Table)
    return table


def row_dict(row: Mapping[Any, Any] | None) -> dict[str, Any]:
    """`mappings().first()` 的非空窄化（pyright 债批）：调用点按逻辑不变量必存在
    （PK 回查 / 刚插入行回读 / 聚合行）——为空即编程错误，assert 直接暴露而非静默。"""
    assert row is not None, "row_dict: 回查行不存在（调用点不变量被破坏）"
    return dict(row)


def _enum(enum_cls: type) -> SAEnum:
    """存枚举 value（TEXT + CHECK），值域 = contracts.enums（契约 A §1/§8.1）。"""
    return SAEnum(
        enum_cls,
        values_callable=lambda e: [m.value for m in e],
        native_enum=False,
        validate_strings=True,
    )


# ---------------------------------------------------------------- 4.1 身份与基座


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(_ULID, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    slug: Mapped[str] = mapped_column(Text, unique=True)
    attachment_max_mb: Mapped[int] = mapped_column(Integer, server_default=text("200"))
    onboarding_greeting: Mapped[bool] = mapped_column(Boolean, server_default=text("1"))
    ui_theme: Mapped[str] = mapped_column(_enum(UiTheme), server_default=text("'dark'"))
    notif_desktop: Mapped[bool] = mapped_column(Boolean, server_default=text("1"))
    notif_sound: Mapped[bool] = mapped_column(Boolean, server_default=text("0"))
    setup_state: Mapped[dict] = mapped_column(JSON, server_default=text("'{}'"))
    created_at: Mapped[str] = mapped_column(Text)


class Computer(Base):
    __tablename__ = "computers"

    id: Mapped[str] = mapped_column(_ULID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(_ULID, ForeignKey("workspaces.id"))
    name: Mapped[str] = mapped_column(Text)
    os: Mapped[str | None] = mapped_column(Text)
    arch: Mapped[str | None] = mapped_column(Text)
    daemon_version: Mapped[str | None] = mapped_column(Text)
    api_key_hash: Mapped[str] = mapped_column(Text, unique=True)
    detected_runtimes: Mapped[list] = mapped_column(JSON, server_default=text("'[]'"))
    status: Mapped[str] = mapped_column(_enum(ComputerStatus), server_default=text("'offline'"))
    last_seen_at: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)


class Member(Base):
    __tablename__ = "members"
    __table_args__ = (
        # UNIQUE(workspace_id, name COLLATE NOCASE)——@解析键（契约 A members）
        Index(
            "uq_members_workspace_name_nocase",
            "workspace_id",
            text("name COLLATE NOCASE"),
            unique=True,
        ),
        # R1：kind='agent' → role≠'owner'
        CheckConstraint(
            "kind != 'agent' OR role != 'owner'",
            name="ck_members_agent_not_owner",
        ),
    )

    id: Mapped[str] = mapped_column(_ULID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(_ULID, ForeignKey("workspaces.id"))
    kind: Mapped[str] = mapped_column(_enum(MemberKind))
    name: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(_enum(MemberRole), server_default=text("'member'"))
    removed_at: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)


class Agent(Base):
    __tablename__ = "agents"

    member_id: Mapped[str] = mapped_column(_ULID, ForeignKey("members.id"), primary_key=True)
    computer_id: Mapped[str] = mapped_column(_ULID, ForeignKey("computers.id"))
    runtime: Mapped[str] = mapped_column(_enum(Runtime))
    model: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, server_default=text("''"))
    home_path: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(_enum(AgentStatus), server_default=text("'offline'"))
    created_by_member_id: Mapped[str] = mapped_column(_ULID, ForeignKey("members.id"))
    # v1.0.10（0009 加列）：引用 agent_role_templates.key（无 FK——模板可增删，身份标记不失效）。
    role_template_key: Mapped[str | None] = mapped_column(Text)


class AgentSkill(Base):
    __tablename__ = "agent_skills"
    __table_args__ = (PrimaryKeyConstraint("agent_member_id", "skill"),)

    agent_member_id: Mapped[str] = mapped_column(_ULID, ForeignKey("agents.member_id"))
    skill: Mapped[str] = mapped_column(Text)
    granted_by_member_id: Mapped[str] = mapped_column(_ULID, ForeignKey("members.id"))
    granted_at: Mapped[str] = mapped_column(Text)


class AgentRoleTemplate(Base):
    """M6b 内置角色模板（03 §3.1「Orchestrator = 数据不是代码」）——全局字典表，无 workspace_id。"""

    __tablename__ = "agent_role_templates"

    id: Mapped[str] = mapped_column(_ULID, primary_key=True)
    key: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    description_prefill: Mapped[str] = mapped_column(Text)
    prompt_sections: Mapped[Any] = mapped_column(JSON)
    builtin: Mapped[bool] = mapped_column(Boolean, server_default=text("1"))


# ---------------------------------------------------------------- 4.2 会话面


class Channel(Base):
    __tablename__ = "channels"
    __table_args__ = (
        # UNIQUE(workspace_id, name) WHERE kind='channel'（部分唯一索引）
        Index(
            "uq_channels_workspace_name_channel",
            "workspace_id",
            "name",
            unique=True,
            sqlite_where=text("kind = 'channel'"),
        ),
    )

    id: Mapped[str] = mapped_column(_ULID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(_ULID, ForeignKey("workspaces.id"))
    kind: Mapped[str] = mapped_column(_enum(ChannelKind))
    name: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, server_default=text("''"))
    is_private: Mapped[bool] = mapped_column(Boolean, server_default=text("0"))
    dm_key: Mapped[str | None] = mapped_column(Text, unique=True)
    archived_at: Mapped[str | None] = mapped_column(Text)
    joint_ref: Mapped[str | None] = mapped_column(Text)
    next_task_number: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    remind_todo_h: Mapped[int] = mapped_column(Integer, server_default=text("24"))
    remind_inprog_h: Mapped[int] = mapped_column(Integer, server_default=text("12"))
    remind_review_h: Mapped[int] = mapped_column(Integer, server_default=text("24"))
    remind_escalation: Mapped[bool] = mapped_column(Boolean, server_default=text("1"))
    held_reeval_min: Mapped[int] = mapped_column(Integer, server_default=text("5"))
    held_escalate_n: Mapped[int] = mapped_column(Integer, server_default=text("3"))
    decomp_mode: Mapped[str] = mapped_column(_enum(DecompMode), server_default=text("'draft'"))
    decomp_node_limit: Mapped[int] = mapped_column(Integer, server_default=text("12"))
    orch_escalation: Mapped[bool] = mapped_column(Boolean, server_default=text("0"))
    created_at: Mapped[str] = mapped_column(Text)


class ChannelMember(Base):
    __tablename__ = "channel_members"
    __table_args__ = (PrimaryKeyConstraint("channel_id", "member_id"),)

    channel_id: Mapped[str] = mapped_column(_ULID, ForeignKey("channels.id"))
    member_id: Mapped[str] = mapped_column(_ULID, ForeignKey("members.id"))
    joined_at: Mapped[str] = mapped_column(Text)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_channel_created", "channel_id", "created_at"),
        Index("ix_messages_thread_created", "thread_root_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(_ULID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(_ULID, ForeignKey("workspaces.id"))
    channel_id: Mapped[str] = mapped_column(_ULID, ForeignKey("channels.id"))
    thread_root_id: Mapped[str | None] = mapped_column(_ULID, ForeignKey("messages.id"))
    author_member_id: Mapped[str | None] = mapped_column(_ULID, ForeignKey("members.id"))
    kind: Mapped[str] = mapped_column(_enum(MessageKind), server_default=text("'user'"))
    card_kind: Mapped[str | None] = mapped_column(_enum(CardKind))
    card_ref: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)


class MessageMention(Base):
    __tablename__ = "message_mentions"
    __table_args__ = (PrimaryKeyConstraint("message_id", "member_id"),)

    message_id: Mapped[str] = mapped_column(_ULID, ForeignKey("messages.id"))
    member_id: Mapped[str] = mapped_column(_ULID, ForeignKey("members.id"))


class File(Base):
    __tablename__ = "files"
    __table_args__ = (
        # 0004：消息读面按 message_id 批查附件（契约 A v1.0.4）+ 频道文件页签按 id 游标。
        Index("ix_files_message", "message_id"),
        Index("ix_files_channel", "channel_id", "id"),
    )

    id: Mapped[str] = mapped_column(_ULID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(_ULID, ForeignKey("workspaces.id"))
    message_id: Mapped[str] = mapped_column(_ULID, ForeignKey("messages.id"))
    channel_id: Mapped[str] = mapped_column(_ULID, ForeignKey("channels.id"))
    name: Mapped[str] = mapped_column(Text)
    mime: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(Text)
    stored_path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)


class ReadPosition(Base):
    __tablename__ = "read_positions"
    __table_args__ = (PrimaryKeyConstraint("member_id", "channel_id"),)

    member_id: Mapped[str] = mapped_column(_ULID, ForeignKey("members.id"))
    channel_id: Mapped[str] = mapped_column(_ULID, ForeignKey("channels.id"))
    last_read_message_id: Mapped[str] = mapped_column(_ULID, ForeignKey("messages.id"))
    last_read_at: Mapped[str] = mapped_column(Text)


# ---------------------------------------------------------------- 4.5 提醒（M1）


class Reminder(Base):
    __tablename__ = "reminders"
    __table_args__ = (
        # D1-L2：kind='recurring' → loop_contract_id NOT NULL
        CheckConstraint(
            "kind != 'recurring' OR loop_contract_id IS NOT NULL",
            name="ck_reminders_recurring_needs_contract",
        ),
    )

    id: Mapped[str] = mapped_column(_ULID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(_ULID, ForeignKey("workspaces.id"))
    agent_member_id: Mapped[str] = mapped_column(_ULID, ForeignKey("members.id"))
    kind: Mapped[str] = mapped_column(_enum(ReminderKind))
    cadence: Mapped[str] = mapped_column(Text)
    anchor_channel_id: Mapped[str] = mapped_column(_ULID, ForeignKey("channels.id"))
    anchor_message_id: Mapped[str | None] = mapped_column(_ULID, ForeignKey("messages.id"))
    # anchor_task_id→tasks(M2)、loop_contract_id→task_contracts(M3)：目标表跨批次未建，不落 FK
    anchor_task_id: Mapped[str | None] = mapped_column(_ULID)
    loop_contract_id: Mapped[str | None] = mapped_column(_ULID)
    next_fire_at: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(_enum(ReminderStatus), server_default=text("'active'"))
    cancelled_by_member_id: Mapped[str | None] = mapped_column(_ULID, ForeignKey("members.id"))
    created_at: Mapped[str] = mapped_column(Text)


# ---------------------------------------------------------------- 4.6 可观测性（不可变表）


class DiagnosticEvent(Base):
    __tablename__ = "diagnostic_events"
    __table_args__ = (
        Index("ix_diag_agent_seq", "agent_member_id", "seq"),
        Index("ix_diag_type", "type"),
        Index("ix_diag_batch", "batch_id"),
        {"sqlite_autoincrement": True},
    )

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(_ULID, ForeignKey("workspaces.id"))
    agent_member_id: Mapped[str | None] = mapped_column(_ULID, ForeignKey("members.id"))
    type: Mapped[str] = mapped_column(Text)
    channel_id: Mapped[str | None] = mapped_column(_ULID, ForeignKey("channels.id"))
    task_id: Mapped[str | None] = mapped_column(_ULID)  # tasks（M2）未建，不落 FK
    batch_id: Mapped[str | None] = mapped_column(_ULID, ForeignKey("landing_batches.id"))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(Text)


class TokenUsageEvent(Base):
    __tablename__ = "token_usage_events"

    id: Mapped[str] = mapped_column(_ULID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(_ULID, ForeignKey("workspaces.id"))
    agent_member_id: Mapped[str] = mapped_column(_ULID, ForeignKey("members.id"))
    task_id: Mapped[str | None] = mapped_column(_ULID)  # tasks（M2）未建，不落 FK
    channel_id: Mapped[str | None] = mapped_column(_ULID, ForeignKey("channels.id"))
    input_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    cache_read_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    cache_write_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    source_session: Mapped[str | None] = mapped_column(Text)
    reported_at: Mapped[str] = mapped_column(Text)


# ---------------------------------------------------------------- 4.7 账本与落地批次（M1）


class LandingBatch(Base):
    __tablename__ = "landing_batches"

    id: Mapped[str] = mapped_column(_ULID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(_ULID, ForeignKey("workspaces.id"))
    channel_id: Mapped[str] = mapped_column(_ULID, ForeignKey("channels.id"))
    kind: Mapped[str] = mapped_column(_enum(LandingBatchKind))
    content_hash: Mapped[str] = mapped_column(Text)
    source_ref: Mapped[str] = mapped_column(Text)
    confirmed_by: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(_enum(LandingBatchStatus), server_default=text("'running'"))
    created_at: Mapped[str] = mapped_column(Text)
    done_at: Mapped[str | None] = mapped_column(Text)


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    __table_args__ = (
        UniqueConstraint("op_id", name="uq_ledger_op_id"),
        {"sqlite_autoincrement": True},
    )

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    op_id: Mapped[str] = mapped_column(Text)
    request_hash: Mapped[str] = mapped_column(Text)
    batch_id: Mapped[str | None] = mapped_column(_ULID, ForeignKey("landing_batches.id"))
    actor_member_id: Mapped[str | None] = mapped_column(_ULID, ForeignKey("members.id"))
    kind: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(Text)


# ---------------------------------------------------------------- 4.4 画布（M1 建表）


class Canvas(Base):
    __tablename__ = "canvases"

    id: Mapped[str] = mapped_column(_ULID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(_ULID, ForeignKey("workspaces.id"))
    channel_id: Mapped[str] = mapped_column(_ULID, ForeignKey("channels.id"), unique=True)
    baseline_version: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    baseline_hash: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)


# ---------------------------------------------------------------- 4.3 任务与契约（M2）


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        # UNIQUE(channel_id, number)——取自 channels.next_task_number（契约 A §4.3）
        UniqueConstraint("channel_id", "number", name="uq_tasks_channel_number"),
        Index("ix_tasks_channel_status", "channel_id", "status"),
        Index("ix_tasks_owner_status", "owner_member_id", "status"),
    )

    id: Mapped[str] = mapped_column(_ULID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(_ULID, ForeignKey("workspaces.id"))
    channel_id: Mapped[str] = mapped_column(_ULID, ForeignKey("channels.id"))
    number: Mapped[int] = mapped_column(Integer)
    # 锚点消息：一条消息至多一个任务（root_message_id UNIQUE，契约 A §4.3 / B §9.3）
    root_message_id: Mapped[str] = mapped_column(
        _ULID, ForeignKey("messages.id"), unique=True
    )
    title: Mapped[str] = mapped_column(Text)
    # CHECK ∈ (todo,in_progress,in_review,done,closed) 由 _enum(TaskStatus) 生成
    status: Mapped[str] = mapped_column(_enum(TaskStatus), server_default=text("'todo'"))
    # 同刻唯一 owner = 单列天然满足；claim 防重 = 条件更新 owner IS NULL（T2）
    owner_member_id: Mapped[str | None] = mapped_column(_ULID, ForeignKey("members.id"))
    level: Mapped[str] = mapped_column(_enum(TaskLevel), server_default=text("'l1'"))
    created_by_member_id: Mapped[str] = mapped_column(_ULID, ForeignKey("members.id"))
    project_id: Mapped[str | None] = mapped_column(_ULID, ForeignKey("projects.id"))
    writes_code: Mapped[bool] = mapped_column(Boolean, server_default=text("0"))
    silence_override_h: Mapped[int | None] = mapped_column(Integer)  # D5，M4 才消费
    status_changed_at: Mapped[str] = mapped_column(Text)  # 沉默提醒计时锚
    created_at: Mapped[str] = mapped_column(Text)


class TaskEvent(Base):
    __tablename__ = "task_events"
    __table_args__ = (
        Index("ix_task_events_task_seq", "task_id", "seq"),
        {"sqlite_autoincrement": True},
    )

    # 契约 A §4.3：seq INTEGER PK AUTOINCREMENT（同 ledger_entries/diagnostic_events 口径）
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(_ULID, ForeignKey("tasks.id"))
    # CHECK ∈ (status_change,claim,unclaim,assign,force_start,reminder_sent,escalated)
    # ——assign 由 C0 增补进 TaskEventKind，_enum 自动纳入 CHECK
    kind: Mapped[str] = mapped_column(_enum(TaskEventKind))
    # kind=status_change 时必填（app 级；DB 允空以承载 claim/assign 行）
    from_status: Mapped[str | None] = mapped_column(_enum(TaskStatus))
    to_status: Mapped[str | None] = mapped_column(_enum(TaskStatus))
    # C0 增列：kind∈claim/assign 时 = 新 owner（assign 置空为 NULL）——owner 变更审计闭环
    owner_member_id: Mapped[str | None] = mapped_column(_ULID, ForeignKey("members.id"))
    actor_member_id: Mapped[str | None] = mapped_column(_ULID, ForeignKey("members.id"))
    created_at: Mapped[str] = mapped_column(Text)


# ---------------------------------------------------------------- 4.2 会话面派生（M2）


class MessageTaskRef(Base):
    __tablename__ = "message_task_refs"
    __table_args__ = (PrimaryKeyConstraint("message_id", "task_id"),)  # 复合 PK

    message_id: Mapped[str] = mapped_column(_ULID, ForeignKey("messages.id"))
    task_id: Mapped[str] = mapped_column(_ULID, ForeignKey("tasks.id"))


class ActivityItem(Base):
    __tablename__ = "activity_items"

    id: Mapped[str] = mapped_column(_ULID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(_ULID, ForeignKey("workspaces.id"))
    member_id: Mapped[str] = mapped_column(_ULID, ForeignKey("members.id"))  # 接收者
    # CHECK ∈ (mention,dm,silence_escalation,held_escalation,fail_closed,system)
    # ——dm 由 C0 增补进 ActivityKind
    kind: Mapped[str] = mapped_column(_enum(ActivityKind))
    channel_id: Mapped[str | None] = mapped_column(_ULID, ForeignKey("channels.id"))
    message_id: Mapped[str | None] = mapped_column(_ULID, ForeignKey("messages.id"))
    task_id: Mapped[str | None] = mapped_column(_ULID, ForeignKey("tasks.id"))
    created_at: Mapped[str] = mapped_column(Text)
    done_at: Mapped[str | None] = mapped_column(Text)  # "Mark as done"


# ---------------------------------------------------------------- 4.3/4.4 契约与画布（M3）


class TaskContract(Base):
    __tablename__ = "task_contracts"
    __table_args__ = (
        # task_id 与 reminder_id 恰一非空（契约 A §4.3）
        CheckConstraint(
            "(task_id IS NOT NULL) + (reminder_id IS NOT NULL) = 1",
            name="ck_task_contracts_task_xor_reminder",
        ),
        # 修订链不变量的 DB 兜底：同 (task_id, kind) 至多一个活动行（superseded_at IS NULL）。
        # 并发提交同 kind 时第二个活动插入被拒（IntegrityError），杜绝"两活动行"污染修订链、
        # 保证 T7 门读到确定的活动 handoff（review 修复）。reminder 侧对称索引随 M4。
        Index(
            "uq_task_contracts_active",
            "task_id",
            "kind",
            unique=True,
            sqlite_where=text("superseded_at IS NULL AND task_id IS NOT NULL"),
        ),
        # 读面索引：active_contracts / active_contract / T7 门每次 in_review 都按 task_id 过滤
        # （对齐同批 task_events 的 ix_task_events_task_seq；无索引则契约累积后全表扫）。
        Index("ix_task_contracts_task", "task_id"),
    )

    id: Mapped[str] = mapped_column(_ULID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(_ULID, ForeignKey("workspaces.id"))
    task_id: Mapped[str | None] = mapped_column(_ULID, ForeignKey("tasks.id"))
    # reminder_id→reminders：与 reminders.loop_contract_id 对称，不落 FK
    # （跨批引用惯例，见 Reminder 模型 loop_contract_id 注释）——本批只落 task_id 一侧 FK。
    reminder_id: Mapped[str | None] = mapped_column(_ULID)
    kind: Mapped[str] = mapped_column(_enum(ContractKind))
    version: Mapped[str] = mapped_column(Text)  # 'coagentia.task-plan.v1' 等（PRD §4.3）
    body: Mapped[dict] = mapped_column(JSON)  # M3 收紧为三种 schema（JsonValue 占位）
    revision: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    superseded_at: Mapped[str | None] = mapped_column(Text)  # UPDATE 写入——本表非不可变
    created_by_member_id: Mapped[str] = mapped_column(_ULID, ForeignKey("members.id"))
    created_at: Mapped[str] = mapped_column(Text)


class CanvasNode(Base):
    __tablename__ = "canvas_nodes"
    __table_args__ = (
        # kind='agent' → task_id NOT NULL（引用不是副本，C8）
        CheckConstraint(
            "kind != 'agent' OR task_id IS NOT NULL",
            name="ck_canvas_nodes_agent_needs_task",
        ),
        # kind='system' → system_action NOT NULL（W8）
        CheckConstraint(
            "kind != 'system' OR system_action IS NOT NULL",
            name="ck_canvas_nodes_system_needs_action",
        ),
    )

    id: Mapped[str] = mapped_column(_ULID, primary_key=True)
    canvas_id: Mapped[str] = mapped_column(_ULID, ForeignKey("canvases.id"))
    kind: Mapped[str] = mapped_column(_enum(CanvasNodeKind))
    task_id: Mapped[str | None] = mapped_column(_ULID, ForeignKey("tasks.id"), unique=True)
    is_summary: Mapped[bool] = mapped_column(Boolean, server_default=text("0"))
    system_action: Mapped[str | None] = mapped_column(_enum(SystemAction))
    command: Mapped[str | None] = mapped_column(Text)  # system_action='check' 必填（V14，app 级）
    system_status: Mapped[str | None] = mapped_column(_enum(SystemNodeStatus))
    # v1.0.12（M8 / W9）：前驱 satisfied 判定档，默认 strict（现状语义；汇总节点落地默认 partial）。
    # 不参与基线快照（放行策略非结构身份，改档不动 baseline/base）；W9 satisfied 由内核
    # derive_blocked 按 live 行读取（M8b L7）。0012 增量补列（第三例既有表加列，沿 0008/0009）。
    upstream_policy: Mapped[str] = mapped_column(
        _enum(UpstreamPolicy), server_default=text("'strict'")
    )
    pos_x: Mapped[float] = mapped_column(Float, server_default=text("0"))  # 不参与基线快照
    pos_y: Mapped[float] = mapped_column(Float, server_default=text("0"))
    created_at: Mapped[str] = mapped_column(Text)


class CanvasEdge(Base):
    __tablename__ = "canvas_edges"
    __table_args__ = (
        UniqueConstraint(
            "canvas_id", "from_node_id", "to_node_id", name="uq_canvas_edges_triplet"
        ),
        # 无自环；无环（DAG）由 server 事务内拓扑排序保证，非 DB 约束（M3b E4/E5）
        CheckConstraint("from_node_id != to_node_id", name="ck_canvas_edges_no_self_loop"),
    )

    id: Mapped[str] = mapped_column(_ULID, primary_key=True)
    canvas_id: Mapped[str] = mapped_column(_ULID, ForeignKey("canvases.id"))
    from_node_id: Mapped[str] = mapped_column(_ULID, ForeignKey("canvas_nodes.id"))
    to_node_id: Mapped[str] = mapped_column(_ULID, ForeignKey("canvas_nodes.id"))


# ---------------------------------------------------------------- 4.5 护栏与提醒（M4）


class HeldDraft(Base):
    """M4（D4/G1–G6）：被扣草稿。**可变表**——status/held_count/resolved_* 会 UPDATE。"""

    __tablename__ = "held_drafts"
    __table_args__ = (
        # 活动行唯一（契约 A v1.0.5）：同 (agent_member_id, channel_id, thread_root_id) 至多一个
        # 活动行（status ∈ held/reevaluating）——held 关联规则的 DB 兜底，防并发重发建双行。
        # thread_root_id 可空 → 表达式列 COALESCE(thread_root_id, '')（先例
        # uq_task_contracts_active、Member.uq_members_workspace_name_nocase 的 text() 表达式列）。
        Index(
            "uq_held_drafts_active",
            "agent_member_id",
            "channel_id",
            text("COALESCE(thread_root_id, '')"),
            unique=True,
            sqlite_where=text("status IN ('held', 'reevaluating')"),
        ),
        # 读面索引：GET /held-drafts?status= 按 status 过滤（对齐同批读面索引先例）。
        Index("ix_held_drafts_status", "status"),
    )

    id: Mapped[str] = mapped_column(_ULID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(_ULID, ForeignKey("workspaces.id"))
    agent_member_id: Mapped[str] = mapped_column(_ULID, ForeignKey("members.id"))
    channel_id: Mapped[str] = mapped_column(_ULID, ForeignKey("channels.id"))
    # thread_root_id→messages.id：目标线程根消息（messages 为 M1，已建，落 FK；同 Message 惯例）
    thread_root_id: Mapped[str | None] = mapped_column(_ULID, ForeignKey("messages.id"))
    draft_body: Mapped[str] = mapped_column(Text)  # 草稿全文（G2 卡片可见）
    # v1.0.5：被扣草稿保存完整发送载荷——放行"原样发送"（G3）不丢附件、不丢建任务意图。
    file_ids: Mapped[list | None] = mapped_column(JSON)  # staging 附件 id 清单（契约 D §9.2）
    as_task: Mapped[dict | None] = mapped_column(JSON)  # 携带的 as_task 意图原样保存（B §9.4）
    # reasons：HeldDraftReasons（unread_message_ids 上限 50 + total_unread 真实计数，v1.0.5）
    reasons: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(_enum(HeldDraftStatus), server_default=text("'held'"))
    held_count: Mapped[int] = mapped_column(Integer, server_default=text("1"))  # G5 连续被扣计数
    next_reeval_at: Mapped[str] = mapped_column(Text)  # G4 倒计时锚（channels.held_reeval_min）
    escalated_at: Mapped[str | None] = mapped_column(Text)  # 升级 @人类时刻
    resolved_by_member_id: Mapped[str | None] = mapped_column(_ULID, ForeignKey("members.id"))
    resolved_at: Mapped[str | None] = mapped_column(Text)
    resolution: Mapped[str | None] = mapped_column(_enum(HeldResolution))
    created_at: Mapped[str] = mapped_column(Text)


# ---------------------------------------------------------------- 4.10 模板（M5）


class Template(Base):
    """M5（FR-7.1）：工程三角等流程资产。body = TemplateBody（nodes/edges/roles/briefing，
    契约 A §4.10，入库前 model_validate）。builtin 行（工程三角）= server 启动 upsert 维护，
    不可删改（B §11.1）。工作区级小表，查询恒按 workspace_id 全量拉——零额外索引。"""

    __tablename__ = "templates"

    id: Mapped[str] = mapped_column(_ULID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(_ULID, ForeignKey("workspaces.id"))
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, server_default=text("''"))
    body: Mapped[dict] = mapped_column(JSON)  # TemplateBody（A v1.0.6，contracts 收紧）
    # builtin：契约 A §4.10 INTEGER 0（TemplateRow.builtin 读面为 bool，0/1 天然互转）。
    builtin: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    created_by_member_id: Mapped[str] = mapped_column(_ULID, ForeignKey("members.id"))
    created_at: Mapped[str] = mapped_column(Text)


# ---------------------------------------------------------------- 4.2 会话面（M5）


class ChannelNotificationSetting(Base):
    """M5（FR-4.7）：每频道通知设置。**可变表**——mode 会 UPDATE（PUT upsert）。
    复合 PK (channel_id, member_id) 即查询键，无行回默认 mode='all'（服务层派生）。"""

    __tablename__ = "channel_notification_settings"
    __table_args__ = (PrimaryKeyConstraint("channel_id", "member_id"),)

    channel_id: Mapped[str] = mapped_column(_ULID, ForeignKey("channels.id"))
    member_id: Mapped[str] = mapped_column(_ULID, ForeignKey("members.id"))
    # mode：值域单源自 contracts.NotificationMode（all/mentions/mute），_enum → TEXT+CHECK。
    mode: Mapped[str] = mapped_column(
        _enum(NotificationMode), server_default=text("'all'")
    )


# ---------------------------------------------------------------- 4.8 编排（M6b）


class Proposal(Base):
    """DecompositionProposal 与 delta 的生命周期实体（拆解设计 §3/§5/§11）。

    部分唯一索引「同 source 单一非终态提案」= UNIQUE(source_task_id) WHERE
    status NOT IN ('landed','superseded','rejected','failed')——SQLite 支持 partial index。
    """

    __tablename__ = "proposals"
    __table_args__ = (
        Index(
            "uq_proposals_active_source",
            "source_task_id",
            unique=True,
            sqlite_where=text(
                "status NOT IN ('landed','superseded','rejected','failed')"
            ),
        ),
    )

    id: Mapped[str] = mapped_column(_ULID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(_ULID, ForeignKey("workspaces.id"))
    channel_id: Mapped[str] = mapped_column(_ULID, ForeignKey("channels.id"))
    source_task_id: Mapped[str] = mapped_column(_ULID, ForeignKey("tasks.id"))
    kind: Mapped[str] = mapped_column(_enum(ProposalKind), server_default=text("'full'"))
    revision: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    status: Mapped[str] = mapped_column(
        _enum(ProposalStatus), server_default=text("'drafting'")
    )
    body: Mapped[Any] = mapped_column(JSON)
    proposal_hash: Mapped[str] = mapped_column(Text)
    base_hash: Mapped[str | None] = mapped_column(Text)  # delta 的基线指纹（F9）
    landed_hash: Mapped[str | None] = mapped_column(Text)
    adjustments: Mapped[Any] = mapped_column(JSON, server_default=text("'[]'"))
    repair_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    proposed_by_member_id: Mapped[str] = mapped_column(_ULID, ForeignKey("members.id"))
    created_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)


class SummaryRun(Base):
    """M8（O8 汇总协调状态，契约 A v1.0.12 §6.4）：汇总任务的循环护栏计数。**可变表**——
    round/stall/replan 三计数走条件 UPDATE CAS 推进，blocked_at 置位/清空。行创建 = 汇总节点
    gating 首次解除（lazy）。task_id 为 PK（与汇总任务 1:1）。"""

    __tablename__ = "summary_runs"

    task_id: Mapped[str] = mapped_column(_ULID, ForeignKey("tasks.id"), primary_key=True)
    canvas_id: Mapped[str] = mapped_column(_ULID, ForeignKey("canvases.id"))
    workspace_id: Mapped[str] = mapped_column(_ULID, ForeignKey("workspaces.id"))
    round_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    stall_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    replan_used: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    last_fingerprint: Mapped[str | None] = mapped_column(Text)  # §6.2 summary_fp
    blocked_at: Mapped[str | None] = mapped_column(Text)  # 非空 = 协调阻断中
    created_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)


# ---------------------------------------------------------------- 4.9 Project 与交付链（M6a）


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(_ULID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(_ULID, ForeignKey("workspaces.id"))
    computer_id: Mapped[str] = mapped_column(_ULID, ForeignKey("computers.id"))
    name: Mapped[str] = mapped_column(Text)
    repo_path: Mapped[str] = mapped_column(Text)
    dev_command: Mapped[str | None] = mapped_column(Text)
    deploy_command: Mapped[str | None] = mapped_column(Text)
    preview_idle_min: Mapped[int] = mapped_column(Integer, server_default=text("30"))
    worktree_keep_days: Mapped[int] = mapped_column(Integer, server_default=text("7"))
    created_at: Mapped[str] = mapped_column(Text)


class ChannelProject(Base):
    __tablename__ = "channel_projects"
    __table_args__ = (PrimaryKeyConstraint("channel_id", "project_id"),)

    channel_id: Mapped[str] = mapped_column(
        _ULID, ForeignKey("channels.id", ondelete="CASCADE")
    )
    project_id: Mapped[str] = mapped_column(
        _ULID, ForeignKey("projects.id", ondelete="CASCADE")
    )


class Worktree(Base):
    __tablename__ = "worktrees"

    id: Mapped[str] = mapped_column(_ULID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(_ULID, ForeignKey("workspaces.id"))
    project_id: Mapped[str] = mapped_column(_ULID, ForeignKey("projects.id"))
    task_id: Mapped[str] = mapped_column(_ULID, ForeignKey("tasks.id"), unique=True)
    branch: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(_enum(WorktreeStatus))
    merge_commit: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)
    merged_at: Mapped[str | None] = mapped_column(Text)
    cleaned_at: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------- 4.9 预览会话（M7a）

# 「活跃预览」谓词集 = 契约 PreviewStatus 的非终态子集（starting/running）。部分唯一索引
# 「每任务至多一活跃预览」的谓词、以及 K3 检索/回收调度均引此单一常量，杜绝 CR-10 同型
# 「索引谓词与状态集双源漂移」；与 contracts.enums.PreviewStatus 的一致性由
# test_alembic_upgrade::test_preview_active_statuses_align_with_contract 钉死。
PREVIEW_ACTIVE_STATUSES: tuple[str, ...] = ("starting", "running")
_PREVIEW_ACTIVE_LITERALS = ", ".join(f"'{status}'" for status in PREVIEW_ACTIVE_STATUSES)
_PREVIEW_ACTIVE_WHERE = f"status IN ({_PREVIEW_ACTIVE_LITERALS})"


class PreviewSession(Base):
    """M7a（FR-11，契约 A v1.0.11 §4.9）：任务独立预览的 dev server 会话。**可变表**——
    status/port/last_active_at/recycled_at/fail_log_tail 会被 UPDATE（健康检查转 running 携
    port、心跳 touch 推进 last_active_at、回收落 recycled_at、失败落 fail_log_tail），不进
    IMMUTABLE 集。状态机边写将来必条件 UPDATE（K3 的活）；本表只以部分唯一索引兜底
    「每任务至多一活跃预览」，防并发双 POST /preview 建双活跃行。"""

    __tablename__ = "preview_sessions"
    __table_args__ = (
        # 活跃唯一（契约 A v1.0.11「活跃唯一不变量」）：同 task_id 至多一个活跃行
        # （status ∈ starting/running）——ensure+touch 幂等的 DB 兜底。终态行
        # （recycled/failed）落在 sqlite_where 外，不占唯一（可多行，历史留痕）。
        # 谓词由 _PREVIEW_ACTIVE_WHERE 单源生成（避免 held_drafts/proposals 式硬编码双源）。
        Index(
            "ix_preview_sessions_task_active",
            "task_id",
            unique=True,
            sqlite_where=text(_PREVIEW_ACTIVE_WHERE),
        ),
        # 读面/回收调度索引：hub 周期扫描 idle 预览按 status 过滤（对齐 ix_held_drafts_status）。
        Index("ix_preview_sessions_status", "status"),
    )

    id: Mapped[str] = mapped_column(_ULID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(_ULID, ForeignKey("workspaces.id"))
    task_id: Mapped[str] = mapped_column(_ULID, ForeignKey("tasks.id"))
    worktree_id: Mapped[str] = mapped_column(_ULID, ForeignKey("worktrees.id"))
    port: Mapped[int | None] = mapped_column(Integer)  # starting 期未知，转 running 携端口
    status: Mapped[str] = mapped_column(_enum(PreviewStatus))
    # 失败日志尾 ≤2KB（交互 §12 数据源；契约 A v1.0.11 增列 / D preview.status.log_tail）
    fail_log_tail: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[str] = mapped_column(Text)
    last_active_at: Mapped[str | None] = mapped_column(Text)  # 心跳 touch 推进（B §13.1）
    recycled_at: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------- 4.9 部署（M7b）

# 「活跃部署」谓词集 = 契约 DeploymentStatus 的非终态子集（queued/running）。部分唯一索引
# 「每 project 至多一活跃部署」的谓词、以及 K4 建行 409 判定/对账 #10 均引此单一常量，杜绝
# CR-10 同型「索引谓词与状态集双源漂移」；与 contracts.enums.DeploymentStatus 的一致性由
# test_alembic_upgrade::test_deployment_active_statuses_align_with_contract 钉死。
DEPLOYMENT_ACTIVE_STATUSES: tuple[str, ...] = ("queued", "running")
_DEPLOYMENT_ACTIVE_LITERALS = ", ".join(
    f"'{status}'" for status in DEPLOYMENT_ACTIVE_STATUSES
)
_DEPLOYMENT_ACTIVE_WHERE = f"status IN ({_DEPLOYMENT_ACTIVE_LITERALS})"


class Deployment(Base):
    """M7b（FR-12，契约 A v1.5 §4.9 / B §13.2）：一次部署命令的执行留痕。**可变表**——
    status/exit_code/url/token_summary/log_path/started_at/finished_at 会被 UPDATE（daemon
    上报 deploy.log 转 running 携 started_at、deploy.finished 落终态携 exit_code/url），不进
    IMMUTABLE 集。状态机边写一律条件 UPDATE（K4 的活；铁律 2 CAS 纪律）；本表以部分唯一索引
    兜底「每 project 至多一活跃部署」，防并发双 POST 建双活跃行（409 DEPLOY_IN_PROGRESS）。

    注：DeploymentRow/Public 无 created_at，但建行需排序 + 新账区间上界（本次 created_at）——
    加 created_at 内部列（表内部用；序列化 deployment_public 不吐，只吐契约字段）。
    """

    __tablename__ = "deployments"
    __table_args__ = (
        # 活跃唯一（B §13.2「单一非终态不变量」）：同 project_id 至多一个活跃行
        # （status ∈ queued/running）——建行 409 的 DB 兜底。终态行（success/failed）落在
        # sqlite_where 外，不占唯一（可多行，历史留痕）。谓词由 _DEPLOYMENT_ACTIVE_WHERE 单源生成。
        Index(
            "uq_deployments_active_project",
            "project_id",
            unique=True,
            sqlite_where=text(_DEPLOYMENT_ACTIVE_WHERE),
        ),
        # 读面/对账索引：POST 新账下界按 project + status='success' 查上一 success（对齐
        # ix_preview_sessions_status 读面索引先例）。
        Index("ix_deployments_project_status", "project_id", "status"),
    )

    id: Mapped[str] = mapped_column(_ULID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(_ULID, ForeignKey("workspaces.id"))
    project_id: Mapped[str] = mapped_column(_ULID, ForeignKey("projects.id"))
    triggered_by_member_id: Mapped[str] = mapped_column(_ULID, ForeignKey("members.id"))
    branch: Mapped[str] = mapped_column(Text)
    commit_hash: Mapped[str | None] = mapped_column(Text)
    command: Mapped[str] = mapped_column(Text)  # 触发时 project.deploy_command 快照（留痕）
    status: Mapped[str] = mapped_column(_enum(DeploymentStatus), server_default=text("'queued'"))
    exit_code: Mapped[int | None] = mapped_column(Integer)
    url: Mapped[str | None] = mapped_column(Text)  # 部署工具输出末行 URL（daemon 提取）
    # 日志落文件（契约 D §9.1）：server 收 deploy.log 落 <data_root>/deploy-logs/<id>.log 绝对路径。
    log_path: Mapped[str | None] = mapped_column(Text)
    # 新账 Σ 快照（TokenSummary）：POST 建行纯 SQL 推导落列，查询不重算（失败部署不推进区间）。
    token_summary: Mapped[dict | None] = mapped_column(JSON)
    started_at: Mapped[str | None] = mapped_column(Text)  # 首条 deploy.log 转 running 落
    finished_at: Mapped[str | None] = mapped_column(Text)  # deploy.finished 终态落
    # 表内部列（不进 Public）：建行排序 + 新账区间上界（本次 created_at）。
    created_at: Mapped[str] = mapped_column(Text)
