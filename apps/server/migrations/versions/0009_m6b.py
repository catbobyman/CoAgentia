"""M6b 建表与 agents 加列（契约 A v1.0.10）。

新表只取 M6B_TABLES 显式子集（纪律 9 显式点名，勿 metadata.create_all 全集）：
proposals（部分唯一索引「同 source 单一非终态提案」）、agent_role_templates（全局字典表）。
agents.role_template_key 是第二例既有表加列（沿 0008 tasks 加列先例）。历史 0001 使用实时
metadata create_all，从零升级时 agents 可能已带该列；真实 M6a 增量库则没有。因此先反射列名、
缺失才 ADD，使从零与增量两路收敛到同一结构。

Revision ID: 0009_m6b
Revises: 0008_m6a
Create Date: 2026-07-12
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from coagentia_server.db import models

revision: str = "0009_m6b"
down_revision: str | None = "0008_m6a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _m6b_tables() -> list:
    return [models.Base.metadata.tables[name] for name in models.M6B_TABLES]


def _agent_columns() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns("agents")}


def upgrade() -> None:
    models.Base.metadata.create_all(bind=op.get_bind(), tables=_m6b_tables())

    if "role_template_key" not in _agent_columns():
        op.add_column(
            "agents",
            sa.Column("role_template_key", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    if "role_template_key" in _agent_columns():
        with op.batch_alter_table("agents", recreate="always") as batch_op:
            batch_op.drop_column("role_template_key")
    models.Base.metadata.drop_all(bind=op.get_bind(), tables=_m6b_tables())
