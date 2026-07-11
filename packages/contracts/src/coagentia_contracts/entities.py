"""实体线上形状（契约 A §4 全部表的忠实翻译）。

约定（契约 A §8）：
- 每实体两形状：`XxxRow`（全字段）与 `XxxPublic`（API 线上形状，剔除敏感/内部列）。
  Public 与 Row 相同时以子类表达（独立 schema title，字段单源）；有剔除时显式重写并由
  tests/test_public_shapes.py 断言字段集关系。
- 字段名 = Pydantic 字段名 = SQLAlchemy 列名（snake_case 三处一致）。
- 布尔在 DB 落 INTEGER 0/1，线上形状为 bool；时间戳线上 = ISO-8601 Z 字符串（零转换）。
- JSON 列在此为嵌套模型；M3+/M6 才定稿的 body 类先以 JsonValue 占位（升版本时收紧）。
- 未列出的表/字段不要发明（契约 A 实现说明）。
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

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
    VerifyBy,
    WorktreeStatus,
)
from coagentia_contracts.ids import Sha256Hex, TimestampZ, Ulid


class ContractModel(BaseModel):
    """所有契约模型的基类：拒绝未知字段（形状即契约，多余字段 = 违约）。"""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- 4.1 身份与基座


class WorkspaceRow(ContractModel):
    id: Ulid
    name: str
    slug: str
    attachment_max_mb: int = 200
    onboarding_greeting: bool = True
    ui_theme: UiTheme = UiTheme.DARK
    notif_desktop: bool = True
    notif_sound: bool = False
    setup_state: dict[str, JsonValue] = Field(default_factory=dict)
    created_at: TimestampZ


class WorkspacePublic(WorkspaceRow):
    pass


class DetectedRuntime(ContractModel):
    """computers.detected_runtimes 数组元素（FR-2.3）。"""

    runtime: Runtime
    installed: bool
    models: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)  # v1.0.6：候选技能池（daemon 探测上报，
    # 列出≠授予不违反 R6；P6 技能页签勾选来源；codex 无技能机制恒空，契约 A v1.0.6 / D v1.0.2）


class ComputerRow(ContractModel):
    id: Ulid
    workspace_id: Ulid
    name: str
    os: str | None = None
    arch: str | None = None
    daemon_version: str | None = None
    api_key_hash: str  # SHA-256(api-key)；明文只在 Add Computer 弹窗出现一次
    detected_runtimes: list[DetectedRuntime] = Field(default_factory=list)
    status: ComputerStatus = ComputerStatus.OFFLINE
    last_seen_at: TimestampZ | None = None
    created_at: TimestampZ


class ComputerPublic(ContractModel):
    """= ComputerRow 剔除 api_key_hash（契约 A §8.2 敏感列）。"""

    id: Ulid
    workspace_id: Ulid
    name: str
    os: str | None = None
    arch: str | None = None
    daemon_version: str | None = None
    detected_runtimes: list[DetectedRuntime] = Field(default_factory=list)
    status: ComputerStatus = ComputerStatus.OFFLINE
    last_seen_at: TimestampZ | None = None
    created_at: TimestampZ


class MemberRow(ContractModel):
    id: Ulid
    workspace_id: Ulid
    kind: MemberKind
    name: str  # UNIQUE(workspace_id, name COLLATE NOCASE)——@解析键
    role: MemberRole = MemberRole.MEMBER
    removed_at: TimestampZ | None = None  # 软删除（消息归属保留身份）
    created_at: TimestampZ


class MemberPublic(MemberRow):
    pass


class AgentRow(ContractModel):
    member_id: Ulid  # PK, FK→members（1:1 扩展）
    computer_id: Ulid
    runtime: Runtime
    model: str
    description: str = ""
    home_path: str  # 文件树由 daemon 按需提供，不入库（FR-3.3）
    status: AgentStatus = AgentStatus.OFFLINE  # 最后已知态；Busy 细分只走 WS 不入库
    created_by_member_id: Ulid


class AgentPublic(AgentRow):
    pass


class AgentSkillRow(ContractModel):
    agent_member_id: Ulid
    skill: str
    granted_by_member_id: Ulid
    granted_at: TimestampZ


class AgentSkillPublic(AgentSkillRow):
    pass


class AgentRoleTemplateRow(ContractModel):
    """M6 形状冻结（03 §3.1 "Orchestrator = 数据不是代码"）。"""

    id: Ulid
    key: str  # UNIQUE，如 'orchestrator'
    name: str
    description_prefill: str
    prompt_sections: JsonValue  # M6 定稿（§13.1 拆解章节 + §12 规则表注入位）
    builtin: bool = True


class AgentRoleTemplatePublic(AgentRoleTemplateRow):
    pass


# ---------------------------------------------------------------- 4.2 会话面


class ChannelRow(ContractModel):
    id: Ulid
    workspace_id: Ulid
    kind: ChannelKind
    name: str | None = None  # kind=channel 必填（部分唯一索引）；dm 为 NULL
    description: str = ""
    is_private: bool = False  # dm 恒 1
    dm_key: str | None = None  # 两成员 id 排序后 "<a>:<b>"
    archived_at: TimestampZ | None = None
    joint_ref: str | None = None  # todo：跨工作区引用预留（FR-1.5），MVP 恒 NULL
    next_task_number: int = 1
    remind_todo_h: int = 24
    remind_inprog_h: int = 12
    remind_review_h: int = 24
    remind_escalation: bool = True
    held_reeval_min: int = 5  # G4
    held_escalate_n: int = 3  # G5
    decomp_mode: DecompMode = DecompMode.DRAFT  # O5
    decomp_node_limit: int = 12  # O6
    orch_escalation: bool = False  # O2
    created_at: TimestampZ


class ChannelPublic(ChannelRow):
    pass


class ChannelMemberRow(ContractModel):
    channel_id: Ulid
    member_id: Ulid
    joined_at: TimestampZ


class ChannelMemberPublic(ChannelMemberRow):
    pass


class ChannelNotificationSettingRow(ContractModel):
    """M5（FR-4.7 每频道通知设置）。"""

    channel_id: Ulid
    member_id: Ulid
    mode: NotificationMode = NotificationMode.ALL


class ChannelNotificationSettingPublic(ChannelNotificationSettingRow):
    pass


class MessageRow(ContractModel):
    """不可变：无 UPDATE/DELETE（契约 A §1）。"""

    id: Ulid
    workspace_id: Ulid
    channel_id: Ulid
    thread_root_id: Ulid | None = None  # 根消息自身必须 NULL（线程不可嵌套）
    author_member_id: Ulid | None = None  # NULL = 系统消息
    kind: MessageKind = MessageKind.USER
    card_kind: CardKind | None = None  # 卡片 = 不可变锚点，活状态走实体 WS 事件
    card_ref: str | None = None
    body: str  # Markdown 原文；@ 与 task #n 保持纯文本
    created_at: TimestampZ


class MessagePublic(MessageRow):
    """读面派生字段 files（v1.0.4，Public≠Row 放宽先例同 ActivityItemPublic.actor_member_id）：
    REST 消息读面（列表/线程/发消息响应/搜索命中）与 message.created 广播填充（[] = 无附件）；
    未附着面（daemon backlog/deliver 帧）保持 None——否则旧消息附件卡受 channelFiles
    首页 ≤50 截断（M2 挂账）。serialize 时按 message_id 联查 files，不落 messages 表。"""

    files: list["FilePublic"] | None = None


class MessageMentionRow(ContractModel):
    """发送时服务端解析一次的派生持久化；body 是唯一事实源。"""

    message_id: Ulid
    member_id: Ulid


class MessageMentionPublic(MessageMentionRow):
    pass


class MessageTaskRefRow(ContractModel):
    """M2：task #n 解析结果（派生持久化）。"""

    message_id: Ulid
    task_id: Ulid


