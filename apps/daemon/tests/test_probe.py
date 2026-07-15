"""runtime 探测（FR-2.3 / 契约 D §7）。"""

from __future__ import annotations

import pytest
from coagentia_contracts.enums import Runtime
from coagentia_daemon.probe import (
    _parse_version,
    probe_claude,
    probe_runtimes,
    scan_claude_skills,
)


async def _runner_ok(argv: list[str]) -> tuple[int, str, str]:
    return 0, "2.1.205 (Claude Code)", ""


async def _runner_fail(argv: list[str]) -> tuple[int, str, str]:
    return 1, "", "boom"


@pytest.mark.asyncio
async def test_probe_claude_installed(monkeypatch) -> None:
    rt, version = await probe_claude(
        _runner_ok, which=lambda _n: "/usr/bin/claude", skills_scan=lambda: ["docx", "pdf"]
    )
    assert rt.runtime == Runtime.CLAUDE_CODE
    assert rt.installed is True
    assert rt.models  # 模型列表非空
    assert rt.skills == ["docx", "pdf"]  # 候选池扫描（契约 E v1.4 §9）
    assert version == "2.1.205"


@pytest.mark.asyncio
async def test_probe_claude_skills_empty_when_not_installed() -> None:
    # 未安装 → 不扫技能（skills_scan 桩即便非空也不调用）。
    rt, _ = await probe_claude(
        _runner_ok, which=lambda _n: None, skills_scan=lambda: ["should-not-appear"]
    )
    assert rt.installed is False
    assert (rt.skills or []) == []


def test_scan_claude_skills_lists_subdirs(tmp_path) -> None:
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "docx").mkdir()
    (skills / "pdf").mkdir()
    (skills / ".hidden").mkdir()  # 隐藏项跳过
    (skills / "SCHEMA.md").write_text("x")  # 非目录跳过
    assert scan_claude_skills(tmp_path) == ["docx", "pdf"]


def test_scan_claude_skills_missing_dir(tmp_path) -> None:
    assert scan_claude_skills(tmp_path / "nonexistent") == []


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
    # 注入 runner（测试上下文）→ codex 深探跳过真机 spawn；两 runtime 均在列。
    rts = await probe_runtimes(_runner_ok)
    assert len(rts) == 2
    assert rts[0].runtime == Runtime.CLAUDE_CODE
    assert rts[1].runtime == Runtime.CODEX


def test_parse_version() -> None:
    assert _parse_version("2.1.205 (Claude Code)") == "2.1.205"
    assert _parse_version("no version here") is None


def test_scan_claude_skills_skips_symlinks(tmp_path) -> None:
    """symlink 不跟随（review #3：防指向大目录/循环）——即便指向真目录也跳过。"""
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "real").mkdir()
    target = tmp_path / "elsewhere"
    target.mkdir()
    try:
        (skills / "linked").symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        import pytest

        pytest.skip("symlink 不可用（Windows 无权限/平台限制）")
    from coagentia_daemon.probe import scan_claude_skills

    assert scan_claude_skills(tmp_path) == ["real"]
