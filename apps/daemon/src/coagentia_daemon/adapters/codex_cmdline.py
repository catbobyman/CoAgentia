"""Codex 命令行拼装 + CODEX_HOME 隔离 + config.toml 物化（契约 E2 §1/§2）。

纯函数（argv/env/config 构造）——可全量单测，不触发子进程。

与 claude cmdline 的差异面（E2 §1/§2 冻结）：
- 命令行：裸 `codex app-server`（stdio 默认监听；无 --listen 旗标，0.144.0 实测校准）。
- 隔离：`CODEX_HOME=<home>/.codex`（等价 CLAUDE_CONFIG_DIR，全局配置不继承，R6）；cwd=home_path。
- MCP 注入：CODEX_HOME/config.toml `[mcp_servers.coagentia]`（command/args）——工具目录复用
  `mcp_command()`（契约 E §3 REST 纯代理，runtime 无关）。
- 权限姿态（NFR5 bypassPermissions 等价）：approvalPolicy=never + sandbox=danger-full-access
  经 thread/start params 注入（见 codex.py），config.toml 只承载 MCP。
- 凭证物化：机器级 `~/.codex/auth.json` 复制进隔离 CODEX_HOME（ChatGPT 登录态；E2 §2.2）。
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
from pathlib import Path

from coagentia_daemon.adapters import cmdline

# win32 npm shim = codex.cmd；允许 env 覆盖（同 COAGENTIA_CLAUDE_BIN 先例）。
CODEX_BIN = os.environ.get("COAGENTIA_CODEX_BIN", "codex")

# 机器级凭证文件（ChatGPT 登录态；隔离 CODEX_HOME 需物化才能鉴权，E2 §2.2）。
_CREDENTIAL_FILES = ("auth.json",)


def resolve_codex_bin() -> str:
    """解析 codex 可执行绝对路径（win32 下 which 命中 codex.cmd）；未命中回退裸名。"""
    return shutil.which(CODEX_BIN) or CODEX_BIN


def build_app_server_argv() -> list[str]:
    """`codex app-server` 命令行（E2 §1.2；stdio 为默认监听面，无 --listen 旗标）。"""
    return [resolve_codex_bin(), "app-server"]


def machine_codex_home() -> Path:
    """机器级 codex home（凭证物化源）：env CODEX_HOME 或 ~/.codex。"""
    env = os.environ.get("CODEX_HOME")
    return Path(env) if env else Path.home() / ".codex"


def isolated_codex_home(home_path: str) -> Path:
    """per-Agent 隔离 CODEX_HOME = <home>/.codex（E2 §2.1）。"""
    return Path(home_path).expanduser() / ".codex"


def build_env(home_path: str, base_env: dict[str, str] | None = None) -> dict[str, str]:
    """CODEX_HOME 隔离（E2 §2.1）：配置目录钉在 Home 内，全局配置/技能不继承。"""
    env = dict(base_env if base_env is not None else os.environ)
    env["CODEX_HOME"] = str(isolated_codex_home(home_path))
    return env


def materialize_credentials(codex_home: Path, source: Path | None = None) -> list[str]:
    """把机器级 codex 凭证复制进隔离 CODEX_HOME（E2 §2.2；ChatGPT 登录态）。

    best-effort：源缺失/损坏不抛（未登录 → 鉴权失败由 turn error 面暴露，verify 阶段确认）。
    """
    src = source or machine_codex_home()
    codex_home = codex_home.expanduser()
    if src.resolve() == codex_home.resolve():
        return []
    copied: list[str] = []
    codex_home.mkdir(parents=True, exist_ok=True)
    for name in _CREDENTIAL_FILES:
        s = src / name
        if not s.is_file():
            continue
        with contextlib.suppress(OSError):
            d = codex_home / name
            # 新鲜度选优（review #5）：隔离目标已存在且不比机器源旧 → 保留。codex app-server 运行时
            # 会刷新 OAuth token（写隔离 auth.json），无条件覆写会用机器旧凭证回退刷新；仅目标缺失
            # （首次）或机器源更新（用户重登）才复制。
            if d.is_file() and d.stat().st_mtime >= s.stat().st_mtime:
                continue
            data = s.read_bytes()
            tmp = d.with_name(f"{d.name}.{os.getpid()}.tmp")
            tmp.write_bytes(data)
            with contextlib.suppress(OSError):
                tmp.chmod(0o600)
            tmp.replace(d)
            copied.append(name)
    return copied


def build_config_toml(*, agent_member_id: str, server_url: str, api_key: str) -> str:
    """config.toml 内容（E2 §2.3）：注入名为 coagentia 的 stdio MCP server。

    值用 json.dumps 序列化——JSON basic string / array 是合法 TOML basic string / array
    （同一双引号转义规则），win32 反斜杠路径由此正确转义。
    """
    cmd, base_args = cmdline.mcp_command()
    args = [
        *base_args,
        "--agent-member",
        agent_member_id,
        "--server-url",
        server_url,
        "--api-key",
        api_key,
    ]
    return (
        "# CoAgentia 生成（E2 §2.3）——per-Agent 隔离 CODEX_HOME；勿手改。\n"
        "[mcp_servers.coagentia]\n"
        f"command = {json.dumps(cmd)}\n"
        f"args = {json.dumps(args)}\n"
    )


def materialize_config(
    codex_home: Path, *, agent_member_id: str, server_url: str, api_key: str
) -> Path:
    """把 config.toml 写入 <CODEX_HOME>/config.toml，返回路径。"""
    codex_home = codex_home.expanduser()
    codex_home.mkdir(parents=True, exist_ok=True)
    path = codex_home / "config.toml"
    payload = build_config_toml(
        agent_member_id=agent_member_id, server_url=server_url, api_key=api_key
    )
    path.write_text(payload, encoding="utf-8")
    return path


def taskkill_argv(pid: int) -> list[str]:
    """win32 杀进程树（E2 §1.2）：terminate 杀不掉 app-server 底层 node，须 taskkill /F /T。"""
    return ["taskkill", "/F", "/T", "/PID", str(pid)]


def is_win32() -> bool:
    return sys.platform == "win32"
