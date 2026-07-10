"""messages_fts 分词器由 unicode61 改 trigram（契约 A §10.4 收口）。

背景：M2 的 0002 用 FTS5 默认 unicode61 分词器建 messages_fts，把**连续 CJK 串**当作单一
token（body="修复登录页面的崩溃" → 整串一个 token），子串（≥3 字）MATCH 不命中。trigram 分词器
按 3 字符滑窗切分，支持任意 ≥3 字符子串检索（含 CJK），故切换之。

做法：DROP 现有 messages_fts（含同步触发器）→ 以 tokenize='trigram' 重建同构 external-content
虚表 → rebuild 从 messages 内容表回填 → 重建 AFTER INSERT 同步触发器。列/影子结构与 0002 完全同构，
仅分词器不同；GET /search 形状不变（契约 B §8.2 / §9.6）。

坑1（幂等/双路）：0002 总先建 messages_fts（unicode61），本迁移必在其后运行，故 DROP 用 IF EXISTS
防御即可。从零路径（0002 建 unicode61 → 本迁移改 trigram）与线上 M2 库增量升级双路都收敛到 trigram。
DROP TABLE messages_fts 会连带清除全部影子表（_data/_idx/_config/_docsize），rebuild 重新回填。

downgrade 反向：DROP trigram 虚表 → 以默认 unicode61 重建 → rebuild 回填 → 重挂触发器（等价 0002）。

Revision ID: 0005_fts_trigram
Revises: 0004_files_indexes
Create Date: 2026-07-10
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005_fts_trigram"
down_revision: str | None = "0004_files_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 同步触发器：messages INSERT → 影子表同步（与 0002 逐字同构，迁移自包含不跨文件 import）。
_FTS_TRIGGER = (
    "CREATE TRIGGER messages_fts_ai AFTER INSERT ON messages BEGIN "
    "INSERT INTO messages_fts(rowid, body) VALUES (new.rowid, new.body); END;"
)
_FTS_REBUILD = "INSERT INTO messages_fts(messages_fts) VALUES('rebuild')"

# trigram：3 字符滑窗，支持 ≥3 字符子串（含连续 CJK）MATCH（契约 A §10.4）。
_FTS_CREATE_TRIGRAM = (
    "CREATE VIRTUAL TABLE messages_fts USING fts5("
    "body, content='messages', content_rowid='rowid', tokenize='trigram')"
)
# unicode61：FTS5 默认分词器（downgrade 复原，与 0002 建法一致）。
_FTS_CREATE_UNICODE61 = (
    "CREATE VIRTUAL TABLE messages_fts USING fts5("
    "body, content='messages', content_rowid='rowid')"
)


def _rebuild_fts(create_ddl: str) -> None:
    """拆旧建新：先删同步触发器 + 虚表（连带影子表），再以 create_ddl 重建 + 回填 + 重挂触发器。"""
    op.execute("DROP TRIGGER IF EXISTS messages_fts_ai")
    op.execute("DROP TABLE IF EXISTS messages_fts")
    op.execute(create_ddl)
    op.execute(_FTS_REBUILD)  # 从 messages 内容表回填存量
    op.execute(_FTS_TRIGGER)


def upgrade() -> None:
    _rebuild_fts(_FTS_CREATE_TRIGRAM)


def downgrade() -> None:
    _rebuild_fts(_FTS_CREATE_UNICODE61)
