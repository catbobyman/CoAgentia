// M3b force-start 二次确认弹层(范式:P13b-modals-contract-danger)。人类越过 gating 强制启动
// blocked 任务 —— 危险操作,故走二次确认(scrim + 红色校验按钮 + 留痕说明)。确认 → POST
// /tasks/{id}/force-start(api.forceStart,走 writeJson);成功 toast「已强制启动,已留痕」,
// 403(非人类 owner)/异常按 code 组 toast。无乐观更新,任务状态靠 WS task.updated 反流。
import { useState } from 'react';
import { Lock, TriangleAlert } from 'lucide-react';

import type { TaskPublic } from '@coagentia/contracts-ts';

import { api, ApiError } from '../api';
import { useToast } from './Toast';
import './force-start.css';

export function ForceStartModal({ task, onClose }: {
  task: TaskPublic;
  onClose: () => void;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  const confirm = () => {
    setBusy(true);
    void api
      .forceStart(task.id)
      .then(() => {
        // 成功回灌靠 WS task.updated(无乐观更新);留痕由 server 写 task_events(force_start)。
        toast.push('已强制启动,已留痕', { tone: 'success' });
        onClose();
      })
      .catch((e: unknown) => {
        if (e instanceof ApiError && e.status === 403) {
          toast.push('无权强制启动:仅人类 owner 可越过 gating', { tone: 'error' });
        } else if (e instanceof ApiError) {
          toast.push(e.message, { tone: 'error' });
        } else {
          toast.push('强制启动失败', { tone: 'error' });
        }
        setBusy(false);
      });
  };

  return (
    <div className="scrim" onClick={onClose} data-testid="force-start-confirm">
      <div className="modal fsmodal" onClick={(e) => e.stopPropagation()}>
        <div className="mtitle">强制启动 blocked 任务</div>
        <div className="fsbody">
          <div className="fstask">
            <Lock />
            <span className="no">#{task.number}</span>
            <span className="ti">{task.title}</span>
          </div>
          <p className="fsdesc">
            该任务当前被上游依赖阻塞(blocked)。强制启动将<b>越过依赖门禁(gating)</b>直接放行,
            上游未完成的产物不会被等待。
          </p>
          <div className="fswarn" role="alert">
            <TriangleAlert />
            此操作会写入审计留痕(force_start),且不可撤销 —— 请确认你要越过 gating。
          </div>
        </div>
        <div className="ops">
          <button className="btn btn-ghost" onClick={onClose} disabled={busy}>取消</button>
          <button
            className="btn btn-danger"
            data-testid="force-start-confirm-btn"
            disabled={busy}
            onClick={confirm}
          >
            确认强制启动
          </button>
        </div>
      </div>
    </div>
  );
}
