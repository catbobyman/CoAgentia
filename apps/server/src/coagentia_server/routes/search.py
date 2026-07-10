"""全局搜索（契约 B §9.6 / §4.8）：GET /search 三分组 = jumps / messages(FTS) / tasks。

- jumps：频道名 / 成员名子串命中（NOCASE），用于 Ctrl-K 快速跳转。
- messages：messages_fts（FTS5 external-content，列 body=第 0 列）MATCH + snippet() «»高亮。
- tasks：title 子串 ∪ 锚点消息 FTS 命中（root_message_id）去重。

FTS 注入防护：用户 q 包成一条带引号的 FTS5 短语（内部双引号翻倍转义），既避免 `AND`/`*`/`:`
等 FTS 语法字符被当运算符，也把整串当一个短语匹配（可预期、稳定）。

中文分词实测结论（2026-07-09，契约 A §10.4）：messages_fts 用 FTS5 默认 unicode61 分词器，
把**连续 CJK 串**当作单一 token（例如 body="修复登录页面的崩溃" → token 就是整串）。因此
以子串（如 "登录"/"崩溃"）MATCH **不命中**；仅当 CJK 词被空白/标点分隔成独立 token（如
"番茄钟"）时整词查询才命中。**结论：unicode61 对中文子串检索不够用，M3 需换 trigram 分词器
（或引入分词/jieba 预切分）**。见 test_search.py::test_chinese_fts_unicode61_limitation。
"""

from __future__ import annotations

from typing import Any

from coagentia_contracts import rest
from coagentia_contracts.enums import SearchKind
from fastapi import APIRouter, Depends
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from coagentia_server.db import models
from coagentia_server.deps import Tx, get_tx
from coagentia_server.routes.messages import files_by_message
from coagentia_server.routes.serialize import (
    channel_public,
    member_public,
    message_public,
    task_public,
)

router = APIRouter(prefix="/api", tags=["search"])

_CHANNEL = models.Channel.__table__
_MEMBER = models.Member.__table__
_TASK = models.Task.__table__

_SEARCH_LIMIT_MAX = 25
_SEARCH_LIMIT_DEFAULT = 10

# messages 表列名（用于把 text() 原始 SQL 的 m.* 行还原成 MessagePublic 形状，剔 snippet 辅助列）。
_MSG_COLS = [c.name for c in models.Message.__table__.c]


def _fts_phrase(q: str) -> str:
    """把用户 q 包成 FTS5 短语字面量（双引号翻倍转义），避免语法字符注入。"""
    return '"' + q.replace('"', '""') + '"'


@router.get("/search", response_model=rest.SearchResponse)
def search(
    tx: Tx = Depends(get_tx),
    q: str = "",
    from_member: str | None = None,
    in_channel: str | None = None,
    kind: SearchKind | None = None,
    limit: int = _SEARCH_LIMIT_DEFAULT,
) -> Any:
    q = q.strip()
    limit = min(max(1, limit), _SEARCH_LIMIT_MAX)
    result: dict[str, Any] = {
        "jumps": {"channels": [], "members": []},
        "messages": [],
        "tasks": [],
    }
    if not q:  # 空 q → 三分组皆空（B §9.6）
        return result

    like = f"%{q}%"
    phrase = _fts_phrase(q)

    # ---- jumps：频道名 / 成员名子串（NOCASE；DM name=None 天然被 LIKE 排除）。
    result["jumps"]["channels"] = [
        channel_public(dict(r))
        for r in tx.conn.execute(
            select(_CHANNEL)
            .where(_CHANNEL.c.name.ilike(like))
            .order_by(_CHANNEL.c.created_at, _CHANNEL.c.id)
            .limit(limit)
        ).mappings()
    ]
    result["jumps"]["members"] = [
        member_public(dict(r))
        for r in tx.conn.execute(
            select(_MEMBER)
            .where(_MEMBER.c.removed_at.is_(None), _MEMBER.c.name.ilike(like))
            .order_by(_MEMBER.c.created_at, _MEMBER.c.id)
            .limit(limit)
        ).mappings()
    ]

    # ---- messages：FTS5 MATCH + snippet()。kind=task 时跳过。
    if kind is not SearchKind.TASK:
        sql = (
            "SELECT m.*, snippet(messages_fts, 0, '«', '»', '…', 12) AS snip "
            "FROM messages_fts f JOIN messages m ON m.rowid = f.rowid "
            "WHERE messages_fts MATCH :q"
        )
        params: dict[str, Any] = {"q": phrase, "lim": limit}
        if from_member is not None:
            sql += " AND m.author_member_id = :from_member"
            params["from_member"] = from_member
        if in_channel is not None:
            sql += " AND m.channel_id = :in_channel"
            params["in_channel"] = in_channel
        sql += " ORDER BY m.created_at DESC, m.id DESC LIMIT :lim"
        try:
            hits: list[tuple[dict[str, Any], str]] = []
            for row in tx.conn.execute(text(sql), params).mappings():
                d = dict(row)
                d.pop("snip", None)
                hits.append(({k: d[k] for k in _MSG_COLS}, row["snip"]))
            # 命中消息同为读面 → 附着 files（契约 A v1.0.4）。
            fmap = files_by_message(tx, [m["id"] for m, _ in hits])
            result["messages"] = [
                {"message": message_public(m, fmap.get(m["id"], [])), "snippet": snip}
                for m, snip in hits
            ]
        except OperationalError:
            # 病态 FTS 查询（罕见的转义边角）→ 降级空 messages，而不是 500。
            result["messages"] = []

    # ---- tasks：title 子串 ∪ 锚点消息 FTS 命中，按 task.id 去重、保序、截断。
    if kind is not SearchKind.MESSAGE:
        merged: dict[str, dict[str, Any]] = {}
        for r in tx.conn.execute(
            select(_TASK)
            .where(_TASK.c.title.ilike(like))
            .order_by(_TASK.c.created_at, _TASK.c.id)
            .limit(limit)
        ).mappings():
            merged[r["id"]] = dict(r)
        anchor_sql = (
            "SELECT t.* FROM messages_fts f "
            "JOIN messages m ON m.rowid = f.rowid "
            "JOIN tasks t ON t.root_message_id = m.id "
            "WHERE messages_fts MATCH :q "
            "ORDER BY t.created_at, t.id LIMIT :lim"
        )
        try:
            for r in tx.conn.execute(
                text(anchor_sql), {"q": phrase, "lim": limit}
            ).mappings():
                merged.setdefault(r["id"], dict(r))
        except OperationalError:
            pass
        result["tasks"] = [task_public(t) for t in list(merged.values())[:limit]]

    return result
