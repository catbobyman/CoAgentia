import { describe, expect, it } from 'vitest';

import { webSocketUrl } from './ws';

describe('webSocketUrl', () => {
  it('uses the current origin for same-origin deployments', () => {
    expect(webSocketUrl('', 'http://127.0.0.1:8787')).toBe('ws://127.0.0.1:8787/api/ws');
  });

  it('upgrades an explicit HTTPS API base to WSS', () => {
    expect(webSocketUrl('https://coagentia.example', 'http://localhost')).toBe(
      'wss://coagentia.example/api/ws',
    );
  });
});