class MessageTaskRefPublic(MessageTaskRefRow):
    pass


class FileRow(ContractModel):
    """附件随消息永存、不可删（FR-4.8）。预上传暂存不落本表（契约 D §9.2）。"""

    id: Ulid
    workspace_id: Ulid
    message_id: Ulid
    channel_id: Ulid  # 冗余列——P4 按频道聚合查询面
    name: str
    mime: str
    size_bytes: int
    sha256: Sha256Hex
    stored_path: str  # 数据目录内相对路径（布局 = 契约 D §9.1）
    created_at: TimestampZ


class FilePublic(ContractModel):
    """= FileRow 剔除 stored_path（服务端内部）；message_id/channel_id 可空 = staging 态
    （契约 D §9.2：预上传返回的 FilePublic 尚未绑定消息）。"""

    id: Ulid
    workspace_id: Ulid
    message_id: Ulid | None = None
    channel_id: Ulid | None = None
    name: str
    mime: str
    size_bytes: int
    sha256: Sha256Hex
    created_at: TimestampZ


# MessagePublic.files 前向引用 FilePublic（定义序在后），此处显式补全。
MessagePublic.model_rebuild()


class ReadPositionRow(ContractModel):
    """未读线与未读计数依据；Agent 侧由 deliver ack 写入（契约 D §8.3）。"""

    member_id: Ulid
    channel_id: Ulid
    last_read_message_id: Ulid
    last_read_at: TimestampZ


