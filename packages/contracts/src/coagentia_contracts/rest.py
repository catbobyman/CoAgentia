"""REST API 契约（契约 B 的代码化）：错误码目录、错误形状、分页、M1 端点请求/响应模型。

M2+ 端点的请求模型随对应里程碑登记（实体 Public 形状已全量存在）；未列出的端点不要发明。
"""

from enum import StrEnum
from typing import Literal

from pydantic import JsonValue, field_validator

from coagentia_contracts.entities import (
    CanvasEdgePublic,
    CanvasNodePublic,
    CanvasPublic,
    ChannelNotificationSettingPublic,
    ChannelPublic,
    ComputerPublic,
    ContractModel,
    HeldDraftPublic,
    LandingBatchPublic,
    MemberPublic,
    MessagePublic,
    ReadPositionPublic,
    TaskContractPublic,
    TaskPlanBody,
    TaskPublic,
)
from coagentia_contracts.enums import (
    CanvasNodeKind,
    ContractKind,
    DeliverableKind,
    EvidenceType,
    LifecycleAction,
    MemberKind,
    MemberRole,
    NotificationMode,
    PresenceStatus,
    SystemAction,
    TaskLevel,
    TaskStatus,
    UiTheme,
)
from coagentia_contracts.ids import Sha256Hex, Ulid

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
    TASK_TRANSITION_INVALID = "TASK_TRANSITION_INVALID"  # 422（§9.1；details {from,to,allowed}）
    GRAPH_CYCLE = "GRAPH_CYCLE"  # 422（V9 报告格式）
    STALE_CONFIRM = "STALE_CONFIRM"  # 409（S2；响应携带最新态）
    DELTA_BASE_MISMATCH = "DELTA_BASE_MISMATCH"  # 409（F9）
    NODE_ACTIVE = "NODE_ACTIVE"  # 422（F10）
    NO_ORCHESTRATOR = "NO_ORCHESTRATOR"  # 409
    IDEMPOTENCY_MISMATCH = "IDEMPOTENCY_MISMATCH"  # 409
    NAME_TAKEN = "NAME_TAKEN"  # 409
    CHANNEL_NOT_EMPTY = "CHANNEL_NOT_EMPTY"  # 409（含消息的频道不可硬删——消息不可变，改用归档）
    CHANNEL_ARCHIVED = "CHANNEL_ARCHIVED"  # 409（FR-1.3）
    COMPUTER_HAS_AGENTS = "COMPUTER_HAS_AGENTS"  # 409（FR-2.7）
    WORKSPACE_EXISTS = "WORKSPACE_EXISTS"  # 409
    DEPLOY_IN_PROGRESS = "DEPLOY_IN_PROGRESS"  # 409（不排队）
    DAEMON_OFFLINE = "DAEMON_OFFLINE"  # 503（含 query 超时，契约 D §3）
    FILE_TOO_LARGE = "FILE_TOO_LARGE"  # 413
    HELD_DRAFT_RESOLVED = "HELD_DRAFT_RESOLVED"  # 409（M4；终态 held 三键干预，details 携当前态）
    NOTIF_IN_DM = "NOTIF_IN_DM"  # 422（M5；DM 无通知设置面，DM 必达裁决，命名对齐 TASK_IN_DM）
    TEMPLATE_CANVAS_NOT_READY = "TEMPLATE_CANVAS_NOT_READY"  # 409（M5；画布无正式节点/存草稿层）
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
    """Agent 主体自设（FR-3.9）；recurring 无 loop_contract → 422（D1-L2）。

    M4 起（v1.2）：可携内联 `loop_contract`——recurring 必填（缺 → 422），server `model_validate`
    后同一事务建 task_contracts（kind=loop_contract、reminder_id 挂接，契约 A §4.3 XOR）并回填
    reminders.loop_contract_id；once 携带 loop_contract → 422（B §4.4/§10.6）。契约在同事务才
    创建，故请求侧不接受 loop_contract_id（那是存储列，非请求字段）。前向引用 LoopContractBody
    （定义序在后，同 entities.MessagePublic.files 先例），文件末尾 model_rebuild() 补全。

    cadence（B §10.6/§11.5）：once = ISO-8601 时刻；recurring = interval（ISO-8601 duration，如
    `PT1H`）或 **cron 五段式**（`分 时 日 月 周`，服务器本地时区，无秒/年/@keyword——M5 v1.3 扩），
    且创建时须与 `loop_contract.cadence` 一致（server 校验，不一致 → 422）。cadence 在 contracts 侧
    是纯 str（无语义校验器）：cron 值域解析/塌缩式重排的判定归 H4 server 侧单点（纪律 7）。
    """

    kind: str
    cadence: str
    anchor_channel_id: Ulid
    anchor_message_id: Ulid | None = None
    anchor_task_id: Ulid | None = None
    loop_contract: "LoopContractBody | None" = None


