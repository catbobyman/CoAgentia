// 头像 + presence 点(设计稿 .av / .av16 / .p)。size: 'msg'(消息流 26px)| 'nav'(侧栏 16px)。
import type { PresenceEntry } from '@coagentia/contracts-ts';

import { PRESENCE_VAR, avatarCfg } from '../lib/uiMaps';

export function Avatar({ name, presence, size }: {
  name: string;
  presence?: PresenceEntry;
  size: 'msg' | 'nav';
}) {
  const cfg = avatarCfg(name);
  const dot = presence ? `var(${PRESENCE_VAR[presence.status] ?? '--border-strong'})` : null;
  const cls = size === 'msg' ? `av${cfg.human ? ' human' : ''}` : 'av16';
  return (
    <span className={cls} style={{ background: `var(--avatar-${cfg.v})` }}>
      {name[0]}
      {dot && (
        <span className={`p${presence?.status === 'busy' ? ' pulse' : ''}`} style={{ background: dot }} />
      )}
    </span>
  );
}
