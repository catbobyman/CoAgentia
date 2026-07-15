// WS → Query 桥:每条信封 patch 对应 query 缓存(setQueryData),实现 NFR1「无刷新更新」。
// 数据形状全部来自 contracts-ts 生成物;未知 type 忽略(契约 C §3:新增事件不算不兼容)。
import type { QueryClient } from '@tanstack/react-query';

import type {
  ActivityItemPublic,
  AgentPublic,
  AgentUpdatedData,
  CanvasDetail,
  CanvasEdgePublic,
  CanvasNodePublic,
  ChannelsSnapshot,
  DeploymentData,
  DeploymentLogData,
  DeploymentPublic,
  Envelope,
  HeldDraftPublic,
  LandingBatchPublic,
  MemberPublic,
  MessagePublic,
  PresenceEntry,
  PreviewUpdatedData,
  ProposalPublic,
  ReadPositionPublic,
  ReminderPublic,
  TaskContractData,
  TaskPublic,
  TaskDetail,
  WorkspacePublic,
  WorkspaceUpdatedData,
  WorktreeUpdatedData,
} from '@coagentia/contracts-ts';

import { qk } from '../lib/queryKeys';
import { type DeployLogState, appendDeployLogChunk } from './deployLog';

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
      // 线程回复实时收敛（qk.thread 是独立 GET 缓存，非 qk.messages 派生）：携 thread_root_id 的新消息
      // 失效对应线程查询——打开的线程面板即时收到回复（O8 汇总摘要/阻断系统消息落线程，横幅/摘要卡
      // 靠此实时刷新；未打开的线程零额外请求）。
      if (message.thread_root_id) {
        void qc.invalidateQueries({ queryKey: qk.thread(message.thread_root_id) });
      }
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
      // PS-WT ② 工作树管理台：前缀失效 live=0/1 两态（进屏靠此 WS 兜底刷新，不轮询）。未打开管理台
      // 无观察者、不额外请求（REST 是事实源，铁律 1）。
      void qc.invalidateQueries({ queryKey: ['worktreesConsole'] });
      break;
    }

    // ---- M7 预览会话（契约 C preview.updated）。data 载 { preview: PreviewSessionPublic }，
    // daemon 状态流转 starting→running（携 port）→failed（携 fail_log_tail）/recycled 经此反流。
    // 按 preview.task_id patch qk.preview 缓存（整体替换，重复应用无害）。未加载（面板未开、getQueryData
    // ===undefined）则放行不建——面板打开时由 POST(ensure) 播种，同 worktree/reminder/held_draft 范式。
    case 'preview.updated': {
      const { preview } = data as PreviewUpdatedData;
      const key = qk.preview(preview.task_id);
      if (qc.getQueryData(key) === undefined) break;
      qc.setQueryData(key, preview);
      break;
    }

    // ---- M7b 部署（契约 C deployment.*）。created/updated 全量广播，载 { deployment:
    // DeploymentPublic }（daemon queued→running→success/failed 反流，携 url/exit_code/token_summary）。
    // 按 deployment.id patch qk.deployment 缓存：整体替换、重复应用无害。未加载（卡未挂载、getQueryData
    // ===undefined）则放行不建——部署卡挂载时由 GET 拉全（同 proposal/preview 范式）。POST 触发的
    // mutation 亦会先播种缓存，故 created 通常已有锚点。
    case 'deployment.created':
    case 'deployment.updated': {
      const { deployment } = data as DeploymentData;
      const key = qk.deployment(deployment.id);
      if (qc.getQueryData<DeploymentPublic>(key) === undefined) break;
      qc.setQueryData<DeploymentPublic>(key, deployment);
      break;
    }

    // deployment.log = 订阅制实时日志（只发订阅该 deployment 的连接，ws/hub.py 过滤）。载
    // { deployment_id, chunk_seq, lines }，按 chunk_seq 去重并入 qk.deploymentLog 累积缓存
    // （R-14：单调去重 + 历史首页前进 pending 缓冲）。未加载（卡未打开日志视图、未播种）则放行不建。
    case 'deployment.log': {
      const d = data as DeploymentLogData;
      const key = qk.deploymentLog(d.deployment_id);
      if (qc.getQueryData<DeployLogState>(key) === undefined) break;
      qc.setQueryData<DeployLogState>(key, (prev) =>
        appendDeployLogChunk(prev, d.chunk_seq, d.lines ?? []),
      );
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

    // ---- M6b 拆解提案（契约 C §7 M6 预留族；事件载 ProposalPublic 完整形状,整体替换、重复应用
    // 无害）。proposal.updated = 生命周期态刷新（validating/repairing/landed/failed…）；draft.presented
    // = 校验通过进 awaiting_confirm（草稿层渲染归后半——此处仅刷提案卡态,handler 挂点已就位）。
    // 二者同载 {proposal}，按 proposal.id patch qk.proposal 缓存（未加载则放行不建——提案卡挂载时
    // 由 REST 拉全，同 reminder/held_draft 范式）。消息流内提案卡数据绑定该 query,patch 即刷新。
    // proposal.updated（生命周期态）/ draft.presented（校验通过进 awaiting_confirm，草稿层渲染源）/
    // delta.proposed·adjusted·confirmed·rejected（delta 提案生命周期，同载 {proposal}）——全部按
    // proposal.id patch qk.proposal 缓存（未加载则放行不建，同 reminder/held_draft 范式）。消息流提案卡、
    // 草稿层、delta 面板均绑定该 query，patch 即刷新。
    case 'proposal.updated':
    case 'draft.presented':
    case 'delta.proposed':
    case 'delta.adjusted':
    case 'delta.confirmed':
    case 'delta.rejected': {
      const { proposal } = data as { proposal: ProposalPublic };
      const key = qk.proposal(proposal.id);
      if (qc.getQueryData<ProposalPublic>(key) === undefined) break;
      qc.setQueryData<ProposalPublic>(key, proposal);
      break;
    }

    // full 草稿生命周期的「仅载 proposal_id」族（契约 C：draft.superseded/confirmed/rejected =
    // ProposalRefData `{proposal_id}`；draft.adjusted = DraftAdjustedData `{proposal_id, adjustments}`）
    // ——**均无完整 ProposalPublic**，无法整体替换，故一律失效该提案 query 让已挂载的提案卡/草稿层
    // refetch 到最新态（未挂载无观察者、不触发请求）。
    //   注意（易错点）：**delta.**adjusted/confirmed/rejected 载完整 `{proposal}` 走上面的整体替换 case；
    // 而 **draft.**adjusted/confirmed/rejected 只载 proposal_id 走本 case——两族同名后缀但 payload 形状
    // 不同（DraftAdjustedData/ProposalRefData vs ProposalData），切勿并进上面的 setQueryData 分支。
    case 'draft.superseded':
    case 'draft.adjusted':
    case 'draft.confirmed':
    case 'draft.rejected': {
      const { proposal_id } = data as { proposal_id: string };
      void qc.invalidateQueries({ queryKey: qk.proposal(proposal_id) });
      break;
    }

    // ---- M6b 落地事件族（landing.*，契约 C §7）。data 载 { batch: LandingBatchPublic }；事件收尾即
    // 结构落定——失效该频道画布/任务/主流（新增节点/任务/锚点消息经此收敛，"画布刷新"）。started 不改
    // 结构（占位提示）故不失效。toast（completed「拆解已落地」/ fail_closed 错误）由 useWsSync 经 store
    // 信号交 <LandingToaster>（wsBridge 保持纯缓存 patch，无 toast/store 耦合）。
    case 'landing.completed':
    case 'landing.fail_closed': {
      const { batch } = data as { batch: LandingBatchPublic };
      void qc.invalidateQueries({ queryKey: qk.canvas(batch.channel_id) });
      void qc.invalidateQueries({ queryKey: qk.tasks(batch.channel_id) });
      void qc.invalidateQueries({ queryKey: qk.messages(batch.channel_id) });
      break;
    }
    case 'landing.started':
      break; // 占位提示态，无结构变更（confirm 202 的「落地执行中」toast 已覆盖起步反馈）

    // ---- F10 死壳补齐：契约登记但 wsBridge 原零处理的实时缺口（靠重连 resyncAll 兜底 → 补精确 patch）。

    // F10a 机器上下线（daemon 核心信号）：computer.* 载完整 ComputerPublic，但 computers 是全量列表
    // 查询（部分播种会不完整）——失效让在场观察者（ComputersScreen/AgentDetailScreen）refetch，在线点
    // 实时变灰/变绿。未打开则无观察者、不额外请求（REST 是事实源，铁律 1）。
    case 'computer.connected':
    case 'computer.disconnected':
    case 'computer.updated':
      void qc.invalidateQueries({ queryKey: qk.computers() });
      break;

    // F10b 契约实时（"让 @Agent 起草"闭环）：task_contract.* 载 {contract}，按 contract.task_id 失效
    // 对应 taskDetail（契约卡挂在线程面板的 TaskDetail.contracts 上）。loop_contract 的 task_id 为空
    // （挂 reminder）→ 无任务详情面，跳过。
    case 'task_contract.created':
    case 'task_contract.updated': {
      const { contract } = data as TaskContractData;
      if (contract.task_id) {
        void qc.invalidateQueries({ queryKey: qk.taskDetail(contract.task_id) });
      }
      break;
    }

    // F10d 频道/成员实时（F8/F3/F9 的实时面配套）：channel.*（五种）失效 channels 快照（新频道/设置/
    // 归档/删除/成员进出）；member.*（三种）失效 members（新成员/改角色/移除）。均 REST 收敛（铁律 1）。
    case 'channel.created':
    case 'channel.updated':
    case 'channel.deleted':
    case 'channel.member_added':
    case 'channel.member_removed':
      void qc.invalidateQueries({ queryKey: qk.channels() });
      break;

    case 'member.created':
    case 'member.updated':
    case 'member.removed':
      void qc.invalidateQueries({ queryKey: qk.members() });
      break;

    // F10（F7 配套）agent.updated 载完整 {agent}，按 agent.member_id 整体替换 qk.agent 缓存（下次启动
    // 生效的 runtime/model 改动实时反映）。未加载（详情页未打开、getQueryData===undefined）则放行不建，
    // 同 reminder/preview 范式。
    case 'agent.updated': {
      const { agent } = data as AgentUpdatedData;
      const key = qk.agent(agent.member_id);
      if (qc.getQueryData(key) === undefined) break;
      qc.setQueryData<AgentPublic>(key, agent);
      break;
    }

    // F10（F4 配套）workspace.updated 载完整 {workspace}，整体替换 workspace 缓存（他端改主题/桌面通知
    // 等设置实时收敛——主题经 RootLayout 的 ui_theme effect 落 data-theme）。workspace 是恒加载单例。
    case 'workspace.updated': {
      const { workspace } = data as WorkspaceUpdatedData;
      qc.setQueryData<WorkspacePublic>(qk.workspace(), workspace);
      break;
    }

    default:
      // 未知/未处理事件:忽略(契约 C §3)
      break;
  }
}
