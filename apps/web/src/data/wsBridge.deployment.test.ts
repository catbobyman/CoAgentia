// M7b wsBridge deployment.*：created/updated 按 deployment.id patch qk.deployment（未加载不造缓存）；
// deployment.log 追加 qk.deploymentLog 累积（未播种不造缓存）。照 wsBridge.preview.test.ts 体例。
import { QueryClient } from '@tanstack/react-query';
import { describe, expect, it } from 'vitest';

import type { DeploymentPublic, Envelope } from '@coagentia/contracts-ts';

import { qk } from '../lib/queryKeys';
import { EMPTY_DEPLOY_LOG, type DeployLogState } from './deployLog';
import { applyEnvelope } from './wsBridge';

const DEP_ID = 'deploy_1';

function deployment(over: Partial<DeploymentPublic> = {}): DeploymentPublic {
  return {
    id: DEP_ID, workspace_id: 'ws_1', project_id: 'project_1',
    triggered_by_member_id: 'mem_owner', branch: 'main', command: 'npm run deploy',
    status: 'queued', ...over,
  };
}

function depEnvelope(type: 'deployment.created' | 'deployment.updated', d: DeploymentPublic): Envelope {
  return {
    type, workspace_id: 'ws_1', channel_id: null, seq: 5,
    key: `deploy:${d.id}`, at: '2026-07-13T00:00:00Z', data: { deployment: d },
  } as Envelope;
}

function logEnvelope(chunkSeq: number, lines: string[]): Envelope {
  return {
    type: 'deployment.log', workspace_id: 'ws_1', channel_id: null, seq: 6,
    key: `deploy:${DEP_ID}:${chunkSeq}`, at: '2026-07-13T00:00:01Z',
    data: { deployment_id: DEP_ID, chunk_seq: chunkSeq, lines },
  } as Envelope;
}

describe('wsBridge deployment.created/updated', () => {
  it('已加载时按 id 替换 qk.deployment（queued→running→success 反流），重复幂等', () => {
    const qc = new QueryClient();
    qc.setQueryData(qk.deployment(DEP_ID), deployment({ status: 'queued' }));

    const running = deployment({ status: 'running', started_at: '2026-07-13T00:00:00Z' });
    applyEnvelope(qc, depEnvelope('deployment.updated', running));
    expect(qc.getQueryData<DeploymentPublic>(qk.deployment(DEP_ID))?.status).toBe('running');

    const success = deployment({
      status: 'success', exit_code: 0, url: 'https://x.example.com',
      started_at: '2026-07-13T00:00:00Z', finished_at: '2026-07-13T00:01:00Z',
    });
    applyEnvelope(qc, depEnvelope('deployment.updated', success));
    applyEnvelope(qc, depEnvelope('deployment.updated', success));
    expect(qc.getQueryData<DeploymentPublic>(qk.deployment(DEP_ID))).toEqual(success);
  });

  it('failed 反流覆盖 exit_code', () => {
    const qc = new QueryClient();
    qc.setQueryData(qk.deployment(DEP_ID), deployment({ status: 'running' }));
    applyEnvelope(qc, depEnvelope('deployment.updated', deployment({ status: 'failed', exit_code: 1 })));
    const got = qc.getQueryData<DeploymentPublic>(qk.deployment(DEP_ID));
    expect(got?.status).toBe('failed');
    expect(got?.exit_code).toBe(1);
  });

  it('未加载（卡未挂载）时不凭 WS 造一份部署缓存', () => {
    const qc = new QueryClient();
    applyEnvelope(qc, depEnvelope('deployment.created', deployment({ status: 'running' })));
    expect(qc.getQueryData(qk.deployment(DEP_ID))).toBeUndefined();
  });
});

describe('wsBridge deployment.log', () => {
  it('历史已并入后把 lines 按 chunk_seq 追加到累积缓存尾部（多帧顺序拼接）', () => {
    const qc = new QueryClient();
    // historyLoaded=true 表示历史首页已并入（R-14）——此后 live 块直接按 seq 去重追加。
    qc.setQueryData<DeployLogState>(qk.deploymentLog(DEP_ID), { ...EMPTY_DEPLOY_LOG, historyLoaded: true });
    applyEnvelope(qc, logEnvelope(0, ['building…', 'step 1']));
    applyEnvelope(qc, logEnvelope(1, ['step 2', 'done']));
    expect(qc.getQueryData<DeployLogState>(qk.deploymentLog(DEP_ID))?.lines).toEqual([
      'building…', 'step 1', 'step 2', 'done',
    ]);
  });

  it('R-14：WS 重连重投同 chunk_seq → 按 seq 单调去重只并一次（不按行文本去重）', () => {
    const qc = new QueryClient();
    qc.setQueryData<DeployLogState>(qk.deploymentLog(DEP_ID), { ...EMPTY_DEPLOY_LOG, historyLoaded: true });
    applyEnvelope(qc, logEnvelope(0, ['l0']));
    applyEnvelope(qc, logEnvelope(1, ['l1']));
    applyEnvelope(qc, logEnvelope(1, ['l1']));  // 重连重投 seq=1 → 去重
    applyEnvelope(qc, logEnvelope(0, ['l0']));  // 迟到重投 seq=0 → 去重
    expect(qc.getQueryData<DeployLogState>(qk.deploymentLog(DEP_ID))?.lines).toEqual(['l0', 'l1']);
  });

  it('R-14：历史首页并入前到达的 live 块进 pending 缓冲，不落 lines', () => {
    const qc = new QueryClient();
    qc.setQueryData<DeployLogState>(qk.deploymentLog(DEP_ID), EMPTY_DEPLOY_LOG);  // historyLoaded=false
    applyEnvelope(qc, logEnvelope(2, ['live-a']));
    const state = qc.getQueryData<DeployLogState>(qk.deploymentLog(DEP_ID));
    expect(state?.lines).toEqual([]);  // 缓冲，未落尾
    expect(state?.pending).toEqual([{ seq: 2, lines: ['live-a'] }]);
  });

  it('未播种（未打开日志视图）时不凭 WS 造日志缓存', () => {
    const qc = new QueryClient();
    applyEnvelope(qc, logEnvelope(0, ['orphan line']));
    expect(qc.getQueryData(qk.deploymentLog(DEP_ID))).toBeUndefined();
  });
});
