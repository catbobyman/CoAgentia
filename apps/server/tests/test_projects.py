"""M6a J2：Project CRUD、频道绑定、admin 门与仓库校验。"""

from __future__ import annotations

import hashlib
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from coagentia_contracts import entities, rest
from coagentia_contracts.enums import WorktreeStatus
from coagentia_server.db import models
from coagentia_server.ledger.service import new_ulid, now_iso
from fastapi.testclient import TestClient
from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Engine

AGENT_KEY = "cak_project_agent_test"


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "中文 Project 仓库"
    repo.mkdir()
    _git("init", "--quiet", cwd=repo)
    return repo


def _computer_id(client: TestClient) -> str:
    return client.get("/api/computers").json()[0]["id"]


def _add_computer(client: TestClient, name: str) -> dict:
    response = client.post("/api/computers", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["computer"]


def _channel(client: TestClient, name: str = "build") -> dict:
    return next(
        channel
        for channel in client.get("/api/channels").json()["items"]
        if channel["name"] == name
    )


def _create_project(client: TestClient, repo: Path, **updates: object) -> dict:
    body: dict[str, object] = {
        "name": "CoAgentia",
        "repo_path": str(repo),
        "computer_id": _computer_id(client),
    }
    body.update(updates)
    response = client.post("/api/projects", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _agent_headers(client: TestClient, engine: Engine) -> dict[str, str]:
    agent = next(
        member for member in client.get("/api/members").json() if member["kind"] == "agent"
    )
    digest = hashlib.sha256(AGENT_KEY.encode()).hexdigest()
    with engine.begin() as conn:
        computer_id = conn.execute(
            select(models.Agent.__table__.c.computer_id).where(
                models.Agent.__table__.c.member_id == agent["id"]
            )
        ).scalar_one()
        conn.execute(
            update(models.Computer.__table__)
            .where(models.Computer.__table__.c.id == computer_id)
            .values(api_key_hash=digest)
        )
    return {
        "Authorization": f"Bearer {AGENT_KEY}",
        "X-Acting-Member": agent["id"],
    }


def test_project_crud_binding_and_derived_channel_ids(
    server_client: TestClient, tmp_path: Path, git_repo: Path
) -> None:
    project = _create_project(server_client, git_repo)
    public = entities.ProjectPublic.model_validate(project)
    assert public.channel_ids == []
    assert public.preview_idle_min == 30
    assert public.worktree_keep_days == 7

    second_repo = tmp_path / "second-repo"
    second_repo.mkdir()
    _git("init", "--quiet", cwd=second_repo)
    patched = server_client.patch(
        f"/api/projects/{public.id}",
        json={
            "name": "CoAgentia Next",
            "repo_path": str(second_repo),
            "dev_command": "pnpm dev",
            "deploy_command": "pnpm deploy",
            "preview_idle_min": 45,
            "worktree_keep_days": 3,
        },
    )
    assert patched.status_code == 200, patched.text
    updated = entities.ProjectPublic.model_validate(patched.json())
    assert updated.name == "CoAgentia Next"
    assert updated.repo_path == str(second_repo)
    assert updated.dev_command == "pnpm dev"
    assert updated.deploy_command == "pnpm deploy"
    assert updated.preview_idle_min == 45
    assert updated.worktree_keep_days == 3

    cleared = server_client.patch(
        f"/api/projects/{public.id}",
        json={"dev_command": None, "deploy_command": None},
    )
    assert cleared.status_code == 200, cleared.text
    cleared_public = entities.ProjectPublic.model_validate(cleared.json())
    assert cleared_public.dev_command is None
    assert cleared_public.deploy_command is None

    build = _channel(server_client)
    bind_url = f"/api/channels/{build['id']}/projects"
    first = server_client.post(bind_url, json={"project_id": public.id})
    second = server_client.post(bind_url, json={"project_id": public.id})
    assert first.status_code == second.status_code == 201
    assert entities.ChannelProjectPublic.model_validate(first.json()).channel_id == build["id"]

    listed = [
        entities.ProjectPublic.model_validate(item)
        for item in server_client.get("/api/projects").json()
    ]
    assert len(listed) == 1
    assert listed[0].channel_ids == [build["id"]]

    assert server_client.delete(f"{bind_url}/{public.id}").status_code == 204
    assert server_client.get("/api/projects").json()[0]["channel_ids"] == []
    assert server_client.delete(f"/api/projects/{public.id}").status_code == 204
    assert server_client.get("/api/projects").json() == []


def test_project_requires_computer_and_valid_git_repo(
    server_client: TestClient, tmp_path: Path, git_repo: Path
) -> None:
    missing_computer = server_client.post(
        "/api/projects", json={"name": "x", "repo_path": str(git_repo)}
    )
    assert missing_computer.status_code == 422
    assert (
        rest.ErrorResponse.model_validate(missing_computer.json()).error.code
        is rest.ErrorCode.VALIDATION_FAILED
    )

    plain_dir = tmp_path / "not-a-repo"
    plain_dir.mkdir()
    invalid = server_client.post(
        "/api/projects",
        json={
            "name": "x",
            "repo_path": str(plain_dir),
            "computer_id": _computer_id(server_client),
        },
    )
    assert invalid.status_code == 422
    error = rest.ErrorResponse.model_validate(invalid.json()).error
    assert error.code is rest.ErrorCode.VALIDATION_FAILED
    assert error.rule == "B§12.12"
    assert error.details == {"field": "repo_path"}

    missing_path = server_client.post(
        "/api/projects",
        json={
            "name": "x",
            "repo_path": str(tmp_path / "missing"),
            "computer_id": _computer_id(server_client),
        },
    )
    assert missing_path.status_code == 422

    unknown_computer = server_client.post(
        "/api/projects",
        json={"name": "x", "repo_path": str(git_repo), "computer_id": "0" * 26},
    )
    assert unknown_computer.status_code == 404
    assert (
        rest.ErrorResponse.model_validate(unknown_computer.json()).error.code
        is rest.ErrorCode.NOT_FOUND
    )

    project = _create_project(server_client, git_repo)
    patch = server_client.patch(
        f"/api/projects/{project['id']}", json={"repo_path": str(plain_dir)}
    )
    assert patch.status_code == 422
    stored = server_client.get("/api/projects").json()[0]
    assert stored["repo_path"] == str(git_repo)


@pytest.mark.parametrize(
    "field",
    ["name", "repo_path", "computer_id", "worktree_keep_days", "preview_idle_min"],
)
def test_project_patch_rejects_null_for_non_nullable_fields(
    server_client: TestClient, git_repo: Path, field: str
) -> None:
    project = _create_project(server_client, git_repo)
    response = server_client.patch(
        f"/api/projects/{project['id']}", json={field: None}
    )
    assert response.status_code == 422
    error = rest.ErrorResponse.model_validate(response.json()).error
    assert error.code is rest.ErrorCode.VALIDATION_FAILED
    assert error.rule == "B§4.11"
    assert error.details == {"field": field}


def test_project_endpoints_are_admin_only(
    server_client: TestClient, seeded_engine: Engine, git_repo: Path
) -> None:
    project = _create_project(server_client, git_repo)
    build = _channel(server_client)
    headers = _agent_headers(server_client, seeded_engine)
    calls = [
        server_client.get("/api/projects", headers=headers),
        server_client.post(
            "/api/projects",
            json={
                "name": "forbidden",
                "repo_path": str(git_repo),
                "computer_id": _computer_id(server_client),
            },
            headers=headers,
        ),
        server_client.patch(
            f"/api/projects/{project['id']}", json={"name": "forbidden"}, headers=headers
        ),
        server_client.delete(f"/api/projects/{project['id']}", headers=headers),
        server_client.post(
            f"/api/channels/{build['id']}/projects",
            json={"project_id": project["id"]},
            headers=headers,
        ),
        server_client.delete(
            f"/api/channels/{build['id']}/projects/{project['id']}", headers=headers
        ),
    ]
    for response in calls:
        assert response.status_code == 403
        error = rest.ErrorResponse.model_validate(response.json()).error
        assert error.code is rest.ErrorCode.PERMISSION_DENIED
        assert error.rule == "admin"


def test_project_bind_rejects_unknown_resources(server_client: TestClient, git_repo: Path) -> None:
    project = _create_project(server_client, git_repo)
    build = _channel(server_client)
    unknown = "0" * 26
    assert server_client.post(
        f"/api/channels/{unknown}/projects", json={"project_id": project["id"]}
    ).status_code == 404
    assert server_client.post(
        f"/api/channels/{build['id']}/projects", json={"project_id": unknown}
    ).status_code == 404
    assert server_client.delete(
        f"/api/channels/{build['id']}/projects/{unknown}"
    ).status_code == 404


def test_concurrent_project_bind_is_atomic_and_idempotent(
    server_client: TestClient, seeded_engine: Engine, git_repo: Path
) -> None:
    project = _create_project(server_client, git_repo)
    build = _channel(server_client)
    url = f"/api/channels/{build['id']}/projects"
    workers = 8
    barrier = threading.Barrier(workers)

    def _bind(_: int) -> tuple[int, dict]:
        barrier.wait(timeout=30)
        response = server_client.post(url, json={"project_id": project["id"]})
        return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_bind, range(workers)))

    assert [status for status, _ in results] == [201] * workers
    assert all(
        body == {"channel_id": build["id"], "project_id": project["id"]}
        for _, body in results
    )
    with seeded_engine.connect() as conn:
        count = conn.execute(
            select(func.count())
            .select_from(models.ChannelProject.__table__)
            .where(
                models.ChannelProject.__table__.c.channel_id == build["id"],
                models.ChannelProject.__table__.c.project_id == project["id"],
            )
        ).scalar_one()
    assert count == 1


