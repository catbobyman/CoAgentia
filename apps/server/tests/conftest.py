"""共享 fixture：临时库 + alembic upgrade head 驱动的 migrated_engine。"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from coagentia_server.db.engine import make_engine, sqlite_url
from sqlalchemy.engine import Engine

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def make_alembic_config(url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return sqlite_url(tmp_path / "coagentia_test.db")


@pytest.fixture
def alembic_cfg(db_url: str) -> Config:
    return make_alembic_config(db_url)


@pytest.fixture
def migrated_engine(db_url: str, alembic_cfg: Config) -> Engine:
    """空临时库 → alembic upgrade head → 返回已挂 PRAGMA 的 Engine。"""
    command.upgrade(alembic_cfg, "head")
    engine = make_engine(url=db_url)
    yield engine
    engine.dispose()
