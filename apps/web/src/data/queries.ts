// TanStack Query 包 REST 拉取(api.ts 演化的消费面)。
// 服务端数据的唯一事实源 = 这里的 query 缓存;WS 事件通过 data/wsBridge 做 setQueryData patch。
import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';

import type {
  AgentCreate,
  AgentPatch,
  CanvasDetail,
  ChannelNotificationSettingPublic,
  ChannelPatch,
  ChannelPublic,
  ChannelsSnapshot,
  DeploymentPublic,
  HeldDraftPublic,
  LifecycleAction,
  MemberPublic,
  MemberRole,
  MessagePublic,
  NotificationMode,
  PresenceEntry,
  PreviewSessionPublic,
  ProjectCreate,
  ProjectPatch,
  ProposalPublic,
  ReadPositionPublic,
  TaskPublic,
  TemplateCreate,
  TemplateInstantiate,
  UsageLevel,
  WorkspacePatch,
  WorkspacePublic,
} from '@coagentia/contracts-ts';

import { api, ApiError } from '../api';
import { useToast } from '../components/Toast';
import { qk } from '../lib/queryKeys';
import {
  type DeployLogState, EMPTY_DEPLOY_LOG, flushPendingDeployLog, mergeDeployLogPage,
} from './deployLog';

// ---- 单实体/列表查询
export const useWorkspace = () =>
  useQuery({ queryKey: qk.workspace(), queryFn: () => api.workspace() });

// 工作区设置 PATCH（F4：主题/桌面通知/声音/附件上限/欢迎语）。成功以响应体替换 workspace 缓存
// （主题切换即时生效——RootLayout 的主题 effect 监听 ui_theme 落 documentElement）；server 另发
// workspace.updated 广播（wsBridge 反流），本地替换是即时收敛。错误由调用方处理（主题失败回滚）。
export const usePatchWorkspace = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: WorkspacePatch) => api.patchWorkspace(patch),
    onSuccess: (ws: WorkspacePublic) => qc.setQueryData<WorkspacePublic>(qk.workspace(), ws),
  });
};

export const useMembers = () =>
  useQuery({ queryKey: qk.members(), queryFn: () => api.members() });

export const useChannelsSnapshot = () =>
  useQuery({ queryKey: qk.channels(), queryFn: () => api.channels() });

export const usePresence = () =>
  useQuery({
    queryKey: qk.presence(),
    queryFn: async () => (await api.presence()).items,
  });

export const useMessages = (channelId: string | undefined) =>
  useQuery({
    queryKey: qk.messages(channelId ?? '_'),
    queryFn: async () => (await api.messages(channelId!)).items as MessagePublic[],
    enabled: !!channelId,
  });

export const useTasks = (channelId: string | undefined) =>
  useQuery({
    queryKey: qk.tasks(channelId ?? '_'),
    queryFn: () => api.tasks(channelId!),
    enabled: !!channelId,
  });

// 线程流(P5):root + 回复(GET /api/messages/{root}/thread)。
export const useThread = (rootMessageId: string | undefined) =>
  useQuery({
    queryKey: qk.thread(rootMessageId ?? '_'),
    queryFn: () => api.thread(rootMessageId!),
    enabled: !!rootMessageId,
  });

// 机器(P7)与 Agent 详情(P6)只读查询。
export const useComputers = () =>
  useQuery({ queryKey: qk.computers(), queryFn: () => api.computers() });

// F6 机器改名 / 移除。改名 server 另发 computer.updated（wsBridge 反流）；移除 204 无 WS 事件——
// 成功后 invalidate computers 收敛（REST 是事实源）。错误（COMPUTER_HAS_AGENTS/PROJECTS 409）由
// 调用方据 code 组 toast——防呆 disabled 是 UI 责任、API 兜底。
export const usePatchComputer = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ computerId, name }: { computerId: string; name: string }) =>
      api.patchComputer(computerId, name),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.computers() }),
  });
};

export const useDeleteComputer = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (computerId: string) => api.deleteComputer(computerId),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.computers() }),
  });
};

export const useAgent = (memberId: string | undefined) =>
  useQuery({
    queryKey: qk.agent(memberId ?? '_'),
    queryFn: () => api.agent(memberId!),
    enabled: !!memberId,
  });

