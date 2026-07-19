// 布局壳(跨路由常驻):Rail + 频道侧栏 + <Outlet/> + WS 重连条。WS 生命周期挂这里(基座级)。
// B2 其它屏共享此壳;M1 只有会话屏经 <Outlet/> 渲染。
import { useEffect, useState } from 'react';
import { Outlet } from '@tanstack/react-router';
import { useQueries } from '@tanstack/react-query';

import type { ChannelPublic, MemberPublic, MessagePublic } from '@coagentia/contracts-ts';

import { api, IS_MOCK } from '../api';
import { qk } from '../lib/queryKeys';
import { useUiStore } from '../lib/store';
import { useWsSync } from '../data/useWsSync';
import {
  channelsOf, memberMap, presenceMap, readPositionsMap,
  useChannelsSnapshot, useMembers, usePresence, useWorkspace,
} from '../data/queries';
import { applyTheme } from '../lib/theme';
import { Rail } from '../components/Rail';
import { ChannelList } from '../components/ChannelList';
import { hasUnreadMention, notifyModeOf } from '../lib/notify';
import { ReconnectBar } from '../components/ReconnectBar';
import { ToastProvider, Toaster } from '../components/Toast';
import { SearchOverlay } from '../components/SearchOverlay';

export function RootLayout() {
  useWsSync(); // 基座级 WS:连接 + 事件 patch + 重连 + 重同步
  const [channelDrawerOpen, setChannelDrawerOpen] = useState(false);

  const activeChannelId = useUiStore((s) => s.activeChannelId);
  const setActiveChannel = useUiStore((s) => s.setActiveChannel);
  const setSearchOpen = useUiStore((s) => s.setSearchOpen);

  // 全局 Ctrl/Cmd+K 打开搜索;Esc 在开启态关闭(P10)。注册一次,窗口级。
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        setSearchOpen(true);
      } else if (e.key === 'Escape' && useUiStore.getState().searchOpen) {
        setSearchOpen(false);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [setSearchOpen]);

  const channelsQ = useChannelsSnapshot();
  const membersQ = useMembers();
  const presenceQ = usePresence();
  const workspaceQ = useWorkspace();

  // F4 主题应用：workspace.ui_theme 落 documentElement[data-theme]（PATCH 落库 + WS workspace.updated
  // 反流均经 workspace 缓存流过此处）。'system' 期间跟随 OS 明暗切换实时重应用。
  const uiTheme = workspaceQ.data?.ui_theme;
  useEffect(() => {
    if (!uiTheme) return;
    applyTheme(uiTheme);
    if (uiTheme !== 'system' || typeof window === 'undefined' || !window.matchMedia) return;
    const mq = window.matchMedia('(prefers-color-scheme: light)');
    const onChange = () => applyTheme('system');
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, [uiTheme]);

  const channels = channelsOf(channelsQ.data);
  const members = membersQ.data ?? [];
  const byId = memberMap(members);
  const presence = presenceMap(presenceQ.data);
  const me = members.find((m: MemberPublic) => m.kind === 'human' && m.role === 'owner');
  const firstAgent = members.find((m: MemberPublic) => m.kind === 'agent');
  const readPositions = readPositionsMap(channelsQ.data);

  // 默认活跃频道 = #build(数据到位后设一次)
  useEffect(() => {
    if (activeChannelId) return;
    const build = channels.find((c) => c.name === 'build') ?? channels[0];
    if (build) setActiveChannel(build.id);
  }, [activeChannelId, channels, setActiveChannel]);

  // 各频道消息(侧栏未读徽标需全频道计数;WS message.created 会 patch 对应 key)
  const msgQueries = useQueries({
    queries: channels
      .filter((c) => c.kind === 'channel')
      .map((c) => ({
        queryKey: qk.messages(c.id),
        queryFn: async () => (await api.messages(c.id)).items as MessagePublic[],
      })),
  });
  const msgByChannel: Record<string, MessagePublic[]> = {};
  channels.filter((c) => c.kind === 'channel').forEach((c, i) => {
    msgByChannel[c.id] = (msgQueries[i]?.data as MessagePublic[]) ?? [];
  });

  const unreadCount = (ch: ChannelPublic): number => {
    const msgs = msgByChannel[ch.id] ?? [];
    const lastId = readPositions[ch.id]?.last_read_message_id;
    if (!lastId) return msgs.length;
    const idx = msgs.findIndex((m) => m.id === lastId);
    return idx < 0 ? 0 : msgs.length - idx - 1;
  };

  const dmPeer = (ch: ChannelPublic): MemberPublic | undefined => {
    const ids = ch.dm_key?.split(':') ?? [];
    return byId[ids.find((id) => id !== me?.id) ?? ''];
  };

  if (!me) {
    return <div className="boot">connecting…</div>;
  }

  return (
    <ToastProvider>
      <ReconnectBar />
      <div className="app">
        <Rail
          meName={me.name}
          firstAgentId={firstAgent?.id}
          onToggleChannels={() => setChannelDrawerOpen((open) => !open)}
        />
        {channelDrawerOpen && (
          <button
            className="chlist-backdrop"
            aria-label="关闭频道列表"
            onClick={() => setChannelDrawerOpen(false)}
          />
        )}
        <ChannelList
          channels={channels}
          activeChannelId={activeChannelId ?? undefined}
          unreadCount={unreadCount}
          notifyMode={(ch) => notifyModeOf(channelsQ.data, ch.id)}
          hasUnreadMention={(ch) =>
            hasUnreadMention(msgByChannel[ch.id] ?? [], readPositions[ch.id]?.last_read_message_id, me.name)
          }
          presenceOf={(id) => presence[id]}
          dmPeer={dmPeer}
          onSelectChannel={(ch) => setActiveChannel(ch.id)}
          canManageProjects={me.role === 'owner' || me.role === 'admin'}
          onPlayTimeline={IS_MOCK ? () => void api.playTimeline() : undefined}
          mobileOpen={channelDrawerOpen}
          onMobileClose={() => setChannelDrawerOpen(false)}
        />
        <Outlet />
      </div>
      <SearchOverlay />
      <Toaster />
    </ToastProvider>
  );
}
