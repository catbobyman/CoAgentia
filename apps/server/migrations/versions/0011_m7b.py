"""M7b 建表（契约 A v1.5 §4.9 / B §13.2）。

新表只取 M7B_TABLES 显式子集（纪律 9 显式点名，勿 metadata.create_all 全集）：
deployments 一张（含 token_summary JSON + log_path + 「同 project_id 单活跃部分唯一索引」
uq_deployments_active_project）。本批纯新表，无既有表加列/改列，故无反射-补列逻辑
（对比 0008/0009 的 tasks/agents 增量补列）。__table_args__ 的部分唯一索引与读面索引随
create_all 一并落库（沿 0010 preview_sessions 先例）。

Revision ID: 0011_m7b
Revises: 0010_m7a
Create Date: 2026-07-13
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from coagentia_server.db import models

revision: str = "0011_m7b"
down_revision: str | None = "0010_m7a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _m7b_tables() -> list:
    return [models.Base.metadata.tables[name] for name in models.M7B_TABLES]


def upgrade() -> None:
    models.Base.metadata.create_all(bind=op.get_bind(), tables=_m7b_tables())


def downgrade() -> None:
    models.Base.metadata.drop_all(bind=op.get_bind(), tables=_m7b_tables())
