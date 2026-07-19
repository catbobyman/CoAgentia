"""浏览器 WS 事件协议（契约 C v1.1 的代码化）：信封、事件目录、上行消息、payload 形状。

铁律（契约 C §1）：事件载状态（payload 带完整 Public 形状），客户端整体替换、重复应用无害；
新事件必须先登记进本目录（契约 C §6/§7 与元素库白名单同一纪律）。

契约 C v1.1（DEDAG 批，2026-07-18）：画布编排随 DAG 退役——canvas.*（节点/边/布局/基线）、
draft.* / delta.* / proposal.*（提案生命周期）、landing.*（落地批）事件族整体移出目录；
message / task / agent / preview / deploy / worktree / presence 等在用事件不变。
"""

from enum import StrEnum
from typing import Literal

from pydantic import JsonValue

from coagentia_contracts.entities import (
    ActivityItemPublic,
    AgentPublic,
    ChannelPublic,
    ComputerPublic,
    ContractModel,
    DeploymentPublic,
    HeldDraftPublic,
    MemberPublic,
    MessagePublic,
    PreviewSessionPublic,
    ReminderPublic,
    TaskContractPublic,
    TaskPublic,
    WorkspacePublic,
    WorktreePublic,
)
from coagentia_contracts.enums import (
    MemberKind,
    PresenceStatus,
    TaskEventKind,
    TaskStatus,
)
from coagentia_contracts.ids import TimestampZ, Ulid

PROTOCOL_V = 1
HEARTBEAT_SEC = 25  # 契约 C §2；daemon WS 同参（契约 D §2）


class EventType(StrEnum):
    """权威事件目录 = 契约 C §6/§7（+§8 的 diagnostic.appended 订阅流）。"""

    # 6.1 系统与工作区（M1）
    SYS_HELLO = "sys.hello"
    SYS_PONG = "sys.pong"
    WORKSPACE_UPDATED = "workspace.updated"
    # 6.2 成员与 Agent（M1）
    PRESENCE_CHANGED = "presence.changed"
    AGENT_ACTIVITY = "agent.activity"
    MEMBER_CREATED = "member.created"
    MEMBER_UPDATED = "member.updated"
    MEMBER_REMOVED = "member.removed"
    AGENT_UPDATED = "agent.updated"
    COMPUTER_CONNECTED = "computer.connected"
    COMPUTER_DISCONNECTED = "computer.disconnected"
    COMPUTER_UPDATED = "computer.updated"
    # 6.3 频道与消息（M1）
    CHANNEL_CREATED = "channel.created"
    CHANNEL_UPDATED = "channel.updated"
    CHANNEL_DELETED = "channel.deleted"
    CHANNEL_MEMBER_ADDED = "channel.member_added"
    CHANNEL_MEMBER_REMOVED = "channel.member_removed"
    MESSAGE_CREATED = "message.created"  # 唯一的消息事件——消息不可变
    READ_UPDATED = "read.updated"
    # 6.4 任务与契约（M2/M3）
    TASK_CREATED = "task.created"
    TASK_UPDATED = "task.updated"
    TASK_CONTRACT_CREATED = "task_contract.created"
    TASK_CONTRACT_UPDATED = "task_contract.updated"
    ACTIVITY_CREATED = "activity.created"
    ACTIVITY_DONE = "activity.done"
    TOKEN_USAGE_REPORTED = "token_usage.reported"
    # 6.5 画布事件族已随 DEDAG 退役（契约 C v1.1）
    # 6.6 护栏与提醒（M4/M1）
    HELD_DRAFT_CREATED = "held_draft.created"
    HELD_DRAFT_UPDATED = "held_draft.updated"
    REMINDER_CREATED = "reminder.created"
    REMINDER_UPDATED = "reminder.updated"
    # 6.7 交付链路（M6–M7）
    WORKTREE_UPDATED = "worktree.updated"
    PREVIEW_UPDATED = "preview.updated"
    DEPLOYMENT_CREATED = "deployment.created"
    DEPLOYMENT_UPDATED = "deployment.updated"
    DEPLOYMENT_LOG = "deployment.log"  # 订阅制（§8）
    # §7 M6 提案/落地事件族（draft.*/delta.*/landing.*/proposal.*）已随 DEDAG 退役（契约 C v1.1）
    # §8 订阅制诊断流（M1，P6 实时尾随）
    DIAGNOSTIC_APPENDED = "diagnostic.appended"


class Envelope(ContractModel):
    """信封四要素：类型 / 作用域 / 序号 / 幂等键（契约 C §3）。"""

    v: int = PROTOCOL_V
    seq: int  # 连接内单调递增；空洞 = 致命 → 断开重连重同步
    type: EventType
    workspace_id: Ulid
    channel_id: Ulid | None = None  # NULL = 工作区级事件
    key: str  # 幂等键 <实体>:<id>:<单调标记>；M6 账本派生事件复用 op_id
    at: TimestampZ
    data: JsonValue  # 形状 = EVENT_PAYLOADS[type]


# ------------------------------------------------------------ 上行消息（全集仅三种，契约 C §5）


class PingMsg(ContractModel):
    type: Literal["ping"]


class SubDiagnosticMsg(ContractModel):
    type: Literal["sub", "unsub"]
    stream: Literal["diagnostic"]
    agent_member_id: Ulid


class SubDeployLogMsg(ContractModel):
    type: Literal["sub", "unsub"]
    stream: Literal["deploy_log"]
    deployment_id: Ulid


# ------------------------------------------------------------ payload 形状（契约 C §6/§7 data 列）


