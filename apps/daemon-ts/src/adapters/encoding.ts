/**
 * 输入编码（契约 E §6）：deliver 批 / inject → stdin stream-json user 帧。
 *
 * - 帧格式：一行一个 `{"type":"user","message":{"role":"user","content":[{"type":"text",...}]}}`。
 * - deliver 渲染：批首注明投递原因；每条 `[#频道] @作者 (时间): 正文`（结构化纯文本，同 @解析原则）。
 * - inject（S1 直投）：首行 `[system → 仅你可见] (来源)`，不进频道流的语义由 server 保证，适配器只喂。
 *
 * **M1 限制**：deliver 只拿到 channel_id / author_member_id（非人类可读名）；此处按 id 渲染，
 * 名字解析属 server 富化面，不在适配器职责内（见 open_issues）。
 *
 * 对等基准 = apps/daemon adapters/encoding.py。
 * py/TS 差异（登记）：py json.dumps 默认分隔符含空格（`", "`/`": "`），JSON.stringify 紧凑无空格；
 * 消费端（CLI stdin）按 JSON 解析，语义等价。非 ASCII 两侧均不转义（ensure_ascii=False ≡ JSON.stringify）。
 */

import type { WakeReason } from '@coagentia/contracts-ts';

export type JsonObject = Record<string, unknown>;

// 值域绑定契约 WakeReason（type-only）；运行时字面量与 py enums.WakeReason.value 对齐。
const WAKE_LABEL: Record<string, string> = {
  channel_message: '频道新消息',
  mention: '有人 @你',
  reminder: '提醒触发',
  canvas_activation: '画布激活',
} satisfies Record<WakeReason, string>;

/** 渲染文本 → 单行 stream-json user 帧（stdin 一行一帧，§6.1）。 */
export function userFrameLine(text: string): string {
  const frame = {
    type: 'user',
    message: { role: 'user', content: [{ type: 'text', text }] },
  };
  return JSON.stringify(frame);
}

/** 单条消息 → `[#频道] @作者 (时间): 正文`（§6.2）。 */
export function renderMessage(msg: JsonObject): string {
  const channel = msg['channel_id'] || '?';
  const author = msg['author_member_id'] || 'system';
  const when = msg['created_at'] || '';
  const body = msg['body'] || '';
  return `[#${String(channel)}] @${String(author)} (${String(when)}): ${String(body)}`;
}

/** deliver 批 → 单个 turn 输入文本（批首投递原因 + 每条渲染行，§6.2）。 */
export function renderDeliver(
  messages: JsonObject[],
  opts: { reason?: string | null; threadRootId?: string | null } = {},
): string {
  const lines: string[] = [];
  const headerBits: string[] = [];
  if (opts.reason) {
    headerBits.push(WAKE_LABEL[opts.reason] ?? opts.reason);
  }
  if (opts.threadRootId) {
    headerBits.push(`线程 ${opts.threadRootId}`);
  }
  if (headerBits.length > 0) {
    lines.push(`[投递 · ${headerBits.join(' · ')}]`);
  }
  for (const m of messages) lines.push(renderMessage(m));
  return lines.join('\n');
}

/** inject（S1 直投）→ 首行系统标注 + 正文（§6.3）。 */
export function renderInject(body: string, source?: JsonObject | null): string {
  const kind = (source ?? {})['kind'];
  const ref = (source ?? {})['ref'];
  let label = '[system → 仅你可见]';
  if (kind) {
    label += ` (${String(kind)}${ref ? `: ${String(ref)}` : ''})`;
  }
  return `${label}\n${body}`;
}

// 说明：render* 是**运行时无关正文**（管理器单点渲染，纪律 8）；载体封装（claude=stream-json
// user 帧 `userFrameLine` / codex=turn/start input）落在各 Process，不在此层组合。
