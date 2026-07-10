// 轻量 toast(无第三方依赖):Context 派发 + 自动消失。写路径失败(claim 冲突/流转非法)
// 由 UI 层 catch ApiError 后 push 文案。样式在 toast.css(token 变量,零发明)。
import { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';

import './toast.css';

export type ToastTone = 'info' | 'error' | 'success';

export interface ToastOptions {
  tone?: ToastTone;
  duration?: number; // ms,默认 4000
}

interface ToastItem {
  id: number;
  msg: string;
  tone: ToastTone;
}

interface ToastApi {
  push: (msg: string, opts?: ToastOptions) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

// 单独暴露渲染面:RootLayout 里 <Toaster/> 消费同一 provider 的 toasts。
const ListContext = createContext<ToastItem[]>([]);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const seq = useRef(0);

  const push = useCallback((msg: string, opts?: ToastOptions) => {
    const id = ++seq.current;
    const tone = opts?.tone ?? 'info';
    setToasts((prev) => [...prev, { id, msg, tone }]);
    const duration = opts?.duration ?? 4000;
    window.setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, duration);
  }, []);

  const api = useMemo<ToastApi>(() => ({ push }), [push]);

  return (
    <ToastContext.Provider value={api}>
      <ListContext.Provider value={toasts}>{children}</ListContext.Provider>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast 必须在 <ToastProvider> 内使用');
  return ctx;
}

export function Toaster() {
  const toasts = useContext(ListContext);
  if (toasts.length === 0) return null;
  return (
    <div className="toaster" role="status" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast toast--${t.tone}`}>
          {t.msg}
        </div>
      ))}
    </div>
  );
}
