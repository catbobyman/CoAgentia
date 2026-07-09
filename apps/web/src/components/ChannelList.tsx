// 频道侧栏(240px):频道分组(含 ops-private🔒 复发点 4)+ DM 分组 + 已归档折叠分组 + 新建频道。
// 复发点 4:频道列表须含 ops-private(锁标)与「已归档」折叠分组。
import { Archive, ChevronRight, Lock, Plus } from 'lucide-react';

import type { ChannelPublic, MemberPublic, PresenceEntry } from '@coagentia/contracts-ts';

import { Avatar } from './Avatar';

export interface ChannelListProps {
  channels: ChannelPublic[];
  activeChannelId: string | undefined;
  unreadCount: (ch: ChannelPublic) => number;
  presenceOf: (memberId: string) => PresenceEntry | undefined;
  dmPeer: (ch: ChannelPublic) => MemberPublic | undefined;
  onSelectChannel: (ch: ChannelPublic) => void;
  onPlayTimeline?: () => void;
}

export function ChannelList(props: ChannelListProps) {
  const { channels, activeChannelId, unreadCount, presenceOf, dmPeer, onSelectChannel } = props;
  const roomChannels = channels.filter((c) => c.kind === 'channel');
  const dms = channels.filter((c) => c.kind === 'dm');

  return (
    <aside className="chlist">
      <div className="grp">Channels</div>
      {roomChannels.map((ch) => {
        const n = unreadCount(ch);
        const active = ch.id === activeChannelId;
        return (
          <div
            key={ch.id}
            className={`ch${active ? ' active' : ''}${n && !active ? ' unread' : ''}`}
            onClick={() => onSelectChannel(ch)}
          >
            {ch.is_private
              ? <span className="lock"><Lock /></span>
              : <span className="hash">#</span>}
            <span className="nm">{ch.name}</span>
            {n > 0 && !active && <span className="cnt">{n}</span>}
          </div>
        );
      })}

      <div className="grp">Direct Messages</div>
      {dms.map((ch) => {
        const peer = dmPeer(ch);
        if (!peer) return null;
        return (
          <div key={ch.id} className="ch" onClick={() => onSelectChannel(ch)}>
            <Avatar name={peer.name} presence={presenceOf(peer.id)} size="nav" />
            <span className="nm">{peer.name}</span>
          </div>
        );
      })}

      <div className="sp" />
      <div className="arch"><ChevronRight /><Archive /><span>已归档</span></div>
      <div className="newch"><Plus /><span>新建频道</span></div>
      {props.onPlayTimeline && (
        <button className="playbtn" onClick={props.onPlayTimeline}>▶ 播放时间线</button>
      )}
    </aside>
  );
}
