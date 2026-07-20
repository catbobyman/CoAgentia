/** transport 纯函数 + 内存传输桩自检（WS 真连接归实机 verify；契约 D §2）。 */

import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { resetFileLogging, setupFileLogging } from '../src/logconfig.ts';
import { DataPaths } from '../src/paths.ts';
import { TransportClosed, WebSocketTransport, serverUrlToWs } from '../src/transport.ts';
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

describe('WebSocketTransport 非 JSON 帧丢弃（CR 修复批 FIX 9）', () => {
  let tmp: string;

  beforeEach(() => {
    tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'coagentia-transport-'));
    resetFileLogging();
  });

  afterEach(() => {
    resetFileLogging();
    fs.rmSync(tmp, { recursive: true, force: true });
  });

  it('丢弃留痕 warn 落盘、连接不撕（后续帧照常投递）', async () => {
    const paths = new DataPaths(tmp);
    setupFileLogging(paths, 'DEBUG');
    // 最小 ws 桩：捕获监听器直接注入 message 事件（无真 socket；构造器只用 addEventListener 面）。
    type MessageListener = (ev: { data: unknown }) => void;
    const listeners = new Map<string, MessageListener>();
    const fakeWs = {
      addEventListener: (type: string, cb: MessageListener) => {
        listeners.set(type, cb);
      },
    };
    const t = new WebSocketTransport(fakeWs as unknown as WebSocket);
    listeners.get('message')!({ data: 'not-json-frame-原文' });
    listeners.get('message')!({ data: '{"kind":"pong"}' });
    expect(await t.recv()).toEqual({ kind: 'pong' }); // 丢弃不撕语义保持：后续帧照常投递
    const content = fs.readFileSync(paths.logPath, 'utf-8');
    expect(content).toContain('drop non-JSON frame: not-json-frame-原文'); // 丢弃留痕（截 120 字符）
  });
});
