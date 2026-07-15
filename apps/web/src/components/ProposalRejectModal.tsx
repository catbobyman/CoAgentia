// M6b 提案拒绝弹窗（草稿层 full / delta 面板共用）：理由可空 → POST /proposals/{id}/reject。
// 理由发进 source 任务线程，是 Orchestrator 重出的纠正信号（拆解设计 §8.2/§11）。
import { useState } from 'react';

export function ProposalRejectModal({ busy, onCancel, onSubmit }: {
  busy: boolean;
  onCancel: () => void;
  onSubmit: (reason: string) => void;
}) {
  const [reason, setReason] = useState('');
  return (
    <div className="scrim" onClick={onCancel}>
      <div className="modal dl-reject" role="dialog" aria-label="拒绝提案" onClick={(e) => e.stopPropagation()}>
        <div className="mtitle">拒绝提案</div>
        <div className="dl-reject-note">理由可空。理由会发进 source 任务线程，作为 Orchestrator 重出的纠正信号。</div>
        <textarea
          className="dl-reject-ta" rows={3} value={reason} autoFocus aria-label="拒绝理由"
          placeholder="例：把人工评审门换成自动化单测 + lint 门" onChange={(e) => setReason(e.target.value)}
        />
        <div className="ops">
          <button type="button" className="btn btn-ghost" onClick={onCancel}>取消</button>
          <button type="button" className="btn btn-secondary" data-testid="proposal-reject-submit" disabled={busy} onClick={() => onSubmit(reason.trim())}>确认拒绝</button>
        </div>
      </div>
    </div>
  );
}
