"""B-1 ②′：canvas_nodes 加 suggested_owner 列（解锁主动唤醒的持久源）。

第四例既有表加列（沿 0008 tasks / 0009 agents / 0012 canvas_nodes.upstream_policy 先例）——历史
0001 使用实时 metadata create_all，从零升级时 canvas_nodes 可能已带该列；真实增量库则没有。因此先
反射列名、缺失才 ADD，使从零与增量两路收敛到同一结构。列可空、无 server_default（既有节点=NULL，
无建议人即解锁时不 @，语义安全）；本批纯 schema、零行为变更，写入归 landing、读取归 hub 解锁扫描。

Revision ID: 0013_b1_suggested_owner
Revises: 0012_m8
Create Date: 2026-07-15
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_b1_suggested_owner"
down_revision: str | None = "0012_m8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _canvas_node_columns() -> set[str]:
    return {col["name"] for col in sa.inspect(op.get_bind()).get_columns("canvas_nodes")}


def upgrade() -> None:
    # FK + String(26) 与 live model 对齐（沿 0008 tasks.project_id add_column 先例）——否则从零
    # （0001 metadata.create_all 建 FK 列）与增量（此处 add_column）两路 schema 分叉（FK 有无）。
    # inline_references=True：SQLite 无 ALTER 加约束，FK 必须内联进 ADD COLUMN（否则 alembic 走
    # add_constraint 报 NotImplementedError；沿 0008 先例，downgrade 靠 batch 重建移除内联 FK）。
    if "suggested_owner" not in _canvas_node_columns():
        op.add_column(
            "canvas_nodes",
            sa.Column(
                "suggested_owner",
                sa.String(length=26),
                sa.ForeignKey("members.id"),
                nullable=True,
            ),
            inline_references=True,
        )


def downgrade() -> None:
    if "suggested_owner" in _canvas_node_columns():
        with op.batch_alter_table("canvas_nodes", recreate="always") as batch_op:
            batch_op.drop_column("suggested_owner")
