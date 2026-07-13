"""全部枚举的唯一定义（契约 A §1：枚举值唯一定义在 contracts 包）。

SQLAlchemy 模型必须 import 本模块，不得重复定义字面量（契约 A §8.1）。
"""

from enum import StrEnum


class MemberKind(StrEnum):
    HUMAN = "human"
    AGENT = "agent"


class MemberRole(StrEnum):
    MEMBER = "member"
    ADMIN = "admin"
    OWNER = "owner"  # R1: kind='agent' 永不 owner（服务层 + DB CHECK 兜底）


class Runtime(StrEnum):
    CLAUDE_CODE = "claude_code"
    CODEX = "codex"  # M5（契约 E §9 扩展位）


class AgentStatus(StrEnum):
    """PRD §4.4 五态；持久化"最后已知态"（契约 A agents 表 / 契约 D §2 级联裁决）。"""

    STARTING = "starting"
    IDLE = "idle"
    BUSY = "busy"
    ERROR = "error"
    OFFLINE = "offline"


class PresenceStatus(StrEnum):
    """`GET /presence` 与 `presence.changed` 的合并视图值域：人类 online/offline，Agent 五态。"""

    ONLINE = "online"
    OFFLINE = "offline"
    STARTING = "starting"
    IDLE = "idle"
    BUSY = "busy"
    ERROR = "error"


class ComputerStatus(StrEnum):
    CONNECTED = "connected"
    OFFLINE = "offline"


class ChannelKind(StrEnum):
    CHANNEL = "channel"
    DM = "dm"


class MessageKind(StrEnum):
    USER = "user"
    SYSTEM = "system"


class CardKind(StrEnum):
    """结构化卡片锚点消息（契约 A messages.card_kind；卡片 = 不可变锚点 + 实体状态走 WS）。"""

    PROPOSAL = "proposal"
    HELD_DRAFT = "held_draft"
    DEPLOYMENT = "deployment"
    FAIL_CLOSED = "fail_closed"
    HANDOFF_DELIVERY = "handoff_delivery"
    MERGE_CONFLICT = "merge_conflict"


