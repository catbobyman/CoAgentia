/**
 * 帧信封窄化层（契约 D §3；对等基准 = coagentia_contracts daemon.py 五种帧 + PING/PONG）。
 *
 * contracts-ts 生成物只有 type-only 形状（data: unknown、无判别联合）——本层提供：
 * ① 出帧构造器（daemon 只发 report/reply/ack/ping/pong）；② 入帧结构判别（server 发
 * instr/query/ack/ping/pong）。运行时校验按 py 侧口径：结构不符按「未知/畸形帧」处置
 * （client 决定 ack failed 或忽略），不做全字段 schema 校验（py 侧同样只按需取字段）。
 */

import type { AckResult, FrameError, InstrType, QueryType, ReportType } from '@coagentia/contracts-ts';

import { DAEMON_PROTOCOL_V } from './generated/constants.ts';
import { newUlid, nowIso } from './util.ts';

export type JsonValue = unknown;
export type JsonObject = Record<string, unknown>;

// ---- kind 常量（值域 = 契约 FrameKind；erasable-only 纪律用 const 对象非 enum）----
export const FRAME_KIND = {
  INSTR: 'instr',
  QUERY: 'query',
  REPLY: 'reply',
  REPORT: 'report',
  ACK: 'ack',
  PING: 'ping',
  PONG: 'pong',
} as const;
export type FrameKindValue = (typeof FRAME_KIND)[keyof typeof FRAME_KIND];

// ---- 入帧视图（server → daemon）----
export interface InstrFrameIn {
  kind: 'instr';
  frame_id: string;
  type: InstrType;
  at: string;
  data: JsonValue;
  v: number;
}

export interface QueryFrameIn {
  kind: 'query';
  frame_id: string;
  type: QueryType;
  at: string;
  data: JsonValue;
  v: number;
}

export interface AckFrameIn {
  kind: 'ack';
  ref: string;
  result: AckResult;
  error?: FrameError | null;
  data?: JsonValue | null;
}

export function isInstrFrame(f: JsonObject): f is JsonObject & InstrFrameIn {
  return f['kind'] === FRAME_KIND.INSTR && typeof f['frame_id'] === 'string' && typeof f['type'] === 'string';
}

export function isQueryFrame(f: JsonObject): f is JsonObject & QueryFrameIn {
  return f['kind'] === FRAME_KIND.QUERY && typeof f['frame_id'] === 'string' && typeof f['type'] === 'string';
}

export function isAckFrame(f: JsonObject): f is JsonObject & AckFrameIn {
  return f['kind'] === FRAME_KIND.ACK && typeof f['ref'] === 'string' && typeof f['result'] === 'string';
}

export function isPing(f: JsonObject): boolean {
  return f['kind'] === FRAME_KIND.PING;
}

export function isPong(f: JsonObject): boolean {
  return f['kind'] === FRAME_KIND.PONG;
}

// ---- 出帧构造器（daemon → server）----
export function reportFrame(type: ReportType, data: JsonValue, frameId?: string): JsonObject {
  return {
    v: DAEMON_PROTOCOL_V,
    kind: FRAME_KIND.REPORT,
    frame_id: frameId ?? newUlid(),
    type,
    at: nowIso(),
    data,
  };
}

export function replyFrame(ref: string, data: JsonValue): JsonObject {
  return { v: DAEMON_PROTOCOL_V, kind: FRAME_KIND.REPLY, ref, data };
}

export function ackFrame(ref: string, result: AckResult, error?: FrameError | null, data?: JsonValue): JsonObject {
  const frame: JsonObject = { v: DAEMON_PROTOCOL_V, kind: FRAME_KIND.ACK, ref, result, error: error ?? null };
  if (data !== undefined) frame['data'] = data;
  return frame;
}

export function pingFrame(): JsonObject {
  return { v: DAEMON_PROTOCOL_V, kind: FRAME_KIND.PING };
}

export function pongFrame(): JsonObject {
  return { v: DAEMON_PROTOCOL_V, kind: FRAME_KIND.PONG };
}
