/* eslint-disable */
/**
 * 生成物，禁止手改（pnpm gen 重新生成）。
 * 源 = packages/contracts 的 Pydantic 模型（契约 A–E 的唯一源）。
 */

export type Id = string;
export type Statement = string;
/**
 * TaskPlan.acceptance_criteria[].verify_by（PRD §4.3 v1）。
 */
export type VerifyBy = 'command' | 'inspect' | 'manual';
export type VerifyRef = string;
export type Code = string;
export type Message = string;
export type Kind = 'ack';
export type Ref = string;
export type AckResult = 'done' | 'noop' | 'failed';
export type V = number;
export type ActorMemberId = string | null;
export type ChannelId = string | null;
export type CreatedAt = string;
export type DoneAt = string | null;
export type Id1 = string;
export type ActivityKind = 'mention' | 'dm' | 'silence_escalation' | 'held_escalation' | 'fail_closed' | 'system';
export type MemberId = string;
export type MessageId = string | null;
export type TaskId = string | null;
export type WorkspaceId = string;
export type ItemId = string;
export type ChannelId1 = string | null;
export type CreatedAt1 = string;
export type DoneAt1 = string | null;
export type Id2 = string;
export type MemberId1 = string;
export type MessageId1 = string | null;
export type TaskId1 = string | null;
export type WorkspaceId1 = string;
export type Detail = string;
export type MemberId2 = string;
export type AgentMemberId = string;
export type HomePath = string;
export type Model = string;
export type Name = string;
export type Runtime = 'claude_code' | 'codex';
export type Skills = string[];
export type ComputerId = string;
export type Description = string;
export type Model1 = string;
export type Name1 = string;
export type RoleTemplateKey = string | null;
export type Runtime1 = string;
export type Description1 = string | null;
export type Model2 = string | null;
export type Runtime2 = string | null;
export type ComputerId1 = string;
export type CreatedByMemberId = string;
export type Description2 = string;
export type HomePath1 = string;
export type MemberId3 = string;
export type Model3 = string;
export type RoleTemplateKey1 = string | null;
/**
 * PRD §4.4 五态；持久化"最后已知态"（契约 A agents 表 / 契约 D §2 级联裁决）。
 */
export type AgentStatus = 'starting' | 'idle' | 'busy' | 'error' | 'offline';
export type AgentMemberId1 = string;
export type Builtin = boolean;
export type DescriptionPrefill = string;
export type Id3 = string;
export type Key = string;
export type Name2 = string;
export type JsonValue = unknown;
export type Builtin1 = boolean;
export type DescriptionPrefill1 = string;
export type Id4 = string;
export type Key1 = string;
export type Name3 = string;
export type ComputerId2 = string;
export type CreatedByMemberId1 = string;
export type Description3 = string;
export type HomePath2 = string;
export type MemberId4 = string;
export type Model4 = string;
export type RoleTemplateKey2 = string | null;
/**
 * PRD §4.4 五态；持久化"最后已知态"（契约 A agents 表 / 契约 D §2 级联裁决）。
 */
export type AgentStatus1 = 'starting' | 'idle' | 'busy' | 'error' | 'offline';
export type AgentMemberId2 = string;
export type GrantedAt = string;
export type GrantedByMemberId = string;
export type Skill = string;
export type AgentMemberId3 = string;
export type GrantedAt1 = string;
export type GrantedByMemberId1 = string;
export type Skill1 = string;
export type AgentMemberId4 = string;
export type ErrorDetail = string | null;
/**
 * PRD §4.4 五态；持久化"最后已知态"（契约 A agents 表 / 契约 D §2 级联裁决）。
 */
export type AgentStatus2 = 'starting' | 'idle' | 'busy' | 'error' | 'offline';
export type AgentMemberId5 = string;
/**
 * 四触发器（PRD §4.5 / 契约 D §5.1）。
 */
export type WakeReason = 'channel_message' | 'mention' | 'reminder' | 'canvas_activation';
export type MessageIds = string[] | null;
export type NodeId = string | null;
export type ReminderId = string | null;
export type Title = string | null;
export type MemberId5 = string | null;
export type Diagnostics = number;
export type Usage = number;
export type BaselineHash = string;
export type BaselineVersion = number;
export type CanvasId = string;
export type BaselineHash1 = string;
export type BaselineVersion1 = number;
export type ChannelId2 = string;
export type Id5 = string;
export type UpdatedAt = string;
export type WorkspaceId2 = string;
export type CanvasId1 = string;
export type FromNodeId = string;
export type Id6 = string;
export type ToNodeId = string;
export type Edges = CanvasEdgePublic[];
export type CanvasId2 = string;
export type Command = string | null;
export type CreatedAt2 = string;
export type Id7 = string;
export type IsSummary = boolean;
export type CanvasNodeKind = 'agent' | 'system';
export type PosX = number;
export type PosY = number;
export type SystemAction = 'merge' | 'check';
export type SystemNodeStatus = 'idle' | 'running' | 'success' | 'failed';
export type TaskId2 = string | null;
/**
 * canvas_nodes.upstream_policy（契约 A v1.0.12 / W9 部分失败）：被配置节点对其前驱集合的
 * satisfied 双档判定。strict = 每个前驱须 Done（agent）/success（system）——现状语义原样；
 * partial = 每个前驱**到达终态**即放行（agent∈{done,closed} / system∈{success,failed 不可
 * retry}）。仍要求全部前驱到达终态（非「任一完成」），只是不限 Done——防单点卡死全 DAG。
 * 汇总节点落地默认 partial（裁决 #5），普通节点默认 strict；人类经 patch_node 改档（O9 面）。
 */
export type UpstreamPolicy = 'strict' | 'partial';
export type Nodes = CanvasNodePublic[];
export type EdgeId = string;
export type CanvasId3 = string;
export type FromNodeId1 = string;
export type Id8 = string;
export type ToNodeId1 = string;
export type CanvasId4 = string;
export type NodeId1 = string;
export type X = number;
export type Y = number;
export type Positions = NodePosition[];
export type BaselineHash2 = string;
export type BaselineVersion2 = number;
export type NodeId2 = string;
export type CanvasId5 = string;
export type Command1 = string | null;
export type CreatedAt3 = string;
export type Id9 = string;
export type IsSummary1 = boolean;
export type PosX1 = number;
export type PosY1 = number;
export type TaskId3 = string | null;
/**
 * canvas_nodes.upstream_policy（契约 A v1.0.12 / W9 部分失败）：被配置节点对其前驱集合的
 * satisfied 双档判定。strict = 每个前驱须 Done（agent）/success（system）——现状语义原样；
 * partial = 每个前驱**到达终态**即放行（agent∈{done,closed} / system∈{success,failed 不可
 * retry}）。仍要求全部前驱到达终态（非「任一完成」），只是不限 Done——防单点卡死全 DAG。
 * 汇总节点落地默认 partial（裁决 #5），普通节点默认 strict；人类经 patch_node 改档（O9 面）。
 */
export type UpstreamPolicy1 = 'strict' | 'partial';
export type BaselineHash3 = string;
export type BaselineVersion3 = number;
export type ChannelId3 = string;
export type Id10 = string;
export type UpdatedAt1 = string;
export type WorkspaceId3 = string;
export type Description4 = string;
export type IsPrivate = boolean;
export type MemberIds = string[];
export type Name4 = string;
export type ArchivedAt = string | null;
export type CreatedAt4 = string;
export type DecompMode = 'draft' | 'direct';
export type DecompNodeLimit = number;
export type Description5 = string;
export type DmKey = string | null;
export type HeldEscalateN = number;
export type HeldReevalMin = number;
export type Id11 = string;
export type IsPrivate1 = boolean;
export type JointRef = string | null;
export type ChannelKind = 'channel' | 'dm';
export type Name5 = string | null;
export type NextTaskNumber = number;
export type OrchEscalation = boolean;
export type RemindEscalation = boolean;
export type RemindInprogH = number;
export type RemindReviewH = number;
export type RemindTodoH = number;
export type WorkspaceId4 = string;
export type MemberId6 = string;
export type ChannelId4 = string;
export type JoinedAt = string;
export type MemberId7 = string;
export type ChannelId5 = string;
export type JoinedAt1 = string;
export type MemberId8 = string;
export type ChannelId6 = string;
export type MemberId9 = string;
export type ChannelId7 = string;
export type MemberId10 = string;
export type NotificationMode = 'all' | 'mentions' | 'mute';
export type ChannelId8 = string;
export type MemberId11 = string;
export type NotificationMode1 = 'all' | 'mentions' | 'mute';
export type DecompMode1 = string | null;
export type DecompNodeLimit1 = number | null;
export type Description6 = string | null;
export type HeldEscalateN1 = number | null;
export type HeldReevalMin1 = number | null;
export type IsPrivate2 = boolean | null;
export type OrchEscalation1 = boolean | null;
export type RemindEscalation1 = boolean | null;
export type RemindInprogH1 = number | null;
export type RemindReviewH1 = number | null;
export type RemindTodoH1 = number | null;
export type ChannelId9 = string;
export type ProjectId = string;
export type ChannelId10 = string;
export type ProjectId1 = string;
export type ArchivedAt1 = string | null;
export type CreatedAt5 = string;
export type DecompMode2 = 'draft' | 'direct';
export type DecompNodeLimit2 = number;
export type Description7 = string;
export type DmKey1 = string | null;
export type HeldEscalateN2 = number;
export type HeldReevalMin2 = number;
export type Id12 = string;
export type IsPrivate3 = boolean;
export type JointRef1 = string | null;
export type Name6 = string | null;
export type NextTaskNumber1 = number;
export type OrchEscalation2 = boolean;
export type RemindEscalation2 = boolean;
export type RemindInprogH2 = number;
export type RemindReviewH2 = number;
export type RemindTodoH2 = number;
export type WorkspaceId5 = string;
export type Items = ChannelPublic[];
export type NotificationSettings = ChannelNotificationSettingPublic[];
export type ChannelId11 = string;
export type LastReadAt = string;
export type LastReadMessageId = string;
export type MemberId12 = string;
export type ReadPositions = ReadPositionPublic[];
export type ExitCode = number;
export type NodeId3 = string;
export type OutputTail = string;
export type RunId = string;
export type Status = 'success' | 'failed';
export type Command2 = string;
export type NodeId4 = string;
export type ProjectId2 = string;
export type RepoPath = string;
export type RunId1 = string;
export type Name7 = string;
export type ApiKey = string;
export type CommandLine = string;
export type Arch = string | null;
export type CreatedAt6 = string;
export type DaemonVersion = string | null;
export type Installed = boolean;
export type Models = string[];
export type Skills1 = string[];
export type DetectedRuntimes = DetectedRuntime[];
export type Id13 = string;
export type LastSeenAt = string | null;
export type Name8 = string;
export type Os = string | null;
export type ComputerStatus = 'connected' | 'offline';
export type WorkspaceId6 = string;
export type Name9 = string;
export type ApiKeyHash = string;
export type Arch1 = string | null;
export type CreatedAt7 = string;
export type DaemonVersion1 = string | null;
export type DetectedRuntimes1 = DetectedRuntime[];
export type Id14 = string;
export type LastSeenAt1 = string | null;
export type Name10 = string;
export type Os1 = string | null;
export type ComputerStatus1 = 'connected' | 'offline';
export type WorkspaceId7 = string;
export type ContractKind = 'task_plan' | 'task_handoff' | 'loop_contract';
export type AgentMemberId6 = string;
export type Title1 = string | null;
export type AgentMemberId7 = string;
export type Detail1 = string;
export type AgentMemberId8 = string;
export type SourceSession = string | null;
export type ComputerId3 = string;
export type HeartbeatSec = number;
export type ProtocolV = number;
export type ServerVersion = string;
export type WorkspaceId8 = string;
export type Agents = DaemonAgentState[];
export type Arch2 = string;
export type BootNonce = string | null;
export type DaemonVersion2 = string;
export type DetectedRuntimes2 = DetectedRuntime[];
export type Os2 = string;
export type LogTail = string | null;
export type Port = number | null;
export type PreviewSessionId = string;
export type Status1 = 'starting' | 'running' | 'recycled' | 'failed';
export type Previews = PreviewStatusData[];
export type TaskId4 = string | null;
export type Text = string | null;
/**
 * TaskHandoff.deliverables[].kind（PRD §4.3 v1）。
 */
export type DeliverableKind = 'file' | 'dir' | 'url' | 'artifact';
export type Path = string;
export type DeploymentId = string;
export type ExitCode1 = number | null;
export type Status2 = 'success' | 'failed';
export type Url = string | null;
export type ChunkSeq = number;
export type DeploymentId1 = string;
export type Lines = string[];
export type Branch = string;
export type Command3 = string;
export type CommitHash = string | null;
export type DeploymentId2 = string;
export type RepoPath1 = string;
export type Branch1 = string;
export type Command4 = string;
export type CommitHash1 = string | null;
export type ExitCode2 = number | null;
export type FinishedAt = string | null;
export type Id15 = string;
export type ProjectId3 = string;
export type StartedAt = string | null;
export type DeploymentStatus = 'queued' | 'running' | 'success' | 'failed';
export type TaskIds = string[];
export type Reporting = number;
export type Total = number;
export type CacheReadTokens = number;
export type CacheWriteTokens = number;
export type Events = number;
export type InputTokens = number;
export type OutputTokens = number;
export type TriggeredByMemberId = string;
export type Url1 = string | null;
export type WorkspaceId9 = string;
export type ChunkSeq1 = number;
export type DeploymentId3 = string;
export type Lines1 = string[];
export type Lines2 = string[];
export type NextAfter = number | null;
export type Truncated = boolean;
export type Branch2 = string;
export type Command5 = string;
export type CommitHash2 = string | null;
export type ExitCode3 = number | null;
export type FinishedAt1 = string | null;
export type Id16 = string;
export type LogPath = string | null;
export type ProjectId4 = string;
export type StartedAt1 = string | null;
export type TriggeredByMemberId1 = string;
export type Url2 = string | null;
export type WorkspaceId10 = string;
export type AgentMemberId9 = string;
export type Events1 = JsonValue[];
export type AgentMemberId10 = string | null;
export type At = string;
export type BatchId = string | null;
export type ChannelId12 = string | null;
export type TaskId5 = string | null;
export type Type = string;
export type AgentMemberId11 = string | null;
export type BatchId1 = string | null;
export type ChannelId13 = string | null;
export type CreatedAt8 = string;
export type Seq = number;
export type TaskId6 = string | null;
export type Type1 = string;
export type WorkspaceId11 = string;
export type AgentMemberId12 = string | null;
export type BatchId2 = string | null;
export type ChannelId14 = string | null;
export type CreatedAt9 = string;
export type Seq1 = number;
export type TaskId7 = string | null;
export type Type2 = string;
export type WorkspaceId12 = string;
export type Events2 = DiagnosticEventIn[];
export type Additions = number;
export type Deletions = number;
export type OldPath = string | null;
export type Patch = string;
export type PatchTruncated = boolean;
export type Path1 = string;
export type Status3 = 'added' | 'modified' | 'deleted' | 'renamed';
export type BaseRef = string;
export type Files = DiffFile[];
export type FilesTruncated = boolean;
export type HeadRef = string;
export type TotalAdditions = number;
export type TotalDeletions = number;
export type MemberId13 = string;
export type Adjustments = JsonValue[];
export type ProposalId = string;
export type FromNodeId2 = string;
export type ToNodeId2 = string;
export type At1 = string;
export type ChannelId15 = string | null;
export type Key2 = string;
export type Seq2 = number;
/**
 * 权威事件目录 = 契约 C §6/§7（+§8 的 diagnostic.appended 订阅流）。
 */
