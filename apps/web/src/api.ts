// REST 数据层：类型全部来自 @coagentia/contracts-ts 生成物（零手写实体形状——同源的证明）。
import type {
  ChannelsSnapshot,
  MemberPublic,
  MessageCreated,
  PresenceSnapshot,
  RestPaths,
  TaskPublic,
  WorkspacePublic,
} from '@coagentia/contracts-ts';

export const API_BASE = 'http://127.0.0.1:8642';

type MessagesPage =
  RestPaths['/api/channels/{channel_id}/messages']['get']['responses']['200']['content']['application/json'];
type TasksPage =
  RestPaths['/api/tasks']['get']['responses']['200']['content']['application/json'];

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

  setReadPosition: (channelId: string, lastReadMessageId: string) =>
    fetch(`${API_BASE}/api/channels/${channelId}/read-position`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ last_read_message_id: lastReadMessageId }),
    }),

  playTimeline: () => fetch(`${API_BASE}/__mock/play`, { method: 'POST' }),
};
