// 频道侧栏(240px):频道分组(含 ops-private🔒 复发点 4)+ DM 分组 + 已归档折叠分组 + 新建频道。
// 复发点 4:频道列表须含 ops-private(锁标)与「已归档」折叠分组。
// F8：主分组排除 archived；「已归档」分组头接展开逻辑，展开渲染 archived 频道（只读进入可浏览）。
import { useState } from 'react';
import { Archive, BellOff, ChevronDown, ChevronRight, Lock, Plus } from 'lucide-react';

import type { ChannelPublic, MemberPublic, NotificationMode, PresenceEntry } from '@coagentia/contracts-ts';

import { Avatar } from './Avatar';
import { NewChannelModal } from './NewChannelModal';
import { ProjectSidebarSection } from './ProjectSidebarSection';
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
  // PS-WT ①：仅 admin 渲染侧栏「项目」区（GET /projects 是 admin 面）。
  canManageProjects?: boolean;
}

export function ChannelList(props: ChannelListProps) {
  const { channels, activeChannelId, unreadCount, presenceOf, dmPeer, onSelectChannel } = props;
  const roomChannels = channels.filter((c) => c.kind === 'channel' && !c.archived_at);
  const archivedChannels = channels.filter((c) => c.kind === 'channel' && !!c.archived_at);
  const dms = channels.filter((c) => c.kind === 'dm');
  const [archOpen, setArchOpen] = useState(false);
  // B-M8-3 新建频道弹窗（补齐「新建频道」死壳的真实入口）。
  const [newChannelOpen, setNewChannelOpen] = useState(false);

  const selectChannel = (ch: ChannelPublic) => {
    onSelectChannel(ch);
    props.onMobileClose?.();
  };

  return (
    <aside className={`chlist${props.mobileOpen ? ' mobile-open' : ''}`}>
      {/* PS-WT ① 侧栏「项目」区（置于 CHANNELS 之上；仅 admin 渲染）。 */}
      <ProjectSidebarSection
        channels={channels}
        activeChannelId={activeChannelId}
        onSelectChannel={selectChannel}
        canManage={props.canManageProjects ?? false}
      />
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
      {/* F8「已归档」折叠分组：仅有归档频道时出现；展开渲染 archived 频道（只读进入可浏览）。 */}
      {archivedChannels.length > 0 && (
        <>
          <div
            className="arch"
            role="button"
            aria-expanded={archOpen}
            aria-label="已归档频道"
            onClick={() => setArchOpen((v) => !v)}
          >
            {archOpen ? <ChevronDown /> : <ChevronRight />}<Archive /><span>已归档</span>
            <span className="arch-n">{archivedChannels.length}</span>
          </div>
          {archOpen && archivedChannels.map((ch) => (
            <div
              key={ch.id}
              className={`ch archived${ch.id === activeChannelId ? ' active' : ''}`}
              onClick={() => selectChannel(ch)}
            >
              {ch.is_private
                ? <span className="lock"><Lock /></span>
                : <span className="hash">#</span>}
              <span className="nm">{ch.name}</span>
            </div>
          ))}
        </>
      )}
      <div
        className="newch"
        role="button"
        aria-label="新建频道"
        onClick={() => setNewChannelOpen(true)}
      ><Plus /><span>新建频道</span></div>
      {props.onPlayTimeline && (
        <button className="playbtn" onClick={props.onPlayTimeline}>▶ 播放时间线</button>
      )}
      {newChannelOpen && (
        <NewChannelModal
          onClose={() => setNewChannelOpen(false)}
          onCreated={(ch) => {
            setNewChannelOpen(false);
            onSelectChannel(ch);
            props.onMobileClose?.();
          }}
        />
      )}
    </aside>
  );
}
