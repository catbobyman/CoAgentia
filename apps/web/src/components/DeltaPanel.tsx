// M6b delta 面板（P2c；kind=delta、status=awaiting_confirm）。对已落地图的增量变更：operations 列表
// （add 绿高亮 / remove 红高亮，目标 title 从画布反查）+ 部分接受（逐 op 复选剔除 → removed_ops 原始
// 下标）+ 剩余 op 集实时结构重验（防呆：引用悬空 / 无环 / 节点数 ≤ decomp_node_limit / remove 目标
// in_progress·in_review NODE_ACTIVE）+ base 检查（proposal.base_hash ≠ 画布基线 → 横幅+禁用，服务端亦
// 409）。[确认落地] 携指纹 CAS + removed_ops；409 DELTA_BASE_MISMATCH → 横幅+刷新提案；409 STALE →
// latest 刷新；422 → 就地错误。拒绝同 full。
import { useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, GitMerge, Terminal, X } from 'lucide-react';

import type {
  CanvasEdgePublic,
  CanvasNodePublic,
  CanvasPublic,
  MemberPublic,
  TaskPublic,
} from '@coagentia/contracts-ts';

import type { DeltaError, DeltaOpView } from '../lib/deltaOps';
import { readDeltaOps, revalidateDelta } from '../lib/deltaOps';
import {
  channelsOf, refreshProposalFromLatest, useChannelsSnapshot, useProposal,
} from '../data/queries';
import { qk } from '../lib/queryKeys';
import { api, ApiError } from '../api';
import { ProposalRejectModal } from './ProposalRejectModal';
import { useToast } from './Toast';
import './delta-panel.css';

const ACTIVE_STATUSES = new Set(['in_progress', 'in_review']);

const OP_WORD: Record<string, string> = {
  add_node: '新增节点', remove_node: '删除节点', add_edge: '新增依赖', remove_edge: '删除依赖',
};

interface DeltaPanelProps {
  channelId: string;
  proposalId: string;
  canvas: CanvasPublic;
  nodes: CanvasNodePublic[];
  edges: CanvasEdgePublic[];
  tasks: TaskPublic[];
  members: MemberPublic[];
  onClose: () => void;
}

