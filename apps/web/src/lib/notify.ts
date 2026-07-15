// 每频道通知设置（B §11.4）的「人类通知面」消费——纯前端展示策略，不动未读事实/read_positions
// （裁决 #6：mode 只作用通知面，未读事实与显示解耦）。后端唯一消费点是 mute 掐 mention activity 生成；
// 这里只决定「侧栏徽标点亮/弱化」与「桌面通知是否触发」两件展示事。判定单点写在本模块（纪律 7）。
import type {
  ChannelNotificationSettingPublic,
  ChannelsSnapshot,
  MessagePublic,
  NotificationMode,
} from '@coagentia/contracts-ts';

/** 无非默认行时的缺省 mode（GET 无行亦回 {mode: all}）。 */
export const DEFAULT_NOTIFY_MODE: NotificationMode = 'all';

/** 从 ChannelsSnapshot.notification_settings（本人非默认行）取某频道 mode，缺省 all。 */
export function notifyModeOf(
  snap: ChannelsSnapshot | undefined,
  channelId: string,
): NotificationMode {
  const rows =
    (snap?.notification_settings as ChannelNotificationSettingPublic[] | undefined) ?? [];
  return rows.find((r) => r.channel_id === channelId)?.mode ?? DEFAULT_NOTIFY_MODE;
}

/** 未读窗口（lastReadId 之后）内是否 @ 了我（口径同 render.ts 的 mention 高亮：`@meName` 子串）。 */
export function hasUnreadMention(
  msgs: MessagePublic[],
  lastReadId: string | undefined,
  meName: string,
): boolean {
  if (!meName) return false;
  const anchor = lastReadId ? msgs.findIndex((m) => m.id === lastReadId) : -1;
  const start = anchor < 0 ? 0 : anchor + 1;
  const token = `@${meName}`;
  for (let i = start; i < msgs.length; i += 1) {
    if ((msgs[i]?.body ?? '').includes(token)) return true;
  }
  return false;
}

export type BadgeStyle = 'normal' | 'mention' | 'muted';

/**
 * 徽标呈现策略（单点判定）：
 * - all：普通未读 → normal（计数点亮）；
 * - mentions：有未读@ → mention（点亮），否则 muted（即便有普通未读也弱化，「仅有未读@时点亮」）；
 * - mute：恒 muted（弱化/灰，不因未读点亮）。
 * 未读为 0 由渲染层判定不出徽标；本函数只定「若有未读该如何呈现」。
 */
export function badgeStyle(
  mode: NotificationMode,
  unreadMention: boolean,
): BadgeStyle {
  if (mode === 'mute') return 'muted';
  if (mode === 'mentions') return unreadMention ? 'mention' : 'muted';
  return 'normal';
}

/**
 * 桌面通知是否触发（按频道 mode 过滤；无服务端推送，纯 Notification API）：
 * - 自己发的消息不通知；
 * - mute：从不通知；
 * - mentions：仅当 @我 时通知；
 * - all：任意新消息通知。
 */
export function shouldDesktopNotify(
  mode: NotificationMode,
  isMention: boolean,
  isSelf: boolean,
): boolean {
  if (isSelf) return false;
  if (mode === 'mute') return false;
  if (mode === 'mentions') return isMention;
  return true;
}
