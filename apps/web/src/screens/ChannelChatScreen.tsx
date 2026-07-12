// P1 会话屏(主列),在新路由/基座下渲染,行为不回退(静载 + WS 五事件无刷新更新,NFR1)。
// 视图状态来自类型化深链 search(tab/task/thread);服务端数据来自 TanStack Query 缓存。
// P5 线程面板由 ?thread= 驱动,在主列右侧展开(不新增顶层路由)。
import { useState } from 'react';
import { useNavigate } from '@tanstack/react-router';

import type { TaskStatus } from '@coagentia/contracts-ts';
import { UNCLAIMABLE_STATUSES } from '@coagentia/contracts-ts';

import type { ChannelSearch, Tab } from '../routes/search';
import {
  memberMap, presenceMap, readPositionsMap,
  useCanvasSnapshot, useChannelFiles, useChannelsSnapshot, useHeldDrafts, useMembers, useMessages,
  usePresence, useTasks, useUsageByTask,
} from '../data/queries';
import { useUiStore } from '../lib/store';
import { Composer } from '../components/Composer';
import { MessageFlow } from '../components/MessageFlow';
import { BoardTab } from '../components/BoardTab';
import { CanvasTab } from '../components/CanvasTab';
import { FilesTab } from '../components/FilesTab';
import { Tabs } from '../components/Tabs';
import { Topbar } from '../components/Topbar';
import { ThreadPanel } from './ThreadPanel';
import { HeldDraftList } from './HeldDraftCard';
import { ChannelSettingsModal } from '../components/ChannelSettingsModal';
import { notifyModeOf } from '../lib/notify';
import { api } from '../api';

