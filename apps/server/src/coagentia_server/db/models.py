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
    ActivityKind,
    AgentStatus,
    CanvasNodeKind,
    CardKind,
    ChannelKind,
    ComputerStatus,
    ContractKind,
    DecompMode,
    LandingBatchKind,
    LandingBatchStatus,
    MemberKind,
    MemberRole,
    MessageKind,
    ReminderKind,
    ReminderStatus,
    Runtime,
    SystemAction,
    SystemNodeStatus,
    TaskEventKind,
    TaskLevel,
    TaskStatus,
    UiTheme,
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
