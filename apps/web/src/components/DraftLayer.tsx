// M6b 草稿画布层（P2b；kind=full、status=awaiting_confirm 的提案）。半透明虚线节点 overlay 到画布上，
// 顶部常驻确认条。人类调整（remove_node/add_edge/remove_edge/edit_node）在客户端累积成 adjustments[]，
// 每次调整即时 applyAdjustments → validateProposal 重验（防呆先于报错——错误非空则确认 disabled + 就地
// 清单）；[确认落地] 携指纹 CAS 一次性提交 adjustments（契约 B §5/§4.10 设计决策：不设逐笔端点）。
// 409 STALE_CONFIRM → latest 刷新重审；422 → 服务端错误清单；[拒绝] → 理由弹窗。rev 替换（对话修正）由
// WS 副作用桥切 activeDraft 到新提案 id，本层检测 id/rev 变更后整体替换 + toast。
import { useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { GitBranch, GitMerge, Lock, Plus, Terminal, Trash2, X } from 'lucide-react';

import type { CanvasPublic, MemberPublic, ProjectPublic } from '@coagentia/contracts-ts';

import type { Adjustment } from '../lib/draftAdjust';
import { applyAdjustments, layerize, readEdges, readNodeView, readNodes } from '../lib/draftAdjust';
import type { DecompError, DecompEnv } from '../lib/decomposition';
import { validateProposal } from '../lib/decomposition';
import { refreshProposalFromLatest, useChannelsSnapshot, useProposal } from '../data/queries';
import { channelsOf } from '../data/queries';
import { qk } from '../lib/queryKeys';
import { api, ApiError } from '../api';
import { ProposalRejectModal } from './ProposalRejectModal';
import { useToast } from './Toast';
import './draft-layer.css';

const COL_W = 248;
const ROW_H = 128;
const NODE_W = 208;
const PAD_X = 28;
const PAD_TOP = 76; // 让开顶部确认条

interface DraftLayerProps {
  channelId: string;
  proposalId: string;
  members: MemberPublic[];
  boundProjects: ProjectPublic[];
  canvas: CanvasPublic;
  onClose: () => void;
}

export function DraftLayer({
  channelId, proposalId, members, boundProjects, canvas, onClose,
}: DraftLayerProps) {
  const toast = useToast();
  const qc = useQueryClient();
  const q = useProposal(proposalId);
  const proposal = q.data;
  const channelsQ = useChannelsSnapshot();
  const channel = channelsOf(channelsQ.data).find((c) => c.id === channelId);
  const nodeLimit = channel?.decomp_node_limit ?? 12;

  const [adjustments, setAdjustments] = useState<Adjustment[]>([]);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [serverErrors, setServerErrors] = useState<DecompError[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [addFrom, setAddFrom] = useState('');
  const [addTo, setAddTo] = useState('');

  // rev 替换：proposalId 变（WS 桥切到新提案）→ 清空累积调整，并对 rev 提升弹 toast。
  const prevRef = useRef<{ id: string; revision: number } | null>(null);
  useEffect(() => {
    setAdjustments([]);
    setServerErrors(null);
    setAddFrom('');
    setAddTo('');
  }, [proposalId]);
  useEffect(() => {
    if (!proposal) return;
    const prev = prevRef.current;
    const rev = proposal.revision ?? 1;
    if (prev && prev.id !== proposal.id && rev > prev.revision) {
      toast.push(`草稿已更新为 rev.${rev}`, { tone: 'info' });
    }
    prevRef.current = { id: proposal.id, revision: rev };
  }, [proposal, toast]);

  const env: DecompEnv = useMemo(
    () => ({
      node_limit: nodeLimit,
      member_ids: members.map((m) => m.id),
      bound_project_ids: boundProjects.map((p) => p.id),
    }),
    [nodeLimit, members, boundProjects],
  );

  // 调整应用（op 恒合法——由本层构造；throw 兜底回退原 body）。
  const adjusted = useMemo(() => {
    if (!proposal) return {};
    try {
      return applyAdjustments(proposal.body, adjustments);
    } catch {
      return (proposal.body ?? {}) as Record<string, unknown>;
    }
  }, [proposal, adjustments]);

  const errors = useMemo(() => validateProposal(adjusted, env), [adjusted, env]);
  const nodeViews = useMemo(
    () => readNodes(adjusted).map(readNodeView).filter((n): n is NonNullable<typeof n> => n !== null),
    [adjusted],
  );
  const edges = useMemo(() => readEdges(adjusted), [adjusted]);
  const layout = useMemo(
    () => layerize(nodeViews.map((n) => n.temp_id), edges.map((e) => [e.from, e.to] as [string, string])),
    [nodeViews, edges],
  );

  const memberName = useMemo(() => {
    const m = new Map(members.map((mm) => [mm.id, mm.name]));
    return (id: string | null) => (id ? m.get(id) ?? id : null);
  }, [members]);

  const posOf = (tempId: string) => {
    const p = layout.get(tempId) ?? { col: 0, row: 0 };
    return { x: PAD_X + p.col * COL_W, y: PAD_TOP + p.row * ROW_H };
  };

  if (q.isLoading && !proposal) {
    return <DraftShell onClose={onClose}><div className="dl-note">草稿加载中…</div></DraftShell>;
  }
  if (!proposal) {
    return (
      <DraftShell onClose={onClose}>
        <div className="dl-note">草稿提案不可用（可能已被清理）。</div>
      </DraftShell>
    );
  }
  if (proposal.status !== 'awaiting_confirm') {
    // 落地/被取代/拒绝 → 该草稿不再可确认；被取代时等 WS 桥切到新 rev。
    return (
      <DraftShell onClose={onClose}>
        <div className="dl-note" role="status">
          {proposal.status === 'superseded'
            ? '草稿已被取代，正在等待新版本…'
            : '该草稿已不在待确认态，可关闭草稿层。'}
        </div>
      </DraftShell>
    );
  }

  const rev = proposal.revision ?? 1;
  const shortHash = proposal.proposal_hash.slice(0, 6);
  const canConfirm = errors.length === 0 && !busy;

  const addAdjust = (a: Adjustment) => setAdjustments((prev) => [...prev, a]);

  const removeNode = (tempId: string) => addAdjust({ op: 'remove_node', temp_id: tempId });
  const editTitle = (tempId: string, title: string) =>
    addAdjust({ op: 'edit_node', temp_id: tempId, changes: { title } });
  const editOwner = (tempId: string, owner: string | null) =>
    addAdjust({ op: 'edit_node', temp_id: tempId, changes: { suggested_owner: owner } });
  const removeEdge = (from: string, to: string) => addAdjust({ op: 'remove_edge', from, to });
  const addEdge = () => {
    if (!addFrom || !addTo || addFrom === addTo) return;
    const exists = edges.some((e) => e.from === addFrom && e.to === addTo);
    if (!exists) addAdjust({ op: 'add_edge', from: addFrom, to: addTo });
    setAddFrom('');
    setAddTo('');
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
        adjustments: adjustments as unknown as Record<string, unknown>[],
        removed_ops: [],
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
    if (e instanceof ApiError && e.code === 'STALE_CONFIRM') {
      refreshProposalFromLatest(qc, channelId, e.latest);
      setAdjustments([]);
      toast.push('已刷新最新态，请重审', { tone: 'error' });
      return;
    }
    if (e instanceof ApiError && e.code === 'VALIDATION_FAILED') {
      const list = extractServerErrors(e.details);
      setServerErrors(list.length ? list : [{ code: 'VALIDATION_FAILED', path: '$', message: e.message }]);
      return;
    }
    toast.push(e instanceof ApiError ? e.message : '确认落地失败', { tone: 'error' });
  };

  const doReject = async (reason: string) => {
    setBusy(true);
    try {
      const rejected = await api.rejectProposal(proposalId, reason || undefined);
      qc.setQueryData(qk.proposal(proposalId), rejected);
      toast.push('已拒绝草稿，理由已发进 source 线程', { tone: 'info' });
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

  const shownErrors = serverErrors ?? errors;

  return (
    <DraftShell onClose={onClose}>
      {/* 顶部常驻确认条 */}
      <div className="dl-bar" data-testid="draft-confirm-bar">
        <span className="dl-msg">
          提案 <b>rev.{rev}</b> · {nodeViews.length} 节点 {edges.length} 依赖 · 指纹{' '}
          <span className="mono" title={proposal.proposal_hash}>{shortHash}</span>
          {adjustments.length > 0 && <span className="dl-adjn">· 已调整 {adjustments.length} 项</span>}
        </span>
        <span className="dl-sp" />
        {errors.length > 0 && (
          <span className="dl-errn" data-testid="draft-error-count">{errors.length} 处错误</span>
        )}
        {adjustments.length > 0 && (
          <button type="button" className="btn btn-ghost" onClick={() => setAdjustments([])}>撤销全部</button>
        )}
        <button type="button" className="btn btn-secondary" data-testid="draft-reject" disabled={busy} onClick={() => setRejectOpen(true)}>拒绝</button>
        <button
          type="button" className="btn btn-primary" data-testid="draft-confirm"
          disabled={!canConfirm} onClick={() => void confirm()}
        >确认落地</button>
      </div>

      {/* 就地错误清单（防呆先于报错；server 422 亦复用此清单） */}
      {shownErrors.length > 0 && (
        <div className="dl-errlist" data-testid="draft-error-list" role="alert">
          <div className="dl-errhd">
            {serverErrors ? '服务端校验未通过：' : '存在校验错误，无法确认落地：'}
          </div>
          <ul>
            {shownErrors.map((er, i) => (
              <li key={`${er.path}-${i}`}><span className="mono">{er.path}</span> {er.message}</li>
            ))}
          </ul>
        </div>
      )}

      {/* 添加依赖（草稿节点间连线） */}
      {nodeViews.length >= 2 && (
        <div className="dl-addedge" data-testid="draft-add-edge">
          <span>添加依赖</span>
          <select aria-label="依赖起点" value={addFrom} onChange={(e) => setAddFrom(e.target.value)}>
            <option value="">起点</option>
            {nodeViews.map((n) => <option key={n.temp_id} value={n.temp_id}>{n.title}</option>)}
          </select>
          <span className="dl-arrow">→</span>
          <select aria-label="依赖终点" value={addTo} onChange={(e) => setAddTo(e.target.value)}>
            <option value="">终点</option>
            {nodeViews.map((n) => <option key={n.temp_id} value={n.temp_id}>{n.title}</option>)}
          </select>
          <button type="button" className="btn btn-ghost" aria-label="添加依赖边" disabled={!addFrom || !addTo || addFrom === addTo} onClick={addEdge}><Plus /></button>
        </div>
      )}

      {/* 草稿 DAG：分层布局的半透明节点 + 虚线边 */}
      <div className="dl-stage">
        <svg className="dl-edges">
          <defs>
            <marker id="dl-arrow" markerWidth="7" markerHeight="7" refX="5.5" refY="3" orient="auto" markerUnits="userSpaceOnUse">
              <path d="M0,0 L6,3 L0,6 Z" fill="var(--border-strong)" />
            </marker>
          </defs>
          {edges.map((e) => {
            const a = posOf(e.from);
            const b = posOf(e.to);
            const x1 = a.x + NODE_W;
            const y1 = a.y + 26;
            const x2 = b.x;
            const y2 = b.y + 26;
            return (
              <path
                key={`${e.from}->${e.to}`}
                d={`M${x1},${y1} C${x1 + 32},${y1} ${x2 - 32},${y2} ${x2},${y2}`}
                fill="none" stroke="var(--border-strong)" strokeWidth="1.5"
                strokeDasharray="5 4" markerEnd="url(#dl-arrow)" opacity=".6"
              />
            );
          })}
        </svg>
        {/* 边删除控件（边中点） */}
        {edges.map((e) => {
          const a = posOf(e.from);
          const b = posOf(e.to);
          const mx = (a.x + NODE_W + b.x) / 2;
          const my = (a.y + b.y) / 2 + 26;
          return (
            <button
              key={`rm-${e.from}->${e.to}`} type="button" className="dl-edge-rm"
              aria-label={`删除依赖 ${e.from} → ${e.to}`} title="删除依赖"
              style={{ left: mx - 10, top: my - 10 }}
              onClick={() => removeEdge(e.from, e.to)}
            ><X /></button>
          );
        })}
        {nodeViews.map((n) => {
          const p = posOf(n.temp_id);
          return (
            <DraftNode
              key={n.temp_id}
              view={n}
              style={{ left: p.x, top: p.y }}
              members={members}
              ownerName={memberName(n.suggested_owner)}
              onEditTitle={(t) => editTitle(n.temp_id, t)}
              onEditOwner={(o) => editOwner(n.temp_id, o)}
              onRemove={() => removeNode(n.temp_id)}
            />
          );
        })}
        {nodeViews.length === 0 && <div className="dl-note">该草稿没有节点。</div>}
      </div>

      {rejectOpen && <ProposalRejectModal busy={busy} onCancel={() => setRejectOpen(false)} onSubmit={(r) => void doReject(r)} />}
    </DraftShell>
  );
}

function DraftShell({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  return (
    <div className="draft-layer" data-testid="draft-layer" data-screen-label="P2b 草稿层">
      <button type="button" className="dl-close" aria-label="关闭草稿层" title="关闭草稿层" onClick={onClose}><X /></button>
      {children}
    </div>
  );
}

function DraftNode({ view, style, members, ownerName, onEditTitle, onEditOwner, onRemove }: {
  view: NonNullable<ReturnType<typeof readNodeView>>;
  style: CSSProperties;
  members: MemberPublic[];
  ownerName: string | null;
  onEditTitle: (title: string) => void;
  onEditOwner: (owner: string | null) => void;
  onRemove: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(view.title);
  const isSystem = view.kind === 'system';
  const Icon = view.system_action === 'merge' ? GitMerge : Terminal;

  const commitTitle = () => {
    setEditing(false);
    const t = draft.trim();
    if (t && t !== view.title) onEditTitle(t);
    else setDraft(view.title);
  };

  return (
    <div className={`dl-node${isSystem ? ' sys' : ''}`} style={style} data-testid="draft-node" data-temp={view.temp_id}>
      <div className="dl-node-hd">
        {isSystem && <span className="dl-diamond"><Icon /></span>}
        {editing ? (
          <input
            className="dl-title-input" aria-label="节点标题" value={draft} autoFocus
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commitTitle}
            onKeyDown={(e) => { if (e.key === 'Enter') commitTitle(); if (e.key === 'Escape') { setDraft(view.title); setEditing(false); } }}
          />
        ) : (
          <button type="button" className="dl-title" title="点击编辑标题" onClick={() => !isSystem && setEditing(true)} disabled={isSystem}>
            {view.title}
          </button>
        )}
        <span className="dl-sp" />
        <button type="button" className="dl-node-rm" aria-label={`移除节点 ${view.title}`} title="移除草稿节点（级联删关联边）" onClick={onRemove}><Trash2 /></button>
      </div>
      {isSystem ? (
        <div className="dl-node-sub"><Lock /> 系统节点 · {view.system_action}</div>
      ) : (
        <div className="dl-node-owner">
          <select
            aria-label={`${view.title} 建议 owner`} value={view.suggested_owner ?? ''}
            onChange={(e) => onEditOwner(e.target.value || null)}
          >
            <option value="">（待认领）</option>
            {members.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
          </select>
          {view.writes_code && <span className="dl-wc" title="代码任务"><GitBranch /></span>}
          {ownerName && <span className="sr-only">{ownerName}</span>}
        </div>
      )}
    </div>
  );
}

/** VALIDATION_FAILED 的 details.errors（服务端 kernel 错误清单）→ DecompError[]（形状异常则空）。 */
function extractServerErrors(details: unknown): DecompError[] {
  const raw = (details as { errors?: unknown } | undefined)?.errors;
  if (!Array.isArray(raw)) return [];
  const out: DecompError[] = [];
  for (const e of raw) {
    if (e && typeof e === 'object' && 'message' in e) {
      const o = e as { code?: unknown; path?: unknown; message?: unknown; hint?: unknown };
      out.push({
        code: typeof o.code === 'string' ? o.code : 'VALIDATION_FAILED',
        path: typeof o.path === 'string' ? o.path : '$',
        message: typeof o.message === 'string' ? o.message : '',
        ...(typeof o.hint === 'string' ? { hint: o.hint } : {}),
      });
    }
  }
  return out;
}
