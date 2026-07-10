// 全局 rail(48px):像素 logo A(复发点 1)+ 导航图标 + 设置/主题 + owner 头像。
// B2 其它屏复用同一 rail;图标走 lucide-react(实现期本地打包,非 CDN)。
// 功能图标导航到对应路由(机器→/computers、成员→首个 Agent 详情、logo→会话屏)。
import {
  Activity, ListTodo, Menu, Monitor, Search, Settings, SunMoon, Users,
} from 'lucide-react';
import { useNavigate, useRouterState } from '@tanstack/react-router';

import { LOGO_A_BITS } from '../lib/uiMaps';
import { useUiStore } from '../lib/store';

function RailItem({ label, active, dot, onClick, className = '', children }: {
  label: string; active?: boolean; dot?: boolean; onClick?: () => void;
  className?: string; children: React.ReactNode;
}) {
  return (
    <div
      className={`rit${active ? ' active' : ''}${className ? ` ${className}` : ''}`}
      aria-label={label}
      title={label}
      onClick={onClick}
    >
      {children}
      {dot && <span className="dot" />}
    </div>
  );
}

export function Rail({ meName, onToggleChannels }: {
  meName: string;
  firstAgentId?: string; // 保留:RootLayout 仍传入(成员图标已改跳 /members,不再消费)
  onToggleChannels?: () => void;
}) {
  const navigate = useNavigate();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const setSearchOpen = useUiStore((s) => s.setSearchOpen);

  return (
    <nav className="rail">
      <div
        className="logo"
        aria-label="CoAgentia"
        title="CoAgentia"
        onClick={() => void navigate({ to: '/', search: { tab: 'chat' } })}
        style={{ cursor: 'pointer' }}
      >
        {LOGO_A_BITS.map((b, i) => <i key={i} className={b ? 'on' : ''} />)}
      </div>
      <RailItem label="频道" className="mobile-menu" onClick={onToggleChannels}><Menu /></RailItem>
      <RailItem label="搜索 Ctrl+K" onClick={() => setSearchOpen(true)}><Search /></RailItem>
      <RailItem
        label="Activity(有未读)"
        dot
        active={pathname.startsWith('/activity')}
        onClick={() => void navigate({ to: '/activity' })}
      ><Activity /></RailItem>
      <RailItem
        label="任务"
        active={pathname.startsWith('/tasks')}
        onClick={() => void navigate({ to: '/tasks' })}
      ><ListTodo /></RailItem>
      <RailItem
        label="成员"
        active={pathname.startsWith('/members') || pathname.startsWith('/agents')}
        onClick={() => void navigate({ to: '/members' })}
      ><Users /></RailItem>
      <RailItem
        label="机器"
        active={pathname.startsWith('/computers')}
        onClick={() => void navigate({ to: '/computers' })}
      ><Monitor /></RailItem>
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
