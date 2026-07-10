// WS → Query 桥:每条信封 patch 对应 query 缓存(setQueryData),实现 NFR1「无刷新更新」。
// 数据形状全部来自 contracts-ts 生成物;未知 type 忽略(契约 C §3:新增事件不算不兼容)。
import type { QueryClient } from '@tanstack/react-query';

import type {
  ActivityItemPublic,
  ChannelsSnapshot,
  Envelope,
  MemberPublic,
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
      // 消息可能携带文件绑定而 channelFiles 无专属事件——失效让文件页签/消息流附件卡
      // 在停留会话中收敛(M2 二轮 review:Agent 交付文件不实时)。仅激活观察者才 refetch。
      void qc.invalidateQueries({ queryKey: qk.channelFiles(message.channel_id) });
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
      // MVP 单人类:ChannelsSnapshot.read_positions 只反映当前 human owner 的已读游标
      // (契约 B §4.5「自身 read-position」)。read.updated 会为每个成员(含 agent)广播,
      // 若不过滤,agent 游标会污染快照并在 readPositionsMap 折叠时覆盖 owner→未读计数错(#6/#7)。
      const members = qc.getQueryData<MemberPublic[]>(qk.members());
      const owner = members?.find((m) => m.kind === 'human' && m.role === 'owner');
      if (!owner || r.member_id !== owner.id) break;
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
      // TaskDetail.usage 是 REST 快照——失效让已打开的线程面板跟上实时上报
      // (M2 二轮 review:面板徽章冻结在打开时刻,与消息流任务牌口径矛盾)。
      void qc.invalidateQueries({ queryKey: qk.taskDetail(taskId) });
      break;
    }

    case 'activity.created': {
      // 前端只有 'all' 单档(挂账批2 简化:tab 过滤归 ActivityScreen 客户端),恒 upsert(缺则建)。
      const { item } = data as { item: ActivityItemPublic };
      // 全局广播携带的 member_id=接收者;REST 读面只回 Owner 本人条目,缓存口径必须一致——
      // 否则多人类工作区他人条目泄入列表/徽标,refetch 后又消失(闪烁)。members 未加载时放行(单人 MVP)。
      const actMembers = qc.getQueryData<MemberPublic[]>(qk.members());
      const actOwner = actMembers?.find((m) => m.kind === 'human' && m.role === 'owner');
      if (actOwner && item.member_id !== actOwner.id) break;
      qc.setQueryData<ActivityItemPublic[]>(qk.activity('all'), (prev) => {
        const list = prev ?? [];
        return list.some((a) => a.id === item.id) ? list : [item, ...list];
      });
      break;
    }

    case 'activity.done': {
      // 标记已读:'all' 单档把该 item 的 done_at 置为事件时间戳(Unread tab 客户端过滤自然收敛)。
      const { item_id } = data as { item_id: string };
      const stamp = env.at;
      qc.setQueryData<ActivityItemPublic[]>(qk.activity('all'), (prev) => {
        if (!prev) return prev;
        const i = prev.findIndex((a) => a.id === item_id);
        if (i < 0) return prev;
        const next = prev.slice();
        next[i] = { ...next[i]!, done_at: stamp };
        return next;
      });
      break;
    }

    default:
      // 未知/未处理事件:忽略(契约 C §3)
      break;
  }
}
