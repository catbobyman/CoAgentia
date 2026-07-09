"""REST API 契约（契约 B 的代码化）：错误码目录、错误形状、分页、M1 端点请求/响应模型。

M2+ 端点的请求模型随对应里程碑登记（实体 Public 形状已全量存在）；未列出的端点不要发明。
"""

from enum import StrEnum

from pydantic import JsonValue

from coagentia_contracts.entities import (
    ChannelPublic,
    ComputerPublic,
    ContractModel,
    HeldDraftPublic,
    MessagePublic,
    ReadPositionPublic,
    TaskPublic,
)
from coagentia_contracts.enums import (
    LifecycleAction,
    MemberKind,
    MemberRole,
    PresenceStatus,
    UiTheme,
)
from coagentia_contracts.ids import Ulid

API_BASE = "/api"  # 只绑定 127.0.0.1（契约 B §1 / NFR5）
PAGE_DEFAULT_LIMIT = 50
PAGE_MAX_LIMIT = 200


class ErrorCode(StrEnum):
    """错误码目录全集（契约 B §3；新增须先登记进契约文档）。"""

    VALIDATION_FAILED = "VALIDATION_FAILED"  # 422
    TASK_IN_DM = "TASK_IN_DM"  # 422（FR-5.1）
    NOT_TOP_LEVEL_MESSAGE = "NOT_TOP_LEVEL_MESSAGE"  # 422（T3）
    CLAIM_RACE = "CLAIM_RACE"  # 409（T2）
    HANDOFF_INCOMPLETE = "HANDOFF_INCOMPLETE"  # 422（T7）
    GRAPH_CYCLE = "GRAPH_CYCLE"  # 422（V9 报告格式）
    STALE_CONFIRM = "STALE_CONFIRM"  # 409（S2；响应携带最新态）
    DELTA_BASE_MISMATCH = "DELTA_BASE_MISMATCH"  # 409（F9）
    NODE_ACTIVE = "NODE_ACTIVE"  # 422（F10）
    NO_ORCHESTRATOR = "NO_ORCHESTRATOR"  # 409
    IDEMPOTENCY_MISMATCH = "IDEMPOTENCY_MISMATCH"  # 409
    NAME_TAKEN = "NAME_TAKEN"  # 409
    CHANNEL_ARCHIVED = "CHANNEL_ARCHIVED"  # 409（FR-1.3）
    COMPUTER_HAS_AGENTS = "COMPUTER_HAS_AGENTS"  # 409（FR-2.7）
    WORKSPACE_EXISTS = "WORKSPACE_EXISTS"  # 409
    DEPLOY_IN_PROGRESS = "DEPLOY_IN_PROGRESS"  # 409（不排队）
    DAEMON_OFFLINE = "DAEMON_OFFLINE"  # 503（含 query 超时，契约 D §3）
    FILE_TOO_LARGE = "FILE_TOO_LARGE"  # 413
    PERMISSION_DENIED = "PERMISSION_DENIED"  # 403（rule 注明 R2/R3/C3/admin 等）
    NOT_FOUND = "NOT_FOUND"  # 404


class ErrorBody(ContractModel):
    """`code` 机器分支、`message` 可直接进 toast、`rule` 溯源 PRD 规则号（契约 B §1）。"""

    code: ErrorCode
    message: str
    rule: str | None = None
    details: JsonValue | None = None


class ErrorResponse(ContractModel):
    error: ErrorBody


class Page[T](ContractModel):
    """游标分页：?after=（正序）/ ?before=（倒序回翻）。"""

    items: list[T]
    next_cursor: str | None = None


# ------------------------------------------------------------ 4.1 工作区


class WorkspaceCreate(ContractModel):
    name: str
    slug: str


class WorkspacePatch(ContractModel):
    name: str | None = None
    slug: str | None = None
    attachment_max_mb: int | None = None
    onboarding_greeting: bool | None = None
    ui_theme: UiTheme | None = None
    notif_desktop: bool | None = None
    notif_sound: bool | None = None
    setup_state: dict[str, JsonValue] | None = None


# ------------------------------------------------------------ 4.2 机器


class ComputerCreate(ContractModel):
    name: str


class ComputerCreated(ContractModel):
    """api_key 明文仅此一次（库中只存哈希，契约 A）。"""

    computer: ComputerPublic
    api_key: str
    command_line: str  # uvx coagentia-daemon --server-url <url> --api-key <key>


class ComputerPatch(ContractModel):
    name: str


# ------------------------------------------------------------ 4.3 成员、Agent 与生命周期


class MemberPatch(ContractModel):
    role: MemberRole  # admin 仅可动 Member 级、owner 任意；R1 兜底


class PresenceEntry(ContractModel):
    """GET /presence 合并视图：presence 不完全入库（契约 B §4.3 / 契约 D §2 级联裁决）。"""

    member_id: Ulid
    kind: MemberKind
    status: PresenceStatus
    busy_detail: str | None = None


class PresenceSnapshot(ContractModel):
    items: list[PresenceEntry]