export const useAgentSkills = (memberId: string | undefined) =>
  useQuery({
    queryKey: qk.agentSkills(memberId ?? '_'),
    queryFn: () => api.agentSkills(memberId!),
    enabled: !!memberId,
  });

export const useAgentReminders = (memberId: string | undefined) =>
  useQuery({
    queryKey: qk.agentReminders(memberId ?? '_'),
    queryFn: () => api.agentReminders(memberId!),
    enabled: !!memberId,
  });

// P6 取消 reminder:成功后失效该 agent 的 reminders 列表让列表收敛。
// WS reminder.updated 亦会把 status 反流为 cancelled(wsBridge)——invalidate 是兜底(WS 未连也收敛)。
export const useCancelReminder = (memberId: string) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (reminderId: string) => api.cancelReminder(reminderId),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.agentReminders(memberId) }),
  });
};

// M5(B §11.3):技能白名单全量替换(PUT)。成功后失效该 agent 的 skills 让列表收敛
// (WS AGENT_UPDATED 只反流 agent 主体，skills 明细无专属事件——invalidate 是单一收敛路径)。
export const usePutAgentSkills = (memberId: string) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (skills: string[]) => api.putAgentSkills(memberId, skills),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.agentSkills(memberId) }),
  });
};

// F2 Agent 生命周期（Stop/Restart/Session reset/Full reset）：调 lifecycle 端点，成功后失效该 agent
// 详情（presence 变化由 WS agent.status/presence.changed 反流，invalidate 是兜底）。错误（如 daemon
// 离线 503）由调用方据 code 组 toast（F2 要求离线单独文案）——故本 hook 不内置 error toast。
export const useAgentLifecycle = (memberId: string) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (action: LifecycleAction) => api.agentLifecycle(memberId, action),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.agent(memberId) }),
  });
};

// F7 Agent runtime/model/description 编辑（PATCH，R3 门）。成功以响应体替换 agent 缓存（下次启动
// 生效；server 另发 agent.updated 广播，wsBridge 反流，本地替换是即时收敛）。错误由调用方 toast。
export const usePatchAgent = (memberId: string) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: AgentPatch) => api.patchAgent(memberId, patch),
    onSuccess: (agent) => qc.setQueryData(qk.agent(memberId), agent),
  });
};

// M5(B-M5-1)频道阈值/描述/公开私有 PATCH。server 另发 CHANNEL_UPDATED，但 wsBridge 无该 case
// （契约 C 零修订，裁决 #7）——故成功后 invalidate channels 收敛（REST 是事实源，铁律 1）。
export const usePatchChannel = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ channelId, patch }: { channelId: string; patch: ChannelPatch }) =>
      api.patchChannel(channelId, patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.channels() }),
  });
};

// F3 发私信：POST /dms 幂等（已存在同对 DM 返既有）。成功后 invalidate channels 让 DM 分组出现
// （server 另发 channel.created，wsBridge 反流，invalidate 是兜底）。返回频道供调用方 mutateAsync
// 后路由跳转。错误由调用方 toast。
export const useCreateDm = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (memberId: string) => api.createDm(memberId),
    // DM 频道须进 channels 快照，跳转后 ChannelChatScreen 才能按 activeChannelId 命中渲染。
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.channels() }),
  });
};

// F9 成员改角色（owner 拍板做）。权限矩阵在 server 执法（admin 仅 Member 级、R1 Agent 永不 Owner）。
// 成功 server 另发 member.updated（wsBridge 反流），本地 invalidate members 兜底。错误（403
// PERMISSION_DENIED 携 rule）由调用方据 code/rule 组 toast。
export const usePatchMember = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ memberId, role }: { memberId: string; role: MemberRole }) =>
      api.patchMember(memberId, role),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.members() }),
  });
};

// F8 频道归档 / 取消归档 / 删除（require_admin）。归档/取消归档 server 发 channel.updated + 系统消息
// （wsBridge 反流）；删除发 channel.deleted。成功后 invalidate channels（+ 归档态变化影响侧栏分组
// 与主流可发性）；REST 是事实源，invalidate 是收敛路径。错误（CHANNEL_NOT_EMPTY 409）由调用方 toast。
function useChannelStateMutation<A>(call: (arg: A) => Promise<unknown>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: call,
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.channels() }),
  });
}
export const useArchiveChannel = () =>
  useChannelStateMutation<string>((channelId) => api.archiveChannel(channelId));
