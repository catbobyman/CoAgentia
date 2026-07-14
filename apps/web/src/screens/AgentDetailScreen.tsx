// P6 Agent 详情(复用主壳,经 <Outlet/> 渲染):六页签 Profile/Home/技能/Reminders/诊断/成本。
// runtime/model 改标注「下次启动生效」(复发点 3);技能 tab 是白名单唯一入口。
// 数据:GET /agents/{id}、/skills、/reminders、/diagnostics、home/tree(全部经 contracts-ts 生成类型)。
import { useState } from 'react';
import { Check, ChevronDown, File as FileIcon, Folder, Monitor, Pencil, Plus, Square } from 'lucide-react';

import type { AgentPatch, AgentPublic, ComputerPublic, LifecycleAction } from '@coagentia/contracts-ts';

import type { AgentTab } from '../routes/search';
import {
  useAgent, useAgentDiagnostics, useAgentLifecycle, useAgentReminders, useAgentSkills,
  useCancelReminder, useComputers, useHomeTree, useMembers, usePatchAgent, usePresence,
  usePutAgentSkills,
} from '../data/queries';
import { presenceMap, memberMap } from '../data/queries';
import { ApiError } from '../api';
import { useToast } from '../components/Toast';
import { ConfirmModal } from '../components/ConfirmModal';
import { fmtDate, fmtTime, fmtTimeSec } from '../lib/time';
import { cronPreview } from '../lib/cron';

const RUNTIME_LABEL: Record<string, string> = {
  claude_code: 'Claude Code', codex: 'Codex', gemini: 'Gemini',
};
// F2 三档重置 + Stop/Restart 的成功文案（FR-3.4）。
const LIFECYCLE_WORD: Record<LifecycleAction, string> = {
  start: '启动', stop: '停止', restart: '重启', reset_session: '重置会话', reset_full: '完全重置',
};

const TAB_DEFS: { key: AgentTab; label: string }[] = [
  { key: 'profile', label: 'Profile' },
  { key: 'home', label: 'Home' },
  { key: 'skills', label: '技能' },
  { key: 'reminders', label: 'Reminders' },
  { key: 'diagnostics', label: '诊断' },
  { key: 'cost', label: '成本' },
];

// presence/status → 状态方块色(token 变量,零发明)。
const STATUS_COLOR: Record<string, string> = {
  starting: '--warning', idle: '--success', busy: '--warning',
  error: '--danger', offline: '--border-strong', online: '--success',
};

