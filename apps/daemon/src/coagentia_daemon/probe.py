"""runtime 探测（FR-2.3 / 契约 D §7 runtimes.detected；runtime.rescan 复用）。

探测 claude CLI 可执行与版本（`claude --version`）→ detected_runtimes。命令执行经可注入
runner（测试注桩免依赖真 CLI）；默认 runner 用 asyncio 子进程（win32 下 shutil.which 解析
`claude.cmd` 绝对路径）。DetectedRuntime 形状（runtime/installed/models[/skills]）在 contracts。

codex（M5，契约 E2）：`which codex` + `codex --version` 判在装；再 spawn `codex app-server`
调 model/list + skills/list 填 models / 候选技能池，taskkill 收尾（冷路径，CALIBRATION §8）。
skills 字段由 H0 在 contracts 落地——未生成时探测仍拿名，构造时按字段存在与否兼容（不阻塞）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from coagentia_contracts.entities import DetectedRuntime
from coagentia_contracts.enums import Runtime

from coagentia_daemon import __version__
from coagentia_daemon.adapters import codex_cmdline

# runner: (argv) -> (returncode, stdout, stderr)
CommandRunner = Callable[[list[str]], Awaitable[tuple[int, str, str]]]

# codex app-server 深探（model/list + skills/list）：(codex_path) -> (model_ids, skill_names)。
CodexQuery = Callable[[str], Awaitable[tuple[list[str], list[str]]]]

_CODEX_QUERY_TIMEOUT = 15.0  # 深探总上限（慢/挂的 app-server 不阻塞 hello，退化 models/skills=[]）

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


def _make_detected(
    runtime: Runtime, installed: bool, models: list[str], skills: list[str]
) -> DetectedRuntime:
    """构造 DetectedRuntime；skills 字段由 H0 落地——存在则填，否则兼容降级（不阻塞探测）。"""
    base: dict[str, Any] = {"runtime": runtime, "installed": installed, "models": models}
    if skills and "skills" in DetectedRuntime.model_fields:
        with contextlib.suppress(Exception):
            return DetectedRuntime(**base, skills=skills)
    return DetectedRuntime(**base)


async def probe_codex(
    runner: CommandRunner | None = None,
    *,
    which: Callable[[str], str | None] = shutil.which,
    query: CodexQuery | None = None,
) -> tuple[DetectedRuntime, str | None]:
    """探测 codex CLI（契约 E2 / FR-2.5）。返回 (DetectedRuntime, version|None)。

    未安装（which 未命中）或 `codex --version` 非零 → installed=False。命中 → 冷路径 spawn
    `codex app-server` 调 model/list + skills/list 填 models / 候选技能池；深探失败退化 [] 不阻塞。
    """
    path = which("codex")
    if not path:
        return _make_detected(Runtime.CODEX, False, [], []), None
    run = runner or _default_runner
    try:
        rc, out, _err = await run([path, "--version"])
    except (OSError, ValueError):
        return _make_detected(Runtime.CODEX, False, [], []), None
    if rc != 0:
        return _make_detected(Runtime.CODEX, False, [], []), None
    version = _parse_version(out)
    # 深探（spawn app-server）仅在生产默认路径跑（runner/query 均未注入）——注入 runner=测试上下文，
    # 跳过真机 spawn（deep query 走带内 stdio，无法经 runner 抽象；注入 query 则用桩）。
    if query is not None:
        q: CodexQuery | None = query
    elif runner is None:
        q = _query_codex_app_server
    else:
        q = None
    models: list[str] = []
    skills: list[str] = []
    if q is not None:
        with contextlib.suppress(Exception):
            models, skills = await asyncio.wait_for(q(path), timeout=_CODEX_QUERY_TIMEOUT)
    return _make_detected(Runtime.CODEX, True, models, skills), version


def _extract_model_ids(result: Any) -> list[str]:
    """model/list 响应 → 模型 id 列表（去重保序；id 优先，回退 model 字段）。"""
    data = result.get("data") if isinstance(result, dict) else None
    out: list[str] = []
    for item in data or []:
        if not isinstance(item, dict):
            continue
        mid = item.get("id") or item.get("model")
        if isinstance(mid, str) and mid and mid not in out:
            out.append(mid)
    return out


def _extract_skill_names(result: Any) -> list[str]:
    """skills/list 响应 → 技能名列表（跨 cwd 条目展平去重；候选池，列出≠授予）。"""
    data = result.get("data") if isinstance(result, dict) else None
    out: list[str] = []
    for entry in data or []:
        for skill in (entry.get("skills") if isinstance(entry, dict) else None) or []:
            name = skill.get("name") if isinstance(skill, dict) else None
            if isinstance(name, str) and name and name not in out:
                out.append(name)
    return out


async def _query_codex_app_server(codex_path: str) -> tuple[list[str], list[str]]:
    """spawn `codex app-server` → initialize/initialized → model/list + skills/list（E2 §8）。

    一次性冷探：读到两条响应即收；win32 taskkill /F /T 杀进程树收尾（terminate 杀不掉 node）。
    """
    proc = await asyncio.create_subprocess_exec(
        codex_path,
        "app-server",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    models: list[str] = []
    skills: list[str] = []
    try:
        assert proc.stdin is not None and proc.stdout is not None
        for msg in (
            {"id": 1, "method": "initialize",
             "params": {"clientInfo": {"name": "coagentia-probe", "version": __version__}}},
            {"method": "initialized"},
            {"id": 2, "method": "model/list", "params": {}},
            {"id": 3, "method": "skills/list", "params": {"cwds": [str(Path.home())]}},
        ):
            proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        await proc.stdin.drain()
        seen: set[int] = set()
        while len(seen) < 2:
            line = await proc.stdout.readline()
            if not line:
                break
            with contextlib.suppress(ValueError):
                frame = json.loads(line.decode("utf-8", "replace"))
                rid = frame.get("id") if isinstance(frame, dict) else None
                if rid == 2:
                    models = _extract_model_ids(frame.get("result"))
                    seen.add(2)
                elif rid == 3:
                    skills = _extract_skill_names(frame.get("result"))
                    seen.add(3)
    finally:
        await _kill_probe_process(proc)
    return models, skills


async def _kill_probe_process(proc: Any) -> None:
    if proc.returncode is not None:
        return
    if sys.platform == "win32" and proc.pid:
        with contextlib.suppress(Exception):
            killer = await asyncio.create_subprocess_exec(
                *codex_cmdline.taskkill_argv(proc.pid),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
    else:
        with contextlib.suppress(Exception):
            proc.kill()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(proc.wait(), timeout=3.0)


async def probe_runtimes(runner: CommandRunner | None = None) -> list[DetectedRuntime]:
    """全 runtime 探测（claude_code + codex，契约 E2）。"""
    claude, _cv = await probe_claude(runner)
    codex, _xv = await probe_codex(runner)
    return [claude, codex]
