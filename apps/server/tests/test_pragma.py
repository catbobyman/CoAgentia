"""真实文件库连接后 PRAGMA 符合契约 A §1（WAL / FK / busy_timeout / synchronous）。"""

from __future__ import annotations

from pathlib import Path

from coagentia_server.db.engine import make_engine
from sqlalchemy import text


def _pragma(engine, name: str):  # noqa: ANN001
    with engine.connect() as conn:
        return conn.execute(text(f"PRAGMA {name}")).scalar()


def test_file_db_pragmas(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "pragma.db")
    try:
        assert _pragma(engine, "foreign_keys") == 1
        assert str(_pragma(engine, "journal_mode")).lower() == "wal"
        assert _pragma(engine, "busy_timeout") == 5000
        assert _pragma(engine, "synchronous") == 1  # NORMAL
    finally:
        engine.dispose()


def test_memory_db_skips_wal() -> None:
    engine = make_engine(url="sqlite:///:memory:")
    try:
        # 内存库 WAL 不适用——跳过；FK 仍必须打开。
        assert _pragma(engine, "foreign_keys") == 1
        assert str(_pragma(engine, "journal_mode")).lower() != "wal"
    finally:
        engine.dispose()
