"""M3 建表：task_contracts / canvas_nodes / canvas_edges（契约 A §4.3/§4.4）。

只建 M3_TABLES 显式子集——与 0001/0002 对称，避免 create_all 读实时 metadata
连带建表（坑1）。三表均可变（无禁 UPDATE/DELETE 触发器），也无 FTS 虚表，
故本迁移只有 create_all/drop_all 一步，无需 op.execute 原生 DDL。

Revision ID: 0003_m3
Revises: 0002_m2
Create Date: 2026-07-10
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from coagentia_server.db import models

revision: str = "0003_m3"
down_revision: str | None = "0002_m2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _m3_tables() -> list:
    return [models.Base.metadata.tables[name] for name in models.M3_TABLES]


def upgrade() -> None:
    bind = op.get_bind()
    # 3 张 M3 表（create_all 在子集内拓扑排序：canvas_nodes 先于 canvas_edges 的 FK）
    models.Base.metadata.create_all(bind=bind, tables=_m3_tables())


def downgrade() -> None:
    # 对称清理（drop_all 反拓扑先删子表：canvas_edges 先于 canvas_nodes）
    models.Base.metadata.drop_all(bind=op.get_bind(), tables=_m3_tables())
