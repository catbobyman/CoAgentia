// M7b WS 上行订阅：sub/unsub 下发 + 断线（sender=null）只登记 + 重连 resend 全部活跃订阅。
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  activeSubscriptionKeys,
  resendSubscriptions,
  setWsSender,
  subscribeDeployLog,
  unsubscribeDeployLog,
} from './wsUplink';

afterEach(() => {
  // 清理跨用例活跃订阅（模块单例）。
  for (const key of activeSubscriptionKeys()) {
    unsubscribeDeployLog(key.replace('deploy_log:', ''));
  }
  setWsSender(null);
});

describe('wsUplink deploy_log 订阅', () => {
  it('连接可用时 sub 立即下发 SubDeployLogMsg 形状帧', () => {
    const sent: unknown[] = [];
    setWsSender((m) => sent.push(m));
    subscribeDeployLog('deploy_1');
    expect(sent).toEqual([{ type: 'sub', stream: 'deploy_log', deployment_id: 'deploy_1' }]);
    expect(activeSubscriptionKeys()).toContain('deploy_log:deploy_1');
  });

  it('unsub 下发 unsub 帧并从活跃集移除（不再重连重发）', () => {
    const sent: unknown[] = [];
    setWsSender((m) => sent.push(m));
    subscribeDeployLog('deploy_1');
    unsubscribeDeployLog('deploy_1');
    expect(sent[sent.length - 1]).toEqual({ type: 'unsub', stream: 'deploy_log', deployment_id: 'deploy_1' });
    expect(activeSubscriptionKeys()).not.toContain('deploy_log:deploy_1');
  });

  it('断线（sender=null）时 sub 只登记不下发，重连 resend 全部活跃订阅', () => {
    setWsSender(null);
    subscribeDeployLog('deploy_1');
    subscribeDeployLog('deploy_2');
    expect(activeSubscriptionKeys().sort()).toEqual(['deploy_log:deploy_1', 'deploy_log:deploy_2']);

    // 重连：注入新 sender + resend。
    const sender = vi.fn();
    setWsSender(sender);
    resendSubscriptions();
    expect(sender).toHaveBeenCalledTimes(2);
    expect(sender).toHaveBeenCalledWith({ type: 'sub', stream: 'deploy_log', deployment_id: 'deploy_1' });
    expect(sender).toHaveBeenCalledWith({ type: 'sub', stream: 'deploy_log', deployment_id: 'deploy_2' });
  });
});
