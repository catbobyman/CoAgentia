/** 帧信封构造与判别（契约 D §3；线上形状对齐 py 侧 client.py 逐字段）。 */

import { describe, expect, it } from 'vitest';

import { DAEMON_PROTOCOL_V } from '../src/generated/constants.ts';
import {
  ackFrame,
  isAckFrame,
  isInstrFrame,
  isPing,
  isQueryFrame,
  pingFrame,
  pongFrame,
  replyFrame,
  reportFrame,
} from '../src/protocol.ts';

describe('出帧构造器', () => {
  it('reportFrame 全字段 + ULID/时间戳形状', () => {
    const f = reportFrame('hello', { a: 1 });
    expect(f['v']).toBe(DAEMON_PROTOCOL_V);
    expect(f['kind']).toBe('report');
    expect(f['type']).toBe('hello');
    expect(f['frame_id']).toMatch(/^[0-9A-HJKMNP-TV-Z]{26}$/);
    expect(f['at']).toMatch(/Z$/);
    expect(f['data']).toEqual({ a: 1 });
  });

  it('ping/pong 帧形 = {v, kind}（与 py client.py 逐字段一致）', () => {
    expect(pingFrame()).toEqual({ v: DAEMON_PROTOCOL_V, kind: 'ping' });
    expect(pongFrame()).toEqual({ v: DAEMON_PROTOCOL_V, kind: 'pong' });
  });

  it('replyFrame = {v, kind, ref, data}；ackFrame error 缺省 null、data 缺省不带', () => {
    expect(replyFrame('R1', { x: 1 })).toEqual({ v: DAEMON_PROTOCOL_V, kind: 'reply', ref: 'R1', data: { x: 1 } });
    expect(ackFrame('F1', 'done')).toEqual({ v: DAEMON_PROTOCOL_V, kind: 'ack', ref: 'F1', result: 'done', error: null });
    expect(ackFrame('F2', 'failed', { code: 'HANDLER_ERROR', message: 'x' })).toMatchObject({
      result: 'failed',
      error: { code: 'HANDLER_ERROR', message: 'x' },
    });
    expect(ackFrame('F3', 'done', null, { extra: 1 })['data']).toEqual({ extra: 1 });
  });
});

describe('入帧判别', () => {
  it('instr/query/ack/ping 判别与畸形帧拒绝', () => {
    expect(isInstrFrame({ kind: 'instr', frame_id: 'F', type: 'agent.start' })).toBe(true);
    expect(isInstrFrame({ kind: 'instr', type: 'agent.start' })).toBe(false); // 缺 frame_id
    expect(isQueryFrame({ kind: 'query', frame_id: 'F', type: 'home.tree' })).toBe(true);
    expect(isAckFrame({ kind: 'ack', ref: 'F', result: 'done' })).toBe(true);
    expect(isAckFrame({ kind: 'ack', ref: 'F' })).toBe(false); // 缺 result
    expect(isPing({ kind: 'ping' })).toBe(true);
    expect(isPing({ kind: 'pong' })).toBe(false);
  });
});
