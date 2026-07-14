// P1 会话屏(主列),在新路由/基座下渲染,行为不回退(静载 + WS 五事件无刷新更新,NFR1)。
// 视图状态来自类型化深链 search(tab/task/thread);服务端数据来自 TanStack Query 缓存。
// P5 线程面板由 ?thread= 驱动,在主列右侧展开(不新增顶层路由)。
import { useEffect, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';

import type { MessagePublic, TaskStatus } from '@coagentia/contracts-ts';
import { UNCLAIMABLE_STATUSES } from '@coagentia/contracts-ts';

import type { ChannelSearch, Tab } from '../routes/search';
import {
  memberMap, presenceMap, readPositionsMap,
  useCanvasSnapshot, useChannelFiles, useChannelsSnapshot, useHeldDrafts, useMembers, useMessages,
  usePresence, useTasks, useThread, useUsageByTask,
} from '../data/queries';
import { useReadCursor } from '../data/useReadCursor';
import { useArchiveChannel, useDeleteChannel, useUnarchiveChannel } from '../data/queries';
import { useUiStore } from '../lib/store';
import { Composer } from '../components/Composer';
import { MessageFlow } from '../components/MessageFlow';
import { BoardTab } from '../components/BoardTab';
import { CanvasTab } from '../components/CanvasTab';
import { FilesTab } from '../components/FilesTab';
import { Tabs } from '../components/Tabs';
import { Topbar } from '../components/Topbar';
import { ThreadPanel } from './ThreadPanel';
import { PreviewDeck } from '../components/PreviewPanel';
import { HeldDraftList } from './HeldDraftCard';
import { ChannelSettingsModal } from '../components/ChannelSettingsModal';
import { ConfirmModal } from '../components/ConfirmModal';
import { useToast } from '../components/Toast';
import { notifyModeOf } from '../lib/notify';
import { api, ApiError } from '../api';

// K2(M8a 加固批)：?thread= 深链直开修复。activeChannelId 只存在 UI store(URL 无 channel 字段)——
// 板块点击路径(WorkspaceBoardScreen.selectTask)会先 setActiveChannel(t.channel_id) 再导航,故命中；
// 但直接携 ?thread= 打开(如粘贴链接/新标签)时 activeChannelId 可能仍是旧频道甚至默认 #build,与线程
// 实际所属频道对不上 → 下方 !channel 早退永远卡在 loading…。从线程消息数组解析所属频道 id(抽为
// 纯函数,可脱离整屏组件单测)。
export function resolveThreadChannelId(
  messages: MessagePublic[] | undefined,
  rootMessageId: string | undefined,
): string | undefined {
  if (!messages || messages.length === 0) return undefined;
  return messages.find((m) => m.id === rootMessageId)?.channel_id ?? messages[0]?.channel_id;
}

export function ChannelChatScreen({ search, setSearch }: {
  search: ChannelSearch;
  setSearch: (next: Partial<ChannelSearch>) => void;
}) {
  const activeChannelId = useUiStore((s) => s.activeChannelId);
  const setActiveChannel = useUiStore((s) => s.setActiveChannel);
  const setActiveDraft = useUiStore((s) => s.setActiveDraft);
  const setActiveDelta = useUiStore((s) => s.setActiveDelta);
  const navigate = useNavigate();
  const toast = useToast();
  const archiveM = useArchiveChannel();
  const unarchiveM = useUnarchiveChannel();
  const deleteM = useDeleteChannel();
  // 「定位到消息」目标(P4 → 会话流):瞬态视图状态,不进深链。
  const [locateId, setLocateId] = useState<string | undefined>();
  // 频道设置弹窗(B-M5-1):⋯ 菜单入口,瞬态视图状态。
  const [settingsOpen, setSettingsOpen] = useState(false);
  // F8 删除频道确认弹窗。
  const [deleteOpen, setDeleteOpen] = useState(false);

  const channelsQ = useChannelsSnapshot();
  const membersQ = useMembers();
  const presenceQ = usePresence();
  const messagesQ = useMessages(activeChannelId ?? undefined);
  const tasksQ = useTasks(activeChannelId ?? undefined);
  const usageQ = useUsageByTask();
  const filesQ = useChannelFiles(activeChannelId ?? undefined);
  const canvasQ = useCanvasSnapshot(activeChannelId ?? undefined);
  const heldDraftsQ = useHeldDrafts(activeChannelId ?? undefined);
  const markRead = useReadCursor();

  // K2：独立按 rootMessageId 查一次线程(与 ThreadPanel 内 useThread 同 queryKey,命中缓存不重复拉取)，
  // 解出线程所属频道；一旦与当前活跃频道不一致(深链直开/跨频道旧态)就纠偏切过去，让下方 !channel
  // 早退能收敛,而不是永远卡在 loading…。search.thread 缺失时 useThread 内 enabled:false，零额外开销。
  const deepLinkThreadQ = useThread(search.thread);
  useEffect(() => {
    const targetChannelId = resolveThreadChannelId(deepLinkThreadQ.data, search.thread);
    if (targetChannelId && targetChannelId !== activeChannelId) {
      setActiveChannel(targetChannelId);
    }
  }, [deepLinkThreadQ.data, search.thread, activeChannelId, setActiveChannel]);

  // F1 已读游标上报：会话（chat）页签可见且窗口聚焦时，把最新消息标记为已读（节流/去重在 markRead 内）。
  // 覆盖三情形：①打开频道 / 切到 chat 页签；②新消息到达（latestMsgId 变化）；③窗口重获焦点。
  // 只报当前活跃频道 + chat 页签 → 后台频道/非会话页签不误报（交互 §… 未读永不误清）。
  const latestMsgId = messagesQ.data?.length
    ? messagesQ.data[messagesQ.data.length - 1]!.id
    : undefined;
  useEffect(() => {
    if (search.tab !== 'chat' || !activeChannelId || !latestMsgId) return;
    const mark = () => { if (document.hasFocus()) markRead(activeChannelId, latestMsgId); };
    mark();
    window.addEventListener('focus', mark);
    return () => window.removeEventListener('focus', mark);
  }, [search.tab, activeChannelId, latestMsgId, markRead]);

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
  // F8 频道归档 / 取消归档 / 删除（Topbar ⋯ 菜单入口）。
  const archived = !!channel.archived_at;
  const doArchive = () => archiveM.mutate(channel.id, {
    onSuccess: () => toast.push('频道已归档', { tone: 'success' }),
    onError: (e: unknown) => toast.push(e instanceof ApiError ? e.message : '归档失败', { tone: 'error' }),
  });
  const doUnarchive = () => unarchiveM.mutate(channel.id, {
    onSuccess: () => toast.push('频道已取消归档', { tone: 'success' }),
    onError: (e: unknown) => toast.push(e instanceof ApiError ? e.message : '取消归档失败', { tone: 'error' }),
  });
  const doDelete = () => deleteM.mutate(channel.id, {
    onSuccess: () => {
      setDeleteOpen(false);
      toast.push('频道已删除', { tone: 'success' });
      // 当前频道已删 → 切到另一个非归档频道（无则清空，RootLayout 会重设默认）。
      const next = (snap?.items ?? []).find(
        (c) => c.id !== channel.id && c.kind === 'channel' && !c.archived_at,
      );
      setActiveChannel(next?.id ?? null);
    },
    onError: (e: unknown) => {
      setDeleteOpen(false);
      toast.push(
        e instanceof ApiError && e.code === 'CHANNEL_NOT_EMPTY'
          ? '频道含消息，无法删除（消息不可变）——请改用归档'
          : e instanceof ApiError ? e.message : '删除频道失败',
        { tone: 'error' },
      );
    },
  });
  const selectTab = (tab: Tab) => setSearch({ tab });
  // F5 在线程中回复：普通消息为 root 开线程（回复不进主流，PRD §4.1）；回复本身有 root 则归其根线程。
  const openThread = (m: { id: string; thread_root_id?: string | null }) =>
    setSearch({ tab: 'chat', thread: m.thread_root_id ?? m.id });
  // F5 转为任务（仅顶级频道消息，MessageFlow 已按 !isTask && !thread_root_id && canConvertToTask 守门）。
  // 成功后任务牌由 WS task.created 反流在消息处出现（无需导航）。
  const convertToTask = (m: { id: string }) => {
    void api.convertToTask(m.id)
      .then(() => toast.push('已转为任务', { tone: 'success' }))
      .catch((e: unknown) => toast.push(e instanceof ApiError ? e.message : '转为任务失败', { tone: 'error' }));
  };
  const canConvertToTask = channel.kind !== 'dm';
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
        <Topbar
          channel={channel}
          stackNames={stackNames}
          onOpenSettings={() => setSettingsOpen(true)}
          onArchive={doArchive}
          onUnarchive={doUnarchive}
          onDelete={() => setDeleteOpen(true)}
        />
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
              onReplyInThread={openThread}
              onConvertToTask={convertToTask}
              canConvertToTask={canConvertToTask}
              onToast={(msg) => toast.push(msg, { tone: 'success' })}
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

        {/* F8 归档频道只读：可浏览历史，不可发消息（FR-1.3 冻结语义）。 */}
        {archived ? (
          <footer className="composer archived-note" role="note">
            此频道已归档 · 只读。可在顶栏 ⋯ 菜单「取消归档」恢复。
          </footer>
        ) : (
          <Composer channelName={channel.name ?? ''} onSend={send} />
        )}
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

      {/* F8 删除频道确认（不可撤销）：键入频道名防呆；仅空频道可删，含消息 → 409 改用归档（toast）。 */}
      {deleteOpen && (
        <ConfirmModal
          title="删除频道"
          danger
          confirmLabel="删除频道"
          requireText={channel.name ?? ''}
          requireTextLabel={`键入 “${channel.name}” 以确认删除`}
          busy={deleteM.isPending}
          message={
            <>
              删除频道 <span className="em">#{channel.name}</span> <span className="em">不可撤销</span>。
              仅空频道可删；含消息的频道请改用「归档」（消息不可变）。
            </>
          }
          onConfirm={doDelete}
          onClose={() => setDeleteOpen(false)}
        />
      )}

      {threadRootId && (
        <ThreadPanel
          key={threadRootId}
          task={threadTask}
          rootMessageId={threadRootId}
          channelId={channel.id}
          archived={archived}
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
          // 线程回复携 thread_root_id（归线程不进主流）；失败 toast（不静默吞掉发送）。
          onSend={(body) =>
            void api.sendMessage(channel.id, body, false, threadRootId).catch((e: unknown) =>
              toast.push(e instanceof ApiError ? e.message : '发送失败', { tone: 'error' }),
            )}
          onReviewProposal={reviewDraft}
          onReviewDelta={reviewDelta}
        />
      )}

      {/* M7 并排预览 deck（FR-11.2）：底部横排多面板，由 [预览] 按钮 openPreview 驱动；空则不渲染。 */}
      <PreviewDeck />
    </div>
  );
}
