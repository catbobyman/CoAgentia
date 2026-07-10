"""id 游标分页的共用形状（契约 B §4.4）。M2 新端点 files/activity 复用，去重两份相同切片逻辑。

游标 = id 在**当前结果集内**的位置（与 M1 messages/tasks 同范式）。已知限制（挂账，与
list_tasks 同类）：若 after 行因过滤/删改在翻页间离开结果集，退化为从头翻可能重发首页——
低影响（MVP 单人、量小），keyset 化（按 (created_at,id) SQL 比较）留后续统一整改。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from coagentia_contracts import rest


def cursor_page(
    rows: list[dict[str, Any]],
    after: str | None,
    limit: int,
    serialize: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    """已材料化（且已排序）的行 → {items, next_cursor}。after=id 往后翻，limit 截断上限。"""
    ids = [r["id"] for r in rows]
    if after and after in ids:
        rows = rows[ids.index(after) + 1 :]
    limit = min(max(1, limit), rest.PAGE_MAX_LIMIT)
    page, tail = rows[:limit], rows[limit:]
    next_cursor = page[-1]["id"] if tail and page else None
    return {"items": [serialize(r) for r in page], "next_cursor": next_cursor}
