"""RuntimeAdapter 接口（契约 E §9）与 AdapterSink 回调口。

E §9 的 RuntimeAdapter 是**每进程**运行时驱动的最小接口——daemon 主体不感知具体 runtime。
`claude_code.ClaudeCodeProcess` 是其 claude 实现；`codex.py`（M5）加实现不改形状。

四类出口回调（status / activity / usage / diagnostic）形状 = 契约 D §7 上报帧，
复用 A6 已定义的 `AdapterSink`（coagentia_daemon.adapter）——两处不重复定义（铁律：单点）。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from coagentia_contracts.daemon import AgentBoot

# AdapterSink 已在 A6 定义（status/activity/usage/diagnostic 四回调）；此处再导出，
# 让 adapters 子包用户无需跨模块 import。
from coagentia_daemon.adapter import AdapterSink

__all__ = ["AdapterSink", "RuntimeAdapter"]


@runtime_checkable
class RuntimeAdapter(Protocol):
    """契约 E §9：每 Agent 一个 runtime 子进程的统一驱动接口。

    - start(boot, resume)：spawn 子进程（resume=True → 附会话续接参数，见 reset_session_args）
    - stop()：优雅关 stdin → 等待 → terminate/kill
    - feed(text)：写入一个 turn 的 stdin 输入（§6 已编码的 user 帧文本）
    - reset_session_args()：三档重置的**会话层**命令行差异（restart 保会话=[--resume,id]；
      reset_session/reset_full 新会话=[]）——控制归代码，判断归上层管理器。
    """

    async def start(self, boot: AgentBoot, resume: bool) -> None: ...

    async def stop(self) -> None: ...

    async def feed(self, text: str) -> None: ...

    def reset_session_args(self) -> list[str]: ...
