// REST 数据层：类型全部来自 @coagentia/contracts-ts 生成物（零手写实体形状——同源的证明）。
import type {
  AgentCreate,
  AgentPublic,
  AgentSkillPublic,
  CanvasDetail,
  CanvasMutation,
  CanvasNodePublic,
  ChannelProjectPublic,
  ChannelNotificationSettingPublic,
  ChannelPatch,
  ChannelPublic,
  ChannelsSnapshot,
  ComputerCreated,
  ComputerPublic,
  ContractDraftRequest,
  DecomposeRequest,
  DeploymentLogPage,
  DeploymentPublic,
  DiagnosticEventPublic,
  DiffPayload,
  EdgeCreate,
  HeldDraftPublic,
  HeldDraftReleaseResponse,
  HeldDraftResponse,
  InstantiateResult,
  LayoutPut,
  MemberPublic,
  MessageCreated,
  MessagePublic,
  NodeCreate,
  NodePatch,
  NotificationMode,
  PresenceSnapshot,
  PreviewSessionPublic,
  ProjectCreate,
  ProjectPatch,
  ProjectPublic,
  ProposalConfirm,
  ProposalConfirmResult,
  ProposalPublic,
  ReminderPublic,
  RestPaths,
  SearchResponse,
  TaskDetail,
  TaskPatch,
  TaskPublic,
  TaskStatus,
  TemplateCreate,
  TemplateInstantiate,
  TemplatePublic,
  UsageLevel,
  UsageReport,
  WorkspaceCreate,
  WorkspacePublic,
} from '@coagentia/contracts-ts';

// 默认同源：生产由 coagentia-server 托管 dist，Vite 开发态由 proxy 转发到真实 Server。
// 只有显式 VITE_MOCK_MODE=true 时才启用 M2/mock-only 查询与时间线控制面。
export const API_BASE = (import.meta.env.VITE_API_BASE ?? '').replace(/\/$/, '');
export const IS_MOCK = import.meta.env.VITE_MOCK_MODE === 'true';

type MessagesPage =
  RestPaths['/api/channels/{channel_id}/messages']['get']['responses']['200']['content']['application/json'];
type TasksPage =
  RestPaths['/api/tasks']['get']['responses']['200']['content']['application/json'];
type ThreadPage =
  RestPaths['/api/messages/{message_id}/thread']['get']['responses']['200']['content']['application/json'];
type DiagnosticsPage =
  RestPaths['/api/agents/{member_id}/diagnostics']['get']['responses']['200']['content']['application/json'];
type FilesPage =
  RestPaths['/api/channels/{channel_id}/files']['get']['responses']['200']['content']['application/json'];
type ActivityPage =
  RestPaths['/api/activity']['get']['responses']['200']['content']['application/json'];
type HeldDraftsPage =
  RestPaths['/api/held-drafts']['get']['responses']['200']['content']['application/json'];

// 契约里 filter/kind 的值域派生自 REST 查询参数(零手写枚举字面量)。
export type ActivityFilter =
  NonNullable<NonNullable<RestPaths['/api/activity']['get']['parameters']['query']>['filter']>;
export type SearchKind =
  NonNullable<NonNullable<RestPaths['/api/search']['get']['parameters']['query']>['kind']>;

// 契约错误体 { error: { code, message, rule?, details? } }(server api.py ErrorResponse 形状)。
interface ErrorEnvelope {
  error?: { code?: string; message?: string; rule?: string | null; details?: unknown };
}

/** 带契约错误码的前端异常:UI 层 catch 后据 code/details 组 toast 文案(纪律:结构化错误上浮)。
 *  `latest` = 少数端点(§5 STALE_CONFIRM 的 `{error, latest}` 双顶层键)在 error 之外并列的最新态载荷,
 *  由 responseError 从响应体顶层原样带出(草稿确认 409 刷新重审用)。 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;
  readonly latest: unknown;
  constructor(status: number, code: string, message: string, details?: unknown, latest?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details;
    this.latest = latest;
  }
}

/** 统一写请求:非 2xx 时解析契约错误体 → 抛 ApiError(带 code/details 供 toast)。
 *  extraHeaders 可选(如 Idempotency-Key)——同键同体重放由 server 幂等登记收敛，不重复副作用。 */
