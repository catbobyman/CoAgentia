"""常量目录：opId 前缀（契约 A §4.7）、活动文案（契约 E §7.2）、
禁用工具、诊断类型、规则号、schema 版本号、任务状态机合法边、MCP 工具目录。"""

from coagentia_contracts.enums import ContractKind, TaskStatus

# ---------------- opId（契约 A §4.7 账本；01 §5.1 修订采纳）

OPID_DECOMP_NODE = "decomp:{batch_id}:node:{temp_id}"
OPID_DECOMP_EDGE = "decomp:{batch_id}:edge:{from_id}->{to_id}"
OPID_DECOMP_SUMMARY = "decomp:{batch_id}:summary"
OPID_DECOMP_DONE = "decomp:{batch_id}:done"
OPID_TMPL_PREFIX = "tmpl:{batch_id}:"
OPID_DELTA_OP = "delta:{batch_id}:op:{index}"
OPID_REST_IDEMPOTENCY = "rest:{key}"  # 幂等重试复用账本（契约 B §1）

# ---------------- activity 受控文案（契约 E §7.2；
# 契约 C §6.2 agent.activity 的 detail 值域；'Draft held' 由 server 合成）

ACTIVITY_PHRASES: tuple[str, ...] = (
    "Thinking…",
    "Replying…",
    "Running command…",
    "Writing file…",
    "Reading files…",
    "Browsing…",
    "Using {tool}…",
    "Subagent started",
    "Draft held",
)

# ---------------- 禁用工具（契约 E §2；终表 A8 真机实测定，关闭 E §11.4）
#
# 依据契约 E §5「Agent 行为唯一出口 = coagentia MCP 工具」：凡与 coagentia 工具**功能重叠**
# 的 Claude Code 内置工具都必须禁用，否则 Agent 会误用内置工具绕过频道/护栏/留痕。
# A8 端到端实测暴露：Agent 曾用内置 `SendMessage`（CC 队友消息）而非 coagentia `send_message`，
# 消息未进频道——由此定出下表（保留本地工作类 Read/Write/Edit/Bash/Glob/Grep/WebFetch/WebSearch/
# ToolSearch/Task/Skill：它们是 Agent 干活/加载 MCP 工具的手段，不构成外部行为出口）。
DISALLOWED_TOOLS: tuple[str, ...] = (
    # 计划模式类（E §2 初值）
    "EnterPlanMode",
    "ExitPlanMode",
    # 通信：内置 SendMessage 与 coagentia send_message 重叠 → 必禁（A8 实测的直接肇因）
    "SendMessage",
    # 调度/提醒：与 coagentia create_reminder/cancel_reminder 重叠
    "CronCreate",
    "CronDelete",
    "CronList",
    "ScheduleWakeup",
    # FleetView 持久后台多 Agent 协调 + 工作流编排：属 coagentia（M6 Orchestrator）职责，
    # 非单 Agent 能力。
    "TaskCreate",
    "TaskGet",
    "TaskList",
    "TaskOutput",
    "TaskStop",
    "TaskUpdate",
    "Workflow",
    # worktree 由 coagentia 交付链路管（M6/W2），Agent 不自管
    "EnterWorktree",
    "ExitWorktree",
    # 项目专用外部工具，非 Agent 能力面
    "DesignSync",
)

# ---------------- Codex 禁用工具（契约 E2 §2.5；终表 H2 A 级实测校准回填）
#
# 依据同 DISALLOWED_TOOLS：凡与 coagentia MCP 工具功能重叠的 codex 内置工具须禁用。codex 无
# send_message/task 类内置工具与 coagentia 工具重叠，终表实测 ≈ 空——占位空 tuple，H2 实测后回填。
CODEX_DISALLOWED_TOOLS: tuple[str, ...] = ()

# ---------------- 诊断类型（契约 A §4.6 命名空间；
# draft/delta/landing 与 WS 事件一名两用——预留 #4；开放集，此处登记已知类型）

