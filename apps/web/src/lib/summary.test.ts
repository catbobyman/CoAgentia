// O8 摘要/护栏消息体解析 + 横幅态派生（M8b B-M8-2）。字符串按后端逐字节镜像（orchestration/
// summary.py render_summary_message / post_coordination_block、routes/tasks.py force-start 锚点）——
// 后端话术漂移则此测试红，提示同步更新 lib/summary.ts 的字面量契约。
import { describe, expect, it } from 'vitest';

import {
  classifySummaryLines,
  deriveO8Banner,
  isBlockMessage,
  isForceStartAnchor,
  isQualitySignal,
  isSummaryMessage,
  parseBlock,
  parseSummaryHeader,
  summaryCoverCounts,
} from './summary';

// 后端 render_summary_message 逐字节形状（第 2 轮 / 上限 8，1/2 覆盖，1 未覆盖）。
const SUMMARY_BODY = [
  '**汇总输入摘要**（第 2 轮 / 上限 8）',
  '覆盖：1/2 个上游节点已 Done；未覆盖：#5「登录页」（closed）',
  '- #3 实现后端 · Rin · done',
  '  deliverables: api/auth.py，api/models.py',
  '  evidence[test]: 28 passed',
  '- #5 登录页 · Kai · closed',
  '  risk: 设计未定',
  '提示：总报告须逐条照抄上方「未覆盖」清单（W9 诚实性）。',
].join('\n');

const BLOCK_ROUNDS =
  '⚠️ 汇总协调已阻断：轮数触顶（8/8）。已停止自动唤醒——请在本线程发言或对汇总节点 force-start 以恢复（恢复归零轮/stall 计数，replan 预算不重置）。';
const BLOCK_STALL =
  '⚠️ 汇总协调已阻断：空转触顶（stall 3/3）。已停止自动唤醒——请在本线程发言或对汇总节点 force-start 以恢复（恢复归零轮/stall 计数，replan 预算不重置）。';
const FORCE_ANCHOR = 'Owner 强制启动了此任务（override 依赖 gating，已留痕）';
const QUALITY =
  '质量信号：你的拆解提案（rev.2）落地时经人类调整——人类在确认时做了 3 处调整。请先复述你对该调整的理解，再把可复用的教训沉淀进 MEMORY.md（同类调整出现两次即视为拆解习惯问题）。';

describe('摘要消息解析', () => {
  it('parseSummaryHeader 提取轮数/上限', () => {
    expect(parseSummaryHeader(SUMMARY_BODY)).toEqual({ round: 2, maxRounds: 8 });
    expect(isSummaryMessage(SUMMARY_BODY)).toBe(true);
    expect(isSummaryMessage('普通系统消息')).toBe(false);
    expect(parseSummaryHeader('普通系统消息')).toBeNull();
  });

  it('summaryCoverCounts 提取覆盖计数（未覆盖 = total - covered）', () => {
    expect(summaryCoverCounts(SUMMARY_BODY)).toEqual({ covered: 1, total: 2 });
  });

  it('classifySummaryLines 逐行分类（header/cover/node/detail/hint）', () => {
    const kinds = classifySummaryLines(SUMMARY_BODY).map((l) => l.kind);
    expect(kinds).toEqual([
      'header', 'cover', 'node', 'detail', 'detail', 'node', 'detail', 'hint',
    ]);
  });

  it('classifySummaryLines 识别截断尾', () => {
    const trunc = classifySummaryLines('**汇总输入摘要**（第 1 轮 / 上限 8）\n…（truncated）');
    expect(trunc[1]!.kind).toBe('truncated');
  });
});

describe('阻断消息解析', () => {
  it('轮数触顶 → reasonKind rounds + 计数', () => {
    expect(isBlockMessage(BLOCK_ROUNDS)).toBe(true);
    expect(parseBlock(BLOCK_ROUNDS)).toEqual({
      reasonKind: 'rounds',
      reasonText: '轮数触顶（8/8）',
      round: 8,
      maxRounds: 8,
    });
  });

  it('空转触顶 → reasonKind stall + 计数', () => {
    expect(parseBlock(BLOCK_STALL)).toEqual({
      reasonKind: 'stall',
      reasonText: '空转触顶（stall 3/3）',
      stall: 3,
      maxStall: 3,
    });
  });

  it('非阻断消息 → null', () => {
    expect(isBlockMessage(SUMMARY_BODY)).toBe(false);
    expect(parseBlock(SUMMARY_BODY)).toBeNull();
  });
});

describe('恢复/质量信号识别', () => {
  it('force-start 锚点', () => {
    expect(isForceStartAnchor(FORCE_ANCHOR)).toBe(true);
    expect(isForceStartAnchor('别的系统消息')).toBe(false);
  });
  it('质量信号前缀', () => {
    expect(isQualitySignal(QUALITY)).toBe(true);
    expect(isQualitySignal(SUMMARY_BODY)).toBe(false);
  });
});

describe('deriveO8Banner 横幅态', () => {
  const human = (id: string) => id === 'human_owner';
  const sys = (body: string) => ({ kind: 'system', body, author_member_id: null });
  const userMsg = (author: string) => ({ kind: 'user', body: '我来看看', author_member_id: author });

  it('非汇总线程（无 O8 消息）→ null', () => {
    expect(deriveO8Banner([userMsg('human_owner'), sys('普通系统消息')], human)).toBeNull();
  });

  it('最新是摘要 → active（轮数 + 未覆盖数）', () => {
    const b = deriveO8Banner([sys(SUMMARY_BODY)], human);
    expect(b).toEqual({ kind: 'active', round: 2, maxRounds: 8, uncovered: 1 });
  });

  it('摘要后阻断 → blocked（原因优先）', () => {
    const b = deriveO8Banner([sys(SUMMARY_BODY), sys(BLOCK_ROUNDS)], human);
    expect(b?.kind).toBe('blocked');
    expect(b).toMatchObject({ reasonKind: 'rounds', round: 8, maxRounds: 8 });
  });

  it('阻断后人类发言 → 恢复清横幅（null）', () => {
    const b = deriveO8Banner(
      [sys(SUMMARY_BODY), sys(BLOCK_STALL), userMsg('human_owner')],
      human,
    );
    expect(b).toBeNull();
  });

  it('阻断后 force-start 锚点 → 恢复清横幅（null）', () => {
    const b = deriveO8Banner([sys(SUMMARY_BODY), sys(BLOCK_ROUNDS), sys(FORCE_ANCHOR)], human);
    expect(b).toBeNull();
  });

  it('阻断后 Agent（非人类）发言 → 仍 blocked（只有人类介入恢复）', () => {
    const b = deriveO8Banner(
      [sys(SUMMARY_BODY), sys(BLOCK_ROUNDS), userMsg('agent_orch')],
      human,
    );
    expect(b?.kind).toBe('blocked');
  });

  it('恢复后新一轮摘要 → 重新点亮 active', () => {
    const next = '**汇总输入摘要**（第 1 轮 / 上限 8）\n覆盖：2/2 个上游节点已 Done';
    const b = deriveO8Banner(
      [sys(SUMMARY_BODY), sys(BLOCK_ROUNDS), userMsg('human_owner'), sys(next)],
      human,
    );
    expect(b).toEqual({ kind: 'active', round: 1, maxRounds: 8, uncovered: 0 });
  });
});
