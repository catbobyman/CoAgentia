"""seed 灌库后关键表行数断言（M1 子集）。"""

from __future__ import annotations

from coagentia_server.db import models
from coagentia_server.db.seed import seed_database
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

_EXPECTED = {
    models.Workspace: 1,
    models.Computer: 1,
    models.Member: 5,
    models.Agent: 4,
    models.Channel: 7,
    models.ChannelMember: 19,
    models.Message: 20,
    models.MessageMention: 3,
    models.ReadPosition: 6,
    models.TokenUsageEvent: 2,
    models.Canvas: 4,
}


def _count(engine: Engine, model: type) -> int:
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(model.__table__)).scalar_one()


def test_seed_load_row_counts(migrated_engine: Engine) -> None:
    counts = seed_database(migrated_engine)
    # 返回值与实查一致
    for model, expected in _EXPECTED.items():
        assert _count(migrated_engine, model) == expected, model.__tablename__
        assert counts[model.__tablename__] == expected


def test_seed_empty_tables_stay_empty(migrated_engine: Engine) -> None:
    seed_database(migrated_engine)
    for model in (models.AgentSkill, models.Reminder, models.LedgerEntry,
                  models.LandingBatch, models.DiagnosticEvent, models.File):
        assert _count(migrated_engine, model) == 0