class ReadPositionPublic(ReadPositionRow):
    pass


class ActivityItemRow(ContractModel):
    """M2：Activity 聚合面（FR-4.6）。"""

    id: Ulid
    workspace_id: Ulid
    member_id: Ulid  # 接收者
    kind: ActivityKind
    channel_id: Ulid | None = None
    message_id: Ulid | None = None
    task_id: Ulid | None = None
    created_at: TimestampZ
    done_at: TimestampZ | None = None


class ActivityItemPublic(ActivityItemRow):
    """读面派生字段：actor_member_id = 触发本条的消息作者（自 message_id 联查，不落库）。

    member_id 是接收者（表列语义）；前端渲染"谁提及了你/谁发来私信"需要作者，
    缺此字段时前端只能错用 member_id（M2 review 确认的行为人错位）。
    """

    actor_member_id: Ulid | None = None


# ---------------------------------------------------------------- 4.3 任务与契约


class TaskRow(ContractModel):
    """M2：带元数据的消息（T1）。blocked 不入库——画布边实时推导（C3）。"""

    id: Ulid
    workspace_id: Ulid
    channel_id: Ulid
    number: int  # UNIQUE(channel_id, number)
    root_message_id: Ulid
    title: str
    status: TaskStatus = TaskStatus.TODO
    owner_member_id: Ulid | None = None  # 同刻唯一 owner；claim = 条件更新（T2）
    level: TaskLevel = TaskLevel.L1
    created_by_member_id: Ulid
    project_id: Ulid | None = None
    writes_code: bool = False
    silence_override_h: int | None = None  # D5 任务级覆盖
    status_changed_at: TimestampZ
    created_at: TimestampZ


class TaskPublic(TaskRow):
    pass


class TaskEventRow(ContractModel):
    """M2：状态账本（T5；不可变表）。"""

    seq: int
    task_id: Ulid
    kind: TaskEventKind
    from_status: TaskStatus | None = None
    to_status: TaskStatus | None = None
    owner_member_id: Ulid | None = None  # v1.0.2：claim/assign 时=新 owner（assign 取消置 NULL）
    actor_member_id: Ulid | None = None  # 系统动作为 NULL
    created_at: TimestampZ


