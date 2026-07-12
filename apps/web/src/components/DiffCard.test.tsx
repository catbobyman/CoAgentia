import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { DiffPayload } from '@coagentia/contracts-ts';

import { DiffPayloadView, diffErrorCopy } from './DiffCard';
import { ApiError } from '../api';

const DIFF: DiffPayload = {
  base_ref: 'main', head_ref: 'coagentia/task-01', total_additions: 12, total_deletions: 3,
  files_truncated: true,
  files: [
    {
      path: 'src/new.ts', old_path: 'src/old.ts', status: 'renamed', additions: 8, deletions: 2,
      patch_truncated: true,
      patch: '@@ -1,2 +1,3 @@\n-old\n+new\n context',
    },
    {
      path: 'public/logo.png', old_path: null, status: 'modified', additions: 0, deletions: 0,
      patch_truncated: false, patch: '',
    },
  ],
};

describe('DiffPayloadView', () => {
  it('渲染总统计、rename、逐文件折叠、patch 行色语义与两级截断', () => {
    render(<DiffPayloadView payload={DIFF} />);
    expect(screen.getByText('+12')).toBeInTheDocument();
    expect(screen.getByText('-3')).toBeInTheDocument();
    expect(screen.getByText(/仅显示前 200 个文件/)).toBeInTheDocument();
    expect(screen.getByText(/src\/old\.ts → src\/new\.ts/)).toBeInTheDocument();

    const renamed = screen.getByRole('button', { name: /R src\/old\.ts → src\/new\.ts \+8 -2/ });
    fireEvent.click(renamed);
    expect(screen.getByText('+new')).toHaveClass('add');
    expect(screen.getByText('-old')).toHaveClass('del');
    expect(screen.getByText(/当前文件 patch 已截断/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /public\/logo\.png/ }));
    expect(screen.getByText('二进制文件或无文本差异')).toBeInTheDocument();
  });

  it('404 与 503 给出不同可恢复文案', () => {
    expect(diffErrorCopy(new ApiError(404, 'NOT_FOUND', 'missing'))).toMatch(/尚无 worktree/);
    expect(diffErrorCopy(new ApiError(503, 'DAEMON_OFFLINE', 'offline'))).toMatch(/daemon 离线/);
  });
});
