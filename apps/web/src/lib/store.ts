// zustand:轻客户端状态（画布选中/面板开合）+ WS 连接状态（重连 UI 数据源）。
// 服务端数据全在 TanStack Query 缓存,不进这里(职责分离,选型 00 §2 前端表)。
import { create } from 'zustand';

export type ConnStatus = 'connecting' | 'online' | 'reconnecting';

export interface ConnectionState {
  status: ConnStatus;
  attempt: number; // 第 n 次重连(0 = 尚未重连过)
}

// M6b 落地事件全局信号（landing.*）：wsBridge 是纯缓存 patch、useWsSync 在 ToastProvider 之外无法
// toast——故把落地事件写进此信号，由 ToastProvider 内的 <LandingToaster> 观察后弹 toast（连接态同款
// 「WS → store → 组件」桥接）。id 单调递增，供组件去重触发。
export interface LandingSignal {
  id: number;
  kind: 'started' | 'completed' | 'fail_closed';
  channelId: string | null;
}

export interface UiState {
  // 当前活跃频道(布局壳选择,index 屏消费;M1 单屏在 store,多频道深链 B2 再提到 URL)
  activeChannelId: string | null;
  // 画布/面板轻状态(B2 起用;基座先立字段与 setter)
  selectedNodeId: string | null;
  threadPanelOpen: boolean;
  // 全局搜索覆盖层(P10,Ctrl+K)开合。纯 UI 态,服务端数据仍在 Query 缓存。
  searchOpen: boolean;
  // WS 连接态(重连 2px 进度条 + toast 的唯一数据源,契约 C §2 / 交互 §13)
  connection: ConnectionState;
  // M6b 频道级激活的草稿层 / delta 面板（channelId → proposalId）。经提案卡进入；rev 替换在 WS 层维护。
  activeDraft: Record<string, string | null>;
  activeDelta: Record<string, string | null>;
  // M6b 落地事件信号（见 LandingSignal 注）。
  landing: LandingSignal | null;

  setActiveChannel: (id: string | null) => void;
  setSelectedNode: (id: string | null) => void;
  setThreadPanelOpen: (open: boolean) => void;
  setSearchOpen: (open: boolean) => void;
  toggleSearch: () => void;
  setConnection: (c: ConnectionState) => void;
  setActiveDraft: (channelId: string, proposalId: string | null) => void;
  setActiveDelta: (channelId: string, proposalId: string | null) => void;
  pushLanding: (kind: LandingSignal['kind'], channelId: string | null) => void;
}

let landingSeq = 0;

export const useUiStore = create<UiState>((set) => ({
  activeChannelId: null,
  selectedNodeId: null,
  threadPanelOpen: false,
  searchOpen: false,
  connection: { status: 'connecting', attempt: 0 },
  activeDraft: {},
  activeDelta: {},
  landing: null,

  setActiveChannel: (activeChannelId) => set({ activeChannelId }),
  setSelectedNode: (selectedNodeId) => set({ selectedNodeId }),
  setThreadPanelOpen: (threadPanelOpen) => set({ threadPanelOpen }),
  setSearchOpen: (searchOpen) => set({ searchOpen }),
  toggleSearch: () => set((s) => ({ searchOpen: !s.searchOpen })),
  setConnection: (connection) => set({ connection }),
  setActiveDraft: (channelId, proposalId) =>
    set((s) => ({ activeDraft: { ...s.activeDraft, [channelId]: proposalId } })),
  setActiveDelta: (channelId, proposalId) =>
    set((s) => ({ activeDelta: { ...s.activeDelta, [channelId]: proposalId } })),
  pushLanding: (kind, channelId) => set({ landing: { id: ++landingSeq, kind, channelId } }),
}));