export function AgentDetailScreen({ memberId, tab, setTab }: {
  memberId: string;
  tab: AgentTab;
  setTab: (tab: AgentTab) => void;
}) {
  const [restartOpen, setRestartOpen] = useState(false);
  const [fullResetOpen, setFullResetOpen] = useState(false);
  const toast = useToast();

  const agentQ = useAgent(memberId);
  const membersQ = useMembers();
  const computersQ = useComputers();
  const presenceQ = usePresence();
  const lifecycleM = useAgentLifecycle(memberId);
  const patchM = usePatchAgent(memberId);

  const members = membersQ.data ?? [];
  const byId = memberMap(members);
  const member = byId[memberId];
  const agent = agentQ.data;
  const presence = presenceMap(presenceQ.data)[memberId];
  const computer = (computersQ.data ?? []).find((c) => c.id === agent?.computer_id);
  const creator = agent?.created_by_member_id ? byId[agent.created_by_member_id] : undefined;
  // R3 权限位:创建者或 admin/owner 才见技能编辑态(与 server put_skills 的 R3 门同判)。
  const me = members.find((m) => m.kind === 'human' && m.role === 'owner');
  const canEditSkills =
    !!me && (me.id === agent?.created_by_member_id || me.role === 'admin' || me.role === 'owner');

  if (!agent || !member) {
    return <main className="main"><div className="boot">loading agent…</div></main>;
  }

  const status = presence?.status ?? agent.status ?? 'offline';
  const statusColor = `var(${STATUS_COLOR[status] ?? '--border-strong'})`;
  const canEdit = canEditSkills; // runtime/model/description 编辑同 R3 门（服务端执法，UI 门是防呆）。

  // F2 生命周期（Stop/Restart/Session reset/Full reset）：成功 toast、daemon 离线（503）单独文案。
  const runLifecycle = (action: LifecycleAction) => {
    lifecycleM.mutate(action, {
      onSuccess: () => toast.push(`已${LIFECYCLE_WORD[action]} @${member.name}`, { tone: 'success' }),
      onError: (e: unknown) =>
        toast.push(
          e instanceof ApiError && e.code === 'DAEMON_OFFLINE'
            ? `@${member.name} 的 daemon 离线，指令未送达`
            : e instanceof ApiError ? e.message : '操作失败',
          { tone: 'error' },
        ),
    });
  };

  // F7 runtime/model/description 编辑（PATCH，下次启动生效）：成功 toast、R3/其它错误上浮。
  const savePatch = (patch: AgentPatch, done?: () => void) => {
    patchM.mutate(patch, {
      onSuccess: () => { toast.push('已更新，下次启动生效', { tone: 'success' }); done?.(); },
      onError: (e: unknown) =>
        toast.push(e instanceof ApiError ? e.message : '更新失败', { tone: 'error' }),
    });
  };

  return (
    <main className="main">
      {/* 头部 */}
      <header className="ahead">
        <span className="av40" style={{ background: 'var(--avatar-7)' }}>
          {member.name[0]}
          <span className={`p${status === 'busy' ? ' pulse' : ''}`} style={{ background: statusColor }} />
        </span>
        <span className="nm">{member.name}</span>
        <span className="pres">
          <i className={status === 'busy' ? 'pulse' : ''} style={{ background: statusColor }} />
          {status[0].toUpperCase() + status.slice(1)}
        </span>
        {presence?.busy_detail && <span className="act">{presence.busy_detail}</span>}
        <span className="sp" />
        <button
          className="btn btn-secondary"
          onClick={() => runLifecycle('stop')}
          disabled={lifecycleM.isPending}
        ><Square />Stop</button>
        <div className="dropwrap">
          <button className="btn btn-secondary" onClick={() => setRestartOpen((v) => !v)}>
            Restart<ChevronDown />
          </button>
          {restartOpen && (
            <div className="drop">
              <div className="it" onClick={() => { setRestartOpen(false); runLifecycle('restart'); }}>Restart</div>
              <div className="it" onClick={() => { setRestartOpen(false); runLifecycle('reset_session'); }}>Session reset</div>
              <div className="sep" />
              {/* Full reset 不可撤销 → 确认弹窗（红 + 键入 Agent 名防呆，交互 §7.3）。 */}
              <div className="it danger" onClick={() => { setRestartOpen(false); setFullResetOpen(true); }}>Full reset</div>
            </div>
          )}
        </div>
      </header>

      {/* 页签条 */}
      <nav className="tabsbar">
        {TAB_DEFS.map((t) => (
          <div
            key={t.key}
            className={`tab${t.key === tab ? ' active' : ''}`}
            onClick={() => setTab(t.key)}
          >{t.label}</div>
        ))}
      </nav>

      {/* 内容 */}
      <section className="content">
        {tab === 'profile' && (
          <ProfileTab
            agent={agent}
            member={member}
            computer={computer}
            creator={creator}
            canEdit={canEdit}
            patching={patchM.isPending}
            onPatch={savePatch}
          />
        )}

        {tab === 'home' && <HomeTab memberId={memberId} />}
        {tab === 'skills' && (
          <SkillsTab memberId={memberId} agent={agent} computer={computer} editable={canEditSkills} />
        )}
        {tab === 'reminders' && <RemindersTab memberId={memberId} />}
        {tab === 'diagnostics' && <DiagnosticsTab memberId={memberId} />}
        {tab === 'cost' && (
          <div className="card">
            <div className="chd"><b>成本 · Token 用量</b><span className="m">按任务聚合(token_usage.reported)</span></div>
            <div className="emptytab">尚无用量记录——运行任务后由 WS 累加。</div>
          </div>
        )}
      </section>

      {/* F2 Full reset 确认（不可撤销）：键入 Agent 名防呆解锁；确认后 reset_full。 */}
      {fullResetOpen && (
        <ConfirmModal
          title="完全重置 Agent"
          danger
          confirmLabel="完全重置"
          requireText={member.name}
          requireTextLabel={`键入 “${member.name}” 以确认完全重置`}
          busy={lifecycleM.isPending}
          message={
            <>
              <span className="em">Full reset</span> 会清空 @{member.name} 的会话与 Home 目录，
              <span className="em">不可撤销</span>。确认后 daemon 将重建该 Agent。
            </>
          }
          onConfirm={() => { setFullResetOpen(false); runLifecycle('reset_full'); }}
          onClose={() => setFullResetOpen(false)}
        />
      )}
    </main>
  );
}

