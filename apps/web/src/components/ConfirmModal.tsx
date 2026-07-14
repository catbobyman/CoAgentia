// 不可撤销动作确认弹窗（交互 §7.3「表单防呆先于报错」）：Full reset / 删频道 / 移除机器共用。
// requireText 非空 = 输入防呆：须逐字键入指定文本（如 Agent/频道名）才解锁确认钮，防误触。
// 复用既有 .scrim/.modal/.field/.ops 体例（AddComputerModal / ChannelSettingsModal 同款）。
import { useState } from 'react';
import type { ReactNode } from 'react';
import { CircleAlert } from 'lucide-react';

import './confirm-modal.css';

export function ConfirmModal({
  title,
  message,
  confirmLabel = '确认',
  danger = false,
  requireText,
  requireTextLabel,
  busy = false,
  onConfirm,
  onClose,
}: {
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  /** 红色确认钮（不可撤销/破坏性动作）。 */
  danger?: boolean;
  /** 非空 = 须逐字键入此文本才解锁确认（防呆）。 */
  requireText?: string;
  requireTextLabel?: string;
  busy?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const [typed, setTyped] = useState('');
  const gateOk = !requireText || typed === requireText;
  const canConfirm = gateOk && !busy;

  return (
    <div className="scrim" onClick={onClose}>
      <div className="modal confirm-modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
        <div className="mtitle">{danger && <CircleAlert className="confirm-danger-ic" />}{title}</div>
        <div className="confirm-msg">{message}</div>
        {requireText && (
          <div className="field">
            <span className="lb">{requireTextLabel ?? `键入 “${requireText}” 以确认`}</span>
            <div className="inp">
              <span className="pr">❯</span>
              <input
                className="val"
                autoFocus
                value={typed}
                placeholder={requireText}
                aria-label="确认输入"
                onChange={(e) => setTyped(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter' && canConfirm) onConfirm(); }}
              />
            </div>
          </div>
        )}
        <div className="ops">
          <button className="btn btn-ghost" onClick={onClose}>取消</button>
          <button
            className={`btn ${danger ? 'btn-danger' : 'btn-primary'}`}
            disabled={!canConfirm}
            onClick={onConfirm}
          >{confirmLabel}</button>
        </div>
      </div>
    </div>
  );
}
