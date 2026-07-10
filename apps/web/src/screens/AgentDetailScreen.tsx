// P6 Agent 详情(复用主壳,经 <Outlet/> 渲染):六页签 Profile/Home/技能/Reminders/诊断/成本。
// runtime/model 改标注「下次启动生效」(复发点 3);技能 tab 是白名单唯一入口。
// 数据:GET /agents/{id}、/skills、/reminders、/diagnostics、home/tree(全部经 contracts-ts 生成类型)。
import { useState } from 'react';
import { ChevronDown, File as FileIcon, Folder, Monitor, Square } from 'lucide-react';

import type { AgentTab } from '../routes/search';
import {
  useAgent, useAgentDiagnostics, useAgentReminders, useAgentSkills,
  useComputers, useHomeTree, useMembers, usePresence,
} from '../data/queries';
import { presenceMap, memberMap } from '../data/queries';

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

  const agentQ = useAgent(memberId);
  const membersQ = useMembers();
  const computersQ = useComputers();
  const presenceQ = usePresence();

  const members = membersQ.data ?? [];
  const byId = memberMap(members);
  const member = byId[memberId];
  const agent = agentQ.data;
  const presence = presenceMap(presenceQ.data)[memberId];
  const computer = (computersQ.data ?? []).find((c) => c.id === agent?.computer_id);
  const creator = agent?.created_by_member_id ? byId[agent.created_by_member_id] : undefined;

  if (!agent || !member) {
    return <main className="main"><div className="boot">loading agent…</div></main>;
  }

  const status = presence?.status ?? agent.status ?? 'offline';
  const statusColor = `var(${STATUS_COLOR[status] ?? '--border-strong'})`;

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
        <button className="btn btn-secondary"><Square />Stop</button>
        <div className="dropwrap">
          <button className="btn btn-secondary" onClick={() => setRestartOpen((v) => !v)}>
            Restart<ChevronDown />
          </button>
          {restartOpen && (
            <div className="drop">
              <div className="it">Restart</div>
              <div className="it">Session reset</div>
              <div className="sep" />
              <div className="it danger">Full reset</div>
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
          <>
            <div className="card">
              <div className="chd"><b>身份</b></div>
              <div className="frow"><span className="lb">Name</span><span className="vl">{member.name}</span></div>
              <div className="frow"><span className="lb">Description</span><span className="vl sub">{agent.description ?? '—'}</span></div>
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
                <span className="vl"><button className="selbtn"><span className="rb">{agent.runtime}</span><ChevronDown /></button></span>
              </div>
              <div className="frow">
                <span className="lb">Model</span>
                <span className="vl"><button className="selbtn">{agent.model}<ChevronDown /></button></span>
              </div>
            </div>
          </>
        )}

        {tab === 'home' && <HomeTab memberId={memberId} />}
        {tab === 'skills' && <SkillsTab memberId={memberId} />}
        {tab === 'reminders' && <RemindersTab memberId={memberId} />}
        {tab === 'diagnostics' && <DiagnosticsTab memberId={memberId} />}
        {tab === 'cost' && (
          <div className="card">
            <div className="chd"><b>成本 · Token 用量</b><span className="m">按任务聚合(token_usage.reported)</span></div>
            <div className="emptytab">尚无用量记录——运行任务后由 WS 累加。</div>
          </div>
        )}
      </section>
    </main>
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

function SkillsTab({ memberId }: { memberId: string }) {
  const q = useAgentSkills(memberId);
  const skills = q.data ?? [];
  return (
    <div className="card">
      <div className="chd"><b>技能白名单</b><span className="m">授予后即生效 · 白名单唯一入口</span></div>
      {skills.length === 0 && <div className="emptytab">未授予任何技能。此页是白名单的唯一配置入口。</div>}
      {skills.map((s) => (
        <div className="frow" key={s.skill}>
          <span className="vl mono">{s.skill}</span>
          <span className="rbadge">granted</span>
        </div>
      ))}
    </div>
  );
}

function RemindersTab({ memberId }: { memberId: string }) {
  const q = useAgentReminders(memberId);
  const reminders = q.data ?? [];
  return (
    <div className="card">
      <div className="chd"><b>Reminders</b></div>
      {reminders.length === 0 && <div className="emptytab">尚无 reminder。</div>}
      {reminders.map((r) => (
        <div className="frow" key={r.id}>
          <span className="vl mono">{r.kind} · {r.cadence ?? '—'}</span>
          <span className="rbadge">{r.status ?? 'active'}</span>
        </div>
      ))}
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
          <span className="mono filesize">{e.created_at.slice(11, 19)}</span>
        </div>
      ))}
    </div>
  );
}
