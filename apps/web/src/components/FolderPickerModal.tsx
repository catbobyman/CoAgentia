// PS-WT ① 目录选择器（共用组件，双入口：NewProjectModal + ProjectSettingsSection「浏览…」）。
// 面包屑 + 单层目录列表：每导航一发 useBrowseFs(cid, path)（react-query key=(cid,path)，回退秒开靠缓存）；
// 根视图 = 盘符列表（path 缺省）。has_git 行加 ⎇ 徽标；denied 置灰不可进；truncated → 底部「已截断」提示。
// 允许选任意目录（裁决 #9）：选中非 git 目录 → 黄条提示不阻止；确认回填 repo_path（父级文本框保持可手改）。
// daemon 离线 → 弹窗内联提示，手输兜底（父级文本框）始终可用。不做：文件显示 / 搜索 / 新建文件夹 / 多选。
import { useState } from 'react';
import { ChevronRight, FolderGit2, Folder, GitBranch, HardDrive, Lock } from 'lucide-react';

import type { FsTreeEntry } from '@coagentia/contracts-ts';

import { ApiError } from '../api';
import { useBrowseFs } from '../data/queries';
import './folder-picker.css';

/** daemon 离线 / 目录读取失败的内联文案（手输兜底始终可用）。 */
function browseErrorCopy(error: unknown): string {
  if (error instanceof ApiError && (error.status === 503 || error.code === 'DAEMON_OFFLINE')) {
    return 'daemon 离线，暂时无法浏览目录。可关闭后在文本框手动输入路径。';
  }
  return error instanceof Error ? error.message : '目录读取失败。可关闭后手动输入路径。';
}

export function FolderPickerModal({ computerId, onPick, onClose }: {
  computerId: string;
  /** 确认选中某目录：回填绝对路径（父级文本框保持可手改）。 */
  onPick: (path: string) => void;
  onClose: () => void;
}) {
  // 导航栈：空 = 根视图（盘符列表）；栈顶 = 当前所在目录（确认时的选中目标）。
  const [stack, setStack] = useState<FsTreeEntry[]>([]);
  const current = stack[stack.length - 1];
  const fsQ = useBrowseFs(computerId, current?.path);
  const entries = fsQ.data?.entries ?? [];

  const enter = (entry: FsTreeEntry) => {
    if (entry.denied) return;
    setStack((s) => [...s, entry]);
  };
  // 面包屑跳转：index<0 → 回根视图；否则截到该层（含）。
  const jumpTo = (index: number) => setStack((s) => s.slice(0, index + 1));

  const confirm = () => {
    if (!current) return;
    onPick(current.path);
    onClose();
  };

  return (
    <div className="scrim" onClick={onClose}>
      <div
        className="modal folder-picker"
        role="dialog"
        aria-label="选择文件夹"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mtitle">选择文件夹</div>

        <nav className="fp-crumbs" aria-label="路径面包屑">
          <button type="button" className="fp-crumb" onClick={() => setStack([])}>
            <HardDrive />此电脑
          </button>
          {stack.map((e, i) => (
            <span className="fp-crumb-seg" key={e.path}>
              <ChevronRight />
              <button type="button" className="fp-crumb" onClick={() => jumpTo(i)}>{e.name}</button>
            </span>
          ))}
        </nav>

        <div className="fp-list" role="listbox" aria-label="目录列表">
          {fsQ.isLoading && <div className="fp-state">读取目录…</div>}
          {fsQ.error && <div className="fp-state error" role="alert">{browseErrorCopy(fsQ.error)}</div>}
          {!fsQ.isLoading && !fsQ.error && entries.length === 0 && (
            <div className="fp-state">该目录下没有子文件夹</div>
          )}
          {!fsQ.error && entries.map((entry) => (
            <button
              type="button"
              key={entry.path}
              className={`fp-row${entry.denied ? ' denied' : ''}`}
              disabled={entry.denied}
              aria-label={entry.name}
              title={entry.denied ? '无权限进入' : entry.path}
              onClick={() => enter(entry)}
            >
              {current ? <Folder className="fp-ic" /> : <HardDrive className="fp-ic" />}
              <span className="fp-name">{entry.name}</span>
              {entry.has_git && (
                <span className="fp-git" title="Git 仓库"><GitBranch /></span>
              )}
              {entry.denied
                ? <span className="fp-denied-ic" aria-hidden><Lock /></span>
                : <ChevronRight className="fp-chev" />}
            </button>
          ))}
          {fsQ.data?.truncated && (
            <div className="fp-truncated">已截断（仅显示前若干项），可手动输入更深路径。</div>
          )}
        </div>

        {current && !current.has_git && (
          <div className="fp-warn" role="status">
            <FolderGit2 />该目录不是 Git 仓库，仍可选择（可稍后初始化）。
          </div>
        )}

        <div className="fp-selected">
          <span className="fp-selected-lb">已选</span>
          <span className="fp-selected-path mono">{current?.path ?? '（请进入一个目录）'}</span>
        </div>

        <div className="ops">
          <button type="button" className="btn btn-ghost" onClick={onClose}>取消</button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={!current}
            title={current ? '选择此文件夹' : '请先进入一个目录'}
            onClick={confirm}
          >
            选择此文件夹
          </button>
        </div>
      </div>
    </div>
  );
}
