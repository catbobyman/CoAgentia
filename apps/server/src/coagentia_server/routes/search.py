"""全局搜索（契约 B §9.6 / §4.8）：GET /search 三分组 = jumps / messages(FTS) / tasks。

- jumps：频道名 / 成员名子串命中（NOCASE），用于 Ctrl-K 快速跳转。
- messages：messages_fts（FTS5 external-content，body=第 0 列，trigram）MATCH + snippet() «»高亮；
  <3 字符查询 trigram 切不出 token → 退化为正文 LIKE 子扫（手工 «»片段），兜住 1~2 字 CJK 子串。
- tasks：title 子串 ∪ 锚点消息 FTS 命中（root_message_id）去重。

FTS 注入防护：用户 q 包成一条带引号的 FTS5 短语（内部双引号翻倍转义），既避免 `AND`/`*`/`:`
等 FTS 语法字符被当运算符，也把整串当一个短语匹配（可预期、稳定）。

中文分词收口（2026-07-10，契约 A §10.4）：messages_fts 已由 unicode61 改 **trigram**（0005 迁移）。
trigram 按 3 字符滑窗切分，**≥3 字符**子串（含连续 CJK，如 "番茄钟"）经 MATCH 直接命中——修掉了
unicode61「连续 CJK 当单一 token、子串不命中」的老问题。唯一边角：trigram 对 **<3 字符**查询切不出
token（MATCH 恒空），故 1~2 字 CJK 子串（如 "番茄"）走正文 LIKE 兜底子扫。见 test_fts_trigram.py。
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

_CHANNEL = models.tbl(models.Channel)
_MEMBER = models.tbl(models.Member)
_TASK = models.tbl(models.Task)
_MESSAGE = models.tbl(models.Message)

_SEARCH_LIMIT_MAX = 25
_SEARCH_LIMIT_DEFAULT = 10

# trigram 分词器的下限：<3 字符切不出 token，MATCH 恒空 → 该长度走 LIKE 兜底子扫。
_TRIGRAM_MIN = 3
# LIKE 兜底片段窗口（按字符计，命中子串两侧各取 N 字符），精神对齐 FTS snippet() 的 12 token 窗口。
_LIKE_SNIPPET_WINDOW = 12

# messages 表列名（用于把 text() 原始 SQL 的 m.* 行还原成 MessagePublic 形状，剔 snippet 辅助列）。
_MSG_COLS = [c.name for c in _MESSAGE.c]


def _fts_phrase(q: str) -> str:
    """把用户 q 包成 FTS5 短语字面量（双引号翻倍转义），避免语法字符注入。"""
    return '"' + q.replace('"', '""') + '"'


def _like_pattern(q: str) -> str:
    """LIKE 子串模式：转义 `\\ % _` 元字符（配 `escape="\\"` 用），使 q 当**字面量**子串。

    否则用户查 `%`/`_` 会被当通配符匹配全部行（如 q='%' → 命中所有非空正文）。
    """
    esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


def _like_snippet(body: str, q: str) -> str:
    """LIKE 兜底路径的手工片段：命中子串包 «»，两侧按窗口截断并加 … 省略号。

    对齐 MATCH 路径 snippet() 的 «»…形状，使响应形状不随 <3 字符查询而变（契约 B §9.6.3）。
    """
    idx = body.lower().find(q.lower())
    if idx < 0:  # 理论不达（进此函数前已 LIKE 命中）；兜底给正文首段。
        head = body[: _LIKE_SNIPPET_WINDOW * 2]
        return head + ("…" if len(body) > len(head) else "")
    start, end = idx, idx + len(q)
    pre = body[max(0, start - _LIKE_SNIPPET_WINDOW) : start]
    post = body[end : end + _LIKE_SNIPPET_WINDOW]
    prefix = "…" if start > _LIKE_SNIPPET_WINDOW else ""
    suffix = "…" if end + _LIKE_SNIPPET_WINDOW < len(body) else ""
    return f"{prefix}{pre}«{body[start:end]}»{post}{suffix}"


def _messages_via_like(
    tx: Tx,
    q: str,
    from_member: str | None,
    in_channel: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """<3 字符查询的正文 LIKE 兜底子扫：走 messages 表本身（trigram MATCH 此长度恒空）。

    过滤/排序/limit 与 MATCH 路径一致；snippet 手工构造。命中消息同为读面 → 附 files（A v1.0.4）。
    """
    stmt = select(_MESSAGE).where(_MESSAGE.c.body.ilike(_like_pattern(q), escape="\\"))
    if from_member is not None:
        stmt = stmt.where(_MESSAGE.c.author_member_id == from_member)
    if in_channel is not None:
        stmt = stmt.where(_MESSAGE.c.channel_id == in_channel)
    stmt = stmt.order_by(_MESSAGE.c.created_at.desc(), _MESSAGE.c.id.desc()).limit(limit)
    rows = [dict(r) for r in tx.conn.execute(stmt).mappings()]
    fmap = files_by_message(tx, [m["id"] for m in rows])
    return [
        {
            "message": message_public(m, fmap.get(m["id"], [])),
            "snippet": _like_snippet(m["body"], q),
        }
        for m in rows
    ]


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

    like = _like_pattern(q)  # 转义元字符（配 escape="\\"），q 当字面量子串
    phrase = _fts_phrase(q)

    # ---- jumps：频道名 / 成员名子串（NOCASE；DM name=None 天然被 LIKE 排除）。
    result["jumps"]["channels"] = [
        channel_public(dict(r))
        for r in tx.conn.execute(
            select(_CHANNEL)
            .where(_CHANNEL.c.name.ilike(like, escape="\\"))
            .order_by(_CHANNEL.c.created_at, _CHANNEL.c.id)
            .limit(limit)
        ).mappings()
    ]
    result["jumps"]["members"] = [
        member_public(dict(r))
        for r in tx.conn.execute(
            select(_MEMBER)
            .where(_MEMBER.c.removed_at.is_(None), _MEMBER.c.name.ilike(like, escape="\\"))
            .order_by(_MEMBER.c.created_at, _MEMBER.c.id)
            .limit(limit)
        ).mappings()
    ]

    # ---- messages：FTS5 trigram MATCH + snippet()；<3 字符走 LIKE 兜底。kind=task 时跳过。
    if kind is not SearchKind.TASK and len(q) < _TRIGRAM_MIN:
        result["messages"] = _messages_via_like(tx, q, from_member, in_channel, limit)
    elif kind is not SearchKind.TASK:
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
                # 单扫（K7）：直接投影 _MSG_COLS（已剔 snip）+ 取 snip，免旧「dict(row) 全列复制 →
                # pop → 再推导 _MSG_COLS」的逐行双扫；输出逐字等价（_MSG_COLS 不含 snip 辅助列）。
                hits.append(({k: row[k] for k in _MSG_COLS}, row["snip"]))
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
            .where(_TASK.c.title.ilike(like, escape="\\"))
            .order_by(_TASK.c.created_at, _TASK.c.id)
            .limit(limit)
        ).mappings():
            merged[r["id"]] = dict(r)
        # 锚点消息命中：≥3 字走 trigram MATCH；<3 字 trigram 恒空 → 锚点正文 LIKE 兜底
        # （与 messages 组同口径，否则短 CJK 子串只在 title 命中的任务会漏，B §10.4）。
        if len(q) < _TRIGRAM_MIN:
            for r in tx.conn.execute(
                select(_TASK)
                .select_from(_MESSAGE.join(_TASK, _TASK.c.root_message_id == _MESSAGE.c.id))
                .where(_MESSAGE.c.body.ilike(_like_pattern(q), escape="\\"))
                .order_by(_TASK.c.created_at, _TASK.c.id)
                .limit(limit)
            ).mappings():
                merged.setdefault(r["id"], dict(r))
        else:
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