export type EventType =
  | 'sys.hello'
  | 'sys.pong'
  | 'workspace.updated'
  | 'presence.changed'
  | 'agent.activity'
  | 'member.created'
  | 'member.updated'
  | 'member.removed'
  | 'agent.updated'
  | 'computer.connected'
  | 'computer.disconnected'
  | 'computer.updated'
  | 'channel.created'
  | 'channel.updated'
  | 'channel.deleted'
  | 'channel.member_added'
  | 'channel.member_removed'
  | 'message.created'
  | 'read.updated'
  | 'task.created'
  | 'task.updated'
  | 'task_contract.created'
  | 'task_contract.updated'
  | 'activity.created'
  | 'activity.done'
  | 'token_usage.reported'
  | 'canvas.node_added'
  | 'canvas.node_updated'
  | 'canvas.node_removed'
  | 'canvas.edge_added'
  | 'canvas.edge_removed'
  | 'canvas.layout_updated'
  | 'canvas.baseline_advanced'
  | 'held_draft.created'
  | 'held_draft.updated'
  | 'reminder.created'
  | 'reminder.updated'
  | 'worktree.updated'
  | 'preview.updated'
  | 'deployment.created'
  | 'deployment.updated'
  | 'deployment.log'
  | 'draft.presented'
  | 'draft.adjusted'
  | 'draft.confirmed'
  | 'draft.rejected'
  | 'draft.superseded'
  | 'delta.proposed'
  | 'delta.adjusted'
  | 'delta.confirmed'
  | 'delta.rejected'
  | 'landing.started'
  | 'landing.completed'
  | 'landing.fail_closed'
  | 'proposal.updated'
  | 'diagnostic.appended';
export type V1 = number;
export type WorkspaceId13 = string;
/**
 * 错误码目录全集（契约 B §3；新增须先登记进契约文档）。
 */
export type ErrorCode =
  | 'VALIDATION_FAILED'
  | 'TASK_IN_DM'
  | 'NOT_TOP_LEVEL_MESSAGE'
  | 'CLAIM_RACE'
  | 'HANDOFF_INCOMPLETE'
  | 'TASK_TRANSITION_INVALID'
  | 'GRAPH_CYCLE'
  | 'STALE_CONFIRM'
  | 'DELTA_BASE_MISMATCH'
  | 'NODE_ACTIVE'
  | 'NO_ORCHESTRATOR'
  | 'IDEMPOTENCY_MISMATCH'
  | 'NAME_TAKEN'
  | 'CHANNEL_NOT_EMPTY'
  | 'CHANNEL_ARCHIVED'
  | 'COMPUTER_HAS_AGENTS'
  | 'COMPUTER_HAS_PROJECTS'
  | 'WORKSPACE_EXISTS'
  | 'DEPLOY_IN_PROGRESS'
  | 'DAEMON_OFFLINE'
  | 'FILE_TOO_LARGE'
  | 'HELD_DRAFT_RESOLVED'
  | 'NOTIF_IN_DM'
  | 'TEMPLATE_CANVAS_NOT_READY'
  | 'SYSTEM_NODE_NOT_RETRYABLE'
  | 'TEMPLATE_BUILTIN_IMMUTABLE'
  | 'PROJECT_IN_USE'
  | 'PERMISSION_DENIED'
  | 'NOT_FOUND';
export type Message1 = string;
export type Rule = string | null;
export type Conclusion = string;
export type Ref1 = string;
/**
 * TaskHandoff.evidence[].type（PRD §4.3 v1）。
 */
export type EvidenceType = 'test' | 'command' | 'screenshot' | 'log';
export type ChannelId16 = string | null;
export type CreatedAt10 = string;
export type Id17 = string;
export type MessageId2 = string | null;
export type Mime = string;
export type Name11 = string;
export type Sha256 = string;
export type SizeBytes = number;
export type WorkspaceId14 = string;
export type ChannelId17 = string;
export type CreatedAt11 = string;
export type Id18 = string;
export type MessageId3 = string;
export type Mime1 = string;
export type Name12 = string;
export type Sha2561 = string;
export type SizeBytes1 = number;
export type StoredPath = string;
export type WorkspaceId15 = string;
export type Base = string | null;
export type ProjectId5 = string;
export type RepoPath2 = string;
export type TaskId8 = string;
export type Title2 = string | null;
export type AgentMemberId13 = string;
export type ChannelId18 = string;
export type CreatedAt12 = string;
export type DraftBody = string;
export type EscalatedAt = string | null;
export type FileIds = string[] | null;
export type HeldCount = number;
export type Id19 = string;
export type NextReevalAt = string;
export type TotalUnread = number;
export type UnreadMessageIds = string[];
export type HeldResolution = 'released' | 'discarded' | 'reevaluated';
export type ResolvedAt = string | null;
export type ResolvedByMemberId = string | null;
export type HeldDraftStatus = 'held' | 'released' | 'discarded' | 'reevaluating' | 'resolved';
export type ThreadRootId = string | null;
export type WorkspaceId16 = string;
export type AuthorMemberId = string | null;
export type Body = string;
/**
 * 结构化卡片锚点消息（契约 A messages.card_kind；卡片 = 不可变锚点 + 实体状态走 WS）。
 */
export type CardKind = 'proposal' | 'held_draft' | 'deployment' | 'fail_closed' | 'handoff_delivery' | 'merge_conflict';
export type CardRef = string | null;
export type ChannelId19 = string;
export type CreatedAt13 = string;
export type Files1 = FilePublic[] | null;
export type Id20 = string;
export type MessageKind = 'user' | 'system';
export type ThreadRootId1 = string | null;
export type WorkspaceId17 = string;
export type AgentMemberId14 = string;
export type ChannelId20 = string;
export type CreatedAt14 = string;
export type DraftBody1 = string;
export type EscalatedAt1 = string | null;
export type FileIds1 = string[] | null;
export type HeldCount1 = number;
export type Id21 = string;
export type NextReevalAt1 = string;
export type ResolvedAt1 = string | null;
export type ResolvedByMemberId1 = string | null;
export type HeldDraftStatus1 = 'held' | 'released' | 'discarded' | 'reevaluating' | 'resolved';
export type ThreadRootId2 = string | null;
export type WorkspaceId18 = string;
export type Kind1 = 'binary';
export type Mime2 = string | null;
export type SizeBytes2 = number;
export type AgentMemberId15 = string;
export type Path2 = string;
export type Content = string;
export type Kind2 = 'text';
export type Truncated1 = boolean;
export type Kind3 = 'dir' | 'file';
export type Mtime = string;
export type Name13 = string;
export type SizeBytes3 = number;
export type AgentMemberId16 = string;
export type Path3 = string;
export type Entries = HomeTreeEntry[];
/**
 * S1 直投的来源域（契约 D §5.2）。
 */
export type InjectKind = 'repair' | 'guard_feedback' | 'contract_draft_request' | 'system';
export type Ref2 = string | null;
export type ChannelId21 = string;
export type ConfirmedBy = string;
export type ContentHash = string;
export type CreatedAt15 = string;
export type DoneAt2 = string | null;
export type Id22 = string;
export type LandingBatchKind = 'decomp' | 'tmpl' | 'delta';
export type SourceRef = string;
export type LandingBatchStatus = 'running' | 'done' | 'fail_closed';
export type WorkspaceId19 = string;
export type ChannelId22 = string;
export type CreatedAt16 = string;
export type CreatedByMemberId2 = string;
export type Id23 = string;
export type TaskLevel = 'l1' | 'l2';
export type Number = number;
export type OwnerMemberId = string | null;
export type ProjectId6 = string | null;
export type RootMessageId = string;
export type SilenceOverrideH = number | null;
export type TaskStatus = 'todo' | 'in_progress' | 'in_review' | 'done' | 'closed';
export type StatusChangedAt = string;
export type Title3 = string;
export type WorkspaceId20 = string;
export type WritesCode = boolean;
export type Tasks = TaskPublic[];
export type At2 = string;
export type FrameId = string;
export type Kind4 = 'instr';
/**
 * 指令帧目录（契约 D §5；自然键与幂等语义见文档逐条标注）。
 */
export type InstrType =
  | 'agent.start'
  | 'agent.stop'
  | 'agent.restart'
  | 'agent.reset_session'
  | 'agent.reset_full'
  | 'agent.wake'
  | 'agent.sleep'
  | 'message.deliver'
  | 'message.inject'
  | 'worktree.ensure'
  | 'worktree.merge'
  | 'worktree.cleanup'
  | 'check.run'
  | 'preview.start'
  | 'preview.stop'
  | 'deploy.run'
  | 'runtime.rescan';
export type V2 = number;
export type ChannelId23 = string;
export type ConfirmedBy1 = string;
export type ContentHash1 = string;
export type CreatedAt17 = string;
export type DoneAt3 = string | null;
export type Id24 = string;
export type SourceRef1 = string;
export type LandingBatchStatus1 = 'running' | 'done' | 'fail_closed';
export type WorkspaceId21 = string;
export type NodeId5 = string;
export type X1 = number;
export type Y1 = number;
export type Positions1 = LayoutPositionIn[];
export type ActorMemberId1 = string | null;
export type BatchId3 = string | null;
export type CreatedAt18 = string;
export type Kind5 = string;
export type OpId = string;
export type RequestHash = string;
export type Seq3 = number;
export type ActorMemberId2 = string | null;
export type BatchId4 = string | null;
export type CreatedAt19 = string;
export type Kind6 = string;
export type OpId1 = string;
export type RequestHash1 = string;
export type Seq4 = number;
/**
 * `POST /agents/{id}/lifecycle`（契约 B §4.3；三档重置枚举只定义这一次）。
 */
export type LifecycleAction = 'start' | 'stop' | 'restart' | 'reset_session' | 'reset_full';
export type MaxRetries = number;
export type MaxRuntimeMin = number;
export type Cadence = string;
export type Escalation = string;
export type Tools = string[];
export type Verification = string[];
export type Version = 'coagentia.loop-contract.v1';
export type CreatedAt20 = string;
export type Id25 = string;
export type MemberKind = 'human' | 'agent';
export type Name14 = string;
export type RemovedAt = string | null;
export type MemberRole = 'member' | 'admin' | 'owner';
export type WorkspaceId22 = string;
export type MemberRole1 = 'member' | 'admin' | 'owner';
export type CreatedAt21 = string;
export type Id26 = string;
export type Name15 = string;
export type RemovedAt1 = string | null;
export type MemberRole2 = 'member' | 'admin' | 'owner';
export type WorkspaceId23 = string;
export type Body1 = string;
export type FileIds2 = string[];
export type ThreadRootId3 = string | null;
export type AgentMemberId17 = string;
export type ChannelId24 = string;
export type Messages = MessagePublic[];
export type ThreadRootId4 = string | null;
export type AgentMemberId18 = string;
export type Body2 = string;
export type DiagnosticType = string;
export type MemberId14 = string;
export type MessageId4 = string;
export type MemberId15 = string;
export type MessageId5 = string;
export type AuthorMemberId1 = string | null;
export type Body3 = string;
export type CardRef1 = string | null;
export type ChannelId25 = string;
export type CreatedAt22 = string;
export type Id27 = string;
export type MessageKind1 = 'user' | 'system';
export type ThreadRootId5 = string | null;
export type WorkspaceId24 = string;
export type MessageId6 = string;
export type TaskId9 = string;
export type MessageId7 = string;
export type TaskId10 = string;
export type Command6 = string | null;
export type ProjectId7 = string | null;
/**
 * @minItems 1
 */
export type AcceptanceCriteria = [AcceptanceCriterion, ...AcceptanceCriterion[]];
export type DefaultsDecided = string[];
export type Goal = string;
export type OutOfScope = string[];
export type Version1 = 'coagentia.task-plan.v1';
export type Title4 = string;
export type UpstreamNodeIds = string[] | null;
export type WritesCode1 = boolean;
export type Command7 = string | null;
export type Title5 = string | null;
export type NotificationMode2 = 'all' | 'mentions' | 'mute';
export type Items1 = unknown[];
export type NextCursor = string | null;
export type Type3 = 'ping';
export type MemberId16 = string;
/**
 * `GET /presence` 与 `presence.changed` 的合并视图值域：人类 online/offline，Agent 五态。
 */
export type PresenceStatus = 'online' | 'offline' | 'starting' | 'idle' | 'busy' | 'error';
export type BusyDetail = string | null;
export type MemberId17 = string;
export type Items2 = PresenceEntry[];
export type FailLogTail = string | null;
export type Id28 = string;
export type LastActiveAt = string | null;
export type Port1 = number | null;
export type RecycledAt = string | null;
export type StartedAt2 = string;
export type PreviewStatus = 'starting' | 'running' | 'recycled' | 'failed';
export type TaskId11 = string;
export type WorkspaceId25 = string;
export type WorktreeId = string;
export type FailLogTail1 = string | null;
export type Id29 = string;
export type LastActiveAt1 = string | null;
export type Port2 = number | null;
export type RecycledAt1 = string | null;
export type StartedAt3 = string;
export type TaskId12 = string;
export type WorkspaceId26 = string;
export type WorktreeId1 = string;
export type DevCommand = string;
export type PreviewSessionId1 = string;
export type TaskId13 = string;
export type WorktreePath = string;
export type PreviewSessionId2 = string;
export type ProjectId8 = string;
export type ComputerId4 = string;
export type DeployCommand = string | null;
export type DevCommand1 = string | null;
export type Name16 = string;
export type PreviewIdleMin = number | null;
export type RepoPath3 = string;
export type WorktreeKeepDays = number | null;
export type ComputerId5 = string | null;
export type DeployCommand1 = string | null;
export type DevCommand2 = string | null;
export type Name17 = string | null;
export type PreviewIdleMin1 = number | null;
export type RepoPath4 = string | null;
export type WorktreeKeepDays1 = number | null;
export type ChannelIds = string[];
export type ComputerId6 = string;
export type CreatedAt23 = string;
export type DeployCommand2 = string | null;
export type DevCommand3 = string | null;
export type Id30 = string;
export type Name18 = string;
export type PreviewIdleMin2 = number;
export type RepoPath5 = string;
export type WorkspaceId27 = string;
export type WorktreeKeepDays2 = number;
export type ComputerId7 = string;
export type CreatedAt24 = string;
export type DeployCommand3 = string | null;
export type DevCommand4 = string | null;
export type Id31 = string;
export type Name19 = string;
export type PreviewIdleMin3 = number;
export type RepoPath6 = string;
export type WorkspaceId28 = string;
export type WorktreeKeepDays3 = number;
export type Adjustments1 = JsonValue[];
export type BaselineHash4 = string;
export type BaselineVersion4 = number;
export type ProposalHash = string;
export type RemovedOps = number[];
export type Adjustments2 = JsonValue[];
export type BaseHash = string | null;
export type ChannelId26 = string;
export type CreatedAt25 = string;
export type Id32 = string;
export type ProposalKind = 'full' | 'delta';
export type LandedHash = string | null;
export type ProposalHash1 = string;
export type ProposedByMemberId = string;
export type RepairCount = number;
export type Revision = number;
export type SourceTaskId = string;
export type ProposalStatus =
  | 'drafting'
  | 'validating'
  | 'repairing'
  | 'awaiting_confirm'
  | 'landing'
  | 'landed'
  | 'superseded'
  | 'rejected'
  | 'failed';
