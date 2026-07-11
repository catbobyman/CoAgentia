// 频道顶栏(44px):#频道名 + 描述 + 成员堆叠 + ⋯ 菜单(频道设置入口,B-M5-1)。B2 各屏顶栏复用。
import { useState } from 'react';
import { Ellipsis } from 'lucide-react';

import type { ChannelPublic } from '@coagentia/contracts-ts';

import { avatarCfg } from '../lib/uiMaps';

export function Topbar({ channel, stackNames, onOpenSettings }: {
  channel: ChannelPublic;
  stackNames: string[];
  onOpenSettings?: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
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
      <div className="dropwrap">
        <div
          className="icobtn"
          role="button"
          aria-label="频道菜单"
          aria-haspopup="menu"
          onClick={() => setMenuOpen((v) => !v)}
        ><Ellipsis /></div>
        {menuOpen && (
          <div className="drop" role="menu">
            <div
              className="it"
              role="menuitem"
              onClick={() => { setMenuOpen(false); onOpenSettings?.(); }}
            >频道设置</div>
          </div>
        )}
      </div>
    </header>
  );
}
