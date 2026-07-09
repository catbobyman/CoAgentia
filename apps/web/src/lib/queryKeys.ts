// 集中式 query key 工厂:WS 桥接 patch 与查询 hook 共用同一组 key,避免漂移。
export const qk = {
  workspace: () => ['workspace'] as const,
  members: () => ['members'] as const,
  channels: () => ['channels'] as const,
  presence: () => ['presence'] as const,
  messages: (channelId: string) => ['messages', channelId] as const,
  tasks: (channelId: string) => ['tasks', channelId] as const,
  usageByTask: () => ['usageByTask'] as const, // 无 REST 源:纯 WS 累加(token_usage.reported)
};
