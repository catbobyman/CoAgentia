// M6b WS 副作用桥（缓存之外的 store 联动）：wsBridge 保持纯缓存 patch，这里承载需要读/写 zustand 的
// 两件事——① 草稿层 rev 替换（draft.superseded → 新 draft.presented 时把激活草稿切到新提案 id）；
// ② 落地事件 → store 信号（<LandingToaster> 观察后弹全局 toast）。二者都从 useWsSync 在 applyEnvelope
// 之后调用（那里已在 ToastProvider 之外，无法直接 toast，故走 store 桥）。纯函数依赖注入 store 存取以便单测。
import type { QueryClient } from '@tanstack/react-query';

import type { Envelope, ProposalPublic } from '@coagentia/contracts-ts';

import { qk } from '../lib/queryKeys';
import type { LandingSignal } from '../lib/store';

export interface ActiveDraftStore {
  getActiveDraft: (channelId: string) => string | null | undefined;
  setActiveDraft: (channelId: string, proposalId: string | null) => void;
}

/**
 * rev 替换（拆解设计 §8 / 交互 §6.6）：对话修正 → 旧提案 superseded + 新 draft.presented（新提案 id、
 * revision+1）。若某频道正激活着草稿层且新提案 supersede 它（同 source 任务）→ 把 activeDraft 切到新
 * 提案 id，草稿层随之整体替换（rev 变更 toast 由 DraftLayer 就地弹）。仅对 kind=full 生效。
 */
export function reconcileActiveDraft(env: Envelope, qc: QueryClient, store: ActiveDraftStore): void {
  if (env.type !== 'draft.presented') return;
  const proposal = (env.data as { proposal?: ProposalPublic }).proposal;
  if (!proposal || proposal.kind === 'delta') return;
  const active = store.getActiveDraft(proposal.channel_id);
  if (!active || active === proposal.id) return;
  // 仅当新提案与当前激活草稿同 source 任务（= rev 替换）才切换；缓存缺旧提案时保守放行（单频道单草稿）。
  const old = qc.getQueryData<ProposalPublic>(qk.proposal(active));
  if (old && old.source_task_id !== proposal.source_task_id) return;
  store.setActiveDraft(proposal.channel_id, proposal.id);
}

/** landing.* 事件 → 信号 kind（供 useWsSync 写 store，<LandingToaster> 弹 toast）；非落地事件返回 null。 */
export function landingSignalKind(env: Envelope): LandingSignal['kind'] | null {
  switch (env.type) {
    case 'landing.started':
      return 'started';
    case 'landing.completed':
      return 'completed';
    case 'landing.fail_closed':
      return 'fail_closed';
    default:
      return null;
  }
}