def test_delete_channel_cascades_project_binding(
    server_client: TestClient, seeded_engine: Engine, git_repo: Path
) -> None:
    project = _create_project(server_client, git_repo)
    created = server_client.post(
        "/api/channels", json={"name": "project-cascade", "member_ids": []}
    )
    assert created.status_code == 201
    channel_id = created.json()["id"]
    assert server_client.post(
        f"/api/channels/{channel_id}/projects", json={"project_id": project["id"]}
    ).status_code == 201

    assert server_client.delete(f"/api/channels/{channel_id}").status_code == 204
    with seeded_engine.connect() as conn:
        binding = conn.execute(
            select(models.ChannelProject.__table__).where(
                models.ChannelProject.__table__.c.channel_id == channel_id,
                models.ChannelProject.__table__.c.project_id == project["id"],
            )
        ).first()
    assert binding is None
    assert server_client.get("/api/projects").json()[0]["channel_ids"] == []


def test_delete_computer_project_gate_and_migration_paths(
    server_client: TestClient, git_repo: Path
) -> None:
    source = _add_computer(server_client, "Project source")
    target = _add_computer(server_client, "Project target")
    project = _create_project(server_client, git_repo, computer_id=source["id"])

    blocked = server_client.delete(f"/api/computers/{source['id']}")
    assert blocked.status_code == 409
    error = rest.ErrorResponse.model_validate(blocked.json()).error
    assert error.code is rest.ErrorCode.COMPUTER_HAS_PROJECTS
    assert error.rule == "B§12.12"
    assert error.details == {"count": 1, "project_ids": [project["id"]]}

    migrated = server_client.patch(
        f"/api/projects/{project['id']}", json={"computer_id": target["id"]}
    )
    assert migrated.status_code == 200
    assert migrated.json()["computer_id"] == target["id"]
    assert server_client.delete(f"/api/computers/{source['id']}").status_code == 204

    assert server_client.delete(f"/api/projects/{project['id']}").status_code == 204
    assert server_client.delete(f"/api/computers/{target['id']}").status_code == 204


