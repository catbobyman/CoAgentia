"""M6a 建表与 tasks 加列（契约 A v1.0.8）。

新表只取 M6A_TABLES 显式子集：projects、channel_projects、worktrees；worktrees
初建即含 merge_commit。tasks.project_id/writes_code 是首例既有表加列。历史 0002
使用实时 metadata，从零升级时可能已带这两列；真实 M5 增量库则没有。因此本迁移先反射
列名，缺失才 ADD，使从零与增量两路收敛到同一结构。

Revision ID: 0008_m6a
Revises: 0007_m5
Create Date: 2026-07-11
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from coagentia_server.db import models

revision: str = "0008_m6a"
down_revision: str | None = "0007_m5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _m6a_tables() -> list:
    return [models.Base.metadata.tables[name] for name in models.M6A_TABLES]


def _task_columns() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns("tasks")}


def upgrade() -> None:
    models.Base.metadata.create_all(bind=op.get_bind(), tables=_m6a_tables())

    columns = _task_columns()
    if "project_id" not in columns:
        op.add_column(
            "tasks",
            sa.Column(
                "project_id",
                sa.String(length=26),
                sa.ForeignKey("projects.id"),
                nullable=True,
            ),
            inline_references=True,
        )
    if "writes_code" not in columns:
        op.add_column(
            "tasks",
            sa.Column(
                "writes_code",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )


def downgrade() -> None:
    columns = _task_columns()
    if {"project_id", "writes_code"} & columns:
        # SQLite DROP COLUMN 不能直接移除带 inline FK 的 project_id；batch 重建一次完成。
        with op.batch_alter_table("tasks", recreate="always") as batch_op:
            if "project_id" in columns:
                batch_op.drop_column("project_id")
            if "writes_code" in columns:
                batch_op.drop_column("writes_code")
    models.Base.metadata.drop_all(bind=op.get_bind(), tables=_m6a_tables())
