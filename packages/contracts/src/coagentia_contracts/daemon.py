"""daemon ↔ server 线协议（契约 D 的代码化）：五种 kind 帧、指令/查询/上报三目录。

两端同栈共享本模块（00 §4.5 同栈红利：协议序列化零偏差）。
铁律（契约 D §1）：指令 at-least-once + ack、处理器按自然键幂等；不建指令 outbox，
离线补发靠 DB 事实源对账（D §4.4）。
"""

from enum import StrEnum
from typing import Literal

from pydantic import JsonValue

from coagentia_contracts.entities import (
    ContractModel,
    DetectedRuntime,
    MessagePublic,
)
from coagentia_contracts.enums import AgentStatus, InjectKind, Runtime, WakeReason
from coagentia_contracts.ids import TimestampZ, Ulid

DAEMON_PROTOCOL_V = 1
DAEMON_WS_PATH = "/api/daemon/ws"
ACK_TIMEOUT_SEC = 10  # instr ack / query reply 超时（契约 D §3）
RECONCILE_INTERVAL_SEC = 60  # 周期兜底扫描（契约 D §4.4）

# 关闭码（契约 D §2）
CLOSE_SUPERSEDED = 4001  # 同 key 新连接顶掉旧连接
CLOSE_PROTOCOL_MISMATCH = 4400


class FrameKind(StrEnum):
    INSTR = "instr"
    QUERY = "query"
    REPLY = "reply"
    REPORT = "report"
    ACK = "ack"
    PING = "ping"
    PONG = "pong"


class InstrType(StrEnum):
    """指令帧目录（契约 D §5；自然键与幂等语义见文档逐条标注）。"""

    AGENT_START = "agent.start"
    AGENT_STOP = "agent.stop"
    AGENT_RESTART = "agent.restart"
    AGENT_RESET_SESSION = "agent.reset_session"
    AGENT_RESET_FULL = "agent.reset_full"
    AGENT_WAKE = "agent.wake"
    AGENT_SLEEP = "agent.sleep"  # 登记不使用（M1-HANDOFF §2D 名目预留）
    MESSAGE_DELIVER = "message.deliver"
    MESSAGE_INJECT = "message.inject"  # S1 直投变体（预留 #3）
    WORKTREE_ENSURE = "worktree.ensure"  # M6
    WORKTREE_MERGE = "worktree.merge"  # M6
    WORKTREE_CLEANUP = "worktree.cleanup"  # M6
    PREVIEW_START = "preview.start"  # M7
    PREVIEW_STOP = "preview.stop"  # M7
    DEPLOY_RUN = "deploy.run"  # M7
    RUNTIME_RESCAN = "runtime.rescan"


class QueryType(StrEnum):
    """查询帧目录（契约 D §6；只读代理，超时 → DAEMON_OFFLINE）。"""

    HOME_TREE = "home.tree"
    HOME_FILE = "home.file"
    GIT_DIFF = "git.diff"  # M6


class ReportType(StrEnum):
    """上报帧目录（契约 D §7）。"""

    HELLO = "hello"
    AGENT_STATUS_CHANGED = "agent.status_changed"
    AGENT_ACTIVITY = "agent.activity"
    RUNTIMES_DETECTED = "runtimes.detected"
    DIAGNOSTICS_BATCH = "diagnostics.batch"
    USAGE_BATCH = "usage.batch"
    DEPLOY_LOG = "deploy.log"  # M7
    DEPLOY_FINISHED = "deploy.finished"  # M7
    PREVIEW_STATUS = "preview.status"  # M7
    WORKTREE_STATUS = "worktree.status"  # M6


class AckResult(StrEnum):
    DONE = "done"
    NOOP = "noop"  # 幂等命中已是目标态
    FAILED = "failed"


# ------------------------------------------------------------ 信封（契约 D §3）


class FrameError(ContractModel):
    code: str
    message: str


class InstrFrame(ContractModel):
    """at-least-once：ack 超时原帧原样重发（同 frame_id）；同 Agent 串行。"""

    v: int = DAEMON_PROTOCOL_V
    kind: Literal[FrameKind.INSTR] = FrameKind.INSTR
    frame_id: Ulid
    type: InstrType
    at: TimestampZ
    data: JsonValue


class QueryFrame(ContractModel):
    v: int = DAEMON_PROTOCOL_V
    kind: Literal[FrameKind.QUERY] = FrameKind.QUERY
    frame_id: Ulid
    type: QueryType
    at: TimestampZ
    data: JsonValue


class ReplyFrame(ContractModel):
    v: int = DAEMON_PROTOCOL_V
    kind: Literal[FrameKind.REPLY] = FrameKind.REPLY
    ref: Ulid  # 指向 query 的 frame_id
    data: JsonValue


class ReportFrame(ContractModel):
    v: int = DAEMON_PROTOCOL_V
    kind: Literal[FrameKind.REPORT] = FrameKind.REPORT
    frame_id: Ulid
    type: ReportType
    at: TimestampZ
    data: JsonValue


class AckFrame(ContractModel):
    v: int = DAEMON_PROTOCOL_V
    kind: Literal[FrameKind.ACK] = FrameKind.ACK
    ref: Ulid
    result: AckResult
    error: FrameError | None = None
    data: JsonValue | None = None


# ------------------------------------------------------------ 指令 data（契约 D §5）


class AgentBoot(ContractModel):
    """启动所需全量配置快照（agent.start/restart/reset_*；"下次启动生效"的生效载体）。"""

    agent_member_id: Ulid
    name: str
    runtime: Runtime
    model: str
    home_path: str
    skills: list[str] = []


class AgentStartData(ContractModel):
    agent: AgentBoot


