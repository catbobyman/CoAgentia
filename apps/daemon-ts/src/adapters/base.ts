/**
 * RuntimeAdapter 接口（契约 E §9）与 AdapterSink 回调口。
 *
 * E §9 的 RuntimeAdapter 是**每进程**运行时驱动的最小接口——daemon 主体不感知具体 runtime。
 * `claude_code.ts` 的 ClaudeCodeProcess 是其 claude 实现；`codex.ts`（M5）加实现不改形状。
 *
 * 四类出口回调（status / activity / usage / diagnostic）形状 = 契约 D §7 上报帧，
 * 复用 A6 已定义的 `AdapterSink`（../adapter.ts）——两处不重复定义（铁律：单点）。
 *
 * 对等基准 = apps/daemon adapters/base.py；与 ../adapter.ts 的 RuntimeAdapter 同名不消歧
 * （挂账 TS-③，裁决 #12 原样直译）——TS import 带路径，文件隔离天然不冲突。
 */

import type { AgentBoot } from '@coagentia/contracts-ts';

// AdapterSink 已在 A6 定义（status/activity/usage/diagnostic 四回调）；此处再导出，
// 让 adapters 子目录用户无需跨模块 import。
export type { AdapterSink } from '../adapter.ts';

/**
 * 契约 E §9：每 Agent 一个 runtime 子进程的统一驱动接口。
 *
 * - start(boot, resume)：spawn 子进程（resume=true → 附会话续接参数，见 resetSessionArgs）
 * - stop()：优雅关 stdin → 等待 → terminate/kill
 * - feed(text)：写入一个 turn 的 stdin 输入（§6 已编码的 user 帧文本）
 * - resetSessionArgs()：三档重置的**会话层**命令行差异（restart 保会话=[--resume,id]；
 *   reset_session/reset_full 新会话=[]）——控制归代码，判断归上层管理器。
 */
export interface RuntimeAdapter {
  start(boot: AgentBoot, resume: boolean): Promise<void>;

  stop(): Promise<void>;

  feed(text: string): Promise<void>;

  resetSessionArgs(): string[];
}
