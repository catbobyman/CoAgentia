"""PS-WT W1 daemon 单元：fs.tree 只读浏览 / worktree.scan 实时扫描 / 孤儿清理护栏。

fs.tree：根视图列盘符（win32 逐盘 / posix 单条 "/"）、子层仅列目录、has_git 命中、denied 逐条
降级、500 截断、永不读文件内容。worktree.scan：空目录、非 ULID 跳过、dirty/branch 解析、单树
git 失败降级不炸整扫。清理护栏：worktrees_dir 外路径被拒、coagentia/ 前缀限定、目录已删幂等成功。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from coagentia_contracts.daemon import (
    FsTreeReply,
    WorktreeCleanupData,
    WorktreeEnsureData,
    WorktreeScanQuery,
    WorktreeScanReply,
)
from coagentia_daemon import client as client_mod
from coagentia_daemon.client import (
    _fs_dir_entries,
    _fs_root_entries,
    _fs_scan_entry,
)
from coagentia_daemon.git import (
    GitWorktreeManager,
    WorktreeSafetyError,
    _main_worktree_head,
)
from coagentia_daemon.paths import DataPaths
from coagentia_daemon.util import new_ulid, now_iso
from helpers import RecordingTransport, make_client

# ---------------------------------------------------------------- git 仓库脚手架


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} 失败：{result.stdout}\n{result.stderr}")
    return result.stdout


def _seed_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "扫描 repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(repo)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    _git(repo, "config", "user.name", "CoAgentia Test")
    _git(repo, "config", "user.email", "test@coagentia.local")
    _git(repo, "config", "core.autocrlf", "false")
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "--", "seed.txt")
    _git(repo, "commit", "-m", "seed")
    return repo


def _manager(tmp_path: Path) -> tuple[GitWorktreeManager, DataPaths]:
    paths = DataPaths(tmp_path / "root")
    paths.ensure_dirs()
    return GitWorktreeManager(paths), paths


async def _ensure(manager: GitWorktreeManager, repo: Path, project_id: str, task_id: str) -> Path:
    await manager.ensure(
        WorktreeEnsureData(
            task_id=task_id,
            project_id=project_id,
            repo_path=str(repo),
            branch=f"coagentia/task-{task_id}",
        )
    )
    return manager.paths.worktree_path(project_id, task_id)


# ---------------------------------------------------------------- fs.tree：根视图


def test_fs_root_win32_lists_present_drives(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_mod.sys, "platform", "win32")
    present = {"C:\\", "D:\\"}
    monkeypatch.setattr(Path, "exists", lambda self: str(self) in present)
    entries = _fs_root_entries()
    assert [e.path for e in entries] == ["C:\\", "D:\\"]
    assert [e.name for e in entries] == ["C:\\", "D:\\"]
    assert all(not e.has_git and not e.denied for e in entries)


def test_fs_root_posix_single_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_mod.sys, "platform", "linux")
    entries = _fs_root_entries()
    assert len(entries) == 1
    assert entries[0].name == "/" and entries[0].path == "/"
    assert not entries[0].has_git and not entries[0].denied


# ---------------------------------------------------------------- fs.tree：子层列目录


def test_fs_dir_lists_only_dirs_with_has_git(tmp_path: Path) -> None:
    base = tmp_path / "base"
    base.mkdir()
    (base / "file.txt").write_text("x", encoding="utf-8")  # 文件：不列
    (base / "plain").mkdir()  # 普通目录：has_git=False
    repo_dir = base / "repo"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()  # 目录 .git
    wt_dir = base / "worktree"
    wt_dir.mkdir()
    # worktree 的 .git 是文件（不是目录）也算命中。
    (wt_dir / ".git").write_text("gitdir: /somewhere\n", encoding="utf-8")

    entries, truncated = _fs_dir_entries(base)
    assert not truncated
    by_name = {e.name: e for e in entries}
    assert set(by_name) == {"plain", "repo", "worktree"}  # 文件被跳过
    assert [e.name for e in entries] == ["plain", "repo", "worktree"]  # 按名排序
    assert by_name["plain"].has_git is False
    assert by_name["repo"].has_git is True
    assert by_name["worktree"].has_git is True  # 文件形式的 .git 也算
    assert all(not e.denied for e in entries)
    assert all(Path(e.path).name == e.name for e in entries)  # path 为绝对子路径


def test_fs_dir_truncates_over_limit(tmp_path: Path) -> None:
    base = tmp_path / "many"
    base.mkdir()
    for i in range(client_mod._FS_TREE_MAX + 1):
        (base / f"d{i:04d}").mkdir()
    entries, truncated = _fs_dir_entries(base)
    assert truncated is True
    assert len(entries) == client_mod._FS_TREE_MAX


def test_fs_dir_exactly_at_limit_not_truncated(tmp_path: Path) -> None:
    base = tmp_path / "edge"
    base.mkdir()
    for i in range(client_mod._FS_TREE_MAX):
        (base / f"d{i:04d}").mkdir()
    entries, truncated = _fs_dir_entries(base)
    assert truncated is False
    assert len(entries) == client_mod._FS_TREE_MAX


def test_fs_dir_unreadable_layer_is_empty_not_crash(tmp_path: Path) -> None:
    missing = tmp_path / "nope"  # 不存在 → os.scandir 抛 OSError → 空层，不炸
    entries, truncated = _fs_dir_entries(missing)
    assert entries == [] and truncated is False


class _FakeEntry:
    """伪 os.DirEntry：is_dir 可被指定抛 OSError 以覆盖 denied 逐条降级。"""

    def __init__(
        self, name: str, path: str, *, raise_is_dir: bool = False, is_dir: bool = True
    ) -> None:
        self.name = name
        self.path = path
        self._raise = raise_is_dir
        self._is_dir = is_dir

    def is_dir(self) -> bool:
        if self._raise:
            raise PermissionError("拒绝访问")
        return self._is_dir


def test_fs_scan_entry_denied_on_probe_error() -> None:
    entry = _fs_scan_entry(_FakeEntry("locked", "C:\\locked", raise_is_dir=True))  # type: ignore[arg-type]
    assert entry is not None
    assert entry.name == "locked" and entry.denied is True and entry.has_git is False


def test_fs_scan_entry_skips_files() -> None:
    assert _fs_scan_entry(_FakeEntry("f", "C:\\f", is_dir=False)) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------- fs.tree：handle_query 端到端


@pytest.mark.asyncio
async def test_handle_query_fs_tree_reply(tmp_path: Path) -> None:
    base = tmp_path / "q"
    base.mkdir()
    (base / "child").mkdir()
    (base / "note.txt").write_text("x", encoding="utf-8")
    transport = RecordingTransport()
    client, _adapter, _ = make_client(tmp_path, transport=transport)
    frame = {
        "v": 1,
        "kind": "query",
        "frame_id": new_ulid(),
        "type": "fs.tree",
        "at": now_iso(),
        "data": {"path": str(base)},
    }
    await client.handle_query(frame)
    reply = transport.sent[-1]
    assert reply["kind"] == "reply" and reply["ref"] == frame["frame_id"]
    parsed = FsTreeReply.model_validate(reply["data"])
    assert [e.name for e in parsed.entries] == ["child"]
    assert parsed.truncated is False


# ---------------------------------------------------------------- worktree.scan


@pytest.mark.asyncio
async def test_scan_empty_worktrees_dir(tmp_path: Path) -> None:
    manager, _paths = _manager(tmp_path)
    reply = await manager.scan(WorktreeScanQuery())
    assert reply.entries == []


@pytest.mark.asyncio
async def test_scan_skips_non_ulid_dirs(tmp_path: Path) -> None:
    manager, paths = _manager(tmp_path)
    repo = _seed_repo(tmp_path)
    project_id = new_ulid()
    task_id = new_ulid()
    await _ensure(manager, repo, project_id, task_id)
    # 非 ULID 的 project 段、非 ULID 的 task 段：都应被跳过。
    (paths.worktrees_dir / "not-a-ulid").mkdir()
    (paths.worktrees_dir / "not-a-ulid" / task_id).mkdir(parents=True)
    (paths.worktrees_dir / project_id / "not-a-ulid-task").mkdir(parents=True)

    reply = await manager.scan(WorktreeScanQuery())
    assert [(e.project_id, e.task_id) for e in reply.entries] == [(project_id, task_id)]


@pytest.mark.asyncio
async def test_scan_parses_branch_dirty_ahead_behind(tmp_path: Path) -> None:
    manager, _paths = _manager(tmp_path)
    repo = _seed_repo(tmp_path)
    project_id = new_ulid()
    task_id = new_ulid()
    target = await _ensure(manager, repo, project_id, task_id)
    (target / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")  # 未提交改动

    reply = await manager.scan(WorktreeScanQuery())
    assert len(reply.entries) == 1
    entry = reply.entries[0]
    assert entry.project_id == project_id and entry.task_id == task_id
    assert entry.branch == f"coagentia/task-{task_id}"
    assert entry.head_commit is not None and len(entry.head_commit) == 40
    assert entry.dirty is True
    assert entry.ahead == 0 and entry.behind == 0  # 与主仓 HEAD 同点
    assert entry.error is None


@pytest.mark.asyncio
async def test_scan_ahead_behind_after_divergence(tmp_path: Path) -> None:
    manager, _paths = _manager(tmp_path)
    repo = _seed_repo(tmp_path)
    project_id = new_ulid()
    task_id = new_ulid()
    target = await _ensure(manager, repo, project_id, task_id)
    # 树内 +1 提交（领先），主仓 +1 提交（本树落后）。
    (target / "f.txt").write_text("t\n", encoding="utf-8")
    _git(target, "add", "--", "f.txt")
    _git(target, "commit", "-m", "tree ahead")
    (repo / "m.txt").write_text("m\n", encoding="utf-8")
    _git(repo, "add", "--", "m.txt")
    _git(repo, "commit", "-m", "main ahead")

    reply = await manager.scan(WorktreeScanQuery())
    entry = reply.entries[0]
    assert entry.ahead == 1 and entry.behind == 1
    assert entry.error is None


@pytest.mark.asyncio
async def test_scan_single_tree_failure_degrades(tmp_path: Path) -> None:
    """一个 ULID 目录不是 git 树 → 该条 error 降级，不炸整扫；同批合法树照常上报。"""
    manager, paths = _manager(tmp_path)
    repo = _seed_repo(tmp_path)
    good_project = new_ulid()
    good_task = new_ulid()
    await _ensure(manager, repo, good_project, good_task)
    # 造一棵 ULID 命名但非 git 的目录。
    bad_project = new_ulid()
    bad_task = new_ulid()
    (paths.worktrees_dir / bad_project / bad_task).mkdir(parents=True)

    reply = await manager.scan(WorktreeScanQuery())
    by_task = {e.task_id: e for e in reply.entries}
    assert set(by_task) == {good_task, bad_task}
    good = by_task[good_task]
    assert good.error is None and good.branch == f"coagentia/task-{good_task}"
    bad = by_task[bad_task]
    assert bad.error is not None  # git 失败逐条降级
    assert bad.branch is None and bad.head_commit is None and bad.dirty is False
    assert bad.ahead is None and bad.behind is None


@pytest.mark.asyncio
async def test_handle_query_worktree_scan_reply(tmp_path: Path) -> None:
    transport = RecordingTransport()
    client, _adapter, _ = make_client(tmp_path, transport=transport)
    repo = _seed_repo(tmp_path)
    project_id = new_ulid()
    task_id = new_ulid()
    await client.git.ensure(
        WorktreeEnsureData(
            task_id=task_id,
            project_id=project_id,
            repo_path=str(repo),
            branch=f"coagentia/task-{task_id}",
        )
    )
    frame = {
        "v": 1,
        "kind": "query",
        "frame_id": new_ulid(),
        "type": "worktree.scan",
        "at": now_iso(),
        "data": {},
    }
    await client.handle_query(frame)
    reply = transport.sent[-1]
    assert reply["kind"] == "reply" and reply["ref"] == frame["frame_id"]
    parsed = WorktreeScanReply.model_validate(reply["data"])
    assert [(e.project_id, e.task_id) for e in parsed.entries] == [(project_id, task_id)]


def test_main_worktree_head_parses_first_block() -> None:
    porcelain = (
        "worktree /repo/main\n"
        "HEAD 1111111111111111111111111111111111111111\n"
        "branch refs/heads/main\n"
        "\n"
        "worktree /repo/linked\n"
        "HEAD 2222222222222222222222222222222222222222\n"
        "branch refs/heads/coagentia/task-x\n"
    )
    assert _main_worktree_head(porcelain) == "1111111111111111111111111111111111111111"
    assert _main_worktree_head("") is None


# ---------------------------------------------------------------- 孤儿清理护栏


def test_assert_managed_target_rejects_outside_worktrees_dir(tmp_path: Path) -> None:
    manager, _paths = _manager(tmp_path)
    task_id = new_ulid()
    with pytest.raises(WorktreeSafetyError):
        manager._assert_managed_target((tmp_path / "elsewhere" / task_id), task_id)


@pytest.mark.asyncio
async def test_orphan_branch_cleanup_limited_to_coagentia_prefix(tmp_path: Path) -> None:
    manager, _paths = _manager(tmp_path)
    repo = _seed_repo(tmp_path)
    _git(repo, "branch", "coagentia/task-dead")
    _git(repo, "branch", "feature/keep")

    # 非 coagentia/ 前缀：护栏拦下，不删。
    await manager._cleanup_orphan_branch(repo, "feature/keep")
    assert "feature/keep" in _git(repo, "branch", "--list", "feature/keep")

    # coagentia/ 前缀：删除。
    await manager._cleanup_orphan_branch(repo, "coagentia/task-dead")
    assert _git(repo, "branch", "--list", "coagentia/task-dead").strip() == ""

    # 无主仓：静默跳过，不炸。
    await manager._cleanup_orphan_branch(None, "coagentia/task-dead")


@pytest.mark.asyncio
async def test_orphan_cleanup_missing_dir_is_idempotent_success(tmp_path: Path) -> None:
    manager, _paths = _manager(tmp_path)
    project_id = new_ulid()
    task_id = new_ulid()
    data = WorktreeCleanupData(task_id=task_id, project_id=project_id)

    op1 = await manager.cleanup(data)  # 目录从不存在 → 幂等成功（视为已清理）
    assert op1.status is not None
    assert op1.status.status == "cleaned" and op1.status.task_id == task_id

    op2 = await manager.cleanup(data)  # 再清理 → 仍成功；同进程缓存 → noop
    assert op2.status is not None and op2.status.status == "cleaned"
    assert op2.changed is False


@pytest.mark.asyncio
async def test_orphan_cleanup_removes_tree_and_branch(tmp_path: Path) -> None:
    """孤儿全链：新管理器（无 _known，模拟 daemon 重启/DB 无 task）按 (project_id, task_id) 自拼
    路径 → 物理移除 worktree + 删除 coagentia/ 死分支。"""
    builder, paths = _manager(tmp_path)
    repo = _seed_repo(tmp_path)
    project_id = new_ulid()
    task_id = new_ulid()
    target = await _ensure(builder, repo, project_id, task_id)
    assert target.is_dir()
    branch = f"coagentia/task-{task_id}"
    assert branch in _git(repo, "branch", "--list", branch)

    # 独立管理器共享同一 DataPaths：无缓存，走 project_id 自拼 + 反查主仓路径。
    orphan_mgr = GitWorktreeManager(paths)
    op = await orphan_mgr.cleanup(WorktreeCleanupData(task_id=task_id, project_id=project_id))
    assert op.status is not None and op.status.status == "cleaned"
    assert not target.exists()  # 物理树移除
    assert _git(repo, "branch", "--list", branch).strip() == ""  # 死分支删除


@pytest.mark.asyncio
async def test_m6_cleanup_without_project_id_keeps_branch(tmp_path: Path) -> None:
    """既有 M6 清理（不带 project_id）行为不变：移除 worktree 但**保留分支**。"""
    manager, _paths = _manager(tmp_path)
    repo = _seed_repo(tmp_path)
    project_id = new_ulid()
    task_id = new_ulid()
    target = await _ensure(manager, repo, project_id, task_id)
    branch = f"coagentia/task-{task_id}"

    op = await manager.cleanup(WorktreeCleanupData(task_id=task_id))
    assert op.status is not None and op.status.status == "cleaned"
    assert not target.exists()
    assert branch in _git(repo, "branch", "--list", branch)  # M6 不删分支
