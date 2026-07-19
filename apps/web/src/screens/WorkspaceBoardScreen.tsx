// P11 任务聚合板(/tasks):跨频道聚合全部任务 → Board/List 双视图 + Channel/Creator/Assignee 三过滤。
// 数据源:对每个频道用 qk.tasks(c.id) 逐个查询后 flatten——与 wsBridge 的 task.created/updated patch
// 共用同一组 query key,故拖列改状态后 WS 回灌即实时移列(纪律 7:合法边只认 TASK_TRANSITIONS)。
import { useMemo, useState } from 'react';
import { useQueries } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';

import type { MemberPublic, TaskPublic, TaskStatus } from '@coagentia/contracts-ts';
import { TASK_TRANSITIONS } from '@coagentia/contracts-ts';

import { api } from '../api';
import { ApiError } from '../api';
import { qk } from '../lib/queryKeys';
import { channelsOf, memberMap, useChannelsSnapshot, useMembers } from '../data/queries';
import { useUiStore } from '../lib/store';
import { useToast } from '../components/Toast';
import { Avatar } from '../components/Avatar';
import { STATUS_VAR, STATUS_WORD } from '../lib/uiMaps';
import { relTime } from '../lib/time';
import './workspace-board.css';

const COLUMNS: TaskStatus[] = ['todo', 'in_progress', 'in_review', 'done', 'closed'];