export type UpdatedAt2 = string;
export type WorkspaceId29 = string;
export type ProposalId1 = string;
export type Reason = string | null;
export type Adjustments3 = JsonValue[];
export type BaseHash1 = string | null;
export type ChannelId27 = string;
export type CreatedAt26 = string;
export type Id33 = string;
export type ProposalKind1 = 'full' | 'delta';
export type LandedHash1 = string | null;
export type ProposalHash2 = string;
export type ProposedByMemberId1 = string;
export type RepairCount1 = number;
export type Revision1 = number;
export type SourceTaskId1 = string;
export type ProposalStatus1 =
  | 'drafting'
  | 'validating'
  | 'repairing'
  | 'awaiting_confirm'
  | 'landing'
  | 'landed'
  | 'superseded'
  | 'rejected'
  | 'failed';
export type UpdatedAt3 = string;
export type WorkspaceId30 = string;
export type At3 = string;
export type FrameId1 = string;
export type Kind7 = 'query';
/**
 * 查询帧目录（契约 D §6；只读代理，超时 → DAEMON_OFFLINE）。
 */
export type QueryType = 'home.tree' | 'home.file' | 'git.diff';
export type V3 = number;
export type LastReadMessageId1 = string;
export type ChannelId28 = string;
export type LastReadAt1 = string;
export type LastReadMessageId2 = string;
export type MemberId18 = string;
export type ChannelId29 = string;
export type LastReadMessageId3 = string;
export type MemberId19 = string;
export type AnchorChannelId = string;
export type AnchorMessageId = string | null;
export type AnchorTaskId = string | null;
export type Cadence1 = string;
export type Kind8 = string;
export type AgentMemberId19 = string;
export type AnchorChannelId1 = string;
export type AnchorMessageId1 = string | null;
export type AnchorTaskId1 = string | null;
export type Cadence2 = string;
export type CancelledByMemberId = string | null;
export type CreatedAt27 = string;
export type Id34 = string;
export type ReminderKind = 'once' | 'recurring';
export type LoopContractId = string | null;
export type NextFireAt = string;
export type ReminderStatus = 'active' | 'cancelled' | 'done';
export type WorkspaceId31 = string;
export type AgentMemberId20 = string;
export type AnchorChannelId2 = string;
export type AnchorMessageId2 = string | null;
export type AnchorTaskId2 = string | null;
export type Cadence3 = string;
export type CancelledByMemberId1 = string | null;
export type CreatedAt28 = string;
export type Id35 = string;
export type LoopContractId1 = string | null;
export type NextFireAt1 = string;
export type ReminderStatus1 = 'active' | 'cancelled' | 'done';
export type WorkspaceId32 = string;
export type Kind9 = 'reply';
export type Ref3 = string;
export type V4 = number;
export type At4 = string;
export type FrameId2 = string;
export type Kind10 = 'report';
/**
 * 上报帧目录（契约 D §7）。
 */
export type ReportType =
  | 'hello'
  | 'agent.status_changed'
  | 'agent.activity'
  | 'runtimes.detected'
  | 'diagnostics.batch'
  | 'usage.batch'
  | 'deploy.log'
  | 'deploy.finished'
  | 'preview.status'
  | 'worktree.status'
  | 'check.finished';
export type V5 = number;
export type Runtimes = DetectedRuntime[];
export type Channels = ChannelPublic[];
export type Members = MemberPublic[];
export type Snippet = string;
export type Messages1 = SearchMessageResult[];
export type Tasks1 = TaskPublic[];
export type Skills2 = string[];
export type DeploymentId4 = string;
export type Stream = 'deploy_log';
export type Type4 = 'sub' | 'unsub';
export type AgentMemberId21 = string;
export type Stream1 = 'diagnostic';
export type Type5 = 'sub' | 'unsub';
export type BlockedAt = string | null;
export type CanvasId6 = string;
export type CreatedAt29 = string;
export type LastFingerprint = string | null;
export type ReplanUsed = number;
export type RoundCount = number;
export type StallCount = number;
export type TaskId14 = string;
export type UpdatedAt4 = string;
export type WorkspaceId33 = string;
export type BlockedAt1 = string | null;
export type CanvasId7 = string;
export type CreatedAt30 = string;
export type LastFingerprint1 = string | null;
export type ReplanUsed1 = number;
export type RoundCount1 = number;
export type StallCount1 = number;
export type TaskId15 = string;
export type UpdatedAt5 = string;
export type WorkspaceId34 = string;
export type ConnId = string;
export type HeartbeatSec1 = number;
export type ProtocolV1 = number;
export type ServerVersion1 = string;
export type WorkspaceId35 = string;
export type ActorMemberId3 = string | null;
export type TaskStatus1 = 'todo' | 'in_progress' | 'in_review' | 'done' | 'closed';
export type TaskEventKind =
  'status_change' | 'claim' | 'unclaim' | 'assign' | 'force_start' | 'reminder_sent' | 'escalated';
export type CreatedAt31 = string;
export type CreatedByMemberId3 = string;
export type Id36 = string;
export type ReminderId1 = string | null;
export type Revision2 = number;
export type SupersededAt = string | null;
export type TaskId16 = string | null;
export type Version2 = string;
export type WorkspaceId36 = string;
export type CreatedAt32 = string;
export type CreatedByMemberId4 = string;
export type Id37 = string;
export type ReminderId2 = string | null;
export type Revision3 = number;
export type SupersededAt1 = string | null;
export type TaskId17 = string | null;
export type Version3 = string;
export type WorkspaceId37 = string;
export type Contracts = TaskContractPublic[];
export type CacheReadTokens1 = number;
export type CacheWriteTokens1 = number;
export type Events3 = number;
export type InputTokens1 = number;
export type OutputTokens1 = number;
export type Branch3 = string;
export type CleanedAt = string | null;
export type CreatedAt33 = string;
export type Id38 = string;
export type MergeCommit = string | null;
export type MergedAt = string | null;
export type Path4 = string;
export type ProjectId9 = string;
export type WorktreeStatus = 'active' | 'merged' | 'conflicted' | 'cleaned';
export type TaskId18 = string;
export type WorkspaceId38 = string;
export type ActorMemberId4 = string | null;
export type CreatedAt34 = string;
export type OwnerMemberId1 = string | null;
export type Seq5 = number;
export type TaskId19 = string;
export type ActorMemberId5 = string | null;
export type CreatedAt35 = string;
export type OwnerMemberId2 = string | null;
export type Seq6 = number;
export type TaskId20 = string;
export type Deliverables = Deliverable[];
export type Evidence1 = Evidence[];
export type FromMember = string;
export type OpenRisks = string[];
/**
 * TaskHandoff 的结构化评审结论（契约 B §12.10）。
 */
export type ReviewVerdict = 'pass' | 'downgrade' | 'send_back' | 'needs_human';
export type ToMember = string;
export type VerifyPlan = string;
export type Version4 = 'coagentia.task-handoff.v1';
export type TaskLevel1 = 'l1' | 'l2';
export type SilenceOverrideH1 = number | null;
export type Title6 = string | null;
export type ChannelId30 = string;
export type CreatedAt36 = string;
export type CreatedByMemberId5 = string;
export type Id39 = string;
export type TaskLevel2 = 'l1' | 'l2';
export type Number1 = number;
export type OwnerMemberId3 = string | null;
export type ProjectId10 = string | null;
export type RootMessageId1 = string;
export type SilenceOverrideH2 = number | null;
export type TaskStatus2 = 'todo' | 'in_progress' | 'in_review' | 'done' | 'closed';
export type StatusChangedAt1 = string;
export type Title7 = string;
export type WorkspaceId39 = string;
export type WritesCode2 = boolean;
export type Briefing = string;
export type FromKey = string;
export type ToKey = string;
export type Edges1 = TemplateEdge[];
export type Key3 = string;
export type ProjectId11 = string | null;
export type Role = string;
export type Title8 = string;
export type WritesCode3 = boolean;
export type Nodes1 = TemplateNode[];
export type Description8 = string;
export type Placeholder = string;
export type Roles = TemplateRole[];
export type ChannelId31 = string;
export type Description9 = string;
export type IncludeNodeIds = string[] | null;
export type Name20 = string;
export type RolePlaceholders = {
  [k: string]: string;
} | null;
export type ChannelId32 = string;
export type Description10 = string | null;
export type Name21 = string | null;
export type Builtin2 = boolean;
export type CreatedAt37 = string;
export type CreatedByMemberId6 = string;
export type Description11 = string;
export type Id40 = string;
export type Name22 = string;
export type WorkspaceId40 = string;
export type Builtin3 = boolean;
export type CreatedAt38 = string;
export type CreatedByMemberId7 = string;
export type Description12 = string;
export type Id41 = string;
export type Name23 = string;
export type WorkspaceId41 = string;
export type CacheReadTokens2 = number;
export type CacheWriteTokens2 = number;
export type InputTokens2 = number;
export type OutputTokens2 = number;
export type AgentMemberId22 = string;
export type CacheReadTokens3 = number;
export type CacheWriteTokens3 = number;
export type ChannelId33 = string | null;
export type Id42 = string;
export type InputTokens3 = number;
export type OutputTokens3 = number;
export type ReportedAt = string;
export type SourceSession1 = string | null;
export type ThreadRootId6 = string | null;
export type AgentMemberId23 = string;
export type CacheReadTokens4 = number;
export type CacheWriteTokens4 = number;
export type ChannelId34 = string | null;
export type Id43 = string;
export type InputTokens4 = number;
export type OutputTokens4 = number;
export type ReportedAt1 = string;
export type SourceSession2 = string | null;
export type TaskId21 = string | null;
export type WorkspaceId42 = string;
export type AgentMemberId24 = string;
export type CacheReadTokens5 = number;
export type CacheWriteTokens5 = number;
export type ChannelId35 = string | null;
export type Id44 = string;
export type InputTokens5 = number;
export type OutputTokens5 = number;
export type ReportedAt2 = string;
export type SourceSession3 = string | null;
export type TaskId22 = string | null;
export type WorkspaceId43 = string;
export type AgentMemberId25 = string;
export type TaskId23 = string | null;
export type Events4 = TokenUsageEventIn[];
export type Label = string;
export type Ref4 = string;
export type Breakdown = UsageBreakdownItem[] | null;
/**
 * `GET /usage?level=`（契约 B §13.4）三层聚合维度值域（?filter=/?kind= 枚举先例）。
 */
export type UsageLevel = 'task' | 'agent' | 'canvas';
export type Ref5 = string;
export type Name24 = string;
export type Slug = string;
export type AttachmentMaxMb = number | null;
export type Name25 = string | null;
export type NotifDesktop = boolean | null;
export type NotifSound = boolean | null;
export type OnboardingGreeting = boolean | null;
export type SetupState = {
  [k: string]: JsonValue;
} | null;
export type Slug1 = string | null;
export type UiTheme = 'dark' | 'light' | 'system';
export type AttachmentMaxMb1 = number;
export type CreatedAt39 = string;
export type Id45 = string;
export type Name26 = string;
export type NotifDesktop1 = boolean;
export type NotifSound1 = boolean;
export type OnboardingGreeting1 = boolean;
export type Slug2 = string;
export type UiTheme1 = 'dark' | 'light' | 'system';
export type AttachmentMaxMb2 = number;
export type CreatedAt40 = string;
export type Id46 = string;
export type Name27 = string;
export type NotifDesktop2 = boolean;
export type NotifSound2 = boolean;
export type OnboardingGreeting2 = boolean;
export type Slug3 = string;
export type UiTheme2 = 'dark' | 'light' | 'system';
export type TaskId24 = string;
export type Branch4 = string;
export type ProjectId12 = string;
export type RepoPath7 = string;
export type TaskId25 = string;
export type Branch5 = string;
export type Message2 = string;
export type ProjectId13 = string;
export type RepoPath8 = string;
export type TaskId26 = string;
export type Branch6 = string;
export type CleanedAt1 = string | null;
export type CreatedAt41 = string;
export type Id47 = string;
export type MergeCommit1 = string | null;
export type MergedAt1 = string | null;
export type Path5 = string;
export type ProjectId14 = string;
export type TaskId27 = string;
export type WorkspaceId44 = string;
export type Branch7 = string;
export type ConflictFiles = string[] | null;
export type MergeCommit2 = string | null;
export type Path6 = string;
export type Status4 = 'active' | 'merged' | 'conflicted' | 'cleaned';
export type TaskId28 = string;