def test_delete_computer_agent_gate_precedes_project_gate(
    server_client: TestClient, git_repo: Path
) -> None:
    computer_id = _computer_id(server_client)
    _create_project(server_client, git_repo, computer_id=computer_id)
    blocked = server_client.delete(f"/api/computers/{computer_id}")
    assert blocked.status_code == 409
    error = rest.ErrorResponse.model_validate(blocked.json()).error
    assert error.code is rest.ErrorCode.COMPUTER_HAS_AGENTS
    assert error.rule == "FR-2.7"


def test_computer_project_details_are_stable_and_bounded(
    server_client: TestClient, seeded_engine: Engine, git_repo: Path
) -> None:
    computer = _add_computer(server_client, "Many projects")
    workspace_id = server_client.get("/api/workspace").json()["id"]
    project_ids = [new_ulid() for _ in range(52)]
    created_at = now_iso()
    with seeded_engine.begin() as conn:
        conn.execute(
            insert(models.Project.__table__),
            [
                {
                    "id": project_id,
                    "workspace_id": workspace_id,
                    "computer_id": computer["id"],
                    "name": f"Project {index}",
                    "repo_path": str(git_repo),
                    "created_at": created_at,
                }
                for index, project_id in enumerate(reversed(project_ids))
            ],
        )

    blocked = server_client.delete(f"/api/computers/{computer['id']}")
    assert blocked.status_code == 409
    error = rest.ErrorResponse.model_validate(blocked.json()).error
    assert error.code is rest.ErrorCode.COMPUTER_HAS_PROJECTS
    assert error.details == {
        "count": 52,
        "project_ids": sorted(project_ids)[:50],
    }


