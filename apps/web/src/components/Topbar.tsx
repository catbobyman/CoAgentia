// 频道顶栏(44px):#频道名 + 描述 + 成员堆叠 + ⋯ 菜单(频道设置入口,B-M5-1)。B2 各屏顶栏复用。
import { useState } from 'react';
import { Ellipsis } from 'lucide-react';

import type { ChannelPublic } from '@coagentia/contracts-ts';

import { avatarCfg } from '../lib/uiMaps';

export function Topbar({ channel, stackNames, onOpenSettings, onArchive, onUnarchive, onDelete }: {
  channel: ChannelPublic;
  stackNames: string[];
  onOpenSettings?: () => void;
  // F8 频道菜单：归档/取消归档/删除（DM 无这组——归档语义只作用于频道）。
  onArchive?: () => void;
  onUnarchive?: () => void;
  onDelete?: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const isDm = channel.kind === 'dm';
  const archived = !!channel.archived_at;
  const act = (fn?: () => void) => { setMenuOpen(false); fn?.(); };
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
              onClick={() => act(onOpenSettings)}
            >频道设置</div>
            {/* 归档/删除只作用于频道（DM 无归档语义）。 */}
            {!isDm && !archived && (
              <div className="it" role="menuitem" onClick={() => act(onArchive)}>归档频道</div>
            )}
            {!isDm && archived && (
              <div className="it" role="menuitem" onClick={() => act(onUnarchive)}>取消归档</div>
            )}
            {!isDm && (
              <>
                <div className="sep" />
                <div className="it danger" role="menuitem" onClick={() => act(onDelete)}>删除频道</div>
              </>
            )}
          </div>
        )}
      </div>
    </header>
  );
}
