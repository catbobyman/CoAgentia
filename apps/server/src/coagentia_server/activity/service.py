"""Activity 域服务层（契约 B §9.7）：activity 行插入 + activity.created 广播的公开底座。

M4 F2 迁移：原为 routes/messages.py 私有 `_emit_activity`（吃 `Tx`、依赖 request 上下文）。
D5 沉默升级链在 hub 后台路径需发射升级类 activity，但 hub 无 request 上下文——故把底层
逻辑抽到本层，改成**事务上下文注入式**签名（不吃 Request），让 routes（`Tx`）与 hub
（`GatewayTx`）共同消费。二者同构：都持 `.conn`（写库）+ `.emit`（登记待广播事件，事务
提交后由各自的 tx flush 到 event bus——契约 C §1.4 提交后按序发射）。故本函数登记广播而
非直发，天然保住"提交后才广播、回滚不广播"的原子性（消息生成侧 as_task/文件绑定半失败时
不漏发幽灵 activity）。
"""

from __future__ import annotations

from typing import Any, Protocol

from coagentia_contracts.ws import EventType
from sqlalchemy import insert
from sqlalchemy.engine import Connection

from coagentia_server.db import models
from coagentia_server.ledger import service
from coagentia_server.routes.serialize import activity_item_public

_ACTIVITY = models.tbl(models.ActivityItem)


class EmitTx(Protocol):
    """`Tx`（deps）与 `GatewayTx`（gateway_tx）的结构化交集：写库 + 登记提交后广播。"""

    conn: Connection

    def emit(self, etype: Any, channel_id: str | None, data: dict[str, Any]) -> None: ...


def emit_activity(
    tx: EmitTx,
    *,
    workspace_id: str,
    member_id: str,
    kind: str,
    channel_id: str | None = None,
    message_id: str | None = None,
    task_id: str | None = None,
    actor_member_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """插一条 activity_items 并登记 activity.created 广播（channel_id=None 全局，供前端 P9）。

    tx 为 `Tx`（路由）或 `GatewayTx`（hub 后台）——不依赖 Request。actor_member_id（触发消息
    作者）只进 Public 载荷不落库——表列 member_id 语义=接收者。created_at 省略则取当前时刻；
    消息生成侧传入该消息的 ts，保「消息与其派生 activity 同 created_at」不变量。返回
    ActivityItemPublic dict（供上层复用/断言）。
    """
    ts = created_at if created_at is not None else service.now_iso()
    row = {
        "id": service.new_ulid(),
        "workspace_id": workspace_id,
        "member_id": member_id,
        "kind": kind,
        "channel_id": channel_id,
        "message_id": message_id,
        "task_id": task_id,
        "created_at": ts,
        "done_at": None,
    }
    tx.conn.execute(insert(_ACTIVITY).values(**row))
    pub = activity_item_public({**row, "actor_member_id": actor_member_id})
    tx.emit(EventType.ACTIVITY_CREATED, None, {"item": pub})
    return pub


__all__ = ["EmitTx", "emit_activity"]
