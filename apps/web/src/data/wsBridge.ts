// WS → Query 桥:每条信封 patch 对应 query 缓存(setQueryData),实现 NFR1「无刷新更新」。
// 数据形状全部来自 contracts-ts 生成物;未知 type 忽略(契约 C §3:新增事件不算不兼容)。
import type { QueryClient } from '@tanstack/react-query';

import type {
  ActivityItemPublic,
  CanvasDetail,
  CanvasEdgePublic,
  CanvasNodePublic,
  ChannelsSnapshot,
  Envelope,
  HeldDraftPublic,
  MemberPublic,
  MessagePublic,
  PresenceEntry,
  ReadPositionPublic,
  ReminderPublic,
  TaskPublic,
  TaskDetail,
  WorktreeUpdatedData,
} from '@coagentia/contracts-ts';

import { qk } from '../lib/queryKeys';

// canvas.* 事件 data 载 canvas_id（非 channel_id）,而快照缓存按 channel 存(qk.canvas)。
// 二法反查承载该 canvas 的频道快照:①按 canvas.id 命中(带 canvas_id 的事件);
// ②按内容命中(node_removed/edge_removed 只带 node_id/edge_id,无 canvas_id)。
// ①是②在谓词 = 「canvas.id 匹配」下的特例,故 patchCanvasById 直接委托 patchCanvasContaining(单一遍历/守卫)。
function patchCanvasContaining(
  qc: QueryClient,
  has: (d: CanvasDetail) => boolean,
  fn: (d: CanvasDetail) => CanvasDetail,
): void {
  for (const [key, d] of qc.getQueriesData<CanvasDetail>({ queryKey: ['canvas'] })) {
    if (d && has(d)) {
      qc.setQueryData<CanvasDetail>(key, (prev) => (prev ? fn(prev) : prev));
    }
  }
}
function patchCanvasById(
  qc: QueryClient,
  canvasId: string,
  fn: (d: CanvasDetail) => CanvasDetail,
): void {
  patchCanvasContaining(qc, (d) => d.canvas?.id === canvasId, fn);
}

