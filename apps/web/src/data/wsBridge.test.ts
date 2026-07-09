// read.updated 的 owner-only 守卫回归(#6/#7):ChannelsSnapshot.read_positions 只反映
// 当前 human owner(契约 B §4.5「自身 read-position」)。agent 游标广播必须被忽略,
// 否则 readPositionsMap 按 channel_id 折叠时 agent 覆盖 owner → 未读计数错。
// 运行:pnpm -F @coagentia/web test
import { QueryClient } from '@tanstack/react-query';
import { describe, expect, it } from 'vitest';

import type { ChannelsSnapshot, Envelope, MemberPublic } from '@coagentia/contracts-ts';

import { qk } from '../lib/queryKeys';
import { readPositionsMap } from './queries';
import { applyEnvelope } from './wsBridge';

const OWNER: MemberPublic = {
  id: 'mem_owner', kind: 'human', role: 'owner', name: 'Owner',
  workspace_id: 'ws_1', created_at: '2026-07-09T00:00:00Z',
};
const AGENT: MemberPublic = {
  id: 'mem_agent', kind: 'agent', name: 'Agent',
  workspace_id: 'ws_1', created_at: '2026-07-09T00:00:00Z',
};

function seedClient(): QueryClient {
  const qc = new QueryClient();
  qc.setQueryData<MemberPublic[]>(qk.members(), [OWNER, AGENT]);
  const snap: ChannelsSnapshot = {
    items: [],
    // REST 快照只含 owner 自身游标(channels.py 过滤 member_id == me)。
    read_positions: [
      { channel_id: 'ch_build', member_id: OWNER.id, last_read_message_id: 'msg_05', last_read_at: '2026-07-09T00:00:00Z' },
    ],
  };
  qc.setQueryData<ChannelsSnapshot>(qk.channels(), snap);
  return qc;
}

function readUpdated(memberId: string, lastReadId: string): Envelope {
  return {
    type: 'read.updated',
    channel_id: 'ch_build',
    workspace_id: 'ws_1',
    seq: 1,
    key: 'k1',
    at: '2026-07-09T01:00:00Z',
    data: { channel_id: 'ch_build', member_id: memberId, last_read_message_id: lastReadId },
  } as Envelope;
}

describe('wsBridge read.updated owner-only 守卫(#6/#7)', () => {
  it('忽略 agent 的 read.updated:owner 游标不被污染/覆盖', () => {
    const qc = seedClient();
    applyEnvelope(qc, readUpdated(AGENT.id, 'msg_08'));

    const snap = qc.getQueryData<ChannelsSnapshot>(qk.channels());
    // 数组里不应新增 agent 条目
    expect(snap!.read_positions).toHaveLength(1);
    // 折叠后仍是 owner 的游标(msg_05),而非 agent 的 msg_08
    expect(readPositionsMap(snap)['ch_build']!.last_read_message_id).toBe('msg_05');
    expect(readPositionsMap(snap)['ch_build']!.member_id).toBe(OWNER.id);
  });

  it('接受 owner 自身的 read.updated:游标推进', () => {
    const qc = seedClient();
    applyEnvelope(qc, readUpdated(OWNER.id, 'msg_09'));

    const snap = qc.getQueryData<ChannelsSnapshot>(qk.channels());
    expect(snap!.read_positions).toHaveLength(1);
    expect(readPositionsMap(snap)['ch_build']!.last_read_message_id).toBe('msg_09');
  });

  it('members 缓存缺失时安全忽略(不抛错、不写入)', () => {
    const qc = seedClient();
    qc.removeQueries({ queryKey: qk.members() });
    expect(() => applyEnvelope(qc, readUpdated(OWNER.id, 'msg_09'))).not.toThrow();
    const snap = qc.getQueryData<ChannelsSnapshot>(qk.channels());
    expect(readPositionsMap(snap)['ch_build']!.last_read_message_id).toBe('msg_05');
  });
});
