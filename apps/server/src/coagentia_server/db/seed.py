"""Seed 加载器：读 packages/fixtures/seed.json 的 M1 子集，INSERT 进库。

dev 工具 + pytest fixture 双用途。M1 子集 = workspace / computers / members / agents /
channels / channel_members / messages / message_mentions / read_positions /
token_usage_events / canvases（agent_skills/reminders/ledger/landing_batches 无种子行；
tasks/presence 非 M1 表跳过）。插入顺序满足外键依赖。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import Table, insert
from sqlalchemy.engine import Engine

from coagentia_server.db import models

# packages/fixtures/seed.json（apps/server/src/coagentia_server/db → 上溯 5 层到 coagentia 根）
FIXTURES = Path(__file__).resolve().parents[5] / "packages" / "fixtures"
SEED_PATH = FIXTURES / "seed.json"

def _table(name: str) -> Table:
    return models.Base.metadata.tables[name]


# (seed.json 键, 表名, 是否单行) —— 顺序 = 外键依赖拓扑
_PLAN: tuple[tuple[str, str, bool], ...] = (
    ("workspace", "workspaces", True),
    ("computers", "computers", False),
    ("members", "members", False),
    ("agents", "agents", False),
    ("channels", "channels", False),
    ("channel_members", "channel_members", False),
    ("messages", "messages", False),
    ("message_mentions", "message_mentions", False),
    ("read_positions", "read_positions", False),
    ("token_usage_events", "token_usage_events", False),
    ("canvases", "canvases", False),
)


def load_seed_dict(path: Path | str = SEED_PATH) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def seed_database(engine: Engine, seed: dict[str, Any] | None = None) -> dict[str, int]:
    """把 M1 子集灌进已建表的库；返回各表插入行数。"""
    data = seed if seed is not None else load_seed_dict()
    counts: dict[str, int] = {}
    with engine.begin() as conn:
        for key, table_name, single in _PLAN:
            if key not in data:
                continue
            table = _table(table_name)
            rows = [data[key]] if single else list(data[key])
            if rows:
                conn.execute(insert(table), rows)
            counts[table_name] = len(rows)
    return counts
