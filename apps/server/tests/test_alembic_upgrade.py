"""空库 upgrade head 成功、17 表齐、downgrade 可回。"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from coagentia_contracts.enums import DeploymentStatus, PreviewStatus
from coagentia_server.db.engine import make_engine
from coagentia_server.db.models import (
    DEPLOYMENT_ACTIVE_STATUSES,
    IMMUTABLE_TABLES,
    M6A_TABLES,
    M6B_TABLES,
    M7A_TABLES,
    M7B_TABLES,
    PREVIEW_ACTIVE_STATUSES,
)
from sqlalchemy import Column, MetaData, String, Table, inspect, select

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
# 契约 A v1.0.8 §5 M6a 批次（0008；proposals 明确留给 0009）。
M6A_EXPECTED_TABLES = {"projects", "channel_projects", "worktrees"}
# 契约 A v1.0.10 §5 M6b 批次（0009：proposals + agent_role_templates 两张 + agents 加列）。
M6B_EXPECTED_TABLES = {"proposals", "agent_role_templates"}
# 契约 A v1.0.11 §5 M7a 批次（0010：preview_sessions 一张，纯新表）。
M7A_EXPECTED_TABLES = {"preview_sessions"}
# 契约 A v1.5 §5 M7b 批次（0011：deployments 一张，纯新表）。
M7B_EXPECTED_TABLES = {"deployments"}


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
    assert M6A_EXPECTED_TABLES.isdisjoint(mid)  # 0001 不得泄漏建出 M6a 表
    assert "messages_fts" not in mid
    command.upgrade(alembic_cfg, "head")
    final = _table_names(db_url)
    assert (
        M1_EXPECTED_TABLES | M2_EXPECTED_TABLES | M3_EXPECTED_TABLES
        | M4_EXPECTED_TABLES | M5_EXPECTED_TABLES | M6A_EXPECTED_TABLES
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


def test_upgrade_head_creates_m6a_tables_and_task_columns(
    db_url: str, alembic_cfg: Config
) -> None:
    assert set(M6A_TABLES) == M6A_EXPECTED_TABLES
    command.upgrade(alembic_cfg, "head")
    engine = make_engine(url=db_url)
    try:
        insp = inspect(engine)
        project_cols = {c["name"] for c in insp.get_columns("projects")}
        binding_pk = insp.get_pk_constraint("channel_projects")["constrained_columns"]
        worktree_cols = {c["name"] for c in insp.get_columns("worktrees")}
        task_cols = {c["name"]: c for c in insp.get_columns("tasks")}
        worktree_unique = {
            tuple(item["column_names"]) for item in insp.get_unique_constraints("worktrees")
        }
    finally:
        engine.dispose()

    assert M6A_EXPECTED_TABLES <= _table_names(db_url)
    assert project_cols == {
        "id", "workspace_id", "computer_id", "name", "repo_path", "dev_command",
        "deploy_command", "preview_idle_min", "worktree_keep_days", "created_at",
    }
    assert binding_pk == ["channel_id", "project_id"]
    assert worktree_cols == {
        "id", "workspace_id", "project_id", "task_id", "branch", "path", "status",
        "merge_commit", "created_at", "merged_at", "cleaned_at",
    }
    assert ("task_id",) in worktree_unique
    assert {"project_id", "writes_code"} <= set(task_cols)
    assert task_cols["project_id"]["nullable"] is True
    assert task_cols["writes_code"]["nullable"] is False
    assert str(task_cols["writes_code"]["default"]) == "0"


def test_incremental_m5_schema_adds_task_columns_and_preserves_rows(
    db_url: str, alembic_cfg: Config
) -> None:
    """历史 M5 schema 切片：revision=0007，tasks 无 M6 两列；0008 原位补列并保行。"""
    metadata = MetaData()
    workspaces = Table("workspaces", metadata, Column("id", String(26), primary_key=True))
    computers = Table("computers", metadata, Column("id", String(26), primary_key=True))
    channels = Table("channels", metadata, Column("id", String(26), primary_key=True))
    tasks = Table("tasks", metadata, Column("id", String(26), primary_key=True))
    # agents 是 M1 表（真实 M5 库恒有）；0009 对其加 role_template_key，故此切片须含 agents 桩。
    agents = Table("agents", metadata, Column("member_id", String(26), primary_key=True))
    alembic_version = Table(
        "alembic_version", metadata, Column("version_num", String(32), primary_key=True)
    )
    engine = make_engine(url=db_url)
    try:
        metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(workspaces.insert().values(id="ws"))
            conn.execute(computers.insert().values(id="computer"))
            conn.execute(channels.insert().values(id="channel"))
            conn.execute(tasks.insert().values(id="existing-task"))
            conn.execute(agents.insert().values(member_id="existing-agent"))
            conn.execute(alembic_version.insert().values(version_num="0007_m5"))
    finally:
        engine.dispose()

    command.upgrade(alembic_cfg, "head")
    engine = make_engine(url=db_url)
    try:
        columns = {c["name"] for c in inspect(engine).get_columns("tasks")}
        agent_columns = {c["name"] for c in inspect(engine).get_columns("agents")}
        with engine.connect() as conn:
            row = conn.execute(
                select(tasks.c.id).where(tasks.c.id == "existing-task")
            ).one()
            values = conn.exec_driver_sql(
                "SELECT project_id, writes_code FROM tasks WHERE id='existing-task'"
            ).one()
            agent_role = conn.exec_driver_sql(
                "SELECT role_template_key FROM agents WHERE member_id='existing-agent'"
            ).one()
    finally:
        engine.dispose()
    assert {"project_id", "writes_code"} <= columns
    assert "role_template_key" in agent_columns  # 0009 原位补列
    assert row.id == "existing-task"
    assert values == (None, 0)
    assert agent_role == (None,)  # 既有 agent 行加列默认 NULL，零回归
    assert M6A_EXPECTED_TABLES <= _table_names(db_url)


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
    assert M6A_EXPECTED_TABLES.isdisjoint(mid)  # 0002 不得泄漏建出 M6a 表
    command.upgrade(alembic_cfg, "head")
    final = _table_names(db_url)
    assert (
        M1_EXPECTED_TABLES | M2_EXPECTED_TABLES | M3_EXPECTED_TABLES
        | M4_EXPECTED_TABLES | M5_EXPECTED_TABLES | M6A_EXPECTED_TABLES
    ) <= final


def test_incremental_from_0005_to_head(db_url: str, alembic_cfg: Config) -> None:
    # 增量路径：先到 0005（M1+M2+M3+FTS trigram 库），再升 head——模拟线上 M3 库升 M4（F1 出口）。
    command.upgrade(alembic_cfg, "0005_fts_trigram")
    mid = _table_names(db_url)
    assert (M1_EXPECTED_TABLES | M2_EXPECTED_TABLES | M3_EXPECTED_TABLES) <= mid
    assert M4_EXPECTED_TABLES.isdisjoint(mid)   # 0005 不得泄漏建出 M4 表（坑1 回归守门）
    assert M5_EXPECTED_TABLES.isdisjoint(mid)   # 0005 不得泄漏建出 M5 表（坑1 回归守门）
    assert M6A_EXPECTED_TABLES.isdisjoint(mid)  # 0005 不得泄漏建出 M6a 表
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
    assert M6A_EXPECTED_TABLES.isdisjoint(mid)  # 0006 不得泄漏建出 M6a 表
    command.upgrade(alembic_cfg, "head")
    final = _table_names(db_url)
    assert M5_EXPECTED_TABLES <= final          # 0007 增量建出 templates + notification_settings
    assert M6A_EXPECTED_TABLES <= final         # 0008 增量建出 Project/Worktree 三表


def test_upgrade_head_creates_m6b_tables_and_agent_column(
    db_url: str, alembic_cfg: Config
) -> None:
    """0009 从零建 proposals + agent_role_templates 两张 + agents 加 role_template_key 列。"""
    assert set(M6B_TABLES) == M6B_EXPECTED_TABLES
    command.upgrade(alembic_cfg, "head")
    engine = make_engine(url=db_url)
    try:
        insp = inspect(engine)
        proposal_cols = {c["name"] for c in insp.get_columns("proposals")}
        role_cols = {c["name"] for c in insp.get_columns("agent_role_templates")}
        agent_cols = {c["name"] for c in insp.get_columns("agents")}
    finally:
        engine.dispose()
    assert M6B_EXPECTED_TABLES <= _table_names(db_url)
    assert proposal_cols == {
        "id", "workspace_id", "channel_id", "source_task_id", "kind", "revision",
        "status", "body", "proposal_hash", "base_hash", "landed_hash", "adjustments",
        "repair_count", "proposed_by_member_id", "created_at", "updated_at",
    }
    assert role_cols == {"id", "key", "name", "description_prefill", "prompt_sections", "builtin"}
    assert "role_template_key" in agent_cols


def test_proposals_active_source_partial_index_enforced(
    db_url: str, alembic_cfg: Config
) -> None:
    """部分唯一索引「同 source 单一非终态提案」：第二个非终态提案被拒；终态可共存。"""
    import pytest as _pytest
    from sqlalchemy import text as _sql
    from sqlalchemy.exc import IntegrityError

    command.upgrade(alembic_cfg, "head")
    engine = make_engine(url=db_url)
    seed = (
        "INSERT INTO workspaces (id, name, slug, created_at) VALUES ('ws','w','w-slug','t')",
        "INSERT INTO members (id, workspace_id, kind, name, created_at) "
        "VALUES ('orch','ws','agent','Orch','t')",
        "INSERT INTO channels (id, workspace_id, kind, created_at) "
        "VALUES ('ch','ws','channel','t')",
        "INSERT INTO messages (id, workspace_id, channel_id, body, created_at) "
        "VALUES ('m1','ws','ch','need','t')",
        "INSERT INTO tasks (id, workspace_id, channel_id, number, root_message_id, title, "
        " status, level, created_by_member_id, status_changed_at, created_at) "
        "VALUES ('tk','ws','ch',1,'m1','T','todo','l1','orch','t','t')",
    )
    ins = _sql(
        "INSERT INTO proposals "
        "(id, workspace_id, channel_id, source_task_id, kind, revision, status, body, "
        " proposal_hash, adjustments, repair_count, proposed_by_member_id, created_at, updated_at) "
        "VALUES (:id, 'ws', 'ch', 'tk', 'full', 1, :status, '{}', '', '[]', 0, 'orch', 't', 't')"
    )
    try:
        with engine.begin() as conn:
            for stmt in seed:
                conn.execute(_sql(stmt))
            conn.execute(ins, {"id": "p1", "status": "drafting"})
        with engine.begin() as conn:
            with _pytest.raises(IntegrityError):
                conn.execute(ins, {"id": "p2", "status": "awaiting_confirm"})
        with engine.begin() as conn:
            # 把 p1 置终态后可再建活动提案（终态落在 sqlite_where 外）。
            conn.execute(_sql("UPDATE proposals SET status='superseded' WHERE id='p1'"))
            conn.execute(ins, {"id": "p3", "status": "drafting"})
    finally:
        engine.dispose()


def test_incremental_from_0008_to_head(db_url: str, alembic_cfg: Config) -> None:
    # 增量路径：先到 0008（M1..M6a 库），再升 head——模拟线上 M6a 库升 M6b。
    # 注：从零 create_all 读实时 Agent 模型故 agents 在 0001 即带 role_template_key 列；
    # 「真实旧库无此列 → 加列」的路径由 M5 切片桩测覆盖，本测只守 M6b 表批次不泄漏 + 建齐。
    command.upgrade(alembic_cfg, "0008_m6a")
    mid = _table_names(db_url)
    assert M6A_EXPECTED_TABLES <= mid
    assert M6B_EXPECTED_TABLES.isdisjoint(mid)  # 0008 不得泄漏建出 M6b 表
    assert M7A_EXPECTED_TABLES.isdisjoint(mid)  # 0008 不得泄漏建出 M7a 表
    command.upgrade(alembic_cfg, "head")
    final = _table_names(db_url)
    assert M6B_EXPECTED_TABLES <= final
    engine = make_engine(url=db_url)
    try:
        assert "role_template_key" in {
            c["name"] for c in inspect(engine).get_columns("agents")
        }
    finally:
        engine.dispose()


def _preview_index_names(db_url: str) -> set[str]:
    """直查 sqlite_master 取 preview_sessions 的索引名集（部分索引亦可见，
    对齐 held_drafts 表达式索引的直查手法——inspect().get_indexes() 对部分/表达式索引不稳）。"""
    from sqlalchemy import text as _sql

    engine = make_engine(url=db_url)
    try:
        with engine.connect() as conn:
            return {
                r[0]
                for r in conn.execute(
                    _sql(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='index' AND tbl_name='preview_sessions'"
                    )
                )
            }
    finally:
        engine.dispose()


def _preview_active_index_sql(db_url: str) -> str:
    """取活跃部分唯一索引的建索引 SQL（含 WHERE 谓词），供 CR-10 同型断言核对字面量。"""
    from sqlalchemy import text as _sql

    engine = make_engine(url=db_url)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                _sql(
                    "SELECT sql FROM sqlite_master WHERE type='index' "
                    "AND name='ix_preview_sessions_task_active'"
                )
            ).one()
    finally:
        engine.dispose()
    return row[0]


def test_upgrade_head_creates_m7a_preview_sessions(db_url: str, alembic_cfg: Config) -> None:
    """0010 从零建 preview_sessions（fail_log_tail 全列 + 活跃部分唯一索引 + status 读面索引）。"""
    assert set(M7A_TABLES) == M7A_EXPECTED_TABLES
    command.upgrade(alembic_cfg, "head")
    engine = make_engine(url=db_url)
    try:
        preview_cols = {c["name"] for c in inspect(engine).get_columns("preview_sessions")}
    finally:
        engine.dispose()
    assert M7A_EXPECTED_TABLES <= _table_names(db_url)
    assert preview_cols == {
        "id", "workspace_id", "task_id", "worktree_id", "port", "status",
        "fail_log_tail", "started_at", "last_active_at", "recycled_at",
    }
    assert {
        "ix_preview_sessions_task_active", "ix_preview_sessions_status"
    } <= _preview_index_names(db_url)


def test_incremental_from_0009_to_head(db_url: str, alembic_cfg: Config) -> None:
    # 增量路径：先到 0009（M1..M6b 库），再升 head——模拟线上 M6b 库升 M7a（K1 出口）。
    command.upgrade(alembic_cfg, "0009_m6b")
    mid = _table_names(db_url)
    assert (
        M1_EXPECTED_TABLES | M2_EXPECTED_TABLES | M3_EXPECTED_TABLES | M4_EXPECTED_TABLES
        | M5_EXPECTED_TABLES | M6A_EXPECTED_TABLES | M6B_EXPECTED_TABLES
    ) <= mid
    assert M7A_EXPECTED_TABLES.isdisjoint(mid)   # 0009 不得泄漏建出 M7a 表（坑1 回归守门）
    command.upgrade(alembic_cfg, "head")
    final = _table_names(db_url)
    assert M7A_EXPECTED_TABLES <= final          # 0010 增量建出 preview_sessions


def test_preview_sessions_active_index_enforces_uniqueness(
    db_url: str, alembic_cfg: Config
) -> None:
    """活跃部分唯一索引兜底：同 task_id 第二个活跃行（status='running'）被拒；
    一活跃行（running）+ 一终态行（recycled）可共存——终态落在 sqlite_where 外不占唯一。
    FK 强制为 ON，故先播完整父链（workspace→computer→member→channel→message→task→
    project→worktree），preview_sessions 三 FK（workspace/task/worktree）方满足。"""
    import pytest as _pytest
    from sqlalchemy import text as _sql
    from sqlalchemy.exc import IntegrityError

    command.upgrade(alembic_cfg, "head")
    engine = make_engine(url=db_url)
    seed = (
        "INSERT INTO workspaces (id, name, slug, created_at) VALUES ('ws','w','w-slug','t')",
        "INSERT INTO computers (id, workspace_id, name, api_key_hash, created_at) "
        "VALUES ('cmp','ws','C1','hash','t')",
        "INSERT INTO members (id, workspace_id, kind, name, created_at) "
        "VALUES ('mem','ws','agent','A1','t')",
        "INSERT INTO channels (id, workspace_id, kind, created_at) "
        "VALUES ('ch','ws','channel','t')",
        "INSERT INTO messages (id, workspace_id, channel_id, body, created_at) "
        "VALUES ('m1','ws','ch','need','t')",
        "INSERT INTO tasks (id, workspace_id, channel_id, number, root_message_id, title, "
        " status, level, created_by_member_id, status_changed_at, created_at) "
        "VALUES ('tk','ws','ch',1,'m1','T','todo','l1','mem','t','t')",
        "INSERT INTO projects (id, workspace_id, computer_id, name, repo_path, created_at) "
        "VALUES ('pr','ws','cmp','P1','/repo','t')",
        "INSERT INTO worktrees (id, workspace_id, project_id, task_id, branch, path, status, "
        " created_at) VALUES ('wt','ws','pr','tk','feat','/wt','active','t')",
    )
    ins = _sql(
        "INSERT INTO preview_sessions "
        "(id, workspace_id, task_id, worktree_id, status, started_at) "
        "VALUES (:id, 'ws', 'tk', 'wt', :status, 't')"
    )
    try:
        with engine.begin() as conn:
            for stmt in seed:
                conn.execute(_sql(stmt))
            conn.execute(ins, {"id": "pv1", "status": "running"})  # 首个活跃行成功
        with engine.begin() as conn:
            # 同 task_id 第二个活跃行（starting 亦属活跃）被拒
            with _pytest.raises(IntegrityError):
                conn.execute(ins, {"id": "pv2", "status": "starting"})
        with engine.begin() as conn:
            # 终态行（recycled）落在部分索引 sqlite_where 外，可与活跃行共存
            conn.execute(ins, {"id": "pv3", "status": "recycled"})
    finally:
        engine.dispose()


def test_preview_active_statuses_align_with_contract(
    db_url: str, alembic_cfg: Config
) -> None:
    """CR-10 同型断言：把「活跃预览状态字面量集」的三处来源钉在一起，任一漂移即红——
    ① ORM 模块常量 PREVIEW_ACTIVE_STATUSES；② 契约 contracts.enums.PreviewStatus 的非终态子集；
    ③ 实际落库的部分唯一索引 WHERE 谓词。避免 held_drafts/proposals 式硬编码双源无人守。"""
    contract_active = {PreviewStatus.STARTING.value, PreviewStatus.RUNNING.value}
    contract_terminal = {PreviewStatus.RECYCLED.value, PreviewStatus.FAILED.value}
    all_states = {s.value for s in PreviewStatus}

    # ① 模块常量 == 契约非终态子集；活跃 ∪ 终态 == 全集，且两两不交（终态判定亦被钉）。
    assert set(PREVIEW_ACTIVE_STATUSES) == contract_active
    assert contract_active | contract_terminal == all_states
    assert contract_active.isdisjoint(contract_terminal)

    # ③ 实际索引谓词只含活跃字面量，绝不含终态字面量（防手改索引 SQL 漂移）。
    command.upgrade(alembic_cfg, "head")
    index_sql = _preview_active_index_sql(db_url)
    for literal in contract_active:
        assert f"'{literal}'" in index_sql
    for literal in contract_terminal:
        assert f"'{literal}'" not in index_sql


def _deployment_index_sql(db_url: str, name: str) -> str:
    from sqlalchemy import text as _sql

    engine = make_engine(url=db_url)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                _sql(
                    "SELECT sql FROM sqlite_master WHERE type='index' "
                    "AND name=:name"
                ),
                {"name": name},
            ).one()
    finally:
        engine.dispose()
    return row[0]


def test_upgrade_head_creates_m7b_deployments(db_url: str, alembic_cfg: Config) -> None:
    """0011 从零建 deployments（全列 + 活跃部分唯一索引 + project_status 读面索引）。"""
    assert set(M7B_TABLES) == M7B_EXPECTED_TABLES
    command.upgrade(alembic_cfg, "head")
    engine = make_engine(url=db_url)
    try:
        cols = {c["name"] for c in inspect(engine).get_columns("deployments")}
        index_names = {ix["name"] for ix in inspect(engine).get_indexes("deployments")}
    finally:
        engine.dispose()
    assert M7B_EXPECTED_TABLES <= _table_names(db_url)
    assert cols == {
        "id", "workspace_id", "project_id", "triggered_by_member_id", "branch",
        "commit_hash", "command", "status", "exit_code", "url", "log_path",
        "token_summary", "started_at", "finished_at", "created_at",
    }
    assert {"uq_deployments_active_project", "ix_deployments_project_status"} <= index_names


def test_incremental_from_0010_to_head(db_url: str, alembic_cfg: Config) -> None:
    # 增量路径：先到 0010（M1..M7a 库），再升 head——模拟线上 M7a 库升 M7b（K1 出口）。
    command.upgrade(alembic_cfg, "0010_m7a")
    mid = _table_names(db_url)
    assert M7A_EXPECTED_TABLES <= mid
    assert M7B_EXPECTED_TABLES.isdisjoint(mid)  # 0010 不得泄漏建出 M7b 表（坑1 回归守门）
    command.upgrade(alembic_cfg, "head")
    assert M7B_EXPECTED_TABLES <= _table_names(db_url)


def test_deployments_active_index_enforces_uniqueness(
    db_url: str, alembic_cfg: Config
) -> None:
    """活跃部分唯一索引兜底：同 project_id 第二个活跃行（queued）被拒；一活跃行 + 一终态行
    （success）可共存（终态落在 sqlite_where 外，不占唯一）。"""
    import pytest as _pytest
    from sqlalchemy import text as _sql
    from sqlalchemy.exc import IntegrityError

    command.upgrade(alembic_cfg, "head")
    engine = make_engine(url=db_url)
    seed = (
        "INSERT INTO workspaces (id, name, slug, created_at) VALUES ('ws','w','w-slug','t')",
        "INSERT INTO computers (id, workspace_id, name, api_key_hash, created_at) "
        "VALUES ('cmp','ws','C1','hash','t')",
        "INSERT INTO members (id, workspace_id, kind, name, created_at) "
        "VALUES ('mem','ws','human','O','t')",
        "INSERT INTO projects (id, workspace_id, computer_id, name, repo_path, created_at) "
        "VALUES ('pr','ws','cmp','P1','/repo','t')",
    )
    ins = _sql(
        "INSERT INTO deployments "
        "(id, workspace_id, project_id, triggered_by_member_id, branch, command, "
        " status, created_at) "
        "VALUES (:id, 'ws', 'pr', 'mem', 'main', 'deploy', :status, 't')"
    )
    try:
        with engine.begin() as conn:
            for stmt in seed:
                conn.execute(_sql(stmt))
            conn.execute(ins, {"id": "dp1", "status": "running"})  # 首个活跃行成功
        with engine.begin() as conn:
            with _pytest.raises(IntegrityError):  # 同 project 第二个活跃行被拒
                conn.execute(ins, {"id": "dp2", "status": "queued"})
        with engine.begin() as conn:
            conn.execute(ins, {"id": "dp3", "status": "success"})  # 终态可共存
    finally:
        engine.dispose()


def test_deployment_active_statuses_align_with_contract(
    db_url: str, alembic_cfg: Config
) -> None:
    """CR-10 同型断言：把「活跃部署状态字面量集」的三处来源钉在一起——① ORM 常量
    DEPLOYMENT_ACTIVE_STATUSES；② 契约 DeploymentStatus 非终态子集；③ 落库索引 WHERE 谓词。"""
    contract_active = {DeploymentStatus.QUEUED.value, DeploymentStatus.RUNNING.value}
    contract_terminal = {DeploymentStatus.SUCCESS.value, DeploymentStatus.FAILED.value}
    all_states = {s.value for s in DeploymentStatus}

    assert set(DEPLOYMENT_ACTIVE_STATUSES) == contract_active
    assert contract_active | contract_terminal == all_states
    assert contract_active.isdisjoint(contract_terminal)

    command.upgrade(alembic_cfg, "head")
    index_sql = _deployment_index_sql(db_url, "uq_deployments_active_project")
    for literal in contract_active:
        assert f"'{literal}'" in index_sql
    for literal in contract_terminal:
        assert f"'{literal}'" not in index_sql


def test_downgrade_base_drops_tables(db_url: str, alembic_cfg: Config, tmp_path: Path) -> None:
    command.upgrade(alembic_cfg, "head")
    command.downgrade(alembic_cfg, "base")
    names = _table_names(db_url)
    assert M1_EXPECTED_TABLES.isdisjoint(names)
    assert M2_EXPECTED_TABLES.isdisjoint(names)
    assert M3_EXPECTED_TABLES.isdisjoint(names)
    assert M4_EXPECTED_TABLES.isdisjoint(names)
    assert M5_EXPECTED_TABLES.isdisjoint(names)
    assert M6A_EXPECTED_TABLES.isdisjoint(names)
    assert M6B_EXPECTED_TABLES.isdisjoint(names)
    assert M7A_EXPECTED_TABLES.isdisjoint(names)
    assert M7B_EXPECTED_TABLES.isdisjoint(names)
    assert "messages_fts" not in names
