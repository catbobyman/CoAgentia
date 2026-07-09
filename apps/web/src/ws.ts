// WS 客户端（契约 C §2/§5）：应用层心跳 + 指数退避重连 + 信封分发。
import type { Envelope } from '@coagentia/contracts-ts';

export function connectWs(onEvent: (env: Envelope) => void): () => void {
  let sock: WebSocket | null = null;
  let heartbeat: number | undefined;
  let retryMs = 1000;
  let closed = false;

  const open = () => {
    sock = new WebSocket('ws://127.0.0.1:8642/api/ws');
    sock.onmessage = (raw) => {
      const env = JSON.parse(raw.data as string) as Envelope;
      if (env.type === 'sys.hello') {
        retryMs = 1000;
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
      window.setTimeout(open, retryMs);
      retryMs = Math.min(retryMs * 2, 30_000); // 1s→2s→…封顶 30s（契约 C §2）
    };
  };
  open();
  return () => {
    closed = true;
    window.clearInterval(heartbeat);
    sock?.close();
  };
}
