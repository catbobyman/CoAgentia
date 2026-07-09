"""命令行拼装 + 环境隔离 + MCP 配置物化（契约 E §2/§3）。

纯函数（argv/env/config 构造）——可全量单测，不触发子进程。

E §2 命令行：
    claude --output-format stream-json --input-format stream-json
           --include-partial-messages --permission-mode bypassPermissions
           --model <model> --append-system-prompt <身份注入>
           --mcp-config <coagentia-mcp.json>
           --disallowed-tools <DISALLOWED_TOOLS...> --verbose
隔离：CLAUDE_CONFIG_DIR=<home>/.claude（全局技能/配置不继承，R6）；cwd=home_path。
`--verbose` 本模式真机实测必需（否则帧不全，E §11.2 已确认）。
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from coagentia_contracts.constants import DISALLOWED_TOOLS
from coagentia_contracts.daemon import AgentBoot

CLAUDE_BIN = os.environ.get("COAGENTIA_CLAUDE_BIN", "claude")

# 身份注入文案（E §2：文本是产品文案不冻结；必含名字/member_id/工具用法/护栏约定）。
_IDENTITY_TEMPLATE = (
    "你是 CoAgentia 工作区的 Agent「{name}」（member_id={member_id}）。\n"
    "工作区语言：中文；沟通简洁、对事不对人。\n"
    "【发言纪律】你的一切主动行为都必须通过名为 coagentia 的 MCP server 提供的工具完成，"
    "对应关系：\n"
    "  · 发频道/线程消息 → coagentia 的 send_message 工具（**不是**内置 SendMessage）；\n"
    "  · 上传文件 → upload_file；回看历史 → get_messages / get_thread；\n"
    "  · 建/销提醒 → create_reminder / cancel_reminder；看频道/成员 → list_channels / list_members。\n"
    "这些工具属 coagentia MCP server；若尚未加载，先用 ToolSearch 搜 \"coagentia\" 载入再调用。\n"
    "散文正文不会被转成频道消息——只有显式调用 coagentia 工具才会真正发出。\n"
    "护栏：send_message 返回 202 held（被扣）时停止重发、等待反馈直投，勿盲目重试。\n"
    "记忆载体是你的 Home（MEMORY.md / notes/），当前工作目录即你的 Home。"
)


def build_identity_prompt(boot: AgentBoot) -> str:
    """--append-system-prompt 身份注入文本（§2）。"""
    return _IDENTITY_TEMPLATE.format(name=boot.name, member_id=boot.agent_member_id)


def build_env(home_path: str, base_env: dict[str, str] | None = None) -> dict[str, str]:
    """CLAUDE_CONFIG_DIR 隔离（§2）：配置目录钉在 Home 内，全局技能/配置不继承。"""
    env = dict(base_env if base_env is not None else os.environ)
    env["CLAUDE_CONFIG_DIR"] = str(Path(home_path).expanduser() / ".claude")
    return env


def default_config_dir() -> Path:
    """机器级 claude 配置目录（凭证物化源，FR-2.3）。"""
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(env) if env else Path.home() / ".claude"


_CREDENTIAL_FILES = (".credentials.json",)


def materialize_credentials(config_dir: Path, source: Path | None = None) -> list[str]:
    """把机器级 runtime 凭证复制进隔离配置目录（§2 凭证物化；BYO Key 不经 server）。

    仅在目标缺失时复制（幂等）；源缺失静默跳过（未登录场景由 CLI 自身报错）。
    """
    src = source or default_config_dir()
    if src.resolve() == config_dir.resolve():
        return []
    copied: list[str] = []
    config_dir.mkdir(parents=True, exist_ok=True)
    for name in _CREDENTIAL_FILES:
        s = src / name
        d = config_dir / name
        if s.is_file() and not d.exists():
            with contextlib.suppress(OSError):
                d.write_bytes(s.read_bytes())
                copied.append(name)
    return copied


def mcp_command() -> tuple[str, list[str]]:
    """coagentia MCP stdio server 的启动命令（§3）。

    以当前 Python 解释器 `-m coagentia_daemon mcp ...` 拉起（免依赖 PATH 上的 console script；
    E §3 原型是 `uvx coagentia-daemon mcp ...`，本机用同解释器等价）。
    """
    return sys.executable, ["-m", "coagentia_daemon", "mcp"]


def build_mcp_config(
    *, agent_member_id: str, server_url: str, api_key: str
) -> dict[str, Any]:
    """coagentia-mcp.json 内容（§3）：注入名为 coagentia 的 stdio MCP server。"""
    cmd, base_args = mcp_command()
    return {
        "mcpServers": {
            "coagentia": {
                "type": "stdio",
                "command": cmd,
                "args": [
                    *base_args,
                    "--agent-member",
                    agent_member_id,
                    "--server-url",
                    server_url,
                    "--api-key",
                    api_key,
                ],
            }
        }
    }


def materialize_mcp_config(
    config_dir: Path, *, agent_member_id: str, server_url: str, api_key: str
) -> Path:
    """把 MCP 配置写入 <config_dir>/coagentia-mcp.json，返回路径。"""
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "coagentia-mcp.json"
    payload = build_mcp_config(
        agent_member_id=agent_member_id, server_url=server_url, api_key=api_key
    )
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def build_argv(
    boot: AgentBoot,
    *,
    mcp_config_path: str | os.PathLike[str] | None = None,
    resume_session_id: str | None = None,
) -> list[str]:
    """claude CLI 命令行（§2）。resume_session_id 给定 → 附 `--resume <id>`（会话续接）。"""
    argv: list[str] = [
        CLAUDE_BIN,
        "--output-format",
        "stream-json",
        "--input-format",
        "stream-json",
        "--include-partial-messages",
        "--permission-mode",
        "bypassPermissions",
        "--verbose",  # 本模式必需（E §11.2 实测确认）
        "--model",
        boot.model,
        "--append-system-prompt",
        build_identity_prompt(boot),
    ]
    if mcp_config_path is not None:
        argv += ["--mcp-config", str(mcp_config_path), "--strict-mcp-config"]
    if DISALLOWED_TOOLS:
        # --disallowed-tools <tools...> 变参：逐个 argv 元素（后接的 flag 终止收集）。
        argv.append("--disallowed-tools")
        argv.extend(DISALLOWED_TOOLS)
    if resume_session_id:
        argv += ["--resume", resume_session_id]
    return argv
