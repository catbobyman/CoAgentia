// M4b 被扣草稿卡(CardKind.HELD_DRAFT,交互 §4.14 / G1–G6)。
// Agent 主体发送被 freshness 门(D4)扣住时,以卡片形态落在原目标位(主流 / 线程)。
// 结构:草稿全文(长文折叠)+ 结构化被扣原因(未读清单可点跳转 + 截断计数)+ 本地读秒倒计时
// (由 next_reeval_at 每秒计算,不推帧、不依赖 WS)+ 三键(release/discard/reevaluate,仅人类可见)
// + 升级横条(escalated_at 非空)+ 终态折叠回执(released/discarded/resolved 隐藏三键)。
import { useEffect, useState } from 'react';
import { ArrowUpRight, CircleAlert, RotateCcw, Send, Trash2 } from 'lucide-react';

import type { HeldDraftPublic, HeldDraftStatus, MemberPublic } from '@coagentia/contracts-ts';

import {
  useDiscardHeldDraft, useReevaluateHeldDraft, useReleaseHeldDraft,
} from '../data/queries';
import { fmtTime } from '../lib/time';
import './held-draft-card.css';

// 值域语义单一事实源:终态集合(隐藏三键、折叠回执)——契约 HeldDraftStatus 的终态子集。
const TERMINAL: readonly HeldDraftStatus[] = ['released', 'discarded', 'resolved'];
const isTerminal = (s: HeldDraftStatus): boolean => TERMINAL.includes(s);

// 终态回执文案(resolution 优先,退化到 status)。
const RESULT_WORD: Record<string, string> = {
  released: '已放行 · 草稿已作为消息发出',
  discarded: '已丢弃 · 草稿不再发送',
  reevaluated: '已重评估',
  resolved: '已解决',
};

// 草稿全文折叠阈值(字符):超过则默认折叠、给「展开/收起」。
const FOLD_LEN = 280;

/** 每秒读秒:返回 target 距今剩余整秒(≤0 归 0)。本地 setInterval,不推帧、不依赖 WS。 */
function useRemainingSeconds(targetIso: string): number {
  const compute = () => Math.max(0, Math.floor((new Date(targetIso).getTime() - Date.now()) / 1000));
  const [remaining, setRemaining] = useState(compute);
  useEffect(() => {
    setRemaining(compute()); // target 变化(WS 反流新 next_reeval_at)时立即重算
    const id = setInterval(() => setRemaining(compute()), 1000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetIso]);
  return remaining;
}

/** 秒 → 倒计时显示:≥1h 用 {h}h{mm}m,否则 {m}:{ss}。 */
function fmtCountdown(sec: number): string {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h > 0) return `${h}h${String(m).padStart(2, '0')}m`;
  return `${m}:${String(s).padStart(2, '0')}`;
}

