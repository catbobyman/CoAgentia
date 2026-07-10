// P10 全局搜索覆盖层(Ctrl+K)。对照设计稿 P10-search.html:640px 弹层、❯ 终端输入、
// 跳转/消息/任务三分组、命中 «» 高亮、键盘选中态。开合由 store.searchOpen 驱动(RootLayout 挂载)。
//
// 前缀解析(MVP):from:名字 → from_member(按成员名子串解析成 id)、in:频道 → in_channel(按频道名)、
// task: → kind=task。解析后的残余文本作为 q 传后端;无法解析的 from:/in: token 保留进 q(仍出结果)。
import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { useQuery } from '@tanstack/react-query';

import type { ChannelPublic, MemberPublic, MessagePublic, TaskPublic } from '@coagentia/contracts-ts';

import { api, type SearchKind } from '../api';
import { qk } from '../lib/queryKeys';
import { useUiStore } from '../lib/store';
import { channelsOf, memberMap, useChannelsSnapshot, useMembers } from '../data/queries';
import { Avatar } from './Avatar';
import { TaskChip } from './TaskChip';
import './search-overlay.css';

interface ParsedQuery {
  q: string;
  kind?: SearchKind;
  from_member?: string;
  in_channel?: string;
}

// 名字子串解析成 id(NOCASE,取第一个命中)。
function resolveByName<T extends { id: string; name?: string | null }>(
  items: T[],
  name: string,
): string | undefined {
  const n = name.toLowerCase();
  if (!n) return undefined; // 空片段('in: '/'from: ')不得命中首个条目(''.includes('')恒真)
  return items.find((i) => (i.name ?? '').toLowerCase().includes(n))?.id;
}

function parseQuery(raw: string, members: MemberPublic[], channels: ChannelPublic[]): ParsedQuery {
  let kind: SearchKind | undefined;
  let from_member: string | undefined;
  let in_channel: string | undefined;
  const rest: string[] = [];
  for (const token of raw.split(/\s+/).filter(Boolean)) {
    const lower = token.toLowerCase();
    if (lower.startsWith('from:')) {
      const id = resolveByName(members, token.slice(5));
      if (id) from_member = id;
      else rest.push(token); // 未解析 → 保留进 q
    } else if (lower.startsWith('in:')) {
      const id = resolveByName(channels, token.slice(3));
      if (id) in_channel = id;
      else rest.push(token);
    } else if (lower.startsWith('task:')) {
      kind = 'task';
      const tail = token.slice(5);
      if (tail) rest.push(tail);
    } else {
      rest.push(token);
    }
  }
  return { q: rest.join(' ').trim(), kind, from_member, in_channel };
}

// snippet 的 «命中» 段渲染成 <mark>。
function renderSnippet(snippet: string): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  const re = /«([^»]*)»/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let key = 0;
  while ((m = re.exec(snippet)) !== null) {
    if (m.index > last) out.push(snippet.slice(last, m.index));
    out.push(
      <mark className="hit" key={key++}>
        {m[1]}
      </mark>,
    );
    last = m.index + m[0].length;
  }
  if (last < snippet.length) out.push(snippet.slice(last));
  return out;
}

type FlatItem =
  | { kind: 'channel'; channel: ChannelPublic }
  | { kind: 'member'; member: MemberPublic }
  | { kind: 'message'; result: { message: MessagePublic; snippet: string } }
  | { kind: 'task'; task: TaskPublic };

