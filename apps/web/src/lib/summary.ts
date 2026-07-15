// O8 汇总执行域前端消费面（M8b B-M8-2，Orchestrator汇总设计 §4/§6/§8）：解析后端护栏系统消息体，
// 派生「汇总任务线程横幅」态与结构化摘要卡片输入。**零新端点**——横幅/卡片全部从系统消息 body 派生
// （护栏可见哲学，人机同源；判定归 server，前端只消费不复算——M8-HANDOFF §6 #7）。
//
// 字符串契约的唯一镜像点在此文件（改后端话术须同改此处 + summary.test.ts 逐字节断言）。后端权威：
//   摘要消息   = orchestration/summary.py render_summary_message（§4.1）
//   阻断消息   = orchestration/summary.py post_coordination_block（§6.3）
//   恢复锚点   = routes/tasks.py force_start_task（force-start 锚点系统消息）
//   质量信号   = orchestration/quality.py adjustment_signal_body（§8.1）

// ---- 字面量契约（与后端逐字节对齐；勿改一字，改则同改 summary.py/tasks.py 与本文件测试）。
const SUMMARY_HEADER = /^\*\*汇总输入摘要\*\*（第\s*(\d+)\s*轮\s*\/\s*上限\s*(\d+)）/;
const COVER_LINE = /^覆盖：(\d+)\/(\d+)\s*个上游节点已 Done/;
const BLOCK_PREFIX = '⚠️ 汇总协调已阻断：';
const ROUNDS_REASON = /轮数触顶（(\d+)\/(\d+)）/;
const STALL_REASON = /空转触顶（stall\s*(\d+)\/(\d+)）/;
const FORCE_START_ANCHOR = '强制启动了此任务';
const QUALITY_PREFIX = '质量信号：';

// ---- 摘要消息（§4.1） -------------------------------------------------------

export interface SummaryHeader {
  round: number;
  maxRounds: number;
}

/** 首行匹配「**汇总输入摘要**（第 N 轮 / 上限 M）」→ {round, maxRounds}；非摘要消息 → null。 */
export function parseSummaryHeader(body: string): SummaryHeader | null {
  const m = SUMMARY_HEADER.exec(body);
  if (!m) return null;
  return { round: Number(m[1]), maxRounds: Number(m[2]) };
}

/** 该消息体是否为汇总输入摘要（首行头匹配）。 */
export function isSummaryMessage(body: string): boolean {
  return SUMMARY_HEADER.test(body);
}

/** 从「覆盖：C/T 个上游节点已 Done」提取覆盖计数（未覆盖数 = total - covered）；无该行 → null。 */
export function summaryCoverCounts(body: string): { covered: number; total: number } | null {
  for (const line of body.split(/\r?\n/)) {
    const m = COVER_LINE.exec(line);
    if (m) return { covered: Number(m[1]), total: Number(m[2]) };
  }
  return null;
}

// 摘要卡片按行类型（SummaryCard 渲染消费；不深解每节点字段——标题含 ' · ' 时拆分不可靠，故按前缀
// 分类保结构而不臆测字段边界）。
export type SummaryLineKind =
  | 'header' // **汇总输入摘要**（第 N 轮 / 上限 M）
  | 'cover' // 覆盖：C/T …；未覆盖：…
  | 'node' // - #n 标题 · owner · status
  | 'detail' // 2 空格缩进的 deliverables/evidence/risk
  | 'hint' // 提示：…
  | 'truncated' // …（truncated）
  | 'text'; // 其它（兜底原样）

export interface SummaryLine {
  kind: SummaryLineKind;
  text: string; // 去缩进后的展示文本
}

