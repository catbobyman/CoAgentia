// M6b 落地事件全局 toast 观察者（landing.*）。useWsSync 在 ToastProvider 之外无法 toast，故经 store
// 的 landing 信号（wsSideEffects.landingSignalKind → pushLanding）桥接到此——本组件在 ToastProvider 内，
// 按信号 id 去重后弹 toast：completed「拆解已落地」/ fail_closed 错误「落地 fail-closed，请查看告警」
// （画布/任务刷新已由 wsBridge 失效收敛）；started 无 toast（confirm 202 的「落地执行中」已覆盖起步）。
import { useEffect, useRef } from 'react';

import { useUiStore } from '../lib/store';
import { useToast } from './Toast';

export function LandingToaster() {
  const landing = useUiStore((s) => s.landing);
  const toast = useToast();
  const lastId = useRef(0);

  useEffect(() => {
    if (!landing || landing.id === lastId.current) return;
    lastId.current = landing.id;
    if (landing.kind === 'completed') {
      toast.push('拆解已落地', { tone: 'success' });
    } else if (landing.kind === 'fail_closed') {
      toast.push('落地 fail-closed，请查看告警', { tone: 'error' });
    }
  }, [landing, toast]);

  return null;
}
