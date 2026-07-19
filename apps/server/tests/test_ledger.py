"""ledger 幂等两件套（契约 A §4.7 hit 与 fail-closed 恢复规则；重放随 DEDAG 退役）。

1. 同键同指纹 → hit：第二次 record 不新增行、返回原结果；
2. 同键异指纹 → fail-closed：batch status='fail_closed'、diagnostic_events 有 landing.fail_closed、
   频道有 fail_closed 卡片消息。

用 seed 灌入的 workspace + #all 频道满足外键（ledger.batch_id / messages.channel_id / diag）。
"""

from __future__ import annotations

import pytest
from coagentia_contracts.entities import LedgerEntryRow
from coagentia_contracts.enums import CardKind, LandingBatchKind, LandingBatchStatus
from coagentia_contracts.kernel.fingerprint import fingerprint
from coagentia_server.db import models
from coagentia_server.db.seed import seed_database
from coagentia_server.ledger import service
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

_WS = "01K0WKSP000000000000000001"
_CH = "01K0CHAN000000000000000001"  # #all（seed）

_LEDGER = models.LedgerEntry.__table__
_BATCH = models.LandingBatch.__table__
_DIAG = models.DiagnosticEvent.__table__
_MSG = models.Message.__table__


@pytest.fixture
def seeded(migrated_engine: Engine) -> Engine:
    seed_database(migrated_engine)
    return migrated_engine


def _make_batch(conn) -> str:
    batch = service.create_batch(
        conn,
        workspace_id=_WS,
        channel_id=_CH,
        kind=LandingBatchKind.DECOMP,
        content_hash="a" * 64,
        source_ref="prop-1",
        confirmed_by="01K0MMBR000000000000000001",
    )
    return batch.id


# ---------------------------------------------------------------- 1. hit


def test_same_op_same_hash_is_hit_no_new_row(seeded: Engine) -> None:
    payload = {"title": "单文件番茄钟", "temp_id": "n1"}
    with seeded.begin() as conn:
        bid = _make_batch(conn)
        op_id = f"decomp:{bid}:node:n1"
        first = service.record(conn, op_id, "create_node", payload, batch_id=bid)
        second = service.record(conn, op_id, "create_node", payload, batch_id=bid)

    assert first["status"] == "new"
    assert second["status"] == "hit"
    # 命中返回原行（同一 seq / created_at）
    assert isinstance(second["entry"], LedgerEntryRow)
    assert second["entry"].seq == first["entry"].seq
    assert second["entry"].request_hash == fingerprint(payload)

    # 账本只此一行（未新增）
    with seeded.connect() as conn:
        n = conn.execute(
            select(func.count()).select_from(_LEDGER).where(_LEDGER.c.op_id == op_id)
        ).scalar_one()
    assert n == 1


# ---------------------------------------------------------------- 2. mismatch → fail-closed


def test_same_op_diff_hash_triggers_fail_closed(seeded: Engine) -> None:
    with seeded.begin() as conn:
        bid = _make_batch(conn)
        op_id = f"decomp:{bid}:node:n1"
        service.record(conn, op_id, "create_node", {"title": "A"}, batch_id=bid)
        res = service.record(conn, op_id, "create_node", {"title": "B-DIFFERENT"}, batch_id=bid)

    assert res["status"] == "mismatch"

    with seeded.connect() as conn:
        # 批次 status='fail_closed'
        status = conn.execute(
            select(_BATCH.c.status).where(_BATCH.c.id == bid)
        ).scalar_one()
        assert status == LandingBatchStatus.FAIL_CLOSED.value

        # diagnostic_events 有 landing.fail_closed 行（按 batch 过滤）
        diag = conn.execute(
            select(func.count())
            .select_from(_DIAG)
            .where(_DIAG.c.batch_id == bid, _DIAG.c.type == "landing.fail_closed")
        ).scalar_one()
        assert diag == 1

        # 频道有 fail_closed 卡片消息（系统消息，author=NULL，card_ref=batch_id）
        card = conn.execute(
            select(_MSG.c.author_member_id, _MSG.c.channel_id, _MSG.c.card_kind)
            .where(_MSG.c.card_ref == bid, _MSG.c.card_kind == CardKind.FAIL_CLOSED.value)
        ).mappings().all()
        assert len(card) == 1
        assert card[0]["author_member_id"] is None
        assert card[0]["channel_id"] == _CH

        # 账本仍只一行（mismatch 不写入）
        n = conn.execute(
            select(func.count()).select_from(_LEDGER).where(_LEDGER.c.op_id == op_id)
        ).scalar_one()
    assert n == 1