DIAGNOSTIC_TYPES: tuple[str, ...] = (
    # agent.*（契约 E §8）
    "agent.process_started",
    "agent.process_exited",
    "agent.crash_restarted",
    "agent.session_lost",
    "agent.command",
    "agent.file_edit",
    "agent.tool_call",
    "agent.turn_output",
    "agent.unknown_frame",
    # guard.*（G6，server 侧）
    "guard.held",
    "guard.released",
    "guard.discarded",
    "guard.reevaluate_requested",
    "guard.escalated",
    # 拆解设计 §15 原样（M6）
    "decomp.requested",
    "decomp.context_injected",
    "proposal.drafted",
    "proposal.validation_failed",
    "proposal.repair_attempt",
    "proposal.failed_escalated",
    # J8 新登记（M6b 波 2；拆解设计 §15 未穷举的两处留痕，命名守 proposal.* 命名空间）：
    "proposal.duplicate_ignored",  # awaiting_confirm 期重提同指纹 → 忽略不动 revision（§8.2）
    "proposal.awaiting_reminder_sent",  # awaiting_confirm 超 24h @请求者（F5；防重发推导依据）
    "draft.presented",
    "draft.adjusted",
    "draft.confirmed",
    "draft.rejected",
    "draft.superseded",
    "landing.started",
    "landing.op_applied",
    "landing.op_replayed",
    "landing.completed",
    "landing.fail_closed",
    "delta.proposed",
    "delta.adjusted",
    "delta.confirmed",
    "delta.rejected",
    # deploy.* / preview.*（M7）
    "deploy.started",
    "deploy.finished",
    "preview.started",
    "preview.recycled",
    "preview.failed",
    # system / daemon（契约 D）
    "system.file_gc",
    "daemon.buffer_overflow",
)

DIAGNOSTIC_NAMESPACES: tuple[str, ...] = (
    "agent.",
    "guard.",
    "decomp.",
    "proposal.",
    "draft.",
    "delta.",
    "landing.",
    "deploy.",
    "preview.",
    "system.",
    "daemon.",
)

# ---------------- 权限规则号（PRD §3；rule 字段值域）

RULE_CODES: tuple[str, ...] = (
    "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8",
    "C3", "W1", "T2", "T3", "T7", "admin",
)

# ---------------- schema 版本号（PRD §4.3 / 拆解设计）

SCHEMA_TASK_PLAN_V1 = "coagentia.task-plan.v1"
SCHEMA_TASK_HANDOFF_V1 = "coagentia.task-handoff.v1"
SCHEMA_LOOP_CONTRACT_V1 = "coagentia.loop-contract.v1"
SCHEMA_DECOMPOSITION_V1 = "coagentia.decomposition.v1"
SCHEMA_DECOMPOSITION_DELTA_V1 = "coagentia.decomposition-delta.v1"
SCHEMA_DECOMPOSITION_ERRORS_V1 = "coagentia.decomposition-errors.v1"

# ---------------- Orchestrator 内置角色模板展示常量
# （03-接入架构 §3.1「Orchestrator = 数据不是代码」）
# 单源迁移（纪律 7）：key/name/description_prefill 三展示常量的唯一源。server orchestration/
# role_templates.py import 引用（prompt_sections 等生成内容仍留 server 侧），前端经 gen 出的
# constants.ts 消费（创建 Agent 弹窗角色模板段预填 + NO_ORCHESTRATOR 引导链）。值随 J11 阶段 4
# 定稿话术时在此单点改。
ORCHESTRATOR_ROLE_TEMPLATE_KEY = "orchestrator"
ORCHESTRATOR_ROLE_TEMPLATE_NAME = "Orchestrator（任务拆解协调者）"
ORCHESTRATOR_ROLE_TEMPLATE_DESCRIPTION_PREFILL = (
    "本频道的任务拆解协调者：@它并给一句话需求，它会把需求拆成可校验、可确认、可恢复的任务 DAG "
    "提案（判断归模型、控制归引擎）。提案经系统确定性校验、需人类在草稿画布上确认后落地；被校验"
    "退回时自动按错误清单修复重提。"
)

# ---------------- T7 流转门必填字段（PRD §4.3「T7 校验非空」；server 校验 + 前端提示同源）
# 置 in_review 时该任务活动 TaskHandoff 须逐个非空；纪律 7 单一事实源，不在 server 侧另列字面量。
HANDOFF_REQUIRED_FIELDS: tuple[str, ...] = ("deliverables", "evidence")

