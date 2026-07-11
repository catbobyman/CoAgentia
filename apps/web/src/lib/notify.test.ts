// 每频道通知设置消费(M5 B §11.4)。运行:pnpm -F @coagentia/web test
import { describe, expect, it } from 'vitest';

import type { ChannelsSnapshot, MessagePublic } from '@coagentia/contracts-ts';

import { badgeStyle, hasUnreadMention, notifyModeOf, shouldDesktopNotify } from './notify';

function msg(id: string, body: string): MessagePublic {
  return { id, body, channel_id: 'ch1', workspace_id: 'ws1', created_at: '2026-07-11T00:00:00Z' };
}

describe('notifyModeOf', () => {
  const snap = {
    items: [],
    read_positions: [],
    notification_settings: [{ channel_id: 'ch1', member_id: 'me', mode: 'mute' }],
  } as unknown as ChannelsSnapshot;

  it('非默认行命中 → 该 mode', () => {
    expect(notifyModeOf(snap, 'ch1')).toBe('mute');
  });
  it('无行 / 无快照 → 默认 all', () => {
    expect(notifyModeOf(snap, 'ch2')).toBe('all');
    expect(notifyModeOf(undefined, 'ch1')).toBe('all');
  });
});

describe('hasUnreadMention', () => {
  const msgs = [msg('m1', 'hi'), msg('m2', 'ping @Memcyo'), msg('m3', 'ok')];
  it('lastRead 之后有 @我 → true', () => {
    expect(hasUnreadMention(msgs, 'm1', 'Memcyo')).toBe(true);
  });
  it('@我 在已读窗口内(lastRead 之后无) → false', () => {
    expect(hasUnreadMention(msgs, 'm2', 'Memcyo')).toBe(false);
  });
  it('无 lastRead → 全量扫描', () => {
    expect(hasUnreadMention(msgs, undefined, 'Memcyo')).toBe(true);
  });
  it('meName 空 → false', () => {
    expect(hasUnreadMention(msgs, undefined, '')).toBe(false);
  });
});

describe('badgeStyle', () => {
  it('all → normal', () => {
    expect(badgeStyle('all', false)).toBe('normal');
    expect(badgeStyle('all', true)).toBe('normal');
  });
  it('mentions → 有@点亮 mention, 无@弱化 muted', () => {
    expect(badgeStyle('mentions', true)).toBe('mention');
    expect(badgeStyle('mentions', false)).toBe('muted');
  });
  it('mute → 恒 muted', () => {
    expect(badgeStyle('mute', true)).toBe('muted');
    expect(badgeStyle('mute', false)).toBe('muted');
  });
});

describe('shouldDesktopNotify', () => {
  it('自己发的从不通知', () => {
    expect(shouldDesktopNotify('all', true, true)).toBe(false);
  });
  it('all → 任意新消息', () => {
    expect(shouldDesktopNotify('all', false, false)).toBe(true);
  });
  it('mentions → 仅 @我', () => {
    expect(shouldDesktopNotify('mentions', true, false)).toBe(true);
    expect(shouldDesktopNotify('mentions', false, false)).toBe(false);
  });
  it('mute → 从不', () => {
    expect(shouldDesktopNotify('mute', true, false)).toBe(false);
  });
});
