"""契约域服务层（契约 B §4.3/§4.7；M3a E2/E3）：task_contracts 读、修订链写、T7 校验读。

范式仿 tasks/service.py：本层只消费 contracts 包的常量/枚举（纪律 7 单一事实源），
序列化统一走 routes/serialize.py 的 task_contract_public（本层只吐 DB 行 dict）。
"""

from __future__ import annotations

from typing import Any

from coagentia_contracts.constants import HANDOFF_REQUIRED_FIELDS
from coagentia_contracts.enums import ContractKind
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from coagentia_server.db import models
from coagentia_server.ledger import service

_TC = models.tbl(models.TaskContract)


# ---------------------------------------------------------------- 读


def active_contracts(conn: Connection, task_id: str) -> list[dict[str, Any]]:
    """该任务全部契约行（含历史），按 created_at, id 排序——前端按 superseded_at 分活动/历史。"""
    rows = conn.execute(
        select(_TC).where(_TC.c.task_id == task_id).order_by(_TC.c.created_at, _TC.c.id)
    ).mappings()
    return [dict(r) for r in rows]


def active_contract(conn: Connection, task_id: str, kind: ContractKind) -> dict[str, Any] | None:
    """该任务该 kind 的活动行（superseded_at IS NULL），预期至多一行。"""
    row = (
        conn.execute(
            select(_TC).where(
                _TC.c.task_id == task_id,
                _TC.c.kind == kind,
                _TC.c.superseded_at.is_(None),
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


# ---------------------------------------------------------------- 写（提交 + 修订链）


def submit_contract(
    tx: Any,
    *,
    task_id: str,
    workspace_id: str,
    kind: ContractKind,
    body_dict: dict[str, Any],
    created_by: str,
) -> tuple[dict[str, Any], bool]:
    """提交/修订同 (task_id, kind) 契约（事务内执行；SQLite 写锁天然串行化并发提交）。

    有活动行 → supersede 旧行（写 superseded_at）+ 新行 revision=旧+1，返回 is_revision=True；
    无活动行 → 新行 revision=1，is_revision=False。version 取 body_dict 里已经过 kind 对应
    body 模型校验过的 `version` 字段（body 模型的 Literal 默认值即 schema 版本号，纪律 7 单一
    事实源——不在本层另建 kind→版本号映射表）。
    """
    # supersede 旧活动行 + 插入新活动行须原子（SAVEPOINT，范式同 M2 convert 硬化）：
    # uq_task_contracts_active 保证同 (task_id, kind) 至多一活动行；真并发下第二个插入触发
    # IntegrityError → 重读最新活动行后按新 revision 重试一次（顺序提交路径永不冲突）。
    for attempt in range(2):
        prior = active_contract(tx.conn, task_id, kind)
        ts = service.now_iso()
        revision = prior["revision"] + 1 if prior is not None else 1
        is_revision = prior is not None
        new_id = service.new_ulid()
        try:
            with tx.conn.begin_nested():
                if prior is not None:
                    tx.conn.execute(
                        update(_TC).where(_TC.c.id == prior["id"]).values(superseded_at=ts)
                    )
                tx.conn.execute(
                    insert(_TC).values(
                        id=new_id,
                        workspace_id=workspace_id,
                        task_id=task_id,
                        reminder_id=None,
                        kind=kind,
                        version=body_dict.get("version", ""),
                        body=body_dict,
                        revision=revision,
                        superseded_at=None,
                        created_by_member_id=created_by,
                        created_at=ts,
                    )
                )
        except IntegrityError:
            if attempt == 0:  # 竞态：另一事务抢先建活动行，重读后重试一次
                continue
            raise  # 仍冲突（数据始终一致、无双活动行）——上抛而非静默
        row = tx.conn.execute(select(_TC).where(_TC.c.id == new_id)).mappings().first()
        assert row is not None
        return dict(row), is_revision
    raise AssertionError("unreachable")  # pragma: no cover


# ---------------------------------------------------------------- T7 校验读


def active_handoff_missing(conn: Connection, task_id: str) -> list[str]:
    """T7 门：该任务活动 TaskHandoff 缺失的必填字段清单（顺序 = HANDOFF_REQUIRED_FIELDS）。

    无活动 handoff 视同两个字段皆缺（裁决 5）；有 handoff 则逐个检查 deliverables/evidence
    是否为非空列表（空列表与缺失同判——T7「校验非空」的字面意思）。
    """
    handoff = active_contract(conn, task_id, ContractKind.TASK_HANDOFF)
    if handoff is None:
        return list(HANDOFF_REQUIRED_FIELDS)
    body = handoff["body"]
    if not isinstance(body, dict):
        body = {}
    return [field for field in HANDOFF_REQUIRED_FIELDS if not body.get(field)]


__all__ = [
    "active_contract",
    "active_contracts",
    "active_handoff_missing",
    "submit_contract",
]
