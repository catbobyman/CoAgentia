/** transport 纯函数 + 内存传输桩自检（WS 真连接归实机 verify；契约 D §2）。 */

import { describe, expect, it } from 'vitest';

import { TransportClosed, serverUrlToWs } from '../src/transport.ts';
import { RecordingTransport, AutoAckTransport } from './helpers.ts';

describe('serverUrlToWs', () => {
  it('http → ws + 固定路径', () => {
    expect(serverUrlToWs('http://127.0.0.1:8787')).toBe('ws://127.0.0.1:8787/api/daemon/ws');
  });

  it('https → wss', () => {
    expect(serverUrlToWs('https://coagentia.example.com')).toBe('wss://coagentia.example.com/api/daemon/ws');
  });

  it('已是 ws(s) 保持', () => {
    expect(serverUrlToWs('wss://h:1/x')).toBe('wss://h:1/api/daemon/ws');
  });
});

describe('RecordingTransport（测试底座自检）', () => {
  it('send 记录、feed 注入、close 后 recv/send 抛 TransportClosed', async () => {
    const t = new RecordingTransport();
    await t.send({ kind: 'report', type: 'hello', frame_id: 'F1' });
    expect(t.reports('hello')).toHaveLength(1);
    t.feed({ kind: 'pong' });
    expect(await t.recv()).toEqual({ kind: 'pong' });
    const pending = t.recv();
    await t.close();
    await expect(pending).rejects.toThrow(TransportClosed);
    await expect(t.send({})).rejects.toThrow(TransportClosed);
  });

  it('AutoAckTransport 对 hello 自动回 hello_ack、ping 自动回 pong', async () => {
    const t = new AutoAckTransport(7);
    await t.send({ kind: 'report', type: 'hello', frame_id: 'FH' });
    const ack = (await t.recv()) as { kind: string; ref: string; data: { heartbeat_sec: number } };
    expect(ack.kind).toBe('ack');
    expect(ack.ref).toBe('FH');
    expect(ack.data.heartbeat_sec).toBe(7);
    await t.send({ kind: 'ping' });
    expect((await t.recv())['kind']).toBe('pong');
  });
});
