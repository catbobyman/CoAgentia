// REST 数据层：类型全部来自 @coagentia/contracts-ts 生成物（零手写实体形状——同源的证明）。
import type {
  AgentPublic,
  AgentSkillPublic,
  ChannelsSnapshot,
  ComputerCreated,
  ComputerPublic,
  DiagnosticEventPublic,
  MemberPublic,
  MessageCreated,
  MessagePublic,
  PresenceSnapshot,
  ReminderPublic,
  RestPaths,
  SearchResponse,
  TaskDetail,
  TaskPublic,
  TaskStatus,
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

// 契约里 filter/kind 的值域派生自 REST 查询参数(零手写枚举字面量)。
export type ActivityFilter =
  NonNullable<NonNullable<RestPaths['/api/activity']['get']['parameters']['query']>['filter']>;
export type SearchKind =
  NonNullable<NonNullable<RestPaths['/api/search']['get']['parameters']['query']>['kind']>;

// 契约错误体 { error: { code, message, rule?, details? } }(server api.py ErrorResponse 形状)。
interface ErrorEnvelope {
  error?: { code?: string; message?: string; rule?: string | null; details?: unknown };
}

/** 带契约错误码的前端异常:UI 层 catch 后据 code/details 组 toast 文案(纪律:结构化错误上浮)。 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;
  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

/** 统一写请求:非 2xx 时解析契约错误体 → 抛 ApiError(带 code/details 供 toast)。 */
async function writeJson<T>(
  path: string,
  method: 'POST' | 'PATCH' | 'PUT',
  body?: unknown,
): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) {
    let code = `HTTP_${r.status}`;
    let message = `${method} ${path} -> ${r.status}`;
    let details: unknown;
    try {
      const parsed = (await r.json()) as ErrorEnvelope;
      if (parsed.error) {
        code = parsed.error.code ?? code;
        message = parsed.error.message ?? message;
        details = parsed.error.details;
      }
    } catch {
      // 非 JSON 错误体:保留默认 code/message
    }
    throw new ApiError(r.status, code, message, details);
  }
  // 204/空体容错(某些 done/unclaim 端点可能无 body)。
  const text = await r.text();
  return (text ? JSON.parse(text) : undefined) as T;
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
  if (!r.ok) throw new Error(`${path} -> ${r.status}`);
  return (await r.json()) as T;
}

export const api = {
  workspace: () => get<WorkspacePublic>('/api/workspace'),
  channels: () => get<ChannelsSnapshot>('/api/channels'),
  members: () => get<MemberPublic[]>('/api/members'),
  presence: () => get<PresenceSnapshot>('/api/presence'),
  messages: (channelId: string) =>
    get<MessagesPage>(`/api/channels/${channelId}/messages?limit=200`),
  tasks: (channelId: string) =>
    get<TasksPage>(`/api/tasks?channel_id=${channelId}`).then((p) => p.items as TaskPublic[]),

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

  sendMessage: async (channelId: string, body: string, asTask: boolean): Promise<MessageCreated> => {
    const r = await fetch(`${API_BASE}/api/channels/${channelId}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(asTask ? { body, as_task: {} } : { body }),
    });
    if (!r.ok) throw new Error(`send -> ${r.status}`);
    return (await r.json()) as MessageCreated;
  },

  thread: (rootMessageId: string) =>
    get<ThreadPage>(`/api/messages/${rootMessageId}/thread`).then((p) => p.items as MessagePublic[]),

  // ---- 机器(P7)与 Agent 详情(P6)
  computers: () => get<ComputerPublic[]>('/api/computers'),
  agent: (memberId: string) => get<AgentPublic>(`/api/agents/${memberId}`),
  agentSkills: (memberId: string) => get<AgentSkillPublic[]>(`/api/agents/${memberId}/skills`),
  agentReminders: (memberId: string) => get<ReminderPublic[]>(`/api/agents/${memberId}/reminders`),
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
