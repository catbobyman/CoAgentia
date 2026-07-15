// PS-WT ② 工作树管理台（独立顶级屏，仿 ComputersScreen；路由 /worktrees）。列全部任务 worktree，
// 孤儿/丢失漂移态浮出，只读 + 清理（merge 永不入管理台，状态机单一入口）。数据 = GET /worktrees：
// 进屏 live=0 秒出 DB 骨架 → 自动跟发 live=1 补实时对账（dirty/落后度 + 孤儿）；不轮询，靠
// worktree.updated WS 失效兜底刷新（wsBridge 前缀失效 qk.worktreesConsole）。
// 清理按钮仅 merged/conflicted 行与孤儿行（active 永不给，裁决 #10）；确认弹窗明列将删目录绝对路径 +
// 分支名。该机离线 → 行保 DB 态、清理禁用 + tooltip。
import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { GitBranch, RefreshCw, Trash2 } from 'lucide-react';

import type { WorktreeConsoleItem } from '@coagentia/contracts-ts';

import { api, ApiError } from '../api';
import { qk } from '../lib/queryKeys';
import { useCleanupOrphan, useCleanupWorktree, useWorktreesConsole } from '../data/queries';
import { useUiStore } from '../lib/store';
import { useToast } from '../components/Toast';
import { ConfirmModal } from '../components/ConfirmModal';
import { DiffCard } from '../components/DiffCard';
import { relTime } from '../lib/time';
import './worktrees.css';

// 状态徽标：派生态（孤儿/丢失）优先于 DB 态；否则映射四个 DB 态。
function statusBadge(item: WorktreeConsoleItem): { label: string; cls: string } {
  if (item.derived === 'orphan') return { label: '孤儿', cls: 'orphan' };
  if (item.derived === 'missing') return { label: '丢失', cls: 'missing' };
  switch (item.status) {
    case 'active': return { label: '活跃', cls: 'active' };
    case 'merged': return { label: '已合并', cls: 'merged' };
    case 'conflicted': return { label: '冲突', cls: 'conflicted' };
    case 'cleaned': return { label: '已清理', cls: 'cleaned' };
    default: return { label: '—', cls: 'unknown' };
  }
}

// 清理按钮资格：孤儿行（需 task_id 可定位）+ merged/conflicted 登记行；active/cleaned 永不给。
function canCleanup(item: WorktreeConsoleItem): boolean {
  if (item.derived === 'orphan') return item.task_id != null;
  return item.status === 'merged' || item.status === 'conflicted';
}

const SCAN_WORD: Record<string, string> = { ok: '在线', offline: '离线', timeout: '超时' };

