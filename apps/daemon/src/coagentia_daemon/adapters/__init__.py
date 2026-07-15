"""契约 E 落地：Claude Code runtime 适配器（A7 替换 A6 FakeAdapter）。

- base.py     RuntimeAdapter Protocol（E §9 每进程驱动接口）+ AdapterSink 回调
- frames.py   stream-json 帧 → 四类回调映射（防腐层 / 相位聚合 / usage 提取，E §7/§8）
- encoding.py deliver / inject → stdin user 帧（E §6）
- cmdline.py  命令行拼装 + 环境隔离 + MCP 配置物化（E §2/§3）
- mcp.py      coagentia stdio MCP server（M1 最小工具集 → REST 代理，E §3）
- claude_code.py  ClaudeCodeProcess（每进程驱动）+ RuntimeManager（daemon 侧管理器，A6 接口）
- codex.py    CodexProcess（每进程驱动，契约 E2）+ CodexFrameRouter（JSON-RPC 帧映射）
- codex_cmdline.py  codex 命令行 + CODEX_HOME 隔离 + config.toml 物化（E2 §1/§2）
"""

from coagentia_daemon.adapters.claude_code import ClaudeCodeAdapter, RuntimeManager
from coagentia_daemon.adapters.codex import CodexProcess

__all__ = ["ClaudeCodeAdapter", "CodexProcess", "RuntimeManager"]