export function SearchOverlay() {
  const open = useUiStore((s) => s.searchOpen);
  const setSearchOpen = useUiStore((s) => s.setSearchOpen);
  const setActiveChannel = useUiStore((s) => s.setActiveChannel);
  const navigate = useNavigate();

  const membersQ = useMembers();
  const channelsQ = useChannelsSnapshot();
  const members = useMemo(() => membersQ.data ?? [], [membersQ.data]);
  const channels = useMemo(() => channelsOf(channelsQ.data), [channelsQ.data]);
  const byId = memberMap(members);

  const [raw, setRaw] = useState('');
  const [debounced, setDebounced] = useState('');
  const [sel, setSel] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  // 开时聚焦 + 复位;关时清空查询态。
  useEffect(() => {
    if (open) {
      setRaw('');
      setDebounced('');
      setSel(0);
      const t = window.setTimeout(() => inputRef.current?.focus(), 0);
      return () => window.clearTimeout(t);
    }
    return undefined;
  }, [open]);

  // 防抖 ~150ms。
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(raw), 150);
    return () => window.clearTimeout(t);
  }, [raw]);

  const parsed = useMemo(
    () => parseQuery(debounced, members, channels),
    [debounced, members, channels],
  );

  const searchQ = useQuery({
    queryKey: qk.search(JSON.stringify(parsed)),
    queryFn: () => api.search(parsed),
    enabled: open && parsed.q.length > 0,
    staleTime: 10_000,
  });

  // 三分组扁平化(键盘导航需线性索引)。
  const flat = useMemo<FlatItem[]>(() => {
    const data = searchQ.data;
    if (!data) return [];
    const items: FlatItem[] = [];
    for (const c of data.jumps.channels ?? []) items.push({ kind: 'channel', channel: c });
    for (const m of data.jumps.members ?? []) items.push({ kind: 'member', member: m });
    for (const r of data.messages ?? []) items.push({ kind: 'message', result: r });
    for (const t of data.tasks ?? []) items.push({ kind: 'task', task: t });
    return items;
  }, [searchQ.data]);

  useEffect(() => {
    setSel(0);
  }, [flat]);

  if (!open) return null;

  const close = () => setSearchOpen(false);

  const activate = (item: FlatItem) => {
    switch (item.kind) {
      case 'channel':
        setActiveChannel(item.channel.id);
        void navigate({ to: '/', search: { tab: 'chat' } });
        break;
      case 'member':
        // 仅 Agent 有详情路由(/agents/$id)；人类成员无 profile 屏 → 去成员表(P8),
        // 否则跳 agent 路由对人类 404 并卡在"loading agent…"。
        if (item.member.kind === 'agent') {
          void navigate({
            to: '/agents/$memberId',
            params: { memberId: item.member.id },
            search: { tab: 'profile' },
          });
        } else {
          void navigate({ to: '/members' });
        }
        break;
      case 'message': {
        const msg = item.result.message;
        setActiveChannel(msg.channel_id);
        const thread = msg.thread_root_id ?? msg.id;
        void navigate({ to: '/', search: { tab: 'chat', thread } });
        break;
      }
      case 'task':
        setActiveChannel(item.task.channel_id);
        void navigate({
          to: '/',
          search: { tab: 'chat', task: item.task.id, thread: item.task.root_message_id },
        });
        break;
    }
    close();
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      close();
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSel((i) => (flat.length ? (i + 1) % flat.length : 0));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSel((i) => (flat.length ? (i - 1 + flat.length) % flat.length : 0));
    } else if (e.key === 'Enter') {
      // 输入法组合态的 Enter 是"确认候选词"——不得触发跳转并关闭弹层(与 Composer 同防护)。
      if ((e.nativeEvent as KeyboardEvent).isComposing || e.keyCode === 229) return;
      e.preventDefault();
      const item = flat[sel];
      if (item) activate(item);
    }
  };

  // 每个分组渲染时用 flat 里的全局索引匹配键盘选中态。
  let idx = -1;
  const nextIdx = () => (idx += 1);

  const data = searchQ.data;
  const channelJumps = data?.jumps.channels ?? [];
  const memberJumps = data?.jumps.members ?? [];
  const messages = data?.messages ?? [];
  const tasks = data?.tasks ?? [];

  return (
    <>
      <button className="search-scrim" aria-label="关闭搜索" onClick={close} />
      <div className="search-overlay" role="dialog" aria-label="全局搜索">
        <div className="sbar">
          <span className="pr">❯</span>
          <input
            ref={inputRef}
            className="q-input"
            value={raw}
            placeholder="搜索消息、任务、频道、成员…  (from: / in: / task:)"
            onChange={(e) => setRaw(e.target.value)}
            onKeyDown={onKeyDown}
            spellCheck={false}
            autoComplete="off"
          />
          <span className="esc">Esc</span>
        </div>

        <div className="results">
          {channelJumps.length + memberJumps.length > 0 && <div className="rgrp">跳转</div>}
          {channelJumps.map((c) => {
            const i = nextIdx();
            return (
              <button
                key={c.id}
                className={`ritem${i === sel ? ' kbd-sel' : ''}`}
                onMouseEnter={() => setSel(i)}
                onClick={() => activate({ kind: 'channel', channel: c })}
              >
                <span className="ic">
                  <span className="hash">#</span>
                </span>
                <span className="tt">{c.name}</span>
              </button>
            );
          })}
          {memberJumps.map((m) => {
            const i = nextIdx();
            return (
              <button
                key={m.id}
                className={`ritem${i === sel ? ' kbd-sel' : ''}`}
                onMouseEnter={() => setSel(i)}
                onClick={() => activate({ kind: 'member', member: m })}
              >
                <span className="ic">
                  <Avatar name={m.name} size="nav" />
                </span>
                <span className="tt">{m.name}</span>
              </button>
            );
          })}

          {messages.length > 0 && <div className="rgrp">消息</div>}
          {messages.map((r) => {
            const i = nextIdx();
            const author = r.message.author_member_id
              ? byId[r.message.author_member_id]
              : undefined;
            return (
              <button
                key={r.message.id}
                className={`ritem${i === sel ? ' kbd-sel' : ''}`}
                onMouseEnter={() => setSel(i)}
                onClick={() => activate({ kind: 'message', result: r })}
              >
                <span className="ic msg-ic">≡</span>
                <span className="tt">
                  {author && <span className="sub">{author.name}: </span>}
                  {renderSnippet(r.snippet)}
                </span>
              </button>
            );
          })}

          {tasks.length > 0 && <div className="rgrp">任务</div>}
          {tasks.map((t) => {
            const i = nextIdx();
            const owner = t.owner_member_id ? byId[t.owner_member_id] : undefined;
            return (
              <button
                key={t.id}
                className={`ritem ritem--task${i === sel ? ' kbd-sel' : ''}`}
                onMouseEnter={() => setSel(i)}
                onClick={() => activate({ kind: 'task', task: t })}
              >
                <TaskChip task={t} owner={owner} />
              </button>
            );
          })}

          {parsed.q.length > 0 && !searchQ.isLoading && flat.length === 0 && (
            <div className="search-empty">无匹配结果</div>
          )}
          {parsed.q.length === 0 && (
            <div className="search-empty">输入以搜索。前缀:from: / in: / task:</div>
          )}
        </div>

        <div className="foot">
          <span className="grpx">
            <span className="kbd">from:</span>@名字
          </span>
          <span className="grpx">
            <span className="kbd">in:</span>#频道
          </span>
          <span className="grpx">
            <span className="kbd">task:</span>
          </span>
          <span className="sp" />
          <span className="grpx">
            <span className="kbd">↑↓</span>选择
          </span>
          <span className="grpx">
            <span className="kbd">↵</span>跳转
          </span>
        </div>
      </div>
    </>
  );
}