export interface CoAgentiaContracts {
  AcceptanceCriterion?: AcceptanceCriterion;
  AckFrame?: AckFrame;
  ActivityCreatedData?: ActivityCreatedData;
  ActivityDoneData?: ActivityDoneData;
  ActivityItemPublic?: ActivityItemPublic;
  ActivityItemRow?: ActivityItemRow;
  AgentActivityData?: AgentActivityData;
  AgentBoot?: AgentBoot;
  AgentCreate?: AgentCreate;
  AgentPatch?: AgentPatch;
  AgentPublic?: AgentPublic;
  AgentRefData?: AgentRefData;
  AgentRoleTemplatePublic?: AgentRoleTemplatePublic;
  AgentRoleTemplateRow?: AgentRoleTemplateRow;
  AgentRow?: AgentRow;
  AgentSkillPublic?: AgentSkillPublic;
  AgentSkillRow?: AgentSkillRow;
  AgentStartData?: AgentStartData;
  AgentStatusChangedData?: AgentStatusChangedData;
  AgentUpdatedData?: AgentUpdatedData;
  AgentWakeData?: AgentWakeData;
  AsTask?: AsTask;
  AssignRequest?: AssignRequest;
  BufferedCounts?: BufferedCounts;
  CanvasBaselineAdvancedData?: CanvasBaselineAdvancedData;
  CanvasDetail?: CanvasDetail;
  CanvasEdgeData?: CanvasEdgeData;
  CanvasEdgePublic?: CanvasEdgePublic;
  CanvasEdgeRemovedData?: CanvasEdgeRemovedData;
  CanvasEdgeRow?: CanvasEdgeRow;
  CanvasLayoutUpdatedData?: CanvasLayoutUpdatedData;
  CanvasMutation?: CanvasMutation;
  CanvasNodeData?: CanvasNodeData;
  CanvasNodePublic?: CanvasNodePublic;
  CanvasNodeRemovedData?: CanvasNodeRemovedData;
  CanvasNodeRow?: CanvasNodeRow;
  CanvasPublic?: CanvasPublic;
  CanvasRow?: CanvasRow;
  ChannelCreate?: ChannelCreate;
  ChannelData?: ChannelData;
  ChannelMemberAdd?: ChannelMemberAdd;
  ChannelMemberPublic?: ChannelMemberPublic;
  ChannelMemberRow?: ChannelMemberRow;
  ChannelMembershipData?: ChannelMembershipData;
  ChannelNotificationSettingPublic?: ChannelNotificationSettingPublic;
  ChannelNotificationSettingRow?: ChannelNotificationSettingRow;
  ChannelPatch?: ChannelPatch;
  ChannelProjectPublic?: ChannelProjectPublic;
  ChannelProjectRow?: ChannelProjectRow;
  ChannelPublic?: ChannelPublic;
  ChannelRow?: ChannelRow;
  ChannelsSnapshot?: ChannelsSnapshot;
  CheckFinishedData?: CheckFinishedData;
  CheckRunData?: CheckRunData;
  ComputerCreate?: ComputerCreate;
  ComputerCreated?: ComputerCreated;
  ComputerData?: ComputerData;
  ComputerPatch?: ComputerPatch;
  ComputerPublic?: ComputerPublic;
  ComputerRow?: ComputerRow;
  ContractCreate?: ContractCreate;
  ContractDraftRequest?: ContractDraftRequest;
  ConvertToTask?: ConvertToTask;
  DaemonAgentActivityData?: DaemonAgentActivityData;
  DaemonAgentState?: DaemonAgentState;
  DaemonHelloAckData?: DaemonHelloAckData;
  DaemonHelloData?: DaemonHelloData;
  DecomposeRequest?: DecomposeRequest;
  Deliverable?: Deliverable;
  DeployFinishedData?: DeployFinishedData;
  DeployLogReportData?: DeployLogReportData;
  DeployRunData?: DeployRunData;
  DeploymentData?: DeploymentData;
  DeploymentLogData?: DeploymentLogData;
  DeploymentLogPage?: DeploymentLogPage;
  DeploymentPublic?: DeploymentPublic;
  DeploymentRow?: DeploymentRow;
  DetectedRuntime?: DetectedRuntime;
  DiagnosticAppendedData?: DiagnosticAppendedData;
  DiagnosticEventIn?: DiagnosticEventIn;
  DiagnosticEventPublic?: DiagnosticEventPublic;
  DiagnosticEventRow?: DiagnosticEventRow;
  DiagnosticsBatchData?: DiagnosticsBatchData;
  DiffFile?: DiffFile;
  DiffPayload?: DiffPayload;
  DmCreate?: DmCreate;
  DraftAdjustedData?: DraftAdjustedData;
  EdgeCreate?: EdgeCreate;
  Envelope?: Envelope;
  ErrorBody?: ErrorBody;
  ErrorResponse?: ErrorResponse;
  Evidence?: Evidence;
  FilePublic?: FilePublic;
  FileRow?: FileRow;
  FrameError?: FrameError;
  GitDiffQuery?: GitDiffQuery;
  HeldDraftAsTask?: HeldDraftAsTask;
  HeldDraftData?: HeldDraftData;
  HeldDraftPublic?: HeldDraftPublic;
  HeldDraftReasons?: HeldDraftReasons;
  HeldDraftReleaseResponse?: HeldDraftReleaseResponse;
  HeldDraftResponse?: HeldDraftResponse;
  HeldDraftRow?: HeldDraftRow;
  HomeFileBinaryReply?: HomeFileBinaryReply;
  HomeFileQuery?: HomeFileQuery;
  HomeFileTextReply?: HomeFileTextReply;
  HomeTreeEntry?: HomeTreeEntry;
  HomeTreeQuery?: HomeTreeQuery;
  HomeTreeReply?: HomeTreeReply;
  InjectSource?: InjectSource;
  InstantiateResult?: InstantiateResult;
  InstrFrame?: InstrFrame;
  LandingBatchData?: LandingBatchData;
  LandingBatchPublic?: LandingBatchPublic;
  LandingBatchRow?: LandingBatchRow;
  LayoutPositionIn?: LayoutPositionIn;
  LayoutPut?: LayoutPut;
  LedgerEntryPublic?: LedgerEntryPublic;
  LedgerEntryRow?: LedgerEntryRow;
  LifecycleRequest?: LifecycleRequest;
  LoopBudget?: LoopBudget;
  LoopContractBody?: LoopContractBody;
  MemberData?: MemberData;
  MemberPatch?: MemberPatch;
  MemberPublic?: MemberPublic;
  MemberRow?: MemberRow;
  MessageCreate?: MessageCreate;
  MessageCreated?: MessageCreated;
  MessageCreatedData?: MessageCreatedData;
  MessageDeliverData?: MessageDeliverData;
  MessageHeld?: MessageHeld;
  MessageInjectData?: MessageInjectData;
  MessageMentionPublic?: MessageMentionPublic;
  MessageMentionRow?: MessageMentionRow;
  MessagePublic?: MessagePublic;
  MessageRow?: MessageRow;
  MessageTaskRefPublic?: MessageTaskRefPublic;
  MessageTaskRefRow?: MessageTaskRefRow;
  NodeCreate?: NodeCreate;
  NodePatch?: NodePatch;
  NodePosition?: NodePosition;
  NotificationSettingPut?: NotificationSettingPut;
  Page?: Page;
  PingMsg?: PingMsg;
  PresenceChangedData?: PresenceChangedData;
  PresenceEntry?: PresenceEntry;
  PresenceSnapshot?: PresenceSnapshot;
  PreviewSessionPublic?: PreviewSessionPublic;
  PreviewSessionRow?: PreviewSessionRow;
  PreviewStartData?: PreviewStartData;
  PreviewStatusData?: PreviewStatusData;
  PreviewStopData?: PreviewStopData;
  PreviewUpdatedData?: PreviewUpdatedData;
  ProjectBind?: ProjectBind;
  ProjectCreate?: ProjectCreate;
  ProjectPatch?: ProjectPatch;
  ProjectPublic?: ProjectPublic;
  ProjectRow?: ProjectRow;
  ProposalConfirm?: ProposalConfirm;
  ProposalConfirmExpected?: ProposalConfirmExpected;
  ProposalConfirmResult?: ProposalConfirmResult;
  ProposalData?: ProposalData;
  ProposalPublic?: ProposalPublic;
  ProposalRefData?: ProposalRefData;
  ProposalReject?: ProposalReject;
  ProposalRow?: ProposalRow;
  QueryFrame?: QueryFrame;
  ReadPositionPublic?: ReadPositionPublic;
  ReadPositionPut?: ReadPositionPut;
  ReadPositionRow?: ReadPositionRow;
  ReadUpdatedData?: ReadUpdatedData;
  ReminderCreate?: ReminderCreate;
  ReminderData?: ReminderData;
  ReminderPublic?: ReminderPublic;
  ReminderRow?: ReminderRow;
  ReplyFrame?: ReplyFrame;
  ReportFrame?: ReportFrame;
  RuntimeRescanData?: RuntimeRescanData;
  RuntimesDetectedData?: RuntimesDetectedData;
  SearchJumps?: SearchJumps;
  SearchMessageResult?: SearchMessageResult;
  SearchResponse?: SearchResponse;
  SkillsPut?: SkillsPut;
  SubDeployLogMsg?: SubDeployLogMsg;
  SubDiagnosticMsg?: SubDiagnosticMsg;
  SummaryRunPublic?: SummaryRunPublic;
  SummaryRunRow?: SummaryRunRow;
  SysHelloData?: SysHelloData;
  SysPongData?: SysPongData;
  TaskChange?: TaskChange;
  TaskContractData?: TaskContractData;
  TaskContractPublic?: TaskContractPublic;
  TaskContractRow?: TaskContractRow;
  TaskCreatedData?: TaskCreatedData;
  TaskDetail?: TaskDetail;
  TaskEventPublic?: TaskEventPublic;
  TaskEventRow?: TaskEventRow;
  TaskHandoffBody?: TaskHandoffBody;
  TaskPatch?: TaskPatch;
  TaskPlanBody?: TaskPlanBody;
  TaskPublic?: TaskPublic;
  TaskRow?: TaskRow;
  TaskStatusChange?: TaskStatusChange;
  TaskUpdatedData?: TaskUpdatedData;
  TaskUsage?: TaskUsage;
  TasksReporting?: TasksReporting;
  TemplateBody?: TemplateBody;
  TemplateCreate?: TemplateCreate;
  TemplateEdge?: TemplateEdge;
  TemplateInstantiate?: TemplateInstantiate;
  TemplateNode?: TemplateNode;
  TemplatePatch?: TemplatePatch;
  TemplatePublic?: TemplatePublic;
  TemplateRole?: TemplateRole;
  TemplateRow?: TemplateRow;
  TokenSummary?: TokenSummary;
  TokenTotals?: TokenTotals;
  TokenUsageEventIn?: TokenUsageEventIn;
  TokenUsageEventPublic?: TokenUsageEventPublic;
  TokenUsageEventRow?: TokenUsageEventRow;
  TokenUsageReportedData?: TokenUsageReportedData;
  UsageBatchData?: UsageBatchData;
  UsageBreakdownItem?: UsageBreakdownItem;
  UsageBucket?: UsageBucket;
  UsageReport?: UsageReport;
  WakeRefs?: WakeRefs;
  WorkspaceCreate?: WorkspaceCreate;
  WorkspacePatch?: WorkspacePatch;
  WorkspacePublic?: WorkspacePublic;
  WorkspaceRow?: WorkspaceRow;
  WorkspaceUpdatedData?: WorkspaceUpdatedData;
  WorktreeCleanupData?: WorktreeCleanupData;
  WorktreeEnsureData?: WorktreeEnsureData;
  WorktreeMergeData?: WorktreeMergeData;
  WorktreePublic?: WorktreePublic;
  WorktreeRow?: WorktreeRow;
  WorktreeStatusData?: WorktreeStatusData;
  WorktreeUpdatedData?: WorktreeUpdatedData;
}
/**
 * TaskPlan 验收标准单条（可证伪表述、禁形容词——PRD §4.3；文案规范不在此校验）。
 */
export interface AcceptanceCriterion {
  id: Id;
  statement: Statement;
  verify_by: VerifyBy;
  verify_ref: VerifyRef;
}
export interface AckFrame {
  data?: unknown;
  error?: FrameError | null;
  kind?: Kind;
  ref: Ref;
  result: AckResult;
  v?: V;
}
export interface FrameError {
  code: Code;
  message: Message;
}
export interface ActivityCreatedData {
  item: ActivityItemPublic;
}
/**
 * 读面派生字段：actor_member_id = 触发本条的消息作者（自 message_id 联查，不落库）。
 *
 * member_id 是接收者（表列语义）；前端渲染"谁提及了你/谁发来私信"需要作者，
 * 缺此字段时前端只能错用 member_id（M2 review 确认的行为人错位）。
 */
export interface ActivityItemPublic {
  actor_member_id?: ActorMemberId;
  channel_id?: ChannelId;
  created_at: CreatedAt;
  done_at?: DoneAt;
  id: Id1;
  kind: ActivityKind;
  member_id: MemberId;
  message_id?: MessageId;
  task_id?: TaskId;
  workspace_id: WorkspaceId;
}
export interface ActivityDoneData {
  item_id: ItemId;
}
/**
 * M2：Activity 聚合面（FR-4.6）。
 */
export interface ActivityItemRow {
  channel_id?: ChannelId1;
  created_at: CreatedAt1;
  done_at?: DoneAt1;
  id: Id2;
  kind: ActivityKind;
  member_id: MemberId1;
  message_id?: MessageId1;
  task_id?: TaskId1;
  workspace_id: WorkspaceId1;
}
/**
 * 瞬态：每 Agent ≥500ms 节流、只发最新，不入库；detail 值域 = constants.ACTIVITY_PHRASES。
 */
export interface AgentActivityData {
  detail: Detail;
  member_id: MemberId2;
}
/**
 * 启动所需全量配置快照（agent.start/restart/reset_*；"下次启动生效"的生效载体）。
 */
export interface AgentBoot {
  agent_member_id: AgentMemberId;
  home_path: HomePath;
  model: Model;
  name: Name;
  runtime: Runtime;
  skills?: Skills;
}
export interface AgentCreate {
  computer_id: ComputerId;
  description?: Description;
  model: Model1;
  name: Name1;
  role_template_key?: RoleTemplateKey;
  runtime: Runtime1;
}
/**
 * runtime/model/description 修改 = 下次启动生效（FR-3.5）；R3 门。
 */
export interface AgentPatch {
  description?: Description1;
  model?: Model2;
  runtime?: Runtime2;
}
export interface AgentPublic {
  computer_id: ComputerId1;
  created_by_member_id: CreatedByMemberId;
  description?: Description2;
  home_path: HomePath1;
  member_id: MemberId3;
  model: Model3;
  role_template_key?: RoleTemplateKey1;
  runtime: Runtime;
  status?: AgentStatus;
}
export interface AgentRefData {
  agent_member_id: AgentMemberId1;
}
export interface AgentRoleTemplatePublic {
  builtin?: Builtin;
  description_prefill: DescriptionPrefill;
  id: Id3;
  key: Key;
  name: Name2;
  prompt_sections: JsonValue;
}
/**
 * M6 形状冻结（03 §3.1 "Orchestrator = 数据不是代码"）。
 */