export const useUnarchiveChannel = () =>
  useChannelStateMutation<string>((channelId) => api.unarchiveChannel(channelId));
export const useDeleteChannel = () =>
  useChannelStateMutation<string>((channelId) => api.deleteChannel(channelId));

/** 通知设置 PUT 后本地更新 ChannelsSnapshot.notification_settings（裁决 #7 零新增 WS 事件，PUT 后
 *  操作方本地更新）：非默认 mode → upsert 该频道行；mode=all（默认）→ 从「非默认行」列表剔除。 */
function patchNotificationSetting(
  qc: QueryClient,
  channelId: string,
  memberId: string,
  mode: NotificationMode,
) {
  qc.setQueryData<ChannelsSnapshot>(qk.channels(), (prev) => {
    if (!prev) return prev;
    const rows = (prev.notification_settings as ChannelNotificationSettingPublic[] | undefined) ?? [];
    const rest = rows.filter((r) => r.channel_id !== channelId);
    const next = mode === 'all' ? rest : [...rest, { channel_id: channelId, member_id: memberId, mode }];
    return { ...prev, notification_settings: next };
  });
}

// 通知设置：人类本人自治。成功后按响应体 mode 本地更新快照（乐观地免整表 refetch）。
export const usePutNotificationSetting = (meId: string | undefined) => {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: ({ channelId, mode }: { channelId: string; mode: NotificationMode }) =>
      api.putNotificationSetting(channelId, mode),
    onSuccess: (res) => {
      if (meId) patchNotificationSetting(qc, res.channel_id, res.member_id ?? meId, res.mode ?? 'all');
    },
    onError: (e: unknown) =>
      toast.push(e instanceof ApiError ? e.message : '更新通知设置失败', { tone: 'error' }),
  });
};

// ---- M5b 模板(B §11.1/§11.2)。列表工作区级(builtin 置前，body 全量供向导预览)；模板 CRUD 零 WS
// 事件(裁决 #7)——存为模板成功后 invalidate 列表收敛；实例化成功后 invalidate 目标频道画布/任务/
// 主流(WS task.created/canvas.*/message.created 亦反流，invalidate 是兜底，REST 是事实源铁律 1)。
export const useTemplates = () =>
  useQuery({ queryKey: qk.templates(), queryFn: () => api.templates() });

export const useCreateTemplate = () => {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (body: TemplateCreate) => api.createTemplate(body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.templates() });
      toast.push('已存为模板', { tone: 'success' });
    },
    onError: (e: unknown) =>
      toast.push(e instanceof ApiError ? e.message : '存为模板失败', { tone: 'error' }),
  });
};

export const useInstantiateTemplate = () => {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: ({ templateId, body, idempotencyKey }: {
      templateId: string; body: TemplateInstantiate; idempotencyKey?: string;
    }) => api.instantiateTemplate(templateId, body, idempotencyKey),
    onSuccess: (_res, { body }) => {
      const ch = body.channel_id;
      void qc.invalidateQueries({ queryKey: qk.canvas(ch) });
      void qc.invalidateQueries({ queryKey: qk.tasks(ch) });
      void qc.invalidateQueries({ queryKey: qk.messages(ch) });
      toast.push('已从模板实例化到频道', { tone: 'success' });
    },
    // 422 VALIDATION_FAILED 携 details.missing(未覆盖的角色占位名列表)——UI 责任层已用
    // missingRoleMappings 拦在实例化钮前，此处仅兜底(如并发改模板等边缘态)并把占位名带进 toast。
    onError: (e: unknown) => {
      let msg = e instanceof ApiError ? e.message : '实例化失败';
      if (e instanceof ApiError && e.code === 'VALIDATION_FAILED') {
        const missing = (e.details as { missing?: unknown } | undefined)?.missing;
        if (Array.isArray(missing) && missing.length > 0) {
          msg = `${msg}:${missing.join('、')}`;
        }
      }
      toast.push(msg, { tone: 'error' });
    },
  });
};

export const useAgentDiagnostics = (memberId: string | undefined) =>
  useQuery({
    queryKey: qk.agentDiagnostics(memberId ?? '_'),
    queryFn: () => api.agentDiagnostics(memberId!),
    enabled: !!memberId,
  });

