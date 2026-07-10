"""REST M1 路由（契约 B §4.1–4.6，39 端点）。"""

from fastapi import FastAPI

from coagentia_server.routes import (
    activity,
    channels,
    computers,
    files,
    members,
    messages,
    search,
    tasks,
    workspace,
)


def install_routes(app: FastAPI) -> None:
    for module in (
        workspace,
        computers,
        members,
        channels,
        messages,
        tasks,
        files,
        search,
        activity,
    ):
        app.include_router(module.router)