export function HeldDraftCard({
  draft, channelId, memberById, canResolve, onLocateMessage,
}: {
  draft: HeldDraftPublic;
  channelId: string;
  memberById: Record<string, MemberPublic>;
  /** 仅人类可见三键(web = 人类 owner 视图,恒真;显式传入以贴契约「仅人类」语义并便于测试)。 */
  canResolve: boolean;
  /** 点未读清单某条 → 跳转定位到该消息(主流/线程共用同一 locate 通道)。 */
  onLocateMessage?: (messageId: string) => void;
}) {
  const releaseM = useReleaseHeldDraft(channelId);
  const discardM = useDiscardHeldDraft(channelId);
  const reevalM = useReevaluateHeldDraft(channelId);
  const [expanded, setExpanded] = useState(false);

  const status = draft.status ?? 'held';
  const terminal = isTerminal(status);
  const remaining = useRemainingSeconds(draft.next_reeval_at);
  // 倒计时到点(或 status 已进 reevaluating)→ 显示「重评估中」;终态不显示倒计时。
  const reevaluating = status === 'reevaluating' || (status === 'held' && remaining <= 0);

  const agentName = memberById[draft.agent_member_id]?.name ?? draft.agent_member_id;
  const body = draft.draft_body ?? '';
  const long = body.length > FOLD_LEN;
  const shownBody = long && !expanded ? `${body.slice(0, FOLD_LEN)}…` : body;

  const reasons = draft.reasons;
  const shownIds = reasons?.unread_message_ids ?? [];
  const totalUnread = reasons?.total_unread ?? shownIds.length;
  const overflow = Math.max(0, totalUnread - shownIds.length);

  const pending = releaseM.isPending || discardM.isPending || reevalM.isPending;
  const resolvedBy = draft.resolved_by_member_id ? memberById[draft.resolved_by_member_id]?.name : undefined;

  return (
    <div className="helddraft" data-comment-anchor="held-draft-card" data-status={status}>
      <div className="hd-head">
        <span className="hd-kind">被扣草稿</span>
        <span className="hd-by">@{agentName}</span>
        {draft.held_count != null && draft.held_count > 1 && (
          <span className="hd-chip">扣留 ×{draft.held_count}</span>
        )}
        <span className="hd-m">{fmtTime(draft.created_at)}</span>
      </div>

      {/* 升级横条:escalated_at 非空 = 已升级喊人(silence/held 升级链)。 */}
      {draft.escalated_at && (
        <div className="hd-escalate" role="status">
          <CircleAlert />已升级喊人 · {fmtTime(draft.escalated_at)}
        </div>
      )}

      {/* 草稿全文(长文折叠) */}
      <div className="hd-body">{shownBody}</div>
      {long && (
        <button className="hd-fold" onClick={() => setExpanded((v) => !v)}>
          {expanded ? '收起' : '展开全文'}
        </button>
      )}

      {/* 结构化被扣原因:未读清单(可点跳转)+ 截断计数 */}
      {shownIds.length > 0 && (
        <div className="hd-reasons">
          <span className="hd-rlb">因这些未读被扣</span>
          <div className="hd-unread">
            {shownIds.map((mid) => (
              <button
                key={mid}
                className="hd-uchip"
                onClick={() => onLocateMessage?.(mid)}
                title="跳转到该消息"
              >
                msg {mid.slice(-6)}
              </button>
            ))}
            {overflow > 0 && <span className="hd-more">还有 {overflow} 条未读</span>}
          </div>
        </div>
      )}

      {/* 底栏:终态回执 / 倒计时 + 三键 */}
      {terminal ? (
        <div className="hd-receipt" role="status">
          {RESULT_WORD[draft.resolution ?? status] ?? '已收敛'}
          {resolvedBy && <span className="hd-sub"> · by {resolvedBy}</span>}
          {draft.resolved_at && <span className="hd-sub"> · {fmtTime(draft.resolved_at)}</span>}
        </div>
      ) : (
        <div className="hd-foot">
          <span className="hd-count" data-testid="held-countdown">
            {reevaluating ? '重评估中…' : `${fmtCountdown(remaining)} 后重评估`}
          </span>
          {canResolve && (
            <div className="hd-keys">
              <button
                className="hd-key release"
                onClick={() => releaseM.mutate(draft.id)}
                disabled={pending}
              ><Send />放行</button>
              <button
                className="hd-key reeval"
                onClick={() => reevalM.mutate(draft.id)}
                disabled={pending}
              ><RotateCcw />重评估</button>
              <button
                className="hd-key discard"
                onClick={() => discardM.mutate(draft.id)}
                disabled={pending}
              ><Trash2 />丢弃</button>
            </div>
          )}
        </div>
      )}

      {/* as_task 意图角标(放行时随消息落任务,B §9.4 语义)。 */}
      {draft.as_task && !terminal && (
        <div className="hd-astask"><ArrowUpRight />放行后建任务{draft.as_task.title ? `:${draft.as_task.title}` : ''}</div>
      )}
    </div>
  );
}

/**
 * 一个频道的被扣草稿组:按 thread_root_id 归位。
 * - 主流(threadRootId 省略):渲染 thread_root_id 为空的 held。
 * - 线程内(threadRootId 传入):渲染 thread_root_id 匹配该线程根的 held。
 */
export function HeldDraftList({
  drafts, channelId, threadRootId, memberById, canResolve, onLocateMessage,
}: {
  drafts: HeldDraftPublic[];
  channelId: string;
  threadRootId?: string;
  memberById: Record<string, MemberPublic>;
  canResolve: boolean;
  onLocateMessage?: (messageId: string) => void;
}) {
  const scoped = drafts.filter((d) =>
    threadRootId ? d.thread_root_id === threadRootId : !d.thread_root_id,
  );
  if (scoped.length === 0) return null;
  return (
    <div className="helddraft-list">
      {scoped.map((d) => (
        <HeldDraftCard
          key={d.id}
          draft={d}
          channelId={channelId}
          memberById={memberById}
          canResolve={canResolve}
          onLocateMessage={onLocateMessage}
        />
      ))}
    </div>
  );
}