export const useHomeTree = (memberId: string | undefined) =>
  useQuery({
    queryKey: qk.homeTree(memberId ?? '_'),
    queryFn: () => api.homeTree(memberId!),
    enabled: !!memberId,
  });

// ---- M2 只读查询(stage2 消费)。写路径依赖 WS task.updated 实时回灌,不做乐观更新。
export const useTaskDetail = (taskId: string | undefined) =>
  useQuery({
    queryKey: qk.taskDetail(taskId ?? '_'),
    queryFn: () => api.taskDetail(taskId!),
    enabled: !!taskId,
  });

export const useTaskDiff = (taskId: string | undefined, enabled = true) =>
  useQuery({
    queryKey: qk.taskDiff(taskId ?? '_'),
    queryFn: () => api.taskDiff(taskId!),
    enabled: !!taskId && enabled,
    retry: false,
  });

export const useChannelFiles = (channelId: string | undefined) =>
  useQuery({
    queryKey: qk.channelFiles(channelId ?? '_'),
    queryFn: async () => (await api.channelFiles(channelId!)).items,
    enabled: !!channelId,
  });

// P2 画布快照(B §4.9):画布头 + 节点/边。写路径不做乐观更新,靠 canvas.* WS 反流(wsBridge)。
export const useCanvasSnapshot = (channelId: string | undefined) =>
  useQuery({
    queryKey: qk.canvas(channelId ?? '_'),
    queryFn: () => api.canvasSnapshot(channelId!),
    enabled: !!channelId,
  });

// ---- M6a Project 工作区级读写。绑定/解绑后 ProjectPublic.channel_ids 是唯一收敛读面。
export const useProjects = (enabled = true) =>
  useQuery({ queryKey: qk.projects(), queryFn: () => api.projects(), enabled });

function useProjectMutation<A>(call: (arg: A) => Promise<unknown>, success: string) {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: call,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.projects() });
      toast.push(success, { tone: 'success' });
    },
    onError: (e: unknown) =>
      toast.push(e instanceof ApiError ? e.message : 'Project 操作失败', { tone: 'error' }),
  });
}

export const useCreateProject = () =>
  useProjectMutation<ProjectCreate>((body) => api.createProject(body), 'Project 已创建');
export const usePatchProject = () =>
  useProjectMutation<{ projectId: string; patch: ProjectPatch }>(
    ({ projectId, patch }) => api.patchProject(projectId, patch),
    'Project 已更新',
  );
export const useDeleteProject = () =>
  useProjectMutation<string>((projectId) => api.deleteProject(projectId), 'Project 已删除');
export const useBindProject = () =>
  useProjectMutation<{ channelId: string; projectId: string }>(
    ({ channelId, projectId }) => api.bindProject(channelId, projectId),
    'Project 已绑定',
  );
export const useUnbindProject = () =>
  useProjectMutation<{ channelId: string; projectId: string }>(
    ({ channelId, projectId }) => api.unbindProject(channelId, projectId),
    'Project 已解除绑定',
  );

// ---- M6b 拆解提案（B §4.10）。提案卡渲染源，按 proposal_id GET；proposal.updated/draft.* WS
// 事件通过 wsBridge 按 proposal.id patch 本缓存（REST 是事实源，WS 载 ProposalPublic 整体替换）。
export const useProposal = (proposalId: string | undefined, enabled = true) =>
  useQuery({
    queryKey: qk.proposal(proposalId ?? '_'),
    queryFn: () => api.proposal(proposalId!),
    enabled: !!proposalId && enabled,
  });

/** 409 STALE_CONFIRM 的 latest 载荷（B §5 ①：`{proposal, baseline_version, baseline_hash}`）→ 刷新
 *  提案缓存 + 画布基线（草稿层/delta 面板"已刷新最新态，请重审"的收敛源）。返回刷新后的提案（供调用方
 *  据其新状态决定后续，如已转 rejected/failed）。latest 形状异常 → 原样返回 undefined 不动缓存。 */