# ------------------------------------------------------------ 4.5 频道与 DM


class ChannelsSnapshot(ContractModel):
    """GET /channels：全量频道 + 自身 read-position + 本人非默认通知设置（B §4.5/§6/§11.4）。

    v1.3：扩第三字段 `notification_settings`（本人全部**非默认**行，前端渲染徽标源；PUT 后
    本地更新，零新增 WS 事件）。冷态/全默认 → []（H0 字段就位，H3 填充）。
    """

    items: list[ChannelPublic]
    read_positions: list[ReadPositionPublic]
    notification_settings: list[ChannelNotificationSettingPublic] = []


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

    @field_validator("body")
    @classmethod
    def body_must_be_utf8(cls, value: str) -> str:
        """拒绝 JSON 可表达、但 UTF-8/SQLite 无法编码的未配对 surrogate。"""
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("消息正文包含无效 Unicode 字符") from exc
        return value


class MessageCreated(ContractModel):
    """as_task 成功时 task 非空（原子）。"""

    message: MessagePublic
    task: TaskPublic | None = None


class MessageHeld(ContractModel):
    """Agent 主体发送被 freshness 扣住 → 202（G1；人类发送永不 held）。"""

    held_draft: HeldDraftPublic


class HeldDraftReleaseResponse(ContractModel):
    """POST /held-drafts/{id}/release 响应（B §4.14）：以原载荷落消息 + held 行置 released 终态。"""

    message: MessagePublic
    held_draft: HeldDraftPublic


class HeldDraftResponse(ContractModel):
    """POST /held-drafts/{id}/discard | /reevaluate 响应（B §4.14）：仅回 held 行最新态。"""

    held_draft: HeldDraftPublic


class ReadPositionPut(ContractModel):
    last_read_message_id: Ulid


# ------------------------------------------------------------ 4.7/4.8 任务域（M2）


class TaskStatusChange(ContractModel):
    """状态写（B §9.1）——POST /tasks/{id}/status。

    非法边 → 422 TASK_TRANSITION_INVALID；to==当前 → 幂等 200。
    """

    to: TaskStatus


class AssignRequest(ContractModel):
    """改派（B §9.2）——POST /tasks/{id}/assign；member_id=None → 取消指派（不动 status）。"""

    member_id: Ulid | None = None


class ConvertToTask(ContractModel):
    """Convert to Task（B §9.3）——POST /messages/{id}/task。

    title 缺省 = 锚点 body 首非空行剥 MD 前缀、>80 截断。
    """

    title: str | None = None


class TaskPatch(ContractModel):
    """元数据补丁（B §4.7）——PATCH /tasks/{id}；不写 task_events，广播 task.updated。

    `level`：升格载体（M3 P-2 拍板）——仅 `l1→l2` 单向放行；`l2→l1` 或非法值由 server
    校验拒 422 TASK_TRANSITION_INVALID（rule=D1）。升格本身不写 task_events。
    """

    title: str | None = None
    silence_override_h: int | None = None  # 列 M2 就位；D5 消费方随 M4
    level: TaskLevel | None = None


class TaskUsage(ContractModel):
    """TaskDetail 的成本聚合（token_usage_events 按 task_id 汇总）。"""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    events: int = 0  # token_usage_events 按 task_id 聚合的行数


class TaskDetail(ContractModel):
    """GET /tasks/{id}（B §9.8）。"""

    task: TaskPublic
    contracts: list[TaskContractPublic] = []  # M3 前恒空
    usage: TaskUsage


class CanvasDetail(ContractModel):
    """GET /channels/{id}/canvas（B §4.9）：画布头 + 节点/边（空画布二者皆空）。"""

    canvas: CanvasPublic
    nodes: list[CanvasNodePublic] = []
    edges: list[CanvasEdgePublic] = []


