// 深链还原核心闸:validateChannelSearch 把任意 URL search 输入还原为类型化视图状态。
// 对应完成判据「深链还原有测试」。运行:pnpm -F @coagentia/web test
import { describe, expect, it } from 'vitest';

import { validateChannelSearch } from './search';

describe('validateChannelSearch (深链 ?tab=&thread=&task=&node= 还原)', () => {
  it('空输入 → 默认 chat 屏,其它字段缺省', () => {
    expect(validateChannelSearch({})).toEqual({
      tab: 'chat', thread: undefined, task: undefined, node: undefined,
    });
  });

  it('还原四要素:tab/thread/task/node', () => {
    const restored = validateChannelSearch({
      tab: 'canvas', thread: 'msg_1', task: 'task_7', node: 'node_a',
    });
    expect(restored).toEqual({
      tab: 'canvas', thread: 'msg_1', task: 'task_7', node: 'node_a',
    });
  });

  it('非法 tab 归一为 chat', () => {
    expect(validateChannelSearch({ tab: 'bogus' }).tab).toBe('chat');
  });

  it('四个合法 tab 全部保真', () => {
    for (const tab of ['chat', 'canvas', 'board', 'files']) {
      expect(validateChannelSearch({ tab }).tab).toBe(tab);
    }
  });

  it('空串字段被丢弃(不是空串而是 undefined)', () => {
    expect(validateChannelSearch({ tab: 'board', task: '' }).task).toBeUndefined();
  });
});
