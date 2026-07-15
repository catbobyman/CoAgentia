"""工作树管理台读面合账（PS-WT，设计 §5.2；方案 A：DB 骨架 + 实时对账 enrich）。

本模块唯一非平凡逻辑 = **server 侧合账**：把 worktrees DB 骨架与 daemon 实时磁盘扫描按
`(computer_id, project_id, task_id)` 三元组对齐，派生 ok/missing/orphan 三态。纯函数、无 IO，
路由预取所有依赖数据后调用，便于单元穷举合账矩阵四象限 × 整机在线/离线。

合账矩阵（设计 §5.2）：
| DB 行             | 磁盘条目 | 结果                                   |
|-------------------|---------|----------------------------------------|
| active/merged/conflicted | 有 | derived="ok" + live 字段              |
| active            | 无      | derived="missing"（丢失）              |
| merged/conflicted | 无      | derived="ok"（终态无树正常）           |
| cleaned           | 无      | derived="ok"（已清正常，前端折叠）     |
| 无 或 已 cleaned  | 有      | 追加孤儿行 derived="orphan"（id=None） |

某机离线/超时（scans 该机 status ≠ ok）→ 该机所有行 live=None、derived 保 DB 态（无从判定漂移）。
"""

from __future__ import annotations

from typing import Any

from coagentia_contracts import rest
from coagentia_contracts.enums import WorktreeStatus

# computer_id -> (status: "ok"|"offline"|"timeout", entries | None)
ScanOutcome = tuple[str, list[dict[str, Any]] | None]

_NON_CLEANED_STATUSES = {
    WorktreeStatus.ACTIVE.value,
    WorktreeStatus.MERGED.value,
    WorktreeStatus.CONFLICTED.value,
}


def _live_from_entry(entry: dict[str, Any]) -> rest.WorktreeLive:
    return rest.WorktreeLive(
        dirty=bool(entry.get("dirty")),
        ahead=entry.get("ahead"),
        behind=entry.get("behind"),
        head_commit=entry.get("head_commit"),
    )


def reconcile_console(
    *,
    db_rows: list[dict[str, Any]],
    scans: dict[str, ScanOutcome],
    project_names: dict[str, str],
    task_info: dict[str, tuple[str | None, str | None]],
    live: bool,
) -> tuple[list[rest.WorktreeConsoleItem], list[rest.WorktreeScanStatus]]:
    """合账 → (items, scan_statuses)。

    db_rows：worktrees × projects × tasks 一次 join 的全部行（含 cleaned），每行须含 worktree 列
    + project_name + computer_id + task_title + channel_id。
    scans：live=1 时各机扫描结果；live=0 传空。
    project_names / task_info：孤儿行（无 DB 登记）补 project_name 与 (task_title, channel_id) 用。
    """
    # 磁盘索引：(computer_id, project_id, task_id) → 扫描条目（仅 status=ok 的机器）。
    disk_index: dict[tuple[str, str, str], dict[str, Any]] = {}
    if live:
        for cid, (status, entries) in scans.items():
            if status == "ok" and entries:
                for entry in entries:
                    key = (cid, entry["project_id"], entry["task_id"])
                    disk_index[key] = entry

    # 有非-cleaned DB 登记行的三元组（task_id UNIQUE，故每三元组至多一行）→ 其磁盘条目视为"ok"归属，
    # 不作孤儿；只有"无登记"或"仅 cleaned 登记"的磁盘条目才浮出孤儿。
    non_cleaned_keys: set[tuple[str, str, str]] = set()
    for row in db_rows:
        if row["status"] in _NON_CLEANED_STATUSES:
            non_cleaned_keys.add((row["computer_id"], row["project_id"], row["task_id"]))

    items: list[rest.WorktreeConsoleItem] = []
    for row in db_rows:
        computer_id = row["computer_id"]
        status = row["status"]
        key = (computer_id, row["project_id"], row["task_id"])
        scan = scans.get(computer_id) if live else None
        scan_ok = scan is not None and scan[0] == "ok"

        derived = "ok"
        live_obj: rest.WorktreeLive | None = None
        if scan_ok:
            entry = disk_index.get(key)
            if status == WorktreeStatus.CLEANED.value:
                # 终态：cleaned 无论磁盘有无都记 ok；有树的漂移由下方孤儿行单独浮出。
                derived = "ok"
            elif entry is not None:
                derived = "ok"
                live_obj = _live_from_entry(entry)
            elif status == WorktreeStatus.ACTIVE.value:
                derived = "missing"  # active 登记但磁盘无 = 丢失
            else:
                derived = "ok"  # merged/conflicted 无树 = 终态正常形态
        # scan 不可用（live=0 或该机离线/超时）→ derived 保 DB 态 ok、live=None（无从判定漂移）。

        items.append(
            rest.WorktreeConsoleItem(
                id=row["id"],
                project_id=row["project_id"],
                project_name=row["project_name"],
                computer_id=computer_id,
                task_id=row["task_id"],
                task_title=row["task_title"],
                channel_id=row["channel_id"],
                branch=row["branch"],
                path=row["path"],
                status=WorktreeStatus(status),
                derived=derived,
                merge_commit=row["merge_commit"],
                created_at=row["created_at"],
                merged_at=row["merged_at"],
                cleaned_at=row["cleaned_at"],
                live=live_obj,
            )
        )

    # 孤儿：磁盘有树但无非-cleaned DB 登记（无登记 或 仅 cleaned 登记=清理漂移）。
    orphans: list[rest.WorktreeConsoleItem] = []
    for (cid, project_id, task_id), entry in disk_index.items():
        if (cid, project_id, task_id) in non_cleaned_keys:
            continue
        task_title, channel_id = task_info.get(task_id, (None, None))
        orphans.append(
            rest.WorktreeConsoleItem(
                id=None,
                project_id=project_id,
                project_name=project_names.get(project_id) or project_id,
                computer_id=cid,
                task_id=task_id,
                task_title=task_title,
                channel_id=channel_id,
                branch=entry.get("branch"),
                path=entry["path"],
                status=None,
                derived="orphan",
                merge_commit=None,
                created_at=None,
                merged_at=None,
                cleaned_at=None,
                live=_live_from_entry(entry),
            )
        )

    # 确定性排序：DB 行按 (project_id, created_at, id)，孤儿行按 (project_id, task_id) 尾随。
    items.sort(key=lambda it: (it.project_id, it.created_at or "", it.id or ""))
    orphans.sort(key=lambda it: (it.project_id, it.task_id or ""))

    scan_statuses = [
        rest.WorktreeScanStatus(computer_id=cid, status=status)  # type: ignore[arg-type]
        for cid, (status, _entries) in scans.items()
    ]
    scan_statuses.sort(key=lambda s: s.computer_id)

    return items + orphans, scan_statuses


__all__ = ["ScanOutcome", "reconcile_console"]
