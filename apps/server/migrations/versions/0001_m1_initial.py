"""M1 首迁移：建 17 张表（契约 A §5 首行清单）+ 五张不可变表禁 UPDATE/DELETE 触发器。

全量 DDL = db/models.py 的 metadata（唯一形状源）+ 本文件的原生触发器 SQL（契约 A §1 不可变表）。

Revision ID: 0001_m1_initial
Revises:
Create Date: 2026-07-09
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from coagentia_server.db import models

revision: str = "0001_m1_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _immutable_trigger_ddl(table: str) -> tuple[str, str]:
    upd = (
        f"CREATE TRIGGER trg_{table}_no_update BEFORE UPDATE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, '{table} is immutable'); END;"
    )
    dele = (
        f"CREATE TRIGGER trg_{table}_no_delete BEFORE DELETE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, '{table} is immutable'); END;"
    )
    return upd, dele


def upgrade() -> None:
    bind = op.get_bind()
    # 步骤 4：17 张表全量 DDL（含约束/索引），源自 models.Base.metadata。
    models.Base.metadata.create_all(bind=bind)
    # 步骤 5：五张不可变表的 BEFORE UPDATE/DELETE 触发器（body 仅 RAISE(ABORT)）。
    for table in models.IMMUTABLE_TABLES:
        for ddl in _immutable_trigger_ddl(table):
            op.execute(ddl)


def downgrade() -> None:
    for table in models.IMMUTABLE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_update")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_delete")
    models.Base.metadata.drop_all(bind=op.get_bind())
