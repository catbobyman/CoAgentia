// M7 wsBridge preview.updated：按 preview.task_id patch qk.preview 缓存（daemon 状态流转反流）；
// 未加载（面板未开）不凭 WS 造缓存。照 wsBridge.worktree.test.ts 体例。
import { QueryClient } from '@tanstack/react-query';
import { describe, expect, it } from 'vitest';

import type { Envelope, PreviewSessionPublic } from '@coagentia/contracts-ts';

import { qk } from '../lib/queryKeys';
import { applyEnvelope } from './wsBridge';

const TASK_ID = 'task_1';

function session(over: Partial<PreviewSessionPublic> = {}): PreviewSessionPublic {
  return {
    id: 'prev_1', workspace_id: 'ws_1', task_id: TASK_ID, worktree_id: 'wt_1',
    status: 'starting', started_at: '2026-07-13T00:00:00Z',
    ...over,
  };
}

function envelope(preview: PreviewSessionPublic): Envelope {
  return {
    type: 'preview.updated', workspace_id: 'ws_1', channel_id: 'ch_1', seq: 12,
    key: `preview:${preview.id}`, at: '2026-07-13T00:00:01Z', data: { preview },
  } as Envelope;
}

describe('wsBridge preview.updated', () => {
  it('已加载时按 task_id 替换 qk.preview（starting→running 携 port），重复信封幂等', () => {
    const qc = new QueryClient();
    qc.setQueryData(qk.preview(TASK_ID), session({ status: 'starting' }));

    const running = session({ status: 'running', port: 5173, last_active_at: '2026-07-13T00:00:01Z' });
    applyEnvelope(qc, envelope(running));
    applyEnvelope(qc, envelope(running));

    expect(qc.getQueryData<PreviewSessionPublic>(qk.preview(TASK_ID))).toEqual(running);
  });

  it('failed 携 fail_log_tail 反流覆盖', () => {
    const qc = new QueryClient();
    qc.setQueryData(qk.preview(TASK_ID), session({ status: 'starting' }));

    const failed = session({ status: 'failed', fail_log_tail: 'Error: port in use\n  at boot' });
    applyEnvelope(qc, envelope(failed));

    const got = qc.getQueryData<PreviewSessionPublic>(qk.preview(TASK_ID));
    expect(got?.status).toBe('failed');
    expect(got?.fail_log_tail).toMatch(/port in use/);
  });

  it('未加载（面板未开）时不凭 WS 造一份预览缓存', () => {
    const qc = new QueryClient();
    applyEnvelope(qc, envelope(session({ status: 'running', port: 5173 })));
    expect(qc.getQueryData(qk.preview(TASK_ID))).toBeUndefined();
  });
});
