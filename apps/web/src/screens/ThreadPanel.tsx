// P5 任务线程面板(420px):任务牌头 §2.2 + 契约折叠卡 §4.6 + 线程回复流(复用 MessageFlow)+ 状态操作条。
// 由类型化深链 ?thread= 驱动(在 ChannelChatScreen 内消费,非顶层路由)。
// M1 只需形状:契约卡/交付卡/状态操作条为结构 UI,写操作 mock 无逻辑(claim/状态流转不落库)。
import { ChevronDown, X } from 'lucide-react';

import type { MemberPublic, PresenceEntry, TaskPublic } from '@coagentia/contracts-ts';

import { STATUS_VAR, STATUS_WORD } from '../lib/uiMaps';
import { useThread } from '../data/queries';
import { Avatar } from '../components/Avatar';
import { MessageFlow } from '../components/MessageFlow';
import { Composer } from '../components/Composer';

// verify_by 枚举仅 command | inspect | manual(复发点 2;PRD §4.3)。
// 契约卡无 REST 源(task_contract 属 M3),此处以设计稿代表性 AC 行呈现"形状"。
const CONTRACT_ACS: { id: string; text: React.ReactNode; verify: 'command' | 'inspect' | 'manual' }[] = [
  { id: 'AC-01', text: <>focus 归零后自动切换到 break,验证命令 <span className="cmd">npm test</span></>, verify: 'command' },
  { id: 'AC-02', text: '单文件 index.html 双击即可运行,无网络依赖', verify: 'manual' },
  { id: 'AC-03', text: '计时循环使用 requestAnimationFrame,暂停恢复误差 ≤1s', verify: 'inspect' },
];

export function ThreadPanel({
  task, rootMessageId, memberById, memberNames, meName, presenceOf, usage, onClose, onSend,
}: {
  task?: TaskPublic;
  rootMessageId: string;
  memberById: Record<string, MemberPublic>;
  memberNames: string[];
  meName: string;
  presenceOf: (memberId: string) => PresenceEntry | undefined;
  usage?: number;
  onClose: () => void;
  onSend: (body: string) => void;
}) {
  const threadQ = useThread(rootMessageId);
  const items = threadQ.data ?? [];
  // 回复流 = 线程条目去掉 root(root 内容已在牌头呈现)。
  const replies = items.filter((m) => m.id !== rootMessageId);

  const status = task?.status ?? 'todo';
  const owner = task?.owner_member_id ? memberById[task.owner_member_id] : undefined;
  const creator = task?.created_by_member_id ? memberById[task.created_by_member_id] : undefined;
  const created = task?.created_at?.slice(11, 16);

  return (
    <aside className="panel" data-screen-label={`任务线程 #${task?.number ?? ''}`}>
      {/* [1] 任务牌头 */}
      <header className="phead">
        <div className="row1">
          <span className="no">#{task?.number ?? '—'}</span>
          <span className="ttl">{task?.title ?? '线程'}</span>
          <span className="icobtn close" aria-label="关闭面板" onClick={onClose}><X /></span>
        </div>
        <div className="row2">
          <span className="stchip">
            <i style={{ background: `var(${STATUS_VAR[status]})` }} />{STATUS_WORD[status]}
          </span>
          {owner && (
            <span className="who"><Avatar name={owner.name} presence={presenceOf(owner.id)} size="nav" />{owner.name}</span>
          )}
          {creator && <span className="meta">by {creator.name}{created ? ` · ${created}` : ''}</span>}
          {usage !== undefined && <span className="tokbadge">{(usage / 1000).toFixed(1)}k tok</span>}
        </div>
        <div className="row3">
          <span className="planentry">TaskPlan · AC×{CONTRACT_ACS.length}<span className="ar">▾</span></span>
          <span className="handoff">TaskHandoff 待提交</span>
        </div>
      </header>

      {/* [2] 契约折叠卡(展开态) */}
      <section className="contract">
        <div className="chd"><b>TaskPlan</b><span className="m">AC×{CONTRACT_ACS.length} · by {creator?.name ?? '—'}</span></div>
        <div className="goal">
          <span className="lb">Goal</span>
          交付一个单文件(index.html)番茄钟:25/5 相位循环、开始/暂停/重置,零依赖。
        </div>
        <span className="aclb">Acceptance Criteria</span>
        {CONTRACT_ACS.map((ac) => (
          <div className="acrow" key={ac.id}>
            <span className="acid">{ac.id}</span>
            <span className="acst">{ac.text}</span>
            <span className="vchip">{ac.verify}</span>
          </div>
        ))}
        <button className="more">展开全部 ▸</button>
      </section>

      {/* [3] 线程回复流(复用 MessageFlow) */}
      <MessageFlow
        messages={replies}
        memberById={memberById}
        memberNames={memberNames}
        meName={meName}
        presenceOf={presenceOf}
        taskByRoot={{}}
        usageByTask={{}}
      />

      {/* [4] 状态操作条(claim / 状态流转;M1 仅形状) */}
      <div className="opsbar">
        <span className="lb">Status</span>
        <button className="btn btn-ghost">unclaim</button>
        <button className="stdrop">
          <i className="sq" style={{ background: `var(${STATUS_VAR[status]})` }} />
          {STATUS_WORD[status]}<ChevronDown />
        </button>
      </div>

      {/* [5] 面板编辑器(无 As Task) */}
      <Composer channelName="thread" variant="panel" hideAsTask onSend={(body) => onSend(body)} />
    </aside>
  );
}
