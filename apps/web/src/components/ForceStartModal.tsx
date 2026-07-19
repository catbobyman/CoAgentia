// force-start 二次确认弹层(范式:P13b-modals-contract-danger)。DEDAG 后语义收敛为「催动」:
// 人类对拖延/沉默任务直投唤醒负责人 Agent 立即开工(与群聊 @ 互补的强制通道,带审计留痕),
// 故走二次确认(scrim + 红色校验按钮 + 留痕说明)。确认 → POST /tasks/{id}/force-start
// (api.forceStart,走 writeJson);成功 toast「已强制启动,已留痕」,403(非人类)/异常按 code 组
// toast。无乐观更新,任务状态靠 WS task.updated 反流。
import { useState } from 'react';
import { TriangleAlert, Zap } from 'lucide-react';

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
          toast.push('无权强制启动:仅人类可强制启动任务', { tone: 'error' });
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
        <div className="mtitle">强制启动任务</div>
        <div className="fsbody">
          <div className="fstask">
            <Zap />
            <span className="no">#{task.number}</span>
            <span className="ti">{task.title}</span>
          </div>
          <p className="fsdesc">
            将<b>直投唤醒负责人 Agent 立即开工</b>——适用于 @ 无响应或迟迟未动的任务;
            这是绕过常规消息投递节奏的强制通道。
          </p>
          <div className="fswarn" role="alert">
            <TriangleAlert />
            此操作会写入审计留痕(force_start),且不可撤销 —— 请确认你要强制启动。
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
