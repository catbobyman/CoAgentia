"""FastAPI 应用工厂（M1 骨架：仅健康检查；模块路由随里程碑接入）。"""

from __future__ import annotations

from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="coagentia-server", version="0.1.0")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
