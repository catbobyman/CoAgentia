"""空库 upgrade head 成功、17 表齐、downgrade 可回。"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from coagentia_server.db.engine import make_engine
from coagentia_server.db.models import IMMUTABLE_TABLES
from sqlalchemy import inspect

# 契约 A §5 M1 首行清单（17 张）。
M1_EXPECTED_TABLES = {
    "workspaces", "computers", "members", "agents", "agent_skills",
    "channels", "channel_members", "messages", "message_mentions", "files",
    "read_positions", "reminders", "diagnostic_events", "token_usage_events",
    "ledger_entries", "landing_batches", "canvases",
}
# 契约 A §5 M2 批次（4 张）。
M2_EXPECTED_TABLES = {"tasks", "task_events", "message_task_refs", "activity_items"}
# 契约 A §5 M3 批次（3 张）。
M3_EXPECTED_TABLES = {"task_contracts", "canvas_nodes", "canvas_edges"}
# 契约 A §5 M4 批次（1 张：held_drafts，0006）。
M4_EXPECTED_TABLES = {"held_drafts"}
# 契约 A §5 M5 批次（2 张：templates + channel_notification_settings，0007）。
M5_EXPECTED_TABLES = {"templates", "channel_notification_settings"}


def _table_names(url: str) -> set[str]:
    engine = make_engine(url=url)
    try:
        names = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    return names


def test_upgrade_head_creates_17_tables(db_url: str, alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    names = _table_names(db_url)
    assert M1_EXPECTED_TABLES <= names
    assert len(M1_EXPECTED_TABLES) == 17


def test_upgrade_creates_immutable_triggers(db_url: str, alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    engine = make_engine(url=db_url)
    try:
        from sqlalchemy import text

        with engine.connect() as conn:
            trigs = {
                r[0]
                for r in conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='trigger'")
                )
            }
    finally:
        engine.dispose()
    for table in IMMUTABLE_TABLES:
        assert f"trg_{table}_no_update" in trigs
        assert f"trg_{table}_no_delete" in trigs


def test_upgrade_head_creates_m2_tables_and_fts(db_url: str, alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    names = _table_names(db_url)
    assert M2_EXPECTED_TABLES <= names
    assert "messages_fts" in names          # FTS5 虚表
    assert "messages_fts_data" in names     # 影子表存在 = 建成


def test_incremental_upgrade_from_0001_to_head(db_url: str, alembic_cfg: Config) -> None:
    # 增量路径：先到 0001（M1 库），再升 head——模拟线上 M1 库升 M2/M3
    command.upgrade(alembic_cfg, "0001_m1_initial")
    mid = _table_names(db_url)
    assert M1_EXPECTED_TABLES <= mid
    assert M2_EXPECTED_TABLES.isdisjoint(mid)   # 0001 不得泄漏建出 M2 表（坑1 回归守门）
    assert M3_EXPECTED_TABLES.isdisjoint(mid)   # 0001 不得泄漏建出 M3 表（坑1 回归守门）
    assert M4_EXPECTED_TABLES.isdisjoint(mid)   # 0001 不得泄漏建出 M4 表（坑1 回归守门）
    assert M5_EXPECTED_TABLES.isdisjoint(mid)   # 0001 不得泄漏建出 M5 表（坑1 回归守门）
    assert "messages_fts" not in mid
    command.upgrade(alembic_cfg, "head")
    final = _table_names(db_url)
    assert (
        M1_EXPECTED_TABLES | M2_EXPECTED_TABLES | M3_EXPECTED_TABLES
        | M4_EXPECTED_TABLES | M5_EXPECTED_TABLES
    ) <= final
    assert "messages_fts" in final


def test_upgrade_head_creates_m3_tables(db_url: str, alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    names = _table_names(db_url)
    assert M3_EXPECTED_TABLES <= names
    assert len(M3_EXPECTED_TABLES) == 3


def test_upgrade_head_creates_m4_held_drafts(db_url: str, alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    names = _table_names(db_url)
    assert M4_EXPECTED_TABLES <= names
    assert len(M4_EXPECTED_TABLES) == 1


def test_upgrade_head_creates_m5_tables(db_url: str, alembic_cfg: Config) -> None:
    # 契约 A §5 M5 批次（templates + channel_notification_settings，0007）从零建齐。
    command.upgrade(alembic_cfg, "head")
    names = _table_names(db_url)
    assert M5_EXPECTED_TABLES <= names
    assert len(M5_EXPECTED_TABLES) == 2


def test_upgrade_head_creates_held_drafts_active_index(db_url: str, alembic_cfg: Config) -> None:
    # 活动行分区唯一索引（COALESCE(thread_root_id,'') 表达式 + sqlite_where）随 create_all 建出；
    # 读面索引 ix_held_drafts_status 同表随之。二者存在 = __table_args__ 声明已落库（0006 出口）。
    # 直查 sqlite_master：inspect().get_indexes() 会跳过表达式索引（COALESCE），反射不到 uq。
    from sqlalchemy import text as _sql

    command.upgrade(alembic_cfg, "head")
    engine = make_engine(url=db_url)
    try:
        with engine.connect() as conn:
            index_names = {
                r[0]
                for r in conn.execute(
                    _sql(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='index' AND tbl_name='held_drafts'"
                    )
                )
            }
    finally:
        engine.dispose()
    assert {"uq_held_drafts_active", "ix_held_drafts_status"} <= index_names


def test_held_drafts_active_index_enforces_uniqueness(db_url: str, alembic_cfg: Config) -> None:
    """分区唯一索引兜底：同 (agent, channel, COALESCE(thread,'')) 第二个活动行插入被拒；
    thread_root_id 空亦经 COALESCE 归一为 ''，故空 thread 的重发也命中同一唯一键（v1.0.5 兜底）。
    终态行（status 不在 held/reevaluating）落在 sqlite_where 外，不受约束（可多行）。
    FK 强制为 ON，故先播最小父行（workspace/agent 成员/channel）。"""
    import pytest as _pytest
    from sqlalchemy import text as _sql
    from sqlalchemy.exc import IntegrityError

    command.upgrade(alembic_cfg, "head")
    engine = make_engine(url=db_url)
    seed = (
        "INSERT INTO workspaces (id, name, slug, created_at) VALUES ('ws','w','w-slug','t')",
        "INSERT INTO members (id, workspace_id, kind, name, created_at) "
        "VALUES ('ag','ws','agent','A1','t')",
        "INSERT INTO channels (id, workspace_id, kind, created_at) "
        "VALUES ('ch','ws','channel','t')",
    )
    ins = _sql(
        "INSERT INTO held_drafts "
        "(id, workspace_id, agent_member_id, channel_id, thread_root_id, draft_body, "
        " reasons, status, held_count, next_reeval_at, created_at) "
        "VALUES (:id, 'ws', 'ag', 'ch', :thread, 'd', '{}', :status, 1, 't', 't')"
    )
    try:
        with engine.begin() as conn:
            for stmt in seed:
                conn.execute(_sql(stmt))
            # 首个活动行（thread=NULL）成功
            conn.execute(ins, {"id": "h1", "thread": None, "status": "held"})
        with engine.begin() as conn:
            # 同 (ag, ch, COALESCE(NULL,'')='') 的第二个活动行（reevaluating 亦属活动）被拒
            with _pytest.raises(IntegrityError):
                conn.execute(ins, {"id": "h2", "thread": None, "status": "reevaluating"})
        with engine.begin() as conn:
            # 终态行（resolved）落在部分索引 sqlite_where 外，可与活动行共存
            conn.execute(ins, {"id": "h3", "thread": None, "status": "resolved"})
    finally:
        engine.dispose()


def test_m5_tables_columns_and_pk(db_url: str, alembic_cfg: Config) -> None:
    # M5 两表列面与 PK 对照契约 A §4.10/§4.2：templates 全列齐、builtin 默认 0；
    # channel_notification_settings 复合 PK (channel_id, member_id) + mode 默认 'all'。
    command.upgrade(alembic_cfg, "head")
    engine = make_engine(url=db_url)
    try:
        insp = inspect(engine)
        tmpl_cols = {c["name"] for c in insp.get_columns("templates")}
        cns_cols = {c["name"]: c for c in insp.get_columns("channel_notification_settings")}
        cns_pk = insp.get_pk_constraint("channel_notification_settings")
    finally:
        engine.dispose()
    assert tmpl_cols == {
        "id", "workspace_id", "name", "description", "body",
        "builtin", "created_by_member_id", "created_at",
    }
    assert set(cns_cols) == {"channel_id", "member_id", "mode"}
    # 复合 PK 两列（顺序 = channel_id, member_id）。
    assert cns_pk["constrained_columns"] == ["channel_id", "member_id"]
    # mode 默认 'all'（SQLite server_default 反射带引号）。
    assert "all" in str(cns_cols["mode"]["default"])


def test_upgrade_head_creates_files_indexes(db_url: str, alembic_cfg: Config) -> None:
    # 0004：files 二级索引（消息读面派生 files 批查 + 频道文件页签游标）
    command.upgrade(alembic_cfg, "head")
    engine = make_engine(url=db_url)
    try:
        index_names = {ix["name"] for ix in inspect(engine).get_indexes("files")}
    finally:
        engine.dispose()
    assert {"ix_files_message", "ix_files_channel"} <= index_names


def test_incremental_from_0002_to_head(db_url: str, alembic_cfg: Config) -> None:
    # 增量路径：先到 0002（M1+M2 库），再升 head——模拟线上 M2 库升 M3
    command.upgrade(alembic_cfg, "0002_m2")
    mid = _table_names(db_url)
    assert (M1_EXPECTED_TABLES | M2_EXPECTED_TABLES) <= mid
    assert M3_EXPECTED_TABLES.isdisjoint(mid)   # 0002 不得泄漏建出 M3 表（坑1 回归守门）
    assert M4_EXPECTED_TABLES.isdisjoint(mid)   # 0002 不得泄漏建出 M4 表（坑1 回归守门）
    assert M5_EXPECTED_TABLES.isdisjoint(mid)   # 0002 不得泄漏建出 M5 表（坑1 回归守门）
    command.upgrade(alembic_cfg, "head")
    final = _table_names(db_url)
    assert (
        M1_EXPECTED_TABLES | M2_EXPECTED_TABLES | M3_EXPECTED_TABLES
        | M4_EXPECTED_TABLES | M5_EXPECTED_TABLES
    ) <= final


def test_incremental_from_0005_to_head(db_url: str, alembic_cfg: Config) -> None:
    # 增量路径：先到 0005（M1+M2+M3+FTS trigram 库），再升 head——模拟线上 M3 库升 M4（F1 出口）。
    command.upgrade(alembic_cfg, "0005_fts_trigram")
    mid = _table_names(db_url)
    assert (M1_EXPECTED_TABLES | M2_EXPECTED_TABLES | M3_EXPECTED_TABLES) <= mid
    assert M4_EXPECTED_TABLES.isdisjoint(mid)   # 0005 不得泄漏建出 M4 表（坑1 回归守门）
    assert M5_EXPECTED_TABLES.isdisjoint(mid)   # 0005 不得泄漏建出 M5 表（坑1 回归守门）
    command.upgrade(alembic_cfg, "head")
    final = _table_names(db_url)
    assert M4_EXPECTED_TABLES <= final          # 0006 增量建出 held_drafts


def test_incremental_from_0006_to_head(db_url: str, alembic_cfg: Config) -> None:
    # 增量路径：先到 0006（M1..M4 库），再升 head——模拟线上 M4 库升 M5（H1 出口）。
    command.upgrade(alembic_cfg, "0006_m4_held_drafts")
    mid = _table_names(db_url)
    assert (
        M1_EXPECTED_TABLES | M2_EXPECTED_TABLES | M3_EXPECTED_TABLES | M4_EXPECTED_TABLES
    ) <= mid
    assert M5_EXPECTED_TABLES.isdisjoint(mid)   # 0006 不得泄漏建出 M5 表（坑1 回归守门）
    command.upgrade(alembic_cfg, "head")
    final = _table_names(db_url)
    assert M5_EXPECTED_TABLES <= final          # 0007 增量建出 templates + notification_settings


def test_downgrade_base_drops_tables(db_url: str, alembic_cfg: Config, tmp_path: Path) -> None:
    command.upgrade(alembic_cfg, "head")
    command.downgrade(alembic_cfg, "base")
    names = _table_names(db_url)
    assert M1_EXPECTED_TABLES.isdisjoint(names)
    assert M2_EXPECTED_TABLES.isdisjoint(names)
    assert M3_EXPECTED_TABLES.isdisjoint(names)
    assert M4_EXPECTED_TABLES.isdisjoint(names)
    assert M5_EXPECTED_TABLES.isdisjoint(names)
    assert "messages_fts" not in names
