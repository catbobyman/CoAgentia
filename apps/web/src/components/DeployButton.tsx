// M7b [部署] 按钮 + 确认弹窗（交互 §12 / B §13.2）。R8 全员含 Agent 可点（无角色门）；点击 →
// 确认弹窗（显示 branch@hash、deploy command、触发者）→ 空体 POST 触发部署。branch@hash 由 server
// 触发时解析主干 HEAD（前端无 HEAD 端点），弹窗以「主干 HEAD（部署时解析）」标注。进行中再次触发
// → 409 DEPLOY_IN_PROGRESS → toast「上一次部署进行中」（useTriggerDeploy 内据 code 分派）；无
// deploy_command → 弹窗内按钮禁用 + 提示（server 兜底 422）。
import { useState } from 'react';
import { Rocket, TriangleAlert } from 'lucide-react';

import { useTriggerDeploy } from '../data/queries';
import './deployment-card.css';

/** [部署] 按钮：R8 全员可点（始终可点，配置/串行判定归弹窗与 server）。 */
export function DeployButton({
  projectId, deployCommand, triggererName, onDeployed,
}: {
  projectId: string | undefined;
  deployCommand?: string | null;
  triggererName?: string;
  /** 触发成功回调（可选）：如切页签展示部署卡。 */
  onDeployed?: (deploymentId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const triggerM = useTriggerDeploy();
  const disabled = !projectId;

  const confirm = () => {
    if (!projectId) return;
    triggerM.mutate(projectId, {
      onSuccess: (dep) => {
        setOpen(false);
        onDeployed?.(dep.id);
      },
    });
  };

  return (
    <>
      <button
        type="button"
        className="btn btn-ghost deploy-btn"
        data-testid="deploy-btn"
        disabled={disabled}
        title={disabled ? '该频道未绑定 Project，无法部署' : '触发一次部署（全员含 Agent，R8）'}
        onClick={() => setOpen(true)}
      >
        <Rocket /> 部署
      </button>
      {open && projectId && (
        <DeployConfirmModal
          deployCommand={deployCommand}
          triggererName={triggererName}
          busy={triggerM.isPending}
          onCancel={() => setOpen(false)}
          onConfirm={confirm}
        />
      )}
    </>
  );
}

/** 确认弹窗：展示 branch@hash（部署时解析）/ deploy command / 触发者 + 二次确认（部署不可撤销）。 */
export function DeployConfirmModal({
  deployCommand, triggererName, busy, onCancel, onConfirm,
}: {
  deployCommand?: string | null;
  triggererName?: string;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const hasCommand = !!deployCommand && deployCommand.trim().length > 0;
  return (
    <div className="scrim" onClick={onCancel} data-testid="deploy-confirm">
      <div className="modal deploy-modal" onClick={(e) => e.stopPropagation()}>
        <div className="mtitle"><Rocket /> 确认部署</div>
        <div className="deploy-confirm-body">
          <dl className="deploy-fields">
            <dt>分支</dt>
            <dd data-testid="deploy-branch">主干 HEAD（部署时由 server 解析 branch@hash）</dd>
            <dt>部署命令</dt>
            <dd className="mono" data-testid="deploy-command">
              {hasCommand ? deployCommand : <span className="deploy-nocmd">（未配置部署命令）</span>}
            </dd>
            <dt>触发者</dt>
            <dd data-testid="deploy-triggerer">{triggererName ?? '当前成员'}</dd>
          </dl>
          {!hasCommand && (
            <div className="deploy-warn" role="alert">
              <TriangleAlert />
              该 Project 未配置部署命令，无法部署。请先在 Project 设置中配置。
            </div>
          )}
          <p className="deploy-note">部署不可撤销（如需回退，可再次部署旧版本）。同 Project 串行，进行中再触发将排队拒绝。</p>
        </div>
        <div className="ops">
          <button className="btn btn-ghost" onClick={onCancel} disabled={busy}>取消</button>
          <button
            className="btn btn-primary"
            data-testid="deploy-confirm-btn"
            disabled={busy || !hasCommand}
            onClick={onConfirm}
          >
            {busy ? '触发中…' : '确认部署'}
          </button>
        </div>
      </div>
    </div>
  );
}
