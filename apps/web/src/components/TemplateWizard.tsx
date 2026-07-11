// 模板向导三步(B-M5-2 ②，对照设计稿 P13 向导)：①选模板(卡片 + DAG 缩略图) → ②角色映射
// (每占位下拉选成员/待认领/新建；评审+实现映射到同 runtime 就地 warning 不阻塞 FR-7.3/§11.2 #6)
// → ③预览简报 + DAG → 实例化(单事务落地批，成功跳目标频道画布)。role_mapping 全覆盖后才可实例化
// (§11.2 #1，避免 422)。零新增 WS 事件(裁决 #7)——落地广播走既有 task/canvas/message 事件。
import { useEffect, useMemo, useRef, useState } from 'react';
import { useQueries } from '@tanstack/react-query';
import { AlertTriangle, ArrowLeft, ArrowRight, Rocket } from 'lucide-react';

import type { MemberPublic, TemplateBody, TemplatePublic } from '@coagentia/contracts-ts';

import { api } from '../api';
import { qk } from '../lib/queryKeys';
import { useInstantiateTemplate, useTemplates } from '../data/queries';
import {
  classifyRole,
  hasSameRuntimeReview,
  missingRoleMappings,
} from '../lib/templates';
import './templates.css';

const RUNTIME_WORD: Record<string, string> = { claude_code: 'Claude Code', codex: 'Codex' };
const UNASSIGNED = '__unassigned__';
const CREATE = '__create__';

