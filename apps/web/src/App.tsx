// P1 会话屏（契约形状验证）：mock REST 拉取 + WS 事件无刷新更新（NFR1）。
// 类型只用 @coagentia/contracts-ts 生成物；DOM 结构与 class 名沿用归档设计稿 P1-channel-chat.html。
import { useEffect, useReducer, useRef, useState } from 'react';

import type {
  ChannelPublic,
  Envelope,
  MemberPublic,
  MessagePublic,
  PresenceEntry,
  TaskPublic,
  WorkspacePublic,
} from '@coagentia/contracts-ts';

import { api } from './api';
import { connectWs } from './ws';

// ---- UI 常量（设计稿映射，非契约数据）
const AVATARS: Record<string, { v: number; human?: boolean }> = {
  Memcyo: { v: 3, human: true },
  Pat: { v: 1 },
  Hank: { v: 7 },
  Rin: { v: 5 },
  Orchestrator: { v: 4 },
};
const STATUS_WORD: Record<string, string> = {
  todo: 'Todo', in_progress: 'In Progress', in_review: 'In Review',
  done: 'Done', closed: 'Closed',
};
const STATUS_VAR: Record<string, string> = {
  todo: '--st-todo', in_progress: '--st-progress', in_review: '--st-review',
  done: '--st-done', closed: '--st-closed',
};
const PRESENCE_VAR: Record<string, string> = {
  online: '--success', idle: '--success', busy: '--warning',
  error: '--danger', offline: '--border-strong',
};

interface State {
  ready: boolean;
  workspace: WorkspacePublic | null;
  members: MemberPublic[];
  presence: Record<string, PresenceEntry>;
  channels: ChannelPublic[];
  readPositions: Record<string, string>;
  messages: Record<string, MessagePublic[]>;
  tasks: Record<string, TaskPublic>;
  usageByTask: Record<string, number>;
}

const initial: State = {
  ready: false, workspace: null, members: [], presence: {}, channels: [],
  readPositions: {}, messages: {}, tasks: {}, usageByTask: {},
};

type Action = { kind: 'boot'; state: Partial<State> } | { kind: 'ws'; env: Envelope };

function reducer(state: State, action: Action): State {
  if (action.kind === 'boot') return { ...state, ...action.state, ready: true };
  const { env } = action;
  const data = env.data as never;
  switch (env.type) {
    case 'message.created': {
      const { message } = data as { message: MessagePublic };
      const list = state.messages[message.channel_id] ?? [];
      if (list.some((m) => m.id === message.id)) return state;
      return {
        ...state,
        messages: { ...state.messages, [message.channel_id]: [...list, message] },
      };
    }
    case 'task.created':
    case 'task.updated': {
      const { task } = data as { task: TaskPublic };
      return { ...state, tasks: { ...state.tasks, [task.id]: task } };
    }
    case 'presence.changed': {
      const p = data as { member_id: string; kind: 'human' | 'agent'; status: string };
      const prev = state.presence[p.member_id];
      return {
        ...state,
        presence: {
          ...state.presence,
          [p.member_id]: {
            member_id: p.member_id, kind: p.kind,
            status: p.status as PresenceEntry['status'],
            busy_detail: p.status === 'busy' ? (prev?.busy_detail ?? null) : null,
          },
        },
      };
    }
    case 'agent.activity': {
      const a = data as { member_id: string; detail: string };
      const prev = state.presence[a.member_id];
      if (!prev) return state;
      return {
        ...state,
        presence: { ...state.presence, [a.member_id]: { ...prev, busy_detail: a.detail } },
      };
    }
    case 'read.updated': {
      const r = data as { channel_id: string; member_id: string; last_read_message_id: string };
      const me = state.members.find((m) => m.kind === 'human' && m.role === 'owner');
      if (!me || r.member_id !== me.id) return state;
      return {
        ...state,
        readPositions: { ...state.readPositions, [r.channel_id]: r.last_read_message_id },
      };
    }
    case 'token_usage.reported': {
      const u = data as {
        task_id?: string | null;
        totals: { input_tokens: number; output_tokens: number };
      };
      if (!u.task_id) return state;
      const sum = (state.usageByTask[u.task_id] ?? 0) + u.totals.input_tokens + u.totals.output_tokens;
      return { ...state, usageByTask: { ...state.usageByTask, [u.task_id]: sum } };
    }
    default:
      return state;
  }
}

