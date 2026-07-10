"""空库 upgrade head 成功、17 表齐、downgrade 可回。"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from coagentia_server.db.engine import make_engine
from coagentia_server.db.models import IMMUTABLE_TABLES
from sqlalchemy import inspect

# 契约 A §5 M1 首行清单（17 张）。
M1_EXPECTED_TABLES = {
    "workspaces", "computers", "members", "agents", "agent_skills",
    "channels", "channel_members", "messages", "message_mentions", "files",
    "read_positions", "reminders", "diagnostic_events", "token_usage_events",
    "ledger_entries", "landing_batches", "canvases",
}


def _table_names(url: str) -> set[str]:
    engine = make_engine(url=url)
    try:
        names = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    return names


def test_upgrade_head_creates_17_tables(db_url: str, alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    names = _table_names(db_url)
    assert M1_EXPECTED_TABLES <= names
    assert len(M1_EXPECTED_TABLES) == 17


def test_upgrade_creates_immutable_triggers(db_url: str, alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    engine = make_engine(url=db_url)
    try:
        from sqlalchemy import text

        with engine.connect() as conn:
            trigs = {
                r[0]
                for r in conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='trigger'")
                )
            }
    finally:
        engine.dispose()
    for table in IMMUTABLE_TABLES:
        assert f"trg_{table}_no_update" in trigs
        assert f"trg_{table}_no_delete" in trigs


def test_downgrade_base_drops_tables(db_url: str, alembic_cfg: Config, tmp_path: Path) -> None:
    command.upgrade(alembic_cfg, "head")
    command.downgrade(alembic_cfg, "base")
    names = _table_names(db_url)
    assert M1_EXPECTED_TABLES.isdisjoint(names)
