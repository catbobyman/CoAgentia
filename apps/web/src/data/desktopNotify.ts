// 桌面通知（Notification API，无服务端推送——裁决 #6/§2 非目标）：WS message.created 到达时，
// 按该频道通知 mode 过滤是否弹系统通知。仅在浏览器已授予通知权限时触发（不主动弹权限请求：
// 无工作区级总开关的 off 入口前不打扰用户，privacy-preserving）。判定单点 = lib/notify.shouldDesktopNotify。
import type { QueryClient } from '@tanstack/react-query';
import type {
  ChannelPublic,
  ChannelsSnapshot,
  Envelope,
  MemberPublic,
  MessagePublic,
} from '@coagentia/contracts-ts';

import { qk } from '../lib/queryKeys';
import { notifyModeOf, shouldDesktopNotify } from '../lib/notify';

export function maybeDesktopNotify(qc: QueryClient, env: Envelope): void {
  if (env.type !== 'message.created') return;
  if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return;

  const { message } = env.data as { message: MessagePublic };
  const members = qc.getQueryData<MemberPublic[]>(qk.members());
  const me = members?.find((m) => m.kind === 'human' && m.role === 'owner');
  if (!me) return;

  const snap = qc.getQueryData<ChannelsSnapshot>(qk.channels());
  const mode = notifyModeOf(snap, message.channel_id);
  const isSelf = message.author_member_id === me.id;
  const isMention = (message.body ?? '').includes(`@${me.name}`);
  if (!shouldDesktopNotify(mode, isMention, isSelf)) return;

  const author = members?.find((m) => m.id === message.author_member_id);
  const channel = (snap?.items as ChannelPublic[] | undefined)?.find((c) => c.id === message.channel_id);
  const title = channel?.name ? `#${channel.name}` : '新消息';
  const bodyText = `${author?.name ?? '有人'}: ${message.body ?? ''}`.slice(0, 120);
  try {
    // tag = message.id 去重（同消息重放不叠加）。
    new Notification(title, { body: bodyText, tag: message.id });
  } catch {
    // 个别环境 Notification 构造受限——纯增益，静默忽略不影响主流。
  }
}
