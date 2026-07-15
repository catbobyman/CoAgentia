// O8 汇总任务线程横幅（M8b B-M8-2 ①，汇总设计 §6）：汇总任务线程顶部呈现协调循环态——active（轮数
// N/M + 未覆盖计数）/ blocked（阻断原因 + 计数事实 + 恢复引导）。态由 lib/summary.deriveO8Banner 从线程
// 系统消息体派生（护栏可见、人机同源、零新端点）。恢复走既有 override 按钮（force-start，裁决 #8）。
import { Play, ShieldAlert, ShieldCheck } from 'lucide-react';

import type { O8Banner } from '../lib/summary';

export function SummaryBanner({ banner, onRecover }: {
  banner: O8Banner;
  /** 阻断态「强制启动以恢复」——打开 ForceStartModal（force-start 汇总任务同事务 recover）。 */
  onRecover?: () => void;
}) {
  if (banner.kind === 'active') {
    return (
      <div className="o8-banner o8-active" role="status" data-testid="o8-banner">
        <ShieldCheck />
        <span className="o8-lb">汇总协调中</span>
        <span className="o8-rounds" data-testid="o8-rounds">
          第 {banner.round} 轮 / 上限 {banner.maxRounds}
        </span>
        {banner.uncovered > 0 && (
          <span className="o8-uncovered" data-testid="o8-uncovered">未覆盖 {banner.uncovered}</span>
        )}
      </div>
    );
  }
  const detail =
    banner.reasonKind === 'rounds'
      ? `轮数触顶 ${banner.round ?? '?'}/${banner.maxRounds ?? '?'}`
      : `空转 stall ${banner.stall ?? '?'}/${banner.maxStall ?? '?'}`;
  return (
    <div className="o8-banner o8-blocked" role="alert" data-testid="o8-banner">
      <div className="o8-blocked-head">
        <ShieldAlert />
        <span className="o8-lb">汇总协调已阻断</span>
        <span className="o8-reason" data-testid="o8-reason">{detail}</span>
      </div>
      <div className="o8-blocked-foot">
        <span className="o8-guide">已停止自动唤醒——在本线程发言，或强制启动以恢复（恢复归零计数）。</span>
        {onRecover && (
          <button type="button" className="o8-recover" data-testid="o8-recover" onClick={onRecover}>
            <Play /> 强制启动以恢复
          </button>
        )}
      </div>
    </div>
  );
}
