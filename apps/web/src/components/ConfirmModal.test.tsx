// 不可撤销动作确认弹窗（Full reset / 删频道 / 移除机器共用）：requireText 防呆——须逐字键入才解锁确认。
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import { ConfirmModal } from './ConfirmModal';

describe('ConfirmModal', () => {
  it('无 requireText：确认钮直接可用', () => {
    const onConfirm = vi.fn();
    render(<ConfirmModal title="删除" message="确认？" confirmLabel="删除" onConfirm={onConfirm} onClose={() => {}} />);
    const btn = screen.getByRole('button', { name: '删除' });
    expect(btn).not.toBeDisabled();
    fireEvent.click(btn);
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it('requireText：未键入正确文本时确认钮禁用，键入后解锁', () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmModal
        title="完全重置" message="不可撤销" confirmLabel="完全重置" danger
        requireText="Orchestrator" onConfirm={onConfirm} onClose={() => {}}
      />,
    );
    const btn = screen.getByRole('button', { name: '完全重置' });
    expect(btn).toBeDisabled();

    const input = screen.getByLabelText('确认输入');
    fireEvent.change(input, { target: { value: 'Orchestr' } }); // 不完全
    expect(btn).toBeDisabled();

    fireEvent.change(input, { target: { value: 'Orchestrator' } }); // 逐字匹配
    expect(btn).not.toBeDisabled();
    fireEvent.click(btn);
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it('取消钮触发 onClose', () => {
    const onClose = vi.fn();
    render(<ConfirmModal title="删除" message="确认？" onConfirm={() => {}} onClose={onClose} />);
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
