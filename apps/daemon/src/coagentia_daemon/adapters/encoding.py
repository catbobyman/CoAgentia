"""输入编码（契约 E §6）：deliver 批 / inject → stdin stream-json user 帧。

- 帧格式：一行一个 `{"type":"user","message":{"role":"user","content":[{"type":"text",...}]}}`。
- deliver 渲染：批首注明投递原因；每条 `[#频道] @作者 (时间): 正文`（结构化纯文本，同 @解析原则）。
- inject（S1 直投）：首行 `[system → 仅你可见] (来源)`，不进频道流的语义由 server 保证，适配器只喂。

**M1 限制**：deliver 只拿到 channel_id / author_member_id（非人类可读名）；此处按 id 渲染，
名字解析属 server 富化面，不在适配器职责内（见 open_issues）。
"""

from __future__ import annotations

import json
from typing import Any

from coagentia_contracts.enums import WakeReason

_WAKE_LABEL: dict[str, str] = {
    WakeReason.CHANNEL_MESSAGE.value: "频道新消息",
    WakeReason.MENTION.value: "有人 @你",
    WakeReason.REMINDER.value: "提醒触发",
    WakeReason.CANVAS_ACTIVATION.value: "画布激活",
}


def user_frame_line(text: str) -> str:
    """渲染文本 → 单行 stream-json user 帧（stdin 一行一帧，§6.1）。"""
    frame = {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }
    return json.dumps(frame, ensure_ascii=False)


def render_message(msg: dict[str, Any]) -> str:
    """单条消息 → `[#频道] @作者 (时间): 正文`（§6.2）。"""
    channel = msg.get("channel_id") or "?"
    author = msg.get("author_member_id") or "system"
    when = msg.get("created_at") or ""
    body = msg.get("body") or ""
    return f"[#{channel}] @{author} ({when}): {body}"


def render_deliver(
    messages: list[dict[str, Any]],
    *,
    reason: str | None = None,
    thread_root_id: str | None = None,
) -> str:
    """deliver 批 → 单个 turn 输入文本（批首投递原因 + 每条渲染行，§6.2）。"""
    lines: list[str] = []
    header_bits: list[str] = []
    if reason:
        header_bits.append(_WAKE_LABEL.get(reason, reason))
    if thread_root_id:
        header_bits.append(f"线程 {thread_root_id}")
    if header_bits:
        lines.append(f"[投递 · {' · '.join(header_bits)}]")
    lines.extend(render_message(m) for m in messages)
    return "\n".join(lines)


def render_inject(body: str, source: dict[str, Any] | None = None) -> str:
    """inject（S1 直投）→ 首行系统标注 + 正文（§6.3）。"""
    kind = (source or {}).get("kind")
    ref = (source or {}).get("ref")
    label = "[system → 仅你可见]"
    if kind:
        label += f" ({kind}{f': {ref}' if ref else ''})"
    return f"{label}\n{body}"


# 说明：render_* 是**运行时无关正文**（管理器单点渲染，纪律 8）；载体封装（claude=stream-json
# user 帧 `user_frame_line` / codex=turn/start input）落在各 Process，不在此层组合。
