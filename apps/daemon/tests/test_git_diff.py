"""M6 J4 Diff 查询：真实 Git 仓库形状、截断与 query/reply 验收。"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from coagentia_contracts.daemon import (
    DiffPayload,
    GitDiffQuery,
    WorktreeCleanupData,
    WorktreeEnsureData,
)
from coagentia_daemon.git import (
    DIFF_MAX_FILES,
    DIFF_MAX_PATCH_BYTES,
    GitCommandError,
    GitWorktreeManager,
)
from coagentia_daemon.paths import DataPaths
from coagentia_daemon.util import new_ulid, now_iso
from helpers import RecordingTransport, make_client


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "core.quotepath=false", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed ({result.returncode}): "
            f"{result.stdout}\n{result.stderr}"
        )
    return result.stdout


def _scratch_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "中文 Diff 项目"
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
    (repo / "修改.txt").write_text("before\n", encoding="utf-8")
    (repo / "delete.txt").write_text("delete me\n", encoding="utf-8")
    (repo / "rename = old.txt").write_text("same\n", encoding="utf-8")
    (repo / "binary.bin").write_bytes(b"\x00\x01old")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")
    return repo


async def _manager_with_tree(
    tmp_path: Path,
) -> tuple[GitWorktreeManager, Path, str, str, str]:
    repo = _scratch_repo(tmp_path)
    paths = DataPaths(tmp_path / "数据 根")
    paths.ensure_dirs()
    manager = GitWorktreeManager(paths)
    task_id = new_ulid()
    project_id = new_ulid()
    branch = f"coagentia/task-{task_id}"
    await manager.ensure(
        WorktreeEnsureData(
            task_id=task_id,
            project_id=project_id,
            repo_path=str(repo),
            branch=branch,
        )
    )
    return manager, repo, task_id, project_id, branch


@pytest.mark.asyncio
async def test_diff_reports_all_git_shapes_with_utf8_paths(tmp_path: Path) -> None:
    manager, repo, task_id, project_id, branch = await _manager_with_tree(tmp_path)
    tree = manager.paths.worktree_path(project_id, task_id)
    (tree / "修改.txt").write_text("after\n", encoding="utf-8")
    (tree / "delete.txt").unlink()
    (tree / "rename = old.txt").rename(tree / "rename = new.txt")
    (tree / "binary.bin").write_bytes(b"\x00\x01new")
    (tree / "新增 中文.txt").write_text("第一行\n第二行\n", encoding="utf-8")
    _git(tree, "add", "-A")
    _git(tree, "commit", "-m", "中文 diff")

    payload = await manager.diff(
        GitDiffQuery(project_id=project_id, repo_path=str(repo), task_id=task_id)
    )

    assert payload.base_ref == "main"
    assert payload.head_ref == branch
    assert payload.files_truncated is False
    by_path = {item.path: item for item in payload.files}
    assert by_path["新增 中文.txt"].status == "added"
    assert by_path["修改.txt"].status == "modified"
    assert by_path["delete.txt"].status == "deleted"
    renamed = by_path["rename = new.txt"]
    assert renamed.status == "renamed"
    assert renamed.old_path == "rename = old.txt"
    assert "rename from rename = old.txt" in renamed.patch
    assert "rename to rename = new.txt" in renamed.patch
    binary = by_path["binary.bin"]
    assert (binary.additions, binary.deletions, binary.patch) == (0, 0, "")
    assert binary.patch_truncated is False
    assert "+after" in by_path["修改.txt"].patch
    assert "-before" in by_path["修改.txt"].patch
    assert payload.total_additions == sum(item.additions for item in payload.files)
    assert payload.total_deletions == sum(item.deletions for item in payload.files)


@pytest.mark.asyncio
async def test_diff_truncates_utf8_patch_and_file_list_but_totals_cover_all(
    tmp_path: Path,
) -> None:
    assert DIFF_MAX_FILES == 200
    assert DIFF_MAX_PATCH_BYTES == 64 * 1024
    manager, repo, task_id, project_id, _branch = await _manager_with_tree(tmp_path)
    tree = manager.paths.worktree_path(project_id, task_id)
    (tree / "a-large.txt").write_text(
        "\n".join(["中文内容"] * 80) + "\n", encoding="utf-8"
    )
    (tree / "z-extra.txt").write_text("one\ntwo\n", encoding="utf-8")
    _git(tree, "add", "-A")
    _git(tree, "commit", "-m", "large diff")

    payload = await manager.diff(
        GitDiffQuery(project_id=project_id, repo_path=str(repo), task_id=task_id),
        max_files=1,
        max_patch_bytes=95,
    )

    assert payload.files_truncated is True
    assert len(payload.files) == 1
    assert payload.files[0].patch_truncated is True
    assert len(payload.files[0].patch.encode("utf-8")) <= 95
    payload.files[0].patch.encode("utf-8").decode("utf-8")
    assert payload.total_additions > payload.files[0].additions


@pytest.mark.asyncio
async def test_diff_remains_readable_after_worktree_cleanup(tmp_path: Path) -> None:
    manager, repo, task_id, project_id, branch = await _manager_with_tree(tmp_path)
    tree = manager.paths.worktree_path(project_id, task_id)
    (tree / "delivered.txt").write_text("done\n", encoding="utf-8")
    _git(tree, "add", "-A")
    _git(tree, "commit", "-m", "delivered")

    await manager.cleanup(WorktreeCleanupData(task_id=task_id))
    payload = await manager.diff(
        GitDiffQuery(project_id=project_id, repo_path=str(repo), task_id=task_id)
    )

    assert not tree.exists()
    assert _git(repo, "show-ref", "--verify", f"refs/heads/{branch}")
    assert payload.head_ref == branch
    assert [item.path for item in payload.files] == ["delivered.txt"]


@pytest.mark.asyncio
async def test_diff_missing_task_branch_is_a_query_failure(tmp_path: Path) -> None:
    manager, repo, task_id, project_id, branch = await _manager_with_tree(tmp_path)
    await manager.cleanup(WorktreeCleanupData(task_id=task_id))
    _git(repo, "branch", "-D", branch)

    with pytest.raises(GitCommandError, match="Diff ref 不存在"):
        await manager.diff(
            GitDiffQuery(project_id=project_id, repo_path=str(repo), task_id=task_id)
        )


@pytest.mark.asyncio
async def test_git_diff_query_returns_contract_reply_and_errors_like_home_queries(
    tmp_path: Path,
) -> None:
    transport = RecordingTransport()
    client, _adapter, _ = make_client(tmp_path, transport=transport)
    task_id = new_ulid()
    project_id = new_ulid()
    expected = DiffPayload(
        base_ref="main",
        head_ref=f"coagentia/task-{task_id}",
        files=[],
        total_additions=0,
        total_deletions=0,
        files_truncated=False,
    )
    client.git.diff = AsyncMock(return_value=expected)  # type: ignore[method-assign]
    frame = {
        "v": 1,
        "kind": "query",
        "frame_id": new_ulid(),
        "type": "git.diff",
        "at": now_iso(),
        "data": {
            "project_id": project_id,
            "repo_path": str(tmp_path),
            "task_id": task_id,
            "base": None,
        },
    }

    await client.handle_query(frame)

    reply = transport.sent[-1]
    assert reply["kind"] == "reply" and reply["ref"] == frame["frame_id"]
    assert DiffPayload.model_validate(reply["data"]) == expected
    client.git.diff.assert_awaited_once_with(GitDiffQuery.model_validate(frame["data"]))

    client.git.diff = AsyncMock(side_effect=RuntimeError("diff failed"))  # type: ignore[method-assign]
    frame["frame_id"] = new_ulid()
    await client.handle_query(frame)
    assert transport.sent[-1]["data"] == {"error": "diff failed"}


def test_split_diff_sections_handles_tricky_shapes() -> None:
    """#8 单测：切分只认行首字面 `diff --git `；内容行前缀、纯 rename、binary 段均无歧义。"""
    from coagentia_daemon.git import _split_diff_sections

    raw = (
        "diff --git a/a.txt b/a.txt\n"
        "index 0000000..1111111 100644\n"
        "--- a/a.txt\n"
        "+++ b/a.txt\n"
        "@@ -1 +1,2 @@\n"
        " keep\n"
        "+diff --git a/fake b/fake\n"  # 内容行：以 '+' 起头，不得开新段
        "diff --git a/old name.txt b/new name.txt\n"
        "similarity index 100%\n"
        "rename from old name.txt\n"
        "rename to new name.txt\n"
        "diff --git a/bin.bin b/bin.bin\n"
        "index 0000000..1111111 100644\n"
        "Binary files a/bin.bin and b/bin.bin differ\n"
    )
    sections = _split_diff_sections(raw)
    assert len(sections) == 3
    assert sections[0].endswith("+diff --git a/fake b/fake\n")
    assert sections[1].startswith("diff --git a/old name.txt b/new name.txt\n")
    assert "rename to new name.txt" in sections[1]
    assert sections[2].endswith("differ\n")
    assert "".join(sections) == raw  # keepends 无损切分
    assert _split_diff_sections("") == []


