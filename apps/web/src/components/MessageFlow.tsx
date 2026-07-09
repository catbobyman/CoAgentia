// 消息流(设计稿 [E])。折叠头、日期分隔、未读线、系统消息、行内任务牌 —— 从 App.tsx 抽出。
import type { MemberPublic, MessagePublic, PresenceEntry, TaskPublic } from '@coagentia/contracts-ts';

import { PRESENCE_VAR } from '../lib/uiMaps';
import { renderBody } from '../lib/render';
import { Avatar } from './Avatar';
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
  onSelectTask?: (taskId: string) => void;
}

export function MessageFlow(props: MessageFlowProps) {
  const {
    messages, memberById, memberNames, meName, presenceOf,
    taskByRoot, usageByTask, lastReadId, selectedTaskId, onSelectTask,
  } = props;
  const lastReadIdx = messages.findIndex((m) => m.id === lastReadId);

  return (
    <section className="flow">
      {messages.map((m, i) => {
        const prev = messages[i - 1];
        const date = m.created_at.slice(5, 10);
        const newDay = !prev || prev.created_at.slice(5, 10) !== date;
        const author = m.author_member_id ? memberById[m.author_member_id] : null;
        const task = taskByRoot[m.id];
        const cont = !newDay && prev && prev.author_member_id === m.author_member_id
          && m.kind === 'user' && prev.kind === 'user';
        const pres = author ? presenceOf(author.id) : undefined;
        const usage = task ? usageByTask[task.id] : undefined;
        const ownerId = task?.owner_member_id ?? undefined;
        const owner = ownerId ? memberById[ownerId] : undefined;
        return (
          <div key={m.id}>
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
                    ? <span className="htime">{m.created_at.slice(11, 16)}</span>
                    : author && <Avatar name={author.name} presence={pres} size="msg" />}
                </div>
                <div>
                  {!cont && author && (
                    <div className="hd">
                      <span className="nm">{author.name}</span>
                      {pres && (
                        <span
                          className={`pp${pres.status === 'busy' ? ' pulse' : ''}`}
                          style={{ background: `var(${PRESENCE_VAR[pres.status]})` }}
                        />
                      )}
                      <span className="ts">{m.created_at.slice(11, 16)}</span>
                      {pres?.busy_detail && <span className="tail">{pres.busy_detail}</span>}
                    </div>
                  )}
                  <div className="body" dangerouslySetInnerHTML={{ __html: renderBody(m.body, memberNames, meName) }} />
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
