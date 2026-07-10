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


def _m1_tables() -> list:
    return [models.Base.metadata.tables[name] for name in models.M1_TABLES]


def upgrade() -> None:
    bind = op.get_bind()
    # 步骤 4：只建 M1 的 17 张（含约束/索引），源自 models.Base.metadata。
    # 显式点名 tables=——否则 create_all 读实时全集会连带建出 M2 表（坑1）。
    models.Base.metadata.create_all(bind=bind, tables=_m1_tables())
    # 步骤 5：M1 五张不可变表的 BEFORE UPDATE/DELETE 触发器（body 仅 RAISE(ABORT)）。
    # task_events 是第 6 张，但它 0002 才建，故此处只遍历 M1_IMMUTABLE_TABLES（坑2）。
    for table in models.M1_IMMUTABLE_TABLES:
        for ddl in _immutable_trigger_ddl(table):
            op.execute(ddl)


def downgrade() -> None:
    for table in models.M1_IMMUTABLE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_update")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_delete")
    models.Base.metadata.drop_all(bind=op.get_bind(), tables=_m1_tables())