# ---------------- 任务契约允许的 kind（POST /tasks/{id}/contracts 端点门）
# loop_contract 属 Reminder 域（D1-L2：循环 Reminder 必先 LoopContract），归 reminder_id 一侧、
# 生成消费随 M4——不可挂 Task（否则污染任务契约面）。纪律 7 单一事实源。
TASK_CONTRACT_KINDS: frozenset[ContractKind] = frozenset(
    {ContractKind.TASK_PLAN, ContractKind.TASK_HANDOFF}
)

# ---------------- 遥测缓冲默认值（契约 D §7，实现默认非协议形状）

BUFFER_DIAGNOSTICS_MAX = 10_000
BUFFER_USAGE_MAX = 100_000
BUFFER_DEPLOY_LOG_MAX_BYTES = 5 * 1024 * 1024

# ---------------- 任务状态机合法边（契约 B §9.1；纪律 7 单一事实源，server 校验+前端防呆同源）
# 值 = 合法目标态集合（不含自身；to==from → 幂等 200 不写事件；空集 = 终态）。
# claim 联动 todo→in_progress、unclaim 联动 in_progress→todo 均落在合法边内（§9.2）。
TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.TODO: frozenset({TaskStatus.IN_PROGRESS, TaskStatus.CLOSED}),
    TaskStatus.IN_PROGRESS: frozenset(
        {TaskStatus.TODO, TaskStatus.IN_REVIEW, TaskStatus.CLOSED}
    ),
    TaskStatus.IN_REVIEW: frozenset(
        {TaskStatus.IN_PROGRESS, TaskStatus.DONE, TaskStatus.CLOSED}
    ),
    TaskStatus.DONE: frozenset(),  # 终态（PRD §4.2 Done→[*]）
    TaskStatus.CLOSED: frozenset({TaskStatus.TODO}),  # reopen
}

# ---------------- claim 语义门（契约 B §9.2；非第二份边表，claim 前置校验，纪律 7 独立于边表）
# 终态不可认领：done 无出边；closed 须先 reopen→todo。server claim 前置 + 前端认领钮防呆同源。
UNCLAIMABLE_STATUSES: frozenset[TaskStatus] = frozenset({TaskStatus.DONE, TaskStatus.CLOSED})

# ---------------- coagentia MCP 工具目录（契约 E §3；Agent 行为唯一出口，每工具↔一 REST 端点）
# 纯代理：daemon adapters/mcp.py 零业务规则；与 DISALLOWED_TOOLS 不重叠（内置 TaskCreate 已禁）。
COAGENTIA_MCP_TOOLS: tuple[str, ...] = (
    # M1（契约 E v1.0）
    "send_message",     # POST /channels/{id}/messages（M2 起支持 as_task 参数，B §9.4）
    "get_messages",     # GET  /channels/{id}/messages
    "get_thread",       # GET  /messages/{id}/thread
    "upload_file",      # POST /files
    "get_file",         # GET  /files/{id}/content
    "create_reminder",  # POST /reminders
    "cancel_reminder",  # DELETE /reminders/{id}
    "list_channels",    # GET  /channels
    "list_members",     # GET  /members
    # M2（契约 E v1.1）
    "list_tasks",       # GET  /tasks
    "get_task",         # GET  /tasks/{id}（TaskDetail）
    "claim_task",       # POST /tasks/{id}/claim（CLAIM_RACE 结构化透传）
    "unclaim_task",     # POST /tasks/{id}/unclaim（仅本人为 owner）
    "set_task_status",  # POST /tasks/{id}/status（TASK_TRANSITION_INVALID 透传）
    "search",           # GET  /search（三分组）
    # M7（契约 E v1.5）——零工具连胜止于 M6；R8「部署全员含 Agent」的通道兑现
    "trigger_deploy",   # POST /projects/{id}/deployments（DEPLOY_IN_PROGRESS/VALIDATION_FAILED
                        # /DAEMON_OFFLINE 结构化透传；请求体空，分支/commit 由 server 解析主干）
)
