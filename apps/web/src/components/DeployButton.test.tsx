// M7b [部署] 按钮 + 确认弹窗：弹窗展示 branch@hash / deploy command / 触发者；确认 → 空体 POST；
// 409 DEPLOY_IN_PROGRESS → toast「上一次部署进行中」；无 deploy_command → 确认按钮禁用 + 提示。
// 运行:pnpm -F @coagentia/web test
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return { ...actual, api: { ...actual.api, createDeployment: vi.fn() } };
});

import type { DeploymentPublic } from '@coagentia/contracts-ts';

import { api, ApiError } from '../api';
import { ToastProvider, Toaster } from './Toast';
import { DeployButton } from './DeployButton';

const DEPLOYMENT: DeploymentPublic = {
  id: 'deploy_1', workspace_id: 'ws_1', project_id: 'project_1',
  triggered_by_member_id: 'mem_owner', branch: 'main', command: 'npm run deploy', status: 'queued',
};

function renderBtn(props: Partial<React.ComponentProps<typeof DeployButton>> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onDeployed = vi.fn();
  render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <DeployButton
          projectId="project_1"
          deployCommand="npm run deploy"
          triggererName="Memcyo"
          onDeployed={onDeployed}
          {...props}
        />
        <Toaster />
      </ToastProvider>
    </QueryClientProvider>,
  );
  return { onDeployed };
}

describe('DeployButton 确认弹窗', () => {
  beforeEach(() => vi.clearAllMocks());

  it('点击 [部署] 打开弹窗，展示 branch@hash / deploy command / 触发者', () => {
    renderBtn();
    fireEvent.click(screen.getByTestId('deploy-btn'));
    expect(screen.getByTestId('deploy-confirm')).toBeInTheDocument();
    expect(screen.getByTestId('deploy-branch')).toHaveTextContent(/主干 HEAD/);
    expect(screen.getByTestId('deploy-command')).toHaveTextContent('npm run deploy');
    expect(screen.getByTestId('deploy-triggerer')).toHaveTextContent('Memcyo');
  });

  it('确认 → 空体 POST createDeployment(projectId)，成功回调带 deployment_id', async () => {
    vi.mocked(api.createDeployment).mockResolvedValue(DEPLOYMENT);
    const { onDeployed } = renderBtn();
    fireEvent.click(screen.getByTestId('deploy-btn'));
    fireEvent.click(screen.getByTestId('deploy-confirm-btn'));
    await waitFor(() => expect(api.createDeployment).toHaveBeenCalledWith('project_1'));
    await waitFor(() => expect(onDeployed).toHaveBeenCalledWith('deploy_1'));
    // 弹窗关闭。
    expect(screen.queryByTestId('deploy-confirm')).not.toBeInTheDocument();
  });

  it('进行中 409 DEPLOY_IN_PROGRESS → toast「上一次部署进行中」', async () => {
    vi.mocked(api.createDeployment).mockRejectedValue(
      new ApiError(409, 'DEPLOY_IN_PROGRESS', '进行中'),
    );
    renderBtn();
    fireEvent.click(screen.getByTestId('deploy-btn'));
    fireEvent.click(screen.getByTestId('deploy-confirm-btn'));
    await waitFor(() => expect(screen.getByText('上一次部署进行中')).toBeInTheDocument());
  });

  it('无 deploy_command → 确认按钮禁用 + 提示，不发 POST', () => {
    renderBtn({ deployCommand: null });
    fireEvent.click(screen.getByTestId('deploy-btn'));
    expect(screen.getByTestId('deploy-confirm-btn')).toBeDisabled();
    expect(screen.getByRole('alert')).toHaveTextContent(/未配置部署命令/);
    fireEvent.click(screen.getByTestId('deploy-confirm-btn'));
    expect(api.createDeployment).not.toHaveBeenCalled();
  });

  it('未绑定 Project（projectId 缺失）→ [部署] 按钮禁用', () => {
    renderBtn({ projectId: undefined });
    expect(screen.getByTestId('deploy-btn')).toBeDisabled();
  });
});
