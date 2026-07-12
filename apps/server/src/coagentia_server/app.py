"""FastAPI 应用工厂（契约 A/B/C/D 的 M1 服务面）。

装配：lifespan（事件总线 + 文件 GC 定时——坑 3：后台任务挂 lifespan，不在 handler 内 create_task）·
每请求短事务 Session 依赖（deps.get_tx）· ApiError → ErrorResponse 处理器 · 六组 REST 路由。
只绑 127.0.0.1 由入口 uvicorn --host 落实（契约 B §1 / NFR5）；真 server 同源伺服 UI，无需 CORS。
"""

from __future__ import annotations

import asyncio
import contextlib
import mimetypes
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket
from sqlalchemy.engine import Engine
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocketDisconnect

from coagentia_server import __version__
from coagentia_server.api import install_error_handlers
from coagentia_server.computers import DaemonHub
from coagentia_server.db.engine import DEFAULT_DB_PATH, make_engine
from coagentia_server.events import EventBus
from coagentia_server.files import FileStore
from coagentia_server.files.gc import run_gc
from coagentia_server.orchestration.role_templates import upsert_builtin_role_templates
from coagentia_server.routes import install_routes
from coagentia_server.templates.service import upsert_builtin_templates
from coagentia_server.ws import WsHub

GC_INTERVAL_SEC = 60 * 60  # 契约 D §9.2：每小时扫 staging

# 默认数据根 = ~/.coagentia/server（与 coagentia.db 同居，契约 D §9.1）。
DEFAULT_DATA_ROOT = DEFAULT_DB_PATH.parent


class SpaStaticFiles(StaticFiles):
    """静态资源服务；非 API 的客户端路由回退到 index.html。"""

    async def get_response(self, path: str, scope: Any) -> Response:
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or not self._should_fallback(path):
                raise
            return await super().get_response("index.html", scope)
        if response.status_code == 404 and self._should_fallback(path):
            return await super().get_response("index.html", scope)
        return response

    @staticmethod
    def _should_fallback(path: str) -> bool:
        return not path.startswith("api/") and not Path(path).suffix


def _find_web_dist(configured: str | Path | None) -> Path | None:
    """解析显式目录、环境变量或 monorepo 的 apps/web/dist。"""
    candidates: list[Path] = []
    if configured is not None:
        candidates.append(Path(configured))
    elif env_path := os.environ.get("COAGENTIA_WEB_DIST"):
        candidates.append(Path(env_path))
    else:
        candidates.append(Path(__file__).resolve().parents[3] / "web" / "dist")
    return next((path for path in candidates if (path / "index.html").is_file()), None)


def create_app(
    *,
    engine: Engine | None = None,
    data_root: str | Path | None = None,
    server_url: str = "http://127.0.0.1:8787",
    web_dist: str | Path | None = None,
) -> FastAPI:
    engine = engine or make_engine()
    file_store = FileStore(data_root if data_root is not None else DEFAULT_DATA_ROOT)
    file_store.ensure_dirs()
    bus = EventBus()
    ws_hub = WsHub(engine, bus, __version__)
    daemon_hub = DaemonHub(engine, bus, __version__)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # WS 消费任务挂 lifespan（坑 3：不在 handler 内 create_task；捕获运行 loop 供跨线程投递）。
        loop = asyncio.get_running_loop()
        ws_task = ws_hub.start(loop)
        daemon_hub.start(loop)  # daemon 网关：bus 订阅 + 周期对账/reminder/心跳 loop
        # 启动对每 workspace upsert 工程三角 builtin 模板（B §11.1 #3；幂等、空库优雅跳过）。
        await asyncio.to_thread(upsert_builtin_templates, engine)
        # 启动 upsert 内置 Orchestrator 角色模板（A §4.1；全局字典表、幂等键 key='orchestrator'）。
        await asyncio.to_thread(upsert_builtin_role_templates, engine)
        # 启动时 GC 一次 + 每小时定时（坑 3：任务挂 lifespan，随应用生命周期收放）。
        await asyncio.to_thread(run_gc, engine, file_store)
        gc_task = asyncio.create_task(_gc_loop(engine, file_store))
        try:
            yield
        finally:
            ws_hub.stop()
            await daemon_hub.stop()
            for task in (gc_task, ws_task):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="coagentia-server", version="0.1.0", lifespan=lifespan)
    app.state.engine = engine
    app.state.bus = bus
    app.state.file_store = file_store
    app.state.server_url = server_url
    app.state.ws_hub = ws_hub
    app.state.daemon_hub = daemon_hub
    app.state.web_dist = _find_web_dist(web_dist)

    install_error_handlers(app)
    install_routes(app)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        return Response(status_code=204)

    @app.websocket("/api/daemon/ws")
    async def daemon_ws_endpoint(sock: WebSocket) -> None:
        # daemon 线协议端点（契约 D §2）：认证 → 握手 → 对账 → 收帧循环，全在 hub.serve。
        await daemon_hub.serve(sock)

    @app.websocket("/api/ws")
    async def ws_endpoint(sock: WebSocket) -> None:
        # 端点（契约 C §2）：接受即发 hello；上行仅心跳与流订阅（§5）；断连清连接与订阅。
        conn = await ws_hub.attach(sock)
        try:
            while True:
                raw = await sock.receive_json()
                await ws_hub.handle_uplink(conn, raw)
        except WebSocketDisconnect:
            pass
        finally:
            await ws_hub.detach(conn)

    if app.state.web_dist is not None:
        # Windows 注册表可能把 .js 映射为 text/plain；浏览器会拒绝加载模块脚本。
        mimetypes.add_type("application/javascript", ".js", strict=True)
        app.mount(
            "/",
            SpaStaticFiles(directory=app.state.web_dist, html=True),
            name="web",
        )

    return app


async def _gc_loop(engine: Engine, file_store: FileStore) -> None:
    while True:
        await asyncio.sleep(GC_INTERVAL_SEC)
        await asyncio.to_thread(run_gc, engine, file_store)
