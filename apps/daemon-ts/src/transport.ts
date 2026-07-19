/**
 * WS 传输抽象（契约 D §2 接入；对等基准 = apps/daemon transport.py）。
 *
 * client 只依赖 send/recv/close 三方法 → 单元测试可注入内存传输（RecordingTransport），
 * 集成用 wsConnect 连真 server /api/daemon/ws。连接关闭统一抛 TransportClosed
 * （client 据此进入指数退避重连）。
 *
 * 校准条款（任务书 §4）：原生 WebSocket（undici）；自定义头走 `{ headers }` 扩展（cal7）；
 * 断开唯一触发 = close 事件（cal4：对端强杀只发 close/1006，不依赖 error 先行）。
 */

import { DAEMON_WS_PATH } from './generated/constants.ts';

export type JsonObject = Record<string, unknown>;

/** 传输已关闭（连接断开 / 主动关闭）——client 据此重连。 */
export class TransportClosed extends Error {}

export interface Transport {
  send(frame: JsonObject): Promise<void>;
  recv(): Promise<JsonObject>;
  close(): Promise<void>;
}

/** 原生 WebSocket 连接包装（JSON 文本帧，契约 D §3）：入帧队列化供拉式 recv。 */
export class WebSocketTransport implements Transport {
  private queue: JsonObject[] = [];
  private waiter: { resolve: (f: JsonObject) => void; reject: (e: Error) => void } | null = null;
  private closed = false;

  private readonly ws: WebSocket;

  constructor(ws: WebSocket) {
    this.ws = ws;
    ws.addEventListener('message', (ev: MessageEvent) => {
      let frame: JsonObject;
      try {
        const raw = typeof ev.data === 'string' ? ev.data : Buffer.from(ev.data as ArrayBuffer).toString('utf-8');
        frame = JSON.parse(raw) as JsonObject;
      } catch {
        return; // 非 JSON 帧丢弃（py json.loads 抛错撕连接；server 不发非 JSON，此处防御性丢弃不撕）
      }
      if (this.waiter !== null) {
        const w = this.waiter;
        this.waiter = null;
        w.resolve(frame);
      } else {
        this.queue.push(frame);
      }
    });
    ws.addEventListener('close', (ev: CloseEvent) => {
      this.closed = true;
      if (this.waiter !== null) {
        const w = this.waiter;
        this.waiter = null;
        w.reject(new TransportClosed(`code=${ev.code} reason=${ev.reason}`));
      }
    });
    // error 事件仅记录性质；关闭统一由 close 事件驱动（cal4）
    ws.addEventListener('error', () => {});
  }

  async send(frame: JsonObject): Promise<void> {
    if (this.closed || this.ws.readyState !== WebSocket.OPEN) {
      throw new TransportClosed('websocket not open');
    }
    this.ws.send(JSON.stringify(frame));
  }

  async recv(): Promise<JsonObject> {
    const next = this.queue.shift();
    if (next !== undefined) return next;
    if (this.closed) throw new TransportClosed('websocket closed');
    return new Promise<JsonObject>((resolve, reject) => {
      this.waiter = { resolve, reject };
    });
  }

  async close(): Promise<void> {
    if (!this.closed) {
      try {
        this.ws.close();
      } catch {
        // 已断开等：与 py suppress(ConnectionClosed, OSError) 同款
      }
    }
  }
}

/** http(s)://host:port → ws(s)://host:port/api/daemon/ws（契约 D §2 WS 端点）。 */
export function serverUrlToWs(serverUrl: string): string {
  const u = new URL(serverUrl);
  const scheme = u.protocol === 'https:' || u.protocol === 'wss:' ? 'wss' : 'ws';
  return `${scheme}://${u.host}${DAEMON_WS_PATH}`;
}

/** 连 /api/daemon/ws，携带 Authorization: Bearer（daemon 是真客户端，契约 D §2）；open 超时 10s。 */
export async function wsConnect(serverUrl: string, apiKey: string): Promise<WebSocketTransport> {
  const wsUrl = serverUrlToWs(serverUrl);
  // undici 扩展：第二参 { headers }（cal7 实测 Authorization 完好到达）
  const ws = new WebSocket(wsUrl, {
    headers: { Authorization: `Bearer ${apiKey}` },
  } as unknown as string[]);
  ws.binaryType = 'arraybuffer';
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => {
      try {
        ws.close();
      } catch {
        // 忽略：连接尚未建立时 close 可抛
      }
      reject(new TransportClosed('open timeout'));
    }, 10_000);
    ws.addEventListener('open', () => {
      clearTimeout(timer);
      resolve();
    });
    ws.addEventListener('close', (ev: CloseEvent) => {
      clearTimeout(timer);
      reject(new TransportClosed(`code=${ev.code} reason=${ev.reason}`));
    });
  });
  return new WebSocketTransport(ws);
}