export function WorkspaceBoardScreen() {
  const navigate = useNavigate();
  const setActiveChannel = useUiStore((s) => s.setActiveChannel);
  const toast = useToast();

  const channelsQ = useChannelsSnapshot();
  const membersQ = useMembers();

  const channels = channelsOf(channelsQ.data).filter((c) => c.kind === 'channel' && !c.archived_at);
  const members = membersQ.data ?? [];
  const byId = memberMap(members);

  // 逐频道查任务,共用 qk.tasks(c.id) → WS patch 直达此缓存,拖列后自动实时移列。
  const taskQueries = useQueries({
    queries: channels.map((c) => ({
      queryKey: qk.tasks(c.id),
      queryFn: () => api.tasks(c.id),
    })),
  });
  const channelNameById = useMemo(
    () => Object.fromEntries(channels.map((c) => [c.id, c.name ?? '—'])),
    [channels],
  );
  const allTasks: TaskPublic[] = taskQueries.flatMap((q) => q.data ?? []);

  const [view, setView] = useState<'board' | 'list'>('board');
  const [fChannel, setFChannel] = useState('');
  const [fCreator, setFCreator] = useState('');
  const [fAssignee, setFAssignee] = useState('');
  const [dragOver, setDragOver] = useState<TaskStatus | null>(null);

  const tasks = allTasks.filter((t) =>
    (!fChannel || t.channel_id === fChannel)
    && (!fCreator || t.created_by_member_id === fCreator)
    && (!fAssignee || t.owner_member_id === fAssignee));

  // 过滤下拉候选:去重的 creator / assignee 成员集合。
  const creators = uniqueMembers(allTasks.map((t) => t.created_by_member_id), byId);
  const assignees = uniqueMembers(allTasks.map((t) => t.owner_member_id), byId);

  const openTask = (t: TaskPublic) => {
    setActiveChannel(t.channel_id);
    void navigate({ to: '/', search: { tab: 'chat', task: t.id, thread: t.root_message_id } });
  };

  const moveTask = (t: TaskPublic, to: TaskStatus) => {
    const from = t.status ?? 'todo';
    if (from === to) return;
    if (!TASK_TRANSITIONS[from].includes(to)) {
      toast.push(`非法流转:${STATUS_WORD[from]} → ${STATUS_WORD[to]}`, { tone: 'error' });
      return;
    }
    api.setTaskStatus(t.id, to).catch((e: unknown) => {
      const msg = e instanceof ApiError ? e.message : '流转失败';
      toast.push(msg, { tone: 'error' });
    });
  };

  return (
    <main className="main wsboard">
      <div className="wsb-head">
        <h1>Tasks</h1>
        <span className="sp" />
        <div className="seg">
          <button className={view === 'board' ? 'active' : ''} onClick={() => setView('board')}>Board</button>
          <button className={view === 'list' ? 'active' : ''} onClick={() => setView('list')}>List</button>
        </div>
      </div>

      <div className="wsb-filter">
        <FilterSelect label="Channel" value={fChannel} onChange={setFChannel}
          options={channels.map((c) => ({ value: c.id, label: `#${c.name ?? '—'}` }))} />
        <FilterSelect label="Creator" value={fCreator} onChange={setFCreator}
          options={creators.map((m) => ({ value: m.id, label: m.name }))} />
        <FilterSelect label="Assignee" value={fAssignee} onChange={setFAssignee}
          options={assignees.map((m) => ({ value: m.id, label: m.name }))} />
        <span className="wsb-count">{tasks.length} 个任务</span>
      </div>

      {view === 'board' ? (
        <div className="wsb-board">
          {COLUMNS.map((col) => {
            const colTasks = tasks.filter((t) => (t.status ?? 'todo') === col);
            return (
              <div
                key={col}
                className={`wcol${dragOver === col ? ' dragover' : ''}`}
                onDragOver={(e) => { e.preventDefault(); setDragOver(col); }}
                onDragLeave={() => setDragOver((d) => (d === col ? null : d))}
                onDrop={(e) => {
                  e.preventDefault();
                  setDragOver(null);
                  const id = e.dataTransfer.getData('text/plain');
                  const t = allTasks.find((x) => x.id === id);
                  if (t) moveTask(t, col);
                }}
              >
                <div className="wcol-hd">
                  <i style={{ background: `var(${STATUS_VAR[col]})` }} />
                  <span>{STATUS_WORD[col]}</span>
                  <span className="n">{colTasks.length}</span>
                </div>
                <div className="wcol-body">
                  {colTasks.map((t) => {
                    const owner = t.owner_member_id ? byId[t.owner_member_id] : undefined;
                    return (
                      <div
                        key={t.id}
                        className="wcard"
                        draggable
                        onDragStart={(e) => e.dataTransfer.setData('text/plain', t.id)}
                        onClick={() => openTask(t)}
                      >
                        <div className="wcard-top">
                          <span className="chchip"><span className="hash">#</span>{channelNameById[t.channel_id] ?? '—'}</span>
                          <span className="no">#{t.number}</span>
                        </div>
                        <div className="wcard-ttl">{t.title}</div>
                        {owner && (
                          <div className="wcard-owner"><Avatar name={owner.name} size="nav" />{owner.name}</div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="wsb-tablewrap">
          <table className="wsb-tbl">
            <thead>
              <tr>
                <th style={{ width: 56 }}>#</th>
                <th>Title</th>
                <th>Channel</th>
                <th>Owner</th>
                <th>Status</th>
                <th>Last Activity</th>
              </tr>
            </thead>
            <tbody>
              {tasks.map((t) => {
                const status = t.status ?? 'todo';
                const owner = t.owner_member_id ? byId[t.owner_member_id] : undefined;
                const done = status === 'done' || status === 'closed';
                return (
                  <tr key={t.id} className={`row${done ? ' done' : ''}`} onClick={() => openTask(t)}>
                    <td className="no">#{t.number}</td>
                    <td className="title">{t.title}</td>
                    <td><span className="chchip"><span className="hash">#</span>{channelNameById[t.channel_id] ?? '—'}</span></td>
                    <td>{owner ? <span className="ownercell"><Avatar name={owner.name} size="nav" />{owner.name}</span> : <span className="mu">—</span>}</td>
                    <td>
                      <span className="stcell"><i style={{ background: `var(${STATUS_VAR[status]})` }} />{STATUS_WORD[status]}</span>
                    </td>
                    <td className="rt">{relTime(t.status_changed_at)}</td>
                  </tr>
                );
              })}
              {tasks.length === 0 && (
                <tr><td colSpan={6} className="wsb-empty">无匹配任务</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}

function uniqueMembers(ids: (string | null | undefined)[], byId: Record<string, MemberPublic>): MemberPublic[] {
  const seen = new Set<string>();
  const out: MemberPublic[] = [];
  for (const id of ids) {
    if (!id || seen.has(id)) continue;
    const m = byId[id];
    if (m) { seen.add(id); out.push(m); }
  }
  return out;
}

function FilterSelect({ label, value, onChange, options }: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <label className="wsb-sel">
      <span className="mu">{label}:</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">全部</option>
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </label>
  );
}