class SearchJumps(ContractModel):
    """GET /search 的跳转分组（名称子串命中，NOCASE）。"""

    channels: list[ChannelPublic] = []
    members: list[MemberPublic] = []


class SearchMessageResult(ContractModel):
    message: MessagePublic
    snippet: str  # FTS5 snippet()：«»高亮 + …省略、窗口 12 token（B §9.6.3）


class SearchResponse(ContractModel):
    """搜索（B §9.6）——GET /search，三分组。"""

    jumps: SearchJumps
    messages: list[SearchMessageResult] = []  # messages_fts 命中
    tasks: list[TaskPublic] = []  # title 子串 ∪ 锚点 FTS 去重


# ---- 4.3 L2 契约 body 模型（PRD §4.3 v1）
#
# 三种 schema 建齐：TaskPlan（`AcceptanceCriterion`/`TaskPlanBody` 已下沉 entities——被
# templates.plan_skeleton 复用，此处从 entities re-export 供契约端点/NodeCreate/CONTRACT_BODY_MODELS
# 用）；TaskHandoff/LoopContract 仅请求侧消费，留本模块。`ContractCreate.body` 保持 JsonValue，按
# kind 的二次 model_validate 在 server 侧执行（E2）——此处只登记契约形状，不做 kind↔模型分派。


class Deliverable(ContractModel):
    path: str  # 绝对路径 / 工件链接
    kind: DeliverableKind


class Evidence(ContractModel):
    type: EvidenceType
    ref: str  # 命令 + 输出摘要 / 文件路径
    conclusion: str


class TaskHandoffBody(ContractModel):
    """跨 Agent 交接契约（置 In Review 时必填，T7 校验非空——PRD §4.3 v1）。"""

    version: Literal["coagentia.task-handoff.v1"] = "coagentia.task-handoff.v1"
    from_member: Ulid  # `from` 是 Python 关键字，契约字段名用 from_member/to_member
    to_member: Ulid  # Agent 或人类
    # deliverables/evidence 提交期允许空（handoff 可增量起草）；"≥1 非空"由 **T7 流转门**在置
    # in_review 时执法（PRD §4.3「T7 校验非空」+ §5.3）——否则"缺 deliverables 拒"路径不可达。
    deliverables: list[Deliverable] = []
    evidence: list[Evidence] = []
    open_risks: list[str] = []  # 已知风险与未尽事项（可空）
    verify_plan: str  # 建议接收方如何独立复核


class LoopBudget(ContractModel):
    max_retries: int = 1  # 默认重试一次即止，不许无限探索
    max_runtime_min: int


class LoopContractBody(ContractModel):
    """循环任务上岗契约（创建循环 Reminder 时必填——PRD §4.3 v1；生成消费归 M4，模型 M3 建齐）。"""

    version: Literal["coagentia.loop-contract.v1"] = "coagentia.loop-contract.v1"
    cadence: str  # cron 或 interval 表达
    verification: list[str]  # 每次输出必含的校验项
    budget: LoopBudget
    tools: list[str] = []  # 允许使用的工具/技能
    escalation: str  # 何时拉人 / 拉其他 Agent


# ---- kind↔schema 单一事实源（纪律 7）：POST /tasks/{id}/contracts 按 kind 二次 model_validate
# 用的分派表；server（E2）与本包共用，避免 if/elif 在多处重复长成第二份事实。

CONTRACT_BODY_MODELS: dict[ContractKind, type[ContractModel]] = {
    ContractKind.TASK_PLAN: TaskPlanBody,
    ContractKind.TASK_HANDOFF: TaskHandoffBody,
    ContractKind.LOOP_CONTRACT: LoopContractBody,
}

# ReminderCreate.loop_contract 前向引用 LoopContractBody（定义序在后），此处补全（同
# entities.MessagePublic.files 先例）。
ReminderCreate.model_rebuild()


# ---- 4.3/4.7 契约端点请求模型（M3）


class ContractCreate(ContractModel):
    """POST /tasks/{id}/contracts（提交与修订）。

    `body` 故意留 JsonValue：按 `kind` 对应哪个 body 模型二次 `model_validate` 是 server
    侧职责（kind≠schema 或字段校验失败 → 422 VALIDATION_FAILED），此包不做 kind↔模型分派。
    """

    kind: ContractKind
    body: JsonValue


