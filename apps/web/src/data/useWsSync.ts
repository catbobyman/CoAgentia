// 基座级 WS 生命周期:连接一次,信封 → query 缓存 patch(wsBridge),状态 → zustand(重连 UI),
// 断线重连成功 → REST 重同步(契约 C §4)。挂在布局壳,跨路由常驻。
import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';

import { resyncAll } from './queries';
import { applyEnvelope } from './wsBridge';
import { connectWs } from '../ws';
import { useUiStore } from '../lib/store';

export function useWsSync() {
  const qc = useQueryClient();
  const setConnection = useUiStore((s) => s.setConnection);

  useEffect(() => {
    const cleanup = connectWs({
      onEvent: (env) => applyEnvelope(qc, env),
      onStatus: (status, attempt) => setConnection({ status, attempt }),
      onResync: () => void resyncAll(qc),
    });
    return cleanup;
  }, [qc, setConnection]);
}
