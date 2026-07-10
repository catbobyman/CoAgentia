// P1 会话屏(主列),在新路由/基座下渲染,行为不回退(静载 + WS 五事件无刷新更新,NFR1)。
// 视图状态来自类型化深链 search(tab/task/thread);服务端数据来自 TanStack Query 缓存。
// P5 线程面板由 ?thread= 驱动,在主列右侧展开(不新增顶层路由)。
import { useState } from 'react';
import { useNavigate } from '@tanstack/react-router';

import type { FilePublic, TaskStatus } from '@coagentia/contracts-ts';
import { UNCLAIMABLE_STATUSES } from '@coagentia/contracts-ts';

import type { ChannelSearch, Tab } from '../routes/search';
import {
  memberMap, presenceMap, readPositionsMap,
  useChannelFiles, useChannelsSnapshot, useMembers, useMessages, usePresence, useTasks, useUsageByTask,
} from '../data/queries';
import { useUiStore } from '../lib/store';
import { Composer } from '../components/Composer';
import { MessageFlow } from '../components/MessageFlow';
import { BoardTab } from '../components/BoardTab';
import { FilesTab } from '../components/FilesTab';
import { Tabs } from '../components/Tabs';
import { Topbar } from '../components/Topbar';
import { ThreadPanel } from './ThreadPanel';
import { api } from '../api';

export function ChannelChatScreen({ search, setSearch }: {
  search: ChannelSearch;
  setSearch: (next: Partial<ChannelSearch>) => void;
}) {
  const activeChannelId = useUiStore((s) => s.activeChannelId);
  const navigate = useNavigate();
  // 「定位到消息」目标(P4 → 会话流):瞬态视图状态,不进深链。
  const [locateId, setLocateId] = useState<string | undefined>();

  const channelsQ = useChannelsSnapshot();
  const membersQ = useMembers();
  const presenceQ = usePresence();
  const messagesQ = useMessages(activeChannelId ?? undefined);
  const tasksQ = useTasks(activeChannelId ?? undefined);
  const usageQ = useUsageByTask();
  const filesQ = useChannelFiles(activeChannelId ?? undefined);

  const snap = channelsQ.data;
  const channel = snap?.items?.find((c) => c.id === activeChannelId);
  if (!channel || !membersQ.data) {
    return <main className="main"><div className="boot">loading…</div></main>;
  }

  const members = membersQ.data;
  const me = members.find((m) => m.kind === 'human' && m.role === 'owner');
  const byId = memberMap(members);
  const memberNames = members.map((m) => m.name);
  const presence = presenceMap(presenceQ.data);
  const messages = messagesQ.data ?? [];
  const tasks = tasksQ.data ?? [];
  const usageByTask = usageQ.data ?? {};

  const files = filesQ.data ?? [];
  // 附件按 message_id 聚合(消息流附件卡数据源)。
  const filesByMessage = files.reduce<Record<string, FilePublic[]>>((acc, f) => {
    if (f.message_id) (acc[f.message_id] ??= []).push(f);
    return acc;
  }, {});

  const taskByRoot = Object.fromEntries(tasks.map((t) => [t.root_message_id, t]));
  // 「完结态」消费生成常量(纪律 7 单一事实源;当前值域 = {done, closed})。
  const boardCount = tasks.filter(
    (t) => !UNCLAIMABLE_STATUSES.includes((t.status ?? 'todo') as TaskStatus),
  ).length;
  const filesCount = files.length;
  const readPos = readPositionsMap(snap)[channel.id];
  const lastReadId = readPos?.last_read_message_id;

  const stackNames = members
    .filter((m) => m.kind === 'agent' || m.role === 'owner')
    .map((m) => m.name)
    .slice(0, 4);

  const send = (body: string, asTask: boolean) => {
    void api.sendMessage(channel.id, body, asTask); // 回显靠 WS 广播(契约 C §5)
  };
  const selectTab = (tab: Tab) => setSearch({ tab });
  // 点任务牌 → 打开线程面板(?thread=root),同时高亮(?task=)。再点同一牌关闭。
  const selectTask = (taskId: string) => {
    if (search.task === taskId) {
      setSearch({ task: undefined, thread: undefined });
      return;
    }
    const t = tasks.find((x) => x.id === taskId);
    setSearch({ task: taskId, thread: t?.root_message_id });
  };
  const openAgent = (memberId: string) =>
    void navigate({ to: '/agents/$memberId', params: { memberId }, search: { tab: 'profile' } });

  // 线程面板数据:?thread= 命中的 root 消息对应任务。
  const threadRootId = search.thread;
  const threadTask = threadRootId ? taskByRoot[threadRootId] : undefined;
  const threadUsage = threadTask ? usageByTask[threadTask.id] : undefined;

  return (
    <div className="chatwrap">
      <main className="main">
        <Topbar channel={channel} stackNames={stackNames} />
        <Tabs
          active={search.tab}
          canvasCount={3}
          boardCount={boardCount}
          filesCount={filesCount}
          onSelect={selectTab}
        />

        {search.tab === 'chat' ? (
          <MessageFlow
            messages={messages}
            memberById={byId}
            memberNames={memberNames}
            meName={me?.name ?? ''}
            presenceOf={(id) => presence[id]}
            taskByRoot={taskByRoot}
            usageByTask={usageByTask}
            filesByMessage={filesByMessage}
            lastReadId={lastReadId}
            selectedTaskId={search.task}
            locateId={locateId}
            onLocateDone={() => setLocateId(undefined)}
            onSelectTask={selectTask}
            onOpenAgent={openAgent}
          />
        ) : search.tab === 'board' ? (
          <BoardTab
            tasks={tasks}
            memberById={byId}
            presenceOf={(id) => presence[id]}
            selectedTaskId={search.task}
            onSelectTask={selectTask}
          />
        ) : search.tab === 'files' ? (
          <FilesTab
            channelId={channel.id}
            onLocate={(messageId) => {
              // 附件绑定在线程回复上时,消息不在主流——切页签之外还要展开所在线程。
              const target = messages.find((m) => m.id === messageId);
              setSearch({ tab: 'chat', thread: target?.thread_root_id ?? undefined });
              setLocateId(messageId);
            }}
          />
        ) : (
          <section className="flow">
            <div className="boot">「{search.tab}」屏 B2 搭建中 —— 路由/深链已就绪。</div>
          </section>
        )}

        <Composer channelName={channel.name ?? ''} onSend={send} />
      </main>

      {threadRootId && (
        <ThreadPanel
          key={threadRootId}
          task={threadTask}
          rootMessageId={threadRootId}
          memberById={byId}
          memberNames={memberNames}
          meName={me?.name ?? ''}
          meId={me?.id}
          presenceOf={(id) => presence[id]}
          usage={threadUsage}
          filesByMessage={filesByMessage}
          locateId={locateId}
          onLocateDone={() => setLocateId(undefined)}
          onClose={() => setSearch({ thread: undefined, task: undefined })}
          onSend={(body) => void api.sendMessage(channel.id, body, false)}
        />
      )}
    </div>
  );
}
