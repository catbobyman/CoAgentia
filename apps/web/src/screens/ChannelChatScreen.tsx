// P1 会话屏(主列),在新路由/基座下渲染,行为不回退(静载 + WS 五事件无刷新更新,NFR1)。
// 视图状态来自类型化深链 search(tab/task);服务端数据来自 TanStack Query 缓存。
import type { ChannelSearch, Tab } from '../routes/search';
import {
  memberMap, presenceMap, readPositionsMap,
  useChannelsSnapshot, useMembers, useMessages, usePresence, useTasks, useUsageByTask,
} from '../data/queries';
import { useUiStore } from '../lib/store';
import { Composer } from '../components/Composer';
import { MessageFlow } from '../components/MessageFlow';
import { Tabs } from '../components/Tabs';
import { Topbar } from '../components/Topbar';
import { api } from '../api';

export function ChannelChatScreen({ search, setSearch }: {
  search: ChannelSearch;
  setSearch: (next: Partial<ChannelSearch>) => void;
}) {
  const activeChannelId = useUiStore((s) => s.activeChannelId);

  const channelsQ = useChannelsSnapshot();
  const membersQ = useMembers();
  const presenceQ = usePresence();
  const messagesQ = useMessages(activeChannelId ?? undefined);
  const tasksQ = useTasks(activeChannelId ?? undefined);
  const usageQ = useUsageByTask();

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

  const taskByRoot = Object.fromEntries(tasks.map((t) => [t.root_message_id, t]));
  const boardCount = tasks.filter((t) => !['done', 'closed'].includes(t.status ?? 'todo')).length;
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
  const selectTask = (taskId: string) =>
    setSearch({ task: search.task === taskId ? undefined : taskId });

  return (
    <main className="main">
      <Topbar channel={channel} stackNames={stackNames} />
      <Tabs active={search.tab} canvasCount={3} boardCount={boardCount} onSelect={selectTab} />

      {search.tab === 'chat' ? (
        <MessageFlow
          messages={messages}
          memberById={byId}
          memberNames={memberNames}
          meName={me?.name ?? ''}
          presenceOf={(id) => presence[id]}
          taskByRoot={taskByRoot}
          usageByTask={usageByTask}
          lastReadId={lastReadId}
          selectedTaskId={search.task}
          onSelectTask={selectTask}
        />
      ) : (
        <section className="flow">
          <div className="boot">「{search.tab}」屏 B2 搭建中 —— 路由/深链已就绪。</div>
        </section>
      )}

      <Composer channelName={channel.name ?? ''} onSend={send} />
    </main>
  );
}
