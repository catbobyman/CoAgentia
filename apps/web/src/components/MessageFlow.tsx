// 消息流(设计稿 [E])。折叠头、日期分隔、未读线、系统消息、行内任务牌、附件卡 —— 从 App.tsx 抽出。
import { useEffect } from 'react';

import type { FilePublic, MemberPublic, MessagePublic, PresenceEntry, TaskPublic } from '@coagentia/contracts-ts';

import { PRESENCE_VAR } from '../lib/uiMaps';
import { renderBody } from '../lib/render';
import { fmtDate, fmtTime } from '../lib/time';
import { Avatar } from './Avatar';
import { AttachCard } from './AttachCard';
import { TaskChip } from './TaskChip';

export interface MessageFlowProps {
  messages: MessagePublic[];
  memberById: Record<string, MemberPublic>;
  memberNames: string[];
  meName: string;
  presenceOf: (memberId: string) => PresenceEntry | undefined;
  taskByRoot: Record<string, TaskPublic>;
  usageByTask: Record<string, number>;
  filesByMessage?: Record<string, FilePublic[]>; // 消息附件卡数据源(FR-4.8;M1 遗留并入 B-M2-3)
  lastReadId?: string;
  selectedTaskId?: string;
  locateId?: string; // 「定位到消息」目标:滚动至该消息并闪烁高亮(P4 → 会话流)
  onLocateDone?: () => void;
  onSelectTask?: (taskId: string) => void;
  onOpenAgent?: (memberId: string) => void; // 点击 Agent 头像/名进入 P6 详情
}

export function MessageFlow(props: MessageFlowProps) {
  const {
    messages, memberById, memberNames, meName, presenceOf, taskByRoot, usageByTask,
    filesByMessage, lastReadId, selectedTaskId, locateId, onLocateDone, onSelectTask, onOpenAgent,
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
        return (
          <div key={m.id} id={`msg-${m.id}`}>
            {newDay && <div className="datesep"><span>{date}</span></div>}
            {i === lastReadIdx + 1 && lastReadIdx >= 0 && (
              <div className="unreadline"><span className="ln" /><span>新消息</span></div>
            )}
            {m.kind === 'system' ? (
              <div className="sysmsg">
                <span className="sys">系统</span>
                <span dangerouslySetInnerHTML={{ __html: renderBody(m.body, memberNames, meName) }} />
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
                  <div className="body" dangerouslySetInnerHTML={{ __html: renderBody(m.body, memberNames, meName) }} />
                  {filesByMessage?.[m.id]?.map((f) => <AttachCard key={f.id} file={f} />)}
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
