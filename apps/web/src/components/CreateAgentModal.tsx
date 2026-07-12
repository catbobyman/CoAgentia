// P13 创建 Agent 弹窗（B §11.3 POST /agents；M6b 增角色模板段为 NO_ORCHESTRATOR 引导链服务）。
// 字段：名字 / runtime（claude_code|codex 分段）/ model / 所在机器（MVP 单机预选唯一 computer，
// 同 ProjectSettingsSection 体例）/ description 文本域。
// **角色模板段（可选）**：MVP 唯一项 = Orchestrator（数据源 = contracts 生成的三常量，纪律 7 单源）；
// 选中即把 description 预填为模板话术（用户可改），提交携 role_template_key；不选 = 现行为零变化
// （请求体不含 role_template_key）。preselectRoleKey 供引导链打开时预选（交互 §6.8）。
import { useState } from 'react';

import type { AgentPublic, Runtime } from '@coagentia/contracts-ts';
import {
  ORCHESTRATOR_ROLE_TEMPLATE_DESCRIPTION_PREFILL,
  ORCHESTRATOR_ROLE_TEMPLATE_KEY,
  ORCHESTRATOR_ROLE_TEMPLATE_NAME,
} from '@coagentia/contracts-ts';

import { ApiError } from '../api';
import { useComputers, useCreateAgent } from '../data/queries';
import { useToast } from './Toast';
import './create-agent.css';

// MVP 角色模板清单（唯一项 = Orchestrator）。数据源 = 生成常量；后续多模板时改为 REST 列表。
const ROLE_TEMPLATES: ReadonlyArray<{ key: string; name: string; prefill: string }> = [
  {
    key: ORCHESTRATOR_ROLE_TEMPLATE_KEY,
    name: ORCHESTRATOR_ROLE_TEMPLATE_NAME,
    prefill: ORCHESTRATOR_ROLE_TEMPLATE_DESCRIPTION_PREFILL,
  },
];

const RUNTIME_WORD: Record<string, string> = { claude_code: 'Claude Code', codex: 'Codex' };

export function CreateAgentModal({
  preselectRoleKey, onClose, onCreated,
}: {
  /** 引导链打开时预选的角色模板 key（交互 §6.8「预选 Orchestrator 角色模板」）。 */
  preselectRoleKey?: string;
  onClose: () => void;
  /** 创建成功回调（引导链据此回画布并重新聚焦拆解入口）。 */
  onCreated?: (agent: AgentPublic) => void;
}) {
  const toast = useToast();
  const computersQ = useComputers();
  const createM = useCreateAgent();
  const computers = computersQ.data ?? [];

  const preset = ROLE_TEMPLATES.find((t) => t.key === preselectRoleKey);
  const [name, setName] = useState(preset ? 'Orchestrator' : '');
  const [runtime, setRuntime] = useState<Runtime>('claude_code');
  const [model, setModel] = useState('');
  // MVP 单机：预选唯一 computer（B §4.11 同款「UI 预选、服务端不默认推断」）。
  // computers 可能异步到位——state 只存用户显式选择，未选时派生默认为首台（免 setState-in-render）。
  const [pickedComputerId, setPickedComputerId] = useState('');
  const computerId = pickedComputerId || (computers[0]?.id ?? '');
  const [roleKey, setRoleKey] = useState(preset?.key ?? '');
  const [description, setDescription] = useState(preset?.prefill ?? '');
  const [error, setError] = useState<string | undefined>();

  const valid = name.trim() !== '' && model.trim() !== '' && computerId !== '';

  // 选中模板 → 预填 description（覆盖为模板话术，用户可继续编辑）；切回「不使用」保留已填文本
  // （不清空——用户可能已在模板底稿上加工）。
  const pickRole = (key: string) => {
    setRoleKey(key);
    const tpl = ROLE_TEMPLATES.find((t) => t.key === key);
    if (tpl) setDescription(tpl.prefill);
  };

  const submit = () => {
    if (!valid || createM.isPending) return;
    setError(undefined);
    createM.mutate(
      {
        name: name.trim(),
        runtime,
        model: model.trim(),
        computer_id: computerId,
        description,
        ...(roleKey ? { role_template_key: roleKey } : {}),
      },
      {
        onSuccess: (agent) => {
          toast.push(`已创建 Agent @${name.trim()}`, { tone: 'success' });
          onCreated?.(agent);
          onClose();
        },
        // NAME_TAKEN / VALIDATION_FAILED 等就地报错不关窗（同 ProjectSettingsSection 体例）。
        onError: (e: unknown) =>
          setError(e instanceof ApiError ? e.message : '创建 Agent 失败'),
      },
    );
  };

  return (
    <div className="scrim" onClick={onClose}>
      <div
        className="modal create-agent-modal"
        role="dialog"
        aria-label="创建 Agent"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mtitle">创建 Agent</div>

        <div className="field">
          <label className="lb" htmlFor="ca-name">名字</label>
          <div className="inp">
            <input
              id="ca-name" className="val" value={name} placeholder="Agent 名字（频道内唯一）"
              onChange={(e) => setName(e.target.value)}
            />
          </div>
        </div>

        <div className="field">
          <span className="lb">Runtime</span>
          <div className="ca-seg" role="radiogroup" aria-label="Runtime">
            {(['claude_code', 'codex'] as const).map((rt) => (
              <button
                key={rt} type="button" role="radio" aria-checked={runtime === rt}
                className={runtime === rt ? 'active' : ''}
                onClick={() => setRuntime(rt)}
              >
                {RUNTIME_WORD[rt]}
              </button>
            ))}
          </div>
        </div>

        <div className="field">
          <label className="lb" htmlFor="ca-model">模型</label>
          <div className="inp">
            <input
              id="ca-model" className="val mono" value={model}
              placeholder="模型标识（传给 runtime，如 sonnet）"
              onChange={(e) => setModel(e.target.value)}
            />
          </div>
        </div>

        <div className="field">
          <label className="lb" htmlFor="ca-computer">所在机器</label>
          <select
            id="ca-computer" className="ca-select" aria-label="所在机器"
            value={computerId} onChange={(e) => setPickedComputerId(e.target.value)}
          >
            {computers.length === 0 && <option value="">暂无机器（先去 P7 添加）</option>}
            {computers.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </div>

        {/* 角色模板段（可选，M6b）：选中即预填 description，提交携 role_template_key。 */}
        <div className="field">
          <label className="lb" htmlFor="ca-role">角色模板（可选）</label>
          <select
            id="ca-role" className="ca-select" aria-label="角色模板" data-testid="role-template-select"
            value={roleKey} onChange={(e) => pickRole(e.target.value)}
          >
            <option value="">不使用模板</option>
            {ROLE_TEMPLATES.map((t) => <option key={t.key} value={t.key}>{t.name}</option>)}
          </select>
          {roleKey && (
            <div className="ca-note">已按模板预填成员说明，可在下方修改。</div>
          )}
        </div>

        <div className="field">
          <label className="lb" htmlFor="ca-desc">成员说明（description）</label>
          <textarea
            id="ca-desc" className="ca-ta" rows={4} value={description}
            placeholder="这个 Agent 是做什么的（供成员与 Orchestrator 拆解时参考）"
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>

        {error && <div className="ca-error" role="alert">{error}</div>}

        <div className="ops">
          <button type="button" className="btn btn-ghost" onClick={onClose}>取消</button>
          <button
            type="button" className="btn btn-primary"
            disabled={!valid || createM.isPending}
            onClick={submit}
          >
            创建 Agent
          </button>
        </div>
      </div>
    </div>
  );
}
