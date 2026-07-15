// B-M8-3 新建频道弹窗（B §4.5 POST /channels）：产品化外壳补齐侧栏「新建频道」死壳的真实入口。
// 字段：名字（必填非空）/ 说明（可选）/ 私有频道开关（默认公开）。复用 create-agent.css 的
// .modal/.field/.inp/.ops 体例（token 零发明）。POST /channels 不自动拉入创建者 → 新建即空频道
// （MVP，成员后续加）。NAME_TAKEN(409) 就地报错不关窗（同 CreateAgentModal 体例）。
import { useState } from 'react';

import type { ChannelPublic } from '@coagentia/contracts-ts';

import { ApiError } from '../api';
import { useCreateChannel } from '../data/queries';
import { useToast } from './Toast';
import './create-agent.css';

export function NewChannelModal({
  onClose, onCreated,
}: {
  onClose: () => void;
  /** 创建成功回调（调用方据此选中/跳转新频道）。 */
  onCreated?: (ch: ChannelPublic) => void;
}) {
  const toast = useToast();
  const createM = useCreateChannel();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [isPrivate, setIsPrivate] = useState(false);
  const [error, setError] = useState<string | undefined>();

  const valid = name.trim() !== '';

  const submit = () => {
    if (!valid || createM.isPending) return;
    setError(undefined);
    createM.mutate(
      {
        name: name.trim(),
        // 说明可空——空字符串不下发（server 忽略缺省），只在有内容时携带。
        ...(description.trim() ? { description: description.trim() } : {}),
        is_private: isPrivate,
        // MVP：不预置成员，创建者亦不自动入频道（POST /channels 语义），需要时后续再加。
        member_ids: [],
      },
      {
        onSuccess: (ch) => {
          toast.push(`已创建频道 #${ch.name}`, { tone: 'success' });
          onCreated?.(ch);
          onClose();
        },
        // NAME_TAKEN / VALIDATION_FAILED 等就地报错不关窗（同 CreateAgentModal 体例）。
        onError: (e: unknown) =>
          setError(e instanceof ApiError ? e.message : '新建频道失败'),
      },
    );
  };

  return (
    <div className="scrim" onClick={onClose}>
      <div
        className="modal create-agent-modal"
        role="dialog"
        aria-label="新建频道"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mtitle">新建频道</div>

        <div className="field">
          <label className="lb" htmlFor="nc-name">名字</label>
          <div className="inp">
            <input
              id="nc-name" className="val" value={name} placeholder="频道名（工作区内唯一）"
              autoFocus
              onChange={(e) => setName(e.target.value)}
            />
          </div>
        </div>

        <div className="field">
          <label className="lb" htmlFor="nc-desc">说明（可选）</label>
          <textarea
            id="nc-desc" className="ca-ta" rows={3} value={description}
            placeholder="这个频道是做什么的"
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>

        <div className="field">
          <label className="lb" htmlFor="nc-private">私有频道</label>
          <div className="nc-toggle">
            <input
              id="nc-private" type="checkbox" checked={isPrivate}
              onChange={(e) => setIsPrivate(e.target.checked)}
            />
            <span className="nc-toggle-txt">仅受邀成员可见（🔒）</span>
          </div>
        </div>

        {error && <div className="ca-error" role="alert">{error}</div>}

        <div className="ops">
          <button type="button" className="btn btn-ghost" onClick={onClose}>取消</button>
          <button
            type="button" className="btn btn-primary"
            disabled={!valid || createM.isPending}
            onClick={submit}
          >
            创建频道
          </button>
        </div>
      </div>
    </div>
  );
}
