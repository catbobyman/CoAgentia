"""FastAPI 应用工厂（契约 A/B/C/D 的 M1 服务面）。

装配：lifespan（事件总线 + 文件 GC 定时——坑 3：后台任务挂 lifespan，不在 handler 内 create_task）·
每请求短事务 Session 依赖（deps.get_tx）· ApiError → ErrorResponse 处理器 · 六组 REST 路由。
只绑 127.0.0.1 由入口 uvicorn --host 落实（契约 B §1 / NFR5）；真 server 同源伺服 UI，无需 CORS。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, WebSocket
from sqlalchemy.engine import Engine
from starlette.websockets import WebSocketDisconnect

from coagentia_server import __version__
from coagentia_server.api import install_error_handlers
from coagentia_server.computers import DaemonHub
from coagentia_server.db.engine import DEFAULT_DB_PATH, make_engine
from coagentia_server.events import EventBus
from coagentia_server.files import FileStore
from coagentia_server.files.gc import run_gc
from coagentia_server.routes import install_routes
from coagentia_server.ws import WsHub

GC_INTERVAL_SEC = 60 * 60  # 契约 D §9.2：每小时扫 staging

# 默认数据根 = ~/.coagentia/server（与 coagentia.db 同居，契约 D §9.1）。
DEFAULT_DATA_ROOT = DEFAULT_DB_PATH.parent


def create_app(
    *,
    engine: Engine | None = None,
    data_root: str | Path | None = None,
    server_url: str = "http://127.0.0.1:8787",
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

    install_error_handlers(app)
    install_routes(app)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

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

    return app


async def _gc_loop(engine: Engine, file_store: FileStore) -> None:
    while True:
        await asyncio.sleep(GC_INTERVAL_SEC)
        await asyncio.to_thread(run_gc, engine, file_store)
