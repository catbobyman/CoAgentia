// 布局壳(跨路由常驻):Rail + 频道侧栏 + <Outlet/> + WS 重连条。WS 生命周期挂这里(基座级)。
// B2 其它屏共享此壳;M1 只有会话屏经 <Outlet/> 渲染。
import { useEffect } from 'react';
import { Outlet } from '@tanstack/react-router';
import { useQueries } from '@tanstack/react-query';

import type { ChannelPublic, MemberPublic, MessagePublic } from '@coagentia/contracts-ts';

import { api } from '../api';
import { qk } from '../lib/queryKeys';
import { useUiStore } from '../lib/store';
import { useWsSync } from '../data/useWsSync';
import {
  channelsOf, memberMap, presenceMap, readPositionsMap,
  useChannelsSnapshot, useMembers, usePresence,
} from '../data/queries';
import { Rail } from '../components/Rail';
import { ChannelList } from '../components/ChannelList';
import { ReconnectBar } from '../components/ReconnectBar';

export function RootLayout() {
  useWsSync(); // 基座级 WS:连接 + 事件 patch + 重连 + 重同步

  const activeChannelId = useUiStore((s) => s.activeChannelId);
  const setActiveChannel = useUiStore((s) => s.setActiveChannel);

  const channelsQ = useChannelsSnapshot();
  const membersQ = useMembers();
  const presenceQ = usePresence();

  const channels = channelsOf(channelsQ.data);
  const members = membersQ.data ?? [];
  const byId = memberMap(members);
  const presence = presenceMap(presenceQ.data);
  const me = members.find((m: MemberPublic) => m.kind === 'human' && m.role === 'owner');
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
    <>
      <ReconnectBar />
      <div className="app">
        <Rail meName={me.name} />
        <ChannelList
          channels={channels}
          activeChannelId={activeChannelId ?? undefined}
          unreadCount={unreadCount}
          presenceOf={(id) => presence[id]}
          dmPeer={dmPeer}
          onSelectChannel={(ch) => setActiveChannel(ch.id)}
          onPlayTimeline={() => void api.playTimeline()}
        />
        <Outlet />
      </div>
    </>
  );
}
