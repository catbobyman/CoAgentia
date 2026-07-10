"""messages_fts（FTS5 external-content）与 messages 插入同步 + 增量升级回填（契约 A §4.2）。"""

from __future__ import annotations

from alembic import command
from alembic.config import Config
from coagentia_server.db.engine import make_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

_WS = "01K0WKSP000000000000000001"
_CH = "01K0CHAN000000000000000001"
_TS = "2026-07-09T12:00:00.000Z"
_MATCH = (
    "SELECT m.id FROM messages_fts f JOIN messages m ON m.rowid = f.rowid "
    "WHERE messages_fts MATCH :q"
)


def _seed_ws_channel(conn) -> None:  # noqa: ANN001
    conn.execute(text(
        "INSERT INTO workspaces (id, name, slug, created_at) VALUES (:id,'w','w',:ts)"
    ), {"id": _WS, "ts": _TS})
    conn.execute(text(
        "INSERT INTO channels (id, workspace_id, kind, name, created_at) "
        "VALUES (:id,:ws,'channel','build',:ts)"
    ), {"id": _CH, "ws": _WS, "ts": _TS})


def _insert_msg(conn, mid: str, body: str) -> None:  # noqa: ANN001
    conn.execute(text(
        "INSERT INTO messages (id, workspace_id, channel_id, body, created_at) "
        "VALUES (:id,:ws,:ch,:body,:ts)"
    ), {"id": mid, "ws": _WS, "ch": _CH, "body": body, "ts": _TS})


def test_fts_syncs_on_message_insert(migrated_engine: Engine) -> None:
    """head 库：插消息 → messages_fts_ai 触发器同步 → 可检索（含中文）。"""
    with migrated_engine.begin() as conn:
        _seed_ws_channel(conn)
        _insert_msg(conn, "01K0MESG000000000000000010", "pomodoro 番茄钟 build sprint")
    with migrated_engine.connect() as conn:
        assert [r[0] for r in conn.execute(text(_MATCH), {"q": "pomodoro"})] == [
            "01K0MESG000000000000000010"
        ]
        assert [r[0] for r in conn.execute(text(_MATCH), {"q": "番茄钟"})] == [
            "01K0MESG000000000000000010"
        ]


def test_fts_backfills_existing_messages_on_incremental_upgrade(
    db_url: str, alembic_cfg: Config
) -> None:
    """M1 库已有消息 → 升 head 时 rebuild 回填 → 存量消息可检索。"""
    command.upgrade(alembic_cfg, "0001_m1_initial")
    engine = make_engine(url=db_url)
    with engine.begin() as conn:
        _seed_ws_channel(conn)
        _insert_msg(conn, "01K0MESG000000000000000020", "legacy 检索 target message")
    engine.dispose()
    command.upgrade(alembic_cfg, "head")  # 0002 建 fts + rebuild 回填
    engine = make_engine(url=db_url)
    try:
        with engine.connect() as conn:
            assert [r[0] for r in conn.execute(text(_MATCH), {"q": "legacy"})] == [
                "01K0MESG000000000000000020"
            ]
    finally:
        engine.dispose()
