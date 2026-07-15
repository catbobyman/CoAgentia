// 时间显示统一入口:server 时间戳为带 Z 的 UTC ISO-8601(ledger.now_iso),渲染一律转本地时区。
// M2 二轮 review:消息流用 slice(11,16) 硬切 ISO 显示 UTC 原样,与 Activity 屏本地时间同刻不同显;
// 各屏私有 relTime 也在此收拢为单一实现。

/** HH:MM(本地时区,24h)。无效输入回 '—'。 */
export function fmtTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
}

/** HH:MM:SS(本地时区)——诊断流等需要秒级的位置。 */
export function fmtTimeSec(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}

/** MM-DD(本地时区)——消息流日期分隔等。 */
export function fmtDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/** 相对语义:今天→HH:MM / 昨天 / MM-DD(P9 Activity 与 P11 聚合板共用)。 */
export function relTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const now = new Date();
  if (d.toDateString() === now.toDateString()) return fmtTime(iso);
  const yst = new Date(now);
  yst.setDate(now.getDate() - 1);
  if (d.toDateString() === yst.toDateString()) return '昨天';
  return fmtDate(iso);
}
