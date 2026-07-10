"""runtime 探测（FR-2.3 / 契约 D §7 runtimes.detected；runtime.rescan 复用）。

探测 claude CLI 可执行与版本（`claude --version`）→ detected_runtimes。命令执行经可注入
runner（测试注桩免依赖真 CLI）；默认 runner 用 asyncio 子进程（win32 下 shutil.which 解析
`claude.cmd` 绝对路径）。DetectedRuntime 形状（runtime/installed/models）在 contracts。
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Awaitable, Callable

from coagentia_contracts.entities import DetectedRuntime
from coagentia_contracts.enums import Runtime

# runner: (argv) -> (returncode, stdout, stderr)
CommandRunner = Callable[[list[str]], Awaitable[tuple[int, str, str]]]

# claude Code 已知模型（UI 模型下拉候选；契约无版本字段，模型列表为 detected_runtimes.models）。
# 权威候选随 CLI 演进——A7 真冒烟后可从 init 帧的 model/slash_commands 富化，此处给 MVP 默认。
DEFAULT_CLAUDE_MODELS: tuple[str, ...] = (
    "claude-opus-4-8",
    "claude-sonnet-4-5",
    "claude-haiku-4-5",
)


async def _default_runner(argv: list[str]) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


async def probe_claude(
    runner: CommandRunner | None = None,
    *,
    which: Callable[[str], str | None] = shutil.which,
) -> tuple[DetectedRuntime, str | None]:
    """探测 claude CLI。返回 (DetectedRuntime, version|None)。

    未安装（which 未命中）→ installed=False, models=[]。
    命中 → `claude --version` rc==0 视为可用，models 用已知候选。
    """
    path = which("claude")
    if not path:
        return DetectedRuntime(runtime=Runtime.CLAUDE_CODE, installed=False, models=[]), None
    run = runner or _default_runner
    try:
        rc, out, _err = await run([path, "--version"])
    except (OSError, ValueError):
        return DetectedRuntime(runtime=Runtime.CLAUDE_CODE, installed=False, models=[]), None
    installed = rc == 0
    version = _parse_version(out) if installed else None
    models = list(DEFAULT_CLAUDE_MODELS) if installed else []
    return DetectedRuntime(runtime=Runtime.CLAUDE_CODE, installed=installed, models=models), version


def _parse_version(text: str) -> str | None:
    """从 `2.1.205 (Claude Code)` 一类输出提取首个 x.y.z。"""
    for token in text.replace("(", " ").replace(")", " ").split():
        parts = token.split(".")
        if len(parts) >= 2 and all(p.isdigit() for p in parts[:2]):
            return token
    return None


async def probe_runtimes(runner: CommandRunner | None = None) -> list[DetectedRuntime]:
    """全 runtime 探测（M1 仅 claude_code；codex 归 M5 契约 E §9）。"""
    claude, _version = await probe_claude(runner)
    return [claude]
