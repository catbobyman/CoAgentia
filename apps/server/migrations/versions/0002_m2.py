"""M2 建表：tasks / task_events / message_task_refs / activity_items + messages_fts（FTS5）
+ task_events 不可变触发器（契约 A §1 第 6 张；§4.2 FTS external-content）。

只建 M2_TABLES 显式子集——与 0001 只建 M1_TABLES 对称，避免 create_all 读实时 metadata
连带建表（坑1）。task_events 触发器在此补齐（0001 只建 M1 五张，坑2）。

Revision ID: 0002_m2
Revises: 0001_m1_initial
Create Date: 2026-07-09
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from coagentia_server.db import models

revision: str = "0002_m2"
down_revision: str | None = "0001_m1_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FTS_CREATE = (
    "CREATE VIRTUAL TABLE messages_fts USING fts5("
    "body, content='messages', content_rowid='rowid')"
)
_FTS_TRIGGER = (
    "CREATE TRIGGER messages_fts_ai AFTER INSERT ON messages BEGIN "
    "INSERT INTO messages_fts(rowid, body) VALUES (new.rowid, new.body); END;"
)
_FTS_REBUILD = "INSERT INTO messages_fts(messages_fts) VALUES('rebuild')"


def _immutable_trigger_ddl(table: str) -> tuple[str, str]:
    # 与 0001 同构（迁移自包含，不跨文件 import 私有 helper）
    upd = (
        f"CREATE TRIGGER trg_{table}_no_update BEFORE UPDATE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, '{table} is immutable'); END;"
    )
    dele = (
        f"CREATE TRIGGER trg_{table}_no_delete BEFORE DELETE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, '{table} is immutable'); END;"
    )
    return upd, dele


def _m2_tables() -> list:
    return [models.Base.metadata.tables[name] for name in models.M2_TABLES]


def upgrade() -> None:
    bind = op.get_bind()
    # ① 4 张 M2 表（create_all 在子集内拓扑排序：tasks 先于 task_events/refs/activity）
    models.Base.metadata.create_all(bind=bind, tables=_m2_tables())
    # ② FTS5 虚表 + 与 messages INSERT 同步触发器 + 存量回填（增量升级时 M1 消息补索引）
    op.execute(_FTS_CREATE)
    op.execute(_FTS_TRIGGER)
    op.execute(_FTS_REBUILD)
    # ③ task_events 不可变触发器（第 6 张，坑2 补齐）
    for table in models.M2_IMMUTABLE_TABLES:
        for ddl in _immutable_trigger_ddl(table):
            op.execute(ddl)


def downgrade() -> None:
    # 对称清理：先触发器/虚表，后建表（drop_all 反拓扑先删子表）
    for table in models.M2_IMMUTABLE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_update")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_delete")
    op.execute("DROP TRIGGER IF EXISTS messages_fts_ai")
    op.execute("DROP TABLE IF EXISTS messages_fts")  # 影子表随之自动清除
    models.Base.metadata.drop_all(bind=op.get_bind(), tables=_m2_tables())
