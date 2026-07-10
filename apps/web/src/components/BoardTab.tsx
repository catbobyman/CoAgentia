// P3 频道看板页签(对照 P3-board.html):5 态分列 + Done/Closed 默认折叠窄条 + 拖列改状态。
// 拖前用 TASK_TRANSITIONS(纪律 7 单一事实源)校验合法目标列;非法列禁止放下 + toast。
// 移列成功靠 WS task.updated 实时回灌(wsBridge 已处理),无乐观更新;过渡 --t-slow(240ms)。
import { useState } from 'react';

import type { MemberPublic, PresenceEntry, TaskPublic, TaskStatus } from '@coagentia/contracts-ts';
import { TASK_TRANSITIONS } from '@coagentia/contracts-ts';

import { STATUS_VAR, STATUS_WORD } from '../lib/uiMaps';
import { api, ApiError } from '../api';
import { useToast } from './Toast';
import { Avatar } from './Avatar';
import './board-tab.css';

const COLUMNS: TaskStatus[] = ['todo', 'in_progress', 'in_review', 'done', 'closed'];
const COLLAPSIBLE: TaskStatus[] = ['done', 'closed'];

export function BoardTab({ tasks, memberById, presenceOf, selectedTaskId, onSelectTask }: {
  tasks: TaskPublic[];
  memberById: Record<string, MemberPublic>;
  presenceOf: (memberId: string) => PresenceEntry | undefined;
  selectedTaskId?: string;
  onSelectTask?: (taskId: string) => void;
}) {
  const toast = useToast();
  // 拖拽态:{id, from} 于 dragStart 落定,dragEnd 清除(驱动合法列高亮 + 落列校验)。
  const [drag, setDrag] = useState<{ id: string; from: TaskStatus } | null>(null);
  // Done/Closed 默认折叠;点开加入展开集合。
  const [expanded, setExpanded] = useState<Set<TaskStatus>>(new Set());

  const byStatus = (s: TaskStatus) => tasks.filter((t) => (t.status ?? 'todo') === s);

  // 目标列是否可放下:非同列 且 in TASK_TRANSITIONS[from]。
  const canDrop = (col: TaskStatus) =>
    !!drag && drag.from !== col && (TASK_TRANSITIONS[drag.from] ?? []).includes(col);

  const onDrop = (col: TaskStatus) => {
    if (!drag) return;
    const { id, from } = drag;
    setDrag(null);
    if (from === col) return; // 同列:无流转
    if (!(TASK_TRANSITIONS[from] ?? []).includes(col)) {
      toast.push(`不能从 ${STATUS_WORD[from]} 流转到 ${STATUS_WORD[col]}`, { tone: 'error' });
      return; // 非法目标:弹回 + toast
    }
    void api.setTaskStatus(id, col).catch((e: unknown) => {
      if (e instanceof ApiError && e.code === 'TASK_TRANSITION_INVALID') {
        toast.push('非法流转:目标状态不被当前状态允许', { tone: 'error' });
      } else if (e instanceof ApiError) {
        toast.push(e.message, { tone: 'error' });
      } else {
        toast.push('流转失败', { tone: 'error' });
      }
    });
  };

  const renderCard = (t: TaskPublic) => {
    const st = t.status ?? 'todo';
    const owner = t.owner_member_id ? memberById[t.owner_member_id] : undefined;
    return (
      <div
        key={t.id}
        className={`bcard${selectedTaskId === t.id ? ' sel' : ''}${drag?.id === t.id ? ' dragging' : ''}`}
        draggable
        onDragStart={() => setDrag({ id: t.id, from: st })}
        onDragEnd={() => setDrag(null)}
        onClick={() => onSelectTask?.(t.id)}
      >
        <span className="no">#{t.number}</span>
        <div className="ti">{t.title}</div>
        <div className="bt">
          <span className="bar" style={{ background: `var(${STATUS_VAR[st]})` }} />
          {owner && <Avatar name={owner.name} presence={presenceOf(owner.id)} size="nav" />}
          <span className="sp" />
        </div>
      </div>
    );
  };

  return (
    <section className="boardtab">
      {COLUMNS.map((col) => {
        const list = byStatus(col);
        const collapsed = COLLAPSIBLE.includes(col) && !expanded.has(col);
        if (collapsed) {
          return (
            <div
              key={col}
              className={`bcolfold${canDrop(col) ? ' drop-ok' : ''}`}
              aria-label={`${STATUS_WORD[col]},点击展开`}
              onClick={() => setExpanded((prev) => new Set(prev).add(col))}
              onDragOver={(e) => { if (canDrop(col)) e.preventDefault(); }}
              onDrop={() => onDrop(col)}
            >
              <i className="sq" style={{ background: `var(${STATUS_VAR[col]})` }} />
              <span className="vt">{STATUS_WORD[col]}</span>
              <span className="n">{list.length}</span>
              <span className="ar">▸</span>
            </div>
          );
        }
        return (
          <div
            key={col}
            className={`bcol${canDrop(col) ? ' drop-ok' : ''}`}
            onDragOver={(e) => { if (canDrop(col)) e.preventDefault(); }}
            onDrop={() => onDrop(col)}
          >
            <div className="bcolhd">
              <i style={{ background: `var(${STATUS_VAR[col]})` }} />
              <b>{STATUS_WORD[col]}</b>
              <span className="n">{list.length}</span>
              {COLLAPSIBLE.includes(col) && (
                <span
                  className="fold"
                  aria-label="折叠"
                  onClick={(e) => {
                    e.stopPropagation();
                    setExpanded((prev) => {
                      const next = new Set(prev);
                      next.delete(col);
                      return next;
                    });
                  }}
                >▾</span>
              )}
            </div>
            {list.length === 0
              ? <div className="bempty">空</div>
              : list.map(renderCard)}
          </div>
        );
      })}
    </section>
  );
}
