// 频道侧栏(240px):频道分组(含 ops-private🔒 复发点 4)+ DM 分组 + 已归档折叠分组 + 新建频道。
// 复发点 4:频道列表须含 ops-private(锁标)与「已归档」折叠分组。
import { Archive, BellOff, ChevronRight, Lock, Plus } from 'lucide-react';

import type { ChannelPublic, MemberPublic, NotificationMode, PresenceEntry } from '@coagentia/contracts-ts';

import { Avatar } from './Avatar';
import { badgeStyle } from '../lib/notify';

export interface ChannelListProps {
  channels: ChannelPublic[];
  activeChannelId: string | undefined;
  unreadCount: (ch: ChannelPublic) => number;
  presenceOf: (memberId: string) => PresenceEntry | undefined;
  dmPeer: (ch: ChannelPublic) => MemberPublic | undefined;
  onSelectChannel: (ch: ChannelPublic) => void;
  // M5(B §11.4):每频道通知 mode + 未读窗口是否 @我——决定徽标点亮/弱化(裁决 #6:只作用通知面)。
  notifyMode?: (ch: ChannelPublic) => NotificationMode;
  hasUnreadMention?: (ch: ChannelPublic) => boolean;
  onPlayTimeline?: () => void;
  mobileOpen?: boolean;
  onMobileClose?: () => void;
}

export function ChannelList(props: ChannelListProps) {
  const { channels, activeChannelId, unreadCount, presenceOf, dmPeer, onSelectChannel } = props;
  const roomChannels = channels.filter((c) => c.kind === 'channel');
  const dms = channels.filter((c) => c.kind === 'dm');

  return (
    <aside className={`chlist${props.mobileOpen ? ' mobile-open' : ''}`}>
      <div className="grp">Channels</div>
      {roomChannels.map((ch) => {
        const n = unreadCount(ch);
        const active = ch.id === activeChannelId;
        const mode = props.notifyMode?.(ch) ?? 'all';
        const style = badgeStyle(mode, props.hasUnreadMention?.(ch) ?? false);
        // 点亮 = 有未读 且 非当前 且 mode 未弱化(mute / mentions 无@)；mute 频道名恒弱化。
        const lit = n > 0 && !active && style !== 'muted';
        return (
          <div
            key={ch.id}
            className={`ch${active ? ' active' : ''}${lit ? ' unread' : ''}${mode === 'mute' ? ' muted' : ''}`}
            onClick={() => {
              onSelectChannel(ch);
              props.onMobileClose?.();
            }}
          >
            {ch.is_private
              ? <span className="lock"><Lock /></span>
              : <span className="hash">#</span>}
            <span className="nm">{ch.name}</span>
            {mode === 'mute' && <span className="chmute" aria-label="已静音"><BellOff /></span>}
            {lit && <span className={`cnt${style === 'mention' ? ' mention' : ''}`}>{n}</span>}
          </div>
        );
      })}

      <div className="grp">Direct Messages</div>
      {dms.map((ch) => {
        const peer = dmPeer(ch);
        if (!peer) return null;
        return (
          <div
            key={ch.id}
            className="ch"
            onClick={() => {
              onSelectChannel(ch);
              props.onMobileClose?.();
            }}
          >
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
