// 消息流附件卡数据源(M2 挂账修复):AttachCard 消费消息读面派生 m.files(契约 A v1.0.4),
// 不再依赖 channelFiles 首页 ≤50 的按 message_id 聚合——旧文件附件卡不缺席。
// 运行:pnpm -F @coagentia/web test
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';

import type { FilePublic, MemberPublic, MessagePublic } from '@coagentia/contracts-ts';

import { MessageFlow } from './MessageFlow';

const OWNER: MemberPublic = {
  id: 'mem_owner', kind: 'human', role: 'owner', name: 'Memcyo',
  workspace_id: 'ws_1', created_at: '2026-07-09T00:00:00Z',
};

const FILE: FilePublic = {
  id: 'file_1', workspace_id: 'ws_1', message_id: 'msg_1', channel_id: 'ch_1',
  name: 'spec.md', mime: 'text/markdown', size_bytes: 2048,
  sha256: 'a'.repeat(64), created_at: '2026-07-09T04:00:00Z',
};

function msg(id: string, files: MessagePublic['files']): MessagePublic {
  return {
    id, workspace_id: 'ws_1', channel_id: 'ch_1', author_member_id: OWNER.id,
    kind: 'user', body: `body of ${id}`, created_at: '2026-07-09T04:00:00Z', files,
  };
}

function renderFlow(messages: MessagePublic[]) {
  return render(
    <MessageFlow
      messages={messages}
      memberById={{ [OWNER.id]: OWNER }}
      memberNames={[OWNER.name]}
      meName={OWNER.name}
      presenceOf={() => undefined}
      taskByRoot={{}}
      usageByTask={{}}
    />,
  );
}

describe('MessageFlow 附件卡(消息读面派生 files)', () => {
  it('m.files 非空 → 渲染 AttachCard(文件名+下载)', () => {
    renderFlow([msg('msg_1', [FILE])]);
    expect(screen.getByTitle('预览')).toHaveTextContent('spec.md');
    expect(screen.getByLabelText('下载')).toBeInTheDocument();
  });

  it('files 为 []/null(未附着面)均不渲染附件卡、不崩溃', () => {
    renderFlow([msg('msg_a', []), msg('msg_b', null)]);
    expect(screen.queryByTitle('预览')).toBeNull();
  });
});
