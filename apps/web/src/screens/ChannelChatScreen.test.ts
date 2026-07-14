// K2(M8a 加固批)：?thread= 深链直开修复的核心闸——resolveThreadChannelId 从线程消息数组解析所属
// 频道 id,供 ChannelChatScreen 在 activeChannelId 与线程实际频道不一致时纠偏 setActiveChannel。
// 抽为纯函数以脱离整屏组件单测(同 routes/search.ts 的 validateChannelSearch 体例)。
// 运行:pnpm -F @coagentia/web test
import { describe, expect, it } from 'vitest';

import type { MessagePublic } from '@coagentia/contracts-ts';

import { resolveThreadChannelId } from './ChannelChatScreen';

function msg(id: string, channelId: string): MessagePublic {
  return {
    id, channel_id: channelId, workspace_id: 'ws_1', body: 'x',
    created_at: '2026-07-14T00:00:00Z',
  };
}

describe('resolveThreadChannelId(?thread= 深链所属频道解析)', () => {
  it('命中 rootMessageId 的那条消息 → 取其 channel_id', () => {
    const messages = [msg('root_1', 'ch_a'), msg('reply_1', 'ch_a')];
    expect(resolveThreadChannelId(messages, 'root_1')).toBe('ch_a');
  });

  it('rootMessageId 未命中数组任何一条(异常/乱序) → 兜底取数组首条', () => {
    const messages = [msg('root_1', 'ch_b'), msg('reply_1', 'ch_b')];
    expect(resolveThreadChannelId(messages, 'missing_id')).toBe('ch_b');
  });

  it('消息数组为空/未加载 → undefined(不误纠偏)', () => {
    expect(resolveThreadChannelId([], 'root_1')).toBeUndefined();
    expect(resolveThreadChannelId(undefined, 'root_1')).toBeUndefined();
  });

  it('rootMessageId 为 undefined(无深链) → 兜底取数组首条(调用方不应传空数组以外的场景)', () => {
    const messages = [msg('root_1', 'ch_c')];
    expect(resolveThreadChannelId(messages, undefined)).toBe('ch_c');
  });
});