export interface AgentRoleTemplateRow {
  builtin?: Builtin1;
  description_prefill: DescriptionPrefill1;
  id: Id4;
  key: Key1;
  name: Name3;
  prompt_sections: JsonValue;
}
export interface AgentRow {
  computer_id: ComputerId2;
  created_by_member_id: CreatedByMemberId1;
  description?: Description3;
  home_path: HomePath2;
  member_id: MemberId4;
  model: Model4;
  role_template_key?: RoleTemplateKey2;
  runtime: Runtime;
  status?: AgentStatus1;
}
export interface AgentSkillPublic {
  agent_member_id: AgentMemberId2;
  granted_at: GrantedAt;
  granted_by_member_id: GrantedByMemberId;
  skill: Skill;
}
export interface AgentSkillRow {
  agent_member_id: AgentMemberId3;
  granted_at: GrantedAt1;
  granted_by_member_id: GrantedByMemberId1;
  skill: Skill1;
}
export interface AgentStartData {
  agent: AgentBoot;
}
/**
 * agents.status 列的唯一写入方（契约 D §7）。
 */
export interface AgentStatusChangedData {
  agent_member_id: AgentMemberId4;
  error_detail?: ErrorDetail;
  status: AgentStatus2;
}
export interface AgentUpdatedData {
  agent: AgentPublic;
}
export interface AgentWakeData {
  agent_member_id: AgentMemberId5;
  reason: WakeReason;
  refs: WakeRefs;
}
export interface WakeRefs {
  message_ids?: MessageIds;
  node_id?: NodeId;
  reminder_id?: ReminderId;
}
export interface AsTask {
  title?: Title;
}
/**
 * 改派（B §9.2）——POST /tasks/{id}/assign；member_id=None → 取消指派（不动 status）。
 */
export interface AssignRequest {
  member_id?: MemberId5;
}
export interface BufferedCounts {
  diagnostics?: Diagnostics;
  usage?: Usage;
}
export interface CanvasBaselineAdvancedData {
  baseline_hash: BaselineHash;
  baseline_version: BaselineVersion;
  canvas_id: CanvasId;
}
/**
 * GET /channels/{id}/canvas（B §4.9）：画布头 + 节点/边（空画布二者皆空）。
 */
export interface CanvasDetail {
  canvas: CanvasPublic;
  edges?: Edges;
  nodes?: Nodes;
}
export interface CanvasPublic {
  baseline_hash: BaselineHash1;
  baseline_version?: BaselineVersion1;
  channel_id: ChannelId2;
  id: Id5;
  updated_at: UpdatedAt;
  workspace_id: WorkspaceId2;
}
export interface CanvasEdgePublic {
  canvas_id: CanvasId1;
  from_node_id: FromNodeId;
  id: Id6;
  to_node_id: ToNodeId;
}
export interface CanvasNodePublic {
  canvas_id: CanvasId2;
  command?: Command;
  created_at: CreatedAt2;
  id: Id7;
  is_summary?: IsSummary;
  kind: CanvasNodeKind;
  pos_x?: PosX;
  pos_y?: PosY;
  system_action?: SystemAction | null;
  system_status?: SystemNodeStatus | null;
  task_id?: TaskId2;
  upstream_policy?: UpstreamPolicy;
}
export interface CanvasEdgeData {
  edge: CanvasEdgePublic;
}
export interface CanvasEdgeRemovedData {
  edge_id: EdgeId;
}
/**
 * M3。UNIQUE(canvas_id, from, to)；无环由串行化点内拓扑排序保证。
 */
export interface CanvasEdgeRow {
  canvas_id: CanvasId3;
  from_node_id: FromNodeId1;
  id: Id8;
  to_node_id: ToNodeId1;
}
export interface CanvasLayoutUpdatedData {
  canvas_id: CanvasId4;
  positions: Positions;
}
export interface NodePosition {
  node_id: NodeId1;
  x: X;
  y: Y;
}
/**
 * 画布结构写统一响应（B §4.9）：附最新基线版本/指纹，命中的节点或边随写回填。
 */
export interface CanvasMutation {
  baseline_hash: BaselineHash2;
  baseline_version: BaselineVersion2;
  edge?: CanvasEdgePublic | null;
  node?: CanvasNodePublic | null;
}
export interface CanvasNodeData {
  node: CanvasNodePublic;
}
export interface CanvasNodeRemovedData {
  node_id: NodeId2;
}
/**
 * M3。草稿层节点不落本表（proposals.body 渲染）。
 */
export interface CanvasNodeRow {
  canvas_id: CanvasId5;
  command?: Command1;
  created_at: CreatedAt3;
  id: Id9;
  is_summary?: IsSummary1;
  kind: CanvasNodeKind;
  pos_x?: PosX1;
  pos_y?: PosY1;
  system_action?: SystemAction | null;
  system_status?: SystemNodeStatus | null;
  task_id?: TaskId3;
  upstream_policy?: UpstreamPolicy1;
}
/**
 * M1 建表（预留 #2）。基线语义 = 契约 A §6。
 */
export interface CanvasRow {
  baseline_hash: BaselineHash3;
  baseline_version?: BaselineVersion3;
  channel_id: ChannelId3;
  id: Id10;
  updated_at: UpdatedAt1;
  workspace_id: WorkspaceId3;
}
export interface ChannelCreate {
  description?: Description4;
  is_private?: IsPrivate;
  member_ids?: MemberIds;
  name: Name4;
}
export interface ChannelData {
  channel: ChannelPublic;
}
export interface ChannelPublic {
  archived_at?: ArchivedAt;
  created_at: CreatedAt4;
  decomp_mode?: DecompMode;
  decomp_node_limit?: DecompNodeLimit;
  description?: Description5;
  dm_key?: DmKey;
  held_escalate_n?: HeldEscalateN;
  held_reeval_min?: HeldReevalMin;
  id: Id11;
  is_private?: IsPrivate1;
  joint_ref?: JointRef;
  kind: ChannelKind;
  name?: Name5;
  next_task_number?: NextTaskNumber;
  orch_escalation?: OrchEscalation;
  remind_escalation?: RemindEscalation;
  remind_inprog_h?: RemindInprogH;
  remind_review_h?: RemindReviewH;
  remind_todo_h?: RemindTodoH;
  workspace_id: WorkspaceId4;
}
export interface ChannelMemberAdd {
  member_id: MemberId6;
}
export interface ChannelMemberPublic {
  channel_id: ChannelId4;
  joined_at: JoinedAt;
  member_id: MemberId7;
}
export interface ChannelMemberRow {
  channel_id: ChannelId5;
  joined_at: JoinedAt1;
  member_id: MemberId8;
}
export interface ChannelMembershipData {
  channel_id: ChannelId6;
  member_id: MemberId9;
}
export interface ChannelNotificationSettingPublic {
  channel_id: ChannelId7;
  member_id: MemberId10;
  mode?: NotificationMode;
}
/**
 * M5（FR-4.7 每频道通知设置）。
 */
export interface ChannelNotificationSettingRow {
  channel_id: ChannelId8;
  member_id: MemberId11;
  mode?: NotificationMode1;
}
export interface ChannelPatch {
  decomp_mode?: DecompMode1;
  decomp_node_limit?: DecompNodeLimit1;
  description?: Description6;
  held_escalate_n?: HeldEscalateN1;
  held_reeval_min?: HeldReevalMin1;
  is_private?: IsPrivate2;
  orch_escalation?: OrchEscalation1;
  remind_escalation?: RemindEscalation1;
  remind_inprog_h?: RemindInprogH1;
  remind_review_h?: RemindReviewH1;
  remind_todo_h?: RemindTodoH1;
}
export interface ChannelProjectPublic {
  channel_id: ChannelId9;
  project_id: ProjectId;
}
export interface ChannelProjectRow {
  channel_id: ChannelId10;
  project_id: ProjectId1;
}
export interface ChannelRow {
  archived_at?: ArchivedAt1;
  created_at: CreatedAt5;
  decomp_mode?: DecompMode2;
  decomp_node_limit?: DecompNodeLimit2;
  description?: Description7;
  dm_key?: DmKey1;
  held_escalate_n?: HeldEscalateN2;
  held_reeval_min?: HeldReevalMin2;
  id: Id12;
  is_private?: IsPrivate3;
  joint_ref?: JointRef1;
  kind: ChannelKind;
  name?: Name6;
  next_task_number?: NextTaskNumber1;
  orch_escalation?: OrchEscalation2;
  remind_escalation?: RemindEscalation2;
  remind_inprog_h?: RemindInprogH2;
  remind_review_h?: RemindReviewH2;
  remind_todo_h?: RemindTodoH2;
  workspace_id: WorkspaceId5;
}
/**
 * GET /channels：全量频道 + 自身 read-position + 本人非默认通知设置（B §4.5/§6/§11.4）。
 *
 * v1.3：扩第三字段 `notification_settings`（本人全部**非默认**行，前端渲染徽标源；PUT 后
 * 本地更新，零新增 WS 事件）。冷态/全默认 → []（H0 字段就位，H3 填充）。
 */
export interface ChannelsSnapshot {
  items: Items;
  notification_settings?: NotificationSettings;
  read_positions: ReadPositions;
}
export interface ReadPositionPublic {
  channel_id: ChannelId11;
  last_read_at: LastReadAt;
  last_read_message_id: LastReadMessageId;
  member_id: MemberId12;
}
export interface CheckFinishedData {
  exit_code: ExitCode;
  node_id: NodeId3;
  output_tail: OutputTail;
  run_id: RunId;
  status: Status;
}
export interface CheckRunData {
  command: Command2;
  node_id: NodeId4;
  project_id: ProjectId2;
  repo_path: RepoPath;
  run_id: RunId1;
}
export interface ComputerCreate {
  name: Name7;
}
/**
 * api_key 明文仅此一次（库中只存哈希，契约 A）。
 */
export interface ComputerCreated {
  api_key: ApiKey;
  command_line: CommandLine;
  computer: ComputerPublic;
}
/**
 * = ComputerRow 剔除 api_key_hash（契约 A §8.2 敏感列）。
 */
export interface ComputerPublic {
  arch?: Arch;
  created_at: CreatedAt6;
  daemon_version?: DaemonVersion;
  detected_runtimes?: DetectedRuntimes;
  id: Id13;
  last_seen_at?: LastSeenAt;
  name: Name8;
  os?: Os;
  status?: ComputerStatus;
  workspace_id: WorkspaceId6;
}
/**
 * computers.detected_runtimes 数组元素（FR-2.3）。
 */
export interface DetectedRuntime {
  installed: Installed;
  models?: Models;
  runtime: Runtime;
  skills?: Skills1;
}
export interface ComputerData {
  computer: ComputerPublic;
}
export interface ComputerPatch {
  name: Name9;
}
export interface ComputerRow {
  api_key_hash: ApiKeyHash;
  arch?: Arch1;
  created_at: CreatedAt7;
  daemon_version?: DaemonVersion1;
  detected_runtimes?: DetectedRuntimes1;
  id: Id14;
  last_seen_at?: LastSeenAt1;
  name: Name10;
  os?: Os1;
  status?: ComputerStatus1;
  workspace_id: WorkspaceId7;
}
/**
 * POST /tasks/{id}/contracts（提交与修订）。
 *
 * `body` 故意留 JsonValue：按 `kind` 对应哪个 body 模型二次 `model_validate` 是 server
 * 侧职责（kind≠schema 或字段校验失败 → 422 VALIDATION_FAILED），此包不做 kind↔模型分派。
 */
export interface ContractCreate {
  body: JsonValue;
  kind: ContractKind;
}
/**
 * POST /tasks/{id}/contracts/request-draft（"让 @Agent 起草"）。
 *
 * 效果 = S1 定向直投唤醒（`InjectKind.CONTRACT_DRAFT_REQUEST`，契约 D）；daemon 离线 → 503
 * DAEMON_OFFLINE（P-3，best-effort 非积压）。
 */
export interface ContractDraftRequest {
  agent_member_id: AgentMemberId6;
  kind: ContractKind;
}
/**
 * Convert to Task（B §9.3）——POST /messages/{id}/task。
 *
 * title 缺省 = 锚点 body 首非空行剥 MD 前缀、>80 截断。
 */
export interface ConvertToTask {
  title?: Title1;
}
export interface DaemonAgentActivityData {
  agent_member_id: AgentMemberId7;
  detail: Detail1;
}
/**
 * hello 的真实进程表条目。
 */
export interface DaemonAgentState {
  agent_member_id: AgentMemberId8;
  source_session?: SourceSession;
  status: AgentStatus2;
}
/**
 * server 对 hello 的应答（握手第 3 步，契约 D §4.1）。
 */
export interface DaemonHelloAckData {
  computer_id: ComputerId3;
  heartbeat_sec: HeartbeatSec;
  protocol_v: ProtocolV;
  server_version: ServerVersion;
  workspace_id: WorkspaceId8;
}
export interface DaemonHelloData {
  agents: Agents;
  arch: Arch2;
  boot_nonce?: BootNonce;
  buffered: BufferedCounts;
  daemon_version: DaemonVersion2;
  detected_runtimes: DetectedRuntimes2;
  os: Os2;
  previews?: Previews;
}
/**
 * preview.status 上报 data；hello.previews 进程表条目复用同形状（v1.0.5）。
 */
export interface PreviewStatusData {
  log_tail?: LogTail;
  port?: Port;
  preview_session_id: PreviewSessionId;
  status: Status1;
}
export interface DecomposeRequest {
  task_id?: TaskId4;
  text?: Text;
}
export interface Deliverable {
  kind: DeliverableKind;
  path: Path;
}
export interface DeployFinishedData {
  deployment_id: DeploymentId;
  exit_code?: ExitCode1;
  status: Status2;
  url?: Url;
}
export interface DeployLogReportData {
  chunk_seq: ChunkSeq;
  deployment_id: DeploymentId1;
  lines: Lines;
}
export interface DeployRunData {
  branch: Branch;
  command: Command3;
  commit_hash?: CommitHash;
  deployment_id: DeploymentId2;
  repo_path: RepoPath1;
}
export interface DeploymentData {
  deployment: DeploymentPublic;
}
/**
 * = DeploymentRow 剔除 log_path（服务端内部；日志经端点/WS 流读取）。
 */
export interface DeploymentPublic {
  branch: Branch1;
  command: Command4;
  commit_hash?: CommitHash1;
  exit_code?: ExitCode2;
  finished_at?: FinishedAt;
  id: Id15;
  project_id: ProjectId3;
  started_at?: StartedAt;
  status: DeploymentStatus;
  token_summary?: TokenSummary | null;
  triggered_by_member_id: TriggeredByMemberId;
  url?: Url1;
  workspace_id: WorkspaceId9;
}
/**
 * deployments.token_summary 新账快照（契约 B §13.4）：上次 success 部署以来 merged 任务集
 * 聚合。task_ids 按 id 稳定排序、最多 50 项（details 有界先例 §12.12 #4）。
 */
