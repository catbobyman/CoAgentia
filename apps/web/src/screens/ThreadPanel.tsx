// P5 任务线程面板(420px):任务牌头 §2.2 + 契约折叠卡 §4.6 + 线程回复流(复用 MessageFlow)+ 状态操作条。
// 由类型化深链 ?thread= 驱动(在 ChannelChatScreen 内消费,非顶层路由)。
// M2 接真:opsbar 的 claim/unclaim/状态流转打真端点;契约/usage 用 useTaskDetail 真数据(成功靠 WS task.updated 回灌,无乐观更新)。
import { useState } from 'react';
import { ArrowUp, ChevronDown, CircleAlert, GitBranch, ListTree, Sparkles, X } from 'lucide-react';

import type {
  ContractKind,
  HeldDraftPublic,
  MemberPublic,
  PresenceEntry,
  TaskContractPublic,
  TaskHandoffBody,
  TaskPlanBody,
  TaskPublic,
  TaskStatus,
  ReviewVerdict,
} from '@coagentia/contracts-ts';
import { TASK_TRANSITIONS, UNCLAIMABLE_STATUSES } from '@coagentia/contracts-ts';

import { STATUS_VAR, STATUS_WORD } from '../lib/uiMaps';
import { fmtTime } from '../lib/time';
import { useTaskDetail, useThread } from '../data/queries';
import { api, ApiError } from '../api';
import { useToast } from '../components/Toast';
import { Avatar } from '../components/Avatar';
import { DecomposeGuideModals, useDecompose } from '../components/DecomposeGuide';
import { MessageFlow } from '../components/MessageFlow';
import { Composer } from '../components/Composer';
import { HeldDraftList } from './HeldDraftCard';
import { DiffCard } from '../components/DiffCard';
import '../components/diff-card.css';

// 契约 kind → 中文短名(§4.6);loop_contract 归 Reminder 上岗流程,不在任务线程契约卡出现。
const CONTRACT_KIND_LABEL: Record<ContractKind, string> = {
  task_plan: 'TaskPlan',
  task_handoff: 'TaskHandoff',
  loop_contract: 'LoopContract',
};
// "让 @Agent 起草"菜单可选 kind——任务域只出 TaskPlan/TaskHandoff(纪律:LoopContract 走 Reminder)。
const DRAFT_KINDS: ContractKind[] = ['task_plan', 'task_handoff'];

const VERDICT_LABEL: Record<ReviewVerdict, string> = {
  pass: '通过', downgrade: '降级通过', send_back: '退回重做', needs_human: '需要人类',
};

/** 按 kind 拆活动契约 / 历史版本(revision 修订链——superseded_at≠null 即历史)。 */
function splitContracts(contracts: TaskContractPublic[]) {
  const active = contracts.filter((c) => c.superseded_at == null);
  const historical = contracts.filter((c) => c.superseded_at != null);
  return {
    activePlan: active.find((c) => c.kind === 'task_plan'),
    activeHandoff: active.find((c) => c.kind === 'task_handoff'),
    historical,
  };
}

/** T7 拒绝(HANDOFF_INCOMPLETE)时 error.details.missing 的窄化提取(交互 §5.4)。
 * 兜底:即便 details.missing 缺失/形状异常也返回非空数组,保证就地横幅始终有反馈(不静默吞)。 */
export function missingFromHandoffError(e: unknown): string[] | undefined {
  if (!(e instanceof ApiError) || e.code !== 'HANDOFF_INCOMPLETE') return undefined;
  const d = e.details as { missing?: unknown } | undefined;
  const missing = Array.isArray(d?.missing)
    ? (d!.missing as unknown[]).filter((x): x is string => typeof x === 'string')
    : [];
  return missing.length > 0 ? missing : ['交接材料不完整'];
}