export function ChannelChatScreen({ search, setSearch }: {
  search: ChannelSearch;
  setSearch: (next: Partial<ChannelSearch>) => void;
}) {
  const activeChannelId = useUiStore((s) => s.activeChannelId);
  const setActiveDraft = useUiStore((s) => s.setActiveDraft);
  const setActiveDelta = useUiStore((s) => s.setActiveDelta);
  const navigate = useNavigate();
  // 「定位到消息」目标(P4 → 会话流):瞬态视图状态,不进深链。
  const [locateId, setLocateId] = useState<string | undefined>();
  // 频道设置弹窗(B-M5-1):⋯ 菜单入口,瞬态视图状态。
  const [settingsOpen, setSettingsOpen] = useState(false);

  const channelsQ = useChannelsSnapshot();
  const membersQ = useMembers();
  const presenceQ = usePresence();
  const messagesQ = useMessages(activeChannelId ?? undefined);
  const tasksQ = useTasks(activeChannelId ?? undefined);
  const usageQ = useUsageByTask();
  const filesQ = useChannelFiles(activeChannelId ?? undefined);
  const canvasQ = useCanvasSnapshot(activeChannelId ?? undefined);
  const heldDraftsQ = useHeldDrafts(activeChannelId ?? undefined);

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

  // 附件卡数据源已改消息读面派生 files(契约 A v1.0.4);channelFiles 只服务文件页签计数与列表。
  const files = filesQ.data ?? [];
  // 被扣草稿(M4b):主流渲染 thread_root_id 为空者;线程内渲染匹配线程根者(HeldDraftList 内按此归位)。
  const heldDrafts = heldDraftsQ.data ?? [];
  const canResolve = me?.kind === 'human'; // 三键仅人类可见(web = 人类 owner 视图)

  const taskByRoot = Object.fromEntries(tasks.map((t) => [t.root_message_id, t]));
  // 「完结态」消费生成常量(纪律 7 单一事实源;当前值域 = {done, closed})。
  const boardCount = tasks.filter(
    (t) => !UNCLAIMABLE_STATUSES.includes((t.status ?? 'todo') as TaskStatus),
  ).length;
  const filesCount = files.length;
  const canvasCount = canvasQ.data?.nodes?.length ?? 0;
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
  // M6b 提案卡入口：full「查看草稿」→ 激活草稿层；delta「审查增量」→ 激活 delta 面板；均切画布页签。
  // 二者互斥（并行审计修复）：切页签不卸 store 态,不清对方会双层叠加遮挡（频道内单提案审阅）。
  const reviewDraft = (proposalId: string) => {
    setActiveDelta(channel.id, null);
    setActiveDraft(channel.id, proposalId);
    setSearch({ tab: 'canvas' });
  };
  const reviewDelta = (proposalId: string) => {
    setActiveDraft(channel.id, null);
    setActiveDelta(channel.id, proposalId);
    setSearch({ tab: 'canvas' });
  };
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
  // 跳转定位到某条消息(未读清单点选 / 文件页签共用):切到 chat + 展开所在线程 + 闪烁高亮。
  const locateMessage = (messageId: string) => {
    const target = messages.find((m) => m.id === messageId);
    setSearch({ tab: 'chat', thread: target?.thread_root_id ?? undefined });
    setLocateId(messageId);
  };

  // 线程面板数据:?thread= 命中的 root 消息对应任务。
  const threadRootId = search.thread;
  const threadTask = threadRootId ? taskByRoot[threadRootId] : undefined;
  const threadUsage = threadTask ? usageByTask[threadTask.id] : undefined;

  return (
    <div className="chatwrap">
      <main className="main">
        <Topbar channel={channel} stackNames={stackNames} onOpenSettings={() => setSettingsOpen(true)} />
        <Tabs
          active={search.tab}
          canvasCount={canvasCount}
          boardCount={boardCount}
          filesCount={filesCount}
          onSelect={selectTab}
        />

        {search.tab === 'chat' ? (
          <>
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
              locateId={locateId}
              onLocateDone={() => setLocateId(undefined)}
              onSelectTask={selectTask}
              onOpenAgent={openAgent}
              onReviewProposal={reviewDraft}
              onReviewDelta={reviewDelta}
              onOpenProposalThread={(m) => setSearch({ tab: 'chat', thread: m.thread_root_id ?? m.id })}
            />
            {/* 主流被扣草稿(thread_root_id 为空)——线程内的由 ThreadPanel 渲染。 */}
            <HeldDraftList
              drafts={heldDrafts}
              channelId={channel.id}
              memberById={byId}
              canResolve={canResolve}
              onLocateMessage={locateMessage}
            />
          </>
        ) : search.tab === 'canvas' ? (
          <CanvasTab
            channelId={channel.id}
            tasks={tasks}
            members={members}
            presence={presenceQ.data ?? []}
            messages={messages}
            search={search}
            setSearch={setSearch}
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
          // 附件绑定在线程回复上时消息不在主流——locateMessage 切页签之外还会展开所在线程。
          <FilesTab channelId={channel.id} onLocate={locateMessage} />
        ) : (
          <section className="flow">
            <div className="boot">「{search.tab}」屏 B2 搭建中 —— 路由/深链已就绪。</div>
          </section>
        )}

        <Composer channelName={channel.name ?? ''} onSend={send} />
      </main>

      {settingsOpen && (
        <ChannelSettingsModal
          channel={channel}
          meId={me?.id}
          currentMode={notifyModeOf(snap, channel.id)}
          canManageProjects={me?.role === 'owner' || me?.role === 'admin'}
          onClose={() => setSettingsOpen(false)}
        />
      )}

      {threadRootId && (
        <ThreadPanel
          key={threadRootId}
          task={threadTask}
          rootMessageId={threadRootId}
          channelId={channel.id}
          memberById={byId}
          memberNames={memberNames}
          meName={me?.name ?? ''}
          meId={me?.id}
          presenceOf={(id) => presence[id]}
          usage={threadUsage}
          heldDrafts={heldDrafts}
          canResolve={canResolve}
          onLocateMessage={locateMessage}
          locateId={locateId}
          onLocateDone={() => setLocateId(undefined)}
          onClose={() => setSearch({ thread: undefined, task: undefined })}
          onSend={(body) => void api.sendMessage(channel.id, body, false)}
          onReviewProposal={reviewDraft}
          onReviewDelta={reviewDelta}
        />
      )}
    </div>
  );
}