export function WorktreesScreen() {
  const navigate = useNavigate();
  const setActiveChannel = useUiStore((s) => s.setActiveChannel);
  const toast = useToast();

  // 进屏 live=0 秒出骨架 → useEffect 跟发 live=1（placeholderData 保上一态不闪空）。
  const [live, setLive] = useState<0 | 1>(0);
  const consoleQ = useWorktreesConsole(live);
  useEffect(() => { setLive(1); }, []);

  // 机器名映射（扫描状态条显示名而非 id）。
  const computersQ = useQuery({ queryKey: qk.computers(), queryFn: () => api.computers() });
  const computerName = useMemo(
    () => Object.fromEntries((computersQ.data ?? []).map((c) => [c.id, c.name])),
    [computersQ.data],
  );

  const cleanupWtM = useCleanupWorktree();
  const cleanupOrphanM = useCleanupOrphan();

  const [confirmItem, setConfirmItem] = useState<WorktreeConsoleItem | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [cleanedOpen, setCleanedOpen] = useState<Record<string, boolean>>({});

  const reply = consoleQ.data;
  const items = reply?.items ?? [];
  const scans = reply?.scans ?? [];
  const scanStatusOf = (computerId: string): string | undefined =>
    scans.find((s) => s.computer_id === computerId)?.status;
  const isOffline = (computerId: string): boolean => {
    const st = scanStatusOf(computerId);
    return st === 'offline' || st === 'timeout';
  };

  const rowKey = (item: WorktreeConsoleItem): string =>
    item.id ?? `orphan:${item.project_id}:${item.task_id ?? '?'}:${item.path}`;

  // 按项目分组（project_id → { name, active[], cleaned[] }），保原顺序。
  const groups = useMemo(() => {
    const map = new Map<string, { name: string; active: WorktreeConsoleItem[]; cleaned: WorktreeConsoleItem[] }>();
    for (const item of items) {
      let g = map.get(item.project_id);
      if (!g) { g = { name: item.project_name, active: [], cleaned: [] }; map.set(item.project_id, g); }
      if (item.status === 'cleaned') g.cleaned.push(item);
      else g.active.push(item);
    }
    return [...map.entries()];
  }, [items]);

  const openTask = (item: WorktreeConsoleItem) => {
    if (!item.channel_id || !item.task_id) return;
    setActiveChannel(item.channel_id);
    void navigate({ to: '/', search: { tab: 'chat', task: item.task_id } });
  };

  const rescan = () => {
    setLive(1);
    void consoleQ.refetch();
  };

  const cleanupErrorCopy = (e: unknown): string => {
    if (e instanceof ApiError && e.code === 'WORKTREE_NOT_TERMINAL') return '该 worktree 未处于可清理状态（走任务流程）';
    if (e instanceof ApiError && e.code === 'WORKTREE_PREVIEW_ACTIVE') return '预览正在运行，请先停止预览再清理';
    if (e instanceof ApiError && e.code === 'WORKTREE_NOT_ORPHAN') return '该目录仍有登记记录，不是孤儿';
    if (e instanceof ApiError && e.code === 'DAEMON_OFFLINE') return 'daemon 离线，无法清理';
    return e instanceof ApiError ? e.message : '清理失败';
  };

  const confirmCleanup = () => {
    const item = confirmItem;
    if (!item) return;
    const handlers = {
      onSuccess: () => { setConfirmItem(null); toast.push('已清理 worktree', { tone: 'success' }); },
      onError: (e: unknown) => { setConfirmItem(null); toast.push(cleanupErrorCopy(e), { tone: 'error' }); },
    };
    if (item.derived === 'orphan' && item.task_id) {
      cleanupOrphanM.mutate(
        { computerId: item.computer_id, body: { project_id: item.project_id, task_id: item.task_id } },
        handlers,
      );
    } else if (item.id) {
      cleanupWtM.mutate(item.id, handlers);
    }
  };
  const cleaning = cleanupWtM.isPending || cleanupOrphanM.isPending;

  const renderRow = (item: WorktreeConsoleItem) => {
    const key = rowKey(item);
    const badge = statusBadge(item);
    const offline = isOffline(item.computer_id);
    const expandable = !!item.id && !!item.task_id; // 登记行才可复用 DiffCard（base..head）
    const open = !!expanded[key];
    return (
      <div className="wt-row-wrap" key={key}>
        <div className="wt-row">
          <button
            type="button" className="wt-branch"
            disabled={!expandable}
            aria-label={expandable ? `展开 Diff ${item.branch ?? key}` : (item.branch ?? '(无分支)')}
            onClick={() => expandable && setExpanded((m) => ({ ...m, [key]: !m[key] }))}
          >
            <GitBranch />
            <span className="wt-branch-nm">{item.branch ?? '(无分支)'}</span>
          </button>
          <span className={`wt-badge b-${badge.cls}`}>{badge.label}</span>
          {item.channel_id && item.task_id ? (
            <button type="button" className="wt-task-link" onClick={() => openTask(item)}>
              {item.task_title ?? '(无标题任务)'}
            </button>
          ) : (
            <span className="wt-task-none">{item.task_title ?? (item.task_id ?? '—')}</span>
          )}
          <span className="wt-live">
            {item.live == null ? (
              <span className="wt-dim">—</span>
            ) : (
              <>
                {item.live.dirty && <span className="wt-dirty">有改动</span>}
                {(item.live.behind ?? 0) > 0 && <span className="wt-bh">↓{item.live.behind}</span>}
                {(item.live.ahead ?? 0) > 0 && <span className="wt-bh">↑{item.live.ahead}</span>}
                {!item.live.dirty && !(item.live.behind ?? 0) && !(item.live.ahead ?? 0) && (
                  <span className="wt-clean">干净</span>
                )}
              </>
            )}
          </span>
          <span className="wt-time">{item.created_at ? relTime(item.created_at) : '—'}</span>
          {canCleanup(item) ? (
            <span className="tipwrap">
              <button
                type="button" className="wt-clean-btn"
                disabled={offline || cleaning}
                aria-label={`清理 ${item.branch ?? key}`}
                onClick={() => setConfirmItem(item)}
              ><Trash2 /></button>
              {offline && <span className="tip">该机离线，无法清理</span>}
            </span>
          ) : (
            <span className="wt-clean-spacer" />
          )}
        </div>
        {expandable && open && (
          <div className="wt-diff"><DiffCard taskId={item.task_id!} /></div>
        )}
      </div>
    );
  };

  return (
    <main className="main worktrees">
      <div className="phead">
        <h1>Worktrees</h1>
        <button className="btn btn-secondary" disabled={consoleQ.isFetching} onClick={rescan}>
          <RefreshCw />重新扫描
        </button>
      </div>

      {/* 每机扫描状态条（live=1 才有 scans；ok/offline/timeout）。 */}
      {scans.length > 0 && (
        <div className="wt-scans">
          {scans.map((s) => (
            <span className={`wt-scan s-${s.status}`} key={s.computer_id}>
              <i />
              {computerName[s.computer_id] ?? s.computer_id}
              <span className="wt-scan-st">{SCAN_WORD[s.status] ?? s.status}</span>
            </span>
          ))}
        </div>
      )}

      {consoleQ.isLoading && <div className="wt-empty">加载 worktree…</div>}
      {!consoleQ.isLoading && items.length === 0 && (
        <div className="wt-empty">还没有 worktree</div>
      )}

      {groups.map(([projectId, g]) => (
        <div className="wt-group" key={projectId}>
          <div className="wt-group-head"><GitBranch />{g.name}</div>
          {g.active.length === 0 && g.cleaned.length === 0 && (
            <div className="wt-group-empty">该项目下没有 worktree</div>
          )}
          {g.active.map(renderRow)}
          {g.cleaned.length > 0 && (
            <>
              <button
                type="button" className="wt-cleaned-toggle"
                aria-expanded={!!cleanedOpen[projectId]}
                onClick={() => setCleanedOpen((m) => ({ ...m, [projectId]: !m[projectId] }))}
              >
                已清理 ({g.cleaned.length})
              </button>
              {cleanedOpen[projectId] && g.cleaned.map(renderRow)}
            </>
          )}
        </div>
      ))}

      {confirmItem && (
        <ConfirmModal
          title="清理 worktree"
          danger
          confirmLabel="清理"
          busy={cleaning}
          message={
            <>
              将删除目录 <span className="wt-confirm-path">{confirmItem.path}</span>
              {' '}与分支 <b>{confirmItem.branch ?? '(未知)'}</b>。此操作<span className="em">不可撤销</span>。
            </>
          }
          onConfirm={confirmCleanup}
          onClose={() => setConfirmItem(null)}
        />
      )}
    </main>
  );
}