export interface TokenSummary {
  task_ids?: TaskIds;
  tasks_reporting: TasksReporting;
  usage: UsageBucket;
}
/**
 * W7 覆盖率诚实标注：reporting = 有 usage 上报的任务数，total = 聚合集任务总数
 * （未上报任务计入分母，B §13.4）。level=task 时恒 {0/1, 1}。
 */
export interface TasksReporting {
  reporting?: Reporting;
  total?: Total;
}
/**
 * token 聚合四字段 + 事件计数（§9.8 同源同形；永不折算货币，W7）。
 */
export interface UsageBucket {
  cache_read_tokens?: CacheReadTokens;
  cache_write_tokens?: CacheWriteTokens;
  events?: Events;
  input_tokens?: InputTokens;
  output_tokens?: OutputTokens;
}
export interface DeploymentLogData {
  chunk_seq: ChunkSeq1;
  deployment_id: DeploymentId3;
  lines: Lines1;
}
/**
 * GET /deployments/{id}/log?after=<行号>（B §13.3）：server 直读落盘日志文件（不依赖 daemon
 * 在线）。next_after = 下一页游标行号（无更多为 None）；truncated = 文件超 5MB 上限截断置真。
 */
export interface DeploymentLogPage {
  lines?: Lines2;
  next_after?: NextAfter;
  truncated?: Truncated;
}
export interface DeploymentRow {
  branch: Branch2;
  command: Command5;
  commit_hash?: CommitHash2;
  exit_code?: ExitCode3;
  finished_at?: FinishedAt1;
  id: Id16;
  log_path?: LogPath;
  project_id: ProjectId4;
  started_at?: StartedAt1;
  status: DeploymentStatus;
  token_summary?: TokenSummary | null;
  triggered_by_member_id: TriggeredByMemberId1;
  url?: Url2;
  workspace_id: WorkspaceId10;
}
/**
 * 订阅制流（契约 C §8）：50 条/批上限；历史翻页走 REST。
 */
export interface DiagnosticAppendedData {
  agent_member_id: AgentMemberId9;
  events: Events1;
}
/**
 * 诊断上行条目：无 seq（server 落库赋）、无 workspace_id（server 由 computer 富化）。
 */
export interface DiagnosticEventIn {
  agent_member_id?: AgentMemberId10;
  at: At;
  batch_id?: BatchId;
  channel_id?: ChannelId12;
  payload: JsonValue;
  task_id?: TaskId5;
  type: Type;
}
export interface DiagnosticEventPublic {
  agent_member_id?: AgentMemberId11;
  batch_id?: BatchId1;
  channel_id?: ChannelId13;
  created_at: CreatedAt8;
  payload: JsonValue;
  seq: Seq;
  task_id?: TaskId6;
  type: Type1;
  workspace_id: WorkspaceId11;
}
/**
 * M1：命令级留痕，落盘持久跨重启（NFR4）；type 命名空间见契约 A §4.6。
 */
export interface DiagnosticEventRow {
  agent_member_id?: AgentMemberId12;
  batch_id?: BatchId2;
  channel_id?: ChannelId14;
  created_at: CreatedAt9;
  payload: JsonValue;
  seq: Seq1;
  task_id?: TaskId7;
  type: Type2;
  workspace_id: WorkspaceId12;
}
export interface DiagnosticsBatchData {
  events: Events2;
}
export interface DiffFile {
  additions: Additions;
  deletions: Deletions;
  old_path?: OldPath;
  patch: Patch;
  patch_truncated: PatchTruncated;
  path: Path1;
  status: Status3;
}
/**
 * git.diff 查询响应（契约 D §6）；REST Diff 卡直接复用此形状。
 */
export interface DiffPayload {
  base_ref: BaseRef;
  files: Files;
  files_truncated: FilesTruncated;
  head_ref: HeadRef;
  total_additions: TotalAdditions;
  total_deletions: TotalDeletions;
}
export interface DmCreate {
  member_id: MemberId13;
}
export interface DraftAdjustedData {
  adjustments: Adjustments;
  proposal_id: ProposalId;
}
/**
 * POST /canvases/{id}/edges：连边；成环由 server 拓扑校验拒 422 GRAPH_CYCLE（V9）。
 */
export interface EdgeCreate {
  from_node_id: FromNodeId2;
  to_node_id: ToNodeId2;
}
/**
 * 信封四要素：类型 / 作用域 / 序号 / 幂等键（契约 C §3）。
 */
export interface Envelope {
  at: At1;
  channel_id?: ChannelId15;
  data: JsonValue;
  key: Key2;
  seq: Seq2;
  type: EventType;
  v?: V1;
  workspace_id: WorkspaceId13;
}
/**
 * `code` 机器分支、`message` 可直接进 toast、`rule` 溯源 PRD 规则号（契约 B §1）。
 */
export interface ErrorBody {
  code: ErrorCode;
  details?: unknown;
  message: Message1;
  rule?: Rule;
}
export interface ErrorResponse {
  error: ErrorBody;
}
export interface Evidence {
  conclusion: Conclusion;
  ref: Ref1;
  type: EvidenceType;
}
/**
 * = FileRow 剔除 stored_path（服务端内部）；message_id/channel_id 可空 = staging 态
 * （契约 D §9.2：预上传返回的 FilePublic 尚未绑定消息）。
 */
export interface FilePublic {
  channel_id?: ChannelId16;
  created_at: CreatedAt10;
  id: Id17;
  message_id?: MessageId2;
  mime: Mime;
  name: Name11;
  sha256: Sha256;
  size_bytes: SizeBytes;
  workspace_id: WorkspaceId14;
}
/**
 * 附件随消息永存、不可删（FR-4.8）。预上传暂存不落本表（契约 D §9.2）。
 */
export interface FileRow {
  channel_id: ChannelId17;
  created_at: CreatedAt11;
  id: Id18;
  message_id: MessageId3;
  mime: Mime1;
  name: Name12;
  sha256: Sha2561;
  size_bytes: SizeBytes1;
  stored_path: StoredPath;
  workspace_id: WorkspaceId15;
}
export interface GitDiffQuery {
  base?: Base;
  project_id: ProjectId5;
  repo_path: RepoPath2;
  task_id: TaskId8;
}
/**
 * 草稿携带的 as_task 意图（v1.0.5）——放行时随消息同一事务执行（语义同 B §9.4）。
 *
 * 形状镜像 `rest.AsTask`（entities 为下层不能反向 import rest；字段单源在此，rest.AsTask
 * 另有其消息端点用途，二者刻意分立以免层次倒置）。
 */
export interface HeldDraftAsTask {
  title?: Title2;
}
export interface HeldDraftData {
  draft: HeldDraftPublic;
}
export interface HeldDraftPublic {
  agent_member_id: AgentMemberId13;
  as_task?: HeldDraftAsTask | null;
  channel_id: ChannelId18;
  created_at: CreatedAt12;
  draft_body: DraftBody;
  escalated_at?: EscalatedAt;
  file_ids?: FileIds;
  held_count?: HeldCount;
  id: Id19;
  next_reeval_at: NextReevalAt;
  reasons: HeldDraftReasons;
  resolution?: HeldResolution | null;
  resolved_at?: ResolvedAt;
  resolved_by_member_id?: ResolvedByMemberId;
  status?: HeldDraftStatus;
  thread_root_id?: ThreadRootId;
  workspace_id: WorkspaceId16;
}
/**
 * 结构化被扣原因（G2：未读消息清单，可点跳转）。
 *
 * v1.0.5：`unread_message_ids` 上限 50 条（截断保留最新）；`total_unread` 为真实未读计数
 * （截断前的全量口径，卡片显示"还有 N 条"）。
 */
export interface HeldDraftReasons {
  total_unread: TotalUnread;
  unread_message_ids: UnreadMessageIds;
}
/**
 * POST /held-drafts/{id}/release 响应（B §4.14）：以原载荷落消息 + held 行置 released 终态。
 */
export interface HeldDraftReleaseResponse {
  held_draft: HeldDraftPublic;
  message: MessagePublic;
}
/**
 * 读面派生字段 files（v1.0.4，Public≠Row 放宽先例同 ActivityItemPublic.actor_member_id）：
 * REST 消息读面（列表/线程/发消息响应/搜索命中）与 message.created 广播填充（[] = 无附件）；
 * 未附着面（daemon backlog/deliver 帧）保持 None——否则旧消息附件卡受 channelFiles
 * 首页 ≤50 截断（M2 挂账）。serialize 时按 message_id 联查 files，不落 messages 表。
 */
export interface MessagePublic {
  author_member_id?: AuthorMemberId;
  body: Body;
  card_kind?: CardKind | null;
  card_ref?: CardRef;
  channel_id: ChannelId19;
  created_at: CreatedAt13;
  files?: Files1;
  id: Id20;
  kind?: MessageKind;
  thread_root_id?: ThreadRootId1;
  workspace_id: WorkspaceId17;
}
/**
 * POST /held-drafts/{id}/discard | /reevaluate 响应（B §4.14）：仅回 held 行最新态。
 */
export interface HeldDraftResponse {
  held_draft: HeldDraftPublic;
}
/**
 * M4（D4/G1–G6）。
 */
export interface HeldDraftRow {
  agent_member_id: AgentMemberId14;
  as_task?: HeldDraftAsTask | null;
  channel_id: ChannelId20;
  created_at: CreatedAt14;
  draft_body: DraftBody1;
  escalated_at?: EscalatedAt1;
  file_ids?: FileIds1;
  held_count?: HeldCount1;
  id: Id21;
  next_reeval_at: NextReevalAt1;
  reasons: HeldDraftReasons;
  resolution?: HeldResolution | null;
  resolved_at?: ResolvedAt1;
  resolved_by_member_id?: ResolvedByMemberId1;
  status?: HeldDraftStatus1;
  thread_root_id?: ThreadRootId2;
  workspace_id: WorkspaceId18;
}
export interface HomeFileBinaryReply {
  kind?: Kind1;
  mime?: Mime2;
  size_bytes: SizeBytes2;
}
export interface HomeFileQuery {
  agent_member_id: AgentMemberId15;
  path: Path2;
}
export interface HomeFileTextReply {
  content: Content;
  kind?: Kind2;
  truncated?: Truncated1;
}
export interface HomeTreeEntry {
  kind: Kind3;
  mtime: Mtime;
  name: Name13;
  size_bytes: SizeBytes3;
}
export interface HomeTreeQuery {
  agent_member_id: AgentMemberId16;
  path: Path3;
}
export interface HomeTreeReply {
  entries: Entries;
}
export interface InjectSource {
  kind: InjectKind;
  ref?: Ref2;
}
/**
 * 实例化响应（B §4.12）：单事务落地批 + 逐节点落地任务（零新增 WS 事件，广播走既有事件）。
 */
export interface InstantiateResult {
  batch: LandingBatchPublic;
  tasks?: Tasks;
}
export interface LandingBatchPublic {
  channel_id: ChannelId21;
  confirmed_by: ConfirmedBy;
  content_hash: ContentHash;
  created_at: CreatedAt15;
  done_at?: DoneAt2;
  id: Id22;
  kind: LandingBatchKind;
  source_ref: SourceRef;
  status?: LandingBatchStatus;
  workspace_id: WorkspaceId19;
}
export interface TaskPublic {
  channel_id: ChannelId22;
  created_at: CreatedAt16;
  created_by_member_id: CreatedByMemberId2;
  id: Id23;
  level?: TaskLevel;
  number: Number;
  owner_member_id?: OwnerMemberId;
  project_id?: ProjectId6;
  root_message_id: RootMessageId;
  silence_override_h?: SilenceOverrideH;
  status?: TaskStatus;
  status_changed_at: StatusChangedAt;
  title: Title3;
  workspace_id: WorkspaceId20;
  writes_code?: WritesCode;
}
/**
 * at-least-once：ack 超时原帧原样重发（同 frame_id）；同 Agent 串行。
 */
export interface InstrFrame {
  at: At2;
  data: JsonValue;
  frame_id: FrameId;
  kind?: Kind4;
  type: InstrType;
  v?: V2;
}
export interface LandingBatchData {
  batch: LandingBatchPublic;
}
/**
 * 幂等键命名空间锚（01 §5.1 修订：opId 含 batch_id）。
 */
export interface LandingBatchRow {
  channel_id: ChannelId23;
  confirmed_by: ConfirmedBy1;
  content_hash: ContentHash1;
  created_at: CreatedAt17;
  done_at?: DoneAt3;
  id: Id24;
  kind: LandingBatchKind;
  source_ref: SourceRef1;
  status?: LandingBatchStatus1;
  workspace_id: WorkspaceId21;
}
/**
 * 单节点坐标（pos_x/pos_y 不参与基线快照，契约 A §6）。
 */
export interface LayoutPositionIn {
  node_id: NodeId5;
  x: X1;
  y: Y1;
}
/**
 * PUT /canvases/{id}/layout：整批坐标覆盖（不推进基线）。
 */
export interface LayoutPut {
  positions: Positions1;
}
export interface LedgerEntryPublic {
  actor_member_id?: ActorMemberId1;
  batch_id?: BatchId3;
  created_at: CreatedAt18;
  kind: Kind5;
  op_id: OpId;
  payload: JsonValue;
  request_hash: RequestHash;
  seq: Seq3;
}
/**
 * 通用幂等账本（03 §3.2 基础设施；不可变表）。opId 格式见 constants.OPID_*。
 */
export interface LedgerEntryRow {
  actor_member_id?: ActorMemberId2;
  batch_id?: BatchId4;
  created_at: CreatedAt19;
  kind: Kind6;
  op_id: OpId1;
  payload: JsonValue;
  request_hash: RequestHash1;
  seq: Seq4;
}
export interface LifecycleRequest {
  action: LifecycleAction;
}
export interface LoopBudget {
  max_retries?: MaxRetries;
  max_runtime_min: MaxRuntimeMin;
}
/**
 * 循环任务上岗契约（创建循环 Reminder 时必填——PRD §4.3 v1；生成消费归 M4，模型 M3 建齐）。
 */
export interface LoopContractBody {
  budget: LoopBudget;
  cadence: Cadence;
  escalation: Escalation;
  tools?: Tools;
  verification: Verification;
  version?: Version;
}
export interface MemberData {
  member: MemberPublic;
}
export interface MemberPublic {
  created_at: CreatedAt20;
  id: Id25;
  kind: MemberKind;
  name: Name14;
  removed_at?: RemovedAt;
  role?: MemberRole;
  workspace_id: WorkspaceId22;
}
export interface MemberPatch {
  role: MemberRole1;
}
export interface MemberRow {
  created_at: CreatedAt21;
  id: Id26;
  kind: MemberKind;
  name: Name15;
  removed_at?: RemovedAt1;
  role?: MemberRole2;
  workspace_id: WorkspaceId23;
}
export interface MessageCreate {
  as_task?: AsTask | null;
  body: Body1;
  file_ids?: FileIds2;
  thread_root_id?: ThreadRootId3;
}
/**
 * as_task 成功时 task 非空（原子）。
 */
