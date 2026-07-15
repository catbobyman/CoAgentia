// M6b 提案卡（card_kind==='proposal' 的锚点消息 → 结构化卡片；拆解设计 §8.1 / 交互 §6.8）。
// 消息正文（散文，已由 MessageFlow 剥离 <control> 块）在卡外照常渲染；本卡渲染提案摘要：
//   模式 / 节点数 / 依赖边数缩略 / 指纹短码（proposal_hash 前 6 位）/ 生命周期态徽标（全态配色）
//   + 「在画布中审阅」入口（草稿层归后半——先挂跳画布页签）+ failed 态错误清单入口（引导查看线程）。
// 数据源 = GET /proposals/{card_ref}（useProposal，react-query），proposal.updated/draft.* WS 实时刷新。
import type { CSSProperties } from 'react';
import { ExternalLink, GitBranch, Layers, Workflow } from 'lucide-react';

import type { ProposalPublic } from '@coagentia/contracts-ts';

import { PROPOSAL_STATUS_VAR, PROPOSAL_STATUS_WORD } from '../lib/uiMaps';
import { useProposal } from '../data/queries';
import { readDeltaOps } from '../lib/deltaOps';
import './proposal-card.css';

const MODE_WORD: Record<string, string> = { decompose: '拆解', single_task: '单任务' };

// 提案态徽标失败/终态判定（展示态：failed=修复穷尽、rejected=人类拒绝、superseded=被取代）。
const FAILED: ReadonlySet<string> = new Set(['failed']);
const CLOSED_NO_REVIEW: ReadonlySet<string> = new Set(['failed', 'rejected', 'superseded']);

/** proposal.body（JsonValue）里安全读出摘要字段（body 形状由 server 提交期校验，此处守空防崩）。 */
function readSummary(body: unknown): {
  mode: string; nodeCount: number; edgeCount: number; summary: string;
} {
  const b = (body ?? {}) as { mode?: unknown; nodes?: unknown; edges?: unknown; summary?: unknown };
  return {
    mode: typeof b.mode === 'string' ? b.mode : '',
    nodeCount: Array.isArray(b.nodes) ? b.nodes.length : 0,
    edgeCount: Array.isArray(b.edges) ? b.edges.length : 0,
    summary: typeof b.summary === 'string' ? b.summary : '',
  };
}

export function ProposalCard({
  proposalId, onReviewInCanvas, onReviewDelta, onViewThread,
}: {
  proposalId: string;
  /** full 提案「查看草稿」→ 切画布页签 + 激活草稿层（awaiting_confirm）/ 其余可审态「在画布中审阅」。 */
  onReviewInCanvas?: (proposalId: string) => void;
  /** delta 提案「审查增量」→ 打开 delta 面板（awaiting_confirm）。 */
  onReviewDelta?: (proposalId: string) => void;
  /** failed 态「查看线程」→ 打开 source 线程看错误清单系统消息。 */
  onViewThread?: () => void;
}) {
  const q = useProposal(proposalId);
  const proposal = q.data as ProposalPublic | undefined;

  if (q.isLoading && !proposal) {
    return <div className="proposal-card loading" data-testid="proposal-card">拆解提案加载中…</div>;
  }
  if (!proposal) {
    return (
      <div className="proposal-card error" data-testid="proposal-card" role="status">
        拆解提案暂不可用（可能已被清理）。
      </div>
    );
  }

  const status = proposal.status ?? 'drafting';
  const kind = proposal.kind ?? 'full';
  const isDelta = kind === 'delta';
  const { mode, nodeCount, edgeCount, summary } = readSummary(proposal.body);
  // delta 形态 body 无 full 的 mode/nodes/edges——用 readDeltaOps 统计增删（code-review 修复：
  // 否则 delta 卡恒显 0 节点/依赖 0/模式 —，指标行对用户无意义）。
  const deltaOps = isDelta ? readDeltaOps(proposal.body) : [];
  const deltaAdd = deltaOps.filter((o) => o.kind === 'add').length;
  const deltaRemove = deltaOps.filter((o) => o.kind === 'remove').length;
  const shortHash = proposal.proposal_hash.slice(0, 6);
  const isFailed = FAILED.has(status);
  const canReview = !CLOSED_NO_REVIEW.has(status);
  const awaiting = status === 'awaiting_confirm';
  // delta 待确认 → 「审查增量」（delta 面板）；full 待确认 → 「查看草稿」（草稿层）；其余可审态 → 「在画布中审阅」。
  const isReviewDelta = awaiting && kind === 'delta';
  const reviewLabel = awaiting && kind === 'full' ? '查看草稿' : '在画布中审阅';

  return (
    <div
      className="proposal-card"
      data-testid="proposal-card"
      data-status={status}
      data-kind={proposal.kind ?? 'full'}
    >
      <div className="pc-head">
        <span className="pc-kind"><Workflow />拆解提案</span>
        {proposal.revision != null && proposal.revision > 1 && (
          <span className="pc-rev">rev {proposal.revision}</span>
        )}
        <span className="sp" />
        <span
          className="pc-badge"
          data-testid="proposal-status"
          style={{ '--pcst': `var(${PROPOSAL_STATUS_VAR[status] ?? '--text-muted'})` } as unknown as CSSProperties}
        >
          {PROPOSAL_STATUS_WORD[status] ?? status}
        </span>
      </div>

      {summary && <div className="pc-summary">{summary}</div>}

      <div className="pc-metrics">
        {isDelta ? (
          <>
            <span className="pc-metric" data-testid="proposal-mode"><Layers />增量</span>
            <span className="pc-metric" data-testid="delta-add">+{deltaAdd} 新增</span>
            <span className="pc-metric" data-testid="delta-remove"><GitBranch />−{deltaRemove} 删除</span>
          </>
        ) : (
          <>
            <span className="pc-metric" data-testid="proposal-mode">
              <Layers />{MODE_WORD[mode] ?? (mode || '—')}
            </span>
            <span className="pc-metric">{nodeCount} 节点</span>
            <span className="pc-metric"><GitBranch />依赖 {edgeCount}</span>
          </>
        )}
        <span className="pc-hash" title={`指纹 ${proposal.proposal_hash}`}>#{shortHash}</span>
      </div>

      {isFailed && (
        <div className="pc-failed" role="alert">
          <span>提案修复穷尽，已升级人类；错误清单见 source 线程系统消息。</span>
          {onViewThread && (
            <button type="button" className="pc-link" data-testid="proposal-view-thread" onClick={onViewThread}>
              查看线程<ExternalLink />
            </button>
          )}
        </div>
      )}

      {isReviewDelta && onReviewDelta ? (
        <div className="pc-foot">
          <button
            type="button"
            className="pc-review"
            data-testid="proposal-review-delta"
            onClick={() => onReviewDelta(proposalId)}
          >
            审查增量<ExternalLink />
          </button>
        </div>
      ) : canReview && onReviewInCanvas ? (
        <div className="pc-foot">
          <button
            type="button"
            className="pc-review"
            data-testid="proposal-review"
            onClick={() => onReviewInCanvas(proposalId)}
          >
            {reviewLabel}<ExternalLink />
          </button>
        </div>
      ) : null}
    </div>
  );
}
