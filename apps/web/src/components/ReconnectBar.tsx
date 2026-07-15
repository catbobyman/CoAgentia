// WS 重连 UI(契约 C §2 / 交互 §13):顶部 2px --warning 进度条 + toast「重连中…(第 n 次)」。
// 数据源 = zustand connection 态(由 ws.ts onStatus 上报)。
import { useUiStore } from '../lib/store';

export function ReconnectBar() {
  const { status, attempt } = useUiStore((s) => s.connection);
  if (status === 'online') return null;

  const reconnecting = status === 'reconnecting';
  const label = reconnecting
    ? `重连中…(第 ${attempt} 次)`
    : '连接中…';

  return (
    <>
      <div className="wsbar" role="progressbar" aria-label={label} />
      <div className="wstoast" role="status">{label}</div>
    </>
  );
}
