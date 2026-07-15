"""CLI 入口（契约 D §2 daemon 主进程；契约 E §3 `mcp` 子命令）。

- `coagentia-daemon --server-url <url> --api-key <key>`：daemon 主循环。
- `coagentia-daemon mcp --agent-member <id> --server-url <url> --api-key <key>`：
  coagentia stdio MCP server（由 claude 子进程经 --mcp-config 拉起，E §3）。

win32 显式设 Proactor event loop（asyncio 子进程需 Proactor，契约 E/00 §4.5）；组装
DataPaths + TelemetryBuffer + ClaudeCodeAdapter（A7 真适配器）+ DaemonClient，进无限重连主循环。
"""

from __future__ import annotations

import argparse
import asyncio
import platform
import sys

from coagentia_daemon import __version__
from coagentia_daemon.adapters import RuntimeManager
from coagentia_daemon.buffer import TelemetryBuffer
from coagentia_daemon.client import DaemonClient
from coagentia_daemon.paths import DataPaths


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="coagentia-daemon")
    sub = parser.add_subparsers(dest="command")

    mcp = sub.add_parser("mcp", help="coagentia stdio MCP server（契约 E §3）")
    mcp.add_argument("--agent-member", required=True)
    mcp.add_argument("--server-url", required=True)
    mcp.add_argument("--api-key", required=True)

    parser.add_argument("--server-url", help="server 基址，如 http://127.0.0.1:8787")
    parser.add_argument("--api-key", help="Add Computer 弹窗生成的 api-key")
    parser.add_argument("--data-root", default=None, help="数据根目录（默认 ~/.coagentia）")
    parser.add_argument("--version", action="version", version=f"coagentia-daemon {__version__}")
    return parser.parse_args(argv)


def _install_win32_loop_policy() -> None:
    if sys.platform == "win32":
        # asyncio 子进程在 win32 需 Proactor loop（契约 E daemon 用 asyncio 子进程）。
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


def build_client(server_url: str, api_key: str, *, data_root: str | None = None) -> DaemonClient:
    paths = DataPaths(data_root)
    paths.ensure_dirs()
    buffer = TelemetryBuffer(paths)
    # M5：runtime 管理器按 boot.runtime 分派 claude / codex 进程类（契约 E2）。
    adapter = RuntimeManager(paths, server_url=server_url, api_key=api_key)
    return DaemonClient(
        server_url=server_url,
        api_key=api_key,
        adapter=adapter,
        buffer=buffer,
        paths=paths,
        os_name=f"{platform.system()} {platform.release()}",
        arch=platform.machine(),
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "mcp":
        from coagentia_daemon.adapters import mcp as mcp_mod

        return mcp_mod.run(args.agent_member, args.server_url, args.api_key)
    if not args.server_url or not args.api_key:
        raise SystemExit("--server-url 与 --api-key 必填")
    _install_win32_loop_policy()
    # daemon 主进程文件日志装配（B-4 可观测性；mcp 子进程路径已在上面 return，不装配）。
    from coagentia_daemon.logconfig import setup_file_logging

    paths = DataPaths(args.data_root)
    paths.ensure_dirs()
    log = setup_file_logging(paths)
    log.info(
        "daemon starting: version=%s server_url=%s data_root=%s",
        __version__,
        args.server_url,
        args.data_root or "(default)",
    )
    client = build_client(args.server_url, args.api_key, data_root=args.data_root)

    async def run_client() -> None:
        try:
            await client.run()
        finally:
            await asyncio.shield(client.shutdown())

    try:
        asyncio.run(run_client())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
