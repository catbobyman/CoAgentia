// F1 已读游标上报：节流（2s，前沿即报 + 尾随合并）、按频道去重、乐观更新 owner 自身 read_positions。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, type ReactNode } from 'react';

import type { ChannelsSnapshot, MemberPublic } from '@coagentia/contracts-ts';

const { setReadPosition } = vi.hoisted(() => ({
  setReadPosition: vi.fn((_channelId: string, _messageId: string) => Promise.resolve()),
}));
vi.mock('../api', () => ({ api: { setReadPosition } }));

import { qk } from '../lib/queryKeys';
import { readPositionsMap } from './queries';
import { useReadCursor } from './useReadCursor';

const OWNER: MemberPublic = {
  id: 'mem_owner', kind: 'human', role: 'owner', name: 'Owner',
  workspace_id: 'ws_1', created_at: '2026-07-09T00:00:00Z',
};

function setup() {
  const qc = new QueryClient();
  qc.setQueryData<MemberPublic[]>(qk.members(), [OWNER]);
  qc.setQueryData<ChannelsSnapshot>(qk.channels(), { items: [], read_positions: [] });
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: qc }, children);
  const { result } = renderHook(() => useReadCursor(), { wrapper });
  return { qc, markRead: result.current };
}

beforeEach(() => { setReadPosition.mockClear(); vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); });

describe('useReadCursor', () => {
  it('首次调用即上报 + 乐观更新 owner 游标', () => {
    const { qc, markRead } = setup();
    act(() => markRead('ch1', 'msg1'));
    expect(setReadPosition).toHaveBeenCalledWith('ch1', 'msg1');
    const snap = qc.getQueryData<ChannelsSnapshot>(qk.channels());
    expect(readPositionsMap(snap)['ch1']?.last_read_message_id).toBe('msg1');
    expect(readPositionsMap(snap)['ch1']?.member_id).toBe(OWNER.id);
  });

  it('同 id 去重：不重复上报', () => {
    const { markRead } = setup();
    act(() => markRead('ch1', 'msg1'));
    act(() => markRead('ch1', 'msg1'));
    expect(setReadPosition).toHaveBeenCalledTimes(1);
  });

  it('节流窗口内的新 id 尾随合并，2s 后落最新', () => {
    const { markRead } = setup();
    act(() => markRead('ch1', 'msg1')); // t=0 前沿即报
    expect(setReadPosition).toHaveBeenCalledTimes(1);
    act(() => markRead('ch1', 'msg2')); // 窗口内：排期尾随
    act(() => markRead('ch1', 'msg3')); // 覆盖 pending 为最新
    expect(setReadPosition).toHaveBeenCalledTimes(1); // 尚未落
    act(() => vi.advanceTimersByTime(2000));
    expect(setReadPosition).toHaveBeenCalledTimes(2);
    expect(setReadPosition).toHaveBeenLastCalledWith('ch1', 'msg3'); // 落最新，非 msg2
  });

  it('缺 messageId 不上报', () => {
    const { markRead } = setup();
    act(() => markRead('ch1', undefined));
    expect(setReadPosition).not.toHaveBeenCalled();
  });

  it('上报失败 → 清除去重标记，同 id 后续可重报', async () => {
    const { markRead } = setup();
    setReadPosition.mockImplementationOnce(() => Promise.reject(new Error('net')));
    act(() => markRead('ch1', 'msg1')); // call 1（拒绝）
    await act(async () => { await Promise.resolve(); await Promise.resolve(); }); // flush .catch 清标记
    expect(setReadPosition).toHaveBeenCalledTimes(1);
    act(() => markRead('ch1', 'msg1')); // 去重已清 → 通过；窗口内排尾随
    act(() => vi.advanceTimersByTime(2000));
    expect(setReadPosition).toHaveBeenCalledTimes(2); // 重报成功
  });
});
