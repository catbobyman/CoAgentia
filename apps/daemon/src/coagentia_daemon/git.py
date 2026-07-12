"""M6 交付链的 Git 执行底座（契约 D §5.3、B §12.6/§12.8）。

所有命令都用 argv 直启，不经 shell；stdout/stderr 统一显式 UTF-8 解码。worktree 生命周期
只以 Git 登记与固定数据根为事实，不新增本地持久 registry。进程内缓存仅用于重复 cleanup 时
重报已知 branch/path，daemon 重启后的恢复仍从真实 worktree 反查。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import stat
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from coagentia_contracts.daemon import (
    DiffFile,
    DiffPayload,
    GitDiffQuery,
    WorktreeCleanupData,
    WorktreeEnsureData,
    WorktreeMergeData,
    WorktreeStatusData,
)

from coagentia_daemon.paths import DataPaths

GIT_TIMEOUT_SEC = 60.0
DIFF_MAX_FILES = 200
DIFF_MAX_PATCH_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class GitResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class GitCommandError(RuntimeError):
    """Git 非预期失败；原始 stdout/stderr 保留给 daemon 诊断。"""

    def __init__(self, result: GitResult, message: str | None = None) -> None:
        self.result = result
        detail = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
        prefix = message or f"git 退出 {result.returncode}"
        super().__init__(f"{prefix}: {detail}" if detail else prefix)


class WorktreeSafetyError(RuntimeError):
    """拒绝越过固定 worktree 根或覆盖未知内容。"""


ProcessRunner = Callable[[Sequence[str], float], Awaitable[GitResult]]


async def _await_uninterruptibly(awaitable: Awaitable[None]) -> None:
    """完成 Git 恢复临界区；调用方随后重抛最初的 CancelledError。"""
    cleanup = asyncio.ensure_future(awaitable)
    current = asyncio.current_task()
    if current is not None:
        while current.cancelling():
            current.uncancel()
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            if current is not None:
                while current.cancelling():
                    current.uncancel()
    cleanup.result()


async def _terminate_and_drain(
    proc: asyncio.subprocess.Process,
    communication: asyncio.Task[tuple[bytes, bytes]],
) -> None:
    await _kill_process_tree(proc)
    out_raw, err_raw = await communication
    out_raw.decode("utf-8", errors="replace")
    err_raw.decode("utf-8", errors="replace")


async def run_process(argv: Sequence[str], timeout_sec: float = GIT_TIMEOUT_SEC) -> GitResult:
    """直启短命令；超时只终止本次启动的进程树。"""
    args = tuple(str(arg) for arg in argv)
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["LC_ALL"] = "C.UTF-8"
    env["LANG"] = "C.UTF-8"
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    communication = asyncio.create_task(proc.communicate())
    try:
        out_raw, err_raw = await asyncio.wait_for(
            asyncio.shield(communication), timeout=timeout_sec
        )
    except TimeoutError as exc:
        timeout_error = TimeoutError(f"子进程超时（{timeout_sec:g}s）：{args[0]}")
        try:
            await _terminate_and_drain(proc, communication)
        except Exception as cleanup_error:
            timeout_error.add_note(f"子进程超时清理失败：{cleanup_error!r}")
        raise timeout_error from exc
    except asyncio.CancelledError as cancelled:
        try:
            await _await_uninterruptibly(_terminate_and_drain(proc, communication))
        except Exception as cleanup_error:
            cancelled.add_note(f"子进程取消清理失败：{cleanup_error!r}")
        raise
    return GitResult(
        argv=args,
        returncode=proc.returncode or 0,
        stdout=out_raw.decode("utf-8", errors="replace"),
        stderr=err_raw.decode("utf-8", errors="replace"),
    )


async def _kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    killed_tree = False
    if sys.platform == "win32" and proc.pid:
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/F",
                "/T",
                "/PID",
                str(proc.pid),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out_raw, err_raw = await killer.communicate()
            # taskkill 同属子进程，显式 UTF-8 解码；文本只用于完成排空。
            out_raw.decode("utf-8", errors="replace")
            err_raw.decode("utf-8", errors="replace")
            killed_tree = killer.returncode == 0
        except Exception:
            killed_tree = False
    if not killed_tree:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(proc.wait(), timeout=3.0)


async def run_git(
    repo_path: str | Path,
    args: Sequence[str],
    *,
    timeout_sec: float = GIT_TIMEOUT_SEC,
    runner: ProcessRunner = run_process,
    git_bin: str = "git",
) -> GitResult:
    """J3/J4/J5 共用 Git 入口；NUL 输出保留在解码后的 str 中供机器解析。"""
    argv = (
        git_bin,
        "-c",
        "core.quotepath=false",
        "-c",
        "color.ui=false",
        "-C",
        str(repo_path),
        *args,
    )
    return await runner(argv, timeout_sec)


@dataclass(frozen=True, slots=True)
class WorktreeOperation:
    changed: bool
    status: WorktreeStatusData | None


@dataclass(frozen=True, slots=True)
class _WorktreeEntry:
    path: Path
    branch: str | None
    locked: bool = False


@dataclass(frozen=True, slots=True)
class _DiffMeta:
    path: str
    status: Literal["added", "modified", "deleted", "renamed"]
    old_path: str | None = None


@dataclass(frozen=True, slots=True)
class _DiffCount:
    additions: int
    deletions: int
    binary: bool


class GitWorktreeManager:
    """worktree.ensure/cleanup/merge 的自然键幂等执行器。"""

    def __init__(
        self,
        paths: DataPaths,
        *,
        runner: ProcessRunner = run_process,
        git_bin: str = "git",
        timeout_sec: float = GIT_TIMEOUT_SEC,
    ) -> None:
        self.paths = paths
        self._runner = runner
        self._git_bin = git_bin
        self._timeout_sec = timeout_sec
        # 仅优化同进程重复帧；不落盘、不作为恢复事实源。
        self._known: dict[str, WorktreeStatusData] = {}
        self._known_repos: dict[str, Path] = {}

    async def ensure(self, data: WorktreeEnsureData) -> WorktreeOperation:
        repo = await self._validate_repo(data.repo_path)
        await self._validate_branch(repo, data.branch)
        target = self.paths.worktree_path(data.project_id, data.task_id).resolve()
        self._assert_managed_target(target, data.task_id)
        target.parent.mkdir(parents=True, exist_ok=True)

        entries = await self._worktree_entries(repo)
        registered = _entry_at(entries, target)
        if registered is not None and not _lexists(target):
            await self._git(repo, "worktree", "prune")
            entries = await self._worktree_entries(repo)
            registered = _entry_at(entries, target)
        if registered is not None:
            actual_branch = _short_branch(registered.branch)
            if actual_branch != data.branch:
                raise WorktreeSafetyError(
                    f"目标路径已登记为分支 {actual_branch!r}，不是 {data.branch!r}"
                )
            known = self._known.get(data.task_id)
            status = (
                known
                if known is not None
                and known.status != "cleaned"
                and known.path == str(target)
                and known.branch == data.branch
                else _status(data.task_id, "active", data.branch, target)
            )
            self._remember(data.task_id, repo, status)
            return WorktreeOperation(False, status)

        expected_ref = f"refs/heads/{data.branch}"
        other = next((entry for entry in entries if entry.branch == expected_ref), None)
        if other is not None:
            raise WorktreeSafetyError(f"分支已在另一 worktree 使用：{other.path}")
        if _lexists(target):
            if not target.is_dir() or any(target.iterdir()):
                raise WorktreeSafetyError(f"未登记的目标路径非空，拒绝覆盖：{target}")

        branch_exists = await self._branch_exists(repo, data.branch)
        if branch_exists:
            result = await self._git(repo, "worktree", "add", str(target), data.branch, check=False)
        else:
            result = await self._git(
                repo, "worktree", "add", "-b", data.branch, str(target), check=False
            )
        if result.returncode != 0:
            raise GitCommandError(result, "创建 worktree 失败")

        registered = _entry_at(await self._worktree_entries(repo), target)
        if registered is None or _short_branch(registered.branch) != data.branch:
            raise WorktreeSafetyError("git worktree add 成功后未找到预期登记")
        status = _status(data.task_id, "active", data.branch, target)
        self._remember(data.task_id, repo, status)
        return WorktreeOperation(True, status)

    async def cleanup(self, data: WorktreeCleanupData) -> WorktreeOperation:
        known = self._known.get(data.task_id)
        repo = self._known_repos.get(data.task_id)
        if known is not None:
            target = Path(known.path)
            branch = known.branch
        else:
            candidates = self._task_candidates(data.task_id)
            if not candidates:
                # 帧只有 task_id；物理树和登记都已消失时没有可恢复的 project/path，noop 即目标态。
                return WorktreeOperation(False, None)
            if len(candidates) != 1:
                raise WorktreeSafetyError(f"同 task_id 出现多个 worktree 路径：{candidates}")
            target = candidates[0]
            branch = f"coagentia/task-{data.task_id}"
            if (target / ".git").exists():
                try:
                    repo, recovered_branch = await self._recover_from_worktree(target)
                    branch = recovered_branch
                except (GitCommandError, WorktreeSafetyError):
                    # remove 可能已摘掉主仓登记但留下失效 .git 指针。固定两级受管路径足以
                    # 允许物理清理；此时不碰未知主仓、不尝试删除任何锁。
                    repo = None

        target = target.resolve()
        self._assert_managed_target(target, data.task_id)
        existed = _lexists(target)
        registered = False
        remove_result: GitResult | None = None
        if repo is not None and repo.is_dir():
            entries = await self._worktree_entries(repo)
            registered = _entry_at(entries, target) is not None
            if registered:
                remove_result = await self._git(
                    repo, "worktree", "remove", "--force", str(target), check=False
                )
                still_registered = _entry_at(await self._worktree_entries(repo), target) is not None
                if still_registered:
                    raise GitCommandError(remove_result, "清理 worktree 失败（登记仍存在）")

        if _lexists(target):
            _remove_managed_tree(target)
        if repo is not None and repo.is_dir():
            await self._git(repo, "worktree", "prune")
            if _entry_at(await self._worktree_entries(repo), target) is not None:
                if remove_result is not None:
                    raise GitCommandError(remove_result, "prune 后 worktree 登记仍存在")
                raise WorktreeSafetyError("prune 后 worktree 登记仍存在")
        if _lexists(target):
            raise WorktreeSafetyError(f"worktree 物理目录仍存在：{target}")

        status = _status(
            data.task_id,
            "cleaned",
            branch,
            target,
            merge_commit=known.merge_commit if known is not None else None,
        )
        changed = registered or existed or known is None or known.status != "cleaned"
        self._remember(data.task_id, repo, status)
        return WorktreeOperation(changed, status)

    async def merge(self, data: WorktreeMergeData) -> WorktreeOperation:
        repo = await self._validate_repo(data.repo_path)
        await self._validate_branch(repo, data.branch)
        target = self.paths.worktree_path(data.project_id, data.task_id).resolve()
        self._assert_managed_target(target, data.task_id)
        if not await self._branch_exists(repo, data.branch):
            raise WorktreeSafetyError(f"待合并分支不存在：{data.branch}")

        branch_head = (await self._git(repo, "rev-parse", data.branch)).stdout.strip()
        main_head = (await self._git(repo, "rev-parse", "HEAD")).stdout.strip()
        unfinished = await self._git(repo, "rev-parse", "-q", "--verify", "MERGE_HEAD", check=False)
        if unfinished.returncode == 0:
            if branch_head not in unfinished.stdout.split():
                raise WorktreeSafetyError("主工作区存在不属于本任务的未完成 merge，拒绝 abort")
            conflicts_result = await self._git(repo, "diff", "--name-only", "--diff-filter=U", "-z")
            conflict_files = [name for name in conflicts_result.stdout.split("\0") if name]
            abort = await self._git(repo, "merge", "--abort", check=False)
            if abort.returncode != 0:
                raise GitCommandError(abort, "恢复上次未完成 merge 时 abort 失败")
            if (await self._git(repo, "rev-parse", "HEAD")).stdout.strip() != main_head:
                raise WorktreeSafetyError("恢复上次未完成 merge 后主干 HEAD 改变")
            if conflict_files:
                status = _status(
                    data.task_id,
                    "conflicted",
                    data.branch,
                    target,
                    conflict_files=conflict_files,
                )
                self._remember(data.task_id, repo, status)
                return WorktreeOperation(True, status)
            # clean merge 在 commit 前崩溃：abort 回到前态后，同一次重放继续执行。

        ancestor = await self._git(
            repo, "merge-base", "--is-ancestor", data.branch, "HEAD", check=False
        )
        if ancestor.returncode == 0:
            merge_commit = await self._find_merge_commit(repo, branch_head)
            if merge_commit is None:
                raise WorktreeSafetyError("分支已在主干中，但找不到对应的 --no-ff merge commit")
            status = _status(
                data.task_id,
                "merged",
                data.branch,
                target,
                merge_commit=merge_commit,
            )
            self._remember(data.task_id, repo, status)
            return WorktreeOperation(False, status)
        if ancestor.returncode != 1:
            raise GitCommandError(ancestor, "判断分支合并状态失败")

        before_status = (
            await self._git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        ).stdout
        if before_status:
            raise WorktreeSafetyError("主工作区存在未提交更改，拒绝 merge")
        try:
            result = await self._git(
                repo,
                "merge",
                "--no-ff",
                "-m",
                data.message,
                "--",
                data.branch,
                check=False,
            )
        except asyncio.CancelledError as interrupted:
            try:
                await _await_uninterruptibly(
                    self._restore_cancelled_merge(repo, main_head, before_status)
                )
            except Exception as recovery_error:
                interrupted.add_note(f"merge 中断恢复失败：{recovery_error!r}")
            raise
        except TimeoutError as interrupted:
            try:
                await asyncio.shield(
                    self._restore_cancelled_merge(repo, main_head, before_status)
                )
            except Exception as recovery_error:
                interrupted.add_note(f"merge 超时恢复失败：{recovery_error!r}")
            raise
        if result.returncode == 0:
            merge_commit = (await self._git(repo, "rev-parse", "HEAD")).stdout.strip()
            parents = (
                await self._git(repo, "rev-list", "--parents", "-n", "1", merge_commit)
            ).stdout.split()
            if merge_commit == main_head or len(parents) < 3:
                raise WorktreeSafetyError("merge 未生成预期的 --no-ff 双亲提交")
            status = _status(
                data.task_id,
                "merged",
                data.branch,
                target,
                merge_commit=merge_commit,
            )
            self._remember(data.task_id, repo, status)
            return WorktreeOperation(True, status)

        conflicts_result = await self._git(repo, "diff", "--name-only", "--diff-filter=U", "-z")
        conflict_files = [name for name in conflicts_result.stdout.split("\0") if name]
        merge_head = await self._git(repo, "rev-parse", "-q", "--verify", "MERGE_HEAD", check=False)
        if not conflict_files:
            if merge_head.returncode == 0:
                abort = await self._git(repo, "merge", "--abort", check=False)
                if abort.returncode != 0:
                    raise GitCommandError(abort, "非冲突 merge 失败后的 abort 失败")
            raise GitCommandError(result, "git merge 失败（非内容冲突）")

        abort = await self._git(repo, "merge", "--abort", check=False)
        if abort.returncode != 0:
            raise GitCommandError(abort, "git merge --abort 失败")
        restored_head = (await self._git(repo, "rev-parse", "HEAD")).stdout.strip()
        restored_status = (
            await self._git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        ).stdout
        if restored_head != main_head or restored_status != before_status:
            raise WorktreeSafetyError("冲突 abort 后主干未恢复到合并前状态")
        status = _status(
            data.task_id,
            "conflicted",
            data.branch,
            target,
            conflict_files=conflict_files,
        )
        self._remember(data.task_id, repo, status)
        return WorktreeOperation(True, status)

    async def _restore_cancelled_merge(
        self, repo: Path, main_head: str, before_status: str
    ) -> None:
        merge_head = await self._git(
            repo, "rev-parse", "-q", "--verify", "MERGE_HEAD", check=False
        )
        if merge_head.returncode == 0:
            restored = await self._git(repo, "merge", "--abort", check=False)
        else:
            restored = await self._git(repo, "reset", "--hard", main_head, check=False)
        if restored.returncode != 0:
            raise GitCommandError(restored, "取消 merge 后恢复主工作区失败")
        restored_head = (await self._git(repo, "rev-parse", "HEAD")).stdout.strip()
        restored_status = (
            await self._git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        ).stdout
        if restored_head != main_head or restored_status != before_status:
            raise WorktreeSafetyError("取消 merge 后主工作区未恢复到合并前状态")

    async def diff(
        self,
        data: GitDiffQuery,
        *,
        max_files: int = DIFF_MAX_FILES,
        max_patch_bytes: int = DIFF_MAX_PATCH_BYTES,
    ) -> DiffPayload:
        """读取任务分支相对主干的逐文件 diff（契约 D §6）。"""
        if max_files < 0 or max_patch_bytes < 0:
            raise ValueError("Diff 截断上限不得为负数")
        repo = await self._validate_repo(data.repo_path)
        branch = f"coagentia/task-{data.task_id}"
        await self._validate_branch(repo, branch)
        target = self.paths.worktree_path(data.project_id, data.task_id).resolve()
        self._assert_managed_target(target, data.task_id)
        registered = _entry_at(await self._worktree_entries(repo), target)
        if registered is not None and _short_branch(registered.branch) != branch:
            raise WorktreeSafetyError("任务 worktree 登记分支与约定不一致")

        if data.base is None:
            base_commit = (await self._git(repo, "rev-parse", "HEAD")).stdout.strip()
            branch_name = (await self._git(repo, "branch", "--show-current")).stdout.strip()
            base_ref = branch_name or base_commit
        else:
            base_ref = data.base
            base_commit = await self._resolve_commit(repo, data.base)
        head_ref = branch
        head_commit = await self._resolve_commit(repo, f"refs/heads/{branch}")

        name_status = await self._git(
            repo,
            "diff",
            "--name-status",
            "-z",
            "--find-renames",
            "--no-ext-diff",
            base_commit,
            head_commit,
            "--",
        )
        numstat = await self._git(
            repo,
            "diff",
            "--numstat",
            "-z",
            "--find-renames",
            "--no-ext-diff",
            base_commit,
            head_commit,
            "--",
        )
        metadata = _parse_name_status_z(name_status.stdout)
        counts = _parse_numstat_z(numstat.stdout)
        total_additions = sum(item.additions for item in counts.values())
        total_deletions = sum(item.deletions for item in counts.values())

        # 一次全量 unified diff → 按 `diff --git ` 头切分逐文件（#8：子进程数塌缩为常数）。
        # name-status 与本次 patch 用同一 flag 集（--find-renames 等），diffcore 顺序一致，
        # 故 metadata 与 sections 按位对齐；段数不符则 fail-closed，绝不错配 patch。
        full_diff = await self._git(
            repo,
            "diff",
            "--no-color",
            "--no-ext-diff",
            "--find-renames",
            base_commit,
            head_commit,
            "--",
        )
        sections = _split_diff_sections(full_diff.stdout)
        if len(sections) != len(metadata):
            raise WorktreeSafetyError(
                f"Diff 段数与 name-status 不一致：{len(sections)} != {len(metadata)}"
            )

        files: list[DiffFile] = []
        for item, section in zip(metadata[:max_files], sections[:max_files], strict=False):
            key = (item.old_path, item.path)
            count = counts.get(key)
            if count is None:
                raise WorktreeSafetyError(f"Diff 元数据与 numstat 不一致：{item.path}")
            if count.binary:
                patch, patch_truncated = "", False
            else:
                patch, patch_truncated = _truncate_utf8(section, max_patch_bytes)
            files.append(
                DiffFile(
                    path=item.path,
                    status=item.status,
                    old_path=item.old_path,
                    additions=count.additions,
                    deletions=count.deletions,
                    patch=patch,
                    patch_truncated=patch_truncated,
                )
            )
        return DiffPayload(
            base_ref=base_ref,
            head_ref=head_ref,
            files=files,
            total_additions=total_additions,
            total_deletions=total_deletions,
            files_truncated=len(metadata) > max_files,
        )

    async def _git(
        self,
        repo: Path,
        *args: str,
        check: bool = True,
    ) -> GitResult:
        result = await run_git(
            repo,
            args,
            timeout_sec=self._timeout_sec,
            runner=self._runner,
            git_bin=self._git_bin,
        )
        if check and result.returncode != 0:
            raise GitCommandError(result)
        return result

    async def _validate_repo(self, repo_path: str) -> Path:
        repo = Path(repo_path).expanduser().resolve()
        if not repo.is_dir():
            raise WorktreeSafetyError(f"repo_path 不存在或不是目录：{repo}")
        inside = await self._git(repo, "rev-parse", "--is-inside-work-tree", check=False)
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            raise GitCommandError(inside, "repo_path 不是可用 git 工作区")
        return repo

    async def _validate_branch(self, repo: Path, branch: str) -> None:
        result = await self._git(repo, "check-ref-format", "--branch", branch, check=False)
        if result.returncode != 0:
            raise GitCommandError(result, f"非法分支名 {branch!r}")

    async def _branch_exists(self, repo: Path, branch: str) -> bool:
        result = await self._git(
            repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False
        )
        if result.returncode == 0:
            return True
        if result.returncode == 1:
            return False
        raise GitCommandError(result, "查询分支失败")

    async def _worktree_entries(self, repo: Path) -> list[_WorktreeEntry]:
        result = await self._git(repo, "worktree", "list", "--porcelain")
        return _parse_worktree_porcelain(result.stdout)

    async def _recover_from_worktree(self, target: Path) -> tuple[Path, str]:
        entries = await self._worktree_entries(target)
        if not entries:
            raise WorktreeSafetyError(f"无法从 worktree 反查主仓库：{target}")
        branch_result = await self._git(target, "branch", "--show-current")
        branch = branch_result.stdout.strip()
        if not branch:
            raise WorktreeSafetyError(f"worktree 处于 detached HEAD：{target}")
        return entries[0].path.resolve(), branch

    async def _find_merge_commit(self, repo: Path, branch_head: str) -> str | None:
        result = await self._git(
            repo, "rev-list", "--first-parent", "--parents", "--merges", "HEAD"
        )
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 3 and branch_head in parts[2:]:
                return parts[0]
        return None

    async def _resolve_commit(self, repo: Path, ref: str) -> str:
        result = await self._git(
            repo,
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"{ref}^{{commit}}",
            check=False,
        )
        if result.returncode != 0:
            raise GitCommandError(result, f"Diff ref 不存在：{ref}")
        return result.stdout.strip()

    def _task_candidates(self, task_id: str) -> list[Path]:
        if not self.paths.worktrees_dir.is_dir():
            return []
        candidates: list[Path] = []
        for project_dir in self.paths.worktrees_dir.iterdir():
            candidate = project_dir / task_id
            if _lexists(candidate):
                candidates.append(candidate)
        return candidates

    def _assert_managed_target(self, target: Path, task_id: str) -> None:
        root = self.paths.worktrees_dir.resolve()
        resolved = target.resolve()
        try:
            relative = resolved.relative_to(root)
        except ValueError as exc:
            raise WorktreeSafetyError(f"worktree 路径越出数据根：{target}") from exc
        if len(relative.parts) != 2 or relative.parts[-1] != task_id:
            raise WorktreeSafetyError(f"worktree 路径不符合 project/task 布局：{target}")

    def _remember(self, task_id: str, repo: Path | None, status: WorktreeStatusData) -> None:
        self._known[task_id] = status
        if repo is not None:
            self._known_repos[task_id] = repo


def _status(
    task_id: str,
    status: Literal["active", "merged", "conflicted", "cleaned"],
    branch: str,
    path: Path,
    *,
    merge_commit: str | None = None,
    conflict_files: list[str] | None = None,
) -> WorktreeStatusData:
    return WorktreeStatusData(
        task_id=task_id,
        status=status,
        branch=branch,
        path=str(path),
        merge_commit=merge_commit,
        conflict_files=conflict_files,
    )


def _parse_worktree_porcelain(raw: str) -> list[_WorktreeEntry]:
    entries: list[_WorktreeEntry] = []
    current: dict[str, str | bool] = {}
    for line in [*raw.splitlines(), ""]:
        if not line:
            path = current.get("worktree")
            if isinstance(path, str):
                branch = current.get("branch")
                entries.append(
                    _WorktreeEntry(
                        path=Path(path),
                        branch=branch if isinstance(branch, str) else None,
                        locked=bool(current.get("locked")),
                    )
                )
            current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value if value else True
    return entries


def _entry_at(entries: Sequence[_WorktreeEntry], target: Path) -> _WorktreeEntry | None:
    target_key = _path_key(target)
    return next((entry for entry in entries if _path_key(entry.path) == target_key), None)


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path.resolve())))


def _short_branch(branch_ref: str | None) -> str | None:
    prefix = "refs/heads/"
    if branch_ref is None:
        return None
    return branch_ref[len(prefix) :] if branch_ref.startswith(prefix) else branch_ref


def _parse_name_status_z(
    raw: str,
) -> list[_DiffMeta]:
    tokens = raw.split("\0")
    if tokens and tokens[-1] == "":
        tokens.pop()
    result: list[_DiffMeta] = []
    index = 0
    while index < len(tokens):
        code = tokens[index]
        index += 1
        if not code:
            raise WorktreeSafetyError("git diff --name-status 出现空状态")
        kind = code[0]
        if kind == "R":
            if index + 1 >= len(tokens):
                raise WorktreeSafetyError("git diff rename 记录不完整")
            old_path, path = tokens[index], tokens[index + 1]
            index += 2
            result.append(_DiffMeta(path=path, old_path=old_path, status="renamed"))
            continue
        if index >= len(tokens):
            raise WorktreeSafetyError("git diff name-status 记录不完整")
        path = tokens[index]
        index += 1
        status: Literal["added", "modified", "deleted", "renamed"]
        if kind == "A":
            status = "added"
        elif kind == "D":
            status = "deleted"
        elif kind in {"M", "T"}:
            status = "modified"
        else:
            raise WorktreeSafetyError(f"不支持的 git diff 状态：{code}")
        result.append(_DiffMeta(path=path, status=status))
    return result


def _parse_numstat_z(raw: str) -> dict[tuple[str | None, str], _DiffCount]:
    tokens = raw.split("\0")
    if tokens and tokens[-1] == "":
        tokens.pop()
    result: dict[tuple[str | None, str], _DiffCount] = {}
    index = 0
    while index < len(tokens):
        fields = tokens[index].split("\t", 2)
        index += 1
        if len(fields) != 3:
            raise WorktreeSafetyError("git diff --numstat 记录不完整")
        additions_raw, deletions_raw, path = fields
        old_path: str | None = None
        if path == "":
            if index + 1 >= len(tokens):
                raise WorktreeSafetyError("git diff rename numstat 记录不完整")
            old_path, path = tokens[index], tokens[index + 1]
            index += 2
        binary = additions_raw == "-" or deletions_raw == "-"
        try:
            additions = 0 if binary else int(additions_raw)
            deletions = 0 if binary else int(deletions_raw)
        except ValueError as exc:
            raise WorktreeSafetyError("git diff numstat 计数不是整数") from exc
        result[(old_path, path)] = _DiffCount(
            additions=additions,
            deletions=deletions,
            binary=binary,
        )
    return result


def _truncate_utf8(value: str, max_bytes: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True


def _split_diff_sections(raw: str) -> list[str]:
    """把全量 unified diff 按每个 `diff --git ` 头切成逐文件段（#8）。

    只有行首字面为 `diff --git `（无 diff 前缀字符）才开新段——真正的内容行恒以 ' '/'+'/'-' 起头，
    头行（index/---/+++/@@/rename*/Binary files/old mode…）均不以 `diff --git ` 起头，故对含空格/
    中文的路径也无歧义；keepends 保留精确字节，令下游 UTF-8 字节截断与断言与旧逐文件输出一致。
    """
    sections: list[str] = []
    current: list[str] | None = None
    for line in raw.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if current is not None:
                sections.append("".join(current))
            current = [line]
        elif current is not None:
            current.append(line)
    if current is not None:
        sections.append("".join(current))
    return sections


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _remove_managed_tree(path: Path) -> None:
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise WorktreeSafetyError(f"拒绝递归删除链接/junction：{path}")
    if not path.is_dir():
        raise WorktreeSafetyError(f"worktree 目标不是目录：{path}")

    def clear_readonly(func: Callable[..., object], name: str, _exc: BaseException) -> None:
        os.chmod(name, stat.S_IWRITE)
        func(name)

    shutil.rmtree(path, onexc=clear_readonly)


__all__ = [
    "DIFF_MAX_FILES",
    "DIFF_MAX_PATCH_BYTES",
    "GIT_TIMEOUT_SEC",
    "GitCommandError",
    "GitResult",
    "GitWorktreeManager",
    "ProcessRunner",
    "WorktreeOperation",
    "WorktreeSafetyError",
    "run_git",
    "run_process",
]
