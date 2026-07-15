// M7（B-M7-1）预览面板（交互 §12 / B §13.1 / FR-11）。
// [预览] 按钮（交付卡）：仅当任务有 worktree 且 Project 配 dev_command 才亮；点击 → openPreview 入 deck。
// 面板：iframe 承载 http://127.0.0.1:{port}；顶条三态（starting「启动中…（健康检查）」/ running 加载 iframe /
// failed 显 fail_log_tail 尾 20 行 mono + [重试]）+ 回收倒计时（last_active_at+preview_idle_min 客户端推导，
// 纯展示）。心跳：面板打开期每 60s 重发 POST 推进 last_active_at（关闭即停）。并排多任务对比（FR-11.2）=
// PreviewDeck 读 store.previewTargets 多面板横排，每任务各一。
import { useEffect, useState } from 'react';
import { ExternalLink, Monitor, MonitorPlay, RefreshCw, Timer, X } from 'lucide-react';

import type { PreviewSessionPublic } from '@coagentia/contracts-ts';

import { api } from '../api';
import { qk } from '../lib/queryKeys';
import { useStartPreview, usePreviewSession } from '../data/queries';
import { useUiStore, type PreviewTarget } from '../lib/store';
import { useQueryClient } from '@tanstack/react-query';
import './preview-panel.css';

// 失败日志尾行数（交互 §12）。
const FAIL_TAIL_LINES = 20;
// 心跳间隔（B §13.1 建议 60s，实现默认非契约形状）。
const HEARTBEAT_MS = 60_000;
// 回收倒计时进入「即将回收」高亮的阈值（交互 §12：空闲回收前 2 分钟）。
const RECYCLE_SOON_SEC = 120;

/** [预览] 按钮亮灭条件：任务有 worktree 且 Project 配了非空 dev_command（B §13.1 / handoff DoP）。 */
export function canPreview(hasWorktree: boolean, devCommand?: string | null): boolean {
  return hasWorktree && !!devCommand && devCommand.trim().length > 0;
}

/** 活跃态（starting/running）= 心跳/倒计时生效态；failed/recycled 非活跃（重发 POST = ensure 重建）。 */
function isActive(status?: PreviewSessionPublic['status']): boolean {
  return status === 'starting' || status === 'running';
}

/** 回收倒计时（纯客户端推导）：last_active_at + idleMin 分钟 - now = 剩余秒。
 *  无 last_active_at 或非活跃态 → null（不显示倒计时）。 */
export function previewCountdownSeconds(
  session: PreviewSessionPublic | undefined,
  idleMin: number,
  nowMs: number,
): number | null {
  if (!session?.last_active_at || !isActive(session.status)) return null;
  const deadline = new Date(session.last_active_at).getTime() + idleMin * 60_000;
  return Math.max(0, Math.floor((deadline - nowMs) / 1000));
}

/** 秒 → 倒计时显示：≥1h 用 {h}h{mm}m，否则 {m}:{ss}（同 HeldDraftCard 体例）。 */
function fmtCountdown(sec: number): string {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h > 0) return `${h}h${String(m).padStart(2, '0')}m`;
  return `${m}:${String(s).padStart(2, '0')}`;
}

/** 取文本尾 n 行（fail_log_tail 展示尾 20 行）。 */
export function lastLines(text: string | null | undefined, n: number): string {
  if (!text) return '';
  const lines = text.split('\n');
  return lines.slice(Math.max(0, lines.length - n)).join('\n');
}