export function refreshProposalFromLatest(
  qc: QueryClient,
  channelId: string,
  latest: unknown,
): ProposalPublic | undefined {
  if (!latest || typeof latest !== 'object') return undefined;
  const l = latest as {
    proposal?: ProposalPublic;
    baseline_version?: number;
    baseline_hash?: string;
  };
  const proposal = l.proposal;
  if (proposal && typeof proposal === 'object' && 'id' in proposal) {
    qc.setQueryData<ProposalPublic>(qk.proposal(proposal.id), proposal);
  }
  if (typeof l.baseline_version === 'number' && typeof l.baseline_hash === 'string') {
    const version = l.baseline_version;
    const hash = l.baseline_hash;
    qc.setQueryData<CanvasDetail>(qk.canvas(channelId), (prev) =>
      prev
        ? { ...prev, canvas: { ...prev.canvas, baseline_version: version, baseline_hash: hash } }
        : prev,
    );
  }
  return proposal;
}

// P13 创建 Agent（引导链 [创建 Orchestrator] 消费）。成功后失效成员列表让新 Agent 现身（无乐观
// 更新；MEMBER_CREATED WS 亦反流成员，invalidate 是兜底/收敛，REST 是事实源）。toast/就地错误由
// 调用方弹窗处理（NAME_TAKEN 等结构化错误上浮，同 ProjectSettingsSection 就地报错体例）。
export const useCreateAgent = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AgentCreate) => api.createAgent(body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.members() }),
  });
};

// ---- M7（B-M7-1）预览会话（B §13.1）。渲染源 = qk.preview(taskId) 缓存：面板经 useStartPreview
// 的 POST(ensure) 播种，WS preview.updated 后续 patch（daemon 状态流转）。usePreviewSession 只做缓存
// 订阅（enabled:false，不自动拉取——POST 是唯一写副作用推进方，GET 纯读不作自动源，同 usageByTask 范式）。
export const usePreviewSession = (taskId: string | undefined) =>
  useQuery({
    queryKey: qk.preview(taskId ?? '_'),
    queryFn: () => api.getPreview(taskId!),
    enabled: false,
    staleTime: Infinity,
    gcTime: Infinity,
  });

/** POST /tasks/{id}/preview = ensure + touch（幂等）。用于面板打开 + [重试]（failed 非活跃态自然走
 *  ensure 重建）。成功以响应体按 task_id 播种/替换 qk.preview 缓存；DAEMON_OFFLINE 等结构化错误弹 toast。
 *  心跳（面板打开期 60s 重发）不走本 mutation——见 PreviewPanel 内静默直调 api.startPreview（避免 60s
 *  toast 噪声，touch 是尽力而为）。 */
export const useStartPreview = () => {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (taskId: string) => api.startPreview(taskId),
    onSuccess: (session: PreviewSessionPublic) =>
      qc.setQueryData<PreviewSessionPublic>(qk.preview(session.task_id), session),
    onError: (e: unknown) =>
      toast.push(
        e instanceof ApiError && e.code === 'DAEMON_OFFLINE'
          ? 'daemon 离线，无法启动预览'
          : e instanceof Error ? e.message : '启动预览失败',
        { tone: 'error' },
      ),
  });
};

// ---- M7b 部署（B-M7-2 / B §13.2-13.4）。部署卡渲染源 = qk.deployment(id)（GET；WS
// deployment.created/updated 载 DeploymentPublic 按 id patch，daemon queued→running→success/failed
// 反流携 url/exit_code/token_summary）。触发 = 空体 POST（R8 全员含 Agent）；409 DEPLOY_IN_PROGRESS
// → 交互 §12 文案「上一次部署进行中」，422/503 据 code 组 toast。
export const useDeployment = (deploymentId: string | undefined) =>
  useQuery({
    queryKey: qk.deployment(deploymentId ?? '_'),
    queryFn: () => api.getDeployment(deploymentId!),
    enabled: !!deploymentId,
  });

export const useTriggerDeploy = () => {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (projectId: string) => api.createDeployment(projectId),
    // 成功以响应体（queued 部署）播种缓存；结果卡消息 + WS deployment.updated 后续接管展示。
    onSuccess: (dep: DeploymentPublic) =>
      qc.setQueryData<DeploymentPublic>(qk.deployment(dep.id), dep),
    onError: (e: unknown) => {
      const msg =
        e instanceof ApiError && e.code === 'DEPLOY_IN_PROGRESS'
          ? '上一次部署进行中'
          : e instanceof ApiError && e.code === 'DAEMON_OFFLINE'
            ? 'daemon 离线，无法部署'
            : e instanceof ApiError && e.code === 'VALIDATION_FAILED'
              ? '未配置部署命令，无法部署'
              : e instanceof Error ? e.message : '部署触发失败';
      toast.push(msg, { tone: 'error' });
    },
  });
};

