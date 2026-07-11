"""M6 J3 worktree 执行面：用真实 scratch git 仓库锁定生命周期语义。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from coagentia_contracts.daemon import (
    WorktreeCleanupData,
    WorktreeEnsureData,
    WorktreeMergeData,
)
from coagentia_daemon.git import GitCommandError, GitWorktreeManager
from coagentia_daemon.paths import DataPaths
from coagentia_daemon.util import new_ulid


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-c", "core.quotepath=false", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed ({result.returncode}): {result.stdout}\n{result.stderr}"
        )
    return result


def _scratch_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "中文 项目"
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
    (repo / "conflict.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "--", "conflict.txt")
    _git(repo, "commit", "-m", "种子提交")
    return repo


def _manager(tmp_path: Path) -> tuple[GitWorktreeManager, DataPaths]:
    paths = DataPaths(tmp_path / "数据 根")
    paths.ensure_dirs()
    return GitWorktreeManager(paths), paths


def _ensure_data(repo: Path, *, task_id: str, project_id: str) -> WorktreeEnsureData:
    return WorktreeEnsureData(
        task_id=task_id,
        project_id=project_id,
        repo_path=str(repo),
        branch=f"coagentia/task-{task_id}",
    )


@pytest.mark.asyncio
async def test_ensure_is_naturally_idempotent_in_chinese_paths(tmp_path: Path) -> None:
    repo = _scratch_repo(tmp_path)
    manager, paths = _manager(tmp_path)
    task_id = new_ulid()
    project_id = new_ulid()
    data = _ensure_data(repo, task_id=task_id, project_id=project_id)

    first = await manager.ensure(data)
    # 新 manager 模拟 daemon 重启：幂等不能依赖进程内 cache。
    second = await GitWorktreeManager(paths).ensure(data)

    target = paths.worktree_path(project_id, task_id).resolve()
    assert first.changed is True
    assert second.changed is False
    assert first.status is not None
    assert first.status.status == "active"
    assert Path(first.status.path) == target
    assert first.status.branch == data.branch
    assert _git(target, "branch", "--show-current").stdout.strip() == data.branch
    listed = _git(repo, "worktree", "list", "--porcelain").stdout.replace("\\", "/")
    assert str(target).replace("\\", "/") in listed


@pytest.mark.asyncio
async def test_cleanup_recovers_repo_and_branch_from_existing_worktree(tmp_path: Path) -> None:
    repo = _scratch_repo(tmp_path)
    manager, paths = _manager(tmp_path)
    task_id = new_ulid()
    project_id = new_ulid()
    data = _ensure_data(repo, task_id=task_id, project_id=project_id)
    await manager.ensure(data)

    # cleanup 帧只有 task_id；daemon 重启后从固定目录与 .git 指向恢复，不读持久 registry。
    restarted = GitWorktreeManager(paths)
    result = await restarted.cleanup(WorktreeCleanupData(task_id=task_id))

    assert result.changed is True
    assert result.status is not None
    assert result.status.status == "cleaned"
    assert result.status.branch == data.branch
    assert result.status.path == str(paths.worktree_path(project_id, task_id).resolve())


@pytest.mark.asyncio
async def test_cleanup_removes_stale_gitfile_residual_after_daemon_restart(tmp_path: Path) -> None:
    repo = _scratch_repo(tmp_path)
    manager, paths = _manager(tmp_path)
    task_id = new_ulid()
    project_id = new_ulid()
    data = _ensure_data(repo, task_id=task_id, project_id=project_id)
    await manager.ensure(data)
    target = paths.worktree_path(project_id, task_id).resolve()
    stale_gitfile = (target / ".git").read_text(encoding="utf-8")

    # 模拟 Windows remove 半完成态：登记已消失，物理目录（含失效 .git 指针）仍残留。
    _git(repo, "worktree", "remove", "--force", str(target))
    target.mkdir(parents=True)
    (target / ".git").write_text(stale_gitfile, encoding="utf-8")
    (target / "occupied-leftover.txt").write_text("残留", encoding="utf-8")

    restarted = GitWorktreeManager(paths)
    result = await restarted.cleanup(WorktreeCleanupData(task_id=task_id))

    assert result.changed is True
    assert result.status is not None
    assert result.status.status == "cleaned"
    assert result.status.branch == data.branch
    assert result.status.path == str(target)
    assert not target.exists()


@pytest.mark.asyncio
async def test_cleanup_handles_deregistered_residual_and_repeats_noop(tmp_path: Path) -> None:
    repo = _scratch_repo(tmp_path)
    manager, paths = _manager(tmp_path)
    task_id = new_ulid()
    project_id = new_ulid()
    data = _ensure_data(repo, task_id=task_id, project_id=project_id)
    await manager.ensure(data)
    target = paths.worktree_path(project_id, task_id).resolve()

    # 复现校准得到的半完成态：Git 登记已消失，但物理目录残留。
    _git(repo, "worktree", "remove", "--force", str(target))
    target.mkdir(parents=True)
    (target / "occupied-leftover.txt").write_text("残留", encoding="utf-8")

    first = await manager.cleanup(WorktreeCleanupData(task_id=task_id))
    second = await manager.cleanup(WorktreeCleanupData(task_id=task_id))

    assert first.changed is True
    assert second.changed is False
    assert first.status is not None and second.status is not None
    assert first.status.status == second.status.status == "cleaned"
    assert first.status.branch == second.status.branch == data.branch
    assert first.status.path == second.status.path == str(target)
    assert not target.exists()
    assert str(target).replace("\\", "/") not in _git(
        repo, "worktree", "list", "--porcelain"
    ).stdout.replace("\\", "/")


@pytest.mark.asyncio
async def test_cleanup_does_not_override_explicit_worktree_lock(tmp_path: Path) -> None:
    repo = _scratch_repo(tmp_path)
    manager, paths = _manager(tmp_path)
    task_id = new_ulid()
    project_id = new_ulid()
    data = _ensure_data(repo, task_id=task_id, project_id=project_id)
    await manager.ensure(data)
    target = paths.worktree_path(project_id, task_id).resolve()
    _git(repo, "worktree", "lock", "--reason", "J3 test", str(target))

    with pytest.raises(GitCommandError):
        await manager.cleanup(WorktreeCleanupData(task_id=task_id))

    assert target.is_dir()
    listed = _git(repo, "worktree", "list", "--porcelain").stdout.replace("\\", "/")
    assert str(target).replace("\\", "/") in listed
    _git(repo, "worktree", "unlock", str(target))


@pytest.mark.asyncio
async def test_merge_creates_no_ff_commit_and_is_idempotent(tmp_path: Path) -> None:
    repo = _scratch_repo(tmp_path)
    manager, paths = _manager(tmp_path)
    task_id = new_ulid()
    project_id = new_ulid()
    ensure = _ensure_data(repo, task_id=task_id, project_id=project_id)
    await manager.ensure(ensure)
    target = paths.worktree_path(project_id, task_id).resolve()
    (target / "中文交付.txt").write_text("done\n", encoding="utf-8")
    _git(target, "add", "--", "中文交付.txt")
    _git(target, "commit", "-m", "任务中文交付")
    merge = WorktreeMergeData(
        task_id=task_id,
        project_id=project_id,
        repo_path=str(repo),
        branch=ensure.branch,
        message=f"Merge task {task_id}",
    )

    first = await manager.merge(merge)
    second = await manager.merge(merge)

    assert first.changed is True
    assert second.changed is False
    assert first.status is not None and second.status is not None
    assert first.status.status == second.status.status == "merged"
    assert first.status.merge_commit == second.status.merge_commit
    parents = _git(repo, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()
    assert len(parents) == 3  # commit + 两个 parent，证明不是 fast-forward。
    assert first.status.merge_commit == parents[0]
    assert (repo / "中文交付.txt").read_text(encoding="utf-8") == "done\n"


@pytest.mark.asyncio
async def test_merge_conflict_collects_files_before_abort_and_restores_main(tmp_path: Path) -> None:
    repo = _scratch_repo(tmp_path)
    manager, paths = _manager(tmp_path)
    task_id = new_ulid()
    project_id = new_ulid()
    ensure = _ensure_data(repo, task_id=task_id, project_id=project_id)
    await manager.ensure(ensure)
    target = paths.worktree_path(project_id, task_id).resolve()

    (target / "conflict.txt").write_text("branch 中文\n", encoding="utf-8")
    _git(target, "add", "--", "conflict.txt")
    _git(target, "commit", "-m", "任务侧冲突")
    (repo / "conflict.txt").write_text("main 中文\n", encoding="utf-8")
    _git(repo, "add", "--", "conflict.txt")
    _git(repo, "commit", "-m", "主干侧冲突")
    before_head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    before_status = _git(repo, "status", "--porcelain=v1", "-z").stdout

    result = await manager.merge(
        WorktreeMergeData(
            task_id=task_id,
            project_id=project_id,
            repo_path=str(repo),
            branch=ensure.branch,
            message=f"Merge task {task_id}",
        )
    )

    assert result.changed is True
    assert result.status is not None
    assert result.status.status == "conflicted"
    assert result.status.conflict_files == ["conflict.txt"]
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == before_head
    assert _git(repo, "status", "--porcelain=v1", "-z").stdout == before_status
    assert _git(repo, "rev-parse", "-q", "--verify", "MERGE_HEAD", check=False).returncode != 0
    assert (repo / "conflict.txt").read_text(encoding="utf-8") == "main 中文\n"


@pytest.mark.asyncio
async def test_merge_recovers_conflict_left_by_crashed_daemon(tmp_path: Path) -> None:
    repo = _scratch_repo(tmp_path)
    manager, paths = _manager(tmp_path)
    task_id = new_ulid()
    project_id = new_ulid()
    ensure = _ensure_data(repo, task_id=task_id, project_id=project_id)
    await manager.ensure(ensure)
    target = paths.worktree_path(project_id, task_id).resolve()
    (target / "conflict.txt").write_text("branch crash\n", encoding="utf-8")
    _git(target, "add", "--", "conflict.txt")
    _git(target, "commit", "-m", "branch before crash")
    (repo / "conflict.txt").write_text("main crash\n", encoding="utf-8")
    _git(repo, "add", "--", "conflict.txt")
    _git(repo, "commit", "-m", "main before crash")
    before_head = _git(repo, "rev-parse", "HEAD").stdout.strip()

    crashed = _git(
        repo,
        "merge",
        "--no-ff",
        "-m",
        "crashed merge",
        "--",
        ensure.branch,
        check=False,
    )
    assert crashed.returncode == 1
    assert _git(repo, "rev-parse", "-q", "--verify", "MERGE_HEAD").returncode == 0

    restarted = GitWorktreeManager(paths)
    result = await restarted.merge(
        WorktreeMergeData(
            task_id=task_id,
            project_id=project_id,
            repo_path=str(repo),
            branch=ensure.branch,
            message=f"Merge task {task_id}",
        )
    )

    assert result.changed is True
    assert result.status is not None
    assert result.status.status == "conflicted"
    assert result.status.conflict_files == ["conflict.txt"]
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == before_head
    assert _git(repo, "rev-parse", "-q", "--verify", "MERGE_HEAD", check=False).returncode != 0
    assert _git(repo, "status", "--porcelain=v1").stdout == ""
