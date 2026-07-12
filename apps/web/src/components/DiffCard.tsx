import { useState } from 'react';
import { ChevronDown, ChevronRight, FileCode2, GitCompareArrows, RefreshCw } from 'lucide-react';

import type { DiffFile, DiffPayload } from '@coagentia/contracts-ts';

import { ApiError } from '../api';
import { useTaskDiff } from '../data/queries';
import './diff-card.css';

const STATUS_LABEL: Record<DiffFile['status'], string> = {
  added: 'A', modified: 'M', deleted: 'D', renamed: 'R',
};

export function diffErrorCopy(error: unknown): string {
  if (error instanceof ApiError && error.status === 404) return '尚无 worktree，任务激活后再查看 Diff。';
  if (error instanceof ApiError && (error.status === 503 || error.code === 'DAEMON_OFFLINE')) {
    return 'daemon 离线，暂时无法读取 Diff。';
  }
  return error instanceof Error ? error.message : 'Diff 加载失败。';
}

export function DiffCard({ taskId }: { taskId: string }) {
  const [open, setOpen] = useState(false);
  const diffQ = useTaskDiff(taskId, open);
  return (
    <div className="diff-card">
      <button type="button" className="diff-toggle" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <GitCompareArrows />
        <span>Diff</span>
        {open ? <ChevronDown /> : <ChevronRight />}
      </button>
      {open && (
        <div className="diff-body">
          {diffQ.isLoading && <div className="diff-state">读取差异…</div>}
          {diffQ.error && (
            <div className="diff-state error">
              <span>{diffErrorCopy(diffQ.error)}</span>
              <button type="button" className="project-icon" aria-label="重试 Diff" onClick={() => void diffQ.refetch()}><RefreshCw /></button>
            </div>
          )}
          {diffQ.data && <DiffPayloadView payload={diffQ.data} />}
        </div>
      )}
    </div>
  );
}

export function DiffPayloadView({ payload }: { payload: DiffPayload }) {
  return (
    <div className="diff-payload">
      <div className="diff-summary">
        <span className="refs">{payload.base_ref} → {payload.head_ref}</span>
        <span className="plus">+{payload.total_additions}</span>
        <span className="minus">-{payload.total_deletions}</span>
      </div>
      {payload.files_truncated && (
        <div className="diff-truncated">仅显示前 200 个文件，总统计仍覆盖全部差异。</div>
      )}
      {payload.files.length === 0 ? (
        <div className="diff-state">当前分支没有差异。</div>
      ) : payload.files.map((file, index) => (
        <DiffFileRow file={file} key={`${file.path}:${index}`} />
      ))}
    </div>
  );
}

function DiffFileRow({ file }: { file: DiffFile }) {
  const [open, setOpen] = useState(false);
  const pathLabel = file.status === 'renamed' && file.old_path
    ? `${file.old_path} → ${file.path}`
    : file.path;
  return (
    <div className="diff-file">
      <button
        type="button" className="diff-file-head" aria-expanded={open}
        aria-label={`${STATUS_LABEL[file.status]} ${pathLabel} +${file.additions} -${file.deletions} 差异`}
        onClick={() => setOpen((v) => !v)}
      >
        {open ? <ChevronDown /> : <ChevronRight />}
        <span className={`file-status s-${file.status}`}>{STATUS_LABEL[file.status]}</span>
        <FileCode2 />
        <span className="path">{pathLabel}</span>
        <span className="plus">+{file.additions}</span>
        <span className="minus">-{file.deletions}</span>
      </button>
      {open && (
        <div className="diff-patch">
          {file.patch === '' ? (
            <div className="diff-state">二进制文件或无文本差异</div>
          ) : (
            <pre>{file.patch.split('\n').map((line, i) => (
              <span className={patchLineClass(line)} key={`${i}:${line.slice(0, 24)}`}>{line || ' '}</span>
            ))}</pre>
          )}
          {file.patch_truncated && <div className="diff-truncated">当前文件 patch 已截断。</div>}
        </div>
      )}
    </div>
  );
}

function patchLineClass(line: string): string {
  if (line.startsWith('@@')) return 'hunk';
  if (line.startsWith('+') && !line.startsWith('+++')) return 'add';
  if (line.startsWith('-') && !line.startsWith('---')) return 'del';
  if (line.startsWith('diff ') || line.startsWith('index ') || line.startsWith('+++') || line.startsWith('---')) return 'meta';
  return 'ctx';
}
