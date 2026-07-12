// 集中式 query key 工厂:WS 桥接 patch 与查询 hook 共用同一组 key,避免漂移。
export const qk = {
  workspace: () => ['workspace'] as const,
  members: () => ['members'] as const,
  channels: () => ['channels'] as const,
  presence: () => ['presence'] as const,
  messages: (channelId: string) => ['messages', channelId] as const,
  tasks: (channelId: string) => ['tasks', channelId] as const,
  usageByTask: () => ['usageByTask'] as const, // 无 REST 源:纯 WS 累加(token_usage.reported)
  thread: (rootMessageId: string) => ['thread', rootMessageId] as const,
  computers: () => ['computers'] as const,
  agent: (memberId: string) => ['agent', memberId] as const,
  agentSkills: (memberId: string) => ['agentSkills', memberId] as const,
  agentReminders: (memberId: string) => ['agentReminders', memberId] as const,
  agentDiagnostics: (memberId: string) => ['agentDiagnostics', memberId] as const,
  homeTree: (memberId: string) => ['homeTree', memberId] as const,
  // ---- M2
  taskDetail: (taskId: string) => ['taskDetail', taskId] as const,
  taskDiff: (taskId: string) => ['taskDiff', taskId] as const,
  channelFiles: (channelId: string) => ['channelFiles', channelId] as const,
  // ---- M3b 画布:按 channel 存快照;WS canvas.* 事件载 canvas_id,桥接内按 canvas.id 反查该键。
  canvas: (channelId: string) => ['canvas', channelId] as const,
  activity: (filter: string) => ['activity', filter] as const,
  search: (q: string) => ['search', q] as const,
  // ---- M4b HeldDraft:按 channel 存被扣草稿列表;WS held_draft.* 事件载 draft.channel_id,桥接内按此键 patch。
  heldDrafts: (channelId: string) => ['heldDrafts', channelId] as const,
  // ---- M5b 模板:工作区级列表(无频道维度);模板 CRUD 零 WS 事件(裁决 #7),写后 invalidate 收敛。
  templates: () => ['templates'] as const,
  // ---- M6a Project：工作区级小表；频道绑定关系由 ProjectPublic.channel_ids 派生。
  projects: () => ['projects'] as const,
};
