// M7b 部署日志累积模型（wsBridge 实时追加 + queries 历史翻页共用的纯逻辑，纪律：单一事实源）。
// 日志是 append-only：GET /deployments/{id}/log?after=<行号> 前向翻页（从游标后取行、next_after 是
// 下一页游标），订阅制 deployment.log 推实时新块。二者都往 lines 尾部追加。DeploymentCard 打开时按
// qk.deploymentLog(id) 播种，卸载时缓存随 gc 释放。
//
// R-14（M8a L6）：重开进行中部署时历史翻页与实时流交叠——修型 = **先订阅缓冲 → GET 历史 → 按
// chunk_seq 拼接去重**（零契约，ws deployment.log 帧已携 chunk_seq，D §7/C §6.7）。要点：
//   ① live 块按 **chunk_seq 单调去重**（唯一去重键；WS 重连重投同 seq 的块只并一次——不按行文本
//      去重，日志行可重复合法，防返工锚点 6）。
//   ② 历史首页加载**完成前**到达的 live 块进 pending 缓冲（按 seq 去重），首页并入后一次性 flush
//      到尾部，使历史（旧行）先于实时（新块）——消除「订阅先于历史 resolve → 实时块被历史行盖后重复」。
// 残留（bounded，登记观察）：某 chunk 在 GET 快照前已落盘 → 既进历史又在 pending，会重一次。精确
// 消除需 GET /log 暴露其覆盖到的 max chunk_seq（升 B 契约）——裁决 #8「实测若必要才升契约」，暂挂。
export interface DeployLogState {
  lines: string[];
  /** 历史翻页游标（GET 的 next_after）：非 null = 还有更早/更多历史可翻；null = 已抵文件末尾。 */
  nextAfter: number | null;
  /** 文件超 5MB 上限截断（GET 的 truncated）：置真时提示日志已截断。 */
  truncated: boolean;
  /** 已并入 lines 的最高 chunk_seq（live 单调去重键）；无 live 块并入前为 null。 */
  appliedSeq: number | null;
  /** 历史首页是否已并入（false = 尚未，live 块进 pending 缓冲）。 */
  historyLoaded: boolean;
  /** 历史首页加载完成前缓冲的 live 块（按 seq 去重、升序 flush）。 */
  pending: { seq: number; lines: string[] }[];
}

export const EMPTY_DEPLOY_LOG: DeployLogState = {
  lines: [],
  nextAfter: null,
  truncated: false,
  appliedSeq: null,
  historyLoaded: false,
  pending: [],
};

/** 该 chunk_seq 是否已并入或已缓冲（去重命中）——appliedSeq 单调 + pending 集合双查。 */
function seenSeq(state: DeployLogState, seq: number): boolean {
  if (state.appliedSeq !== null && seq <= state.appliedSeq) return true;
  return state.pending.some((p) => p.seq === seq);
}

/** 实时新块并入（订阅制 deployment.log，携 chunk_seq）：历史首页未并入前进 pending 缓冲，之后按
 *  单调 seq 去重追加到尾部。不动翻页游标 / 截断标记。 */
export function appendDeployLogChunk(
  prev: DeployLogState | undefined,
  seq: number,
  lines: string[],
): DeployLogState {
  const base = prev ?? EMPTY_DEPLOY_LOG;
  if (lines.length === 0 || seenSeq(base, seq)) return base;
  if (!base.historyLoaded) {
    return { ...base, pending: [...base.pending, { seq, lines }] };
  }
  return { ...base, lines: [...base.lines, ...lines], appliedSeq: seq };
}

/** 历史翻页并入（GET 一页）：追加该页行、推进游标、并入截断标记。首页并入（historyLoaded false→true）
 *  时把 pending 缓冲按 seq 升序去重 flush 到尾部，让历史先于实时。后续「加载更多」页不重复 flush。 */
export function mergeDeployLogPage(
  prev: DeployLogState | undefined,
  page: { lines?: string[] | null; next_after?: number | null; truncated?: boolean | null },
): DeployLogState {
  const base = prev ?? EMPTY_DEPLOY_LOG;
  const firstLoad = !base.historyLoaded;
  const merged: DeployLogState = {
    ...base,
    lines: [...base.lines, ...(page.lines ?? [])],
    nextAfter: page.next_after ?? null,
    truncated: base.truncated || !!page.truncated,
    historyLoaded: true,
  };
  if (!firstLoad || base.pending.length === 0) {
    return { ...merged, pending: [] };
  }
  // 首页并入：flush pending（升序、单调去重）到历史尾部。
  let appliedSeq = merged.appliedSeq;
  const lines = [...merged.lines];
  for (const chunk of [...base.pending].sort((a, b) => a.seq - b.seq)) {
    if (appliedSeq !== null && chunk.seq <= appliedSeq) continue;
    lines.push(...chunk.lines);
    appliedSeq = chunk.seq;
  }
  return { ...merged, lines, appliedSeq, pending: [] };
}

/** 历史首页拉取失败兜底（DeploymentCard catch）：标记 historyLoaded 并 flush pending，防 live 块
 *  因历史永不 resolve 而一直卡在缓冲不显示（历史尽力而为、失败不阻塞实时流）。 */
export function flushPendingDeployLog(prev: DeployLogState | undefined): DeployLogState {
  const base = prev ?? EMPTY_DEPLOY_LOG;
  if (base.historyLoaded) return base;
  return mergeDeployLogPage(base, { lines: [], next_after: base.nextAfter, truncated: base.truncated });
}
