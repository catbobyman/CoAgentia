// P3 频道看板页签(对照 P3-board.html):5 态分列 + Done/Closed 默认折叠窄条 + 拖列改状态。
// 拖前用 TASK_TRANSITIONS(纪律 7 单一事实源)校验合法目标列;非法列禁止放下 + toast。
// 移列成功靠 WS task.updated 实时回灌(wsBridge 已处理),无乐观更新;过渡 --t-slow(240ms)。
import { useMemo, useState } from 'react';
import { Lock, Play } from 'lucide-react';

import type {
  CanvasDetail,
  MemberPublic,
  PresenceEntry,
  TaskPublic,
  TaskStatus,
} from '@coagentia/contracts-ts';
import { TASK_TRANSITIONS } from '@coagentia/contracts-ts';

import { STATUS_VAR, STATUS_WORD } from '../lib/uiMaps';
import { deriveCanvasBlocked } from '../lib/graph';
import { useCanvasSnapshot } from '../data/queries';
import { api, ApiError } from '../api';
import { useToast } from './Toast';
import { Avatar } from './Avatar';
import { ForceStartModal } from './ForceStartModal';
import './board-tab.css';

const COLUMNS: TaskStatus[] = ['todo', 'in_progress', 'in_review', 'done', 'closed'];
const COLLAPSIBLE: TaskStatus[] = ['done', 'closed'];

/** 从频道画布快照派生「被阻塞的 task_id 集」(看板徽标消费,与画布同一 deriveBlocked 内核):
 *  agent 节点被 blocked 命中 → 其 task_id 入集。satisfied = 上游 agent 任务 done / system success。
 *  纯函数,可单测;跨频道聚合板(P11)对每个频道各调一次再并集。 */
export function blockedTaskIdsFromCanvas(
  detail: CanvasDetail | undefined,
  taskById: Record<string, TaskPublic>,
): Set<string> {
  const nodes = detail?.nodes ?? [];
  const edges = detail?.edges ?? [];
  // satisfied 组装 + deriveBlocked 走 lib/graph 单源(纪律 8,与画布 buildCanvasModel 同一处),
  // 避免看板徽标与画布着色因规则分写而打架。
  const { blocked: blockedNodes } = deriveCanvasBlocked(nodes, edges, taskById);
  const out = new Set<string>();
  for (const n of nodes) {
    if (n.kind === 'agent' && n.task_id && blockedNodes.has(n.id)) out.add(n.task_id);
  }
  return out;
}

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
  // force-start 目标:blocked 卡点「强制启动」置入,ForceStartModal 二次确认后清空。
  const [forceTask, setForceTask] = useState<TaskPublic | null>(null);

  // blocked 徽标:看板同频道,故 channelId 取自任一任务;自查该频道画布快照派生 blocked task_id 集
  // (与画布 deriveBlocked 同源,不必由父级穿透)。空画布/无任务时集为空,徽标自然不出。
  const channelId = tasks[0]?.channel_id;
  const canvasQ = useCanvasSnapshot(channelId);
  const taskById = useMemo(() => Object.fromEntries(tasks.map((t) => [t.id, t])), [tasks]);
  const blocked = useMemo(
    () => blockedTaskIdsFromCanvas(canvasQ.data, taskById),
    [canvasQ.data, taskById],
  );

  const byStatus = (s: TaskStatus) => tasks.filter((t) => (t.status ?? 'todo') === s);

  // 目标列是否可放下:非同列 且 in TASK_TRANSITIONS[from]。
  const canDrop = (col: TaskStatus) =>
    !!drag && drag.from !== col && (TASK_TRANSITIONS[drag.from] ?? []).includes(col);

  const onDrop = (col: TaskStatus) => {
    if (!drag) return;
    const ok = canDrop(col); // 合法性判定只有 canDrop 一份(与拖拽高亮同源)
    const { id, from } = drag;
    setDrag(null);
    if (!ok) {
      if (from !== col) {
        toast.push(`不能从 ${STATUS_WORD[from]} 流转到 ${STATUS_WORD[col]}`, { tone: 'error' });
      }
      return; // 同列无流转;非法目标弹回 + toast
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
    const isBlocked = blocked.has(t.id);
    return (
      <div
        key={t.id}
        className={`bcard${selectedTaskId === t.id ? ' sel' : ''}${drag?.id === t.id ? ' dragging' : ''}${isBlocked ? ' blocked' : ''}`}
        draggable
        onDragStart={(e) => {
          // Firefox 要求 dragstart 期间 setData,否则拖拽会话不启动(M2 二轮 review)。
          e.dataTransfer.setData('text/plain', t.id);
          setDrag({ id: t.id, from: st });
        }}
        onDragEnd={() => setDrag(null)}
        onClick={() => onSelectTask?.(t.id)}
      >
        <span className="no">#{t.number}</span>
        <div className="ti">{t.title}</div>
        <div className="bt">
          <span className="bar" style={{ background: `var(${STATUS_VAR[st]})` }} />
          {owner && <Avatar name={owner.name} presence={presenceOf(owner.id)} size="nav" />}
          <span className="sp" />
          {isBlocked && (
            <span className="blkbadge" data-testid="board-blocked">
              <Lock /> blocked
            </span>
          )}
        </div>
        {isBlocked && (
          <button
            className="fsbtn"
            data-testid="board-force-start"
            title="强制启动(越过 gating,留痕)"
            onClick={(e) => {
              e.stopPropagation(); // 不触发选牌/打开线程
              setForceTask(t);
            }}
          >
            <Play /> 强制启动
          </button>
        )}
      </div>
    );
  };

  return (
    <>
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
    {forceTask && <ForceStartModal task={forceTask} onClose={() => setForceTask(null)} />}
    </>
  );
}
