// PS-WT ① 目录选择器：导航（进入目录 → 发 useBrowseFs）/ 回退缓存（面包屑回退不重发同层）/ 回填
// （确认 → onPick 回传当前目录绝对路径）/ 非 git 黄条 / 截断提示 / denied 置灰不可进 / daemon 离线内联。
// 照 NewChannelModal.test 的 QueryClient + vi.mock('../api') 范式。运行:pnpm -F @coagentia/web test
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return { ...actual, api: { ...actual.api, browseFs: vi.fn() } };
});

import type { FsTreeEntry, FsTreeReply } from '@coagentia/contracts-ts';

import { api, ApiError } from '../api';
import { FolderPickerModal } from './FolderPickerModal';

const DRIVE_C: FsTreeEntry = { name: 'C:', path: 'C:/', has_git: false, denied: false };
const DRIVE_D: FsTreeEntry = { name: 'D:', path: 'D:/', has_git: false, denied: false };
const REPOS: FsTreeEntry = { name: 'repos', path: 'D:/repos', has_git: false, denied: false };
const DENIED: FsTreeEntry = { name: 'sysvol', path: 'D:/sysvol', has_git: false, denied: true };
const ALPHA: FsTreeEntry = { name: 'alpha', path: 'D:/repos/alpha', has_git: true, denied: false };

function reply(entries: FsTreeEntry[], truncated = false): FsTreeReply {
  return { entries, truncated };
}

/** 按 path 分层的假盘符树；D:/repos 截断。 */
function mockTree() {
  vi.mocked(api.browseFs).mockImplementation((_cid: string, path?: string) => {
    if (path == null) return Promise.resolve(reply([DRIVE_C, DRIVE_D]));
    if (path === 'D:/') return Promise.resolve(reply([REPOS, DENIED]));
    if (path === 'D:/repos') return Promise.resolve(reply([ALPHA], true));
    return Promise.resolve(reply([]));
  });
}

function renderPicker(over?: { onPick?: (p: string) => void; onClose?: () => void }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={qc}>
      <FolderPickerModal
        computerId="computer_1"
        onPick={over?.onPick ?? (() => {})}
        onClose={over?.onClose ?? (() => {})}
      />
    </QueryClientProvider>,
  );
}

describe('FolderPickerModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockTree();
  });

  it('根视图列盘符；选择按钮在根禁用；进入盘符 → 发查询并列子目录', async () => {
    renderPicker();
    expect(await screen.findByRole('button', { name: 'D:' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'C:' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '选择此文件夹' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'D:' }));
    expect(await screen.findByRole('button', { name: 'repos' })).toBeInTheDocument();
    await waitFor(() => expect(api.browseFs).toHaveBeenCalledWith('computer_1', 'D:/'));
  });

  it('回退缓存：面包屑回退到已访问层不重发查询', async () => {
    renderPicker();
    fireEvent.click(await screen.findByRole('button', { name: 'D:' }));
    fireEvent.click(await screen.findByRole('button', { name: 'repos' }));
    await screen.findByRole('button', { name: 'alpha' });
    // 面包屑回退到 D:（此层 list 只有 alpha，'D:' 仅面包屑，无歧义）。
    fireEvent.click(screen.getByRole('button', { name: 'D:' }));
    expect(await screen.findByRole('button', { name: 'repos' })).toBeInTheDocument();
    const dCalls = vi.mocked(api.browseFs).mock.calls.filter((c) => c[1] === 'D:/').length;
    expect(dCalls).toBe(1);
  });

  it('确认回填当前目录绝对路径并关闭', async () => {
    const onPick = vi.fn();
    const onClose = vi.fn();
    renderPicker({ onPick, onClose });
    fireEvent.click(await screen.findByRole('button', { name: 'D:' }));
    await screen.findByRole('button', { name: 'repos' });
    fireEvent.click(screen.getByRole('button', { name: '选择此文件夹' }));
    expect(onPick).toHaveBeenCalledWith('D:/');
    expect(onClose).toHaveBeenCalled();
  });

  it('选中非 git 目录 → 黄条提示不阻止；进入 git 目录无黄条', async () => {
    renderPicker();
    fireEvent.click(await screen.findByRole('button', { name: 'D:' }));
    // 进入非 git 的 repos → 黄条。
    fireEvent.click(await screen.findByRole('button', { name: 'repos' }));
    expect(await screen.findByText(/不是 Git 仓库/)).toBeInTheDocument();
    // 选择按钮仍可用（不阻止）。
    expect(screen.getByRole('button', { name: '选择此文件夹' })).not.toBeDisabled();
    // 进入 git 的 alpha → 无黄条（列表异步加载，findBy 等待）。
    fireEvent.click(await screen.findByRole('button', { name: 'alpha' }));
    await waitFor(() => expect(screen.queryByText(/不是 Git 仓库/)).not.toBeInTheDocument());
  });

  it('截断 → 底部提示；denied 行置灰不可进', async () => {
    renderPicker();
    fireEvent.click(await screen.findByRole('button', { name: 'D:' }));
    // denied 行 disabled，点击不进入。
    const deniedBtn = await screen.findByRole('button', { name: 'sysvol' });
    expect(deniedBtn).toBeDisabled();
    // 进入 repos → 截断提示。
    fireEvent.click(screen.getByRole('button', { name: 'repos' }));
    expect(await screen.findByText(/已截断/)).toBeInTheDocument();
  });

  it('daemon 离线 → 内联提示，手输兜底文案', async () => {
    vi.mocked(api.browseFs).mockRejectedValue(new ApiError(503, 'DAEMON_OFFLINE', 'daemon 离线'));
    renderPicker();
    expect(await screen.findByRole('alert')).toHaveTextContent(/daemon 离线/);
    expect(screen.getByText(/手动输入路径/)).toBeInTheDocument();
  });
});