class TaskEventPublic(TaskEventRow):
    pass


class TaskContractRow(ContractModel):
    """M3：L2 契约实例（D1 三种 schema）。task_id 与 reminder_id 恰一非空（CHECK）。"""

    id: Ulid
    workspace_id: Ulid
    task_id: Ulid | None = None
    reminder_id: Ulid | None = None
    kind: ContractKind
    version: str  # 'coagentia.task-plan.v1' 等（PRD §4.3）
    body: JsonValue  # M3 收紧为三种 schema 模型
    revision: int = 1
    superseded_at: TimestampZ | None = None
    created_by_member_id: Ulid
    created_at: TimestampZ


class TaskContractPublic(TaskContractRow):
    pass


# ---- L2 契约 body schema：TaskPlan（PRD §4.3 v1）
#
# JSON 列嵌套模型（§8.3）：task_contracts.body 按 kind 二次 model_validate 的 TaskPlan 分支、
# 且 templates.plan_skeleton 复用同形状（A §4.10）。放在 entities 层（下层）供 rest（NodeCreate/
# CONTRACT_BODY_MODELS，上层 re-export）与 TemplateBody 共用——TaskHandoff/LoopContract body 仍
# 在 rest（仅请求侧消费，无实体 JSON 列引用）。纪律 7 单一事实源。


class AcceptanceCriterion(ContractModel):
    """TaskPlan 验收标准单条（可证伪表述、禁形容词——PRD §4.3；文案规范不在此校验）。"""

    id: str
    statement: str
    verify_by: VerifyBy
    verify_ref: str  # 验证命令或核对说明


class TaskPlanBody(ContractModel):
    """L2 任务计划契约（进入画布/正式立项时必填——PRD §4.3 v1）。"""

    version: Literal["coagentia.task-plan.v1"] = "coagentia.task-plan.v1"
    goal: str  # 一句话用户成果（用户视角）
    acceptance_criteria: Annotated[list[AcceptanceCriterion], Field(min_length=1)]
    defaults_decided: list[str] = []  # 替用户拍板的默认决策（可空但须列明）
    out_of_scope: list[str] = []  # 明确不做（可空）


# ---------------------------------------------------------------- 4.4 画布（S3）


class CanvasRow(ContractModel):
    """M1 建表（预留 #2）。基线语义 = 契约 A §6。"""

    id: Ulid
    workspace_id: Ulid
    channel_id: Ulid  # UNIQUE：每频道恰一
    baseline_version: int = 0  # 单调递增
    baseline_hash: Sha256Hex  # 空画布 = 空快照指纹，非 NULL
    updated_at: TimestampZ


class CanvasPublic(CanvasRow):
    pass


class CanvasNodeRow(ContractModel):
    """M3。草稿层节点不落本表（proposals.body 渲染）。"""

    id: Ulid
    canvas_id: Ulid
    kind: CanvasNodeKind
    task_id: Ulid | None = None  # CHECK: kind='agent' → NOT NULL；引用不是副本（C8）
    is_summary: bool = False
    system_action: SystemAction | None = None  # CHECK: kind='system' → NOT NULL（W8）
    command: str | None = None  # system_action='check' 必填（V14）
    system_status: SystemNodeStatus | None = None
    pos_x: float = 0  # 不参与基线快照（契约 A §6）
    pos_y: float = 0
    created_at: TimestampZ


class CanvasNodePublic(CanvasNodeRow):
    pass


class CanvasEdgeRow(ContractModel):
    """M3。UNIQUE(canvas_id, from, to)；无环由串行化点内拓扑排序保证。"""

    id: Ulid
    canvas_id: Ulid
    from_node_id: Ulid
    to_node_id: Ulid


class CanvasEdgePublic(CanvasEdgeRow):
    pass


# ---------------------------------------------------------------- 4.5 护栏与提醒


