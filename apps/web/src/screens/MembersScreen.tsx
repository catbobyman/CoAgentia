// P8 成员(/members,改编自设计稿 P8-members.html):HUMANS / AGENTS 两段成员表。
// 列 = 头像+在线点 / 名字 / 角色徽章 / (Agent)runtime 徽章 / 在线态文案。Agent 行点进 /agents/$id。
import { useQueries } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { MessageSquare } from 'lucide-react';

import type { MemberPublic, PresenceEntry } from '@coagentia/contracts-ts';

import { api } from '../api';
import { qk } from '../lib/queryKeys';
import { presenceMap, useMembers, usePresence } from '../data/queries';
import { Avatar } from '../components/Avatar';
import './members.css';

const ROLE_WORD: Record<string, string> = { owner: 'Owner', admin: 'Admin', member: 'Member' };
const RUNTIME_WORD: Record<string, string> = { claude_code: 'Claude Code', codex: 'Codex' };
const PRESENCE_WORD: Record<string, string> = {
  online: 'Online', idle: 'Idle · 待命', busy: 'Busy', error: 'Error', offline: 'Offline',
};

export function MembersScreen() {
  const navigate = useNavigate();
  const membersQ = useMembers();
  const presenceQ = usePresence();

  const members = (membersQ.data ?? []).filter((m) => !m.removed_at);
  const presence = presenceMap(presenceQ.data);
  const humans = members.filter((m) => m.kind === 'human');
  const agents = members.filter((m) => m.kind === 'agent');

  // 逐 Agent 拉详情拿 runtime(无批量端点;共用 qk.agent 缓存,与 P6/P7 一致)。
  const agentQueries = useQueries({
    queries: agents.map((m) => ({ queryKey: qk.agent(m.id), queryFn: () => api.agent(m.id) })),
  });
  const runtimeOf: Record<string, string | undefined> = {};
  agents.forEach((m, i) => { runtimeOf[m.id] = agentQueries[i]?.data?.runtime; });

  const openAgent = (memberId: string) =>
    void navigate({ to: '/agents/$memberId', params: { memberId }, search: { tab: 'profile' } });

  return (
    <main className="main membersscr">
      <div className="ms-head">
        <h1>Members</h1>
      </div>

      <div className="ms-scroll">
        <div className="ms-seclb">Humans</div>
        <div className="ms-list">
          {humans.map((m) => (
            <MemberRow key={m.id} member={m} presence={presence[m.id]} />
          ))}
          {humans.length === 0 && <div className="ms-empty">无人类成员</div>}
        </div>

        <div className="ms-seclb">Agents</div>
        <div className="ms-list">
          {agents.map((m) => (
            <MemberRow
              key={m.id}
              member={m}
              presence={presence[m.id]}
              runtime={runtimeOf[m.id]}
              onOpen={() => openAgent(m.id)}
            />
          ))}
          {agents.length === 0 && <div className="ms-empty">无 Agent 成员</div>}
        </div>
      </div>
    </main>
  );
}

function MemberRow({ member, presence, runtime, onOpen }: {
  member: MemberPublic;
  presence?: PresenceEntry;
  runtime?: string;
  onOpen?: () => void;
}) {
  const role = member.role ?? 'member';
  const status = presence?.status ?? 'offline';
  const detail = presence?.busy_detail;
  return (
    <div className={`ms-row${onOpen ? ' clickable' : ''}`} onClick={onOpen}>
      <Avatar name={member.name} presence={presence} size="msg" />
      <span className="nm">{member.name}</span>
      <span className="rolebadge">{ROLE_WORD[role] ?? role}</span>
      {runtime && <span className="rtb">{RUNTIME_WORD[runtime] ?? runtime}</span>}
      <span className="act">{detail ?? PRESENCE_WORD[status] ?? status}</span>
      <span className="sp" />
      <span className="ms-ops">
        <span className="icobtn" aria-label="发私信" onClick={(e) => e.stopPropagation()}><MessageSquare /></span>
      </span>
    </div>
  );
}
