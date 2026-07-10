"""REST M1 路由（契约 B §4.1–4.6，39 端点）。"""

from fastapi import FastAPI

from coagentia_server.routes import channels, computers, members, messages, tasks, workspace


def install_routes(app: FastAPI) -> None:
    for module in (workspace, computers, members, channels, messages, tasks):
        app.include_router(module.router)