/** 摘要体逐行分类（供卡片结构化渲染）。行序保原样。 */
export function classifySummaryLines(body: string): SummaryLine[] {
  return body.split(/\r?\n/).map((raw): SummaryLine => {
    if (SUMMARY_HEADER.test(raw)) return { kind: 'header', text: raw };
    if (COVER_LINE.test(raw)) return { kind: 'cover', text: raw };
    if (/^-\s+#/.test(raw)) return { kind: 'node', text: raw.replace(/^-\s+/, '') };
    if (/^\s{2}(deliverables:|evidence\[|risk:)/.test(raw)) return { kind: 'detail', text: raw.trim() };
    if (/^提示：/.test(raw)) return { kind: 'hint', text: raw };
    if (/…（truncated）/.test(raw)) return { kind: 'truncated', text: raw };
    return { kind: 'text', text: raw };
  });
}

// ---- 阻断消息（§6.3） -------------------------------------------------------

export interface ParsedBlock {
  reasonKind: 'rounds' | 'stall';
  reasonText: string; // 「轮数触顶（8/8）」/「空转触顶（stall 3/3）」原文
  round?: number;
  maxRounds?: number;
  stall?: number;
  maxStall?: number;
}

/** 该消息体是否为协调阻断消息。 */
export function isBlockMessage(body: string): boolean {
  return body.startsWith(BLOCK_PREFIX);
}

/** 解析协调阻断消息 → 原因档 + 计数事实；非阻断消息 → null。 */
export function parseBlock(body: string): ParsedBlock | null {
  if (!isBlockMessage(body)) return null;
  const rounds = ROUNDS_REASON.exec(body);
  if (rounds) {
    return {
      reasonKind: 'rounds',
      reasonText: rounds[0],
      round: Number(rounds[1]),
      maxRounds: Number(rounds[2]),
    };
  }
  const stall = STALL_REASON.exec(body);
  if (stall) {
    return {
      reasonKind: 'stall',
      reasonText: stall[0],
      stall: Number(stall[1]),
      maxStall: Number(stall[2]),
    };
  }
  // 前缀匹配但原因串未识别（后端话术漂移）：仍标记阻断，原因取前缀后至句号。
  const tail = body.slice(BLOCK_PREFIX.length).split('。')[0] ?? '';
  return { reasonKind: 'stall', reasonText: tail };
}

// ---- 恢复信号 / 质量信号 ----------------------------------------------------

/** force-start 锚点系统消息（人类越过 gating，同事务触发 summary.recover）。 */
export function isForceStartAnchor(body: string): boolean {
  return body.includes(FORCE_START_ANCHOR);
}

/** 质量回路信号（带调整落地 → @proposer 线程留痕；§8.1）。 */
export function isQualitySignal(body: string): boolean {
  return body.startsWith(QUALITY_PREFIX);
}

// ---- 横幅态派生（①） --------------------------------------------------------

export type O8Banner =
  | { kind: 'active'; round: number; maxRounds: number; uncovered: number }
  | ParsedBlockBanner;

interface ParsedBlockBanner extends ParsedBlock {
  kind: 'blocked';
}

interface BannerMessage {
  kind?: string; // 'user' | 'system'（消费 MessagePublic，kind 生成为可选）
  body: string;
  author_member_id?: string | null;
}

/** 恢复信号：人类在线程发言 或 force-start 锚点（§6.3 两条恢复路径均可从消息体判定）。 */
function isRecoverySignal(m: BannerMessage, isHuman: (memberId: string) => boolean): boolean {
  if (m.kind === 'user' && m.author_member_id && isHuman(m.author_member_id)) return true;
  return m.kind === 'system' && isForceStartAnchor(m.body);
}

/**
 * 从汇总任务线程消息序列派生当前 O8 横幅态（① 汇总任务线程横幅）。纯消息体派生、零新端点：
 * 顺序扫描，摘要消息 → active（轮数/未覆盖）；阻断消息 → blocked（原因+计数）；阻断态下遇人类恢复信号
 * （发言/force-start）→ 清横幅（后端 recover 已归零；下一轮摘要会重新点亮 active）。非汇总线程（无任何
 * O8 消息）→ null（横幅隐去）。
 */
export function deriveO8Banner(
  messages: BannerMessage[],
  isHuman: (memberId: string) => boolean,
): O8Banner | null {
  let state: O8Banner | null = null;
  for (const m of messages) {
    if (m.kind === 'system' && isSummaryMessage(m.body)) {
      const h = parseSummaryHeader(m.body)!;
      const c = summaryCoverCounts(m.body);
      state = {
        kind: 'active',
        round: h.round,
        maxRounds: h.maxRounds,
        uncovered: c ? c.total - c.covered : 0,
      };
    } else if (m.kind === 'system' && isBlockMessage(m.body)) {
      const b = parseBlock(m.body);
      if (b) state = { kind: 'blocked', ...b };
    } else if (state?.kind === 'blocked' && isRecoverySignal(m, isHuman)) {
      state = null; // 恢复：人类介入清阻断——等下一轮摘要重新点亮
    }
  }
  return state;
}
