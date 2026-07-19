// DEDAG [合并到主干] 按钮 + 确认弹窗（B v1.6 §14；照 DeployButton/ForceStartModal 先例）。
// 显示条件 = 任务 done 且 writes_code；有可合并 worktree（active；conflicted 重触发 = 冲突解决后的
// retry，server 同端点放行）才可点。确认 → POST /tasks/{id}/merge（202 异步）：status=accepted →
// toast「已受理…」+ 按钮置 pending 态（合并中…），合并结果以频道系统消息回报 + worktree.updated
// 反流（wsBridge 既有 patch taskDetail.worktree，零新 WS 订阅——merged/conflicted 终态接管展示）；
// status=merged → 幂等命中，直接呈已合并态。错误 toast 归 useMergeTask（409 同 Project 串行/
// 503 daemon 离线/其余既有通道）。
import { useEffect, useState } from 'react';
import { GitMerge, TriangleAlert } from 'lucide-react';

import type { TaskPublic, WorktreePublic } from '@coagentia/contracts-ts';

import { useMergeTask } from '../data/queries';
import { useToast } from './Toast';
import './diff-card.css';
import './deployment-card.css';

export function MergeButton({ task, worktree }: {
  task: TaskPublic;
  worktree?: WorktreePublic | null;
}) {
  const toast = useToast();
  const mergeM = useMergeTask();
  const [open, setOpen] = useState(false);
  // 202 accepted 后的本地 pending 态（等 worktree.updated 反流终态接管展示）。
  const [pending, setPending] = useState(false);
  // 202 status=merged（幂等命中）：详情缓存由 useMergeTask invalidate 收敛，本地即时呈已合并态。
  const [localMerged, setLocalMerged] = useState(false);

  // worktree 状态反流（merged/conflicted）→ 清 pending：conflicted 让按钮回到可重试态，
  // merged 由已合并分支接管（pending 残留无害但一并清）。
  const wtStatus = worktree?.status;
  useEffect(() => {
    setPending(false);
  }, [wtStatus]);

  // 显隐门：done 且 writes_code 的任务才有合并动作（其余任务不渲染本按钮）。
  if (task.status !== 'done' || !task.writes_code) return null;

  const merged = wtStatus === 'merged' || localMerged;
  // 可合并 = active worktree；conflicted 重触发 = 冲突解决后的 retry（与 server 语义对齐）。
  const mergeable = wtStatus === 'active' || wtStatus === 'conflicted';

  if (merged) {
    return (
      <button
        type="button"
        className="merge-btn"
        data-testid="merge-btn"
        disabled
        title="该任务的 worktree 已合入主干"
      >
        <GitMerge />已合并
      </button>
    );
  }

  const confirm = () => {
    mergeM.mutate(task.id, {
      onSuccess: (res) => {
        setOpen(false);
        if (res.status === 'merged') {
          setLocalMerged(true);
          toast.push('该任务已合并到主干', { tone: 'success' });
          return;
        }
        setPending(true);
        toast.push('已受理，合并结果将以频道系统消息回报', { tone: 'success' });
      },
      // 错误不关窗（toast 由 useMergeTask 弹出），可改期再试或取消。
    });
  };

  return (
    <>
      <button
        type="button"
        className="merge-btn"
        data-testid="merge-btn"
        disabled={pending || !mergeable}
        title={
          pending
            ? '合并已受理，执行中'
            : mergeable
              ? '把该任务的 worktree 合入主干（202 异步）'
              : '暂无可合并的 worktree'
        }
        onClick={() => setOpen(true)}
      >
        <GitMerge />{pending ? '合并中…' : '合并到主干'}
      </button>
      {open && (
        <div className="scrim" onClick={() => setOpen(false)} data-testid="merge-confirm">
          <div className="modal deploy-modal" onClick={(e) => e.stopPropagation()}>
            <div className="mtitle"><GitMerge /> 合并到主干</div>
            <div className="deploy-confirm-body">
              <dl className="deploy-fields">
                <dt>任务</dt>
                <dd data-testid="merge-task">#{task.number} {task.title}</dd>
                <dt>分支</dt>
                <dd className="mono" data-testid="merge-branch">{worktree?.branch ?? '—'}</dd>
              </dl>
              {wtStatus === 'conflicted' && (
                <div className="deploy-warn" role="alert">
                  <TriangleAlert />
                  上次合并有冲突。请确认冲突已在 worktree 内解决后再重试。
                </div>
              )}
              <p className="deploy-note">
                合并为异步执行（202 受理），结果将以频道系统消息回报；同 Project 串行，进行中再触发将被拒绝。
              </p>
            </div>
            <div className="ops">
              <button className="btn btn-ghost" onClick={() => setOpen(false)} disabled={mergeM.isPending}>取消</button>
              <button
                className="btn btn-primary"
                data-testid="merge-confirm-btn"
                disabled={mergeM.isPending}
                onClick={confirm}
              >
                {mergeM.isPending ? '触发中…' : '确认合并'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