class HeldDraftReasons(ContractModel):
    """结构化被扣原因（G2：未读消息清单，可点跳转）。

    v1.0.5：`unread_message_ids` 上限 50 条（截断保留最新）；`total_unread` 为真实未读计数
    （截断前的全量口径，卡片显示"还有 N 条"）。
    """

    unread_message_ids: list[Ulid]
    total_unread: int


class HeldDraftAsTask(ContractModel):
    """草稿携带的 as_task 意图（v1.0.5）——放行时随消息同一事务执行（语义同 B §9.4）。

    形状镜像 `rest.AsTask`（entities 为下层不能反向 import rest；字段单源在此，rest.AsTask
    另有其消息端点用途，二者刻意分立以免层次倒置）。
    """

    title: str | None = None


class HeldDraftRow(ContractModel):
    """M4（D4/G1–G6）。"""

    id: Ulid
    workspace_id: Ulid
    agent_member_id: Ulid
    channel_id: Ulid
    thread_root_id: Ulid | None = None
    draft_body: str  # 草稿全文（G2 卡片可见）
    file_ids: list[Ulid] | None = None  # v1.0.5：草稿携带的 staging 附件（放行不得丢附件）
    as_task: HeldDraftAsTask | None = None  # v1.0.5：草稿携带的 as_task 意图原样保存
    reasons: HeldDraftReasons
    status: HeldDraftStatus = HeldDraftStatus.HELD
    held_count: int = 1  # G5
    next_reeval_at: TimestampZ  # G4 倒计时锚（客户端读秒，不推帧）
    escalated_at: TimestampZ | None = None
    resolved_by_member_id: Ulid | None = None
    resolved_at: TimestampZ | None = None
    resolution: HeldResolution | None = None
    created_at: TimestampZ


class HeldDraftPublic(HeldDraftRow):
    pass


class ReminderRow(ContractModel):
    """M1：Agent 自设唤醒（FR-3.9）。CHECK: kind='recurring' → loop_contract_id NOT NULL。"""

    id: Ulid
    workspace_id: Ulid
    agent_member_id: Ulid  # 创建者 = 唯一被唤醒者
    kind: ReminderKind
    cadence: str  # once：ISO 时刻；recurring：cron/interval 表达式
    anchor_channel_id: Ulid
    anchor_message_id: Ulid | None = None
    anchor_task_id: Ulid | None = None
    loop_contract_id: Ulid | None = None
    next_fire_at: TimestampZ
    status: ReminderStatus = ReminderStatus.ACTIVE
    cancelled_by_member_id: Ulid | None = None
    created_at: TimestampZ


class ReminderPublic(ReminderRow):
    pass


# ---------------------------------------------------------------- 4.6 可观测性（不可变表）


class DiagnosticEventRow(ContractModel):
    """M1：命令级留痕，落盘持久跨重启（NFR4）；type 命名空间见契约 A §4.6。"""

    seq: int
    workspace_id: Ulid
    agent_member_id: Ulid | None = None  # 系统事件为 NULL
    type: str
    channel_id: Ulid | None = None
    task_id: Ulid | None = None
    batch_id: Ulid | None = None
    payload: JsonValue
    created_at: TimestampZ


class DiagnosticEventPublic(DiagnosticEventRow):
    pass


class TokenUsageEventRow(ContractModel):
    """M1 建表：W7 成本口径原始层（仅 provider 上报，永不推导费用）。"""

    id: Ulid  # daemon/适配器生成的 ULID——契约 D §7 exactly-once 去重根基
    workspace_id: Ulid
    agent_member_id: Ulid
    task_id: Ulid | None = None  # 缺失即计入 tasksReporting 分母缺口
    channel_id: Ulid | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    source_session: str | None = None  # 适配器会话标识（契约 E §4）
    reported_at: TimestampZ


class TokenUsageEventPublic(TokenUsageEventRow):
    pass


# ---------------------------------------------------------------- 4.7 账本与落地批次（S5，预留 #1）