class AgentRefData(ContractModel):
    agent_member_id: Ulid


class WakeRefs(ContractModel):
    message_ids: list[Ulid] | None = None
    reminder_id: Ulid | None = None
    node_id: Ulid | None = None


class AgentWakeData(ContractModel):
    agent_member_id: Ulid
    reason: WakeReason
    refs: WakeRefs


class MessageDeliverData(ContractModel):
    """ack(done) 后 server 写该 Agent read_positions（投递游标即已读位置，D §8.3）。"""

    agent_member_id: Ulid
    channel_id: Ulid
    messages: list[MessagePublic]
    thread_root_id: Ulid | None = None


class InjectSource(ContractModel):
    kind: InjectKind
    ref: str | None = None


class MessageInjectData(ContractModel):
    """S1：定向单 Agent、不进频道流、不动 read_positions；发出与 ack 各写一条诊断。"""

    agent_member_id: Ulid
    body: str
    source: InjectSource
    diagnostic_type: str


class WorktreeEnsureData(ContractModel):
    task_id: Ulid
    project_id: Ulid
    repo_path: str
    branch: str


class WorktreeMergeData(ContractModel):
    task_id: Ulid
    merge_plan_ref: str | None = None


class WorktreeCleanupData(ContractModel):
    task_id: Ulid


class PreviewStartData(ContractModel):
    preview_session_id: Ulid
    task_id: Ulid
    worktree_path: str
    dev_command: str


class PreviewStopData(ContractModel):
    preview_session_id: Ulid


class DeployRunData(ContractModel):
    deployment_id: Ulid
    repo_path: str
    command: str
    branch: str
    commit_hash: str | None = None


class RuntimeRescanData(ContractModel):
    pass


# ------------------------------------------------------------ 查询 data 与 reply（契约 D §6）


class HomeTreeQuery(ContractModel):
    agent_member_id: Ulid
    path: str  # daemon 与 server 双重校验：规范化后必须在 home_path 内


class HomeTreeEntry(ContractModel):
    name: str
    kind: Literal["dir", "file"]
    size_bytes: int
    mtime: TimestampZ


class HomeTreeReply(ContractModel):
    entries: list[HomeTreeEntry]


class HomeFileQuery(ContractModel):
    agent_member_id: Ulid
    path: str


class HomeFileTextReply(ContractModel):
    kind: Literal["text"] = "text"
    content: str  # 上限 1MB
    truncated: bool = False


class HomeFileBinaryReply(ContractModel):
    kind: Literal["binary"] = "binary"
    size_bytes: int
    mime: str | None = None


class GitDiffQuery(ContractModel):
    project_id: Ulid
    repo_path: str
    task_id: Ulid
    base: str | None = None


# DiffPayload：M6 随 W3 定稿，contracts 预留名（JsonValue 占位）


# ------------------------------------------------------------ 上报 data（契约 D §7）


class DaemonAgentState(ContractModel):
    """hello 的真实进程表条目。"""

    agent_member_id: Ulid
    status: AgentStatus
    source_session: str | None = None


class BufferedCounts(ContractModel):
    diagnostics: int = 0
    usage: int = 0


class DaemonHelloData(ContractModel):
    daemon_version: str
    os: str
    arch: str
    detected_runtimes: list[DetectedRuntime]
    agents: list[DaemonAgentState]
    buffered: BufferedCounts


class DaemonHelloAckData(ContractModel):
    """server 对 hello 的应答（握手第 3 步，契约 D §4.1）。"""

    protocol_v: int
    server_version: str
    computer_id: Ulid
    workspace_id: Ulid
    heartbeat_sec: int


class AgentStatusChangedData(ContractModel):
    """agents.status 列的唯一写入方（契约 D §7）。"""

    agent_member_id: Ulid
    status: AgentStatus
    error_detail: str | None = None


class DaemonAgentActivityData(ContractModel):
    agent_member_id: Ulid
    detail: str


class RuntimesDetectedData(ContractModel):
    runtimes: list[DetectedRuntime]


class DiagnosticEventIn(ContractModel):
    """诊断上行条目：无 seq（server 落库赋）、无 workspace_id（server 由 computer 富化）。"""

    agent_member_id: Ulid | None = None
    type: str
    channel_id: Ulid | None = None
    task_id: Ulid | None = None
    batch_id: Ulid | None = None
    payload: JsonValue
    at: TimestampZ


class DiagnosticsBatchData(ContractModel):
    events: list[DiagnosticEventIn]  # ≤50 条/批


class TokenUsageEventIn(ContractModel):
    """usage 上行条目：id = 适配器 ULID（exactly-once 去重根基，契约 E §7.4）；
    thread_root_id 为归属提示，server 富化为 task_id 后不落列。"""

    id: Ulid
    agent_member_id: Ulid
    channel_id: Ulid | None = None
    thread_root_id: Ulid | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    source_session: str | None = None
    reported_at: TimestampZ


class UsageBatchData(ContractModel):
    events: list[TokenUsageEventIn]


class DeployLogReportData(ContractModel):
    deployment_id: Ulid
    chunk_seq: int  # 单调，server 按已收最大值去重
    lines: list[str]


class DeployFinishedData(ContractModel):
    deployment_id: Ulid
    status: Literal["success", "failed"]
    exit_code: int
    url: str | None = None


class PreviewStatusData(ContractModel):
    preview_session_id: Ulid
    status: Literal["starting", "running", "recycled", "failed"]
    port: int | None = None


class WorktreeStatusData(ContractModel):
    task_id: Ulid
    status: Literal["active", "merged", "conflicted", "cleaned"]
    branch: str
    path: str
