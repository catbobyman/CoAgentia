"""files 表二级索引补齐（契约 A v1.0.4 消息读面派生 files）。

- ix_files_message：消息读面按 message_id IN 批查附件（列表/线程/发消息响应/搜索命中）。
- ix_files_channel：频道文件页签按 (channel_id, id) 查询/游标（keyset 整改同批受益）。

坑1 索引变体：0001 经 create_all 按实时 metadata 建 files 表，会连带建出本批新加进
File.__table_args__ 的索引——从零路径到 0004 时索引已存在。故必须 if_not_exists
（从零 = 0001 已建、no-op；增量 = 既有库在此补建），downgrade 对称 if_exists。

Revision ID: 0004_files_indexes
Revises: 0003_m3
Create Date: 2026-07-10
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004_files_indexes"
down_revision: str | None = "0003_m3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_files_message", "files", ["message_id"], if_not_exists=True)
    op.create_index("ix_files_channel", "files", ["channel_id", "id"], if_not_exists=True)


def downgrade() -> None:
    op.drop_index("ix_files_channel", table_name="files", if_exists=True)
    op.drop_index("ix_files_message", table_name="files", if_exists=True)
