// zustand:轻客户端状态（面板开合/搜索覆盖层）+ WS 连接状态（重连 UI 数据源）。
// 服务端数据全在 TanStack Query 缓存,不进这里(职责分离,选型 00 §2 前端表)。
import { create } from 'zustand';

export type ConnStatus = 'connecting' | 'online' | 'reconnecting';

export interface ConnectionState {
  status: ConnStatus;
  attempt: number; // 第 n 次重连(0 = 尚未重连过)
}

// M7（FR-11.2）并排多任务预览：打开的任务预览面板集合（多个任务各一面板，无全局互斥）。
// idleMin/taskNumber 在打开时从任务的 Project 解析并随身携带——面板只有 taskId，回收倒计时（纯客户端
// 推导 last_active_at+preview_idle_min）与牌头需要它们，避免面板再回查频道/Project。
export interface PreviewTarget {
  taskId: string;
  taskNumber: number;
  /** projects.preview_idle_min（分钟，默认 30）：回收倒计时的空闲窗口。 */
  idleMin: number;
}

export interface UiState {
  // 当前活跃频道(布局壳选择,index 屏消费;M1 单屏在 store,多频道深链 B2 再提到 URL)
  activeChannelId: string | null;
  threadPanelOpen: boolean;
  // 全局搜索覆盖层(P10,Ctrl+K)开合。纯 UI 态,服务端数据仍在 Query 缓存。
  searchOpen: boolean;
  // WS 连接态(重连 2px 进度条 + toast 的唯一数据源,契约 C §2 / 交互 §13)
  connection: ConnectionState;
  // M7 并排预览面板集合（FR-11.2）：openPreview 按 taskId 去重（幂等，重复点[预览]不叠面板）。
  previewTargets: PreviewTarget[];

  setActiveChannel: (id: string | null) => void;
  setThreadPanelOpen: (open: boolean) => void;
  setSearchOpen: (open: boolean) => void;
  toggleSearch: () => void;
  setConnection: (c: ConnectionState) => void;
  openPreview: (target: PreviewTarget) => void;
  closePreview: (taskId: string) => void;
}

export const useUiStore = create<UiState>((set) => ({
  activeChannelId: null,
  threadPanelOpen: false,
  searchOpen: false,
  connection: { status: 'connecting', attempt: 0 },
  previewTargets: [],

  setActiveChannel: (activeChannelId) => set({ activeChannelId }),
  setThreadPanelOpen: (threadPanelOpen) => set({ threadPanelOpen }),
  setSearchOpen: (searchOpen) => set({ searchOpen }),
  toggleSearch: () => set((s) => ({ searchOpen: !s.searchOpen })),
  setConnection: (connection) => set({ connection }),
  openPreview: (target) =>
    set((s) =>
      s.previewTargets.some((t) => t.taskId === target.taskId)
        ? s // 已开则幂等（保留原面板，不重置状态）
        : { previewTargets: [...s.previewTargets, target] },
    ),
  closePreview: (taskId) =>
    set((s) => ({ previewTargets: s.previewTargets.filter((t) => t.taskId !== taskId) })),
}));
