// O8 汇总输入摘要卡片（M8b B-M8-2 ④，汇总设计 §4）：把系统「汇总输入摘要」消息体从纯文本升级为
// 结构化卡片——轮数 chip、覆盖/未覆盖 callout、逐节点段。纯展示（数据 = 消息 body，解析单点在
// lib/summary.classifySummaryLines），无请求、无状态。
import { ClipboardList } from 'lucide-react';

import { classifySummaryLines, parseSummaryHeader, summaryCoverCounts } from '../lib/summary';

export function SummaryCard({ body }: { body: string }) {
  const header = parseSummaryHeader(body);
  const cover = summaryCoverCounts(body);
  const lines = classifySummaryLines(body);
  const uncovered = cover ? cover.total - cover.covered : 0;

  return (
    <div className="summary-card" data-testid="summary-card">
      <div className="sc-head">
        <ClipboardList />
        <span className="sc-title">汇总输入摘要</span>
        {header && (
          <span className="sc-round" data-testid="sc-round">
            第 {header.round} 轮 / 上限 {header.maxRounds}
          </span>
        )}
        {uncovered > 0 && (
          <span className="sc-uncovered" data-testid="sc-uncovered">未覆盖 {uncovered}</span>
        )}
      </div>
      <div className="sc-body">
        {lines.map((ln, i) => {
          if (ln.kind === 'header') return null; // 头已在 chip 呈现
          if (ln.kind === 'cover') {
            const [left, right] = ln.text.split('；未覆盖：');
            return (
              <div className="sc-cover" key={i}>
                <span>{left}</span>
                {right && <span className="sc-cover-un">未覆盖：{right}</span>}
              </div>
            );
          }
          if (ln.kind === 'node') return <div className="sc-node" key={i}>{ln.text}</div>;
          if (ln.kind === 'detail') return <div className="sc-detail" key={i}>{ln.text}</div>;
          if (ln.kind === 'hint') return <div className="sc-hint" key={i}>{ln.text}</div>;
          if (ln.kind === 'truncated') return <div className="sc-trunc" key={i}>{ln.text}</div>;
          if (ln.text.trim() === '') return null;
          return <div className="sc-text" key={i}>{ln.text}</div>;
        })}
      </div>
    </div>
  );
}
