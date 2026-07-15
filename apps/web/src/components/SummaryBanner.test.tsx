// O8 汇总任务线程横幅（M8b B-M8-2 ①）：active（轮数/未覆盖）/ blocked（原因 + 恢复按钮）三态渲染。
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import { SummaryBanner } from './SummaryBanner';

describe('SummaryBanner', () => {
  it('active：呈现轮数 N/M 与未覆盖计数', () => {
    render(<SummaryBanner banner={{ kind: 'active', round: 3, maxRounds: 8, uncovered: 2 }} />);
    expect(screen.getByTestId('o8-banner')).toHaveClass('o8-active');
    expect(screen.getByTestId('o8-rounds')).toHaveTextContent('第 3 轮 / 上限 8');
    expect(screen.getByTestId('o8-uncovered')).toHaveTextContent('未覆盖 2');
  });

  it('active：未覆盖为 0 时不显示未覆盖 chip', () => {
    render(<SummaryBanner banner={{ kind: 'active', round: 1, maxRounds: 8, uncovered: 0 }} />);
    expect(screen.queryByTestId('o8-uncovered')).toBeNull();
  });

  it('blocked（轮数触顶）：alert + 原因 + 恢复按钮触发 onRecover', () => {
    const onRecover = vi.fn();
    render(
      <SummaryBanner
        banner={{ kind: 'blocked', reasonKind: 'rounds', reasonText: '轮数触顶（8/8）', round: 8, maxRounds: 8 }}
        onRecover={onRecover}
      />,
    );
    expect(screen.getByTestId('o8-banner')).toHaveClass('o8-blocked');
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByTestId('o8-reason')).toHaveTextContent('轮数触顶 8/8');
    fireEvent.click(screen.getByTestId('o8-recover'));
    expect(onRecover).toHaveBeenCalledTimes(1);
  });

  it('blocked（stall）：呈现 stall 计数', () => {
    render(
      <SummaryBanner
        banner={{ kind: 'blocked', reasonKind: 'stall', reasonText: '空转触顶（stall 3/3）', stall: 3, maxStall: 3 }}
      />,
    );
    expect(screen.getByTestId('o8-reason')).toHaveTextContent('空转 stall 3/3');
    // 无 onRecover → 不渲染恢复按钮（active 态或无回调时）。
    expect(screen.queryByTestId('o8-recover')).toBeNull();
  });
});
