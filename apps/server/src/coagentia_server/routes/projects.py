"""Project 与频道绑定 REST 端点（契约 B §4.11/§12.12；M6a J2）。"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from coagentia_contracts import entities, rest
from coagentia_contracts.enums import WorktreeStatus
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from coagentia_server.api import ApiError
from coagentia_server.db import models
from coagentia_server.deps import Tx, acting_member, get_tx, require_admin, require_workspace
from coagentia_server.ledger.service import new_ulid, now_iso
from coagentia_server.routes.serialize import channel_project_public, project_public

router = APIRouter(prefix="/api", tags=["projects"])

_PROJECT = models.tbl(models.Project)
_CHANNEL_PROJECT = models.tbl(models.ChannelProject)
_WORKTREE = models.tbl(models.Worktree)
_TASK = models.tbl(models.Task)
_CHANNEL = models.tbl(models.Channel)
_COMPUTER = models.tbl(models.Computer)


def _fetch_project(tx: Tx, project_id: str, workspace_id: str) -> dict[str, Any]:
    row = (
        tx.conn.execute(
            select(_PROJECT).where(
                _PROJECT.c.id == project_id,
                _PROJECT.c.workspace_id == workspace_id,
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "Project 不存在")
    return dict(row)


def _require_channel(tx: Tx, channel_id: str, workspace_id: str) -> None:
    row = tx.conn.execute(
        select(_CHANNEL.c.id).where(
            _CHANNEL.c.id == channel_id,
            _CHANNEL.c.workspace_id == workspace_id,
        )
    ).first()
    if row is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "频道不存在")


def _require_computer(tx: Tx, computer_id: str, workspace_id: str) -> None:
    row = tx.conn.execute(
        select(_COMPUTER.c.id).where(
            _COMPUTER.c.id == computer_id,
            _COMPUTER.c.workspace_id == workspace_id,
        )
    ).first()
    if row is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "机器不存在")


def _invalid_repo_path(repo_path: str) -> ApiError:
    return ApiError(
        422,
        rest.ErrorCode.VALIDATION_FAILED,
        "repo_path 必须指向存在的 git 仓库",
        rule="B§12.12",
        details={"field": "repo_path"},
    )


def _validate_repo_path(repo_path: str) -> None:
    """MVP 单机直查：路径须为目录，且 git 明确认定它位于工作树内。"""
    path = Path(repo_path)
    try:
        if not path.is_dir():
            raise _invalid_repo_path(repo_path)
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except ApiError:
        raise
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _invalid_repo_path(repo_path) from exc
    if result.returncode != 0 or result.stdout.strip().lower() != "true":
        raise _invalid_repo_path(repo_path)


def _channel_ids(tx: Tx, project_id: str) -> list[str]:
    return list(
        tx.conn.execute(
            select(_CHANNEL_PROJECT.c.channel_id)
            .where(_CHANNEL_PROJECT.c.project_id == project_id)
            .order_by(_CHANNEL_PROJECT.c.channel_id)
        ).scalars()
    )


def _project_public(tx: Tx, row: dict[str, Any]) -> dict[str, Any]:
    return project_public(row, channel_ids=_channel_ids(tx, row["id"]))


@router.get("/projects", response_model=list[entities.ProjectPublic])
def list_projects(request: Request, tx: Tx = Depends(get_tx)) -> Any:
    ws = require_workspace(tx.conn)
    require_admin(acting_member(request, tx.conn))
    rows = [
        dict(row)
        for row in tx.conn.execute(
            select(_PROJECT)
            .where(_PROJECT.c.workspace_id == ws["id"])
            .order_by(_PROJECT.c.created_at, _PROJECT.c.id)
        ).mappings()
    ]
    channels_by_project: dict[str, list[str]] = {row["id"]: [] for row in rows}
    if channels_by_project:
        bindings = tx.conn.execute(
            select(_CHANNEL_PROJECT.c.project_id, _CHANNEL_PROJECT.c.channel_id)
            .where(_CHANNEL_PROJECT.c.project_id.in_(channels_by_project))
            .order_by(_CHANNEL_PROJECT.c.project_id, _CHANNEL_PROJECT.c.channel_id)
        )
        for project_id, channel_id in bindings:
            channels_by_project[project_id].append(channel_id)
    return [
        project_public(row, channel_ids=channels_by_project[row["id"]]) for row in rows
    ]


@router.post("/projects", response_model=entities.ProjectPublic, status_code=201)
def create_project(body: rest.ProjectCreate, request: Request, tx: Tx = Depends(get_tx)) -> Any:
    ws = require_workspace(tx.conn)
    require_admin(acting_member(request, tx.conn))
    _require_computer(tx, body.computer_id, ws["id"])
    _validate_repo_path(body.repo_path)

    project_id = new_ulid()
    values = body.model_dump(exclude_unset=True)
    values.update(id=project_id, workspace_id=ws["id"], created_at=now_iso())
    # 可选数值不传时由表默认兜底；显式 null 不覆盖 NOT NULL 默认列。
    values = {key: value for key, value in values.items() if value is not None}
    tx.conn.execute(insert(_PROJECT).values(**values))
    return _project_public(tx, _fetch_project(tx, project_id, ws["id"]))


@router.patch("/projects/{project_id}", response_model=entities.ProjectPublic)
def patch_project(
    project_id: str,
    body: rest.ProjectPatch,
    request: Request,
    tx: Tx = Depends(get_tx),
) -> Any:
    ws = require_workspace(tx.conn)
    require_admin(acting_member(request, tx.conn))
    _fetch_project(tx, project_id, ws["id"])

    nullable_fields = {"dev_command", "deploy_command"}
    for field in body.model_fields_set - nullable_fields:
        if getattr(body, field) is None:
            raise ApiError(
                422,
                rest.ErrorCode.VALIDATION_FAILED,
                f"{field} 不可为 null",
                rule="B§4.11",
                details={"field": field},
            )
    changes = {field: getattr(body, field) for field in body.model_fields_set}
    if "computer_id" in changes:
        _require_computer(tx, changes["computer_id"], ws["id"])
    if "repo_path" in changes:
        _validate_repo_path(changes["repo_path"])
    if changes:
        tx.conn.execute(update(_PROJECT).where(_PROJECT.c.id == project_id).values(**changes))
    return _project_public(tx, _fetch_project(tx, project_id, ws["id"]))


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str, request: Request, tx: Tx = Depends(get_tx)) -> Response:
    ws = require_workspace(tx.conn)
    require_admin(acting_member(request, tx.conn))
    _fetch_project(tx, project_id, ws["id"])
    active_worktrees = tx.conn.execute(
        select(func.count())
        .select_from(_WORKTREE)
        .where(
            _WORKTREE.c.project_id == project_id,
            _WORKTREE.c.status != WorktreeStatus.CLEANED,
        )
    ).scalar_one()
    if active_worktrees:
        raise ApiError(
            409,
            rest.ErrorCode.PROJECT_IN_USE,
            "Project 仍有未清理 worktree，无法删除",
            rule="B§12.12",
            details={"active_worktrees": active_worktrees},
        )
    # cleaned 树已完成物理清理；Project 删除时移除其生命周期行，并解除历史任务引用。
    # writes_code=>project_id 是 A §4.3 app 不变量，故两字段须一起清回非代码任务态。
    tx.conn.execute(
        delete(_WORKTREE).where(
            _WORKTREE.c.project_id == project_id,
            _WORKTREE.c.status == WorktreeStatus.CLEANED,
        )
    )
    tx.conn.execute(
        update(_TASK)
        .where(_TASK.c.project_id == project_id)
        .values(project_id=None, writes_code=False)
    )
    tx.conn.execute(delete(_PROJECT).where(_PROJECT.c.id == project_id))
    return Response(status_code=204)


@router.post(
    "/channels/{channel_id}/projects",
    response_model=entities.ChannelProjectPublic,
    status_code=201,
)
def bind_project(
    channel_id: str,
    body: rest.ProjectBind,
    request: Request,
    tx: Tx = Depends(get_tx),
) -> Any:
    ws = require_workspace(tx.conn)
    require_admin(acting_member(request, tx.conn))
    _require_channel(tx, channel_id, ws["id"])
    _fetch_project(tx, body.project_id, ws["id"])
    stmt = sqlite_insert(_CHANNEL_PROJECT).values(
        channel_id=channel_id, project_id=body.project_id
    )
    tx.conn.execute(
        stmt.on_conflict_do_nothing(
            index_elements=[_CHANNEL_PROJECT.c.channel_id, _CHANNEL_PROJECT.c.project_id]
        )
    )
    row = tx.conn.execute(
        select(_CHANNEL_PROJECT).where(
            _CHANNEL_PROJECT.c.channel_id == channel_id,
            _CHANNEL_PROJECT.c.project_id == body.project_id,
        )
    ).mappings().one()
    return channel_project_public(dict(row))


@router.delete("/channels/{channel_id}/projects/{project_id}", status_code=204)
def unbind_project(
    channel_id: str,
    project_id: str,
    request: Request,
    tx: Tx = Depends(get_tx),
) -> Response:
    ws = require_workspace(tx.conn)
    require_admin(acting_member(request, tx.conn))
    _require_channel(tx, channel_id, ws["id"])
    _fetch_project(tx, project_id, ws["id"])
    tx.conn.execute(
        delete(_CHANNEL_PROJECT).where(
            _CHANNEL_PROJECT.c.channel_id == channel_id,
            _CHANNEL_PROJECT.c.project_id == project_id,
        )
    )
    return Response(status_code=204)
