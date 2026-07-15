"""daemon 测试底座：临时迁移库 + 真 uvicorn server（供 daemon↔真server 集成测试）。

单元测试（handlers/buffer/paths/probe/reconnect）用内存传输，无需 server；集成测试用本文件的
running_server fixture：alembic upgrade → create_app → 后台线程跑 uvicorn → 返回基址与 Env。
"""

from __future__ import annotations

import hashlib
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import coagentia_server
import pytest
import uvicorn
from alembic import command
from alembic.config import Config
from coagentia_server.app import create_app
from coagentia_server.db import models
from coagentia_server.db.engine import make_engine, sqlite_url
from coagentia_server.ledger.service import new_ulid, now_iso
from sqlalchemy import insert, select
from sqlalchemy.engine import Engine

ALEMBIC_INI = Path(coagentia_server.__file__).resolve().parents[2] / "alembic.ini"

_WS_ID = "01K5WKSP00000000000000000A"
_COMP_ID = "01K5CMPT00000000000000000A"
_OWNER_ID = "01K5HMAN00000000000000000A"


@pytest.fixture
def migrated_engine(tmp_path: Path) -> Iterator[Engine]:
    url = sqlite_url(tmp_path / "coagentia_test.db")
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    engine = make_engine(url=url)
    yield engine
    engine.dispose()


class IntegrationEnv:
    """集成测试最小库场景（workspace + computer + agents），直插不经 REST。"""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.ws_id = _WS_ID
        self.comp_id = _COMP_ID
        self.owner_id = _OWNER_ID
        self.api_key = f"cak_int{new_ulid().lower()}"
        key_hash = hashlib.sha256(self.api_key.encode()).hexdigest()
        with engine.begin() as c:
            c.execute(
                insert(models.Workspace.__table__).values(
                    id=self.ws_id, name="T", slug="t", created_at=now_iso()
                )
            )
            c.execute(
                insert(models.Computer.__table__).values(
                    id=self.comp_id,
                    workspace_id=self.ws_id,
                    name="Rig",
                    api_key_hash=key_hash,
                    status="offline",
                    created_at=now_iso(),
                )
            )
            c.execute(
                insert(models.Member.__table__).values(
                    id=self.owner_id,
                    workspace_id=self.ws_id,
                    kind="human",
                    name="Owner",
                    role="owner",
                    created_at=now_iso(),
                )
            )

    def add_agent(self, name: str, status: str) -> str:
        time.sleep(0.002)
        mid = new_ulid()
        with self.engine.begin() as c:
            c.execute(
                insert(models.Member.__table__).values(
                    id=mid,
                    workspace_id=self.ws_id,
                    kind="agent",
                    name=name,
                    role="member",
                    created_at=now_iso(),
                )
            )
            c.execute(
                insert(models.Agent.__table__).values(
                    member_id=mid,
                    computer_id=self.comp_id,
                    runtime="claude_code",
                    model="claude-opus-4-8",
                    description="",
                    home_path=f"~/.coagentia/agents/{mid}",
                    status=status,
                    created_by_member_id=self.owner_id,
                )
            )
        return mid

    def computer_status(self) -> str:
        with self.engine.connect() as c:
            return c.execute(
                select(models.Computer.__table__.c.status).where(
                    models.Computer.__table__.c.id == self.comp_id
                )
            ).scalar_one()

    def agent_status(self, member_id: str) -> str:
        with self.engine.connect() as c:
            return c.execute(
                select(models.Agent.__table__.c.status).where(
                    models.Agent.__table__.c.member_id == member_id
                )
            ).scalar_one()


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def running_server(migrated_engine: Engine, tmp_path: Path) -> Iterator[tuple[str, IntegrationEnv]]:
    """真 server 在后台线程跑 uvicorn，返回 (base_url, env)。"""
    env = IntegrationEnv(migrated_engine)
    app = create_app(engine=migrated_engine, data_root=tmp_path / "srv-data")
    port = _free_port()
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning", loop="asyncio", lifespan="on"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started, "uvicorn 未在超时内就绪"
    try:
        yield f"http://127.0.0.1:{port}", env
    finally:
        server.should_exit = True
        thread.join(timeout=10)
