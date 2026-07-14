// P8 成员(/members,改编自设计稿 P8-members.html):HUMANS / AGENTS 两段成员表。
// 列 = 头像+在线点 / 名字 / 角色徽章 / (Agent)runtime 徽章 / 在线态文案 / 发私信。Agent 行点进 /agents/$id。
// F3 发私信：图标 → createDm（幂等）→ 切到该 DM 频道。
// F9 成员改角色（owner 拍板做）：角色徽章 → owner/admin 可改下拉（权限矩阵 §3.1：admin 仅 Member 级、
// R1 Agent 永不 Owner；不改自己）。
import { useState } from 'react';
import { useQueries } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { ChevronDown, MessageSquare } from 'lucide-react';

import type { MemberPublic, MemberRole, PresenceEntry } from '@coagentia/contracts-ts';

import { api, ApiError } from '../api';
import { qk } from '../lib/queryKeys';
import { presenceMap, useCreateDm, useMembers, usePatchMember, usePresence } from '../data/queries';
import { useUiStore } from '../lib/store';
import { useToast } from '../components/Toast';
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
  const createDm = useCreateDm();
  const patchMember = usePatchMember();
  const toast = useToast();
  const setActiveChannel = useUiStore((s) => s.setActiveChannel);

  const members = (membersQ.data ?? []).filter((m) => !m.removed_at);
  const presence = presenceMap(presenceQ.data);
  const humans = members.filter((m) => m.kind === 'human');
  const agents = members.filter((m) => m.kind === 'agent');
  const me = members.find((m) => m.kind === 'human' && m.role === 'owner');
  const canManageRoles = me?.role === 'owner' || me?.role === 'admin';

  // 逐 Agent 拉详情拿 runtime(无批量端点;共用 qk.agent 缓存,与 P6/P7 一致)。
  const agentQueries = useQueries({
    queries: agents.map((m) => ({ queryKey: qk.agent(m.id), queryFn: () => api.agent(m.id) })),
  });
  const runtimeOf: Record<string, string | undefined> = {};
  agents.forEach((m, i) => { runtimeOf[m.id] = agentQueries[i]?.data?.runtime; });

  const openAgent = (memberId: string) =>
    void navigate({ to: '/agents/$memberId', params: { memberId }, search: { tab: 'profile' } });

  // F3 发私信：幂等建/取回 DM → 切到该频道（会话屏按 activeChannelId 命中渲染）。
  const openDm = (memberId: string) => {
    createDm.mutate(memberId, {
      onSuccess: (ch) => {
        setActiveChannel(ch.id);
        void navigate({ to: '/', search: { tab: 'chat' } });
      },
      onError: (e: unknown) =>
        toast.push(e instanceof ApiError ? e.message : '发起私信失败', { tone: 'error' }),
    });
  };

  // F9 角色变更：权限矩阵在 server 执法，UI 门是防呆。可改 = owner/admin 且非自身、admin 不动 owner。
  const roleEditable = (m: MemberPublic): boolean => {
    if (!canManageRoles || m.id === me?.id) return false;
    if (me?.role === 'admin' && m.role === 'owner') return false;
    return true;
  };
  const roleOptionsFor = (m: MemberPublic): MemberRole[] => {
    let opts: MemberRole[] = ['owner', 'admin', 'member'];
    if (m.kind === 'agent') opts = opts.filter((r) => r !== 'owner'); // R1 Agent 永不 Owner
    if (me?.role === 'admin') opts = opts.filter((r) => r !== 'owner'); // admin 不能设 owner
    return opts;
  };
  const changeRole = (memberId: string, role: MemberRole) => {
    patchMember.mutate({ memberId, role }, {
      onSuccess: () => toast.push('角色已更新', { tone: 'success' }),
      onError: (e: unknown) =>
        toast.push(e instanceof ApiError ? e.message : '更改角色失败', { tone: 'error' }),
    });
  };

  return (
    <main className="main membersscr">
      <div className="ms-head">
        <h1>Members</h1>
      </div>

      <div className="ms-scroll">
        <div className="ms-seclb">Humans</div>
        <div className="ms-list">
          {humans.map((m) => (
            <MemberRow
              key={m.id}
              member={m}
              presence={presence[m.id]}
              isSelf={m.id === me?.id}
              onDm={m.id === me?.id ? undefined : () => openDm(m.id)}
              roleEditable={roleEditable(m)}
              roleOptions={roleOptionsFor(m)}
              onChangeRole={(role) => changeRole(m.id, role)}
              rolePending={patchMember.isPending}
            />
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
              onDm={() => openDm(m.id)}
              roleEditable={roleEditable(m)}
              roleOptions={roleOptionsFor(m)}
              onChangeRole={(role) => changeRole(m.id, role)}
              rolePending={patchMember.isPending}
            />
          ))}
          {agents.length === 0 && <div className="ms-empty">无 Agent 成员</div>}
        </div>
      </div>
    </main>
  );
}

function MemberRow({
  member, presence, runtime, onOpen, onDm, isSelf,
  roleEditable, roleOptions, onChangeRole, rolePending,
}: {
  member: MemberPublic;
  presence?: PresenceEntry;
  runtime?: string;
  onOpen?: () => void;
  onDm?: () => void;
  isSelf?: boolean;
  roleEditable?: boolean;
  roleOptions?: MemberRole[];
  onChangeRole?: (role: MemberRole) => void;
  rolePending?: boolean;
}) {
  const [roleOpen, setRoleOpen] = useState(false);
  const role = member.role ?? 'member';
  const status = presence?.status ?? 'offline';
  const detail = presence?.busy_detail;
  const stop = (e: React.MouseEvent) => e.stopPropagation();
  return (
    <div className={`ms-row${onOpen ? ' clickable' : ''}`} onClick={onOpen}>
      <Avatar name={member.name} presence={presence} size="msg" />
      <span className="nm">{member.name}{isSelf && <span className="ms-you">你</span>}</span>
      {/* F9 角色徽章：可改 → 下拉；否则静态。 */}
      {roleEditable && roleOptions && roleOptions.length > 0 ? (
        <span className="dropwrap" onClick={stop}>
          <button
            className="rolebadge rolebadge-btn"
            aria-label="更改角色"
            disabled={rolePending}
            onClick={() => setRoleOpen((v) => !v)}
          >{ROLE_WORD[role] ?? role}<ChevronDown /></button>
          {roleOpen && (
            <div className="drop" style={{ top: 26, bottom: 'auto', right: 'auto', left: 0, minWidth: 120 }}>
              {roleOptions.map((r) => (
                <div
                  key={r}
                  className="it"
                  onClick={() => { setRoleOpen(false); if (r !== role) onChangeRole?.(r); }}
                >{ROLE_WORD[r] ?? r}{r === role && ' ·'}</div>
              ))}
            </div>
          )}
        </span>
      ) : (
        <span className="rolebadge">{ROLE_WORD[role] ?? role}</span>
      )}
      {runtime && <span className="rtb">{RUNTIME_WORD[runtime] ?? runtime}</span>}
      <span className="act">{detail ?? PRESENCE_WORD[status] ?? status}</span>
      <span className="sp" />
      <span className="ms-ops">
        {onDm && (
          <span
            className="icobtn"
            role="button"
            aria-label="发私信"
            onClick={(e) => { stop(e); onDm(); }}
          ><MessageSquare /></span>
        )}
      </span>
    </div>
  );
}