class LandingBatchRow(ContractModel):
    """幂等键命名空间锚（01 §5.1 修订：opId 含 batch_id）。"""

    id: Ulid  # 即 batch_id
    workspace_id: Ulid
    channel_id: Ulid
    kind: LandingBatchKind
    content_hash: Sha256Hex  # 内容指纹作追溯，不再作命名空间
    source_ref: str  # proposal id / template id
    confirmed_by: str  # member_id 或字面量 'auto(channel-policy)'
    status: LandingBatchStatus = LandingBatchStatus.RUNNING
    created_at: TimestampZ
    done_at: TimestampZ | None = None  # 写入 = 批次 :done 事实源（S4）


class LandingBatchPublic(LandingBatchRow):
    pass


class LedgerEntryRow(ContractModel):
    """通用幂等账本（03 §3.2 基础设施；不可变表）。opId 格式见 constants.OPID_*。"""

    seq: int  # 全局单调序
    op_id: str  # UNIQUE
    request_hash: Sha256Hex
    batch_id: Ulid | None = None  # 编排类操作必填
    actor_member_id: Ulid | None = None
    kind: str  # create_task / create_node / create_edge / mark_done / …（开放集）
    payload: JsonValue  # 重放的输入
    created_at: TimestampZ


class LedgerEntryPublic(LedgerEntryRow):
    pass


# ---------------------------------------------------------------- 4.8 编排（M6，形状冻结）


class ProposalRow(ContractModel):
    id: Ulid
    workspace_id: Ulid
    channel_id: Ulid
    source_task_id: Ulid  # 同 source 单一非终态提案（部分唯一索引）
    kind: ProposalKind = ProposalKind.FULL
    revision: int = 1
    status: ProposalStatus = ProposalStatus.DRAFTING
    body: JsonValue  # coagentia.decomposition.v1 / -delta.v1（M6 收紧）
    proposal_hash: Sha256Hex
    base_hash: Sha256Hex | None = None  # delta 的基线指纹（F9）
    landed_hash: Sha256Hex | None = None
    adjustments: list[JsonValue] = Field(default_factory=list)
    repair_count: int = 0  # O7（按 revision 重置）
    proposed_by_member_id: Ulid
    created_at: TimestampZ
    updated_at: TimestampZ


class ProposalPublic(ProposalRow):
    pass


# ---------------------------------------------------------------- 4.9 交付链路（M6–M7）


class ProjectRow(ContractModel):
    id: Ulid
    workspace_id: Ulid
    computer_id: Ulid
    name: str
    repo_path: str  # 非 git 仓库时就地报错（P12）
    dev_command: str | None = None
    deploy_command: str | None = None
    preview_idle_min: int = 30  # W4
    worktree_keep_days: int = 7  # FR-10.2
    created_at: TimestampZ


class ProjectPublic(ProjectRow):
    """频道绑定读面由 channel_projects 联查得出，不落 projects 表。"""

    channel_ids: list[Ulid]


class ChannelProjectRow(ContractModel):
    channel_id: Ulid
    project_id: Ulid


class ChannelProjectPublic(ChannelProjectRow):
    pass


class WorktreeRow(ContractModel):
    id: Ulid
    workspace_id: Ulid
    project_id: Ulid
    task_id: Ulid  # UNIQUE：每任务一树（W2）
    branch: str
    path: str
    status: WorktreeStatus
    merge_commit: str | None = None
    created_at: TimestampZ
    merged_at: TimestampZ | None = None
    cleaned_at: TimestampZ | None = None


class WorktreePublic(WorktreeRow):
    pass


class PreviewSessionRow(ContractModel):
    id: Ulid
    workspace_id: Ulid
    task_id: Ulid
    worktree_id: Ulid
    port: int | None = None  # starting 期未知
    status: PreviewStatus
    started_at: TimestampZ
    last_active_at: TimestampZ | None = None
    recycled_at: TimestampZ | None = None