class AgentCreate(ContractModel):
    computer_id: Ulid
    name: str
    runtime: str
    model: str
    description: str = ""
    role_template_key: str | None = None  # M6 Orchestrator 预填位


class AgentPatch(ContractModel):
    """runtime/model/description 修改 = 下次启动生效（FR-3.5）；R3 门。"""

    runtime: str | None = None
    model: str | None = None
    description: str | None = None


class LifecycleRequest(ContractModel):
    action: LifecycleAction  # R2 门：Agent 主体 → 403


class SkillsPut(ContractModel):
    skills: list[str]  # 全量替换制（R6），授予留痕


# ------------------------------------------------------------ 4.4 提醒


class ReminderCreate(ContractModel):
    """Agent 主体自设（FR-3.9）；recurring 无 loop_contract → 422（D1-L2）。"""

    kind: str
    cadence: str
    anchor_channel_id: Ulid
    anchor_message_id: Ulid | None = None
    anchor_task_id: Ulid | None = None
    loop_contract_id: Ulid | None = None


# ------------------------------------------------------------ 4.5 频道与 DM


class ChannelsSnapshot(ContractModel):
    """GET /channels：全量频道 + **自身** read-position 附带（契约 B §4.5/§6）。"""

    items: list[ChannelPublic]
    read_positions: list[ReadPositionPublic]


class ChannelCreate(ContractModel):
    name: str
    description: str = ""
    is_private: bool = False
    member_ids: list[Ulid] = []  # 人/Agent 同列勾选


class ChannelPatch(ContractModel):
    description: str | None = None
    is_private: bool | None = None
    remind_todo_h: int | None = None
    remind_inprog_h: int | None = None
    remind_review_h: int | None = None
    remind_escalation: bool | None = None
    held_reeval_min: int | None = None
    held_escalate_n: int | None = None
    decomp_mode: str | None = None
    decomp_node_limit: int | None = None
    orch_escalation: bool | None = None


class ChannelMemberAdd(ContractModel):
    member_id: Ulid


class DmCreate(ContractModel):
    member_id: Ulid  # dm_key 去重，幂等返回既有或新建


# ------------------------------------------------------------ 4.6 消息、文件与已读


class AsTask(ContractModel):
    title: str | None = None


class MessageCreate(ContractModel):
    body: str
    thread_root_id: Ulid | None = None
    file_ids: list[Ulid] = []
    as_task: AsTask | None = None  # DM 内 → TASK_IN_DM


class MessageCreated(ContractModel):
    """as_task 成功时 task 非空（原子）。"""

    message: MessagePublic
    task: TaskPublic | None = None


class MessageHeld(ContractModel):
    """Agent 主体发送被 freshness 扣住 → 202（G1；人类发送永不 held）。"""

    held_draft: HeldDraftPublic


class ReadPositionPut(ContractModel):
    last_read_message_id: Ulid


# ---------------------------------------------------- M1 端点清单（mock 一致性测试的基准）

ENDPOINTS_M1: tuple[tuple[str, str], ...] = (
    ("POST", "/workspace"),
    ("GET", "/workspace"),
    ("PATCH", "/workspace"),
    ("GET", "/computers"),
    ("POST", "/computers"),
    ("PATCH", "/computers/{computer_id}"),
    ("DELETE", "/computers/{computer_id}"),
    ("GET", "/members"),
    ("PATCH", "/members/{member_id}"),
    ("GET", "/presence"),
    ("POST", "/agents"),
    ("GET", "/agents/{member_id}"),
    ("PATCH", "/agents/{member_id}"),
    ("DELETE", "/agents/{member_id}"),
    ("POST", "/agents/{member_id}/lifecycle"),
    ("GET", "/agents/{member_id}/home/tree"),
    ("GET", "/agents/{member_id}/home/file"),
    ("GET", "/agents/{member_id}/skills"),
    ("PUT", "/agents/{member_id}/skills"),
    ("GET", "/agents/{member_id}/diagnostics"),
    ("GET", "/agents/{member_id}/diagnostics/export"),
    ("POST", "/reminders"),
    ("GET", "/agents/{member_id}/reminders"),
    ("DELETE", "/reminders/{reminder_id}"),
    ("GET", "/channels"),
    ("POST", "/channels"),
    ("PATCH", "/channels/{channel_id}"),
    ("POST", "/channels/{channel_id}/archive"),
    ("POST", "/channels/{channel_id}/unarchive"),
    ("DELETE", "/channels/{channel_id}"),
    ("POST", "/channels/{channel_id}/members"),
    ("DELETE", "/channels/{channel_id}/members/{member_id}"),
    ("POST", "/dms"),
    ("GET", "/channels/{channel_id}/messages"),
    ("GET", "/messages/{message_id}/thread"),
    ("POST", "/channels/{channel_id}/messages"),
    ("POST", "/files"),
    ("GET", "/files/{file_id}/content"),
    ("PUT", "/channels/{channel_id}/read-position"),
)