// 部署日志累积（qk.deploymentLog(id)）：卡打开时 seedDeployLog 播种空缓存（让 WS deployment.log 有
// 锚点可追加），loadDeployLogPage 拉历史（首页无 after，「加载更多」按 nextAfter 续翻）；实时新行由
// wsBridge deployment.log 追加同一缓存。enabled:false + 缓存驱动（同 usePreviewSession/usageByTask 范式）。
// 注：仍在跑且已有大量历史的部署被重开时，历史翻页与实时流可能交叠——卡先拉首页再订阅，覆盖「新部署
// 空历史 / 已完成无实时」两主路径，交叠边缘态留 K9 实机核对（截图归 K9）。
export const useDeployLogState = (deploymentId: string | undefined) =>
  useQuery<DeployLogState>({
    queryKey: qk.deploymentLog(deploymentId ?? '_'),
    queryFn: () => EMPTY_DEPLOY_LOG,
    enabled: false,
    staleTime: Infinity,
    gcTime: Infinity,
  });

export function seedDeployLog(qc: QueryClient, deploymentId: string): void {
  if (qc.getQueryData(qk.deploymentLog(deploymentId)) === undefined) {
    qc.setQueryData<DeployLogState>(qk.deploymentLog(deploymentId), EMPTY_DEPLOY_LOG);
  }
}

export async function loadDeployLogPage(
  qc: QueryClient,
  deploymentId: string,
  after?: number,
): Promise<void> {
  const page = await api.deploymentLog(deploymentId, after);
  qc.setQueryData<DeployLogState>(qk.deploymentLog(deploymentId), (prev) =>
    mergeDeployLogPage(prev, page),
  );
}

// R-14：历史首页拉取失败兜底——标记 historyLoaded 并 flush pending 缓冲的 live 块（否则实时流
// 因历史永不 resolve 而卡在缓冲不显示）。DeploymentCard 打开日志的 loadDeployLogPage.catch 调用。
export function flushDeployLogPending(qc: QueryClient, deploymentId: string): void {
  qc.setQueryData<DeployLogState>(qk.deploymentLog(deploymentId), (prev) =>
    flushPendingDeployLog(prev),
  );
}

// 成本核算（GET /usage?level=&ref=）：画布页签汇总条（level=canvas ref=channel_id）与成本面共用。
// 永不折算货币（W7）；tasks_reporting 诚实标注覆盖率。ref 缺失（如频道无锚）则不拉取。
export const useUsage = (level: UsageLevel, ref: string | undefined, rollup = false) =>
  useQuery({
    queryKey: qk.usage(level, ref ?? '_'),
    queryFn: () => api.usage(level, ref!, rollup),
    enabled: !!ref,
  });

export const useRetryCanvasNode = (channelId: string) => {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (nodeId: string) => api.retryCanvasNode(nodeId),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.canvas(channelId) }),
    onError: (e: unknown) =>
      toast.push(e instanceof ApiError ? e.message : '系统节点重试失败', { tone: 'error' }),
  });
};

// ---- M4b HeldDraft(被扣草稿,B §4.14)。列表 GET 现行被扣(默认活动态 held/reevaluating);三键写路径不做乐观更新,
// 靠成功响应体 held_draft(或 409 HELD_DRAFT_RESOLVED 的 error.details.held_draft)按 id 就地替换缓存,
// WS held_draft.updated 亦会反流终态(wsBridge)——二者收敛到同一条 id 替换,重复应用无害。
export const useHeldDrafts = (channelId: string | undefined) =>
  useQuery({
    queryKey: qk.heldDrafts(channelId ?? '_'),
    queryFn: () => api.heldDrafts(channelId!),
    enabled: !!channelId,
  });

/** 按 id 就地替换该频道 heldDrafts 缓存(缺则不建——列表未加载时无锚点,放行)。 */
function patchHeldDraft(qc: QueryClient, channelId: string, draft: HeldDraftPublic) {
  qc.setQueryData<HeldDraftPublic[]>(qk.heldDrafts(channelId), (prev) => {
    if (!prev) return prev;
    const i = prev.findIndex((d) => d.id === draft.id);
    if (i < 0) return [...prev, draft];
    const next = prev.slice();
    next[i] = draft;
    return next;
  });
}