@pytest.mark.asyncio
async def test_diff_patch_content_containing_diff_header_does_not_bleed(
    tmp_path: Path,
) -> None:
    """#8 端到端：文件内容行恰为 `diff --git ...` 时，切分不得把相邻文件的 patch 串段。"""
    manager, repo, task_id, project_id, _branch = await _manager_with_tree(tmp_path)
    tree = manager.paths.worktree_path(project_id, task_id)
    (tree / "aa.txt").write_text("diff --git a/x b/x\nnormal\n", encoding="utf-8")
    (tree / "zz.txt").write_text("tail\n", encoding="utf-8")
    _git(tree, "add", "-A")
    _git(tree, "commit", "-m", "tricky content")

    payload = await manager.diff(
        GitDiffQuery(project_id=project_id, repo_path=str(repo), task_id=task_id)
    )

    by_path = {item.path: item for item in payload.files}
    assert "+diff --git a/x b/x" in by_path["aa.txt"].patch
    assert "tail" not in by_path["aa.txt"].patch
    assert "+tail" in by_path["zz.txt"].patch
    assert "diff --git a/x b/x" not in by_path["zz.txt"].patch


@pytest.mark.asyncio
async def test_diff_spawns_constant_process_count_regardless_of_file_count(
    tmp_path: Path,
) -> None:
    """#8 核心回归：diff() 的 git 子进程数与变更文件数无关（旧实现逐文件 spawn ≤200 进程）。"""
    manager, repo, task_id, project_id, _branch = await _manager_with_tree(tmp_path)
    tree = manager.paths.worktree_path(project_id, task_id)
    (tree / "one.txt").write_text("1\n", encoding="utf-8")
    _git(tree, "add", "-A")
    _git(tree, "commit", "-m", "one file")

    calls: list[tuple] = []
    original = manager._git

    async def counting(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append(args)
        return await original(*args, **kwargs)

    manager._git = counting  # type: ignore[method-assign]
    query = GitDiffQuery(project_id=project_id, repo_path=str(repo), task_id=task_id)
    await manager.diff(query)
    single_file_count = len(calls)

    for i in range(7):
        (tree / f"more-{i}.txt").write_text(f"{i}\n", encoding="utf-8")
    _git(tree, "add", "-A")
    _git(tree, "commit", "-m", "seven more files")
    calls.clear()
    await manager.diff(query)
    assert len(calls) == single_file_count  # 子进程数恒定