export function applyEnvelope(qc: QueryClient, env: Envelope): void {
  const data = env.data as never;

  switch (env.type) {
    case 'message.created': {
      const { message } = data as { message: MessagePublic };
      qc.setQueryData<MessagePublic[]>(qk.messages(message.channel_id), (prev) => {
        const list = prev ?? [];
        if (list.some((m) => m.id === message.id)) return list; // 幂等(key/id 去重)
        return [...list, message];
      });
      // 消息可能携带文件绑定而 channelFiles 无专属事件——失效让文件页签/消息流附件卡
      // 在停留会话中收敛(M2 二轮 review:Agent 交付文件不实时)。仅激活观察者才 refetch。
      void qc.invalidateQueries({ queryKey: qk.channelFiles(message.channel_id) });
      break;
    }

    case 'task.created':
    case 'task.updated': {
      const { task } = data as { task: TaskPublic };
      qc.setQueryData<TaskPublic[]>(qk.tasks(task.channel_id), (prev) => {
        const list = prev ?? [];
        const i = list.findIndex((t) => t.id === task.id);
        if (i < 0) return [...list, task];
        const next = list.slice();
        next[i] = task;
        return next;
      });
      break;
    }

    case 'worktree.updated': {
      const { worktree } = data as WorktreeUpdatedData;
      const key = qk.taskDetail(worktree.task_id);
      // TaskDetail 还未加载时不造不完整缓存；打开线程时由 REST 拉全。
      if (qc.getQueryData<TaskDetail>(key) !== undefined) {
        qc.setQueryData<TaskDetail>(key, (prev) => (prev ? { ...prev, worktree } : prev));
      }
      // 分支状态/HEAD 变化会改变 Diff；已有观察者立即失效，未打开时不额外请求。
      void qc.invalidateQueries({ queryKey: qk.taskDiff(worktree.task_id) });
      break;
    }

    case 'presence.changed': {
      const p = data as { member_id: string; kind: PresenceEntry['kind']; status: PresenceEntry['status'] };
      qc.setQueryData<PresenceEntry[]>(qk.presence(), (prev) => {
        const list = prev ?? [];
        const i = list.findIndex((e) => e.member_id === p.member_id);
        const busy_detail =
          p.status === 'busy' ? (i >= 0 ? list[i]!.busy_detail : undefined) : undefined;
        const entry: PresenceEntry = { member_id: p.member_id, kind: p.kind, status: p.status, busy_detail };
        if (i < 0) return [...list, entry];
        const next = list.slice();
        next[i] = entry;
        return next;
      });
      break;
    }

    case 'agent.activity': {
      const a = data as { member_id: string; detail: string };
      qc.setQueryData<PresenceEntry[]>(qk.presence(), (prev) => {
        const list = prev ?? [];
        const i = list.findIndex((e) => e.member_id === a.member_id);
        if (i < 0) return list;
        const next = list.slice();
        next[i] = { ...next[i]!, busy_detail: a.detail };
        return next;
      });
      break;
    }

    case 'read.updated': {
      const r = data as { channel_id: string; member_id: string; last_read_message_id: string };
      // MVP 单人类:ChannelsSnapshot.read_positions 只反映当前 human owner 的已读游标
      // (契约 B §4.5「自身 read-position」)。read.updated 会为每个成员(含 agent)广播,
      // 若不过滤,agent 游标会污染快照并在 readPositionsMap 折叠时覆盖 owner→未读计数错(#6/#7)。
      const members = qc.getQueryData<MemberPublic[]>(qk.members());
      const owner = members?.find((m) => m.kind === 'human' && m.role === 'owner');
      if (!owner || r.member_id !== owner.id) break;
      qc.setQueryData<ChannelsSnapshot>(qk.channels(), (prev) => {
        if (!prev) return prev;
        const positions = (prev.read_positions as ReadPositionPublic[]) ?? [];
        const i = positions.findIndex(
          (x) => x.channel_id === r.channel_id && x.member_id === r.member_id,
        );
        const entry: ReadPositionPublic = {
          channel_id: r.channel_id,
          member_id: r.member_id,
          last_read_message_id: r.last_read_message_id,
          last_read_at: env.at,
        };
        const next = positions.slice();
        if (i < 0) next.push(entry);
        else next[i] = entry;
        return { ...prev, read_positions: next };
      });
      break;
    }

    case 'token_usage.reported': {
      const u = data as {
        task_id?: string | null;
        totals: { input_tokens: number; output_tokens: number };
      };
      if (!u.task_id) break;
      const taskId = u.task_id;
      qc.setQueryData<Record<string, number>>(qk.usageByTask(), (prev) => {
        const cur = prev ?? {};
        return {
          ...cur,
          [taskId]: (cur[taskId] ?? 0) + u.totals.input_tokens + u.totals.output_tokens,
        };
      });
      // TaskDetail.usage 是 REST 快照——失效让已打开的线程面板跟上实时上报
      // (M2 二轮 review:面板徽章冻结在打开时刻,与消息流任务牌口径矛盾)。
      void qc.invalidateQueries({ queryKey: qk.taskDetail(taskId) });
      break;
    }

    case 'activity.created': {
      // 前端只有 'all' 单档(挂账批2 简化:tab 过滤归 ActivityScreen 客户端),恒 upsert(缺则建)。
      const { item } = data as { item: ActivityItemPublic };
      // 全局广播携带的 member_id=接收者;REST 读面只回 Owner 本人条目,缓存口径必须一致——
      // 否则多人类工作区他人条目泄入列表/徽标,refetch 后又消失(闪烁)。members 未加载时放行(单人 MVP)。
      const actMembers = qc.getQueryData<MemberPublic[]>(qk.members());
      const actOwner = actMembers?.find((m) => m.kind === 'human' && m.role === 'owner');
      if (actOwner && item.member_id !== actOwner.id) break;
      qc.setQueryData<ActivityItemPublic[]>(qk.activity('all'), (prev) => {
        const list = prev ?? [];
        return list.some((a) => a.id === item.id) ? list : [item, ...list];
      });
      break;
    }

    case 'activity.done': {
      // 标记已读:'all' 单档把该 item 的 done_at 置为事件时间戳(Unread tab 客户端过滤自然收敛)。
      const { item_id } = data as { item_id: string };
      const stamp = env.at;
      qc.setQueryData<ActivityItemPublic[]>(qk.activity('all'), (prev) => {
        if (!prev) return prev;
        const i = prev.findIndex((a) => a.id === item_id);
        if (i < 0) return prev;
        const next = prev.slice();
        next[i] = { ...next[i]!, done_at: stamp };
        return next;
      });
      break;
    }

    // ---- M3b 画布(契约 C §7 canvas.*）。事件载状态,整体替换、重复应用无害(契约 C §1)。
    case 'canvas.node_added':
    case 'canvas.node_updated': {
      const { node } = data as { node: CanvasNodePublic };
      patchCanvasById(qc, node.canvas_id, (d) => {
        const list = d.nodes ?? [];
        const i = list.findIndex((n) => n.id === node.id);
        const nodes = i < 0 ? [...list, node] : list.map((n) => (n.id === node.id ? node : n));
        return { ...d, nodes };
      });
      break;
    }

    case 'canvas.node_removed': {
      const { node_id } = data as { node_id: string };
      // 删节点时其入/出边失去锚点,一并清理(server 会另发 edge_removed,但此处收敛防悬挂边)。
      patchCanvasContaining(
        qc,
        (d) => (d.nodes ?? []).some((n) => n.id === node_id),
        (d) => ({
          ...d,
          nodes: (d.nodes ?? []).filter((n) => n.id !== node_id),
          edges: (d.edges ?? []).filter(
            (e) => e.from_node_id !== node_id && e.to_node_id !== node_id,
          ),
        }),
      );
      break;
    }

    case 'canvas.edge_added': {
      const { edge } = data as { edge: CanvasEdgePublic };
      patchCanvasById(qc, edge.canvas_id, (d) => {
        const list = d.edges ?? [];
        if (list.some((e) => e.id === edge.id)) return d; // 幂等
        return { ...d, edges: [...list, edge] };
      });
      break;
    }

    case 'canvas.edge_removed': {
      const { edge_id } = data as { edge_id: string };
      patchCanvasContaining(
        qc,
        (d) => (d.edges ?? []).some((e) => e.id === edge_id),
        (d) => ({ ...d, edges: (d.edges ?? []).filter((e) => e.id !== edge_id) }),
      );
      break;
    }

    case 'canvas.layout_updated': {
      const p = data as {
        canvas_id: string;
        positions: Array<{ node_id: string; x: number; y: number }>;
      };
      const posById = new Map(p.positions.map((q) => [q.node_id, q]));
      patchCanvasById(qc, p.canvas_id, (d) => ({
        ...d,
        nodes: (d.nodes ?? []).map((n) => {
          const pos = posById.get(n.id);
          return pos ? { ...n, pos_x: pos.x, pos_y: pos.y } : n;
        }),
      }));
      break;
    }

    case 'canvas.baseline_advanced': {
      const b = data as { canvas_id: string; baseline_version: number; baseline_hash: string };
      patchCanvasById(qc, b.canvas_id, (d) => ({
        ...d,
        canvas: { ...d.canvas, baseline_version: b.baseline_version, baseline_hash: b.baseline_hash },
      }));
      break;
    }

    // ---- M4a Reminders(契约 C reminder.*）。data 载 { reminder: ReminderPublic }。
    // 按 agent_member_id patch qk.agentReminders 缓存:created=去重 append(末尾,与 REST
    // created_at 升序一致)、updated(含 cancel 反流 status=cancelled)=按 id 替换。
    // 该 agent 的 reminders 未加载(getQueryData===undefined)则放行不建——详情页打开时再拉全。
    case 'reminder.created':
    case 'reminder.updated': {
      const { reminder } = data as { reminder: ReminderPublic };
      const key = qk.agentReminders(reminder.agent_member_id);
      if (qc.getQueryData<ReminderPublic[]>(key) === undefined) break;
      qc.setQueryData<ReminderPublic[]>(key, (prev) => {
        const list = prev ?? [];
        const i = list.findIndex((r) => r.id === reminder.id);
        if (i < 0) return [...list, reminder]; // created:去重后追加
        const next = list.slice();
        next[i] = reminder; // updated / 幂等重放:按 id 替换
        return next;
      });
      break;
    }

    // ---- M4b HeldDraft(被扣草稿,契约 C held_draft.*）。data 载 { draft: HeldDraftPublic }
    // (hub 实体键 = "draft")。按 draft.channel_id patch qk.heldDrafts 缓存:created=去重 append、
    // updated(含 released/discarded/resolved 终态反流)=按 id 替换。该频道的 heldDrafts 未加载
    // (getQueryData===undefined)则放行不建——卡片渲染位挂载时再拉全(同 reminder 范式)。
    case 'held_draft.created':
    case 'held_draft.updated': {
      const { draft } = data as { draft: HeldDraftPublic };
      const key = qk.heldDrafts(draft.channel_id);
      if (qc.getQueryData<HeldDraftPublic[]>(key) === undefined) break;
      qc.setQueryData<HeldDraftPublic[]>(key, (prev) => {
        const list = prev ?? [];
        const i = list.findIndex((d) => d.id === draft.id);
        if (i < 0) return [...list, draft]; // created:去重后追加
        const next = list.slice();
        next[i] = draft; // updated / 幂等重放 / 终态反流:按 id 替换
        return next;
      });
      break;
    }

    default:
      // 未知/未处理事件:忽略(契约 C §3)
      break;
  }
}
