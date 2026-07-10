"""M4 建表：held_drafts（契约 A §4.5，一张表一次建齐）。

只建 M4_TABLES 显式子集——与 0001/0002/0003 对称，避免 create_all 读实时 metadata
连带建表（坑1）。held_drafts **可变**（status/held_count/resolved_* 会 UPDATE），故无禁
UPDATE/DELETE 触发器，也无 FTS 虚表，本迁移只有 create_all/drop_all 一步。

活动行分区唯一索引 uq_held_drafts_active（COALESCE(thread_root_id,'') 表达式 +
sqlite_where=status IN ('held','reevaluating')，先例 uq_task_contracts_active）与读面索引
ix_held_drafts_status 已声明于 HeldDraft.__table_args__，随 create_all 一并建出——无需
op.create_index 手写（同 0003 靠 metadata 建 uq_task_contracts_active）。

坑1（无需 if_not_exists）：held_drafts 不在 M1/M2/M3_TABLES，故 0001/0002/0003 的
create_all(tables=子集) 均不会泄漏建出本表——从零 upgrade head 与 M3→M4 增量升级双路
都只由本迁移建 held_drafts，建表无需防御性 if_not_exists。

Revision ID: 0006_m4_held_drafts
Revises: 0005_fts_trigram
Create Date: 2026-07-10
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from coagentia_server.db import models

revision: str = "0006_m4_held_drafts"
down_revision: str | None = "0005_fts_trigram"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _m4_tables() -> list:
    return [models.Base.metadata.tables[name] for name in models.M4_TABLES]


def upgrade() -> None:
    # held_drafts 一张表（含 uq_held_drafts_active 分区唯一索引 + ix_held_drafts_status 读面索引，
    # 均由 __table_args__ 声明，create_all 一并建出）。
    models.Base.metadata.create_all(bind=op.get_bind(), tables=_m4_tables())


def downgrade() -> None:
    # 对称清理（drop_all 连带删除表上索引）。
    models.Base.metadata.drop_all(bind=op.get_bind(), tables=_m4_tables())