class PreviewSessionPublic(PreviewSessionRow):
    pass


class DeploymentRow(ContractModel):
    id: Ulid
    workspace_id: Ulid
    project_id: Ulid
    triggered_by_member_id: Ulid  # 全员含 Agent（R8）
    branch: str
    commit_hash: str | None = None
    command: str
    status: DeploymentStatus
    exit_code: int | None = None
    url: str | None = None
    log_path: str | None = None  # 日志落文件（契约 D §9.1），卡片流式读
    token_summary: JsonValue | None = None  # Σ + tasksReporting（W7；M7 收紧）
    started_at: TimestampZ | None = None
    finished_at: TimestampZ | None = None


class DeploymentPublic(ContractModel):
    """= DeploymentRow 剔除 log_path（服务端内部；日志经端点/WS 流读取）。"""

    id: Ulid
    workspace_id: Ulid
    project_id: Ulid
    triggered_by_member_id: Ulid
    branch: str
    commit_hash: str | None = None
    command: str
    status: DeploymentStatus
    exit_code: int | None = None
    url: str | None = None
    token_summary: JsonValue | None = None
    started_at: TimestampZ | None = None
    finished_at: TimestampZ | None = None


# ---------------------------------------------------------------- 4.10 模板


class TemplateNode(ContractModel):
    """TemplateBody.nodes 元素（A v1.0.6 §4.10）：模板内一个 task 节点。"""

    key: str  # 模板内唯一节点键（实例化映射/连边引用）
    title: str  # 任务标题
    role: str  # 角色占位名（引用 TemplateBody.roles[].placeholder）
    plan_skeleton: TaskPlanBody | None = None  # TaskPlan 骨架预填（实例化作 L2 初稿；无则 null）


class TemplateEdge(ContractModel):
    """TemplateBody.edges 元素：node key 引用（`from`/`to` 是 Python 关键字，沿 TaskHandoffBody
    from_member 先例改名 from_key/to_key）；保存与实例化均校验无环（复用 kernel/graph）。"""

    from_key: str
    to_key: str


class TemplateRole(ContractModel):
    """TemplateBody.roles 元素（P13 保存模板弹窗提取表）：角色占位。"""

    placeholder: str  # 占位名（如"实现工程师"）
    description: str = ""  # 角色 description 话术（向导"新建 Agent"预填，FR-7.1）


class TemplateBody(ContractModel):
    """templates.body（A v1.0.6 §4.10 M5 收紧）：DAG 结构 + 角色占位表 + 简报话术（C7）。

    保存序列化（B §11.1）：从画布快照仅取 task 节点、pos 不入；占位按节点 owner 去重、无 owner
    归"待认领"；plan_skeleton 取该任务当前 TaskPlan 契约 body（无则 null）。校验：model_validate +
    edges 无环（复用 kernel/graph）+ nodes.role/edges 引用一致性（server 侧执法）。
    """

    nodes: list[TemplateNode] = Field(default_factory=list)
    edges: list[TemplateEdge] = Field(default_factory=list)
    roles: list[TemplateRole] = Field(default_factory=list)
    briefing: str = ""  # 房间简报话术——实例化后自动发目标频道主流系统消息（FR-7.2）


class TemplateRow(ContractModel):
    """M5：DAG 结构 + 角色占位表 + 简报话术（C7）；实例化走落地事务器（tmpl:<batch_id>:…）。"""

    id: Ulid
    workspace_id: Ulid
    name: str
    description: str = ""
    body: TemplateBody  # v1.0.6 收紧（JsonValue → TemplateBody 嵌套模型，A §4.10）
    builtin: bool = False  # 工程三角 = builtin 行（FR-7.1）
    created_by_member_id: Ulid
    created_at: TimestampZ


class TemplatePublic(TemplateRow):
    pass
