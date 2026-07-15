// M7b 部署卡（card_kind==='deployment' 的锚点消息 → 结构化卡片；交互 §12 / B §13.2-13.4）。
// 渲染：状态徽标（queued/running/success/failed，失败态色）+ token 小结行（新账 Σ 四字段 + 覆盖率
// N/M，永不货币）+ 结果行（URL·耗时·退出码，终态显）+ 实时日志（自动跟随、向上滚动即暂停、「↓ 跟随」
// 胶囊）。日志：打开日志视图发 deploy_log 订阅（wsUplink）+ GET 历史翻页；关闭/卸载退订。
// 数据源 = GET /deployments/{id}（useDeployment），WS deployment.updated/deployment.log 实时刷新。
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { ArrowDown, ExternalLink, Rocket } from 'lucide-react';
import { useQueryClient } from '@tanstack/react-query';

import type { DeploymentPublic, DeploymentStatus, TokenSummary, UsageBucket } from '@coagentia/contracts-ts';

import {
  flushDeployLogPending,
  loadDeployLogPage,
  seedDeployLog,
  useDeployLogState,
  useDeployment,
} from '../data/queries';
import { subscribeDeployLog, unsubscribeDeployLog } from '../data/wsUplink';
import { qk } from '../lib/queryKeys';
import './deployment-card.css';

const STATUS_WORD: Record<DeploymentStatus, string> = {
  queued: '排队中', running: '部署中', success: '成功', failed: '失败',
};
const STATUS_VAR: Record<DeploymentStatus, string> = {
  queued: '--text-muted', running: '--accent', success: '--success', failed: '--danger',
};

/** 终态 = {success, failed}（结果行/日志默认折叠的判据）。 */
export function isTerminal(status: DeploymentStatus): boolean {
  return status === 'success' || status === 'failed';
}

/** token 四字段合计（input+output+cache_read+cache_write；永不折算货币，W7）。 */
export function sumTokens(usage: UsageBucket | undefined): number {
  if (!usage) return 0;
  return (usage.input_tokens ?? 0) + (usage.output_tokens ?? 0)
    + (usage.cache_read_tokens ?? 0) + (usage.cache_write_tokens ?? 0);
}

