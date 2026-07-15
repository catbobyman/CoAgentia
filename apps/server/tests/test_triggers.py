"""五张不可变表（契约 A §1）：UPDATE 与 DELETE 均被 BEFORE 触发器 RAISE(ABORT) 拒绝。"""

from __future__ import annotations

import pytest
from coagentia_server.db.seed import seed_database
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError

_WS = "01K0WKSP000000000000000001"
_CH = "01K0CHAN000000000000000001"
_MSG = "01K0MESG000000000000000001"
_MEMBER_AGENT = "01K0MMBR000000000000000002"
_TS = "2026-07-09T12:00:00.000Z"


def _insert_extra_immutable_rows(engine: Engine) -> None:
    """seed 未覆盖 files/ledger_entries/diagnostic_events/landing_batches——手插最小合法行。"""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO files (id, workspace_id, message_id, channel_id, name, mime, "
                "size_bytes, sha256, stored_path, created_at) VALUES "
                "(:id, :ws, :msg, :ch, 'f.md', 'text/markdown', 10, :sha, 'a/f.md', :ts)"
            ),
            {"id": "01K0FILE000000000000000001", "ws": _WS, "msg": _MSG, "ch": _CH,
             "sha": "a" * 64, "ts": _TS},
        )
        conn.execute(
            text(
                "INSERT INTO landing_batches (id, workspace_id, channel_id, kind, content_hash, "
                "source_ref, confirmed_by, status, created_at) VALUES "
                "(:id, :ws, :ch, 'decomp', :h, 'prop-1', :by, 'running', :ts)"
            ),
            {"id": "01K0BTCH000000000000000001", "ws": _WS, "ch": _CH, "h": "b" * 64,
             "by": "01K0MMBR000000000000000001", "ts": _TS},
        )
        conn.execute(
            text(
                "INSERT INTO ledger_entries (op_id, request_hash, batch_id, kind, payload, "
                "created_at) VALUES (:op, :rh, :bid, 'create_task', '{}', :ts)"
            ),
            {"op": "decomp:01K0BTCH000000000000000001:done", "rh": "c" * 64,
             "bid": "01K0BTCH000000000000000001", "ts": _TS},
        )
        conn.execute(
            text(
                "INSERT INTO diagnostic_events (workspace_id, agent_member_id, type, payload, "
                "created_at) VALUES (:ws, :am, 'guard.held', '{}', :ts)"
            ),
            {"ws": _WS, "am": _MEMBER_AGENT, "ts": _TS},
        )
        conn.execute(
            text(
                "INSERT INTO tasks (id, workspace_id, channel_id, number, root_message_id, "
                "title, created_by_member_id, status_changed_at, created_at) VALUES "
                "(:id, :ws, :ch, 1, :msg, 'T1', :by, :ts, :ts)"
            ),
            {"id": "01K0TASK000000000000000001", "ws": _WS, "ch": _CH, "msg": _MSG,
             "by": "01K0MMBR000000000000000001", "ts": _TS},
        )
        conn.execute(
            text(
                "INSERT INTO task_events (task_id, kind, created_at) VALUES (:tid, 'claim', :ts)"
            ),
            {"tid": "01K0TASK000000000000000001", "ts": _TS},
        )


# (表, UPDATE 语句, DELETE 语句)
_CASES = [
    ("messages", f"UPDATE messages SET body='x' WHERE id='{_MSG}'",
     f"DELETE FROM messages WHERE id='{_MSG}'"),
    ("files", "UPDATE files SET name='x' WHERE id='01K0FILE000000000000000001'",
     "DELETE FROM files WHERE id='01K0FILE000000000000000001'"),
    ("ledger_entries", "UPDATE ledger_entries SET kind='x' WHERE seq=1",
     "DELETE FROM ledger_entries WHERE seq=1"),
    ("diagnostic_events", "UPDATE diagnostic_events SET type='x' WHERE seq=1",
     "DELETE FROM diagnostic_events WHERE seq=1"),
    ("token_usage_events",
     "UPDATE token_usage_events SET input_tokens=9 WHERE id='01K0TKNE000000000000000001'",
     "DELETE FROM token_usage_events WHERE id='01K0TKNE000000000000000001'"),
    ("task_events", "UPDATE task_events SET kind='x' WHERE seq=1",
     "DELETE FROM task_events WHERE seq=1"),
]


@pytest.fixture
def seeded_engine(migrated_engine: Engine) -> Engine:
    seed_database(migrated_engine)
    _insert_extra_immutable_rows(migrated_engine)
    return migrated_engine


def _expect_abort(engine: Engine, sql: str) -> None:
    with pytest.raises((OperationalError, IntegrityError)):
        with engine.begin() as conn:
            conn.execute(text(sql))


@pytest.mark.parametrize("table,update_sql,delete_sql", _CASES, ids=[c[0] for c in _CASES])
def test_immutable_table_rejects_update_and_delete(
    seeded_engine: Engine, table: str, update_sql: str, delete_sql: str
) -> None:
    _expect_abort(seeded_engine, update_sql)
    _expect_abort(seeded_engine, delete_sql)
