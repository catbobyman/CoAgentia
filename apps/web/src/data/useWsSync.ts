// 基座级 WS 生命周期:连接一次,信封 → query 缓存 patch(wsBridge),状态 → zustand(重连 UI),
// 断线重连成功 → REST 重同步(契约 C §4)。挂在布局壳,跨路由常驻。
import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';

import { resyncAll } from './queries';
import { applyEnvelope } from './wsBridge';
import { maybeDesktopNotify } from './desktopNotify';
import { landingSignalKind, reconcileActiveDraft } from './wsSideEffects';
import { connectWs } from '../ws';
import { useUiStore } from '../lib/store';

export function useWsSync() {
  const qc = useQueryClient();
  const setConnection = useUiStore((s) => s.setConnection);

  useEffect(() => {
    const cleanup = connectWs({
      // 先 patch 缓存(事实源),再按频道 mode 决定是否弹桌面通知(纯展示增益,不改缓存);
      // 最后跑 M6b 副作用桥（rev 替换切激活草稿 / 落地事件写 store 信号——本 hook 在 ToastProvider 之外，
      // 经 store 交 <LandingToaster> 弹 toast）。
      onEvent: (env) => {
        applyEnvelope(qc, env);
        maybeDesktopNotify(qc, env);
        const store = useUiStore.getState();
        reconcileActiveDraft(env, qc, {
          getActiveDraft: (channelId) => store.activeDraft[channelId],
          setActiveDraft: store.setActiveDraft,
        });
        const landing = landingSignalKind(env);
        if (landing) store.pushLanding(landing, env.channel_id ?? null);
      },
      onStatus: (status, attempt) => setConnection({ status, attempt }),
      onResync: () => void resyncAll(qc),
    });
    return cleanup;
  }, [qc, setConnection]);
}