// ---- DAG 缩略图(纯展示)：按最长路径分层左→右排布，小矩形节点 + 连线。有环兜底 depth 0。
export function TemplateDagThumb({ body }: { body: TemplateBody }) {
  const nodes = body.nodes ?? [];
  const edges = body.edges ?? [];
  if (nodes.length === 0) return <div className="tdag-empty">空模板</div>;

  const adj: Record<string, string[]> = {};
  const indeg: Record<string, number> = {};
  nodes.forEach((n) => { adj[n.key] = []; indeg[n.key] = 0; });
  edges.forEach((e) => {
    if (adj[e.from_key] && e.to_key in indeg) { adj[e.from_key].push(e.to_key); indeg[e.to_key] += 1; }
  });
  // Kahn 拓扑 + 最长路径深度。
  const depth: Record<string, number> = {};
  const ind = { ...indeg };
  const q = nodes.filter((n) => ind[n.key] === 0).map((n) => n.key);
  q.forEach((k) => { depth[k] = 0; });
  while (q.length) {
    const k = q.shift() as string;
    for (const m of adj[k]) {
      depth[m] = Math.max(depth[m] ?? 0, (depth[k] ?? 0) + 1);
      ind[m] -= 1;
      if (ind[m] === 0) q.push(m);
    }
  }
  nodes.forEach((n) => { if (depth[n.key] === undefined) depth[n.key] = 0; });

  const cols: Record<number, string[]> = {};
  nodes.forEach((n) => { (cols[depth[n.key]] ??= []).push(n.key); });
  const W = 56; const H = 18; const CX = 76; const CY = 26;
  const pos: Record<string, { x: number; y: number }> = {};
  Object.entries(cols).forEach(([d, keys]) => {
    keys.forEach((k, i) => { pos[k] = { x: Number(d) * CX, y: i * CY }; });
  });
  const maxD = Math.max(...Object.keys(cols).map(Number));
  const maxRows = Math.max(...Object.values(cols).map((a) => a.length));
  const vw = maxD * CX + W;
  const vh = (maxRows - 1) * CY + H;
  const titleByKey = Object.fromEntries(nodes.map((n) => [n.key, n.title]));
  const trunc = (s: string) => (s.length > 7 ? `${s.slice(0, 6)}…` : s);

  return (
    <div className="tdag" data-testid="dag-thumb">
      <svg width={Math.min(vw, 200)} viewBox={`0 0 ${vw} ${vh}`} role="img" aria-label="流程缩略图">
        {edges.map((e, i) => {
          const a = pos[e.from_key]; const b = pos[e.to_key];
          if (!a || !b) return null;
          return (
            <line
              key={i}
              x1={a.x + W} y1={a.y + H / 2} x2={b.x} y2={b.y + H / 2}
              stroke="var(--border-strong)" strokeWidth={1}
            />
          );
        })}
        {nodes.map((n) => {
          const p = pos[n.key];
          return (
            <g key={n.key}>
              <rect
                x={p.x} y={p.y} width={W} height={H} rx={3}
                fill="var(--surface-2)" stroke="var(--border-strong)" strokeWidth={1}
              />
              <text
                x={p.x + W / 2} y={p.y + H / 2 + 3}
                textAnchor="middle" fontSize={9} fill="var(--text-secondary)"
              >{trunc(titleByKey[n.key] ?? n.key)}</text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

export function TemplateWizard({ channelId, members, onClose, onInstantiated, onCreateAgent }: {
  channelId: string;
  members: MemberPublic[];
  onClose: () => void;
  onInstantiated: (channelId: string) => void;
  onCreateAgent?: (placeholder: string) => void;
}) {
  const templatesQ = useTemplates();
  const instantiateM = useInstantiateTemplate();
  const templates = templatesQ.data ?? [];

  const agents = useMemo(() => members.filter((m) => m.kind === 'agent' && !m.removed_at), [members]);
  // 逐 Agent 拉详情拿 runtime(无批量端点，共用 qk.agent 缓存，同 MembersScreen 范式)。
  const agentQueries = useQueries({
    queries: agents.map((m) => ({ queryKey: qk.agent(m.id), queryFn: () => api.agent(m.id) })),
  });
  const runtimeOf = useMemo(() => {
    const map: Record<string, string | undefined> = {};
    agents.forEach((m, i) => { map[m.id] = agentQueries[i]?.data?.runtime; });
    return map;
  }, [agents, agentQueries]);

  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [selectedId, setSelectedId] = useState<string | undefined>();
  const [mapping, setMapping] = useState<Record<string, string | null>>({});
  const [awaitingCreateFor, setAwaitingCreateFor] = useState<string | null>(null);

  const selected: TemplatePublic | undefined = templates.find((t) => t.id === selectedId);
  const roles = selected?.body.roles ?? [];

  // 「新建 Agent」回填:awaitingCreateFor 期间若成员表新增 agent，自动映射到该占位(§7 #8 回填)。
  const prevIdsRef = useRef<Set<string>>(new Set(members.map((m) => m.id)));
  useEffect(() => {
    if (awaitingCreateFor) {
      const fresh = members.find((m) => m.kind === 'agent' && !prevIdsRef.current.has(m.id));
      if (fresh) {
        setMapping((prev) => ({ ...prev, [awaitingCreateFor]: fresh.id }));
        setAwaitingCreateFor(null);
      }
    }
    prevIdsRef.current = new Set(members.map((m) => m.id));
  }, [members, awaitingCreateFor]);

  const missing = missingRoleMappings(roles, mapping);
  const sameRuntimeWarn = hasSameRuntimeReview(roles, mapping, (id) => runtimeOf[id]);

  const pickTemplate = (id: string) => { setSelectedId(id); setMapping({}); };
  const onRoleChange = (ph: string, v: string) => {
    if (v === CREATE) { setAwaitingCreateFor(ph); onCreateAgent?.(ph); return; }
    if (v === '') {
      setMapping((prev) => { const n = { ...prev }; delete n[ph]; return n; });
      return;
    }
    setMapping((prev) => ({ ...prev, [ph]: v === UNASSIGNED ? null : v }));
  };

  const instantiate = () => {
    if (!selectedId || missing.length > 0) return;
    instantiateM.mutate(
      { templateId: selectedId, body: { channel_id: channelId, role_mapping: mapping } },
      { onSuccess: () => { onInstantiated(channelId); onClose(); } },
    );
  };

  const selValue = (ph: string): string => {
    if (!(ph in mapping)) return '';
    const v = mapping[ph];
    return v === null ? UNASSIGNED : v;
  };

  return (
    <div className="scrim" onClick={onClose}>
      <div className="modal twizard" onClick={(e) => e.stopPropagation()} data-testid="template-wizard">
        <div className="mtitle">从模板实例化</div>

        <div className="tw-steps" aria-label="向导进度">
          {[
            { n: 1, label: '选模板' },
            { n: 2, label: '角色映射' },
            { n: 3, label: '预览与实例化' },
          ].map((s, i) => (
            <div key={s.n} style={{ display: 'contents' }}>
              {i > 0 && <span className="sep" />}
              <span className={`st${step === s.n ? ' active' : step > s.n ? ' done' : ''}`}>
                <span className="n">{s.n}</span>{s.label}
              </span>
            </div>
          ))}
        </div>

        {/* 步①选模板 */}
        {step === 1 && (
          <div className="tw-cards" data-testid="wizard-step-1">
            {templatesQ.isLoading && <div className="tw-emptylist">模板加载中…</div>}
            {!templatesQ.isLoading && templates.length === 0 && (
              <div className="tw-emptylist">暂无模板</div>
            )}
            {templates.map((t) => (
              <button
                key={t.id}
                type="button"
                className={`tw-card${selectedId === t.id ? ' sel' : ''}`}
                onClick={() => pickTemplate(t.id)}
                data-testid="template-card"
              >
                <TemplateDagThumb body={t.body} />
                <span className="tc-body">
                  <span className="tc-name">
                    {t.name}
                    {t.builtin && <span className="builtinbadge">builtin</span>}
                  </span>
                  {t.description && <span className="tc-desc">{t.description}</span>}
                  <span className="tc-meta">{(t.body.nodes ?? []).length} 节点 · {(t.body.roles ?? []).length} 角色</span>
                </span>
              </button>
            ))}
          </div>
        )}

        {/* 步②角色映射 */}
        {step === 2 && selected && (
          <div data-testid="wizard-step-2">
            {sameRuntimeWarn && (
              <div className="tw-warn" role="alert" data-testid="same-runtime-warn">
                <AlertTriangle />
                评审与实现角色映射到了同一 runtime 的成员——建议跨 runtime 互审(checker ≠ doer)。仍可继续。
              </div>
            )}
            <div className="tw-roles">
              {roles.map((r) => {
                const kind = classifyRole(r);
                const kindWord = kind === 'review' ? '评审' : kind === 'implement' ? '实现' : '';
                return (
                  <div className="tw-role" key={r.placeholder}>
                    <span className="ph">
                      {r.placeholder}
                      {kindWord && <span className="kind">{kindWord}</span>}
                    </span>
                    <select
                      className="rsel"
                      aria-label={`映射 ${r.placeholder}`}
                      value={selValue(r.placeholder)}
                      onChange={(e) => onRoleChange(r.placeholder, e.target.value)}
                    >
                      <option value="">请选择成员…</option>
                      <option value={UNASSIGNED}>待认领</option>
                      {agents.map((a) => {
                        const rt = runtimeOf[a.id];
                        return (
                          <option key={a.id} value={a.id}>
                            {a.name}{rt ? ` · ${RUNTIME_WORD[rt] ?? rt}` : ''}
                          </option>
                        );
                      })}
                      <option value={CREATE}>＋ 新建 Agent…</option>
                    </select>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* 步③预览 */}
        {step === 3 && selected && (
          <div className="tw-preview" data-testid="wizard-step-3">
            <TemplateDagThumb body={selected.body} />
            {selected.body.briefing && (
              <div className="tw-briefing">{selected.body.briefing}</div>
            )}
            <div className="tw-summary">
              {roles.map((r) => {
                const mid = mapping[r.placeholder];
                const name = mid ? (members.find((m) => m.id === mid)?.name ?? mid) : '待认领';
                return (
                  <div className="sr" key={r.placeholder}>
                    <span className="ph">{r.placeholder}</span>
                    <span className="ar">→</span>
                    <span>{name}</span>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        <div className="ops">
          <button className="btn btn-ghost" onClick={onClose}>取消</button>
          {step > 1 && (
            <button className="btn btn-secondary" onClick={() => setStep((s) => (s - 1) as 1 | 2 | 3)}>
              <ArrowLeft /> 上一步
            </button>
          )}
          {step === 1 && (
            <button className="btn btn-primary" disabled={!selectedId} onClick={() => setStep(2)}>
              下一步 <ArrowRight />
            </button>
          )}
          {step === 2 && (
            <button
              className="btn btn-primary"
              disabled={missing.length > 0}
              title={missing.length > 0 ? `未映射:${missing.join('、')}` : undefined}
              onClick={() => setStep(3)}
            >下一步 <ArrowRight /></button>
          )}
          {step === 3 && (
            <button
              className="btn btn-primary"
              data-testid="instantiate-submit"
              disabled={instantiateM.isPending || missing.length > 0}
              onClick={instantiate}
            ><Rocket /> 实例化</button>
          )}
        </div>
      </div>
    </div>
  );
}