export interface MessageCreated {
  message: MessagePublic;
  task?: TaskPublic | null;
}
export interface MessageCreatedData {
  message: MessagePublic;
}
/**
 * ack(done) 后 server 写该 Agent read_positions（投递游标即已读位置，D §8.3）。
 */
export interface MessageDeliverData {
  agent_member_id: AgentMemberId17;
  channel_id: ChannelId24;
  messages: Messages;
  thread_root_id?: ThreadRootId4;
}
/**
 * Agent 主体发送被 freshness 扣住 → 202（G1；人类发送永不 held）。
 */
export interface MessageHeld {
  held_draft: HeldDraftPublic;
}
/**
 * S1：定向单 Agent、不进频道流、不动 read_positions；发出与 ack 各写一条诊断。
 */
export interface MessageInjectData {
  agent_member_id: AgentMemberId18;
  body: Body2;
  diagnostic_type: DiagnosticType;
  source: InjectSource;
}
export interface MessageMentionPublic {
  member_id: MemberId14;
  message_id: MessageId4;
}
/**
 * 发送时服务端解析一次的派生持久化；body 是唯一事实源。
 */
export interface MessageMentionRow {
  member_id: MemberId15;
  message_id: MessageId5;
}
/**
 * 不可变：无 UPDATE/DELETE（契约 A §1）。
 */
export interface MessageRow {
  author_member_id?: AuthorMemberId1;
  body: Body3;
  card_kind?: CardKind | null;
  card_ref?: CardRef1;
  channel_id: ChannelId25;
  created_at: CreatedAt22;
  id: Id27;
  kind?: MessageKind1;
  thread_root_id?: ThreadRootId5;
  workspace_id: WorkspaceId24;
}
export interface MessageTaskRefPublic {
  message_id: MessageId6;
  task_id: TaskId9;
}
/**
 * M2：task #n 解析结果（派生持久化）。
 */
export interface MessageTaskRefRow {
  message_id: MessageId7;
  task_id: TaskId10;
}
/**
 * POST /canvases/{id}/nodes：新增画布节点。
 *
 * `kind='agent'` → 由 task_plan 起一个 agent 节点（引用任务，非副本 C8）；`kind='system'` →
 * system_action 必填、check 动作附 command。字段级 kind↔约束由 server 执法（V14/W8）。
 */
export interface NodeCreate {
  command?: Command6;
  kind: CanvasNodeKind;
  project_id?: ProjectId7;
  system_action?: SystemAction | null;
  task_plan?: TaskPlanBody | null;
  title: Title4;
  upstream_node_ids?: UpstreamNodeIds;
  writes_code?: WritesCode1;
}
/**
 * L2 任务计划契约（进入画布/正式立项时必填——PRD §4.3 v1）。
 */
export interface TaskPlanBody {
  acceptance_criteria: AcceptanceCriteria;
  defaults_decided?: DefaultsDecided;
  goal: Goal;
  out_of_scope?: OutOfScope;
  version?: Version1;
}
/**
 * PATCH /canvases/{id}/nodes/{node_id}：改节点标题 / check 命令。
 */
export interface NodePatch {
  command?: Command7;
  title?: Title5;
}
/**
 * PUT /channels/{id}/notification-setting（B §4.5/§11.4）：upsert 懒建；人类成员本人自治
 * （无 admin 门）；Agent 主体 403（通知是人类面）；kind=dm → 422 NOTIF_IN_DM（DM 必达）。
 * GET 无行回默认 `{mode: all}`（响应用 entities.ChannelNotificationSettingPublic）。
 */
export interface NotificationSettingPut {
  mode: NotificationMode2;
}
/**
 * 游标分页：?after=（正序）/ ?before=（倒序回翻）。
 */
export interface Page {
  items: Items1;
  next_cursor?: NextCursor;
}
export interface PingMsg {
  type: Type3;
}
export interface PresenceChangedData {
  kind: MemberKind;
  member_id: MemberId16;
  status: PresenceStatus;
}
/**
 * GET /presence 合并视图：presence 不完全入库（契约 B §4.3 / 契约 D §2 级联裁决）。
 */
export interface PresenceEntry {
  busy_detail?: BusyDetail;
  kind: MemberKind;
  member_id: MemberId17;
  status: PresenceStatus;
}
export interface PresenceSnapshot {
  items: Items2;
}
export interface PreviewSessionPublic {
  fail_log_tail?: FailLogTail;
  id: Id28;
  last_active_at?: LastActiveAt;
  port?: Port1;
  recycled_at?: RecycledAt;
  started_at: StartedAt2;
  status: PreviewStatus;
  task_id: TaskId11;
  workspace_id: WorkspaceId25;
  worktree_id: WorktreeId;
}
export interface PreviewSessionRow {
  fail_log_tail?: FailLogTail1;
  id: Id29;
  last_active_at?: LastActiveAt1;
  port?: Port2;
  recycled_at?: RecycledAt1;
  started_at: StartedAt3;
  status: PreviewStatus;
  task_id: TaskId12;
  workspace_id: WorkspaceId26;
  worktree_id: WorktreeId1;
}
export interface PreviewStartData {
  dev_command: DevCommand;
  preview_session_id: PreviewSessionId1;
  task_id: TaskId13;
  worktree_path: WorktreePath;
}
export interface PreviewStopData {
  preview_session_id: PreviewSessionId2;
}
export interface PreviewUpdatedData {
  preview: PreviewSessionPublic;
}
export interface ProjectBind {
  project_id: ProjectId8;
}
export interface ProjectCreate {
  computer_id: ComputerId4;
  deploy_command?: DeployCommand;
  dev_command?: DevCommand1;
  name: Name16;
  preview_idle_min?: PreviewIdleMin;
  repo_path: RepoPath3;
  worktree_keep_days?: WorktreeKeepDays;
}
export interface ProjectPatch {
  computer_id?: ComputerId5;
  deploy_command?: DeployCommand1;
  dev_command?: DevCommand2;
  name?: Name17;
  preview_idle_min?: PreviewIdleMin1;
  repo_path?: RepoPath4;
  worktree_keep_days?: WorktreeKeepDays1;
}
/**
 * 频道绑定读面由 channel_projects 联查得出，不落 projects 表。
 */
export interface ProjectPublic {
  channel_ids: ChannelIds;
  computer_id: ComputerId6;
  created_at: CreatedAt23;
  deploy_command?: DeployCommand2;
  dev_command?: DevCommand3;
  id: Id30;
  name: Name18;
  preview_idle_min?: PreviewIdleMin2;
  repo_path: RepoPath5;
  workspace_id: WorkspaceId27;
  worktree_keep_days?: WorktreeKeepDays2;
}
export interface ProjectRow {
  computer_id: ComputerId7;
  created_at: CreatedAt24;
  deploy_command?: DeployCommand3;
  dev_command?: DevCommand4;
  id: Id31;
  name: Name19;
  preview_idle_min?: PreviewIdleMin3;
  repo_path: RepoPath6;
  workspace_id: WorkspaceId28;
  worktree_keep_days?: WorktreeKeepDays3;
}
export interface ProposalConfirm {
  adjustments?: Adjustments1;
  expected: ProposalConfirmExpected;
  removed_ops?: RemovedOps;
}
export interface ProposalConfirmExpected {
  baseline_hash: BaselineHash4;
  baseline_version: BaselineVersion4;
  proposal_hash: ProposalHash;
}
export interface ProposalConfirmResult {
  batch: LandingBatchPublic;
  proposal: ProposalPublic;
}
export interface ProposalPublic {
  adjustments?: Adjustments2;
  base_hash?: BaseHash;
  body: JsonValue;
  channel_id: ChannelId26;
  created_at: CreatedAt25;
  id: Id32;
  kind?: ProposalKind;
  landed_hash?: LandedHash;
  proposal_hash: ProposalHash1;
  proposed_by_member_id: ProposedByMemberId;
  repair_count?: RepairCount;
  revision?: Revision;
  source_task_id: SourceTaskId;
  status?: ProposalStatus;
  updated_at: UpdatedAt2;
  workspace_id: WorkspaceId29;
}
export interface ProposalData {
  proposal: ProposalPublic;
}
export interface ProposalRefData {
  proposal_id: ProposalId1;
}
export interface ProposalReject {
  reason?: Reason;
}
export interface ProposalRow {
  adjustments?: Adjustments3;
  base_hash?: BaseHash1;
  body: JsonValue;
  channel_id: ChannelId27;
  created_at: CreatedAt26;
  id: Id33;
  kind?: ProposalKind1;
  landed_hash?: LandedHash1;
  proposal_hash: ProposalHash2;
  proposed_by_member_id: ProposedByMemberId1;
  repair_count?: RepairCount1;
  revision?: Revision1;
  source_task_id: SourceTaskId1;
  status?: ProposalStatus1;
  updated_at: UpdatedAt3;
  workspace_id: WorkspaceId30;
}
export interface QueryFrame {
  at: At3;
  data: JsonValue;
  frame_id: FrameId1;
  kind?: Kind7;
  type: QueryType;
  v?: V3;
}
export interface ReadPositionPut {
  last_read_message_id: LastReadMessageId1;
}
/**
 * 未读线与未读计数依据；Agent 侧由 deliver ack 写入（契约 D §8.3）。
 */
export interface ReadPositionRow {
  channel_id: ChannelId28;
  last_read_at: LastReadAt1;
  last_read_message_id: LastReadMessageId2;
  member_id: MemberId18;
}
export interface ReadUpdatedData {
  channel_id: ChannelId29;
  last_read_message_id: LastReadMessageId3;
  member_id: MemberId19;
}
/**
 * Agent 主体自设（FR-3.9）；recurring 无 loop_contract → 422（D1-L2）。
 *
 * M4 起（v1.2）：可携内联 `loop_contract`——recurring 必填（缺 → 422），server `model_validate`
 * 后同一事务建 task_contracts（kind=loop_contract、reminder_id 挂接，契约 A §4.3 XOR）并回填
 * reminders.loop_contract_id；once 携带 loop_contract → 422（B §4.4/§10.6）。契约在同事务才
 * 创建，故请求侧不接受 loop_contract_id（那是存储列，非请求字段）。前向引用 LoopContractBody
 * （定义序在后，同 entities.MessagePublic.files 先例），文件末尾 model_rebuild() 补全。
 *
 * cadence（B §10.6/§11.5）：once = ISO-8601 时刻；recurring = interval（ISO-8601 duration，如
 * `PT1H`）或 **cron 五段式**（`分 时 日 月 周`，服务器本地时区，无秒/年/@keyword——M5 v1.3 扩），
 * 且创建时须与 `loop_contract.cadence` 一致（server 校验，不一致 → 422）。cadence 在 contracts 侧
 * 是纯 str（无语义校验器）：cron 值域解析/塌缩式重排的判定归 H4 server 侧单点（纪律 7）。
 */
export interface ReminderCreate {
  anchor_channel_id: AnchorChannelId;
  anchor_message_id?: AnchorMessageId;
  anchor_task_id?: AnchorTaskId;
  cadence: Cadence1;
  kind: Kind8;
  loop_contract?: LoopContractBody | null;
}
export interface ReminderData {
  reminder: ReminderPublic;
}
export interface ReminderPublic {
  agent_member_id: AgentMemberId19;
  anchor_channel_id: AnchorChannelId1;
  anchor_message_id?: AnchorMessageId1;
  anchor_task_id?: AnchorTaskId1;
  cadence: Cadence2;
  cancelled_by_member_id?: CancelledByMemberId;
  created_at: CreatedAt27;
  id: Id34;
  kind: ReminderKind;
  loop_contract_id?: LoopContractId;
  next_fire_at: NextFireAt;
  status?: ReminderStatus;
  workspace_id: WorkspaceId31;
}
/**
 * M1：Agent 自设唤醒（FR-3.9）。CHECK: kind='recurring' → loop_contract_id NOT NULL。
 */
export interface ReminderRow {
  agent_member_id: AgentMemberId20;
  anchor_channel_id: AnchorChannelId2;
  anchor_message_id?: AnchorMessageId2;
  anchor_task_id?: AnchorTaskId2;
  cadence: Cadence3;
  cancelled_by_member_id?: CancelledByMemberId1;
  created_at: CreatedAt28;
  id: Id35;
  kind: ReminderKind;
  loop_contract_id?: LoopContractId1;
  next_fire_at: NextFireAt1;
  status?: ReminderStatus1;
  workspace_id: WorkspaceId32;
}
export interface ReplyFrame {
  data: JsonValue;
  kind?: Kind9;
  ref: Ref3;
  v?: V4;
}
export interface ReportFrame {
  at: At4;
  data: JsonValue;
  frame_id: FrameId2;
  kind?: Kind10;
  type: ReportType;
  v?: V5;
}
export interface RuntimeRescanData {}
export interface RuntimesDetectedData {
  runtimes: Runtimes;
}
/**
 * GET /search 的跳转分组（名称子串命中，NOCASE）。
 */
export interface SearchJumps {
  channels?: Channels;
  members?: Members;
}
export interface SearchMessageResult {
  message: MessagePublic;
  snippet: Snippet;
}
/**
 * 搜索（B §9.6）——GET /search，三分组。
 */
export interface SearchResponse {
  jumps: SearchJumps;
  messages?: Messages1;
  tasks?: Tasks1;
}
export interface SkillsPut {
  skills: Skills2;
}
export interface SubDeployLogMsg {
  deployment_id: DeploymentId4;
  stream: Stream;
  type: Type4;
}
export interface SubDiagnosticMsg {
  agent_member_id: AgentMemberId21;
  stream: Stream1;
  type: Type5;
}
export interface SummaryRunPublic {
  blocked_at?: BlockedAt;
  canvas_id: CanvasId6;
  created_at: CreatedAt29;
  last_fingerprint?: LastFingerprint;
  replan_used?: ReplanUsed;
  round_count?: RoundCount;
  stall_count?: StallCount;
  task_id: TaskId14;
  updated_at: UpdatedAt4;
  workspace_id: WorkspaceId33;
}
/**
 * M8（O8 汇总协调状态，契约 A v1.0.12 §6.4）——汇总任务的循环护栏计数。
 *
 * 行创建时机 = 汇总节点 gating 首次解除（lazy，非落地即建）；三计数（round/stall/replan）一切
 * 推进走**条件 UPDATE CAS**（M6 三度印证教训 + CR-M8-1 合流）。阻断判定仅作用于非终态任务
 * （汇总任务被 close/done → 行随任务终态失效，不清行留痕，F8）。
 */
