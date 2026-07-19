"""REST M1 路由（契约 B §4.1–4.6，39 端点）。"""

from fastapi import FastAPI

from coagentia_server.routes import (
    activity,
    channels,
    computers,
    deployments,
    files,
    held_drafts,
    members,
    messages,
    projects,
    search,
    tasks,
    usage,
    workspace,
    worktrees,
)


def install_routes(app: FastAPI) -> None:
    for module in (
        workspace,
        computers,
        members,
        channels,
        messages,
        projects,
        tasks,
        files,
        search,
        activity,
        held_drafts,
        deployments,
        usage,
        worktrees,
    ):
        app.include_router(module.router)