def test_delete_project_reports_all_uncleaned_worktrees(
    server_client: TestClient, seeded_engine: Engine, git_repo: Path
) -> None:
    project = _create_project(server_client, git_repo)
    build = _channel(server_client)
    workspace_id = project["workspace_id"]
    statuses = [
        WorktreeStatus.ACTIVE,
        WorktreeStatus.CONFLICTED,
        WorktreeStatus.MERGED,
        WorktreeStatus.CLEANED,
    ]
    task_ids: list[str] = []
    for index in range(len(statuses)):
        created = server_client.post(
            f"/api/channels/{build['id']}/messages",
            json={"body": f"task {index}", "as_task": {"title": f"task {index}"}},
        )
        assert created.status_code == 201
        task_ids.append(created.json()["task"]["id"])

    with seeded_engine.begin() as conn:
        for task_id, status in zip(task_ids, statuses, strict=True):
            conn.execute(
                insert(models.Worktree.__table__).values(
                    id=new_ulid(),
                    workspace_id=workspace_id,
                    project_id=project["id"],
                    task_id=task_id,
                    branch=f"coagentia/task-{task_id}",
                    path=str(git_repo / "worktrees" / task_id),
                    status=status,
                    merge_commit="a" * 40 if status is WorktreeStatus.MERGED else None,
                    created_at=now_iso(),
                    merged_at=None,
                    cleaned_at=now_iso() if status is WorktreeStatus.CLEANED else None,
                )
            )

    response = server_client.delete(f"/api/projects/{project['id']}")
    assert response.status_code == 409
    error = rest.ErrorResponse.model_validate(response.json()).error
    assert error.code is rest.ErrorCode.PROJECT_IN_USE
    assert error.rule == "B§12.12"
    assert error.details == {"active_worktrees": 3}


def test_delete_project_removes_cleaned_tree_and_detaches_historical_task(
    server_client: TestClient, seeded_engine: Engine, git_repo: Path
) -> None:
    project = _create_project(server_client, git_repo)
    build = _channel(server_client)
    created = server_client.post(
        f"/api/channels/{build['id']}/messages",
        json={"body": "cleaned task", "as_task": {"title": "cleaned task"}},
    )
    task_id = created.json()["task"]["id"]
    worktree_id = new_ulid()
    with seeded_engine.begin() as conn:
        conn.execute(
            update(models.Task.__table__)
            .where(models.Task.__table__.c.id == task_id)
            .values(project_id=project["id"], writes_code=True)
        )
        conn.execute(
            insert(models.Worktree.__table__).values(
                id=worktree_id,
                workspace_id=project["workspace_id"],
                project_id=project["id"],
                task_id=task_id,
                branch=f"coagentia/task-{task_id}",
                path=str(git_repo / "worktrees" / task_id),
                status=WorktreeStatus.CLEANED,
                merge_commit=None,
                created_at=now_iso(),
                merged_at=None,
                cleaned_at=now_iso(),
            )
        )

    assert server_client.delete(f"/api/projects/{project['id']}").status_code == 204
    with seeded_engine.connect() as conn:
        task = conn.execute(
            select(models.Task.__table__.c.project_id, models.Task.__table__.c.writes_code).where(
                models.Task.__table__.c.id == task_id
            )
        ).one()
        worktree = conn.execute(
            select(models.Worktree.__table__.c.id).where(
                models.Worktree.__table__.c.id == worktree_id
            )
        ).first()
        stored_project = conn.execute(
            select(models.Project.__table__.c.id).where(
                models.Project.__table__.c.id == project["id"]
            )
        ).first()
    assert task.project_id is None
    assert task.writes_code is False
    assert worktree is None
    assert stored_project is None
