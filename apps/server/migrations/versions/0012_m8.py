"""M8 建表与 canvas_nodes 加列（契约 A v1.0.12）。

新表只取 M8_TABLES 显式子集（纪律 9 显式点名，勿 metadata.create_all 全集）：
summary_runs（O8 汇总协调状态，task_id 为 PK）。canvas_nodes.upstream_policy 是第三例既有表
加列（沿 0008 tasks / 0009 agents 加列先例）——历史 0001 使用实时 metadata create_all，从零升级
时 canvas_nodes 可能已带该列；真实增量库则没有。因此先反射列名、缺失才 ADD，使从零与增量两路
收敛到同一结构。列默认 'strict'（现状 satisfied 语义），本批纯 schema、零行为变更；W9 双档内核
与 landing 默认 partial 归 M8b L7 消费。

Revision ID: 0012_m8
Revises: 0011_m7b
Create Date: 2026-07-14
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from coagentia_server.db import models

revision: str = "0012_m8"
down_revision: str | None = "0011_m7b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _m8_tables() -> list:
    return [models.Base.metadata.tables[name] for name in models.M8_TABLES]


def _canvas_node_columns() -> set[str]:
    return {col["name"] for col in sa.inspect(op.get_bind()).get_columns("canvas_nodes")}


def upgrade() -> None:
    models.Base.metadata.create_all(bind=op.get_bind(), tables=_m8_tables())

    if "upstream_policy" not in _canvas_node_columns():
        op.add_column(
            "canvas_nodes",
            sa.Column(
                "upstream_policy",
                sa.Text(),
                nullable=False,
                server_default=sa.text("'strict'"),
            ),
        )


def downgrade() -> None:
    if "upstream_policy" in _canvas_node_columns():
        with op.batch_alter_table("canvas_nodes", recreate="always") as batch_op:
            batch_op.drop_column("upstream_policy")
    models.Base.metadata.drop_all(bind=op.get_bind(), tables=_m8_tables())
