"""runtime 探测（FR-2.3 / 契约 D §7）。"""

from __future__ import annotations

import pytest
from coagentia_contracts.enums import Runtime
from coagentia_daemon.probe import _parse_version, probe_claude, probe_runtimes


async def _runner_ok(argv: list[str]) -> tuple[int, str, str]:
    return 0, "2.1.205 (Claude Code)", ""


async def _runner_fail(argv: list[str]) -> tuple[int, str, str]:
    return 1, "", "boom"


@pytest.mark.asyncio
async def test_probe_claude_installed(monkeypatch) -> None:
    rt, version = await probe_claude(_runner_ok, which=lambda _n: "/usr/bin/claude")
    assert rt.runtime == Runtime.CLAUDE_CODE
    assert rt.installed is True
    assert rt.models  # 模型列表非空
    assert version == "2.1.205"


@pytest.mark.asyncio
async def test_probe_claude_not_on_path() -> None:
    rt, version = await probe_claude(_runner_ok, which=lambda _n: None)
    assert rt.installed is False
    assert rt.models == []
    assert version is None


@pytest.mark.asyncio
async def test_probe_claude_bad_exit() -> None:
    rt, version = await probe_claude(_runner_fail, which=lambda _n: "/usr/bin/claude")
    assert rt.installed is False


@pytest.mark.asyncio
async def test_probe_runtimes_list() -> None:
    rts = await probe_runtimes(_runner_ok)
    assert len(rts) == 1
    assert rts[0].runtime == Runtime.CLAUDE_CODE


def test_parse_version() -> None:
    assert _parse_version("2.1.205 (Claude Code)") == "2.1.205"
    assert _parse_version("no version here") is None
