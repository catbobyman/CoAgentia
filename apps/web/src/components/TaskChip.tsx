// 行内任务牌(设计规范 §2.1)。selected = 深链 ?task= 命中时高亮(视图状态还原)。
import type { MemberPublic, TaskPublic } from '@coagentia/contracts-ts';

import { STATUS_VAR, STATUS_WORD } from '../lib/uiMaps';
import { Avatar } from './Avatar';

export function TaskChip({ task, owner, usage, selected, onClick }: {
  task: TaskPublic;
  owner?: MemberPublic;
  usage?: number;
  selected?: boolean;
  onClick?: () => void;
}) {
  const status = task.status ?? 'todo';
  return (
    <div className={`taskchip${selected ? ' selected' : ''}`} onClick={onClick}>
      <span className="bar" style={{ background: `var(${STATUS_VAR[status]})` }} />
      <span className="no">#{task.number}</span>
      <span className="stw">{STATUS_WORD[status]}</span>
      {owner && (
        <span className="who"><Avatar name={owner.name} size="nav" />{owner.name}</span>
      )}
      <span className="ttl">{task.title}</span>
      {usage !== undefined && <span className="tokbadge">{(usage / 1000).toFixed(1)}k tok</span>}
    </div>
  );
}
