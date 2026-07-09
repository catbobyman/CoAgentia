// TanStack Query 包 REST 拉取(api.ts 演化的消费面)。
// 服务端数据的唯一事实源 = 这里的 query 缓存;WS 事件通过 data/wsBridge 做 setQueryData patch。
import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';

import type {
  ChannelPublic,
  ChannelsSnapshot,
  MemberPublic,
  MessagePublic,
  PresenceEntry,
  ReadPositionPublic,
  TaskPublic,
  WorkspacePublic,
} from '@coagentia/contracts-ts';

import { api } from '../api';
import { qk } from '../lib/queryKeys';

// ---- 单实体/列表查询
export const useWorkspace = () =>
  useQuery({ queryKey: qk.workspace(), queryFn: () => api.workspace() });

export const useMembers = () =>
  useQuery({ queryKey: qk.members(), queryFn: () => api.members() });

export const useChannelsSnapshot = () =>
  useQuery({ queryKey: qk.channels(), queryFn: () => api.channels() });

export const usePresence = () =>
  useQuery({
    queryKey: qk.presence(),
    queryFn: async () => (await api.presence()).items,
  });

export const useMessages = (channelId: string | undefined) =>
  useQuery({
    queryKey: qk.messages(channelId ?? '_'),
    queryFn: async () => (await api.messages(channelId!)).items as MessagePublic[],
    enabled: !!channelId,
  });

export const useTasks = (channelId: string | undefined) =>
  useQuery({
    queryKey: qk.tasks(channelId ?? '_'),
    queryFn: () => api.tasks(channelId!),
    enabled: !!channelId,
  });

// usageByTask 无 REST 源:初值空,由 token_usage.reported 累加(wsBridge)。
export const useUsageByTask = () =>
  useQuery({
    queryKey: qk.usageByTask(),
    queryFn: async (): Promise<Record<string, number>> => ({}),
    staleTime: Infinity,
    gcTime: Infinity,
  });

// ---- 派生 selector(纯函数,组件与桥接复用)
export const channelsOf = (snap?: ChannelsSnapshot): ChannelPublic[] =>
  (snap?.items as ChannelPublic[]) ?? [];

export const readPositionsMap = (snap?: ChannelsSnapshot): Record<string, ReadPositionPublic> =>
  Object.fromEntries(
    ((snap?.read_positions as ReadPositionPublic[]) ?? []).map((r) => [r.channel_id, r]),
  );

export const presenceMap = (items?: PresenceEntry[]): Record<string, PresenceEntry> =>
  Object.fromEntries((items ?? []).map((p) => [p.member_id, p]));

export const memberMap = (members?: MemberPublic[]): Record<string, MemberPublic> =>
  Object.fromEntries((members ?? []).map((m) => [m.id, m]));

// 契约 C §4 步骤 1:重连后按频道做消息增量 + 实体快照重同步。
// M1 基座:直接 invalidate 触发 refetch(REST 是事实源,铁律 1);增量 ?after= 的窗口优化留 B2。
export function resyncAll(qc: QueryClient) {
  return qc.invalidateQueries();
}

export function useResync() {
  const qc = useQueryClient();
  return () => resyncAll(qc);
}

export type { WorkspacePublic, MemberPublic, ChannelPublic, MessagePublic, TaskPublic, PresenceEntry };
