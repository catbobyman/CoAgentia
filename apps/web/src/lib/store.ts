// zustand:轻客户端状态（画布选中/面板开合）+ WS 连接状态（重连 UI 数据源）。
// 服务端数据全在 TanStack Query 缓存,不进这里(职责分离,选型 00 §2 前端表)。
import { create } from 'zustand';

export type ConnStatus = 'connecting' | 'online' | 'reconnecting';

export interface ConnectionState {
  status: ConnStatus;
  attempt: number; // 第 n 次重连(0 = 尚未重连过)
}

export interface UiState {
  // 当前活跃频道(布局壳选择,index 屏消费;M1 单屏在 store,多频道深链 B2 再提到 URL)
  activeChannelId: string | null;
  // 画布/面板轻状态(B2 起用;基座先立字段与 setter)
  selectedNodeId: string | null;
  threadPanelOpen: boolean;
  // WS 连接态(重连 2px 进度条 + toast 的唯一数据源,契约 C §2 / 交互 §13)
  connection: ConnectionState;

  setActiveChannel: (id: string | null) => void;
  setSelectedNode: (id: string | null) => void;
  setThreadPanelOpen: (open: boolean) => void;
  setConnection: (c: ConnectionState) => void;
}

export const useUiStore = create<UiState>((set) => ({
  activeChannelId: null,
  selectedNodeId: null,
  threadPanelOpen: false,
  connection: { status: 'connecting', attempt: 0 },

  setActiveChannel: (activeChannelId) => set({ activeChannelId }),
  setSelectedNode: (selectedNodeId) => set({ selectedNodeId }),
  setThreadPanelOpen: (threadPanelOpen) => set({ threadPanelOpen }),
  setConnection: (connection) => set({ connection }),
}));
