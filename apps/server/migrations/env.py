"""Alembic 环境：以 db/models.py 的 metadata 为 target，支持测试注入临时库 URL。

URL 解析优先级：COAGENTIA_ALEMBIC_URL 环境变量 > alembic.ini 的 sqlalchemy.url >
engine.default_db_url()。在线迁移复用 engine.make_engine（同一套 PRAGMA）。
"""

from __future__ import annotations

import os

from alembic import context
from coagentia_server.db import models
from coagentia_server.db.engine import default_db_url, make_engine

config = context.config
target_metadata = models.Base.metadata


def _resolve_url() -> str:
    env_url = os.environ.get("COAGENTIA_ALEMBIC_URL")
    if env_url:
        return env_url
    ini_url = config.get_main_option("sqlalchemy.url")
    if ini_url:
        return ini_url
    return default_db_url()


def run_migrations_offline() -> None:
    context.configure(
        url=_resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = make_engine(url=_resolve_url())
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