/** 409 HELD_DRAFT_RESOLVED 的 error.details 里窄化提取最新 held_draft(形状异常则 undefined)。 */
export function heldDraftFromResolvedError(e: unknown): HeldDraftPublic | undefined {
  if (!(e instanceof ApiError) || e.code !== 'HELD_DRAFT_RESOLVED') return undefined;
  const d = e.details as { held_draft?: unknown } | undefined;
  const hd = d?.held_draft;
  if (hd && typeof hd === 'object' && 'id' in hd) return hd as HeldDraftPublic;
  return undefined;
}

/** 三键共用工厂:成功以响应体 held_draft 替换缓存;409(已被终解)静默以 details.held_draft 收敛;
 *  其余错误(如 503 daemon 离线,discard/reevaluate 依赖 daemon)弹 error toast——否则失败静默、
 *  卡片停 held、按钮复用,与 ForceStart/流转等写路径的 toast 反馈不一致(评审 #3)。 */
function useHeldDraftAction<R extends { held_draft: HeldDraftPublic }>(
  channelId: string,
  call: (id: string) => Promise<R>,
) {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (id: string) => call(id),
    onSuccess: (res) => patchHeldDraft(qc, channelId, res.held_draft),
    onError: (e: unknown) => {
      const hd = heldDraftFromResolvedError(e); // 409:以最新态刷新卡片(不弹错)
      if (hd) {
        patchHeldDraft(qc, channelId, hd);
        return;
      }
      const msg = e instanceof ApiError && e.code === 'DAEMON_OFFLINE'
        ? 'Agent daemon 离线,无法完成该操作'
        : e instanceof Error ? e.message : '操作失败';
      toast.push(msg, { tone: 'error' });
    },
  });
}

export const useReleaseHeldDraft = (channelId: string) =>
  useHeldDraftAction(channelId, (id) => api.releaseHeldDraft(id));
export const useDiscardHeldDraft = (channelId: string) =>
  useHeldDraftAction(channelId, (id) => api.discardHeldDraft(id));
export const useReevaluateHeldDraft = (channelId: string) =>
  useHeldDraftAction(channelId, (id) => api.reevaluateHeldDraft(id));

// 'all' 单拉,tab 过滤归客户端(挂账批2 简化:原三档缓存 = 双请求 + wsBridge 逐档 patch)。
export const useActivity = () =>
  useQuery({
    queryKey: qk.activity('all'),
    queryFn: async () => (await api.activity('all')).items,
  });

// usageByTask 无 REST 源:初值空,由 token_usage.reported 累加(wsBridge)。
export const useUsageByTask = () =>
  useQuery({
    queryKey: qk.usageByTask(),
    queryFn: async (): Promise<Record<string, number>> => ({}),
    staleTime: Infinity,
    gcTime: Infinity,
  });

// ---- 派生 selector(纯函数,组件与桥接复用)
export const channelsOf = (snap?: ChannelsSnapshot): ChannelPublic[] =>
  (snap?.items as ChannelPublic[]) ?? [];

export const readPositionsMap = (snap?: ChannelsSnapshot): Record<string, ReadPositionPublic> =>
  Object.fromEntries(
    ((snap?.read_positions as ReadPositionPublic[]) ?? []).map((r) => [r.channel_id, r]),
  );

export const presenceMap = (items?: PresenceEntry[]): Record<string, PresenceEntry> =>
  Object.fromEntries((items ?? []).map((p) => [p.member_id, p]));

export const memberMap = (members?: MemberPublic[]): Record<string, MemberPublic> =>
  Object.fromEntries((members ?? []).map((m) => [m.id, m]));

// 契约 C §4 步骤 1:重连后按频道做消息增量 + 实体快照重同步。
// M1 基座:直接 invalidate 触发 refetch(REST 是事实源,铁律 1);增量 ?after= 的窗口优化留 B2。
export function resyncAll(qc: QueryClient) {
  return qc.invalidateQueries();
}

export function useResync() {
  const qc = useQueryClient();
  return () => resyncAll(qc);
}

export type { WorkspacePublic, MemberPublic, ChannelPublic, MessagePublic, TaskPublic, PresenceEntry };
