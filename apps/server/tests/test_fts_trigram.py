"""FTS trigram 专项（契约 A §10.4 收口，M3b 浮动件）：messages_fts 由 unicode61 改 trigram。

覆盖两条：
1. 迁移层：0005 让 messages_fts 变 trigram 分词器，双路（从零 upgrade head / M2 库增量）都收敛，
   downgrade 复原 unicode61；rebuild 回填存量消息可检索。
2. 路由层（真 server GET /search）：中文子串命中（"番茄"→"番茄钟…"）、英文仍命中、中英混合命中，
   且响应形状不变（三分组 + snippet «»…）。

trigram 边角（实测）：<3 字符查询 trigram 切不出 token → MATCH 恒空，故 search.py 对该长度退化为
正文 LIKE 兜底子扫。本文件同时护栏 ≥3 字（走 MATCH）与 <3 字（走 LIKE）两条路径。
"""

from __future__ import annotations

from alembic import command
from alembic.config import Config
from coagentia_contracts import rest
from coagentia_server.db.engine import make_engine
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

BUILD = "build"


# ------------------------------------------------------------------ 迁移层


def _fts_tokenizer(engine: Engine) -> str:
    """从 sqlite_master 读 messages_fts 建表 SQL 判定分词器（含 trigram 则 trigram，否则 61）。"""
    with engine.connect() as conn:
        sql = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE name='messages_fts'")
        ).scalar_one()
    return "trigram" if "trigram" in sql.lower() else "unicode61"


def test_head_messages_fts_uses_trigram(migrated_engine: Engine) -> None:
    """从零 upgrade head：messages_fts 建表 SQL 带 tokenize='trigram'。"""
    assert _fts_tokenizer(migrated_engine) == "trigram"


def test_incremental_from_0002_flips_to_trigram(db_url: str, alembic_cfg: Config) -> None:
    """M2 库（0002 建的 unicode61）增量升 head → 0005 翻成 trigram。"""
    command.upgrade(alembic_cfg, "0002_m2")
    eng = make_engine(url=db_url)
    try:
        assert _fts_tokenizer(eng) == "unicode61"  # 0002 落地即 unicode61
    finally:
        eng.dispose()
    command.upgrade(alembic_cfg, "head")
    eng = make_engine(url=db_url)
    try:
        assert _fts_tokenizer(eng) == "trigram"
    finally:
        eng.dispose()


def test_downgrade_0005_restores_unicode61(db_url: str, alembic_cfg: Config) -> None:
    """downgrade 0005→0004：messages_fts 复原 unicode61（分词器可逆）。"""
    command.upgrade(alembic_cfg, "head")
    eng = make_engine(url=db_url)
    try:
        assert _fts_tokenizer(eng) == "trigram"
    finally:
        eng.dispose()
    command.downgrade(alembic_cfg, "0004_files_indexes")
    eng = make_engine(url=db_url)
    try:
        assert _fts_tokenizer(eng) == "unicode61"
    finally:
        eng.dispose()


def test_trigram_backfills_existing_messages_via_rebuild(
    db_url: str, alembic_cfg: Config
) -> None:
    """M2 库存量消息在 0005 rebuild 后仍可 trigram 子串检索（回填不丢数据）。"""
    command.upgrade(alembic_cfg, "0002_m2")
    ws = "01K0WKSP000000000000000001"
    ch = "01K0CHAN000000000000000001"
    ts = "2026-07-09T12:00:00.000Z"
    eng = make_engine(url=db_url)
    with eng.begin() as conn:
        conn.execute(
            text("INSERT INTO workspaces (id, name, slug, created_at) VALUES (:i,'w','w',:t)"),
            {"i": ws, "t": ts},
        )
        conn.execute(
            text(
                "INSERT INTO channels (id, workspace_id, kind, name, created_at) "
                "VALUES (:i,:w,'channel','build',:t)"
            ),
            {"i": ch, "w": ws, "t": ts},
        )
        conn.execute(
            text(
                "INSERT INTO messages (id, workspace_id, channel_id, body, created_at) "
                "VALUES (:i,:w,:c,:b,:t)"
            ),
            {"i": "01K0MESG000000000000000030", "w": ws, "c": ch, "b": "番茄钟计时法", "t": ts},
        )
    eng.dispose()
    command.upgrade(alembic_cfg, "head")  # 0005：trigram 重建 + rebuild 回填
    eng = make_engine(url=db_url)
    try:
        with eng.connect() as conn:
            # ≥3 字连续 CJK 子串经 trigram MATCH 命中回填的存量消息。
            hits = [
                r[0]
                for r in conn.execute(
                    text(
                        "SELECT m.id FROM messages_fts f JOIN messages m ON m.rowid=f.rowid "
                        "WHERE messages_fts MATCH :q"
                    ),
                    {"q": "番茄钟"},
                )
            ]
        assert hits == ["01K0MESG000000000000000030"]
    finally:
        eng.dispose()


# ------------------------------------------------------------------ 路由层（真 server）