/** 每秒读秒的倒计时 hook（本地 setInterval，随 last_active_at/status 变化重算，不依赖 WS 推帧）。 */
function usePreviewCountdown(session: PreviewSessionPublic | undefined, idleMin: number): number | null {
  const compute = () => previewCountdownSeconds(session, idleMin, Date.now());
  const [sec, setSec] = useState<number | null>(compute);
  useEffect(() => {
    setSec(compute()); // last_active_at（心跳推进）/ status 变化时立即重算
    const id = setInterval(() => setSec(compute()), 1000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.last_active_at, session?.status, idleMin]);
  return sec;
}

/** 交付卡 [预览] 按钮：不满足亮灯条件时置灰（title 说明）；亮时点击 → onOpen。 */
export function PreviewButton({
  hasWorktree, devCommand, onOpen,
}: {
  hasWorktree: boolean;
  devCommand?: string | null;
  onOpen: () => void;
}) {
  const enabled = canPreview(hasWorktree, devCommand);
  return (
    <button
      type="button"
      className="preview-btn"
      disabled={!enabled}
      title={enabled ? '打开预览' : '需任务有 worktree 且 Project 配置 dev_command'}
      onClick={onOpen}
    >
      <MonitorPlay />预览
    </button>
  );
}

/** 顶条状态短标（交互 §12 三态 + recycled）。 */
function PreviewStatusLabel({ status, port }: { status?: PreviewSessionPublic['status']; port?: number | null }) {
  if (status === 'running') {
    return <span className="preview-state running">运行中{port != null ? ` · :${port}` : ''}</span>;
  }
  if (status === 'failed') return <span className="preview-state failed">启动失败</span>;
  if (status === 'recycled') return <span className="preview-state recycled">已回收</span>;
  return <span className="preview-state starting">启动中…（健康检查）</span>;
}

/** 失败日志尾（尾 20 行，mono 等宽）。 */
function PreviewFailLog({ tail }: { tail?: string | null }) {
  const text = lastLines(tail, FAIL_TAIL_LINES);
  return (
    <div className="preview-faillog" role="log" aria-label="启动失败日志">
      {text ? <pre>{text}</pre> : <div className="preview-empty">无日志输出。</div>}
    </div>
  );
}

/** 单个任务预览面板：打开即 ensure（POST）、心跳、三态顶条、iframe/失败日志、回收倒计时。 */
export function PreviewPanel({ target, onClose }: { target: PreviewTarget; onClose: () => void }) {
  const { taskId, taskNumber, idleMin } = target;
  const qc = useQueryClient();
  const startM = useStartPreview();
  const sessionQ = usePreviewSession(taskId);
  const session = sessionQ.data;
  const status = session?.status;
  const countdown = usePreviewCountdown(session, idleMin);

  // 面板打开即 ensure（POST 播种缓存 + 触发 daemon 启动）；仅按 taskId 触发一次。
  useEffect(() => {
    startM.mutate(taskId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId]);

  // 心跳：活跃态每 60s 重发 POST 推进 last_active_at；failed/recycled 不心跳（重发=ensure 重建，不该自动做）；
  // 关闭/切非活跃即停（effect cleanup）。心跳尽力而为——失败静默（真失败经 daemon preview.status→failed 反流顶条）。
  const active = isActive(status);
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => {
      void api
        .startPreview(taskId)
        .then((s) => {
          // 心跳只推进存活态：写缓存前复查本地会话仍活跃——防在途心跳（POST 已发出瞬间会话转
          // failed/recycled）覆盖 daemon 的失败反流、把已死进程重新显示为 running（code-review 修）。
          const cur = qc.getQueryData<PreviewSessionPublic>(qk.preview(taskId));
          if (cur && !isActive(cur.status)) return;
          qc.setQueryData<PreviewSessionPublic>(qk.preview(taskId), s);
        })
        .catch(() => {});
    }, HEARTBEAT_MS);
    return () => clearInterval(id);
  }, [active, taskId, qc]);

  // [重试] / [重新启动] = 再次 POST（failed/recycled 非活跃态自然走 ensure 重建，交互 §12）。
  const retry = () => startM.mutate(taskId);

  // F11 关闭面板即回收：人关面板 = 人不看了，主动 DELETE 下发 preview.stop 回收 dev server（省资源，
  // 与 M7 裁决 #11「人开的面板人再点」对称）。失败静默——idle 超时回收兜底仍在。
  const closeAndRecycle = () => {
    void api.stopPreview(taskId).catch(() => {});
    onClose();
  };

  return (
    <section className="preview-panel" aria-label={`预览 #${taskNumber}`} data-status={status ?? 'starting'}>
      <header className="preview-top">
        <Monitor />
        <span className="preview-num">#{taskNumber}</span>
        <PreviewStatusLabel status={status} port={session?.port} />
        {countdown != null && (
          <span
            className="preview-countdown"
            data-testid="preview-countdown"
            data-soon={countdown <= RECYCLE_SOON_SEC}
          >
            <Timer />{fmtCountdown(countdown)} 后回收
          </span>
        )}
        {status === 'running' && session?.port != null && (
          <a
            className="preview-open"
            href={`http://127.0.0.1:${session.port}`}
            target="_blank"
            rel="noreferrer"
            title="在新标签打开"
          >
            <ExternalLink />
          </a>
        )}
        {(status === 'failed' || status === 'recycled') && (
          <button type="button" className="preview-retry" onClick={retry} disabled={startM.isPending}>
            <RefreshCw />{status === 'recycled' ? '重新启动' : '重试'}
          </button>
        )}
        <button type="button" className="preview-close" aria-label="关闭预览" onClick={closeAndRecycle}>
          <X />
        </button>
      </header>

      <div className="preview-body">
        {status === 'running' && session?.port != null ? (
          <iframe
            className="preview-frame"
            title={`任务 #${taskNumber} 预览`}
            src={`http://127.0.0.1:${session.port}`}
          />
        ) : status === 'failed' ? (
          <PreviewFailLog tail={session?.fail_log_tail} />
        ) : status === 'recycled' ? (
          <div className="preview-placeholder">已回收 · 空闲超时。点「重新启动」再次预览。</div>
        ) : (
          <div className="preview-placeholder starting">
            <Monitor />启动中…（健康检查）
          </div>
        )}
      </div>
    </section>
  );
}

/** 并排预览 deck（FR-11.2）：读 store.previewTargets 横排多面板，每任务各一；空则不渲染。 */
export function PreviewDeck() {
  const targets = useUiStore((s) => s.previewTargets);
  const closePreview = useUiStore((s) => s.closePreview);
  if (targets.length === 0) return null;
  return (
    <div className="preview-deck" role="region" aria-label="预览面板">
      {targets.map((t) => (
        <PreviewPanel key={t.taskId} target={t} onClose={() => closePreview(t.taskId)} />
      ))}
    </div>
  );
}