class SysHelloData(ContractModel):
    protocol_v: int
    server_version: str
    workspace_id: Ulid
    conn_id: str
    heartbeat_sec: int


class SysPongData(ContractModel):
    pass


class WorkspaceUpdatedData(ContractModel):
    workspace: WorkspacePublic


class PresenceChangedData(ContractModel):
    member_id: Ulid
    kind: MemberKind
    status: PresenceStatus


class AgentActivityData(ContractModel):
    """瞬态：每 Agent ≥500ms 节流、只发最新，不入库；detail 值域 = constants.ACTIVITY_PHRASES。"""

    member_id: Ulid
    detail: str


class MemberData(ContractModel):
    member: MemberPublic


class AgentUpdatedData(ContractModel):
    agent: AgentPublic


class ComputerData(ContractModel):
    computer: ComputerPublic


class ChannelData(ContractModel):
    channel: ChannelPublic


class ChannelMembershipData(ContractModel):
    channel_id: Ulid
    member_id: Ulid


class MessageCreatedData(ContractModel):
    message: MessagePublic


class ReadUpdatedData(ContractModel):
    channel_id: Ulid
    member_id: Ulid
    last_read_message_id: Ulid


class TaskCreatedData(ContractModel):
    task: TaskPublic


class TaskChange(ContractModel):
    kind: TaskEventKind
    from_status: TaskStatus | None = None
    to_status: TaskStatus | None = None
    actor_member_id: Ulid | None = None


class TaskUpdatedData(ContractModel):
    task: TaskPublic
    # PATCH /tasks/{id}（元数据改）无对应 TaskEventKind → change 可空（契约 C §6.4 编辑性放宽，
    # 向后兼容；claim/unclaim/assign/status 仍携带完整 change）。
    change: TaskChange | None = None


class TaskContractData(ContractModel):
    contract: TaskContractPublic


class ActivityCreatedData(ContractModel):
    item: ActivityItemPublic


class ActivityDoneData(ContractModel):
    item_id: Ulid


class TokenTotals(ContractModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class TokenUsageReportedData(ContractModel):
    agent_member_id: Ulid
    task_id: Ulid | None = None
    totals: TokenTotals


class HeldDraftData(ContractModel):
    draft: HeldDraftPublic


class ReminderData(ContractModel):
    reminder: ReminderPublic


class WorktreeUpdatedData(ContractModel):
    worktree: WorktreePublic


class PreviewUpdatedData(ContractModel):
    preview: PreviewSessionPublic


class DeploymentData(ContractModel):
    deployment: DeploymentPublic


class DeploymentLogData(ContractModel):
    deployment_id: Ulid
    chunk_seq: int
    lines: list[str]


class DiagnosticAppendedData(ContractModel):
    """订阅制流（契约 C §8）：50 条/批上限；历史翻页走 REST。"""

    agent_member_id: Ulid
    events: list[JsonValue]  # DiagnosticEventPublic 列表（避免循环 import 用 JsonValue 承载）


EVENT_PAYLOADS: dict[EventType, type[ContractModel]] = {
    EventType.SYS_HELLO: SysHelloData,
    EventType.SYS_PONG: SysPongData,
    EventType.WORKSPACE_UPDATED: WorkspaceUpdatedData,
    EventType.PRESENCE_CHANGED: PresenceChangedData,
    EventType.AGENT_ACTIVITY: AgentActivityData,
    EventType.MEMBER_CREATED: MemberData,
    EventType.MEMBER_UPDATED: MemberData,
    EventType.MEMBER_REMOVED: MemberData,
    EventType.AGENT_UPDATED: AgentUpdatedData,
    EventType.COMPUTER_CONNECTED: ComputerData,
    EventType.COMPUTER_DISCONNECTED: ComputerData,
    EventType.COMPUTER_UPDATED: ComputerData,
    EventType.CHANNEL_CREATED: ChannelData,
    EventType.CHANNEL_UPDATED: ChannelData,
    EventType.CHANNEL_DELETED: ChannelData,
    EventType.CHANNEL_MEMBER_ADDED: ChannelMembershipData,
    EventType.CHANNEL_MEMBER_REMOVED: ChannelMembershipData,
    EventType.MESSAGE_CREATED: MessageCreatedData,
    EventType.READ_UPDATED: ReadUpdatedData,
    EventType.TASK_CREATED: TaskCreatedData,
    EventType.TASK_UPDATED: TaskUpdatedData,
    EventType.TASK_CONTRACT_CREATED: TaskContractData,
    EventType.TASK_CONTRACT_UPDATED: TaskContractData,
    EventType.ACTIVITY_CREATED: ActivityCreatedData,
    EventType.ACTIVITY_DONE: ActivityDoneData,
    EventType.TOKEN_USAGE_REPORTED: TokenUsageReportedData,
    EventType.HELD_DRAFT_CREATED: HeldDraftData,
    EventType.HELD_DRAFT_UPDATED: HeldDraftData,
    EventType.REMINDER_CREATED: ReminderData,
    EventType.REMINDER_UPDATED: ReminderData,
    EventType.WORKTREE_UPDATED: WorktreeUpdatedData,
    EventType.PREVIEW_UPDATED: PreviewUpdatedData,
    EventType.DEPLOYMENT_CREATED: DeploymentData,
    EventType.DEPLOYMENT_UPDATED: DeploymentData,
    EventType.DEPLOYMENT_LOG: DeploymentLogData,
    EventType.DIAGNOSTIC_APPENDED: DiagnosticAppendedData,
}
