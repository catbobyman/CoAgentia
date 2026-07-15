"""共享 fixture：临时库 + alembic upgrade head 驱动的 migrated_engine + 真 server TestClient。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from coagentia_server.app import create_app
from coagentia_server.db.engine import make_engine, sqlite_url
from coagentia_server.db.seed import seed_database
from fastapi.testclient import TestClient
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
def migrated_engine(db_url: str, alembic_cfg: Config) -> Iterator[Engine]:
    """空临时库 → alembic upgrade head → 返回已挂 PRAGMA 的 Engine。"""
    command.upgrade(alembic_cfg, "head")
    engine = make_engine(url=db_url)
    yield engine
    engine.dispose()


@pytest.fixture
def seeded_engine(migrated_engine: Engine) -> Engine:
    """migrated_engine + 灌入 seed.json 的 M1 子集。"""
    seed_database(migrated_engine)
    return migrated_engine


@pytest.fixture
def server_client(seeded_engine: Engine, tmp_path: Path) -> Iterator[TestClient]:
    """真 server：create_app(注入库 + 临时数据根) + TestClient（含 lifespan 起停）。"""
    app = create_app(engine=seeded_engine, data_root=tmp_path / "data")
    with TestClient(app) as client:
        yield client


@pytest.fixture
def empty_server_client(migrated_engine: Engine, tmp_path: Path) -> Iterator[TestClient]:
    """真 server：空库（未 bootstrap），用于 POST /workspace 冷启动测试。"""
    app = create_app(engine=migrated_engine, data_root=tmp_path / "data")
    with TestClient(app) as client:
        yield client
