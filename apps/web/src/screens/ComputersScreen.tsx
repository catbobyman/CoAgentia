// P7 机器(复用主壳,经 <Outlet/> 渲染):机器卡(name/os/status/daemon_version/detected_runtimes)
// + 其上 Agent 头像堆 + Add Computer 入口(弹窗生成 uvx 命令行,api_key 明文仅一次)。
import { useState } from 'react';
import { useQueries } from '@tanstack/react-query';
import { Check, Monitor, Plus, X } from 'lucide-react';

import type { ComputerCreated, ComputerPublic } from '@coagentia/contracts-ts';

import { useComputers, useDeleteComputer, useMembers, usePatchComputer, usePresence } from '../data/queries';
import { presenceMap } from '../data/queries';
import { api, ApiError } from '../api';
import { qk } from '../lib/queryKeys';
import { useToast } from '../components/Toast';
import { ConfirmModal } from '../components/ConfirmModal';
import { PRESENCE_VAR } from '../lib/uiMaps';

const RUNTIME_LABEL: Record<string, string> = {
  claude_code: 'Claude Code', codex: 'Codex', gemini: 'Gemini',
};
const rtLabel = (r: string) => RUNTIME_LABEL[r] ?? r;

export function ComputersScreen() {
  const computersQ = useComputers();
  const membersQ = useMembers();
  const presenceQ = usePresence();
  const patchM = usePatchComputer();
  const deleteM = useDeleteComputer();
  const toast = useToast();
  const [modalOpen, setModalOpen] = useState(false);
  // F6 就地改名：正在改名的机器 id + 草稿名。
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState('');
  // F6 移除确认：待删机器（ConfirmModal）。
  const [removing, setRemoving] = useState<ComputerPublic | null>(null);

  const computers = computersQ.data ?? [];
  const members = membersQ.data ?? [];
  const presence = presenceMap(presenceQ.data);
  const agentMembers = members.filter((m) => m.kind === 'agent' && !m.removed_at);

  const startRename = (c: ComputerPublic) => { setRenamingId(c.id); setRenameDraft(c.name); };
  const saveRename = (c: ComputerPublic) => {
    const name = renameDraft.trim();
    if (!name || name === c.name) { setRenamingId(null); return; }
    patchM.mutate({ computerId: c.id, name }, {
      onSuccess: () => { setRenamingId(null); toast.push('机器已改名', { tone: 'success' }); },
      onError: (e: unknown) =>
        toast.push(e instanceof ApiError ? e.message : '改名失败', { tone: 'error' }),
    });
  };
  const confirmRemove = () => {
    if (!removing) return;
    const target = removing;
    deleteM.mutate(target.id, {
      onSuccess: () => { setRemoving(null); toast.push(`已移除机器 ${target.name}`, { tone: 'success' }); },
      onError: (e: unknown) => {
        setRemoving(null);
        const msg =
          e instanceof ApiError && e.code === 'COMPUTER_HAS_AGENTS'
            ? '该机器上仍有 Agent，先删除全部 Agent'
            : e instanceof ApiError && e.code === 'COMPUTER_HAS_PROJECTS'
              ? '该机器仍被 Project 使用，请先迁移或删除 Project'
              : e instanceof ApiError ? e.message : '移除机器失败';
        toast.push(msg, { tone: 'error' });
      },
    });
  };

  // 每个 Agent 的 computer_id 需逐个查(无批量端点);据此把 Agent 归到机器卡。
  const agentQueries = useQueries({
    queries: agentMembers.map((m) => ({
      queryKey: qk.agent(m.id),
      queryFn: () => api.agent(m.id),
    })),
  });
  const computerOfAgent: Record<string, string | undefined> = {};
  agentMembers.forEach((m, i) => { computerOfAgent[m.id] = agentQueries[i]?.data?.computer_id; });

  return (
    <main className="main computers">
      <div className="phead">
        <h1>Computers</h1>
        <button className="btn btn-primary" onClick={() => setModalOpen(true)}>
          <Plus />Add Computer
        </button>
      </div>

      {computers.length === 0 && (
        <div className="empty">
          <div className="ttl">NO COMPUTERS</div>
          <div className="desc">Agent 需要在一台机器上运行——连接你的第一台</div>
          <button className="btn btn-primary" onClick={() => setModalOpen(true)}>Add Computer</button>
        </div>
      )}

      {computers.map((c) => {
        const agentsHere = agentMembers.filter((m) => computerOfAgent[m.id] === c.id);
        return (
          <div className="mcard" key={c.id}>
            <div className="mhd">
              <Monitor />
              {renamingId === c.id ? (
                <span className="mrename">
                  <input
                    className="val" value={renameDraft} autoFocus aria-label="机器名称"
                    onChange={(e) => setRenameDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') saveRename(c);
                      if (e.key === 'Escape') setRenamingId(null);
                    }}
                  />
                  <button className="icobtn" aria-label="保存改名" disabled={patchM.isPending} onClick={() => saveRename(c)}><Check /></button>
                  <button className="icobtn" aria-label="取消改名" onClick={() => setRenamingId(null)}><X /></button>
                </span>
              ) : (
                <b>{c.name}</b>
              )}
              <span className="meta">{c.os ?? 'unknown'} · daemon {c.daemon_version ?? '—'}</span>
              <span className="conn">
                <i style={{ background: `var(${c.status === 'connected' ? '--success' : '--border-strong'})` }} />
                {c.status ?? 'offline'}
              </span>
            </div>
            <div className="mrow">
              <span className="lb">Agents</span>
              <div className="agstack">
                {agentsHere.length === 0 && <span className="mono" style={{ color: 'var(--text-muted)' }}>—</span>}
                {agentsHere.map((m) => {
                  const st = presence[m.id]?.status ?? 'offline';
                  return (
                    <span className="av26" key={m.id} style={{ background: 'var(--avatar-7)' }}>
                      {m.name[0]}
                      <span
                        className={`p${st === 'busy' ? ' pulse' : ''}`}
                        style={{ background: `var(${PRESENCE_VAR[st] ?? '--border-strong'})` }}
                      />
                    </span>
                  );
                })}
              </div>
            </div>
            <div className="mrow">
              <span className="lb">Detected Runtimes</span>
              <div className="rtrow">
                {(c.detected_runtimes ?? []).map((rt) => (
                  <span className={`rtb${rt.installed ? '' : ' off'}`} key={rt.runtime}>
                    {rtLabel(rt.runtime)}
                    {rt.installed ? <span className="ck">✓</span> : <span>(not installed)</span>}
                  </span>
                ))}
              </div>
            </div>
            <div className="mops">
              <button className="btn btn-ghost" onClick={() => startRename(c)}>Rename</button>
              <span className="tipwrap">
                <button
                  className="btn btn-danger"
                  disabled={agentsHere.length > 0}
                  onClick={() => setRemoving(c)}
                >Remove</button>
                {agentsHere.length > 0 && <span className="tip">先删光该机器上的 Agent</span>}
              </span>
            </div>
          </div>
        );
      })}

      {modalOpen && <AddComputerModal onClose={() => setModalOpen(false)} />}

      {/* F6 移除机器确认（不可撤销）：键入机器名防呆。 */}
      {removing && (
        <ConfirmModal
          title="移除机器"
          danger
          confirmLabel="移除机器"
          requireText={removing.name}
          requireTextLabel={`键入 “${removing.name}” 以确认移除`}
          busy={deleteM.isPending}
          message={
            <>
              移除机器 <span className="em">{removing.name}</span> 后，其接入凭证失效、
              该机上不能再运行 Agent。此操作<span className="em">不可撤销</span>。
            </>
          }
          onConfirm={confirmRemove}
          onClose={() => setRemoving(null)}
        />
      )}
    </main>
  );
}

function AddComputerModal({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState('');
  const [created, setCreated] = useState<ComputerCreated | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    setSubmitting(true);
    try {
      setCreated(await api.addComputer(name.trim() || 'New Computer'));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="scrim" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="mtitle">Add Computer</div>
        {!created ? (
          <>
            <div className="field">
              <span className="lb">机器名称</span>
              <div className="inp">
                <span className="pr">❯</span>
                <input className="val" value={name} placeholder="My PC" onChange={(e) => setName(e.target.value)} />
              </div>
            </div>
            <div className="ops">
              <button className="btn btn-ghost" onClick={onClose}>取消</button>
              <button className="btn btn-primary" disabled={submitting} onClick={() => void submit()}>生成接入命令</button>
            </div>
          </>
        ) : (
          <>
            <div className="hint">在目标机器运行以下命令接入(api_key 明文仅此一次):</div>
            <pre className="cmdline">{created.command_line}</pre>
            <div className="field">
              <span className="lb">API Key(仅显示一次)</span>
              <pre className="cmdline">{created.api_key}</pre>
            </div>
            <div className="ops">
              <button className="btn btn-primary" onClick={onClose}>完成</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
