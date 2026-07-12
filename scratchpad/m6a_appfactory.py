"""M6a 实机 verify：uvicorn --factory 入口，把真 server 指向隔离临时库。

启动前提：M6A_DB_URL 指向已 `alembic upgrade head` + seed 的临时 sqlite 文件；
M6A_DATA_ROOT 为 server 数据根。lifespan 不建表/不迁移（由 launcher 先跑）。
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

    # 真 uvicorn 下 hub（事件循环）与 REST（线程池）并发写 file SQLite，默认 busy_timeout=5000
    # 在瞬时争用下偶发 "database is locked"。verify 场景把等待上限拉高到 30s（纯 probe 侧
    # engine 配置，不改产品代码）——微小写各自 ms 内提交，等待方很快拿锁，消除伪锁。
    @event.listens_for(engine, "connect")
    def _bump_busy_timeout(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()

    return create_app(engine=engine, data_root=data_root)
