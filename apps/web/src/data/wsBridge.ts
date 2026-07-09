// WS → Query 桥:每条信封 patch 对应 query 缓存(setQueryData),实现 NFR1「无刷新更新」。
// 数据形状全部来自 contracts-ts 生成物;未知 type 忽略(契约 C §3:新增事件不算不兼容)。
import type { QueryClient } from '@tanstack/react-query';

import type {
  ChannelsSnapshot,
  Envelope,
  MessagePublic,
  PresenceEntry,
  ReadPositionPublic,
  TaskPublic,
} from '@coagentia/contracts-ts';

import { qk } from '../lib/queryKeys';

export function applyEnvelope(qc: QueryClient, env: Envelope): void {
  const data = env.data as never;

  switch (env.type) {
    case 'message.created': {
      const { message } = data as { message: MessagePublic };
      qc.setQueryData<MessagePublic[]>(qk.messages(message.channel_id), (prev) => {
        const list = prev ?? [];
        if (list.some((m) => m.id === message.id)) return list; // 幂等(key/id 去重)
        return [...list, message];
      });
      break;
    }

    case 'task.created':
    case 'task.updated': {
      const { task } = data as { task: TaskPublic };
      qc.setQueryData<TaskPublic[]>(qk.tasks(task.channel_id), (prev) => {
        const list = prev ?? [];
        const i = list.findIndex((t) => t.id === task.id);
        if (i < 0) return [...list, task];
        const next = list.slice();
        next[i] = task;
        return next;
      });
      break;
    }

    case 'presence.changed': {
      const p = data as { member_id: string; kind: PresenceEntry['kind']; status: PresenceEntry['status'] };
      qc.setQueryData<PresenceEntry[]>(qk.presence(), (prev) => {
        const list = prev ?? [];
        const i = list.findIndex((e) => e.member_id === p.member_id);
        const busy_detail =
          p.status === 'busy' ? (i >= 0 ? list[i]!.busy_detail : undefined) : undefined;
        const entry: PresenceEntry = { member_id: p.member_id, kind: p.kind, status: p.status, busy_detail };
        if (i < 0) return [...list, entry];
        const next = list.slice();
        next[i] = entry;
        return next;
      });
      break;
    }

    case 'agent.activity': {
      const a = data as { member_id: string; detail: string };
      qc.setQueryData<PresenceEntry[]>(qk.presence(), (prev) => {
        const list = prev ?? [];
        const i = list.findIndex((e) => e.member_id === a.member_id);
        if (i < 0) return list;
        const next = list.slice();
        next[i] = { ...next[i]!, busy_detail: a.detail };
        return next;
      });
      break;
    }

    case 'read.updated': {
      const r = data as { channel_id: string; member_id: string; last_read_message_id: string };
      qc.setQueryData<ChannelsSnapshot>(qk.channels(), (prev) => {
        if (!prev) return prev;
        const positions = (prev.read_positions as ReadPositionPublic[]) ?? [];
        const i = positions.findIndex(
          (x) => x.channel_id === r.channel_id && x.member_id === r.member_id,
        );
        const entry: ReadPositionPublic = {
          channel_id: r.channel_id,
          member_id: r.member_id,
          last_read_message_id: r.last_read_message_id,
          last_read_at: env.at,
        };
        const next = positions.slice();
        if (i < 0) next.push(entry);
        else next[i] = entry;
        return { ...prev, read_positions: next };
      });
      break;
    }

    case 'token_usage.reported': {
      const u = data as {
        task_id?: string | null;
        totals: { input_tokens: number; output_tokens: number };
      };
      if (!u.task_id) break;
      const taskId = u.task_id;
      qc.setQueryData<Record<string, number>>(qk.usageByTask(), (prev) => {
        const cur = prev ?? {};
        return {
          ...cur,
          [taskId]: (cur[taskId] ?? 0) + u.totals.input_tokens + u.totals.output_tokens,
        };
      });
      break;
    }

    default:
      // 未知/未处理事件:忽略(契约 C §3)
      break;
  }
}