// ---- 纯文本渲染（FR-4.3：@与 task #n 是文本不是外键）
function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function renderBody(body: string, memberNames: string[], meName: string): string {
  const parts = body.split(/```(\w*)\n([\s\S]*?)```/g);
  let html = '';
  for (let i = 0; i < parts.length; i += 3) {
    let text = escapeHtml(parts[i] ?? '').trim();
    text = text.replace(/`([^`]+)`/g, '<span class="icode">$1</span>');
    for (const name of memberNames) {
      const cls = name === meName ? 'mention self' : 'mention';
      text = text.replaceAll(`@${name}`, `<span class="${cls}">@${name}</span>`);
    }
    text = text.replace(/task #(\d+)/g, 'task <span class="tasklink">#$1</span>');
    html += text.replace(/\n/g, '<br/>');
    if (i + 2 < parts.length) {
      const lang = escapeHtml(parts[i + 1] ?? '');
      const code = escapeHtml(parts[i + 2] ?? '');
      html += `<div class="codeblock"><span class="lang">${lang}</span><pre>${code}</pre></div>`;
    }
  }
  return html;
}

function Avatar({ name, presence, size }: {
  name: string; presence?: PresenceEntry; size: 'msg' | 'nav';
}) {
  const cfg = AVATARS[name] ?? { v: ((name.charCodeAt(0) % 8) + 1) };
  const dot = presence ? `var(${PRESENCE_VAR[presence.status] ?? '--border-strong'})` : null;
  const cls = size === 'msg' ? `av${cfg.human ? ' human' : ''}` : 'av16';
  return (
    <span className={cls} style={{ background: `var(--avatar-${cfg.v})` }}>
      {name[0]}
      {dot && (
        <span className={`p${presence?.status === 'busy' ? ' pulse' : ''}`}
              style={{ background: dot }} />
      )}
    </span>
  );
}

export function App() {
  const [state, dispatch] = useReducer(reducer, initial);
  const [draft, setDraft] = useState('');
  const [asTask, setAsTask] = useState(false);
  const flowRef = useRef<HTMLElement>(null);

  useEffect(() => {
    let cleanup = () => {};
    (async () => {
      const [workspace, snapshot, members, presence] = await Promise.all([
        api.workspace(), api.channels(), api.members(), api.presence(),
      ]);
      const channels = snapshot.items as ChannelPublic[];
      const messages: Record<string, MessagePublic[]> = {};
      await Promise.all(channels.map(async (ch) => {
        messages[ch.id] = (await api.messages(ch.id)).items as MessagePublic[];
      }));
      const build = channels.find((c) => c.name === 'build')!;
      const tasks = Object.fromEntries((await api.tasks(build.id)).map((t) => [t.id, t]));
      dispatch({
        kind: 'boot',
        state: {
          workspace, members, channels, messages, tasks,
          presence: Object.fromEntries(presence.items.map((p) => [p.member_id, p])),
          readPositions: Object.fromEntries(
            snapshot.read_positions.map((r) => [r.channel_id, r.last_read_message_id]),
          ),
        },
      });
      cleanup = connectWs((env) => dispatch({ kind: 'ws', env }));
    })();
    return () => cleanup();
  }, []);

  if (!state.ready) return <div className="boot">connecting…</div>;

  const me = state.members.find((m) => m.kind === 'human' && m.role === 'owner')!;
  const build = state.channels.find((c) => c.name === 'build')!;
  const buildMsgs = state.messages[build.id] ?? [];
  const memberById = Object.fromEntries(state.members.map((m) => [m.id, m]));
  const memberNames = state.members.map((m) => m.name);
  const taskByRoot = Object.fromEntries(
    Object.values(state.tasks).map((t) => [t.root_message_id, t]),
  );
  const boardCount = Object.values(state.tasks)
    .filter((t) => !['done', 'closed'].includes(t.status)).length;

  const unreadCount = (ch: ChannelPublic): number => {
    const msgs = state.messages[ch.id] ?? [];
    const last = state.readPositions[ch.id];
    if (!last) return msgs.length;
    const idx = msgs.findIndex((m) => m.id === last);
    return idx < 0 ? 0 : msgs.length - idx - 1;
  };

  const dmPeer = (ch: ChannelPublic): MemberPublic | undefined => {
    const ids = ch.dm_key?.split(':') ?? [];
    return memberById[ids.find((id) => id !== me.id) ?? ''];
  };

  const send = async () => {
    const body = draft.trim();
    if (!body) return;
    setDraft('');
    setAsTask(false);
    await api.sendMessage(build.id, body, asTask); // 回显靠 WS 广播（契约 C §5）
  };

  const lastRead = state.readPositions[build.id];
  const lastReadIdx = buildMsgs.findIndex((m) => m.id === lastRead);
  const logoBits = [0, 1, 1, 0, 1, 0, 0, 1, 1, 1, 1, 1, 1, 0, 0, 1];

  return (
    <div className="app">
      <nav className="rail">
        <div className="logo" aria-label="CoAgentia">
          {logoBits.map((b, i) => <i key={i} className={b ? 'on' : ''} />)}
        </div>
        <div className="sp" />
        <div className="me">
          {me.name[0]}
          <span className="p" style={{ background: 'var(--success)' }} />
        </div>
      </nav>

      <aside className="chlist">
        <div className="grp">Channels</div>
        {state.channels.filter((c) => c.kind === 'channel').map((ch) => {
          const n = unreadCount(ch);
          return (
            <div key={ch.id}
                 className={`ch${ch.id === build.id ? ' active' : ''}${n ? ' unread' : ''}`}>
              <span className="hash">{ch.is_private ? '🔒' : '#'}</span>
              <span className="nm">{ch.name}</span>
              {n > 0 && ch.id !== build.id && <span className="cnt">{n}</span>}
            </div>
          );
        })}
        <div className="grp">Direct Messages</div>
        {state.channels.filter((c) => c.kind === 'dm').map((ch) => {
          const peer = dmPeer(ch);
          if (!peer) return null;
          return (
            <div key={ch.id} className="ch">
              <Avatar name={peer.name} presence={state.presence[peer.id]} size="nav" />
              <span className="nm">{peer.name}</span>
            </div>
          );
        })}
        <div className="sp" />
        <button className="playbtn" onClick={() => api.playTimeline()}>▶ 播放时间线</button>
      </aside>

      <main className="main">
        <header className="topbar">
          <span className="cname"><span className="hash">#</span>{build.name}</span>
          <span className="cdesc">{build.description}</span>
          <div className="stack">
            {['Memcyo', 'Pat', 'Hank', 'Rin'].map((n) => {
              const cfg = AVATARS[n];
              return (
                <span key={n} className="sav"
                      style={{
                        background: `var(--avatar-${cfg.v})`,
                        borderRadius: cfg.human ? 'var(--radius-round)' : 'var(--radius-s)',
                      }}>{n[0]}</span>
              );
            })}
            <span className="n">4</span>
          </div>
        </header>

        <nav className="tabs">
          <div className="tab active">会话</div>
          <div className="tab">画布<span className="cnt">3</span></div>
          <div className="tab">看板<span className="cnt">{boardCount}</span></div>
          <div className="tab">文件</div>
        </nav>

        <section className="flow" ref={flowRef}>
          {buildMsgs.map((m, i) => {
            const prev = buildMsgs[i - 1];
            const date = m.created_at.slice(5, 10);
            const newDay = !prev || prev.created_at.slice(5, 10) !== date;
            const author = m.author_member_id ? memberById[m.author_member_id] : null;
            const task = taskByRoot[m.id];
            const cont = !newDay && prev && prev.author_member_id === m.author_member_id
              && m.kind === 'user' && prev.kind === 'user';
            const pres = author ? state.presence[author.id] : undefined;
            const usage = task ? state.usageByTask[task.id] : undefined;
            return (
              <div key={m.id}>
                {newDay && <div className="datesep"><span>{date}</span></div>}
                {i === lastReadIdx + 1 && lastReadIdx >= 0 && (
                  <div className="unreadline"><span className="ln" /><span>新消息</span></div>
                )}
                {m.kind === 'system' ? (
                  <div className="sysmsg">
                    <span className="sys">系统</span>
                    <span dangerouslySetInnerHTML={{
                      __html: renderBody(m.body, memberNames, me.name),
                    }} />
                  </div>
                ) : (
                  <div className={`msg${cont ? ' cont' : ''}`}>
                    <div className="avc">
                      {cont
                        ? <span className="htime">{m.created_at.slice(11, 16)}</span>
                        : author && <Avatar name={author.name} presence={pres} size="msg" />}
                    </div>
                    <div>
                      {!cont && author && (
                        <div className="hd">
                          <span className="nm">{author.name}</span>
                          {pres && (
                            <span className={`pp${pres.status === 'busy' ? ' pulse' : ''}`}
                                  style={{
                                    background: `var(${PRESENCE_VAR[pres.status]})`,
                                  }} />
                          )}
                          <span className="ts">{m.created_at.slice(11, 16)}</span>
                          {pres?.busy_detail && <span className="tail">{pres.busy_detail}</span>}
                        </div>
                      )}
                      <div className="body" dangerouslySetInnerHTML={{
                        __html: renderBody(m.body, memberNames, me.name),
                      }} />
                      {task && (
                        <div className="taskchip">
                          <span className="bar"
                                style={{ background: `var(${STATUS_VAR[task.status]})` }} />
                          <span className="no">#{task.number}</span>
                          <span className="stw">{STATUS_WORD[task.status]}</span>
                          {task.owner_member_id && memberById[task.owner_member_id] && (
                            <span className="who">
                              <Avatar name={memberById[task.owner_member_id].name} size="nav" />
                              {memberById[task.owner_member_id].name}
                            </span>
                          )}
                          <span className="ttl">{task.title}</span>
                          {usage !== undefined && (
                            <span className="tokbadge">{(usage / 1000).toFixed(1)}k tok</span>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </section>

        <footer className="composer">
          <div className="combox">
            <span className="prompt">❯</span>
            <input className="line" value={draft} placeholder="发消息到 #build"
                   onChange={(e) => setDraft(e.target.value)}
                   onKeyDown={(e) => e.key === 'Enter' && send()} />
            <label className="astask">
              <input type="checkbox" checked={asTask}
                     onChange={(e) => setAsTask(e.target.checked)} />
              As Task
            </label>
            <button className="btn btn-primary" onClick={send}>发送</button>
          </div>
        </footer>
      </main>
    </div>
  );
}
