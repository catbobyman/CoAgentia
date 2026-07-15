// O8 汇总输入摘要卡片（M8b B-M8-2 ④）：系统摘要消息体 → 结构化卡片（轮数 chip / 覆盖-未覆盖 /
// 逐节点段 / 提示）。
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';

import { SummaryCard } from './SummaryCard';

const BODY = [
  '**汇总输入摘要**（第 2 轮 / 上限 8）',
  '覆盖：1/2 个上游节点已 Done；未覆盖：#5「登录页」（closed）',
  '- #3 实现后端 · Rin · done',
  '  deliverables: api/auth.py，api/models.py',
  '- #5 登录页 · Kai · closed',
  '提示：总报告须逐条照抄上方「未覆盖」清单（W9 诚实性）。',
].join('\n');

describe('SummaryCard', () => {
  it('呈现轮数 chip、未覆盖计数与逐节点段', () => {
    render(<SummaryCard body={BODY} />);
    expect(screen.getByTestId('summary-card')).toBeInTheDocument();
    expect(screen.getByTestId('sc-round')).toHaveTextContent('第 2 轮 / 上限 8');
    expect(screen.getByTestId('sc-uncovered')).toHaveTextContent('未覆盖 1');
    // 覆盖行拆出未覆盖片段。
    expect(screen.getByText(/未覆盖：#5「登录页」（closed）/)).toBeInTheDocument();
    // 逐节点段（node 行 header 去掉 "- " 前缀）。
    expect(screen.getByText('#3 实现后端 · Rin · done')).toBeInTheDocument();
    expect(screen.getByText('deliverables: api/auth.py，api/models.py')).toBeInTheDocument();
    // 头行不重复进正文（已在 chip 呈现）。
    expect(screen.queryByText('**汇总输入摘要**（第 2 轮 / 上限 8）')).toBeNull();
  });

  it('无未覆盖时不显示未覆盖 chip', () => {
    render(<SummaryCard body={'**汇总输入摘要**（第 1 轮 / 上限 8）\n覆盖：2/2 个上游节点已 Done'} />);
    expect(screen.queryByTestId('sc-uncovered')).toBeNull();
  });
});