async function writeJson<T>(
  path: string,
  method: 'POST' | 'PATCH' | 'PUT' | 'DELETE',
  body?: unknown,
  extraHeaders?: Record<string, string>,
): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', ...extraHeaders },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) {
    throw await responseError(r, `${method} ${path} -> ${r.status}`);
  }
  // 204/空体容错(某些 done/unclaim 端点可能无 body)。
  const text = await r.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

/** GET/写请求共用的契约错误解析；Diff 依赖 status/code 区分无树 404 与 daemon 503。 */
async function responseError(r: Response, fallback: string): Promise<ApiError> {
  let code = `HTTP_${r.status}`;
  let message = fallback;
  let details: unknown;
  let latest: unknown;
  try {
    const parsed = (await r.json()) as ErrorEnvelope & { latest?: unknown };
    if (parsed.error) {
      code = parsed.error.code ?? code;
      message = parsed.error.message ?? message;
      details = parsed.error.details;
    }
    // STALE_CONFIRM(§5)响应形状 = `{error, latest}` 双顶层键——latest 与 error 并列,带出供刷新重审。
    latest = parsed.latest;
  } catch {
    // 非 JSON 错误体保留 transport 兜底。
  }
  return new ApiError(r.status, code, message, details, latest);
}

// home/tree 是 daemon 查询帧代理(契约 D §6),mock 无 response_model → OpenAPI 未定形状;此处窄化为 UI 消费形。
export interface HomeEntry {
  name: string;
  kind: 'file' | 'dir';
  size_bytes: number;
  mtime: string;
}
export interface HomeTree {
  entries: HomeEntry[];
}

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`);
  if (!r.ok) throw await responseError(r, `${path} -> ${r.status}`);
  return (await r.json()) as T;
}

export const api = {
  workspace: () => get<WorkspacePublic>('/api/workspace'),
  channels: () => get<ChannelsSnapshot>('/api/channels'),
  members: () => get<MemberPublic[]>('/api/members'),
  presence: () => get<PresenceSnapshot>('/api/presence'),
  // check/merge 输出与冲突锚点都是普通系统消息。只取最旧首页会在频道 >200 条后让最新留痕
  // 从 Canvas/冲突卡消失，故沿 tasks 同款游标护栏翻全（升序拼接，WS 后续继续 append）。
  messages: async (channelId: string): Promise<MessagesPage> => {
    const items: MessagePublic[] = [];
    let after: string | null | undefined;
    for (let page = 0; page < 40; page += 1) {
      const qs = new URLSearchParams({ limit: '200' });
      if (after) qs.set('after', after);
      const batch = await get<MessagesPage>(
        `/api/channels/${channelId}/messages?${qs.toString()}`,
      );
      items.push(...(batch.items as MessagePublic[]));
      after = batch.next_cursor;
      if (!after) break;
    }
    return { items, next_cursor: null } as MessagesPage;
  },
  // 跟进游标翻完全部页——server 升序分页默认 50/页,只取首页会让第 51+ 个(最新)任务
  // 从看板/任务牌/计数整体消失(M2 二轮 review)。页数设护栏防异常游标死循环。
  tasks: async (channelId: string): Promise<TaskPublic[]> => {
    const all: TaskPublic[] = [];
    let after: string | null | undefined;
    for (let page = 0; page < 40; page += 1) {
      const qs = new URLSearchParams({ channel_id: channelId, limit: '200' });
      if (after) qs.set('after', after);
      const p = await get<TasksPage>(`/api/tasks?${qs.toString()}`);
      all.push(...(p.items as TaskPublic[]));
      after = p.next_cursor;
      if (!after) break;
    }
    return all;
  },

  // ---- M2 任务域(B §9.8):详情 / 转任务 / claim-unclaim-assign / 状态流转 / patch
  taskDetail: (taskId: string) => get<TaskDetail>(`/api/tasks/${taskId}`),
  convertToTask: (messageId: string, title?: string) =>
    writeJson<TaskPublic>(`/api/messages/${messageId}/task`, 'POST', title ? { title } : {}),
  claimTask: (taskId: string) => writeJson<TaskPublic>(`/api/tasks/${taskId}/claim`, 'POST'),
  unclaimTask: (taskId: string) => writeJson<TaskPublic>(`/api/tasks/${taskId}/unclaim`, 'POST'),
  assignTask: (taskId: string, memberId: string | null) =>
    writeJson<TaskPublic>(`/api/tasks/${taskId}/assign`, 'POST', { member_id: memberId }),
  setTaskStatus: (taskId: string, to: TaskStatus) =>
    writeJson<TaskPublic>(`/api/tasks/${taskId}/status`, 'POST', { to }),
  patchTask: (taskId: string, patch: Partial<Pick<TaskPublic, 'title' | 'silence_override_h'>>) =>
    writeJson<TaskPublic>(`/api/tasks/${taskId}`, 'PATCH', patch),
  // M3(P-2 拍板):L1→L2 升格(PATCH /tasks/{id},M3a 已在线);l2→l1 或非法值由 server 拒
  // 422 TASK_TRANSITION_INVALID(rule=D1)。升格本身不写 task_events,成功靠 WS task.updated 回灌。
  promoteTask: (taskId: string) => {
    const body: TaskPatch = { level: 'l2' };
    return writeJson<TaskPublic>(`/api/tasks/${taskId}`, 'PATCH', body);
  },
  // M3b(E5):人类越过 gating 强制启动 blocked 任务 → POST /tasks/{id}/force-start;写 task_events
  // (force_start 留痕)。端点随 apps/server M3 并行落地,尚未进 RestPaths → 按契约端点清单
  // (rest.py ENDPOINTS_M3)手写路径,形状上线后不变。无权限(非人类 owner)→ 403;成功靠 WS 反流。
  forceStart: (taskId: string) => writeJson<TaskPublic>(`/api/tasks/${taskId}/force-start`, 'POST'),
  // M3:"让 @Agent 起草"(契约 D 定向直投唤醒)。202 = 已排队,无响应体;daemon 离线 → 503 DAEMON_OFFLINE。
  // request-draft 尚未随 apps/server 一起生成进 RestPaths(后端 M3 并行落地中),此处按 packages/contracts
  // 的 ContractDraftRequest 源类型手写路径 —— 端点上线后形状不变,无需改动。
  requestContractDraft: (taskId: string, body: ContractDraftRequest) =>
    writeJson<void>(`/api/tasks/${taskId}/contracts/request-draft`, 'POST', body),

  // ---- M3b 画布(B §4.9)。GET 已在 RestPaths;结构写端点随 apps/server M3 并行落地,
  // 此处按 packages/contracts 源类型手写路径(端点上线后形状不变)。变更全走 writeJson——
  // GRAPH_CYCLE(V9 连边成环)/DAEMON_OFFLINE 等结构化错误据 code/details 组 toast。无乐观更新,
  // 命中的节点/边靠 canvas.* WS 反流(wsBridge)。
  canvasSnapshot: (channelId: string) => get<CanvasDetail>(`/api/channels/${channelId}/canvas`),
  createCanvasNode: (canvasId: string, body: NodeCreate) =>
    writeJson<CanvasMutation>(`/api/canvases/${canvasId}/nodes`, 'POST', body),
  patchCanvasNode: (canvasId: string, nodeId: string, patch: NodePatch) =>
    writeJson<CanvasMutation>(`/api/canvases/${canvasId}/nodes/${nodeId}`, 'PATCH', patch),
  createCanvasEdge: (canvasId: string, body: EdgeCreate) =>
    writeJson<CanvasMutation>(`/api/canvases/${canvasId}/edges`, 'POST', body),
  deleteCanvasEdge: (canvasId: string, edgeId: string) =>
    writeJson<void>(`/api/canvases/${canvasId}/edges/${edgeId}`, 'DELETE'),
  putCanvasLayout: (canvasId: string, body: LayoutPut) =>
    writeJson<CanvasMutation>(`/api/canvases/${canvasId}/layout`, 'PUT', body),
  retryCanvasNode: (nodeId: string) =>
    writeJson<CanvasNodePublic>(`/api/canvas-nodes/${nodeId}/retry`, 'POST'),

  // ---- M2 文件 / 搜索 / 活动(B §9.6 / §4.6-4.8)
  channelFiles: (channelId: string, after?: string) =>
    get<FilesPage>(
      `/api/channels/${channelId}/files${after ? `?after=${encodeURIComponent(after)}` : ''}`,
    ),
  search: (params: {
    q: string;
    kind?: SearchKind;
    from_member?: string;
    in_channel?: string;
    limit?: number;
  }) => {
    const qs = new URLSearchParams();
    qs.set('q', params.q);
    if (params.kind) qs.set('kind', params.kind);
    if (params.from_member) qs.set('from_member', params.from_member);
    if (params.in_channel) qs.set('in_channel', params.in_channel);
    if (params.limit != null) qs.set('limit', String(params.limit));
    return get<SearchResponse>(`/api/search?${qs.toString()}`);
  },
  activity: (filter?: ActivityFilter, after?: string) => {
    const qs = new URLSearchParams();
    if (filter) qs.set('filter', filter);
    if (after) qs.set('after', after);
    const q = qs.toString();
    return get<ActivityPage>(`/api/activity${q ? `?${q}` : ''}`);
  },
  activityDone: (activityId: string) =>
    writeJson<void>(`/api/activity/${activityId}/done`, 'POST'),

  // ---- M4b HeldDraft(被扣草稿,B §4.14)。GET 列表(?channel_id 过滤,不带 status →
  // server 默认只回活动态 held/reevaluating,§6 重同步清单成员;终态回执由三键响应 / WS 会话内
  // 瞬态呈现,不在列表持久回灌——评审 #1)。release/discard/reevaluate
  // 三键端点随 apps/server M4b 并行落地,尚未进 RestPaths → 按 packages/contracts 端点清单手写路径
  // (端点上线后形状不变,同 forceStart/requestContractDraft 先例)。三键返回:release→{message,
  // held_draft}、discard/reevaluate→{held_draft};409 HELD_DRAFT_RESOLVED(已被终解)由 UI 层
  // 据 error.details.held_draft 静默收敛为终态回执(不弹错)。
  heldDrafts: (channelId: string) =>
    get<HeldDraftsPage>(`/api/held-drafts?channel_id=${encodeURIComponent(channelId)}`).then(
      (p) => p.items as HeldDraftPublic[],
    ),
  releaseHeldDraft: (heldDraftId: string) =>
    writeJson<HeldDraftReleaseResponse>(`/api/held-drafts/${heldDraftId}/release`, 'POST'),
  discardHeldDraft: (heldDraftId: string) =>
    writeJson<HeldDraftResponse>(`/api/held-drafts/${heldDraftId}/discard`, 'POST'),
  reevaluateHeldDraft: (heldDraftId: string) =>
    writeJson<HeldDraftResponse>(`/api/held-drafts/${heldDraftId}/reevaluate`, 'POST'),

  // 走统一写路径:发消息/As Task 失败时同样拿到结构化 ApiError(code/details),
  // 而非裸 `send -> 422`(M2 二轮 review:主写路径绕过了 writeJson 基础设施)。
  sendMessage: (channelId: string, body: string, asTask: boolean) =>
    writeJson<MessageCreated>(
      `/api/channels/${channelId}/messages`,
      'POST',
      asTask ? { body, as_task: {} } : { body },
    ),

  thread: (rootMessageId: string) =>
    get<ThreadPage>(`/api/messages/${rootMessageId}/thread`).then((p) => p.items as MessagePublic[]),

  // ---- 机器(P7)与 Agent 详情(P6)
  computers: () => get<ComputerPublic[]>('/api/computers'),
  agent: (memberId: string) => get<AgentPublic>(`/api/agents/${memberId}`),
  agentSkills: (memberId: string) => get<AgentSkillPublic[]>(`/api/agents/${memberId}/skills`),
  // M5(B §11.3 / R6):技能白名单全量替换制(PUT 覆写),body = { skills: string[] };server 去重保序,
  // R3 门(非创建者/admin → 403 PERMISSION_DENIED)。成功回最新授予列表;另发 AGENT_UPDATED 广播。
  putAgentSkills: (memberId: string, skills: string[]) =>
    writeJson<AgentSkillPublic[]>(`/api/agents/${memberId}/skills`, 'PUT', { skills }),
  agentReminders: (memberId: string) => get<ReminderPublic[]>(`/api/agents/${memberId}/reminders`),
  // P6 取消 reminder(DELETE /reminders/{id},204 无体)。服务端另发 WS reminder.updated 把
  // status 反流为 cancelled;权限不足(非 owner)→ 403,不存在 → 404,据 code 组 toast。
  cancelReminder: (reminderId: string) =>
    writeJson<void>(`/api/reminders/${reminderId}`, 'DELETE'),
  agentDiagnostics: (memberId: string) =>
    get<DiagnosticsPage>(`/api/agents/${memberId}/diagnostics`).then(
      (p) => p.items as DiagnosticEventPublic[],
    ),
  homeTree: (memberId: string, path = '/') =>
    get<HomeTree>(`/api/agents/${memberId}/home/tree?path=${encodeURIComponent(path)}`),

  // ---- P0b 创建工作区(mock 恒 409,仅验形状);P7 Add Computer(明文 api_key 仅一次)
  createWorkspace: async (body: WorkspaceCreate): Promise<WorkspacePublic> => {
    const r = await fetch(`${API_BASE}/api/workspace`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`create workspace -> ${r.status}`);
    return (await r.json()) as WorkspacePublic;
  },
  addComputer: async (name: string): Promise<ComputerCreated> => {
    const r = await fetch(`${API_BASE}/api/computers`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    if (!r.ok) throw new Error(`add computer -> ${r.status}`);
    return (await r.json()) as ComputerCreated;
  },

  // ---- P13 创建 Agent（B §11.3）。role_template_key 可选：携带即按角色模板落地（Orchestrator 引导
  // 链预选），缺省 = 现行创建行为不变。NAME_TAKEN(409) 等结构化错误上浮（writeJson）。
  createAgent: (body: AgentCreate) => writeJson<AgentPublic>('/api/agents', 'POST', body),
  // 频道加成员（B §4.5）。引导链创建 Orchestrator 后须入频道——decompose 的 NO_ORCHESTRATOR
  // 判定按「频道成员中 role_template_key='orchestrator' 的 Agent」（server find_orchestrator）。
  addChannelMember: (channelId: string, memberId: string) =>
    writeJson<void>(`/api/channels/${channelId}/members`, 'POST', { member_id: memberId }),

  // ---- M5(B-M5-1)频道设置弹窗:阈值/描述/公开私有走既有 ChannelPatch(PATCH /channels/{id},
  // require_admin);通知设置走 notification-setting 端点(人类本人自治)。均结构化错误上浮(writeJson)。
  patchChannel: (channelId: string, patch: ChannelPatch) =>
    writeJson<ChannelPublic>(`/api/channels/${channelId}`, 'PATCH', patch),
  // 通知设置(B §4.5/§11.4):PUT upsert 懒建,回 ChannelNotificationSettingPublic;
  // dm 频道 → 422 NOTIF_IN_DM(DM 必达,无设置面),Agent → 403(人类本人自治)。
  putNotificationSetting: (channelId: string, mode: NotificationMode) =>
    writeJson<ChannelNotificationSettingPublic>(
      `/api/channels/${channelId}/notification-setting`,
      'PUT',
      { mode },
    ),

  // ---- M6a Project 域与 Diff 卡。ProjectPublic.channel_ids 是绑定关系的唯一前端读面。
  projects: () => get<ProjectPublic[]>('/api/projects'),
  createProject: (body: ProjectCreate) =>
    writeJson<ProjectPublic>('/api/projects', 'POST', body),
  patchProject: (projectId: string, patch: ProjectPatch) =>
    writeJson<ProjectPublic>(`/api/projects/${projectId}`, 'PATCH', patch),
  deleteProject: (projectId: string) =>
    writeJson<void>(`/api/projects/${projectId}`, 'DELETE'),
  bindProject: (channelId: string, projectId: string) =>
    writeJson<ChannelProjectPublic>(`/api/channels/${channelId}/projects`, 'POST', {
      project_id: projectId,
    }),
  unbindProject: (channelId: string, projectId: string) =>
    writeJson<void>(`/api/channels/${channelId}/projects/${projectId}`, 'DELETE'),
  taskDiff: (taskId: string) => get<DiffPayload>(`/api/tasks/${taskId}/diff`),

  // ---- M7(B-M7-1)预览生命周期（B §13.1）。三端点均无请求体，回 PreviewSessionPublic。
  // POST = ensure + touch 幂等：无活跃会话（status ∈ starting/running）→ 建行（starting）并下发
  // preview.start；已活跃 → 仅推进 last_active_at 返回现状。前端面板打开期按心跳重发 POST（60s，
  // last_active_at 的唯一推进方）。GET 纯读不推进。DELETE 下发 preview.stop（回收），回 recycled 形状。
  startPreview: (taskId: string) =>
    writeJson<PreviewSessionPublic>(`/api/tasks/${taskId}/preview`, 'POST'),
  getPreview: (taskId: string) =>
    get<PreviewSessionPublic>(`/api/tasks/${taskId}/preview`),
  stopPreview: (taskId: string) =>
    writeJson<PreviewSessionPublic>(`/api/tasks/${taskId}/preview`, 'DELETE'),

  // ---- M7b 部署（B-M7-2 / B §13.2-13.4）。触发空体 POST（R8 全员含 Agent；分支/commit 由 server
  // 解析主干 HEAD）：DEPLOY_IN_PROGRESS(409 同 Project 串行)/VALIDATION_FAILED(422 无 deploy_command)/
  // DAEMON_OFFLINE(503) 均结构化 ApiError 上浮，UI 据 code 组 toast。idempotencyKey 可选（防丢响应
  // 网络重试重复触发）。GET 部署纯读（部署卡渲染源）；GET log 直读 server 落盘（不依赖 daemon 在线），
  // after=行号游标翻页。
  createDeployment: (projectId: string, idempotencyKey?: string) =>
    writeJson<DeploymentPublic>(
      `/api/projects/${projectId}/deployments`,
      'POST',
      undefined,
      idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
    ),
  getDeployment: (deploymentId: string) =>
    get<DeploymentPublic>(`/api/deployments/${deploymentId}`),
  deploymentLog: (deploymentId: string, after?: number) =>
    get<DeploymentLogPage>(
      `/api/deployments/${deploymentId}/log${after != null ? `?after=${after}` : ''}`,
    ),

  // ---- M7b 成本核算（B §13.4）。三层聚合（task/agent/canvas）；rollup=true 附 breakdown 明细。
  // 永不折算货币（W7）；tasks_reporting 诚实标注覆盖率。ref 是层锚（task_id / agent_member_id /
  // channel_id）。
  usage: (level: UsageLevel, ref: string, rollup = false) => {
    const qs = new URLSearchParams({ level, ref });
    if (rollup) qs.set('rollup', 'true');
    return get<UsageReport>(`/api/usage?${qs.toString()}`);
  },

  // ---- M6b 拆解编排（B §4.10）。POST decompose 三入口归一（T1 @Orchestrator 消息自然走消息路；
  // T2 携 task_id / T3 携 text）→ 202 ProposalPublic；无 Orchestrator → 409 NO_ORCHESTRATOR
  // （前端据此弹创建引导，交互 §6.8）；Orchestrator 离线 → 503 DAEMON_OFFLINE（引导去 P7）。
  // 均走 writeJson 结构化错误上浮，UI 据 code 分派引导弹窗。
  decompose: (channelId: string, body: DecomposeRequest) =>
    writeJson<ProposalPublic>(`/api/channels/${channelId}/decompose`, 'POST', body),
  // 提案卡渲染源。react-query GET，proposal.updated/draft.* WS 载体刷新。
  proposal: (proposalId: string) => get<ProposalPublic>(`/api/proposals/${proposalId}`),
  // 草稿确认 CAS（B §5）：expected 三字段 + adjustments（full 全量草稿）+ removed_ops（delta 部分接受）。
  // 202 {batch, proposal}；409 STALE_CONFIRM → ApiError.latest 携最新态；409 DELTA_BASE_MISMATCH /
  // 422 VALIDATION_FAILED·NODE_ACTIVE → code/details 上浮（UI 据 code 分派横幅/就地错误清单）。
  confirmProposal: (proposalId: string, body: ProposalConfirm) =>
    writeJson<ProposalConfirmResult>(`/api/proposals/${proposalId}/confirm`, 'POST', body),
  // 拒绝草稿/delta（B §4.10）：理由可空（发进 source 线程，Orchestrator 可读的纠正信号）→ 提案转
  // rejected 终态；非 awaiting → 409 STALE_CONFIRM 携最新态（latest）。
  rejectProposal: (proposalId: string, reason?: string) =>
    writeJson<ProposalPublic>(`/api/proposals/${proposalId}/reject`, 'POST', { reason: reason ?? null }),

  // ---- M5(B-M5-2)模板域(B §4.12/§11.1/§11.2)。列表 GET(builtin 置前，body 全量携带供向导预览)；
  // 存为模板 POST(server 读频道画布快照序列化 TemplateBody，画布无正式节点/有草稿层 → 409
  // TEMPLATE_CANVAS_NOT_READY，入口 disabled 是 UI 责任、API 兜底)；实例化 POST(单事务落地批
  // tmpl:<batch_id>:<node_key> 幂等，role_mapping 缺占位 → 422 VALIDATION_FAILED details.missing)。
  // 均走 writeJson 结构化错误上浮(据 code/details 组 toast)。
  templates: () => get<TemplatePublic[]>('/api/templates'),
  createTemplate: (body: TemplateCreate) =>
    writeJson<TemplatePublic>('/api/templates', 'POST', body),
  // idempotencyKey 可选:同键同体重放回同一批(见 routes/templates.py OPID_REST_IDEMPOTENCY)，
  // 防丢响应网络重试在目标频道重复落地一批。
  instantiateTemplate: (templateId: string, body: TemplateInstantiate, idempotencyKey?: string) =>
    writeJson<InstantiateResult>(
      `/api/templates/${templateId}/instantiate`,
      'POST',
      body,
      idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
    ),

  setReadPosition: (channelId: string, lastReadMessageId: string) =>
    fetch(`${API_BASE}/api/channels/${channelId}/read-position`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ last_read_message_id: lastReadMessageId }),
    }),

  playTimeline: () =>
    IS_MOCK
      ? fetch(`${API_BASE}/__mock/play`, { method: 'POST' })
      : Promise.resolve(new Response(null, { status: 204 })),
};