class ContractDraftRequest(ContractModel):
    """POST /tasks/{id}/contracts/request-draft（"让 @Agent 起草"）。

    效果 = S1 定向直投唤醒（`InjectKind.CONTRACT_DRAFT_REQUEST`，契约 D）；daemon 离线 → 503
    DAEMON_OFFLINE（P-3，best-effort 非积压）。
    """

    kind: ContractKind
    agent_member_id: Ulid


# ---- 4.9 画布端点请求/响应模型（M3b）
#
# 结构写（增删节点/边、布局、force-start）落本表并推进基线（B §4.9）；环校验/gating/baseline
# 推进是 server（E4/E5）职责，此包只登记形状。成功写响应统一附 baseline_version/baseline_hash
# （B §4.9）——前端据此对齐乐观 UI 与 canvas.baseline_advanced 广播。


class NodeCreate(ContractModel):
    """POST /canvases/{id}/nodes：新增画布节点。

    `kind='agent'` → 由 task_plan 起一个 agent 节点（引用任务，非副本 C8）；`kind='system'` →
    system_action 必填、check 动作附 command。字段级 kind↔约束由 server 执法（V14/W8）。
    """

    title: str
    kind: CanvasNodeKind
    system_action: SystemAction | None = None  # kind='system' 必填
    command: str | None = None  # system_action='check' 必填
    task_plan: TaskPlanBody | None = None  # kind='agent' 立项载体


class NodePatch(ContractModel):
    """PATCH /canvases/{id}/nodes/{node_id}：改节点标题 / check 命令。"""

    title: str | None = None
    command: str | None = None


class EdgeCreate(ContractModel):
    """POST /canvases/{id}/edges：连边；成环由 server 拓扑校验拒 422 GRAPH_CYCLE（V9）。"""

    from_node_id: Ulid
    to_node_id: Ulid


class LayoutPositionIn(ContractModel):
    """单节点坐标（pos_x/pos_y 不参与基线快照，契约 A §6）。"""

    node_id: Ulid
    x: float
    y: float


class LayoutPut(ContractModel):
    """PUT /canvases/{id}/layout：整批坐标覆盖（不推进基线）。"""

    positions: list[LayoutPositionIn]


class CanvasMutation(ContractModel):
    """画布结构写统一响应（B §4.9）：附最新基线版本/指纹，命中的节点或边随写回填。"""

    baseline_version: int
    baseline_hash: Sha256Hex
    node: CanvasNodePublic | None = None
    edge: CanvasEdgePublic | None = None


# ---- 4.12 模板端点请求/响应模型（M5；行为语义细则见 B §11.1/§11.2，此处只登记形状）
#
# TemplateBody 与其嵌套模型（TemplateNode/Edge/Role）在 entities（JSON 列嵌套模型 §8.3）；
# 保存/实例化事务、TemplateBody 校验（无环/引用一致性）、409 约束都在 server 侧执法，此包不做。


class TemplateCreate(ContractModel):
    """POST /templates 存为模板（B §4.12/§11.1）。

    服务端读 `channel_id` 频道画布快照序列化 `TemplateBody`（A §4.10 提取规则）；
    `role_placeholders`（{member_id: 占位名}）覆盖默认 owner 去重占位名；`include_node_ids` 缺省
    = 全部 task 节点；画布无正式节点 / 存在草稿层 → 409 TEMPLATE_CANVAS_NOT_READY。
    """

    channel_id: Ulid
    name: str
    description: str = ""
    role_placeholders: dict[str, str] | None = None  # {member_id: 占位名} 覆盖默认去重
    include_node_ids: list[Ulid] | None = None  # 缺省 = 全部 task 节点


class TemplateInstantiate(ContractModel):
    """POST /templates/{id}/instantiate（B §4.12/§11.2）。

    `role_mapping` 须覆盖 body.roles 全部占位（缺失 → 422 VALIDATION_FAILED，details.missing 列
    占位名）；值 null = 该角色节点落地为无 owner（"待认领"）。单事务落地批（`tmpl:<batch_id>:
    <node_key>` 幂等，接受 Idempotency-Key）。v1.3 收窄：无内联 create——向导"新建"走既有创建
    Agent 弹窗再回填映射（§7 #8）。
    """

    channel_id: Ulid
    role_mapping: dict[str, Ulid | None]


class InstantiateResult(ContractModel):
    """实例化响应（B §4.12）：单事务落地批 + 逐节点落地任务（零新增 WS 事件，广播走既有事件）。"""

    batch: LandingBatchPublic
    tasks: list[TaskPublic] = []


