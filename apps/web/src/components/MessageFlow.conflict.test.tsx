import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { MessagePublic, TaskPublic } from '@coagentia/contracts-ts';

import { MessageFlow, parseConflictFiles } from './MessageFlow';

const TASK: TaskPublic = {
  id: 'task_conflict', channel_id: 'ch_1', workspace_id: 'ws_1', number: 18,
  title: '解决冲突', status: 'todo', root_message_id: 'msg_conflict',
  created_by_member_id: 'mem_owner', created_at: '2026-07-11T00:00:00Z',
  status_changed_at: '2026-07-11T00:00:00Z', writes_code: true, project_id: 'project_1',
};

const MESSAGE: MessagePublic = {
  id: 'msg_conflict', workspace_id: 'ws_1', channel_id: 'ch_1', kind: 'system',
  card_kind: 'merge_conflict', // #6：结构化 marker，前端不再嗅探 body 文本
  body: 'merge 冲突\nnode_id: node_merge\n冲突文件:\n- src/app.ts\n- 中文/配置.json\n双方 Diff: #12 / #15',
  created_at: '2026-07-11T00:00:00Z',
};

describe('冲突任务卡', () => {
  it('只解析冲突文件清单，不吞后续正文', () => {
    expect(parseConflictFiles(MESSAGE.body)).toEqual(['src/app.ts', '中文/配置.json']);
  });

  it('系统锚点显示冲突文件与任务入口', () => {
    const select = vi.fn();
    render(
      <MessageFlow
        messages={[MESSAGE]} memberById={{}} memberNames={[]} meName="Memcyo"
        presenceOf={() => undefined} taskByRoot={{ [MESSAGE.id]: TASK }} usageByTask={{}}
        onSelectTask={select}
      />,
    );
    expect(screen.getByText('冲突文件 · 2')).toBeInTheDocument();
    expect(screen.getByText('src/app.ts')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /打开冲突任务 #18/ }));
    expect(select).toHaveBeenCalledWith(TASK.id);
  });

  it('正文貌似冲突清单但无 card_kind → 渲染任务牌而非假冲突卡（#6 负例）', () => {
    const fake: MessagePublic = { ...MESSAGE, card_kind: undefined };
    render(
      <MessageFlow
        messages={[fake]} memberById={{}} memberNames={[]} meName="Memcyo"
        presenceOf={() => undefined} taskByRoot={{ [fake.id]: TASK }} usageByTask={{}}
      />,
    );
    expect(screen.queryByText('冲突文件 · 2')).not.toBeInTheDocument();
    expect(screen.getByText(/#18/)).toBeInTheDocument(); // TaskChip 兜底
  });
});
