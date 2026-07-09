// 全局 rail(48px):像素 logo A(复发点 1)+ 导航图标 + 设置/主题 + owner 头像。
// B2 其它屏复用同一 rail;图标走 lucide-react(实现期本地打包,非 CDN)。
import {
  Activity, ListTodo, Monitor, Search, Settings, SunMoon, Users,
} from 'lucide-react';

import { LOGO_A_BITS } from '../lib/uiMaps';

function RailItem({ label, active, dot, children }: {
  label: string; active?: boolean; dot?: boolean; children: React.ReactNode;
}) {
  return (
    <div className={`rit${active ? ' active' : ''}`} aria-label={label} title={label}>
      {children}
      {dot && <span className="dot" />}
    </div>
  );
}

export function Rail({ meName }: { meName: string }) {
  return (
    <nav className="rail">
      <div className="logo" aria-label="CoAgentia">
        {LOGO_A_BITS.map((b, i) => <i key={i} className={b ? 'on' : ''} />)}
      </div>
      <RailItem label="搜索 Ctrl+K"><Search /></RailItem>
      <RailItem label="Activity(有未读)" dot><Activity /></RailItem>
      <RailItem label="任务"><ListTodo /></RailItem>
      <RailItem label="成员"><Users /></RailItem>
      <RailItem label="机器"><Monitor /></RailItem>
      <div className="sp" />
      <RailItem label="设置"><Settings /></RailItem>
      <RailItem label="主题切换"><SunMoon /></RailItem>
      <div className="me">
        {meName[0]}
        <span className="p" style={{ background: 'var(--success)' }} />
      </div>
    </nav>
  );
}
