// WS 客户端(契约 C §2/§4/§5):应用层心跳 + 指数退避重连 + 连接内 seq 空洞自检 + 重同步骨架。
// - seq 空洞 = 致命 → 立即断开重连(契约 C §3);重连成功(sys.hello)后触发 onResync(§4 步骤 1)。
// - 连接状态通过 onStatus 上报,供顶部 2px 进度条 + toast(交互 §13)。
import type { Envelope } from '@coagentia/contracts-ts';

import { API_BASE } from './api';

export type WsStatus = 'connecting' | 'online' | 'reconnecting';

export interface WsHandlers {
  onEvent: (env: Envelope) => void;
  onStatus?: (status: WsStatus, attempt: number) => void;
  // 契约 C §4:重连成功后的 REST 重同步(首次连接不触发,仅断线重连后触发)。
  onResync?: () => void;
}

export function webSocketUrl(apiBase = API_BASE, origin = window.location.origin): string {
  const url = new URL(`${apiBase}/api/ws`, origin);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  return url.toString();
}

export function connectWs(handlers: WsHandlers): () => void {
  const { onEvent, onStatus, onResync } = handlers;
  let sock: WebSocket | null = null;
  let heartbeat: number | undefined;
  let retryTimer: number | undefined;
  let retryMs = 1000;
  let attempt = 0; // 已发生的重连次数(第 n 次)
  let hadConnection = false; // 是否曾经成功连过(用于区分首连 vs 重连)
  let lastSeq: number | null = null; // 连接内单调 seq;每次新连接重置
  let closed = false;

  const status = (s: WsStatus) => onStatus?.(s, attempt);

  const open = () => {
    if (closed) return;
    lastSeq = null;
    sock = new WebSocket(webSocketUrl());

    sock.onmessage = (raw) => {
      let env: Envelope;
      try {
        env = JSON.parse(raw.data as string) as Envelope;
      } catch {
        return;
      }

      // ---- 连接内 seq 空洞自检(契约 C §3):非首帧且 seq 非 +1 递增 → 致命,断开重连重同步。
      if (typeof env.seq === 'number') {
        if (lastSeq !== null && env.seq !== lastSeq + 1) {
          sock?.close(); // onclose 走重连;重连后 onResync 补齐(§4)
          return;
        }
        lastSeq = env.seq;
      }

      if (env.type === 'sys.hello') {
        retryMs = 1000;
        status('online');
        if (hadConnection) onResync?.(); // 断线重连成功 → 重同步(§4 步骤 1)
        hadConnection = true;
        const sec = (env.data as { heartbeat_sec: number }).heartbeat_sec;
        window.clearInterval(heartbeat);
        heartbeat = window.setInterval(
          () => sock?.readyState === WebSocket.OPEN && sock.send(JSON.stringify({ type: 'ping' })),
          sec * 1000,
        );
      }

      onEvent(env);
    };

    sock.onclose = () => {
      window.clearInterval(heartbeat);
      if (closed) return;
      attempt += 1;
      status('reconnecting');
      window.clearTimeout(retryTimer);
      retryTimer = window.setTimeout(open, retryMs);
      retryMs = Math.min(retryMs * 2, 30_000); // 1s→2s→…封顶 30s(契约 C §2)
    };
  };

  status('connecting');
  open();

  return () => {
    closed = true;
    window.clearInterval(heartbeat);
    window.clearTimeout(retryTimer);
    sock?.close();
  };
}
