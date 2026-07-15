"""M7a 实机 verify：uvicorn --factory 入口，真 server 指向隔离临时库 + 短预览回收间隔。

体例同 m6a_appfactory，额外把 hub 的预览回收扫描间隔与 starting 超时调短（verify 内快触发
idle 回收与对账兜底；纯 probe 侧配置，不改产品默认）。M6A_DB_URL/M6A_DATA_ROOT 复用既有 env 名。
"""

from __future__ import annotations

import os

from coagentia_server.app import create_app
from coagentia_server.db.engine import make_engine
from sqlalchemy import event


def make_probe_app():  # noqa: ANN201 — uvicorn factory
    db_url = os.environ["M6A_DB_URL"]
    data_root = os.environ["M6A_DATA_ROOT"]
    engine = make_engine(url=db_url)

    @event.listens_for(engine, "connect")
    def _bump_busy_timeout(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()

    app = create_app(engine=engine, data_root=data_root)
    hub = app.state.daemon_hub
    hub.preview_recycle_interval = 2.0  # verify：快扫 idle 回收（产品默认 60s）
    hub.preview_starting_timeout_sec = 8.0  # verify：starting 超时兜底快触发（产品默认 180s）
    return app
