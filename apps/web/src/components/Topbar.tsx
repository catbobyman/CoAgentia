// 频道顶栏(44px):#频道名 + 描述 + 成员堆叠 + 菜单。B2 各屏顶栏复用。
import { Ellipsis } from 'lucide-react';

import type { ChannelPublic } from '@coagentia/contracts-ts';

import { avatarCfg } from '../lib/uiMaps';

export function Topbar({ channel, stackNames }: {
  channel: ChannelPublic;
  stackNames: string[];
}) {
  return (
    <header className="topbar">
      <span className="cname"><span className="hash">#</span>{channel.name}</span>
      <span className="cdesc">{channel.description}</span>
      <div className="stack" aria-label={`频道成员(${stackNames.length})`}>
        {stackNames.map((n) => {
          const cfg = avatarCfg(n);
          return (
            <span
              key={n}
              className="sav"
              style={{
                background: `var(--avatar-${cfg.v})`,
                borderRadius: cfg.human ? 'var(--radius-round)' : 'var(--radius-s)',
              }}
            >{n[0]}</span>
          );
        })}
        <span className="n">{stackNames.length}</span>
      </div>
      <div className="icobtn" aria-label="频道菜单"><Ellipsis /></div>
    </header>
  );
}