// F7 Profile 页签：身份卡（Description 就地编辑）+ Runtime 配置卡（runtime/model 真下拉）。
// 编辑门 = R3（canEdit，服务端执法，UI 门防呆）。runtime 选项 = 该机 detected_runtimes（未装置灰
// "(not installed)" FR-2.3）；model 选项 = 该 runtime 的候选 models 池 + 自由输入（同 SkillsTab 体例）。
function ProfileTab({ agent, member, computer, creator, canEdit, patching, onPatch }: {
  agent: AgentPublic;
  member: { name: string };
  computer: ComputerPublic | undefined;
  creator: { name?: string } | undefined;
  canEdit: boolean;
  patching: boolean;
  onPatch: (patch: AgentPatch, done?: () => void) => void;
}) {
  const [runtimeOpen, setRuntimeOpen] = useState(false);
  const [modelOpen, setModelOpen] = useState(false);
  const [editDesc, setEditDesc] = useState(false);
  const [descDraft, setDescDraft] = useState(agent.description ?? '');
  const [modelFree, setModelFree] = useState('');

  const runtimes = computer?.detected_runtimes ?? [];
  const modelPool = runtimes.find((rt) => rt.runtime === agent.runtime)?.models ?? [];

  const pickRuntime = (rt: string) => {
    setRuntimeOpen(false);
    if (rt !== agent.runtime) onPatch({ runtime: rt });
  };
  const pickModel = (m: string) => {
    setModelOpen(false);
    if (m && m !== agent.model) onPatch({ model: m });
  };
  const addFreeModel = () => {
    const m = modelFree.trim();
    setModelFree('');
    setModelOpen(false);
    if (m && m !== agent.model) onPatch({ model: m });
  };
  const saveDesc = () => {
    const next = descDraft.trim();
    if (next === (agent.description ?? '').trim()) { setEditDesc(false); return; }
    onPatch({ description: next }, () => setEditDesc(false));
  };

  return (
    <>
      <div className="card">
        <div className="chd"><b>身份</b></div>
        <div className="frow"><span className="lb">Name</span><span className="vl">{member.name}</span></div>
        <div className="frow">
          <span className="lb">Description</span>
          {editDesc ? (
            <span className="vl agent-desc-edit">
              <textarea
                className="agent-desc-ta" rows={3} value={descDraft} autoFocus
                aria-label="Agent 说明" onChange={(e) => setDescDraft(e.target.value)}
              />
              <span className="agent-desc-ops">
                <button className="btn btn-ghost" onClick={() => { setDescDraft(agent.description ?? ''); setEditDesc(false); }}>取消</button>
                <button className="btn btn-primary" disabled={patching} onClick={saveDesc}>保存</button>
              </span>
            </span>
          ) : (
            <span className="vl sub">
              {agent.description || '—'}
              {canEdit && (
                <button
                  className="agent-edit-ic" aria-label="编辑说明"
                  onClick={() => { setDescDraft(agent.description ?? ''); setEditDesc(true); }}
                ><Pencil /></button>
              )}
            </span>
          )}
        </div>
        <div className="frow">
          <span className="lb">Computer</span>
          <span className="vl">
            <span className="machine">
              <Monitor />{computer?.name ?? '—'}
              <span className="st"><i style={{ background: `var(${STATUS_COLOR[computer?.status ?? 'offline'] ?? '--border-strong'})` }} />{computer?.status ?? 'offline'}</span>
            </span>
          </span>
        </div>
        <div className="frow">
          <span className="lb">Created by</span>
          <span className="vl"><span className="inlineav"><span className="av16">{creator?.name?.[0] ?? '—'}</span>{creator?.name ?? '—'}</span></span>
        </div>
      </div>

      <div className="card">
        <div className="chd"><b>Runtime 配置</b><span className="m">下次启动生效 · Home 保留</span></div>
        <div className="frow">
          <span className="lb">Runtime</span>
          <span className="vl">
            {canEdit ? (
              <span className="dropwrap">
                <button className="selbtn" disabled={patching} onClick={() => setRuntimeOpen((v) => !v)}>
                  <span className="rb">{RUNTIME_LABEL[agent.runtime] ?? agent.runtime}</span><ChevronDown />
                </button>
                {runtimeOpen && (
                  <div className="drop" style={{ top: 30, bottom: 'auto' }}>
                    {runtimes.length === 0 && <div className="it" style={{ color: 'var(--text-muted)' }}>该机未探测到 runtime</div>}
                    {runtimes.map((rt) => (
                      <div
                        key={rt.runtime}
                        className={`it${rt.installed ? '' : ' disabled'}`}
                        aria-disabled={!rt.installed}
                        onClick={rt.installed ? () => pickRuntime(rt.runtime) : undefined}
                      >
                        {RUNTIME_LABEL[rt.runtime] ?? rt.runtime}
                        {rt.installed
                          ? (rt.runtime === agent.runtime && <Check style={{ width: 12, height: 12, marginLeft: 6 }} />)
                          : <span style={{ color: 'var(--text-muted)', marginLeft: 6 }}>(not installed)</span>}
                      </div>
                    ))}
                  </div>
                )}
              </span>
            ) : (
              <button className="selbtn" disabled><span className="rb">{RUNTIME_LABEL[agent.runtime] ?? agent.runtime}</span></button>
            )}
          </span>
        </div>
        <div className="frow">
          <span className="lb">Model</span>
          <span className="vl">
            {canEdit ? (
              <span className="dropwrap">
                <button className="selbtn" disabled={patching} onClick={() => setModelOpen((v) => !v)}>
                  {agent.model}<ChevronDown />
                </button>
                {modelOpen && (
                  <div className="drop" style={{ top: 30, bottom: 'auto' }}>
                    {modelPool.map((m) => (
                      <div className="it" key={m} onClick={() => pickModel(m)}>
                        {m}{m === agent.model && <Check style={{ width: 12, height: 12, marginLeft: 6 }} />}
                      </div>
                    ))}
                    <div className="it modeladd" onClick={(e) => e.stopPropagation()}>
                      <input
                        className="val mono" value={modelFree} placeholder="自定义 model 后回车"
                        aria-label="自定义 model"
                        onChange={(e) => setModelFree(e.target.value)}
                        onKeyDown={(e) => { if (e.key === 'Enter') addFreeModel(); }}
                      />
                    </div>
                  </div>
                )}
              </span>
            ) : (
              <button className="selbtn" disabled>{agent.model}</button>
            )}
          </span>
        </div>
      </div>
    </>
  );
}

