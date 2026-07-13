// 消息流(设计稿 [E])。折叠头、日期分隔、未读线、系统消息、行内任务牌、附件卡 —— 从 App.tsx 抽出。
// M6b:card_kind==='proposal' 的消息 → 正文剥离 <control> 块显示散文(lib/decomposition.stripControl,
// 勿重写定界)+ 渲染提案卡(ProposalCard,数据源 GET /proposals/{card_ref} + WS 实时刷新)。
import { useEffect } from 'react';
import { ExternalLink, GitMerge } from 'lucide-react';

import type { MemberPublic, MessagePublic, PresenceEntry, TaskPublic } from '@coagentia/contracts-ts';

import { PRESENCE_VAR } from '../lib/uiMaps';
import { stripControl } from '../lib/decomposition';
import { renderBody } from '../lib/render';
import { fmtDate, fmtTime } from '../lib/time';
import { Avatar } from './Avatar';
import { AttachCard } from './AttachCard';
import { ProposalCard } from './ProposalCard';
import { TaskChip } from './TaskChip';

export interface MessageFlowProps {
  messages: MessagePublic[];
  memberById: Record<string, MemberPublic>;
  memberNames: string[];
  meName: string;
  presenceOf: (memberId: string) => PresenceEntry | undefined;
  taskByRoot: Record<string, TaskPublic>;
  usageByTask: Record<string, number>;
  lastReadId?: string;
  selectedTaskId?: string;
  locateId?: string; // 「定位到消息」目标:滚动至该消息并闪烁高亮(P4 → 会话流)
  onLocateDone?: () => void;
  onSelectTask?: (taskId: string) => void;
  onOpenAgent?: (memberId: string) => void; // 点击 Agent 头像/名进入 P6 详情
  // M6b 提案卡入口(可选——未传则卡内隐藏对应入口):
  onReviewProposal?: (proposalId: string) => void; // full「查看草稿/在画布中审阅」→ 切画布 + 激活草稿层
  onReviewDelta?: (proposalId: string) => void; // delta「审查增量」→ 切画布 + 激活 delta 面板
  onOpenProposalThread?: (message: MessagePublic) => void; // failed 态「查看线程」
}

/** J5 冲突锚点正文的稳定段：`冲突文件:` 后连续 `- path` 行；遇到其他正文立即停止。 */
export function parseConflictFiles(body: string): string[] {
  const lines = body.split(/\r?\n/);
  const start = lines.findIndex((line) => line.trim() === '冲突文件:');
  if (start < 0) return [];
  const files: string[] = [];
  for (const line of lines.slice(start + 1)) {
    const match = /^\s*-\s+(.+?)\s*$/.exec(line);
    if (!match) break;
    files.push(match[1]!);
  }
  return files;
}