# ---- 4.5 每频道通知设置请求模型（M5；人类本人自治，消费规则见 B §11.4）


class NotificationSettingPut(ContractModel):
    """PUT /channels/{id}/notification-setting（B §4.5/§11.4）：upsert 懒建；人类成员本人自治
    （无 admin 门）；Agent 主体 403（通知是人类面）；kind=dm → 422 NOTIF_IN_DM（DM 必达）。
    GET 无行回默认 `{mode: all}`（响应用 entities.ChannelNotificationSettingPublic）。"""

    mode: NotificationMode


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

# ---------------------------------------------------- M2 端点清单（§4.7/§4.8 M2 集 + files 页签）
# force-start / 契约端点归 M3，不列。路径参数命名沿用 M1 约定。
ENDPOINTS_M2: tuple[tuple[str, str], ...] = (
    # §4.7 任务域（M2）
    ("GET", "/tasks"),
    ("POST", "/messages/{message_id}/task"),
    ("GET", "/tasks/{task_id}"),
    ("POST", "/tasks/{task_id}/claim"),
    ("POST", "/tasks/{task_id}/unclaim"),
    ("POST", "/tasks/{task_id}/assign"),
    ("POST", "/tasks/{task_id}/status"),
    ("PATCH", "/tasks/{task_id}"),
    # §4.6 文件页签（v1.1 新增）
    ("GET", "/channels/{channel_id}/files"),
    # §4.8 搜索与 Activity
    ("GET", "/search"),
    ("GET", "/activity"),
    ("POST", "/activity/{activity_id}/done"),
)

# ---------------------------------------------------- M3 端点清单（§4.7 契约/force-start + 画布）
# 画布组（§4.9）块 a 期间只登记不 serve（结构/gating 归块 b）；force-start 归 E5 实现。
ENDPOINTS_M3: tuple[tuple[str, str], ...] = (
    # §4.7 契约与 force-start
    ("GET", "/tasks/{task_id}/contracts"),
    ("POST", "/tasks/{task_id}/contracts"),
    ("POST", "/tasks/{task_id}/contracts/request-draft"),
    ("POST", "/tasks/{task_id}/force-start"),
    # §4.9 画布（块 a 期间登记但不 serve；结构/gating 归 M3b）
    ("GET", "/channels/{channel_id}/canvas"),
    ("POST", "/canvases/{canvas_id}/nodes"),
    ("PATCH", "/canvases/{canvas_id}/nodes/{node_id}"),
    ("DELETE", "/canvases/{canvas_id}/nodes/{node_id}"),
    ("POST", "/canvases/{canvas_id}/edges"),
    ("DELETE", "/canvases/{canvas_id}/edges/{edge_id}"),
    ("PUT", "/canvases/{canvas_id}/layout"),
    ("POST", "/canvas-nodes/{node_id}/retry"),
)

# ---------------------------------------------------- M4 端点清单（§4.14 护栏三键干预）
# held 行只由 freshness 门创建（§4.6 的 202 路径），无 POST /held-drafts 创建端点。
# release/discard/reevaluate = 三键人类干预（Agent 403 rule=G3）；对终态 → 409 HELD_DRAFT_RESOLVED。
ENDPOINTS_M4: tuple[tuple[str, str], ...] = (
    ("GET", "/held-drafts"),
    ("POST", "/held-drafts/{held_draft_id}/release"),
    ("POST", "/held-drafts/{held_draft_id}/discard"),
    ("POST", "/held-drafts/{held_draft_id}/reevaluate"),
)

# ---------------------------------------------------- M5 端点清单（§4.12 模板三 + §4.5 通知设置二）
# 模板组（M5b：H5 存/列 + H6 实例化）与每频道通知设置组（M5a：H3）；路径参数命名沿用先例。
# mock 形状源全 serve（喂 OpenAPI→rest.ts）；真 server serve 与逐端点行为双跑归各实现模块测试。
ENDPOINTS_M5: tuple[tuple[str, str], ...] = (
    # §4.12 模板（M5b）
    ("GET", "/templates"),
    ("POST", "/templates"),
    ("POST", "/templates/{template_id}/instantiate"),
    # §4.5 每频道通知设置（M5a）
    ("GET", "/channels/{channel_id}/notification-setting"),
    ("PUT", "/channels/{channel_id}/notification-setting"),
)