export function DeltaPanel({
  channelId, proposalId, canvas, nodes, edges, tasks, onClose,
}: DeltaPanelProps) {
  const toast = useToast();
  const qc = useQueryClient();
  const q = useProposal(proposalId);
  const proposal = q.data;
  const channelsQ = useChannelsSnapshot();
  const channel = channelsOf(channelsQ.data).find((c) => c.id === channelId);
  const nodeLimit = channel?.decomp_node_limit ?? 12;

  const [removed, setRemoved] = useState<Set<number>>(new Set());
  const [rejectOpen, setRejectOpen] = useState(false);
  const [serverErrors, setServerErrors] = useState<{ code: string; message: string }[] | null>(null);
  const [baseMismatch, setBaseMismatch] = useState(false);
  const [busy, setBusy] = useState(false);

  const taskById = useMemo(() => new Map(tasks.map((t) => [t.id, t])), [tasks]);
  // 画布节点 id → 展示 title（agent = 任务标题；system = 动作名）。
  const titleOf = useMemo(() => {
    const m = new Map<string, string>();
    for (const n of nodes) {
      if (n.kind === 'system') m.set(n.id, n.system_action === 'merge' ? 'Merge' : 'Check');
      else m.set(n.id, (n.task_id ? taskById.get(n.task_id)?.title : undefined) ?? '(任务)');
    }
    return (id: string | undefined) => (id ? m.get(id) ?? id : '—');
  }, [nodes, taskById]);

  // NODE_ACTIVE 集：agent 节点其任务处 in_progress/in_review。
  const activeNodeIds = useMemo(() => {
    const s = new Set<string>();
    for (const n of nodes) {
      const st = n.task_id ? taskById.get(n.task_id)?.status : undefined;
      if (st && ACTIVE_STATUSES.has(st)) s.add(n.id);
    }
    return s;
  }, [nodes, taskById]);

  const ops = useMemo(() => readDeltaOps(proposal?.body), [proposal]);
  const remaining = useMemo(() => ops.filter((o) => !removed.has(o.index)), [ops, removed]);

  const baseHash = proposal?.base_hash ?? null;
  const canvasBaseMismatch = baseHash !== null && baseHash !== canvas.baseline_hash;

  const revalErrors: DeltaError[] = useMemo(
    () => revalidateDelta(remaining, {
      nodeLimit,
      currentNodeIds: nodes.map((n) => n.id),
      currentEdges: edges.map((e) => [e.from_node_id, e.to_node_id] as [string, string]),
      activeNodeIds,
    }),
    [remaining, nodeLimit, nodes, edges, activeNodeIds],
  );

  if (q.isLoading && !proposal) {
    return <DeltaShell onClose={onClose}><div className="dp-note">增量加载中…</div></DeltaShell>;
  }
  if (!proposal) {
    return <DeltaShell onClose={onClose}><div className="dp-note">增量提案不可用（可能已被清理）。</div></DeltaShell>;
  }
  if (proposal.status !== 'awaiting_confirm') {
    return (
      <DeltaShell onClose={onClose}>
        <div className="dp-note" role="status">该增量已不在待确认态（已落地/被取代/拒绝/失败），可关闭。</div>
      </DeltaShell>
    );
  }

  const allRemoved = ops.length > 0 && remaining.length === 0;
  const addCount = remaining.filter((o) => o.kind === 'add').length;
  const remCount = remaining.filter((o) => o.kind === 'remove').length;
  const baseBad = baseMismatch || canvasBaseMismatch;
  const canConfirm = !baseBad && !allRemoved && revalErrors.length === 0 && !busy && ops.length > 0;

  const toggle = (index: number) => {
    setRemoved((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  };

  const confirm = async () => {
    if (!canConfirm) return;
    setBusy(true);
    setServerErrors(null);
    try {
      const res = await api.confirmProposal(proposalId, {
        expected: {
          proposal_hash: proposal.proposal_hash,
          baseline_version: canvas.baseline_version ?? 0,
          baseline_hash: canvas.baseline_hash,
        },
        adjustments: [],
        removed_ops: [...removed].sort((a, b) => a - b),
      });
      qc.setQueryData(qk.proposal(proposalId), res.proposal);
      void qc.invalidateQueries({ queryKey: qk.canvas(channelId) });
      void qc.invalidateQueries({ queryKey: qk.tasks(channelId) });
      void qc.invalidateQueries({ queryKey: qk.messages(channelId) });
      toast.push('落地执行中…', { tone: 'success' });
      onClose();
    } catch (e) {
      handleConfirmError(e);
    } finally {
      setBusy(false);
    }
  };

  const handleConfirmError = (e: unknown) => {
    if (e instanceof ApiError && e.code === 'DELTA_BASE_MISMATCH') {
      setBaseMismatch(true);
      void qc.invalidateQueries({ queryKey: qk.proposal(proposalId) }); // 会转 failed
      toast.push('画布基线已变化，该增量需 Orchestrator 基于新基线重出', { tone: 'error' });
      return;
    }
    if (e instanceof ApiError && e.code === 'STALE_CONFIRM') {
      refreshProposalFromLatest(qc, channelId, e.latest);
      toast.push('已刷新最新态，请重审', { tone: 'error' });
      return;
    }
    if (e instanceof ApiError && (e.code === 'VALIDATION_FAILED' || e.code === 'NODE_ACTIVE')) {
      setServerErrors([{ code: e.code, message: e.message }]);
      return;
    }
    toast.push(e instanceof ApiError ? e.message : '确认落地失败', { tone: 'error' });
  };

  const doReject = async (reason: string) => {
    setBusy(true);
    try {
      const rejected = await api.rejectProposal(proposalId, reason || undefined);
      qc.setQueryData(qk.proposal(proposalId), rejected);
      toast.push('已拒绝增量，理由已发进 source 线程', { tone: 'info' });
      setRejectOpen(false);
      onClose();
    } catch (e) {
      if (e instanceof ApiError && e.code === 'STALE_CONFIRM') {
        refreshProposalFromLatest(qc, channelId, e.latest);
        toast.push('已刷新最新态，请重审', { tone: 'error' });
        setRejectOpen(false);
        return;
      }
      toast.push(e instanceof ApiError ? e.message : '拒绝失败', { tone: 'error' });
    } finally {
      setBusy(false);
    }
  };

  return (
    <DeltaShell onClose={onClose}>
      <div className="dp-head">
        <span className="dp-title">增量变更审查</span>
        <span className="dp-metrics">
          <span className="dp-add">+{addCount} 新增</span>
          <span className="dp-rem">−{remCount} 删除</span>
          <span className="dp-base">base <span className="mono" title={baseHash ?? ''}>{(baseHash ?? '—').slice(0, 6)}</span></span>
        </span>
      </div>

      {baseBad && (
        <div className="dp-banner" data-testid="delta-base-banner" role="alert">
          <AlertTriangle />
          画布基线已变化，该增量需 Orchestrator 基于新基线重出（不可直接落地）。
        </div>
      )}

      <div className="dp-ops" data-testid="delta-ops">
        {ops.length === 0 && <div className="dp-note">该增量没有操作项。</div>}
        {ops.map((o) => (
          <DeltaOpRow
            key={o.index} op={o} excluded={removed.has(o.index)} titleOf={titleOf}
            onToggle={() => toggle(o.index)}
          />
        ))}
      </div>

      {/* 重验/服务端错误就地清单（防呆先于报错） */}
      {allRemoved && (
        <div className="dp-hint" data-testid="delta-all-removed" role="alert">已全部剔除——请改用「拒绝」。</div>
      )}
      {!allRemoved && revalErrors.length > 0 && (
        <div className="dp-errlist" data-testid="delta-reval-errors" role="alert">
          <div className="dp-errhd">剩余增量结构不通过，无法确认落地：</div>
          <ul>{revalErrors.map((er, i) => <li key={`${er.code}-${i}`}>{er.message}</li>)}</ul>
        </div>
      )}
      {serverErrors && serverErrors.length > 0 && (
        <div className="dp-errlist" data-testid="delta-server-errors" role="alert">
          <div className="dp-errhd">服务端校验未通过：</div>
          <ul>{serverErrors.map((er, i) => <li key={`${er.code}-${i}`}><span className="mono">{er.code}</span> {er.message}</li>)}</ul>
        </div>
      )}

      <div className="dp-ops-foot">
        {removed.size > 0 && <span className="dp-removed-n">已剔除 {removed.size} 项</span>}
        <span className="dp-sp" />
        <button type="button" className="btn btn-secondary" data-testid="delta-reject" disabled={busy} onClick={() => setRejectOpen(true)}>拒绝</button>
        <button type="button" className="btn btn-primary" data-testid="delta-confirm" disabled={!canConfirm} onClick={() => void confirm()}>确认落地</button>
      </div>

      {rejectOpen && <ProposalRejectModal busy={busy} onCancel={() => setRejectOpen(false)} onSubmit={(r) => void doReject(r)} />}
    </DeltaShell>
  );
}

function DeltaShell({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  return (
    <aside className="delta-panel" data-testid="delta-panel" data-screen-label="P2c delta 面板">
      <button type="button" className="dp-close" aria-label="关闭增量面板" title="关闭" onClick={onClose}><X /></button>
      {children}
    </aside>
  );
}

function DeltaOpRow({ op, excluded, titleOf, onToggle }: {
  op: DeltaOpView;
  excluded: boolean;
  titleOf: (id: string | undefined) => string;
  onToggle: () => void;
}) {
  const detail = opDetail(op, titleOf);
  return (
    <label className={`dp-op ${op.kind}${excluded ? ' excluded' : ''}`} data-testid="delta-op" data-op={op.op}>
      <input type="checkbox" aria-label={`剔除操作 ${op.op}`} checked={excluded} onChange={onToggle} />
      <span className="dp-op-tag">{op.kind === 'add' ? '＋' : op.kind === 'remove' ? '−' : '·'}</span>
      <span className="dp-op-body">
        <span className="dp-op-word">{OP_WORD[op.op] ?? op.op}</span>
        <span className="dp-op-detail">{detail}</span>
      </span>
      {op.node && (op.node['system_action'] === 'merge' || op.node['system_action'] === 'check') && (
        <span className="dp-op-ico">{op.node['system_action'] === 'merge' ? <GitMerge /> : <Terminal />}</span>
      )}
    </label>
  );
}

function opDetail(op: DeltaOpView, titleOf: (id: string | undefined) => string): string {
  if (op.op === 'add_node') {
    const t = op.node && typeof op.node['title'] === 'string' ? (op.node['title'] as string) : op.nodeRef;
    return t ?? '(新节点)';
  }
  if (op.op === 'remove_node') return titleOf(op.nodeRef);
  if (op.op === 'add_edge' || op.op === 'remove_edge') return `${titleOf(op.from)} → ${titleOf(op.to)}`;
  return op.op;
}
