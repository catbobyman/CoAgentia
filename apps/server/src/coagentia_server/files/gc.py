"""孤儿文件 GC 运行器（契约 D §9.2）：扫 staging，删 mtime>24h 未绑定者，写 system.file_gc。"""

from __future__ import annotations

from coagentia_contracts.constants import DIAGNOSTIC_TYPES
from sqlalchemy import insert, select
from sqlalchemy.engine import Engine

from coagentia_server.db import models
from coagentia_server.files.store import FileStore
from coagentia_server.guard.service import ACTIVE_STATUSES
from coagentia_server.ledger.service import now_iso

# system.file_gc 是登记诊断类型（契约 A §4.6 命名空间；constants.DIAGNOSTIC_TYPES 权威登记）。
_DIAG_FILE_GC = "system.file_gc"
assert _DIAG_FILE_GC in DIAGNOSTIC_TYPES

_DIAG = models.tbl(models.DiagnosticEvent)
_WS = models.tbl(models.Workspace)
_HELD = models.tbl(models.HeldDraft)


def _held_referenced_upload_ids(engine: Engine) -> set[str]:
    """活动 held 行（held/reevaluating）file_ids 引用的全部 staging upload_id 集合。

    这些草稿放行时要「原样发送」不丢附件（G3），故其引用的 staging 文件即便超 24h 也豁免删除；
    held 行进终态后（released/discarded/resolved）不再出现在活动集 → 下轮 GC 可正常回收。
    """
    referenced: set[str] = set()
    with engine.connect() as conn:
        rows = conn.execute(
            select(_HELD.c.file_ids).where(_HELD.c.status.in_(ACTIVE_STATUSES))
        ).scalars()
        for file_ids in rows:
            if file_ids:  # JSON 列可空（无附件草稿）
                referenced.update(file_ids)
    return referenced


def run_gc(engine: Engine, file_store: FileStore, *, now: float | None = None) -> int:
    """删除孤儿 staging 文件；有删除则写一条 system.file_gc 诊断。返回删除数。

    活动 held 行引用的 staging 文件豁免（契约 D §9.2 v1.0.1）：held 草稿放行要原样带附件，进终态
    后不再豁免（下轮可回收）。
    """
    orphans = file_store.scan_orphans(now=now)
    if not orphans:
        return 0
    exempt = _held_referenced_upload_ids(engine)
    orphans = [upload_id for upload_id in orphans if upload_id not in exempt]
    if not orphans:  # 全部被活动 held 引用 → 本轮无删除
        return 0

    deleted: list[str] = []
    workspace_id: str | None = None
    for upload_id in orphans:
        meta = file_store.delete_staged(upload_id)
        deleted.append(upload_id)
        if workspace_id is None and meta is not None:
            workspace_id = meta.workspace_id

    if workspace_id is None:
        with engine.connect() as conn:
            workspace_id = conn.execute(select(_WS.c.id).limit(1)).scalar_one_or_none()
    if workspace_id is None:  # 无工作区 = 无处落诊断（bootstrap 前）
        return len(deleted)

    with engine.begin() as conn:
        conn.execute(
            insert(_DIAG).values(
                workspace_id=workspace_id,
                agent_member_id=None,
                type=_DIAG_FILE_GC,
                channel_id=None,
                task_id=None,
                batch_id=None,
                payload={"deleted_upload_ids": deleted, "count": len(deleted)},
                created_at=now_iso(),
            )
        )
    return len(deleted)
