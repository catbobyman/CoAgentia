import { QueryClient } from '@tanstack/react-query';
import { describe, expect, it } from 'vitest';

import type { Envelope, TaskDetail, TaskPublic, WorktreePublic } from '@coagentia/contracts-ts';

import { qk } from '../lib/queryKeys';
import { applyEnvelope } from './wsBridge';

const TASK: TaskPublic = {
  id: 'task_1', channel_id: 'ch_1', workspace_id: 'ws_1', number: 1,
  title: '实现交付链', status: 'in_progress', root_message_id: 'msg_1',
  created_by_member_id: 'mem_1', created_at: '2026-07-11T00:00:00Z',
  status_changed_at: '2026-07-11T00:00:00Z', writes_code: true, project_id: 'project_1',
};

const WORKTREE: WorktreePublic = {
  id: 'wt_1', workspace_id: 'ws_1', project_id: 'project_1', task_id: TASK.id,
  branch: 'coagentia/task-task_1', path: 'D:/worktrees/project_1/task_1', status: 'active',
  created_at: '2026-07-11T00:00:00Z',
};

function envelope(worktree: WorktreePublic): Envelope {
  return {
    type: 'worktree.updated', workspace_id: 'ws_1', channel_id: 'ch_1', seq: 9,
    key: `worktree:${worktree.id}`, at: '2026-07-11T00:00:01Z', data: { worktree },
  } as Envelope;
}

describe('wsBridge worktree.updated', () => {
  it('只替换已加载 TaskDetail.worktree，重复信封幂等，并失效 Diff', () => {
    const qc = new QueryClient();
    const detail: TaskDetail = { task: TASK, usage: {}, contracts: [], worktree: null };
    qc.setQueryData(qk.taskDetail(TASK.id), detail);
    qc.setQueryData(qk.taskDiff(TASK.id), { base_ref: 'main', head_ref: 'head', files: [] });

    applyEnvelope(qc, envelope(WORKTREE));
    applyEnvelope(qc, envelope(WORKTREE));

    expect(qc.getQueryData<TaskDetail>(qk.taskDetail(TASK.id))?.worktree).toEqual(WORKTREE);
    expect(qc.getQueryState(qk.taskDiff(TASK.id))?.isInvalidated).toBe(true);
  });

  it('详情未加载时不凭 WS 造一份不完整缓存', () => {
    const qc = new QueryClient();
    applyEnvelope(qc, envelope(WORKTREE));
    expect(qc.getQueryData(qk.taskDetail(TASK.id))).toBeUndefined();
  });
});