/** TaskPlan 契约卡:goal + AC 列表(§4.6 P5 设计稿),可选 defaults/out_of_scope 折叠。 */
export function TaskPlanCard({
  contract, memberById,
}: {
  contract: TaskContractPublic;
  memberById: Record<string, MemberPublic>;
}) {
  const plan = contract.body as TaskPlanBody;
  const rev = contract.revision;
  // 防御:body 形状虽由后端提交期校验,但版本漂移/脏行时属 unknown,守空避免整个面板崩溃。
  const acs = plan.acceptance_criteria ?? [];
  return (
    <div data-comment-anchor="contract-card-plan">
      <div className="chd">
        <b>TaskPlan</b>
        {rev != null && rev > 1 && <span className="vchip">rev {rev}</span>}
        <span className="m">
          AC×{acs.length} · by {memberById[contract.created_by_member_id]?.name ?? '—'} · {fmtTime(contract.created_at)}
        </span>
      </div>
      <div className="goal"><span className="lb">Goal</span>{plan.goal}</div>
      {acs.length > 0 && <span className="aclb">Acceptance Criteria</span>}
      {acs.map((ac) => (
        <div className="acrow" key={ac.id}>
          <span className="acid">{ac.id}</span>
          <span className="acst">
            {ac.statement}
            {ac.verify_ref && <> · <span className="cmd">{ac.verify_ref}</span></>}
          </span>
          <span className="vchip">{ac.verify_by}</span>
        </div>
      ))}
      {(!!plan.defaults_decided?.length || !!plan.out_of_scope?.length) && (
        <details>
          <summary className="more">默认值 / 超纲范围</summary>
          {!!plan.defaults_decided?.length && (
            <div className="goal"><span className="lb">Defaults Decided</span>{plan.defaults_decided.join('; ')}</div>
          )}
          {!!plan.out_of_scope?.length && (
            <div className="goal"><span className="lb">Out of Scope</span>{plan.out_of_scope.join('; ')}</div>
          )}
        </details>
      )}
    </div>
  );
}

/** TaskHandoff 契约卡:deliverables/evidence 列表 + verify_plan/open_risks(§4.6)。 */
export function TaskHandoffCard({
  contract, memberById,
}: {
  contract: TaskContractPublic;
  memberById: Record<string, MemberPublic>;
}) {
  const handoff = contract.body as TaskHandoffBody;
  const rev = contract.revision;
  const fromName = memberById[handoff.from_member]?.name ?? handoff.from_member;
  const toName = memberById[handoff.to_member]?.name ?? handoff.to_member;
  return (
    <div data-comment-anchor="contract-card-handoff">
      <div className="chd">
        <b>TaskHandoff</b>
        {rev != null && rev > 1 && <span className="vchip">rev {rev}</span>}
        <span className="m">
          D×{handoff.deliverables?.length ?? 0} · E×{handoff.evidence?.length ?? 0} · by {memberById[contract.created_by_member_id]?.name ?? '—'} · {fmtTime(contract.created_at)}
        </span>
        {handoff.review_verdict && (
          <span className="verdict-badge" data-verdict={handoff.review_verdict}>
            {VERDICT_LABEL[handoff.review_verdict]}
          </span>
        )}
      </div>
      {handoff.review_verdict === 'needs_human' && (
        <div className="needs-human-banner" role="alert"><CircleAlert />需要人类介入后再继续交付。</div>
      )}
      <div className="goal"><span className="lb">From → To</span>{fromName} → {toName}</div>
      {!!handoff.deliverables?.length && (
        <>
          <span className="aclb">Deliverables</span>
          {handoff.deliverables.map((d, i) => (
            <div className="acrow" key={`d-${i}-${d.path}`}>
              <span className="acid">D-{String(i + 1).padStart(2, '0')}</span>
              <span className="acst"><span className="cmd">{d.path}</span></span>
              <span className="vchip">{d.kind}</span>
            </div>
          ))}
        </>
      )}
      {!!handoff.evidence?.length && (
        <>
          <span className="aclb">Evidence</span>
          {handoff.evidence.map((ev, i) => (
            <div className="acrow" key={`e-${i}-${ev.ref}`}>
              <span className="acid">E-{String(i + 1).padStart(2, '0')}</span>
              <span className="acst">{ev.conclusion} · <span className="cmd">{ev.ref}</span></span>
              <span className="vchip">{ev.type}</span>
            </div>
          ))}
        </>
      )}
      <div className="goal"><span className="lb">Verify Plan</span>{handoff.verify_plan}</div>
      {!!handoff.open_risks?.length && (
        <div className="goal"><span className="lb">Open Risks</span>{handoff.open_risks.join('; ')}</div>
      )}
    </div>
  );
}

