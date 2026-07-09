"""数据目录布局（契约 D §9.1/§9.3）。"""

from __future__ import annotations

from pathlib import Path

from coagentia_daemon.paths import DataPaths


def test_ensure_dirs_creates_subtree(tmp_path: Path) -> None:
    p = DataPaths(tmp_path / "root")
    p.ensure_dirs()
    assert p.daemon_dir.is_dir()
    assert p.buffer_dir.is_dir()
    assert p.state_dir.is_dir()
    assert p.agents_dir.is_dir()


def test_agent_home_uses_member_id(tmp_path: Path) -> None:
    p = DataPaths(tmp_path / "root")
    home = p.ensure_agent_home("01K5AGENT0000000000000000A")
    assert home.is_dir()
    assert home.name == "01K5AGENT0000000000000000A"


def test_clear_agent_home_keeps_dir_removes_contents(tmp_path: Path) -> None:
    p = DataPaths(tmp_path / "root")
    home = p.ensure_agent_home("aid")
    (home / "a.txt").write_text("x", encoding="utf-8")
    sub = home / "sub"
    sub.mkdir()
    (sub / "b.txt").write_text("y", encoding="utf-8")
    p.clear_agent_home("aid")
    assert home.is_dir()
    assert list(home.iterdir()) == []


def test_session_bookkeeping_roundtrip(tmp_path: Path) -> None:
    p = DataPaths(tmp_path / "root")
    p.ensure_dirs()
    assert p.read_session("aid") == {}
    p.write_session("aid", {"source_session": "sess-1"})
    assert p.read_session("aid") == {"source_session": "sess-1"}
    p.clear_session("aid")
    assert p.read_session("aid") == {}


def test_default_root_is_home_coagentia() -> None:
    p = DataPaths()
    assert p.root == Path.home() / ".coagentia"
