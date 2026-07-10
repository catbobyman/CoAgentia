// P9 Activity(/activity,改编自设计稿 P9-activity.html):All/Unread/Mentions 三 tab +
// 升级置顶区(M2 恒空但结构就位)+ 事件行(逐项 Mark as done)。
// 数据源 useActivity(filter);WS activity.created/done 由 wsBridge(stage1)实时 patch,列表自动更新。
import { useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { ShieldAlert } from 'lucide-react';

import type { ActivityItemPublic, MemberPublic } from '@coagentia/contracts-ts';

import { api, ApiError, type ActivityFilter } from '../api';
import { memberMap, useActivity, useMembers, channelsOf, useChannelsSnapshot } from '../data/queries';
import { useUiStore } from '../lib/store';
import { relTime } from '../lib/time';
import { useToast } from '../components/Toast';
import './activity.css';

const TABS: { key: ActivityFilter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'unread', label: 'Unread' },
  { key: 'mentions', label: 'Mentions' },
];

// 升级类 kind → 进置顶区(--warning 竖条卡)。
const ESCALATION_KINDS = new Set(['silence_escalation', 'held_escalation', 'fail_closed']);

// 据 kind 合成事件文案(ActivityItemPublic 无预渲染文本,只有引用 id)。
// 「谁」= actor_member_id(触发消息的作者,契约派生字段)——item.member_id 是接收者(恒为
// 查看者本人),M2 二轮 review 确认错用它会把所有行为人渲染成自己。
function phraseOf(item: ActivityItemPublic, byId: Record<string, MemberPublic>, chName: string): string {
  const who = (item.actor_member_id && byId[item.actor_member_id]?.name) || '有人';
  switch (item.kind) {
    case 'mention': return `${who} 在 #${chName} 提及了你`;
    case 'dm': return `${who} 给你发了私信`;
    case 'silence_escalation': return `${who} 的任务长时间静默,已升级`;
    case 'held_escalation': return `${who} 的草稿反复被扣,已升级`;
    case 'fail_closed': return `${who} 触发 fail-closed,需要处理`;
    case 'system': return `${who} 的系统事件`;
    default: return `${who} 的活动`;
  }
}

export function ActivityScreen() {
  const navigate = useNavigate();
  const setActiveChannel = useUiStore((s) => s.setActiveChannel);
  const toast = useToast();

  const [tab, setTab] = useState<ActivityFilter>('all');

  const listQ = useActivity(tab);
  const allQ = useActivity('all'); // 计数用(tab 徽标)
  const membersQ = useMembers();
  const channelsQ = useChannelsSnapshot();

  const byId = memberMap(membersQ.data);
  const chNameById = Object.fromEntries(channelsOf(channelsQ.data).map((c) => [c.id, c.name ?? '—']));

  const items = listQ.data ?? [];
  const allItems = allQ.data ?? [];
  const unreadCount = allItems.filter((a) => !a.done_at).length;
  const mentionCount = allItems.filter((a) => a.kind === 'mention' && !a.done_at).length;

  const escalations = items.filter((a) => ESCALATION_KINDS.has(a.kind));
  const normal = items.filter((a) => !ESCALATION_KINDS.has(a.kind));

  const markDone = (id: string) => {
    api.activityDone(id).catch((e: unknown) => {
      const msg = e instanceof ApiError ? e.message : '标记失败';
      toast.push(msg, { tone: 'error' });
    });
  };

  const gotoSource = (item: ActivityItemPublic) => {
    if (!item.channel_id) return;
    setActiveChannel(item.channel_id);
    void navigate({ to: '/', search: { tab: 'chat' } });
  };

  return (
    <main className="main activityscr">
      <div className="ac-head">
        <h1>Activity</h1>
        <nav className="ac-tabsbar">
          {TABS.map((t) => {
            const cnt = t.key === 'unread' ? unreadCount : t.key === 'mentions' ? mentionCount : 0;
            return (
              <div
                key={t.key}
                className={`ac-tab${tab === t.key ? ' active' : ''}`}
                onClick={() => setTab(t.key)}
              >
                {t.label}
                {cnt > 0 && <span className="cnt">{cnt}</span>}
              </div>
            );
          })}
        </nav>
      </div>

      <section className="ac-feed">
        {escalations.length > 0 && (
          <div className="ac-escsec">
            <div className="ac-esclb">─ 需要处理 ─</div>
            {escalations.map((a) => (
              <div className="ac-esccard" key={a.id} onClick={() => gotoSource(a)}>
                <span className="ic"><ShieldAlert /></span>
                <div className="bd">
                  <div className="tl">{phraseOf(a, byId, chNameById[a.channel_id ?? ''] ?? '—')}</div>
                  {a.channel_id && <div className="sub"><span className="ch">#{chNameById[a.channel_id] ?? '—'}</span></div>}
                </div>
                <span className="rt">{relTime(a.created_at)}</span>
              </div>
            ))}
          </div>
        )}

        {normal.map((a) => {
          const done = !!a.done_at;
          return (
            <div className={`ac-row${done ? ' done' : ''}`} key={a.id} onClick={() => gotoSource(a)}>
              <span className={`unread${done ? ' hollow' : ''}`} />
              {a.channel_id && <span className="chchip"><span className="hash">#</span>{chNameById[a.channel_id] ?? '—'}</span>}
              <span className="sm">{phraseOf(a, byId, chNameById[a.channel_id ?? ''] ?? '—')}</span>
              <span className="rt">{relTime(a.created_at)}</span>
              <span className="mkd">
                {!done && (
                  <button className="btn btn-ghost" onClick={(e) => { e.stopPropagation(); markDone(a.id); }}>
                    Mark as done
                  </button>
                )}
              </span>
            </div>
          );
        })}

        {items.length === 0 && (
          <div className="ac-empty">
            {tab === 'all' ? '暂无活动' : tab === 'unread' ? '没有未读活动' : '没有提及'}
          </div>
        )}
      </section>
    </main>
  );
}
