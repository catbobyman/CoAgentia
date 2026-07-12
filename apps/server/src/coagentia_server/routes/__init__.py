"""REST M1 路由（契约 B §4.1–4.6，39 端点）。"""

from fastapi import FastAPI

from coagentia_server.routes import (
    activity,
    canvas,
    channels,
    computers,
    files,
    held_drafts,
    members,
    messages,
    projects,
    proposals,
    search,
    tasks,
    templates,
    workspace,
)


def install_routes(app: FastAPI) -> None:
    for module in (
        workspace,
        computers,
        members,
        channels,
        messages,
        projects,
        proposals,
        tasks,
        files,
        search,
        activity,
        canvas,
        held_drafts,
        templates,
    ):
        app.include_router(module.router)