/** 大数缩略：≥1000 → {n.n}k，否则原值（token 计数展示，非货币）。 */
export function fmtTokens(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

/** 部署耗时秒：started_at→finished_at；缺任一端点 → null（不显示耗时）。 */
export function deployDurationSec(started?: string | null, finished?: string | null): number | null {
  if (!started || !finished) return null;
  const a = new Date(started).getTime();
  const b = new Date(finished).getTime();
  if (Number.isNaN(a) || Number.isNaN(b) || b < a) return null;
  return Math.round((b - a) / 1000);
}

/** 秒 → 耗时显示：≥60s 用 {m}m{ss}s，否则 {s}s。 */
export function fmtDuration(sec: number): string {
  if (sec >= 60) {
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${m}m${String(s).padStart(2, '0')}s`;
  }
  return `${sec}s`;
}

/** 滚动容器是否贴底（阈值内视同贴底 → 保持自动跟随）。jsdom/happy-dom 布局不生效，故抽纯函数单测。 */
export function scrolledToBottom(
  el: { scrollTop: number; scrollHeight: number; clientHeight: number },
  threshold = 24,
): boolean {
  return el.scrollHeight - el.clientHeight - el.scrollTop <= threshold;
}

/** token 小结行（新账 Σ 四字段 + 覆盖率 N/M 诚实标注，永不货币）。 */
function TokenSummaryRow({ summary }: { summary: TokenSummary }) {
  const { usage, tasks_reporting } = summary;
  const total = sumTokens(usage);
  const reporting = tasks_reporting?.reporting ?? 0;
  const totalTasks = tasks_reporting?.total ?? 0;
  return (
    <div className="dep-tokens" data-testid="deployment-token-summary">
      <span className="dep-tok-total">Σ {fmtTokens(total)} tok</span>
      <span className="dep-tok-part">输入 {fmtTokens(usage?.input_tokens ?? 0)}</span>
      <span className="dep-tok-part">输出 {fmtTokens(usage?.output_tokens ?? 0)}</span>
      <span className="dep-tok-part">缓存读 {fmtTokens(usage?.cache_read_tokens ?? 0)}</span>
      <span className="dep-tok-part">缓存写 {fmtTokens(usage?.cache_write_tokens ?? 0)}</span>
      <span className="dep-tok-cov" data-testid="deployment-tasks-reporting" title="有 usage 上报的任务数 / 聚合集任务总数（未上报计入分母）">
        上报 {reporting}/{totalTasks}
      </span>
    </div>
  );
}

/** 实时日志视图：自动跟随 + 向上滚动暂停 + 「↓ 跟随」胶囊 + 历史翻页 + 截断提示。 */
function DeploymentLogView({ deploymentId }: { deploymentId: string }) {
  const qc = useQueryClient();
  const logQ = useDeployLogState(deploymentId);
  const state = logQ.data;
  const lines = state?.lines ?? [];
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [following, setFollowing] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);

  // 打开日志视图（R-14）：播种 → **先订阅**（让并发到达的 live 块进 pending 缓冲，不丢帧）→ 拉首页
  // 历史（并入时按 chunk_seq 升序 flush pending 到历史尾部，历史行先于实时块，消解交叠重复）。
  // 卸载/关闭：退订。首页拉取只跑一次（seed 幂等；缓存已播种即视为已初始化）。
  useEffect(() => {
    const already = qc.getQueryData(qk.deploymentLog(deploymentId)) !== undefined;
    seedDeployLog(qc, deploymentId);
    subscribeDeployLog(deploymentId);
    if (!already) {
      // 历史尽力而为：失败也要 flush pending（否则 live 块永卡缓冲不显示）。
      void loadDeployLogPage(qc, deploymentId).catch(() => flushDeployLogPending(qc, deploymentId));
    }
    return () => {
      unsubscribeDeployLog(deploymentId);
    };
  }, [qc, deploymentId]);

  // 新行到达时，若处跟随态则滚到底（happy-dom 无真实布局，scrollTop 赋值无副作用、安全）。
  useEffect(() => {
    if (!following) return;
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines.length, following]);

  const onScroll = useCallback(() => {
    const el = scrollRef.current;
    if (el) setFollowing(scrolledToBottom(el));
  }, []);

  const resumeFollow = useCallback(() => {
    setFollowing(true);
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);

  const loadMore = useCallback(async () => {
    if (state?.nextAfter == null || loadingMore) return;
    setLoadingMore(true);
    try {
      await loadDeployLogPage(qc, deploymentId, state.nextAfter);
    } finally {
      setLoadingMore(false);
    }
  }, [qc, deploymentId, state?.nextAfter, loadingMore]);

  return (
    <div className="dep-logwrap">
      {state?.nextAfter != null && (
        <button
          type="button"
          className="dep-loadmore"
          data-testid="deployment-log-more"
          onClick={() => void loadMore()}
          disabled={loadingMore}
        >
          {loadingMore ? '加载中…' : '加载更多历史'}
        </button>
      )}
      <div
        className="dep-log"
        data-testid="deployment-log"
        role="log"
        aria-label="部署日志"
        ref={scrollRef}
        onScroll={onScroll}
      >
        {lines.length > 0
          ? <pre>{lines.join('\n')}</pre>
          : <div className="dep-log-empty">暂无日志输出。</div>}
      </div>
      {state?.truncated && (
        <div className="dep-log-trunc" role="note">日志超上限已截断（尾部保留）。</div>
      )}
      {!following && (
        <button
          type="button"
          className="dep-follow-pill"
          data-testid="deployment-follow-pill"
          onClick={resumeFollow}
        >
          <ArrowDown />跟随
        </button>
      )}
    </div>
  );
}

export function DeploymentCard({ deploymentId }: { deploymentId: string }) {
  const q = useDeployment(deploymentId);
  const deployment = q.data as DeploymentPublic | undefined;
  // 终态默认折叠日志（历史部署不逐卡订阅）；非终态（queued/running）默认展开跟随实时流。
  const [logOpen, setLogOpen] = useState<boolean | null>(null);
  const effectiveOpen = useMemo(() => {
    if (logOpen != null) return logOpen;
    return deployment ? !isTerminal(deployment.status) : false;
  }, [logOpen, deployment]);

  if (q.isLoading && !deployment) {
    return <div className="deployment-card loading" data-testid="deployment-card">部署卡加载中…</div>;
  }
  if (!deployment) {
    return (
      <div className="deployment-card error" data-testid="deployment-card" role="status">
        部署记录暂不可用（可能已被清理）。
      </div>
    );
  }

  const status = deployment.status;
  const terminal = isTerminal(status);
  const durationSec = deployDurationSec(deployment.started_at, deployment.finished_at);
  const hash6 = deployment.commit_hash ? deployment.commit_hash.slice(0, 7) : null;

  return (
    <div className="deployment-card" data-testid="deployment-card" data-status={status}>
      <div className="dep-head">
        <span className="dep-kind"><Rocket />部署</span>
        <span className="dep-branch" title={deployment.command}>
          {deployment.branch}{hash6 ? `@${hash6}` : ''}
        </span>
        <span className="sp" />
        <span
          className="dep-badge"
          data-testid="deployment-status"
          style={{ '--depst': `var(${STATUS_VAR[status]})` } as unknown as CSSProperties}
        >
          {STATUS_WORD[status]}
        </span>
      </div>

      {deployment.token_summary && <TokenSummaryRow summary={deployment.token_summary} />}

      {terminal && (
        <div className="dep-result" data-testid="deployment-result" data-status={status}>
          {deployment.url
            ? (
              <a className="dep-url" href={deployment.url} target="_blank" rel="noreferrer">
                {deployment.url}<ExternalLink />
              </a>
            )
            : <span className="dep-url none">无 URL</span>}
          {durationSec != null && <span className="dep-dur">耗时 {fmtDuration(durationSec)}</span>}
          <span className="dep-exit" data-testid="deployment-exit-code">
            退出码 {deployment.exit_code ?? '—'}
          </span>
        </div>
      )}

      <button
        type="button"
        className="dep-logtoggle"
        data-testid="deployment-log-toggle"
        onClick={() => setLogOpen(!effectiveOpen)}
        aria-expanded={effectiveOpen}
      >
        {effectiveOpen ? '收起日志' : '展开日志'}
      </button>

      {effectiveOpen && <DeploymentLogView deploymentId={deploymentId} />}
    </div>
  );
}