class TaskStatus(StrEnum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    DONE = "done"
    CLOSED = "closed"


class TaskLevel(StrEnum):
    L1 = "l1"
    L2 = "l2"  # 升格只允许 l1→l2（D1）


class TaskEventKind(StrEnum):
    STATUS_CHANGE = "status_change"
    CLAIM = "claim"
    UNCLAIM = "unclaim"
    ASSIGN = "assign"  # v1.0.2：B §4.7 assign 端点留痕载体（owner_member_id=新值或 NULL）
    FORCE_START = "force_start"
    REMINDER_SENT = "reminder_sent"
    ESCALATED = "escalated"


class ContractKind(StrEnum):
    TASK_PLAN = "task_plan"
    TASK_HANDOFF = "task_handoff"
    LOOP_CONTRACT = "loop_contract"


class VerifyBy(StrEnum):
    """TaskPlan.acceptance_criteria[].verify_by（PRD §4.3 v1）。"""

    COMMAND = "command"
    INSPECT = "inspect"
    MANUAL = "manual"


class DeliverableKind(StrEnum):
    """TaskHandoff.deliverables[].kind（PRD §4.3 v1）。"""

    FILE = "file"
    DIR = "dir"
    URL = "url"
    ARTIFACT = "artifact"


class EvidenceType(StrEnum):
    """TaskHandoff.evidence[].type（PRD §4.3 v1）。"""

    TEST = "test"
    COMMAND = "command"
    SCREENSHOT = "screenshot"
    LOG = "log"


class ReviewVerdict(StrEnum):
    """TaskHandoff 的结构化评审结论（契约 B §12.10）。"""

    PASS = "pass"
    DOWNGRADE = "downgrade"
    SEND_BACK = "send_back"
    NEEDS_HUMAN = "needs_human"


class CanvasNodeKind(StrEnum):
    AGENT = "agent"
    SYSTEM = "system"


class SystemAction(StrEnum):
    MERGE = "merge"
    CHECK = "check"


class SystemNodeStatus(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class HeldDraftStatus(StrEnum):
    HELD = "held"
    RELEASED = "released"
    DISCARDED = "discarded"
    REEVALUATING = "reevaluating"
    RESOLVED = "resolved"


class HeldResolution(StrEnum):
    RELEASED = "released"
    DISCARDED = "discarded"
    REEVALUATED = "reevaluated"


class ReminderKind(StrEnum):
    ONCE = "once"
    RECURRING = "recurring"  # 必先 LoopContract（D1-L2）


class ReminderStatus(StrEnum):
    ACTIVE = "active"
    CANCELLED = "cancelled"
    DONE = "done"


class ActivityKind(StrEnum):
    MENTION = "mention"
    DM = "dm"  # v1.0.2：DM 新消息给对方人类成员（FR-4.7 必达；生成规则 B §9.7）
    SILENCE_ESCALATION = "silence_escalation"
    HELD_ESCALATION = "held_escalation"
    FAIL_CLOSED = "fail_closed"
    SYSTEM = "system"


class ActivityFilter(StrEnum):
    """GET /activity?filter=（B §9.7.3）。"""

    ALL = "all"
    UNREAD = "unread"
    MENTIONS = "mentions"


class SearchKind(StrEnum):
    """GET /search?kind=（B §9.6.1；缺省全搜）。"""

    MESSAGE = "message"
    TASK = "task"


class LandingBatchKind(StrEnum):
    DECOMP = "decomp"
    TMPL = "tmpl"
    DELTA = "delta"


class LandingBatchStatus(StrEnum):
    RUNNING = "running"
    DONE = "done"  # done_at 写入 = 批次 :done 事实源（S4）
    FAIL_CLOSED = "fail_closed"


class ProposalKind(StrEnum):
    FULL = "full"
    DELTA = "delta"


class ProposalStatus(StrEnum):
    DRAFTING = "drafting"
    VALIDATING = "validating"
    REPAIRING = "repairing"
    AWAITING_CONFIRM = "awaiting_confirm"
    LANDING = "landing"
    LANDED = "landed"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"
    FAILED = "failed"


class WorktreeStatus(StrEnum):
    ACTIVE = "active"
    MERGED = "merged"
    CONFLICTED = "conflicted"
    CLEANED = "cleaned"


class PreviewStatus(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    RECYCLED = "recycled"
    FAILED = "failed"


class DeploymentStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class UsageLevel(StrEnum):
    """`GET /usage?level=`（契约 B §13.4）三层聚合维度值域（?filter=/?kind= 枚举先例）。"""

    TASK = "task"
    AGENT = "agent"
    CANVAS = "canvas"


class NotificationMode(StrEnum):
    ALL = "all"
    MENTIONS = "mentions"
    MUTE = "mute"


class UiTheme(StrEnum):
    DARK = "dark"
    LIGHT = "light"
    SYSTEM = "system"


class DecompMode(StrEnum):
    DRAFT = "draft"
    DIRECT = "direct"  # O5 直落


class LifecycleAction(StrEnum):
    """`POST /agents/{id}/lifecycle`（契约 B §4.3；三档重置枚举只定义这一次）。"""

    START = "start"
    STOP = "stop"
    RESTART = "restart"
    RESET_SESSION = "reset_session"
    RESET_FULL = "reset_full"


class InjectKind(StrEnum):
    """S1 直投的来源域（契约 D §5.2）。"""

    REPAIR = "repair"
    GUARD_FEEDBACK = "guard_feedback"
    CONTRACT_DRAFT_REQUEST = "contract_draft_request"
    SYSTEM = "system"


class WakeReason(StrEnum):
    """四触发器（PRD §4.5 / 契约 D §5.1）。"""

    CHANNEL_MESSAGE = "channel_message"
    MENTION = "mention"
    REMINDER = "reminder"
    CANVAS_ACTIVATION = "canvas_activation"
