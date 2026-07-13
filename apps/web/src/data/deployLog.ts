// M7b 部署日志累积模型（wsBridge 实时追加 + queries 历史翻页共用的纯逻辑，纪律：单一事实源）。
// 日志是 append-only：GET /deployments/{id}/log?after=<行号> 前向翻页（从游标后取行、next_after 是
// 下一页游标），订阅制 deployment.log 推实时新行。二者都往 lines 尾部追加。DeploymentCard 打开时按
// qk.deploymentLog(id) 播种，卸载时缓存随 gc 释放。
export interface DeployLogState {
  lines: string[];
  /** 历史翻页游标（GET 的 next_after）：非 null = 还有更早/更多历史可翻；null = 已抵文件末尾。 */
  nextAfter: number | null;
  /** 文件超 5MB 上限截断（GET 的 truncated）：置真时提示日志已截断。 */
  truncated: boolean;
}

export const EMPTY_DEPLOY_LOG: DeployLogState = { lines: [], nextAfter: null, truncated: false };

/** 实时新行追加（订阅制 deployment.log）：只并入行，不动翻页游标 / 截断标记。 */
export function appendDeployLogLines(
  prev: DeployLogState | undefined,
  lines: string[],
): DeployLogState {
  const base = prev ?? EMPTY_DEPLOY_LOG;
  if (lines.length === 0) return base;
  return { ...base, lines: [...base.lines, ...lines] };
}

/** 历史翻页并入（GET 一页）：追加该页行、推进游标、并入截断标记（任一页截断即置真）。 */
export function mergeDeployLogPage(
  prev: DeployLogState | undefined,
  page: { lines?: string[] | null; next_after?: number | null; truncated?: boolean | null },
): DeployLogState {
  const base = prev ?? EMPTY_DEPLOY_LOG;
  return {
    lines: [...base.lines, ...(page.lines ?? [])],
    nextAfter: page.next_after ?? null,
    truncated: base.truncated || !!page.truncated,
  };
}