export interface SummaryRunRow {
  blocked_at?: BlockedAt1;
  canvas_id: CanvasId7;
  created_at: CreatedAt30;
  last_fingerprint?: LastFingerprint1;
  replan_used?: ReplanUsed1;
  round_count?: RoundCount1;
  stall_count?: StallCount1;
  task_id: TaskId15;
  updated_at: UpdatedAt5;
  workspace_id: WorkspaceId34;
}
export interface SysHelloData {
  conn_id: ConnId;
  heartbeat_sec: HeartbeatSec1;
  protocol_v: ProtocolV1;
  server_version: ServerVersion1;
  workspace_id: WorkspaceId35;
}
export interface SysPongData {}
export interface TaskChange {
  actor_member_id?: ActorMemberId3;
  from_status?: TaskStatus1 | null;
  kind: TaskEventKind;
  to_status?: TaskStatus1 | null;
}
export interface TaskContractData {
  contract: TaskContractPublic;
}
export interface TaskContractPublic {
  body: JsonValue;
  created_at: CreatedAt31;
  created_by_member_id: CreatedByMemberId3;
  id: Id36;
  kind: ContractKind;
  reminder_id?: ReminderId1;
  revision?: Revision2;
  superseded_at?: SupersededAt;
  task_id?: TaskId16;
  version: Version2;
  workspace_id: WorkspaceId36;
}
/**
 * M3：L2 契约实例（D1 三种 schema）。task_id 与 reminder_id 恰一非空（CHECK）。
 */
export interface TaskContractRow {
  body: JsonValue;
  created_at: CreatedAt32;
  created_by_member_id: CreatedByMemberId4;
  id: Id37;
  kind: ContractKind;
  reminder_id?: ReminderId2;
  revision?: Revision3;
  superseded_at?: SupersededAt1;
  task_id?: TaskId17;
  version: Version3;
  workspace_id: WorkspaceId37;
}
export interface TaskCreatedData {
  task: TaskPublic;
}
/**
 * GET /tasks/{id}（B §9.8）。
 */
export interface TaskDetail {
  contracts?: Contracts;
  task: TaskPublic;
  usage: TaskUsage;
  worktree?: WorktreePublic | null;
}
/**
 * TaskDetail 的成本聚合（token_usage_events 按 task_id 汇总）。
 */
export interface TaskUsage {
  cache_read_tokens?: CacheReadTokens1;
  cache_write_tokens?: CacheWriteTokens1;
  events?: Events3;
  input_tokens?: InputTokens1;
  output_tokens?: OutputTokens1;
}
export interface WorktreePublic {
  branch: Branch3;
  cleaned_at?: CleanedAt;
  created_at: CreatedAt33;
  id: Id38;
  merge_commit?: MergeCommit;
  merged_at?: MergedAt;
  path: Path4;
  project_id: ProjectId9;
  status: WorktreeStatus;
  task_id: TaskId18;
  workspace_id: WorkspaceId38;
}
export interface TaskEventPublic {
  actor_member_id?: ActorMemberId4;
  created_at: CreatedAt34;
  from_status?: TaskStatus1 | null;
  kind: TaskEventKind;
  owner_member_id?: OwnerMemberId1;
  seq: Seq5;
  task_id: TaskId19;
  to_status?: TaskStatus1 | null;
}
/**
 * M2：状态账本（T5；不可变表）。
 */
export interface TaskEventRow {
  actor_member_id?: ActorMemberId5;
  created_at: CreatedAt35;
  from_status?: TaskStatus1 | null;
  kind: TaskEventKind;
  owner_member_id?: OwnerMemberId2;
  seq: Seq6;
  task_id: TaskId20;
  to_status?: TaskStatus1 | null;
}
/**
 * 跨 Agent 交接契约（置 In Review 时必填，T7 校验非空——PRD §4.3 v1）。
 */
export interface TaskHandoffBody {
  deliverables?: Deliverables;
  evidence?: Evidence1;
  from_member: FromMember;
  open_risks?: OpenRisks;
  review_verdict?: ReviewVerdict | null;
  to_member: ToMember;
  verify_plan: VerifyPlan;
  version?: Version4;
}
/**
 * 元数据补丁（B §4.7）——PATCH /tasks/{id}；不写 task_events，广播 task.updated。
 *
 * `level`：升格载体（M3 P-2 拍板）——仅 `l1→l2` 单向放行；`l2→l1` 或非法值由 server
 * 校验拒 422 TASK_TRANSITION_INVALID（rule=D1）。升格本身不写 task_events。
 */
export interface TaskPatch {
  level?: TaskLevel1 | null;
  silence_override_h?: SilenceOverrideH1;
  title?: Title6;
}
/**
 * M2：带元数据的消息（T1）。blocked 不入库——画布边实时推导（C3）。
 */
export interface TaskRow {
  channel_id: ChannelId30;
  created_at: CreatedAt36;
  created_by_member_id: CreatedByMemberId5;
  id: Id39;
  level?: TaskLevel2;
  number: Number1;
  owner_member_id?: OwnerMemberId3;
  project_id?: ProjectId10;
  root_message_id: RootMessageId1;
  silence_override_h?: SilenceOverrideH2;
  status?: TaskStatus2;
  status_changed_at: StatusChangedAt1;
  title: Title7;
  workspace_id: WorkspaceId39;
  writes_code?: WritesCode2;
}
/**
 * 状态写（B §9.1）——POST /tasks/{id}/status。
 *
 * 非法边 → 422 TASK_TRANSITION_INVALID；to==当前 → 幂等 200。
 */
export interface TaskStatusChange {
  to: TaskStatus1;
}
export interface TaskUpdatedData {
  change?: TaskChange | null;
  task: TaskPublic;
}
/**
 * templates.body（A v1.0.9 §4.10）：DAG 结构 + 角色占位表 + 简报话术（C7）。
 *
 * 保存序列化（B §11.1）：从画布快照仅取 task 节点、pos 不入；占位按节点 owner 去重、无 owner
 * 归"待认领"；plan_skeleton 取该任务当前 TaskPlan 契约 body（无则 null）；writes_code/project_id
 * 从任务行原样带走。校验：model_validate + edges 无环（复用 kernel/graph）+ nodes.role/edges 引用
 * 一致性（server 侧执法）。
 */
export interface TemplateBody {
  briefing?: Briefing;
  edges?: Edges1;
  nodes?: Nodes1;
  roles?: Roles;
}
/**
 * TemplateBody.edges 元素：node key 引用（`from`/`to` 是 Python 关键字，沿 TaskHandoffBody
 * from_member 先例改名 from_key/to_key）；保存与实例化均校验无环（复用 kernel/graph）。
 */
export interface TemplateEdge {
  from_key: FromKey;
  to_key: ToKey;
}
/**
 * TemplateBody.nodes 元素（A v1.0.9 §4.10）：模板内一个 task 节点。
 */
export interface TemplateNode {
  key: Key3;
  plan_skeleton?: TaskPlanBody | null;
  project_id?: ProjectId11;
  role: Role;
  title: Title8;
  writes_code?: WritesCode3;
}
/**
 * TemplateBody.roles 元素（P13 保存模板弹窗提取表）：角色占位。
 */
export interface TemplateRole {
  description?: Description8;
  placeholder: Placeholder;
}
/**
 * POST /templates 存为模板（B §4.12/§11.1）。
 *
 * 服务端读 `channel_id` 频道画布快照序列化 `TemplateBody`（A §4.10 提取规则）；
 * `role_placeholders`（{member_id: 占位名}）覆盖默认 owner 去重占位名；`include_node_ids` 缺省
 * = 全部 task 节点；画布无正式节点 / 存在草稿层 → 409 TEMPLATE_CANVAS_NOT_READY。
 */
export interface TemplateCreate {
  channel_id: ChannelId31;
  description?: Description9;
  include_node_ids?: IncludeNodeIds;
  name: Name20;
  role_placeholders?: RolePlaceholders;
}
/**
 * POST /templates/{id}/instantiate（B §4.12/§11.2）。
 *
 * `role_mapping` 须覆盖 body.roles 全部占位（缺失 → 422 VALIDATION_FAILED，details.missing 列
 * 占位名）；值 null = 该角色节点落地为无 owner（"待认领"）。单事务落地批（`tmpl:<batch_id>:
 * <node_key>` 幂等，接受 Idempotency-Key）。v1.3 收窄：无内联 create——向导"新建"走既有创建
 * Agent 弹窗再回填映射（§7 #8）。
 */
export interface TemplateInstantiate {
  channel_id: ChannelId32;
  role_mapping: RoleMapping;
}
export interface RoleMapping {
  [k: string]: string | null;
}
/**
 * PATCH /templates/{id}：仅模板元数据可改（B §4.12）。
 */
export interface TemplatePatch {
  description?: Description10;
  name?: Name21;
}
export interface TemplatePublic {
  body: TemplateBody;
  builtin?: Builtin2;
  created_at: CreatedAt37;
  created_by_member_id: CreatedByMemberId6;
  description?: Description11;
  id: Id40;
  name: Name22;
  workspace_id: WorkspaceId40;
}
/**
 * M5：DAG 结构 + 角色占位表 + 简报话术（C7）；实例化走落地事务器（tmpl:<batch_id>:…）。
 */
export interface TemplateRow {
  body: TemplateBody;
  builtin?: Builtin3;
  created_at: CreatedAt38;
  created_by_member_id: CreatedByMemberId7;
  description?: Description12;
  id: Id41;
  name: Name23;
  workspace_id: WorkspaceId41;
}
export interface TokenTotals {
  cache_read_tokens?: CacheReadTokens2;
  cache_write_tokens?: CacheWriteTokens2;
  input_tokens?: InputTokens2;
  output_tokens?: OutputTokens2;
}
/**
 * usage 上行条目：id = 适配器 ULID（exactly-once 去重根基，契约 E §7.4）；
 * thread_root_id 为归属提示，server 富化为 task_id 后不落列。
 */
export interface TokenUsageEventIn {
  agent_member_id: AgentMemberId22;
  cache_read_tokens?: CacheReadTokens3;
  cache_write_tokens?: CacheWriteTokens3;
  channel_id?: ChannelId33;
  id: Id42;
  input_tokens?: InputTokens3;
  output_tokens?: OutputTokens3;
  reported_at: ReportedAt;
  source_session?: SourceSession1;
  thread_root_id?: ThreadRootId6;
}
export interface TokenUsageEventPublic {
  agent_member_id: AgentMemberId23;
  cache_read_tokens?: CacheReadTokens4;
  cache_write_tokens?: CacheWriteTokens4;
  channel_id?: ChannelId34;
  id: Id43;
  input_tokens?: InputTokens4;
  output_tokens?: OutputTokens4;
  reported_at: ReportedAt1;
  source_session?: SourceSession2;
  task_id?: TaskId21;
  workspace_id: WorkspaceId42;
}
/**
 * M1 建表：W7 成本口径原始层（仅 provider 上报，永不推导费用）。
 */
export interface TokenUsageEventRow {
  agent_member_id: AgentMemberId24;
  cache_read_tokens?: CacheReadTokens5;
  cache_write_tokens?: CacheWriteTokens5;
  channel_id?: ChannelId35;
  id: Id44;
  input_tokens?: InputTokens5;
  output_tokens?: OutputTokens5;
  reported_at: ReportedAt2;
  source_session?: SourceSession3;
  task_id?: TaskId22;
  workspace_id: WorkspaceId43;
}
export interface TokenUsageReportedData {
  agent_member_id: AgentMemberId25;
  task_id?: TaskId23;
  totals: TokenTotals;
}
export interface UsageBatchData {
  events: Events4;
}
/**
 * UsageReport.breakdown 逐子项（rollup=true 附）：agent 逐任务 / canvas 逐节点任务。
 */
export interface UsageBreakdownItem {
  label: Label;
  ref: Ref4;
  usage: UsageBucket;
}
/**
 * GET /usage?level=task|agent|canvas&ref=&rollup=（B §13.4，响应形状冻结）。
 *
 * 永不折算货币（W7）；`tasks_reporting` 诚实标注覆盖率（level=task 恒 {0/1, 1}）；rollup=true
 * 时附 `breakdown` 逐子项明细，默认省略。聚合 SQL/新账推导活 server 单点（纪律 7）。
 */
export interface UsageReport {
  breakdown?: Breakdown;
  level: UsageLevel;
  ref: Ref5;
  tasks_reporting: TasksReporting;
  usage: UsageBucket;
}
export interface WorkspaceCreate {
  name: Name24;
  slug: Slug;
}
export interface WorkspacePatch {
  attachment_max_mb?: AttachmentMaxMb;
  name?: Name25;
  notif_desktop?: NotifDesktop;
  notif_sound?: NotifSound;
  onboarding_greeting?: OnboardingGreeting;
  setup_state?: SetupState;
  slug?: Slug1;
  ui_theme?: UiTheme | null;
}
export interface WorkspacePublic {
  attachment_max_mb?: AttachmentMaxMb1;
  created_at: CreatedAt39;
  id: Id45;
  name: Name26;
  notif_desktop?: NotifDesktop1;
  notif_sound?: NotifSound1;
  onboarding_greeting?: OnboardingGreeting1;
  setup_state?: SetupState1;
  slug: Slug2;
  ui_theme?: UiTheme1;
}
export interface SetupState1 {
  [k: string]: JsonValue;
}
export interface WorkspaceRow {
  attachment_max_mb?: AttachmentMaxMb2;
  created_at: CreatedAt40;
  id: Id46;
  name: Name27;
  notif_desktop?: NotifDesktop2;
  notif_sound?: NotifSound2;
  onboarding_greeting?: OnboardingGreeting2;
  setup_state?: SetupState2;
  slug: Slug3;
  ui_theme?: UiTheme2;
}
export interface SetupState2 {
  [k: string]: JsonValue;
}
export interface WorkspaceUpdatedData {
  workspace: WorkspacePublic;
}
export interface WorktreeCleanupData {
  task_id: TaskId24;
}
export interface WorktreeEnsureData {
  branch: Branch4;
  project_id: ProjectId12;
  repo_path: RepoPath7;
  task_id: TaskId25;
}
export interface WorktreeMergeData {
  branch: Branch5;
  message: Message2;
  project_id: ProjectId13;
  repo_path: RepoPath8;
  task_id: TaskId26;
}
export interface WorktreeRow {
  branch: Branch6;
  cleaned_at?: CleanedAt1;
  created_at: CreatedAt41;
  id: Id47;
  merge_commit?: MergeCommit1;
  merged_at?: MergedAt1;
  path: Path5;
  project_id: ProjectId14;
  status: WorktreeStatus;
  task_id: TaskId27;
  workspace_id: WorkspaceId44;
}
export interface WorktreeStatusData {
  branch: Branch7;
  conflict_files?: ConflictFiles;
  merge_commit?: MergeCommit2;
  path: Path6;
  status: Status4;
  task_id: TaskId28;
}
export interface WorktreeUpdatedData {
  worktree: WorktreePublic;
}
