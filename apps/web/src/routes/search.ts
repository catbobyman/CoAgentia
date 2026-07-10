// 类型化 search params(选型 00 约束 6:URL 即状态,深链 ?tab=&thread=&task=&node= 一等需求)。
// validateChannelSearch 抽为独立纯函数 → 可脱离浏览器单测(深链还原的核心闸)。

export const TABS = ['chat', 'canvas', 'board', 'files'] as const;
export type Tab = (typeof TABS)[number];

export interface ChannelSearch {
  tab: Tab; // 缺省归一为 'chat'
  thread?: string; // 线程根消息 id(B2 线程面板)
  task?: string; // 选中任务 id
  node?: string; // 画布节点 id(B2 画布)
}

// Agent 详情六页签(设计稿 P6 顺序;缺省归一为 profile)。
export const AGENT_TABS = ['profile', 'home', 'skills', 'reminders', 'diagnostics', 'cost'] as const;
export type AgentTab = (typeof AGENT_TABS)[number];

export interface AgentSearch {
  tab: AgentTab; // 缺省归一为 'profile'
}

/** Agent 详情 tab 还原;非法值归一为 profile。 */
export function validateAgentSearch(input: Record<string, unknown>): AgentSearch {
  const raw = input.tab;
  const tab = (AGENT_TABS as readonly string[]).includes(raw as string)
    ? (raw as AgentTab)
    : 'profile';
  return { tab };
}

function asString(v: unknown): string | undefined {
  return typeof v === 'string' && v.length > 0 ? v : undefined;
}

/** 从任意(URL 反序列化)输入还原类型化视图状态;非法 tab 归一为 chat,空串丢弃。 */
export function validateChannelSearch(input: Record<string, unknown>): ChannelSearch {
  const rawTab = input.tab;
  const tab = (TABS as readonly string[]).includes(rawTab as string)
    ? (rawTab as Tab)
    : 'chat';
  return {
    tab,
    thread: asString(input.thread),
    task: asString(input.task),
    node: asString(input.node),
  };
}
