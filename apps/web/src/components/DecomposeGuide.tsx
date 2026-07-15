// M6b 拆解入口共用件（交互 §6.8 / 拆解设计 §4 T2/T3）：
// - useDecompose：POST /channels/{id}/decompose 的请求分派——202 走 onProposal；
//   409 NO_ORCHESTRATOR → 引导态；503 DAEMON_OFFLINE → 离线引导态；其余结构化错误 toast。
// - DecomposeGuideModals：两类引导弹窗 + 创建 Agent 弹窗链（预选 Orchestrator 角色模板）。
//   创建完成 → onOrchestratorCreated（调用方回画布并重新聚焦拆解入口）。
// 判定语义单点：是否有 Orchestrator/是否离线由 server 裁决（前端不预判成员表，只分派错误码）。
import { useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { CircleAlert, WifiOff } from 'lucide-react';

import type { DecomposeRequest, ProposalPublic } from '@coagentia/contracts-ts';
import { ORCHESTRATOR_ROLE_TEMPLATE_KEY } from '@coagentia/contracts-ts';

import { api, ApiError } from '../api';
import { useToast } from './Toast';
import { CreateAgentModal } from './CreateAgentModal';
import './decompose-guide.css';

export type DecomposeGuideState = null | 'no_orchestrator' | 'offline';

/** 拆解请求 hook：分派 202/409/503；guide 态交给 <DecomposeGuideModals> 渲染。 */
export function useDecompose(channelId: string, onProposal: (p: ProposalPublic) => void) {
  const toast = useToast();
  const [guide, setGuide] = useState<DecomposeGuideState>(null);
  const [busy, setBusy] = useState(false);

  const request = async (body: DecomposeRequest): Promise<boolean> => {
    if (busy) return false;
    setBusy(true);
    try {
      const proposal = await api.decompose(channelId, body);
      onProposal(proposal);
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.code === 'NO_ORCHESTRATOR') {
        setGuide('no_orchestrator'); // 交互 §6.8：入口不隐藏，点击弹创建引导
      } else if (e instanceof ApiError && e.code === 'DAEMON_OFFLINE') {
        setGuide('offline'); // 有 Orchestrator 但机器断连 → 引导去 P7
      } else {
        toast.push(e instanceof ApiError ? e.message : '发起拆解失败', { tone: 'error' });
      }
      return false;
    } finally {
      setBusy(false);
    }
  };

  return { request, busy, guide, setGuide };
}

export function DecomposeGuideModals({
  guide, channelId, onClose, onOrchestratorCreated,
}: {
  guide: DecomposeGuideState;
  /** 创建完成后把新 Orchestrator 拉进本频道（find_orchestrator 判定按频道成员，B §4.10）。 */
  channelId: string;
  onClose: () => void;
  /** 创建 Orchestrator 完成（已入频道）→ 回画布重新聚焦拆解入口（交互 §6.8）。 */
  onOrchestratorCreated: () => void;
}) {
  const toast = useToast();
  // 引导链两级：guide 弹窗 → [创建 Orchestrator] → 创建 Agent 弹窗（预选角色模板）。
  const [creating, setCreating] = useState(false);

  if (guide === null) return null;

  if (creating) {
    return (
      <CreateAgentModal
        preselectRoleKey={ORCHESTRATOR_ROLE_TEMPLATE_KEY}
        onClose={() => { setCreating(false); onClose(); }}
        onCreated={(agent) => {
          setCreating(false);
          onClose();
          // 入频道后 decompose 的 NO_ORCHESTRATOR 判定才会通过；失败（如并发已加入）不阻断回聚焦。
          void api
            .addChannelMember(channelId, agent.member_id)
            .catch((e: unknown) => {
              toast.push(
                e instanceof ApiError ? `拉入频道失败:${e.message}` : '拉入频道失败',
                { tone: 'error' },
              );
            })
            .finally(() => onOrchestratorCreated());
        }}
      />
    );
  }

  if (guide === 'no_orchestrator') {
    return (
      <div className="scrim" onClick={onClose}>
        <div
          className="modal decompose-guide"
          role="dialog"
          aria-label="需要协调 Agent"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="mtitle"><CircleAlert className="dg-ico" />本频道还没有协调 Agent</div>
          <div className="dg-body">
            拆解需要一个 Orchestrator 角色的 Agent 来产出任务 DAG 提案。创建后即可回到画布继续拆解。
          </div>
          <div className="ops">
            <button type="button" className="btn btn-ghost" onClick={onClose}>取消</button>
            <button
              type="button" className="btn btn-primary" data-testid="create-orchestrator"
              onClick={() => setCreating(true)}
            >
              创建 Orchestrator
            </button>
          </div>
        </div>
      </div>
    );
  }

  // guide === 'offline'
  return <OrchestratorOfflineModal onClose={onClose} />;
}

// 单独子组件：useNavigate 需要 Router 上下文——只在 offline 分支挂载，宿主（ThreadPanel/CanvasTab）
// 在 guide=null 时零 router 依赖（测试无需 RouterProvider）。
function OrchestratorOfflineModal({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  return (
    <div className="scrim" onClick={onClose}>
      <div
        className="modal decompose-guide"
        role="dialog"
        aria-label="Orchestrator 离线"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mtitle"><WifiOff className="dg-ico" />@Orchestrator 当前离线（机器断连）</div>
        <div className="dg-body">
          Orchestrator 所在机器的 daemon 未连接，拆解请求无法送达。请到机器页检查连接状态。
        </div>
        <div className="ops">
          <button type="button" className="btn btn-ghost" onClick={onClose}>知道了</button>
          <button
            type="button" className="btn btn-primary" data-testid="goto-computers"
            onClick={() => { onClose(); void navigate({ to: '/computers' }); }}
          >
            去机器页检查
          </button>
        </div>
      </div>
    </div>
  );
}

/** T3 画布工具栏入口的需求文本弹窗：输入一句话需求 → POST {text}。 */
export function DecomposeTextModal({
  busy, onSubmit, onClose,
}: {
  busy: boolean;
  onSubmit: (text: string) => void;
  onClose: () => void;
}) {
  const [text, setText] = useState('');
  const valid = text.trim() !== '';
  return (
    <div className="scrim" onClick={onClose}>
      <div
        className="modal decompose-guide"
        role="dialog"
        aria-label="发起拆解"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mtitle">发起拆解</div>
        <div className="dg-body">
          描述一句话需求：系统会代发需求消息并转为 source 任务，@Orchestrator 在其线程内产出拆解提案。
        </div>
        <textarea
          className="dg-ta" rows={3} value={text} autoFocus
          aria-label="需求描述"
          placeholder="例：做一个番茄钟 Web 应用，含统计页"
          onChange={(e) => setText(e.target.value)}
        />
        <div className="ops">
          <button type="button" className="btn btn-ghost" onClick={onClose}>取消</button>
          <button
            type="button" className="btn btn-primary" data-testid="decompose-submit"
            disabled={!valid || busy}
            onClick={() => onSubmit(text.trim())}
          >
            发起拆解
          </button>
        </div>
      </div>
    </div>
  );
}
