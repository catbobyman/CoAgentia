// 消息流(设计稿 [E])。折叠头、日期分隔、未读线、系统消息、行内任务牌、附件卡 —— 从 App.tsx 抽出。
// M6b:card_kind==='proposal' 的消息 → 正文剥离 <control> 块显示散文(lib/decomposition.stripControl,
// 勿重写定界)+ 渲染提案卡(ProposalCard,数据源 GET /proposals/{card_ref} + WS 实时刷新)。
import { useEffect } from 'react';
import { Copy, ExternalLink, GitMerge, Link, ListPlus, Reply } from 'lucide-react';

import type { MemberPublic, MessagePublic, PresenceEntry, TaskPublic } from '@coagentia/contracts-ts';

import { PRESENCE_VAR } from '../lib/uiMaps';
import { stripControl } from '../lib/decomposition';
import { renderBody } from '../lib/render';
import { fmtDate, fmtTime } from '../lib/time';
import { Avatar } from './Avatar';
import { AttachCard } from './AttachCard';
import { DeploymentCard } from './DeploymentCard';
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
  // F5 逐条消息 hover 动作菜单（未传对应回调 → 该动作不显示；Copy text/link 恒有，纯前端）：
  onReplyInThread?: (message: MessagePublic) => void; // ④ 在线程中回复（root = thread_root_id ?? id）
  onConvertToTask?: (message: MessagePublic) => void; // ③ 转为任务（仅顶级频道消息，DM/线程回复不显示）
  canConvertToTask?: boolean; // 该视图是否承载任务（DM/线程 = false → 隐藏「转为任务」）
  onToast?: (msg: string) => void; // Copy 成功反馈（未传则静默）
}

/** F5 逐条消息 hover 动作条（Slack 体例，右上浮出，≤4 图标）。Copy text/link 纯前端；Reply/Convert
 *  经父回调。转任务仅对「顶级频道消息且尚非任务」显示（T3/DM 不承载任务，PRD §4.9）。 */
function MessageActions({ message, isTask, canConvert, onReply, onConvert, onToast }: {
  message: MessagePublic;
  isTask: boolean;
  canConvert: boolean;
  onReply?: (m: MessagePublic) => void;
  onConvert?: (m: MessagePublic) => void;
  onToast?: (msg: string) => void;
}) {
  const copy = (text: string, label: string) => {
    if (typeof navigator === 'undefined' || !navigator.clipboard) return;
    void navigator.clipboard.writeText(text).then(() => onToast?.(label)).catch(() => {});
  };
  // Copy link：深链统一格式 ?thread=<root>&msg=<id>（依赖 M8a 深链修复才算完整；本批先以"能还原
  // 频道+线程"为准——B-M8-1 落地后自动兼容）。
  const copyLink = () => {
    const root = message.thread_root_id ?? message.id;
    const { origin, pathname } = window.location;
    copy(`${origin}${pathname}?thread=${root}&msg=${message.id}`, '已复制链接');
  };
  // 排除承载卡片的消息（提案卡/部署卡/未来任何 user-message card）——转任务会与卡片叠加、语义错乱。
  const showConvert = !!onConvert && canConvert && !isTask && !message.thread_root_id && !message.card_kind;
  return (
    <div className="msg-actions" role="toolbar" aria-label="消息操作">
      <button type="button" className="msg-act" aria-label="复制文本" title="复制文本"
        onClick={() => copy(message.body, '已复制文本')}><Copy /></button>
      <button type="button" className="msg-act" aria-label="复制链接" title="复制链接"
        onClick={copyLink}><Link /></button>
      {onReply && (
        <button type="button" className="msg-act" aria-label="在线程中回复" title="在线程中回复"
          onClick={() => onReply(message)}><Reply /></button>
      )}
      {showConvert && (
        <button type="button" className="msg-act" aria-label="转为任务" title="转为任务"
          onClick={() => onConvert!(message)}><ListPlus /></button>
      )}
    </div>
  );
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
    onReplyInThread, onConvertToTask, canConvertToTask = false, onToast,
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
        const isDeployment = m.card_kind === 'deployment' && !!m.card_ref;
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
                {/* M7b 部署结果卡（card_kind=deployment，结果卡走系统消息）：card_ref = deployment_id。 */}
                {isDeployment && <DeploymentCard deploymentId={m.card_ref!} />}
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
                {/* Copy text/link 恒在；Reply/Convert 由回调决定是否显示。 */}
                <MessageActions
                  message={m}
                  isTask={!!task}
                  canConvert={canConvertToTask}
                  onReply={onReplyInThread}
                  onConvert={onConvertToTask}
                  onToast={onToast}
                />
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
                  {isDeployment && <DeploymentCard deploymentId={m.card_ref!} />}
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