export function ThreadPanel({
  task, rootMessageId, channelId, memberById, memberNames, meName, meId, presenceOf, usage,
  heldDrafts, canResolve, onLocateMessage, locateId, onLocateDone, onClose, onSend,
  onReviewProposal,
}: {
  task?: TaskPublic;
  rootMessageId: string;
  channelId: string;
  memberById: Record<string, MemberPublic>;
  memberNames: string[];
  meName: string;
  meId?: string;
  presenceOf: (memberId: string) => PresenceEntry | undefined;
  usage?: number;
  // 被扣草稿(M4b):频道全量,HeldDraftList 内按 thread_root_id === rootMessageId 归位到本线程。
  heldDrafts?: HeldDraftPublic[];
  canResolve?: boolean;
  onLocateMessage?: (messageId: string) => void;
  locateId?: string;
  onLocateDone?: () => void;
  onClose: () => void;
  onSend: (body: string) => void;
  /** M6b 提案卡「在画布中审阅」→ 切画布页签(由会话屏传入 setSearch 通道)。 */
  onReviewProposal?: () => void;
}) {
  const threadQ = useThread(rootMessageId);
  const detailQ = useTaskDetail(task?.id);
  const toast = useToast();
  const [stOpen, setStOpen] = useState(false);
  const [draftOpen, setDraftOpen] = useState(false);
  // T7(HANDOFF_INCOMPLETE)就地提示:缺失字段列表,非通用 toast(交互 §5.4)。
  const [handoffMissing, setHandoffMissing] = useState<string[] | undefined>(undefined);

  const items = threadQ.data ?? [];
  // 回复流 = 线程条目去掉 root(root 内容已在牌头呈现)。
  const replies = items.filter((m) => m.id !== rootMessageId);

  const status = task?.status ?? 'todo';
  const owner = task?.owner_member_id ? memberById[task.owner_member_id] : undefined;
  const creator = task?.created_by_member_id ? memberById[task.created_by_member_id] : undefined;
  const created = task?.created_at ? fmtTime(task.created_at) : undefined;

  // 真契约 / usage(契约 B §9.8):usage 优先取真 detail 聚合。
  const detail = detailQ.data;
  const contracts = detail?.contracts ?? [];
  const { activePlan, activeHandoff, historical } = splitContracts(contracts);
  const u = detail?.usage;
  const usageTotal = u ? (u.input_tokens ?? 0) + (u.output_tokens ?? 0) : usage;

  // "让 @Agent 起草"候选:memberById 里 kind=agent 的成员(P6/P5 共用同一份成员数据,零新增拉取)。
  const agents = Object.values(memberById).filter((m) => m.kind === 'agent');

  // 合法目标态(纪律 7:单一事实源,消费生成的 TASK_TRANSITIONS,前端不另写边表)。
  const targets: TaskStatus[] = task ? (TASK_TRANSITIONS[status as TaskStatus] ?? []) : [];

  // claim 冲突 → 用 details.current_owner 映射成员名给 toast;非法流转 → toast;
  // T7(HANDOFF_INCOMPLETE)→ 就地提示缺失字段(不是一闪而过的 toast——交互 §5.4)。
  const onWriteError = (e: unknown) => {
    const missing = missingFromHandoffError(e);
    if (missing !== undefined) {
      setHandoffMissing(missing);
      return;
    }
    if (e instanceof ApiError) {
      if (e.code === 'CLAIM_RACE') {
        const d = e.details as { current_owner?: string } | undefined;
        const ownerName = d?.current_owner ? memberById[d.current_owner]?.name : undefined;
        toast.push(ownerName ? `已被 @${ownerName} 认领` : '任务已被他人认领', { tone: 'error' });
        return;
      }
      if (e.code === 'TASK_TRANSITION_INVALID') {
        toast.push('非法流转:目标状态不被当前状态允许', { tone: 'error' });
        return;
      }
      toast.push(e.message, { tone: 'error' });
      return;
    }
    toast.push('操作失败', { tone: 'error' });
  };

  const iAmOwner = !!meId && task?.owner_member_id === meId;
  // 认领钮置灰的两个必失败路径(M2 二轮 review):终态(422 语义门)+ 已被他人认领(409 CLAIM_RACE)。
  const claimable =
    !UNCLAIMABLE_STATUSES.includes(status as TaskStatus) && !task?.owner_member_id;

  const doClaim = () => {
    if (!task) return;
    void api.claimTask(task.id).catch(onWriteError); // 成功回灌靠 WS task.updated
  };
  const doUnclaim = () => {
    if (!task) return;
    void api.unclaimTask(task.id).catch(onWriteError);
  };
  const doStatus = (to: TaskStatus) => {
    if (!task) return;
    setStOpen(false);
    setHandoffMissing(undefined); // 重新尝试:清掉上一次 T7 的就地提示
    void api.setTaskStatus(task.id, to).catch(onWriteError);
  };

  // L1→L2 升格(M3 P-2;PATCH level=l2)。单向放行,l2→l1 由 server 拒 422 TASK_TRANSITION_INVALID
  // (rule=D1,onWriteError 兜底)。成功回灌靠 WS task.updated;画布经 POST nodes 建的任务直接是 L2,
  // 此按钮服务「既有 L1 任务补契约升格」这一辅路(无「引用既有任务为节点」端点)。
  const canPromote = !!task && task.level !== 'l2';
  const doPromote = () => {
    if (!task) return;
    setHandoffMissing(undefined);
    void api.promoteTask(task.id).catch(onWriteError);
  };

  // M6b 拆解入口 T2（拆解设计 §4）：任务卡「拆解」动作——该任务即 source，POST {task_id}。
  // 202 → toast（提案将出现在本线程）；409 NO_ORCHESTRATOR / 503 DAEMON_OFFLINE 由
  // useDecompose 分派引导态（DecomposeGuideModals 渲染，交互 §6.8）。
  const decomposeH = useDecompose(channelId, () => {
    toast.push('拆解已发起，提案将出现在本线程', { tone: 'success' });
  });
  const doDecompose = () => {
    if (!task) return;
    void decomposeH.request({ task_id: task.id });
  };

  // "让 @Agent 起草"(契约 D 定向直投唤醒);202 成功 toast,daemon 离线(503)单独文案。
  const doRequestDraft = (agentMemberId: string, kind: ContractKind) => {
    if (!task) return;
    setDraftOpen(false);
    const agentName = memberById[agentMemberId]?.name ?? agentMemberId;
    void api.requestContractDraft(task.id, { agent_member_id: agentMemberId, kind })
      .then(() => {
        toast.push(`已请求 @${agentName} 起草 ${CONTRACT_KIND_LABEL[kind]}…`, { tone: 'success' });
      })
      .catch((e: unknown) => {
        if (e instanceof ApiError && e.code === 'DAEMON_OFFLINE') {
          toast.push(`@${agentName} 的 daemon 离线,起草请求未送达`, { tone: 'error' });
          return;
        }
        onWriteError(e);
      });
  };

  return (
    <aside className="panel" data-screen-label={`任务线程 #${task?.number ?? ''}`}>
      {/* [1] 任务牌头 */}
      <header className="phead">
        <div className="row1">
          <span className="no">#{task?.number ?? '—'}</span>
          <span className="ttl">{task?.title ?? '线程'}</span>
          <span className="icobtn close" aria-label="关闭面板" onClick={onClose}><X /></span>
        </div>
        <div className="row2">
          <span className="stchip">
            <i style={{ background: `var(${STATUS_VAR[status]})` }} />{STATUS_WORD[status]}
          </span>
          {owner && (
            <span className="who"><Avatar name={owner.name} presence={presenceOf(owner.id)} size="nav" />{owner.name}</span>
          )}
          {creator && <span className="meta">by {creator.name}{created ? ` · ${created}` : ''}</span>}
          {usageTotal !== undefined && usageTotal > 0 && (
            <span className="tokbadge">{(usageTotal / 1000).toFixed(1)}k tok</span>
          )}
        </div>
        {(activePlan || activeHandoff) && (
          <div className="row3">
            {activePlan && (
              <span className="planentry">
                TaskPlan · AC×{(activePlan.body as TaskPlanBody).acceptance_criteria.length}
                <span className="ar">▾</span>
              </span>
            )}
            {activeHandoff ? (
              <span className="handoff">
                TaskHandoff · D×{(activeHandoff.body as TaskHandoffBody).deliverables?.length ?? 0} · E×{(activeHandoff.body as TaskHandoffBody).evidence?.length ?? 0}
              </span>
            ) : (
              <span className="handoff">TaskHandoff 待提交</span>
            )}
          </div>
        )}
      </header>

      {task && (task.writes_code || detail?.worktree) && (
        <section className="delivery-card" aria-label="交付工作树">
          <div className="delivery-head">
            <GitBranch />
            <span className="branch">{detail?.worktree?.branch ?? '等待 worktree 派生'}</span>
            <span className={`delivery-status ${detail?.worktree?.status ?? 'pending'}`}>
              {detail?.worktree?.status ?? 'pending'}
            </span>
          </div>
          <DiffCard taskId={task.id} />
        </section>
      )}

      {/* [2] 契约折叠卡:活动 TaskPlan/TaskHandoff 各一张 + 历史修订折叠 + 起草入口 */}
      <section className="contract">
        {contracts.length > 0 ? (
          <>
            {activePlan && <TaskPlanCard contract={activePlan} memberById={memberById} />}
            {activeHandoff && <TaskHandoffCard contract={activeHandoff} memberById={memberById} />}
            {historical.length > 0 && (
              <details>
                <summary className="more">历史版本 ×{historical.length}</summary>
                {historical.map((c) => (
                  <div className="acrow" key={c.id}>
                    <span className="acid">{CONTRACT_KIND_LABEL[c.kind]}</span>
                    <span className="acst">v{c.version}{c.revision != null && c.revision > 1 ? ` · rev ${c.revision}` : ''}</span>
                    <span className="vchip">superseded {c.superseded_at ? fmtTime(c.superseded_at) : ''}</span>
                  </div>
                ))}
              </details>
            )}
          </>
        ) : (
          <div className="goal">
            <span className="lb">Contract</span>暂无契约(可让 @Agent 起草 TaskPlan/TaskHandoff)。
          </div>
        )}

        <div className="draftrow dropwrap">
          <button className="draft-ai" onClick={() => setDraftOpen((v) => !v)} disabled={!task}>
            <Sparkles />让 @Agent 起草
          </button>
          {canPromote && (
            <button
              className="draft-ai"
              data-testid="promote-l2"
              style={{ marginLeft: 8 }}
              onClick={doPromote}
              disabled={!task}
              title="升格为 L2:进入正式立项(契约需齐备)"
            >
              <ArrowUp />升格为 L2
            </button>
          )}
          <button
            className="draft-ai"
            data-testid="task-decompose"
            style={{ marginLeft: 8 }}
            onClick={doDecompose}
            disabled={!task || decomposeH.busy}
            title="以本任务为 source 发起拆解(@Orchestrator 产出任务 DAG 提案)"
          >
            <ListTree />拆解
          </button>
          {draftOpen && (
            <div className="drop" style={{ top: 32, bottom: 'auto', left: 0, right: 'auto' }}>
              {agents.length === 0 && <div className="it" style={{ color: 'var(--text-muted)' }}>暂无可用 Agent</div>}
              {agents.flatMap((a) =>
                DRAFT_KINDS.map((k) => (
                  <div className="it" key={`${a.id}:${k}`} onClick={() => doRequestDraft(a.id, k)}>
                    @{a.name} · {CONTRACT_KIND_LABEL[k]}
                  </div>
                )),
              )}
            </div>
          )}
        </div>

        {handoffMissing && handoffMissing.length > 0 && (
          <div className="hmiss" role="alert">
            <CircleAlert />缺少:{handoffMissing.join(' / ')}
          </div>
        )}
      </section>

      {/* [3] 线程回复流(复用 MessageFlow;附件卡 = 消息读面派生 files,与主流同源) */}
      <MessageFlow
        messages={replies}
        memberById={memberById}
        memberNames={memberNames}
        meName={meName}
        presenceOf={presenceOf}
        taskByRoot={{}}
        usageByTask={{}}
        locateId={locateId}
        onLocateDone={onLocateDone}
        onReviewProposal={onReviewProposal}
      />

      {/* [3b] 本线程内被扣草稿(thread_root_id === rootMessageId)——主流的由会话屏渲染。 */}
      <HeldDraftList
        drafts={heldDrafts ?? []}
        channelId={channelId}
        threadRootId={rootMessageId}
        memberById={memberById}
        canResolve={canResolve ?? false}
        onLocateMessage={onLocateMessage}
      />

      {/* [4] 状态操作条:claim/unclaim 三态 + 合法目标态流转下拉 */}
      <div className="opsbar">
        <span className="lb">Status</span>
        {iAmOwner ? (
          <button className="btn btn-ghost" onClick={doUnclaim} disabled={!task}>unclaim</button>
        ) : (
          <button className="btn btn-ghost" onClick={doClaim} disabled={!task || !claimable}>认领</button>
        )}
        <div className="dropwrap">
          <button className="stdrop" onClick={() => setStOpen((v) => !v)} disabled={!task || targets.length === 0}>
            <i className="sq" style={{ background: `var(${STATUS_VAR[status]})` }} />
            {STATUS_WORD[status]}<ChevronDown />
          </button>
          {stOpen && targets.length > 0 && (
            <div className="drop" style={{ bottom: 36, top: 'auto', right: 0 }}>
              {targets.map((to) => (
                <div className="it" key={to} onClick={() => doStatus(to)}>
                  <i className="sq" style={{ background: `var(${STATUS_VAR[to]})`, width: 8, height: 8, display: 'inline-block', marginRight: 8 }} />
                  {STATUS_WORD[to]}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* [5] 面板编辑器(无 As Task) */}
      <Composer channelName="thread" variant="panel" hideAsTask onSend={(body) => onSend(body)} />

      {/* M6b 拆解引导链（T2 路径的 409/503 引导；创建完成 toast 引导重新点击「拆解」）。 */}
      <DecomposeGuideModals
        guide={decomposeH.guide}
        channelId={channelId}
        onClose={() => decomposeH.setGuide(null)}
        onOrchestratorCreated={() =>
          toast.push('@Orchestrator 已创建并拉入频道，可重新点击「拆解」', { tone: 'success' })}
      />
    </aside>
  );
}
