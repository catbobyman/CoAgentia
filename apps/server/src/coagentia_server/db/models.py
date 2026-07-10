"""SQLAlchemy 2.0 DeclarativeBase + M1 批次 17 张表（契约 A §4/§5）。

纪律（契约 A §8.1）：列名/类型逐字对齐 packages/contracts 的对应 *Row 模型
（字段名即列名，snake_case 三处一致）；枚举列 import contracts.enums 的 StrEnum，
以 SQLAlchemy Enum(values_callable=…) 存枚举 value，不重复定义字面量。

nullability 由 Mapped 类型推导（SA 2.0 惯用）：Mapped[str] → NOT NULL，
Mapped[str | None] → NULL。不可变表（messages/files/ledger_entries/diagnostic_events/
token_usage_events）的禁 UPDATE/DELETE 触发器由 Alembic 迁移 op.execute 落原生 SQL。
"""

from __future__ import annotations

from coagentia_contracts.enums import (
    AgentStatus,
    CardKind,
    ChannelKind,
    ComputerStatus,
    DecompMode,
    LandingBatchKind,
    LandingBatchStatus,
    MemberKind,
    MemberRole,
    MessageKind,
    ReminderKind,
    ReminderStatus,
    Runtime,
    UiTheme,
)
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# 五张不可变表（契约 A §1 写入纪律）——触发器在迁移里生成。
IMMUTABLE_TABLES: tuple[str, ...] = (
    "messages",
    "files",
    "ledger_entries",
    "diagnostic_events",
    "token_usage_events",
)

_ULID = String(26)


class Base(DeclarativeBase):
    pass


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


class AgentSkill(Base):
    __tablename__ = "agent_skills"
    __table_args__ = (PrimaryKeyConstraint("agent_member_id", "skill"),)

    agent_member_id: Mapped[str] = mapped_column(_ULID, ForeignKey("agents.member_id"))
    skill: Mapped[str] = mapped_column(Text)
    granted_by_member_id: Mapped[str] = mapped_column(_ULID, ForeignKey("members.id"))
    granted_at: Mapped[str] = mapped_column(Text)


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
