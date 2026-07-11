// 循环 Reminder 的 cron cadence 解析与人读预览（B §11.5 五段式：分 时 日 月 周，本地时区）。
// 判定语义的权威在服务层（H4 单点解析器）——前端只做「展示层」的最佳努力预览，
// 无法识别的写法回退为「原样 mono 显示」（纪律 7：不复制值域判定，仅供人读辅助）。

// 周字段数字 → 中文（0 与 7 皆为周日，cron 惯例）。
const WEEKDAY_CN = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];

/** 是否为五段式 cron cadence：恰 5 段且每段仅含 cron 合法字符（排除 "daily 09:00"/"every 30m"）。 */
export function isCronCadence(cadence: string): boolean {
  const parts = cadence.trim().split(/\s+/);
  if (parts.length !== 5) return false;
  return parts.every((p) => /^[\d*,\-/]+$/.test(p));
}

// 单个周字段 token（数字/范围/列表）→ 中文短语；无法识别返回 undefined。
function describeWeekday(dow: string): string | undefined {
  const wd = (n: number): string | undefined => (n >= 0 && n <= 7 ? WEEKDAY_CN[n % 7] : undefined);
  if (/^\d+$/.test(dow)) return wd(Number(dow));
  // 范围 a-b（如 1-5 → 周一至周五）
  const range = /^(\d+)-(\d+)$/.exec(dow);
  if (range) {
    const a = wd(Number(range[1]));
    const b = wd(Number(range[2]));
    return a && b ? `${a}至${b}` : undefined;
  }
  // 列表 a,b,c（如 1,3,5 → 周一、周三、周五）
  if (/^\d+(,\d+)+$/.test(dow)) {
    const names = dow.split(',').map((x) => wd(Number(x)));
    if (names.every((n): n is string => n !== undefined)) return names.join('、');
  }
  return undefined;
}

// 分/时两段均为纯数字 → HH:MM；否则 undefined。
function formatClock(min: string, hour: string): string | undefined {
  if (!/^\d+$/.test(min) || !/^\d+$/.test(hour)) return undefined;
  const h = Number(hour);
  const m = Number(min);
  if (h > 23 || m > 59) return undefined;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

// 步进 */n → n；否则 undefined。
function stepOf(token: string): number | undefined {
  const m = /^\*\/(\d+)$/.exec(token);
  if (!m) return undefined;
  const n = Number(m[1]);
  return n > 0 ? n : undefined;
}

/**
 * cron cadence → 人读中文预览（如「每周五 09:00」「每天 09:00」「每月 1 号 08:30」「每 5 分钟」）。
 * 只覆盖常见形态；无法识别 → undefined（调用方仅显示原串）。
 */
export function cronPreview(cadence: string): string | undefined {
  if (!isCronCadence(cadence)) return undefined;
  // 月字段（第 4 段）不进人读口径——预览按「分 时 日 周」四维定档（可简单映射，B §11.5）。
  const [min, hour, dom, , dow] = cadence.trim().split(/\s+/) as [
    string, string, string, string, string,
  ];

  // 高频步进：每 N 分钟 / 每 N 小时（时间面为 * 时优先识别）。
  const minStep = stepOf(min);
  if (minStep && hour === '*' && dom === '*' && dow === '*') {
    return `每 ${minStep} 分钟`;
  }
  const hourStep = stepOf(hour);
  if (hourStep && /^\d+$/.test(min) && dom === '*' && dow === '*') {
    return `每 ${hourStep} 小时`;
  }

  const clock = formatClock(min, hour);
  if (!clock) return undefined; // 定点类必须能定出时刻，否则回退原串

  const timeSuffix = ` ${clock}`;
  if (dow !== '*') {
    const w = describeWeekday(dow);
    return w ? `每${w}${timeSuffix}` : undefined;
  }
  if (dom !== '*') {
    if (!/^\d+$/.test(dom)) return undefined;
    return `每月 ${Number(dom)} 号${timeSuffix}`;
  }
  return `每天${timeSuffix}`;
}