function HomeTab({ memberId }: { memberId: string }) {
  const q = useHomeTree(memberId);
  const entries = q.data?.entries ?? [];
  return (
    <div className="card">
      <div className="chd"><b>Home</b><span className="m">~/.coagentia/agents/{memberId.slice(-6)}</span></div>
      {entries.length === 0 && <div className="emptytab">空目录。</div>}
      {entries.map((e) => (
        <div className="frow filerow" key={e.name}>
          <span className="fileic">{e.kind === 'dir' ? <Folder /> : <FileIcon />}</span>
          <span className="vl">{e.name}</span>
          <span className="mono filesize">{e.kind === 'dir' ? '—' : `${e.size_bytes} B`}</span>
        </div>
      ))}
    </div>
  );
}

// 技能白名单编辑态(M5 B §11.3 / R6 全量替换)。候选池 = 所在机器该 runtime 探测上报的 skills;
// 展示 = 候选池 ∪ 已授予(池外已授予仍显示可移除);自由输入允许任意串(后端 PUT 接受)。
// 只读态(非创建者/admin)= 仅列已授予 + granted 徽标(旧行为)。
export function SkillsTab({ memberId, agent, computer, editable }: {
  memberId: string;
  agent: AgentPublic;
  computer: ComputerPublic | undefined;
  editable: boolean;
}) {
  const q = useAgentSkills(memberId);
  const putM = usePutAgentSkills(memberId);
  const toast = useToast();
  const [free, setFree] = useState('');

  const granted = q.data ?? [];
  const grantedSet = new Set(granted.map((s) => s.skill));
  // 该 Agent 所在机器、该 runtime 的候选技能池(computer 探测上报;claude=全局技能目录、
  // codex=app-server skills/list——实测两 runtime 均有池,裁决 #11 已推翻)。
  const pool = computer?.detected_runtimes?.find((rt) => rt.runtime === agent.runtime)?.skills ?? [];
  // 展示序:候选池在前(保序去重),再补池外已授予项。
  const union: string[] = [];
  for (const s of [...pool, ...granted.map((g) => g.skill)]) {
    if (!union.includes(s)) union.push(s);
  }

  // 全量替换：以当前授予集为基准，apply(next) 后 PUT。失败弹 toast(权限/网络)。
  const commit = (next: string[]) =>
    putM.mutate(Array.from(new Set(next)), {
      onError: (e: unknown) =>
        toast.push(e instanceof ApiError ? e.message : '更新技能失败', { tone: 'error' }),
    });
  const toggle = (skill: string) => {
    const next = grantedSet.has(skill)
      ? granted.map((g) => g.skill).filter((s) => s !== skill)
      : [...granted.map((g) => g.skill), skill];
    commit(next);
  };
  const addFree = () => {
    const s = free.trim();
    if (!s || grantedSet.has(s)) { setFree(''); return; }
    commit([...granted.map((g) => g.skill), s]);
    setFree('');
  };

  // codex 池空且无授予：引导文案而非空表(裁决 #11)。
  const codexEmpty = agent.runtime === 'codex' && union.length === 0;

  return (
    <div className="card">
      <div className="chd">
        <b>技能白名单</b>
        <span className="m">{editable ? '授予后即生效 · 白名单唯一入口' : '仅创建者/admin 可编辑'}</span>
      </div>

      {codexEmpty ? (
        <div className="emptytab">Codex 该 runtime 暂无技能机制,候选池为空——无需在此授予。</div>
      ) : !editable ? (
        // 只读态：仅已授予 + granted 徽标。
        <>
          {granted.length === 0 && (
            <div className="emptytab">未授予任何技能。此页是白名单的唯一配置入口。</div>
          )}
          {granted.map((s) => (
            <div className="frow" key={s.skill}>
              <span className="vl mono">{s.skill}</span>
              <span className="rbadge">granted</span>
            </div>
          ))}
        </>
      ) : (
        <>
          {union.length === 0 && (
            <div className="emptytab">
              该机器该 runtime 未探测到候选技能——可在下方自由输入手动授予。
            </div>
          )}
          {union.map((skill) => {
            const on = grantedSet.has(skill);
            const external = on && !pool.includes(skill); // 池外已授予
            return (
              <label className="frow skillrow" key={skill} data-testid="skill-row">
                <button
                  type="button"
                  className={`skchk${on ? ' on' : ''}`}
                  role="checkbox"
                  aria-checked={on}
                  aria-label={skill}
                  disabled={putM.isPending}
                  onClick={() => toggle(skill)}
                >
                  {on && <Check />}
                </button>
                <span className="vl mono">{skill}</span>
                {external && <span className="rbadge">池外</span>}
              </label>
            );
          })}
          <div className="frow skilladd">
            <div className="inp">
              <span className="pr">+</span>
              <input
                className="val mono"
                value={free}
                placeholder="自由输入技能名后回车授予"
                onChange={(e) => setFree(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') addFree(); }}
              />
            </div>
            <button
              type="button"
              className="btn btn-secondary"
              disabled={putM.isPending || free.trim() === ''}
              onClick={addFree}
            ><Plus />授予</button>
          </div>
        </>
      )}
    </div>
  );
}

export function RemindersTab({ memberId }: { memberId: string }) {
  const q = useAgentReminders(memberId);
  const cancelM = useCancelReminder(memberId);
  const toast = useToast();
  const reminders = q.data ?? [];

  const cancel = (id: string) =>
    cancelM.mutate(id, {
      onError: (e: unknown) =>
        toast.push(e instanceof ApiError ? e.message : '取消提醒失败', { tone: 'error' }),
    });

  return (
    <div className="card">
      <div className="chd"><b>Reminders</b><span className="m">Agent 自设唤醒(FR-3.9)</span></div>
      {reminders.length === 0 && <div className="emptytab">尚无 reminder。</div>}
      {reminders.map((r) => {
        const status = r.status ?? 'active';
        const active = status === 'active';
        // 锚点信息:频道恒有,task/message 择有标注(尾 6 位,与诊断/Home 尾码口径一致)。
        const anchors: string[] = [];
        if (r.anchor_channel_id) anchors.push(`#${r.anchor_channel_id.slice(-6)}`);
        if (r.anchor_task_id) anchors.push(`task ${r.anchor_task_id.slice(-6)}`);
        if (r.anchor_message_id) anchors.push(`msg ${r.anchor_message_id.slice(-6)}`);
        // 循环 cadence 若是五段式 cron → 原样 mono + 人读预览(M5 B §11.5;无法识别则仅原串)。
        const preview = cronPreview(r.cadence);
        return (
          <div className="frow" key={r.id}>
            <span className="lb">{r.kind}</span>
            <span className="vl">
              <span className="mono" title={preview ? `cron: ${r.cadence}` : undefined}>{r.cadence}</span>
              {preview && <span className="rbadge cronprev">{preview}</span>}
              {r.next_fire_at && (
                <span className="sub"> · 下次 {fmtDate(r.next_fire_at)} {fmtTime(r.next_fire_at)}</span>
              )}
              {anchors.length > 0 && <span className="sub"> · 锚点 {anchors.join(' · ')}</span>}
            </span>
            {/* loop_contract_id 非空 = LoopContract 上岗的 recurring(F4)。 */}
            {r.loop_contract_id && <span className="rbadge">循环 · 契约</span>}
            <span className="rbadge">{status}</span>
            {active && (
              <button
                className="btn btn-ghost"
                onClick={() => cancel(r.id)}
                disabled={cancelM.isPending}
              >取消</button>
            )}
          </div>
        );
      })}
    </div>
  );
}

function DiagnosticsTab({ memberId }: { memberId: string }) {
  const q = useAgentDiagnostics(memberId);
  const events = q.data ?? [];
  return (
    <div className="card">
      <div className="chd"><b>诊断时间线</b><span className="m">命令级留痕(NFR4)</span></div>
      {events.length === 0 && <div className="emptytab">尚无诊断事件。</div>}
      {events.map((e) => (
        <div className="frow" key={e.seq}>
          <span className="lb">#{e.seq}</span>
          <span className="vl mono">{e.type}</span>
          <span className="mono filesize">{fmtTimeSec(e.created_at)}</span>
        </div>
      ))}
    </div>
  );
}
