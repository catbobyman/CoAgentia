// M7b 消息流部署卡路由：card_kind==='deployment' 且有 card_ref → 渲染 DeploymentCard（结果卡走系统
// 消息）；无 card_kind 不触发。照 MessageFlow.conflict.test.tsx 体例。
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return { ...actual, api: { ...actual.api, getDeployment: vi.fn(), deploymentLog: vi.fn() } };
});
vi.mock('../data/wsUplink', () => ({
  subscribeDeployLog: vi.fn(),
  unsubscribeDeployLog: vi.fn(),
}));

import type { DeploymentPublic, MessagePublic } from '@coagentia/contracts-ts';

import { api } from '../api';
import { MessageFlow } from './MessageFlow';

const DEPLOYMENT: DeploymentPublic = {
  id: 'deploy_1', workspace_id: 'ws_1', project_id: 'project_1',
  triggered_by_member_id: 'mem_owner', branch: 'main', command: 'npm run deploy',
  status: 'success', exit_code: 0, url: 'https://app.example.com',
  started_at: '2026-07-13T00:00:00Z', finished_at: '2026-07-13T00:00:30Z',
};

const MESSAGE: MessagePublic = {
  id: 'msg_dep', workspace_id: 'ws_1', channel_id: 'ch_1', kind: 'system',
  card_kind: 'deployment', card_ref: 'deploy_1',
  body: '部署成功 · https://app.example.com · 耗时 30s · 退出码 0',
  created_at: '2026-07-13T00:00:30Z',
};

function renderFlow(message: MessagePublic) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MessageFlow
        messages={[message]} memberById={{}} memberNames={[]} meName="Memcyo"
        presenceOf={() => undefined} taskByRoot={{}} usageByTask={{}}
      />
    </QueryClientProvider>,
  );
}

describe('消息流部署卡（card_kind=deployment）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getDeployment).mockResolvedValue(DEPLOYMENT);
    vi.mocked(api.deploymentLog).mockResolvedValue({ lines: [], next_after: null, truncated: false });
  });

  it('渲染部署卡并按 card_ref 拉取', async () => {
    renderFlow(MESSAGE);
    await waitFor(() => expect(screen.getByTestId('deployment-card')).toBeInTheDocument());
    expect(api.getDeployment).toHaveBeenCalledWith('deploy_1');
    expect(await screen.findByTestId('deployment-result')).toHaveTextContent('退出码 0');
  });

  it('无 card_kind → 不触发部署卡', () => {
    renderFlow({ ...MESSAGE, card_kind: undefined, card_ref: undefined });
    expect(screen.queryByTestId('deployment-card')).not.toBeInTheDocument();
    expect(api.getDeployment).not.toHaveBeenCalled();
  });
});
