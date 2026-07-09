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
  TaskPublic,
  WorkspaceCreate,
  WorkspacePublic,
} from '@coagentia/contracts-ts';

export const API_BASE = 'http://127.0.0.1:8642';

type MessagesPage =
  RestPaths['/api/channels/{channel_id}/messages']['get']['responses']['200']['content']['application/json'];
type TasksPage =
  RestPaths['/api/tasks']['get']['responses']['200']['content']['application/json'];
type ThreadPage =
  RestPaths['/api/messages/{message_id}/thread']['get']['responses']['200']['content']['application/json'];
type DiagnosticsPage =
  RestPaths['/api/agents/{member_id}/diagnostics']['get']['responses']['200']['content']['application/json'];

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

  playTimeline: () => fetch(`${API_BASE}/__mock/play`, { method: 'POST' }),
};