def _channel(client: TestClient, name: str) -> dict:
    return next(c for c in client.get("/api/channels").json()["items"] if c["name"] == name)


def _post(client: TestClient, channel_id: str, body: str) -> dict:
    r = client.post(f"/api/channels/{channel_id}/messages", json={"body": body})
    assert r.status_code == 201
    return r.json()["message"]


def _search(client: TestClient, q: str, **params: str) -> rest.SearchResponse:
    return rest.SearchResponse.model_validate(
        client.get("/api/search", params={"q": q, **params}).json()
    )


# 注：seed.json 已含一条含 "番茄钟" 的消息，故涉及精确计数的用例改用 seed 中不存在的独特 CJK 词
# （蟠桃/星轨/幽兰谷 等，已核实 seed 零命中）避免干扰；task 的 "番茄" 例子见下方 membership 用例。


def test_search_chinese_number_example_hits_via_like(server_client: TestClient) -> None:
    """task 契约例子：搜 "番茄"（2 字）命中我发的 "番茄钟…" 消息。

    seed 另有一条含 "番茄钟" 的消息，故用 membership 判定我的消息命中，snippet 含 «番茄»。
    """
    build = _channel(server_client, BUILD)["id"]
    posted = _post(server_client, build, "番茄钟工作法帮助专注")
    res = _search(server_client, "番茄")
    ids = [m.message.id for m in res.messages]
    assert posted["id"] in ids, "2 字 CJK 子串应经 LIKE 兜底命中"
    mine = next(m for m in res.messages if m.message.id == posted["id"])
    assert "«番茄»" in mine.snippet  # 形状不变：手工片段同样 «»高亮


def test_search_chinese_3char_substring_via_match(server_client: TestClient) -> None:
    """≥3 字连续 CJK 子串走 trigram MATCH：搜 "蟠桃闹"（3 字）命中 "蟠桃闹钟…"，snippet «»高亮。"""
    build = _channel(server_client, BUILD)["id"]
    posted = _post(server_client, build, "蟠桃闹钟专注法很好用")
    res = _search(server_client, "蟠桃闹")
    assert [m.message.id for m in res.messages] == [posted["id"]]
    assert "«" in res.messages[0].snippet and "»" in res.messages[0].snippet


def test_search_chinese_2char_substring_via_like(server_client: TestClient) -> None:
    """<3 字 CJK 子串走 LIKE 兜底：搜 "星轨"（2 字）命中 "星轨仪…"，snippet 含 «星轨»。"""
    build = _channel(server_client, BUILD)["id"]
    posted = _post(server_client, build, "星轨仪校准记录已归档")
    res = _search(server_client, "星轨")
    assert [m.message.id for m in res.messages] == [posted["id"]]
    assert "«星轨»" in res.messages[0].snippet


def test_search_english_substring_still_hits(server_client: TestClient) -> None:
    """英文仍命中（trigram 大小写不敏感 + 内部子串）：zqxwvu 精确、modoro 子串、大小写变体。"""
    build = _channel(server_client, BUILD)["id"]
    _post(server_client, build, "pomodoro sprint zqxwvu")
    assert len(_search(server_client, "zqxwvu").messages) == 1
    assert len(_search(server_client, "modoro").messages) == 1  # "pomodoro" 内部子串
    assert len(_search(server_client, "POMODORO").messages) == 1  # 大小写不敏感


def test_search_mixed_cjk_ascii_substring_hits(server_client: TestClient) -> None:
    """中英混合子串命中：body 含 "登录bug" → 搜 "录bug"（跨 CJK/ASCII 4 字子串）走 MATCH 命中。"""
    build = _channel(server_client, BUILD)["id"]
    posted = _post(server_client, build, "修复登录bug并上线")
    res = _search(server_client, "录bug")
    assert [m.message.id for m in res.messages] == [posted["id"]]


def test_search_response_shape_unchanged(server_client: TestClient) -> None:
    """响应形状不变：三分组齐全 + messages 项含 message/snippet（契约 B §9.6）。"""
    build = _channel(server_client, BUILD)["id"]
    _post(server_client, build, "幽兰谷回声测试消息")
    raw = server_client.get("/api/search", params={"q": "幽兰谷"}).json()
    assert set(raw.keys()) == {"jumps", "messages", "tasks"}
    assert set(raw["jumps"].keys()) == {"channels", "members"}
    assert raw["messages"], "应有命中"
    assert set(raw["messages"][0].keys()) == {"message", "snippet"}
    rest.SearchResponse.model_validate(raw)  # 契约模型校验通过


def test_search_filters_apply_on_like_fallback(server_client: TestClient) -> None:
    """<3 字 LIKE 兜底同样尊重 in_channel 过滤（与 MATCH 路径一致）。"""
    build = _channel(server_client, BUILD)["id"]
    research = _channel(server_client, "research")["id"]
    _post(server_client, build, "幽兰在 build")
    _post(server_client, research, "幽兰在 research")
    scoped = _search(server_client, "幽兰", in_channel=build)
    assert len(scoped.messages) == 1
    assert scoped.messages[0].message.channel_id == build
