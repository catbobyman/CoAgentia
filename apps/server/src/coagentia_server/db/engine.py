"""SQLAlchemy 2.0 同步 Engine 工厂 + PRAGMA 注入（契约 A §1）。

默认库 = ~/.coagentia/server/coagentia.db；支持注入内存/临时库（测试用）。
每次 connect 挂四项 PRAGMA：foreign_keys=ON · busy_timeout=5000 · synchronous=NORMAL ·
journal_mode=WAL（内存库 WAL 不适用，条件跳过；真实文件库必须 WAL）。
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

DEFAULT_DB_PATH = Path.home() / ".coagentia" / "server" / "coagentia.db"


def default_db_url() -> str:
    return sqlite_url(DEFAULT_DB_PATH)


def sqlite_url(db_path: str | Path) -> str:
    """文件库 URL（绝对/相对路径均可）。"""
    return f"sqlite:///{Path(db_path).as_posix()}"


def _is_memory_url(url: str) -> bool:
    return ":memory:" in url or url in ("sqlite://", "sqlite:///:memory:")


def make_engine(
    db_path: str | Path | None = None,
    *,
    url: str | None = None,
    echo: bool = False,
) -> Engine:
    """构造已挂 PRAGMA 的 Engine。

    - `url` 直给（`sqlite:///:memory:` 或临时文件 URL）优先；
    - 否则用 `db_path`（None → 默认库，父目录自动创建）。
    """
    if url is None:
        target = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        url = sqlite_url(target)

    engine = create_engine(url, echo=echo, future=True)
    install_pragmas(engine, is_memory=_is_memory_url(url))
    return engine


def install_pragmas(engine: Engine, *, is_memory: bool) -> None:
    """在 connect 事件上挂契约 A §1 的四项 PRAGMA。"""

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        if not is_memory:
            cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()