export function MessageFlow(props: MessageFlowProps) {
  const {
    messages, memberById, memberNames, meName, presenceOf, taskByRoot, usageByTask,
    lastReadId, selectedTaskId, locateId, onLocateDone, onSelectTask, onOpenAgent,
    onReviewProposal, onReviewDelta, onOpenProposalThread,
  } = props;
  const lastReadIdx = messages.findIndex((m) => m.id === lastReadId);

  useEffect(() => {
    if (!locateId) return;
    const el = document.getElementById(`msg-${locateId}`);
    if (!el) {
      onLocateDone?.(); // 目标不在已加载窗口内:只切页签,不装作定位成功
      return;
    }
    el.scrollIntoView({ block: 'center' });
    el.classList.add('locate-flash');
    const t = setTimeout(() => {
      el.classList.remove('locate-flash');
      onLocateDone?.();
    }, 1600);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [locateId, messages.length]);

  return (
    <section className="flow">
      {messages.map((m, i) => {
        const prev = messages[i - 1];
        const date = fmtDate(m.created_at);
        const newDay = !prev || fmtDate(prev.created_at) !== date;
        const author = m.author_member_id ? memberById[m.author_member_id] : null;
        const task = taskByRoot[m.id];
        const cont = !newDay && prev && prev.author_member_id === m.author_member_id
          && m.kind === 'user' && prev.kind === 'user';
        const pres = author ? presenceOf(author.id) : undefined;
        const usage = task ? usageByTask[task.id] : undefined;
        const ownerId = task?.owner_member_id ?? undefined;
        const owner = ownerId ? memberById[ownerId] : undefined;
        const conflictFiles = m.kind === 'system' && m.card_kind === 'merge_conflict' ? parseConflictFiles(m.body) : [];
        // 机读体不进消息流(M6 review F10):任意含 <control> 的正文一律剥离(定界复用内核
        // 镜像,勿重写)——只剥有卡消息会让修复循环首发的无效提案(无 card_ref)整段原始
        // JSON 泄漏进人类会话;剥空则给占位说明。提案卡单独渲染(isProposal)。
        const isProposal = m.card_kind === 'proposal' && !!m.card_ref;
        const hasControl = m.body.includes('<control>');
        const stripped = hasControl ? stripControl(m.body) : m.body;
        const bodyText = hasControl && stripped === ''
          ? '（机读控制块已交系统处理，正文无散文）'
          : stripped;
        return (
          <div key={m.id} id={`msg-${m.id}`}>
            {newDay && <div className="datesep"><span>{date}</span></div>}
            {i === lastReadIdx + 1 && lastReadIdx >= 0 && (
              <div className="unreadline"><span className="ln" /><span>新消息</span></div>
            )}
            {m.kind === 'system' ? (
              <div className="system-entry">
                <div className="sysmsg">
                  <span className="sys">系统</span>
                  <span dangerouslySetInnerHTML={{ __html: renderBody(m.body, memberNames, meName) }} />
                </div>
                {task && m.card_kind === 'merge_conflict' ? (
                  <div className="conflict-task-card">
                    <div className="conflict-title"><GitMerge /><span>冲突文件 · {conflictFiles.length}</span></div>
                    <ul>{conflictFiles.map((path) => <li key={path}>{path}</li>)}</ul>
                    <button type="button" aria-label={`打开冲突任务 #${task.number}`} onClick={() => onSelectTask?.(task.id)}>
                      <span>#{task.number} {task.title}</span><ExternalLink />
                    </button>
                  </div>
                ) : task ? (
                  <TaskChip
                    task={task} owner={owner} usage={usage}
                    selected={selectedTaskId === task.id}
                    onClick={() => onSelectTask?.(task.id)}
                  />
                ) : null}
              </div>
            ) : (
              <div className={`msg${cont ? ' cont' : ''}`}>
                <div className="avc">
                  {cont
                    ? <span className="htime">{fmtTime(m.created_at)}</span>
                    : author && (
                      <span
                        className={author.kind === 'agent' && onOpenAgent ? 'avlink' : undefined}
                        onClick={author.kind === 'agent' && onOpenAgent ? () => onOpenAgent(author.id) : undefined}
                      >
                        <Avatar name={author.name} presence={pres} size="msg" />
                      </span>
                    )}
                </div>
                <div>
                  {!cont && author && (
                    <div className="hd">
                      <span
                        className={author.kind === 'agent' && onOpenAgent ? 'nm nmlink' : 'nm'}
                        onClick={author.kind === 'agent' && onOpenAgent ? () => onOpenAgent(author.id) : undefined}
                      >{author.name}</span>
                      {pres && (
                        <span
                          className={`pp${pres.status === 'busy' ? ' pulse' : ''}`}
                          style={{ background: `var(${PRESENCE_VAR[pres.status]})` }}
                        />
                      )}
                      <span className="ts">{fmtTime(m.created_at)}</span>
                      {pres?.busy_detail && <span className="tail">{pres.busy_detail}</span>}
                    </div>
                  )}
                  <div className="body" dangerouslySetInnerHTML={{ __html: renderBody(bodyText, memberNames, meName) }} />
                  {/* M6b 提案卡:card_ref = proposal_id,数据 GET /proposals/{id} + WS 实时刷新。 */}
                  {isProposal && (
                    <ProposalCard
                      proposalId={m.card_ref!}
                      onReviewInCanvas={onReviewProposal}
                      onReviewDelta={onReviewDelta}
                      onViewThread={onOpenProposalThread ? () => onOpenProposalThread(m) : undefined}
                    />
                  )}
                  {/* 附件卡数据源 = 消息读面派生 files(契约 A v1.0.4)——不再依赖 channelFiles 首页 ≤50 */}
                  {m.files?.map((f) => <AttachCard key={f.id} file={f} />)}
                  {task && (
                    <TaskChip
                      task={task}
                      owner={owner}
                      usage={usage}
                      selected={selectedTaskId === task.id}
                      onClick={() => onSelectTask?.(task.id)}
                    />
                  )}
                </div>
              </div>
            )}
          </div>
        );
      })}
    </section>
  );
}
