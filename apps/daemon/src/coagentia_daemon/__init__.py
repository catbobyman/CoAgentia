"""coagentia-daemon：执行面客户端（契约 D daemon 侧）。

五职责（FR-2.2）的 daemon 半边：保连接 / 跑 Agent / 管进程 / 投递消息 / 跑交付进程。
本包实现契约 D 的 daemon 侧线协议（握手/对账消费/指令幂等消费/遥测上行/数据目录）；
Agent 进程的实际驱动（命令行拼装、stream-json 解析）归契约 E 的 RuntimeAdapter（A7）。
"""

from __future__ import annotations

__version__ = "0.1.0"
