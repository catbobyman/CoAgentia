import { afterEach, describe, expect, it, vi } from 'vitest';

import { api } from './api';

describe('api.messages', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('follows next_cursor so durable system messages are not hidden past page one', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ items: [{ id: 'message-1' }], next_cursor: 'cursor-1' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ items: [{ id: 'message-2' }], next_cursor: null }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      );
    vi.stubGlobal('fetch', fetchMock);

    const page = await api.messages('channel-1');

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      '/api/channels/channel-1/messages?limit=200',
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/api/channels/channel-1/messages?limit=200&after=cursor-1',
    );
    expect(page.items.map((message) => message.id)).toEqual(['message-1', 'message-2']);
    expect(page.next_cursor).toBeNull();
  });

  // F5-④ 线程回复：sendMessage 携 threadRootId → 请求体含 thread_root_id（归线程不进主流，PRD §4.1）。
  it('sendMessage 携 threadRootId 时请求体含 thread_root_id', async () => {
    // 每次调用返回新 Response（同一实例的 body 只能被 writeJson 的 .text() 消费一次）。
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(() =>
      Promise.resolve(new Response(JSON.stringify({ message: { id: 'm' } }), {
        status: 201, headers: { 'Content-Type': 'application/json' },
      })),
    );
    vi.stubGlobal('fetch', fetchMock);

    await api.sendMessage('ch1', '回复正文', false, 'root-msg-1');
    const [, init] = fetchMock.mock.calls[0]!;
    expect(JSON.parse(init!.body as string)).toEqual({ body: '回复正文', thread_root_id: 'root-msg-1' });

    // 不携 threadRootId（主流发送）→ 无 thread_root_id 键。
    await api.sendMessage('ch1', '主流消息', false);
    const [, init2] = fetchMock.mock.calls[1]!;
    expect(JSON.parse(init2!.body as string)).toEqual({ body: '主流消息' });
  });
});
