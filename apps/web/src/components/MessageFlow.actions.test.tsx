// F5 逐条消息 hover 动作条：Copy text/link 恒在；Reply 由回调决定；Convert to task 仅顶级频道消息
// （非任务、非线程回复、canConvertToTask）显示。
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { MemberPublic, MessagePublic, TaskPublic } from '@coagentia/contracts-ts';

import { MessageFlow } from './MessageFlow';

const AUTHOR: MemberPublic = {
  id: 'mem_h', workspace_id: 'ws_1', kind: 'human', role: 'owner', name: 'Owner',
  created_at: '2026-07-12T00:00:00Z',
};
const baseMsg: MessagePublic = {
  id: 'msg_1', workspace_id: 'ws_1', channel_id: 'ch_1', kind: 'user',
  author_member_id: 'mem_h', body: '一条普通消息', created_at: '2026-07-12T00:00:00Z',
};
const TASK = { id: 'task_1', root_message_id: 'msg_1' } as unknown as TaskPublic;

function renderFlow(props: Partial<React.ComponentProps<typeof MessageFlow>>) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MessageFlow
        messages={[baseMsg]}
        memberById={{ mem_h: AUTHOR }}
        memberNames={['Owner']} meName="Owner"
        presenceOf={() => undefined} taskByRoot={{}} usageByTask={{}}
        {...props}
      />
    </QueryClientProvider>,
  );
}

describe('F5 MessageActions', () => {
  it('Copy text/link 恒在', () => {
    renderFlow({});
    expect(screen.getByLabelText('复制文本')).toBeInTheDocument();
    expect(screen.getByLabelText('复制链接')).toBeInTheDocument();
  });

  it('顶级频道消息（canConvert）显示「转为任务」并回调', () => {
    const onConvertToTask = vi.fn();
    renderFlow({ onConvertToTask, canConvertToTask: true });
    const btn = screen.getByLabelText('转为任务');
    fireEvent.click(btn);
    expect(onConvertToTask).toHaveBeenCalledWith(baseMsg);
  });

  it('已是任务的消息 → 不显示「转为任务」', () => {
    renderFlow({ onConvertToTask: vi.fn(), canConvertToTask: true, taskByRoot: { msg_1: TASK } });
    expect(screen.queryByLabelText('转为任务')).toBeNull();
  });

  it('线程回复（thread_root_id 非空）→ 不显示「转为任务」', () => {
    const reply = { ...baseMsg, id: 'msg_r', thread_root_id: 'msg_1' };
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MessageFlow
          messages={[reply]} memberById={{ mem_h: AUTHOR }} memberNames={['Owner']} meName="Owner"
          presenceOf={() => undefined} taskByRoot={{}} usageByTask={{}}
          onConvertToTask={vi.fn()} canConvertToTask
        />
      </QueryClientProvider>,
    );
    expect(screen.queryByLabelText('转为任务')).toBeNull();
  });

  it('DM（canConvertToTask=false）→ 不显示「转为任务」', () => {
    renderFlow({ onConvertToTask: vi.fn(), canConvertToTask: false });
    expect(screen.queryByLabelText('转为任务')).toBeNull();
  });

  it('承载卡片的消息（card_kind）→ 不显示「转为任务」（防卡片与任务牌叠加）', () => {
    const carded = { ...baseMsg, card_kind: 'deployment' as const };
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MessageFlow
          messages={[carded]} memberById={{ mem_h: AUTHOR }} memberNames={['Owner']} meName="Owner"
          presenceOf={() => undefined} taskByRoot={{}} usageByTask={{}}
          onConvertToTask={vi.fn()} canConvertToTask
        />
      </QueryClientProvider>,
    );
    expect(screen.queryByLabelText('转为任务')).toBeNull();
  });

  it('提供 onReplyInThread → 显示「在线程中回复」并回调 root', () => {
    const onReplyInThread = vi.fn();
    renderFlow({ onReplyInThread });
    fireEvent.click(screen.getByLabelText('在线程中回复'));
    expect(onReplyInThread).toHaveBeenCalledWith(baseMsg);
  });
});
