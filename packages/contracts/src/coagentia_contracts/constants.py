"""常量目录：opId 前缀（契约 A §4.7）、活动文案（契约 E §7.2）、
禁用工具、诊断类型、规则号、schema 版本号。"""

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

# ---------------- 禁用工具（契约 E §2；终表实现期定 E §11.4）

DISALLOWED_TOOLS: tuple[str, ...] = ("EnterPlanMode", "ExitPlanMode")

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

# ---------------- 遥测缓冲默认值（契约 D §7，实现默认非协议形状）

BUFFER_DIAGNOSTICS_MAX = 10_000
BUFFER_USAGE_MAX = 100_000
BUFFER_DEPLOY_LOG_MAX_BYTES = 5 * 1024 * 1024
