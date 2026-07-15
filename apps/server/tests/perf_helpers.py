"""K7 查询计数护栏：统计一段代码在给定 Engine 上执行的 SQL 语句条数。

用 SQLAlchemy `before_cursor_execute` 事件计数（每条游标执行 +1），据此把「消除 N+1」钉成
可回归的量化断言——批量预取后查询条数不随集合规模线性增长（O(1)/O(批)，非 O(n)）。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine

_DML_PREFIXES = ("SELECT", "INSERT", "UPDATE", "DELETE", "WITH")


class _Counter:
    def __init__(self) -> None:
        self.count = 0
        self.statements: list[str] = []

    @property
    def dml(self) -> list[str]:
        """仅 SELECT/INSERT/UPDATE/DELETE 语句（滤除 PRAGMA/BEGIN/COMMIT 等噪声，稳态可比）。"""
        return [s for s in self.statements if s.lstrip().upper().startswith(_DML_PREFIXES)]

    @property
    def dml_count(self) -> int:
        return len(self.dml)


@contextmanager
def count_queries(engine: Engine) -> Iterator[_Counter]:
    """with count_queries(engine) as q: ...  → q.count 为块内该 engine 执行的 SQL 条数。"""
    counter = _Counter()

    def _on_exec(
        _conn: Any, _cursor: Any, statement: str, *_rest: Any
    ) -> None:
        counter.count += 1
        counter.statements.append(statement)

    event.listen(engine, "before_cursor_execute", _on_exec)
    try:
        yield counter
    finally:
        event.remove(engine, "before_cursor_execute", _on_exec)
