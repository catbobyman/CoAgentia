"""SQL keyset 分页（契约 B §4.4）——挂账批2 统一整改（2026-07-10）。

旧范式（材料化全量后 Python 切片 + 游标按"id 在结果集内位置"定位）的两个挂账一并收口：
① after 行因过滤在翻页间离开结果集 → 静默从头翻重发首页（list_tasks 最易触发）；
② activity/files 无 SQL LIMIT，owner 全量 activity / 全频道文件材料化后才截断。

新范式：游标仍是裸 id（对外形状不变）；服务端按 PK 回查游标行取 (created_at, id) 做
SQL 行值比较（SQLite ≥3.15 row-value，本库已要求 ≥3.35——allocate_number RETURNING）。
四个列表读面（messages/tasks/files/activity）的行均无删除路径，游标行必存在；未知游标
沿旧行为宽容忽略（从头翻）。排序保持 (created_at, id) 复合键——不改用裸 id 排序，避免
seed/回填数据 created_at 与 ULID 时间位不同序的边角。LIMIT limit+1 下推 SQL：多取一行
只为判定 next_cursor。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from coagentia_contracts import rest
from sqlalchemy import Connection, Select, Table, select, tuple_


def _anchor(conn: Connection, table: Table, cursor: str | None) -> tuple[Any, Any] | None:
    """游标 id → (created_at, id) 行值锚点；未知 id → None（宽容忽略）。"""
    if not cursor:
        return None
    row = conn.execute(
        select(table.c.created_at, table.c.id).where(table.c.id == cursor)
    ).first()
    return (row[0], row[1]) if row is not None else None


def keyset_page(
    conn: Connection,
    table: Table,
    stmt: Select,
    *,
    after: str | None = None,
    before: str | None = None,
    limit: int,
    desc: bool = False,
    serialize: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    """带过滤的 stmt（无 order/limit）→ {items, next_cursor}，keyset 下推 SQL。

    - after：沿排序方向往后翻（asc 端点=更新，desc 端点=更旧）。
    - before：仅 messages 用。单独出现时 = **紧邻窗口回翻**（取 before 前最近 limit 行，
      仍按端点排序方向返回；next_cursor = 窗口最旧 id，作下一次 before 续翻）；与 after
      同时出现时退化为范围上界。
    """
    limit = min(max(1, limit), rest.PAGE_MAX_LIMIT)
    key = tuple_(table.c.created_at, table.c.id)
    a = _anchor(conn, table, after)
    b = _anchor(conn, table, before)
    if a is not None:
        stmt = stmt.where(key < a if desc else key > a)
    if b is not None:
        stmt = stmt.where(key > b if desc else key < b)
    # 紧邻回翻：只给 before 时反向取窗（否则拿到的是结果集头部而非 before 的邻近页）。
    flip = b is not None and a is None
    eff_desc = desc ^ flip
    order = (
        (table.c.created_at.desc(), table.c.id.desc())
        if eff_desc
        else (table.c.created_at, table.c.id)
    )
    rows = [
        dict(r) for r in conn.execute(stmt.order_by(*order).limit(limit + 1)).mappings()
    ]
    more = len(rows) > limit
    page = rows[:limit]
    if flip:
        page.reverse()
        next_cursor = page[0]["id"] if more and page else None
    else:
        next_cursor = page[-1]["id"] if more and page else None
    return {"items": [serialize(r) for r in page], "next_cursor": next_cursor}
