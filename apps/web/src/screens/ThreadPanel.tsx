// P5 任务线程面板(420px):任务牌头 §2.2 + 契约折叠卡 §4.6 + 线程回复流(复用 MessageFlow)+ 状态操作条。
// 由类型化深链 ?thread= 驱动(在 ChannelChatScreen 内消费,非顶层路由)。
// M2 接真:opsbar 的 claim/unclaim/状态流转打真端点;契约/usage 用 useTaskDetail 真数据(成功靠 WS task.updated 回灌,无乐观更新)。
import { useState } from 'react';
import { ChevronDown, X } from 'lucide-react';

import type { MemberPublic, PresenceEntry, TaskPublic, TaskStatus } from '@coagentia/contracts-ts';
import { TASK_TRANSITIONS, UNCLAIMABLE_STATUSES } from '@coagentia/contracts-ts';

import { STATUS_VAR, STATUS_WORD } from '../lib/uiMaps';
import { useTaskDetail, useThread } from '../data/queries';
import { api, ApiError } from '../api';
import { useToast } from '../components/Toast';
import { Avatar } from '../components/Avatar';
import { MessageFlow } from '../components/MessageFlow';
import { Composer } from '../components/Composer';

export function ThreadPanel({
  task, rootMessageId, memberById, memberNames, meName, meId, presenceOf, usage, onClose, onSend,
}: {
  task?: TaskPublic;
  rootMessageId: string;
  memberById: Record<string, MemberPublic>;
  memberNames: string[];
  meName: string;
  meId?: string;
  presenceOf: (memberId: string) => PresenceEntry | undefined;
  usage?: number;
  onClose: () => void;
  onSend: (body: string) => void;
}) {
  const threadQ = useThread(rootMessageId);
  const detailQ = useTaskDetail(task?.id);
  const toast = useToast();
  const [stOpen, setStOpen] = useState(false);

  const items = threadQ.data ?? [];
  // 回复流 = 线程条目去掉 root(root 内容已在牌头呈现)。
  const replies = items.filter((m) => m.id !== rootMessageId);

  const status = task?.status ?? 'todo';
  const owner = task?.owner_member_id ? memberById[task.owner_member_id] : undefined;
  const creator = task?.created_by_member_id ? memberById[task.created_by_member_id] : undefined;
  const created = task?.created_at?.slice(11, 16);

  // 真契约 / usage(契约 B §9.8):contracts M3 前恒空 → 契约卡收起;usage 优先取真 detail 聚合。
  const detail = detailQ.data;
  const contracts = detail?.contracts ?? [];
  const u = detail?.usage;
  const usageTotal = u ? (u.input_tokens ?? 0) + (u.output_tokens ?? 0) : usage;

  // 合法目标态(纪律 7:单一事实源,消费生成的 TASK_TRANSITIONS,前端不另写边表)。
  const targets: TaskStatus[] = task ? (TASK_TRANSITIONS[status as TaskStatus] ?? []) : [];

  // claim 冲突 → 用 details.current_owner 映射成员名给 toast;非法流转 → toast。
  const onWriteError = (e: unknown) => {
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
  // 终态(done/closed)不可认领(纪律 7 消费生成的 claim 语义门)——认领钮置灰,避免点击必吃 422。
  const claimable = !UNCLAIMABLE_STATUSES.includes(status as TaskStatus);

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
    void api.setTaskStatus(task.id, to).catch(onWriteError);
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
        {contracts.length > 0 && (
          <div className="row3">
            <span className="planentry">TaskPlan · AC×{contracts.length}<span className="ar">▾</span></span>
            <span className="handoff">TaskHandoff 待提交</span>
          </div>
        )}
      </header>

      {/* [2] 契约折叠卡:M3 前 contracts 恒空 → 收起,呈现占位 */}
      <section className="contract">
        {contracts.length > 0 ? (
          <>
            <div className="chd"><b>TaskPlan</b><span className="m">契约 ×{contracts.length}</span></div>
            {contracts.map((c) => (
              <div className="acrow" key={c.id}>
                <span className="acid">{c.kind}</span>
                <span className="acst">v{c.version}{c.revision != null ? ` · rev ${c.revision}` : ''}</span>
                <span className="vchip">by {memberById[c.created_by_member_id]?.name ?? '—'}</span>
              </div>
            ))}
          </>
        ) : (
          <div className="goal"><span className="lb">Contract</span>暂无契约(TaskPlan/TaskHandoff 于 M3 接入)。</div>
        )}
      </section>

      {/* [3] 线程回复流(复用 MessageFlow) */}
      <MessageFlow
        messages={replies}
        memberById={memberById}
        memberNames={memberNames}
        meName={meName}
        presenceOf={presenceOf}
        taskByRoot={{}}
        usageByTask={{}}
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
    </aside>
  );
}
