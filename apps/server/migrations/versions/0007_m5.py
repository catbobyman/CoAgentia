"""M5 建表：templates + channel_notification_settings（契约 A §4.10/§4.2，两张一次建齐）。

只建 M5_TABLES 显式子集——与 0001/0002/0003/0006 对称，避免 create_all 读实时 metadata
连带建表（坑1）。两表均**可变**（templates builtin 启动 upsert / notification_settings mode
会 UPDATE），故无禁 UPDATE/DELETE 触发器，也无 FTS 虚表，本迁移只有 create_all/drop_all 一步。

templates 工作区级小表零额外索引（列表恒按 workspace_id 全量拉）；channel_notification_settings
复合 PK (channel_id, member_id) 即查询键，索引随 PrimaryKeyConstraint 一并建出——均由
__table_args__/列声明承载，无需 op.create_index 手写（同 0006 靠 metadata 建索引）。

坑1（无需 if_not_exists）：两表不在 M1/M2/M3/M4_TABLES，故先前各迁移的 create_all(tables=子集)
均不会泄漏建出本批表——从零 upgrade head 与 M4→M5 增量升级双路都只由本迁移建这两张表。

templates 块 a 期间空置（H1 一次建齐、H5 才写入；M3"迁移不拆两次"先例）。

Revision ID: 0007_m5
Revises: 0006_m4_held_drafts
Create Date: 2026-07-11
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from coagentia_server.db import models

revision: str = "0007_m5"
down_revision: str | None = "0006_m4_held_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _m5_tables() -> list:
    return [models.Base.metadata.tables[name] for name in models.M5_TABLES]


def upgrade() -> None:
    # templates + channel_notification_settings 两张表（无跨表 FK，create_all 子集内建齐）。
    models.Base.metadata.create_all(bind=op.get_bind(), tables=_m5_tables())


def downgrade() -> None:
    # 对称清理（drop_all 连带删除表上索引/约束）。
    models.Base.metadata.drop_all(bind=op.get_bind(), tables=_m5_tables())
