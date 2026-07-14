// F1 已读游标上报（B1：setReadPosition 有定义零调用 → 未读永不清）。返回 markRead(channelId, msgId)：
// 节流（2s，前沿即报 + 尾随合并到最新 id）、按频道去重、乐观更新 ChannelsSnapshot.read_positions
// （owner 自身游标——与 wsBridge read.updated 的 owner-only 守卫同口径，agent 游标不入快照）。
// 真上报走 api.setReadPosition（PUT）；WS read.updated 广播回流是兜底（他端同步 + 本地纠偏）。
import { useCallback, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';

import type { ChannelsSnapshot, MemberPublic, ReadPositionPublic } from '@coagentia/contracts-ts';

import { api } from '../api';
import { qk } from '../lib/queryKeys';

const THROTTLE_MS = 2000;

export function useReadCursor() {
  const qc = useQueryClient();
  const lastReported = useRef<Record<string, string>>({}); // 已成功上报的 id（去重）
  const pending = useRef<Record<string, string>>({}); // 尾随窗口内最新待报 id
  const lastAt = useRef<Record<string, number>>({}); // 上次上报时刻
  const timers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  const report = useCallback(
    (channelId: string, messageId: string) => {
      lastReported.current[channelId] = messageId;
      // 乐观更新 owner 自身游标（wsBridge read.updated 同口径：只反映 human owner）。
      const members = qc.getQueryData<MemberPublic[]>(qk.members());
      const owner = members?.find((m) => m.kind === 'human' && m.role === 'owner');
      if (owner) {
        qc.setQueryData<ChannelsSnapshot>(qk.channels(), (prev) => {
          if (!prev) return prev;
          const positions = (prev.read_positions as ReadPositionPublic[]) ?? [];
          const i = positions.findIndex(
            (x) => x.channel_id === channelId && x.member_id === owner.id,
          );
          const entry: ReadPositionPublic = {
            channel_id: channelId,
            member_id: owner.id,
            last_read_message_id: messageId,
            last_read_at: new Date().toISOString(),
          };
          const next = positions.slice();
          if (i < 0) next.push(entry);
          else next[i] = entry;
          return { ...prev, read_positions: next };
        });
      }
      // 尽力而为：失败时撤销去重标记，让下次 markRead（focus/新消息/切频道 + resync 重现未读后）
      // 能重报同一 id（否则该 id 被永久去重、失败后再不重试——与"下次会重试"的语义一致）。
      void api.setReadPosition(channelId, messageId).catch(() => {
        if (lastReported.current[channelId] === messageId) delete lastReported.current[channelId];
      });
    },
    [qc],
  );

  return useCallback(
    (channelId: string, messageId: string | undefined) => {
      if (!channelId || !messageId) return;
      if (lastReported.current[channelId] === messageId) return; // 去重：已报过该 id
      pending.current[channelId] = messageId;
      const now = Date.now();
      const since = now - (lastAt.current[channelId] ?? 0);
      if (timers.current[channelId]) return; // 尾随已排期，落到最新 pending 即可
      if (since >= THROTTLE_MS) {
        lastAt.current[channelId] = now;
        report(channelId, messageId);
      } else {
        timers.current[channelId] = setTimeout(() => {
          delete timers.current[channelId];
          const id = pending.current[channelId];
          if (id && lastReported.current[channelId] !== id) {
            lastAt.current[channelId] = Date.now();
            report(channelId, id);
          }
        }, THROTTLE_MS - since);
      }
    },
    [report],
  );
}
