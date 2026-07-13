// M7b 成本汇总条（画布页签轻量 chip；交互 §12 / B §13.4）。GET /usage?level=canvas&ref=<channel_id>
// → Σ token 四字段合计 + 覆盖率 N/M（诚实标注，未上报任务计入分母）。永不折算货币（W7）。空集/
// 加载中不闪烁（数据未就绪则不渲染）。
import { Coins } from 'lucide-react';

import { useUsage } from '../data/queries';
import { fmtTokens, sumTokens } from './DeploymentCard';
import './deployment-card.css';

export function CanvasUsageChip({ channelId }: { channelId: string }) {
  const q = useUsage('canvas', channelId);
  const report = q.data;
  if (!report) return null; // 未就绪不占位（避免 300ms 闪烁）
  const total = sumTokens(report.usage);
  const reporting = report.tasks_reporting?.reporting ?? 0;
  const totalTasks = report.tasks_reporting?.total ?? 0;
  return (
    <span
      className="usage-chip"
      data-testid="canvas-usage-chip"
      title="本频道画布任务 token 汇总（有 usage 上报的任务数 / 总任务数；永不折算货币）"
    >
      <Coins />
      <span className="usage-total">Σ {fmtTokens(total)} tok</span>
      <span className="usage-cov" data-testid="canvas-usage-coverage">{reporting}/{totalTasks} 上报</span>
    </span>
  );
}
