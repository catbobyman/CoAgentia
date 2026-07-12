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
});
