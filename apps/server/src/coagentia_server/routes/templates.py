"""模板 REST 端点（契约 B §4.12/§11.1/§11.2；M5b H5+H6）：GET 列表 + POST 存为模板 + 实例化。

序列化 / 校验 / 409 约束 / 实例化事务编排集中在 templates/service.py + contracts.kernel（纪律 7
单一事实源）。模板本体 CRUD **零新增 WS 事件**（B §11.2 #4 裁决——列表走 REST 拉取，PUT 后前端
本地更新）；instantiate 复用 message/task/canvas 既有事件（契约 C v1.0 冻结）。
"""

from __future__ import annotations

from typing import Any

from coagentia_contracts import entities, rest
from coagentia_contracts.constants import OPID_REST_IDEMPOTENCY
from coagentia_contracts.entities import TemplateBody
from coagentia_contracts.kernel.fingerprint import fingerprint
from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import select

from coagentia_server.api import ApiError
from coagentia_server.db import models
from coagentia_server.deps import Tx, acting_member, get_tx, require_workspace
from coagentia_server.ledger import service
from coagentia_server.routes.serialize import task_public, template_public
from coagentia_server.tasks import service as tasks_service
from coagentia_server.templates import service as templates_service

router = APIRouter(prefix="/api", tags=["templates"])

_CHANNEL = models.tbl(models.Channel)


@router.get("/templates", response_model=list[entities.TemplatePublic])
def list_templates(tx: Tx = Depends(get_tx)) -> Any:
    """工作区级列表（B §11.1 #3）：builtin 置前、body 全量携带（向导预览 DAG 缩略图用）。"""
    ws = require_workspace(tx.conn)
    return [template_public(t) for t in templates_service.fetch_templates(tx.conn, ws["id"])]


@router.post("/templates", response_model=entities.TemplatePublic, status_code=201)
def create_template(body: rest.TemplateCreate, request: Request, tx: Tx = Depends(get_tx)) -> Any:
    """存为模板（B §11.1）：读频道画布快照序列化 TemplateBody → 约束 409（二值）→ 校验（无环 /
    引用一致性）→ 落库（builtin=0，created_by=主体）。

    约束二值（B §11.1 #2）：(a) 无正式 task 节点、(b) 存在草稿层（M5 恒空，见
    templates.service.has_draft_layer 注释）→ 409 TEMPLATE_CANVAS_NOT_READY（入口 disabled 是 UI
    责任，此处 API 兜底，details.reason 区分二值）。
    """
    me = acting_member(request, tx.conn)
    channel = (
        tx.conn.execute(select(_CHANNEL).where(_CHANNEL.c.id == body.channel_id))
        .mappings()
        .first()
    )
    if channel is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "频道不存在")

    # (b) 草稿层（M5 恒 False）：与无正式节点并列为 409 二值约束。
    if templates_service.has_draft_layer(tx.conn, body.channel_id):
        raise ApiError(
            409,
            rest.ErrorCode.TEMPLATE_CANVAS_NOT_READY,
            "画布存在草稿层（拆解未确认），无法存为模板",
            rule="B§11.1",
            details={"reason": "draft_layer"},
        )

    template_body = templates_service.serialize_canvas_to_body(
        tx.conn,
        body.channel_id,
        role_placeholders=body.role_placeholders,
        include_node_ids=body.include_node_ids,
    )
    # (a) 无画布 / 无正式 task 节点（include 过滤后为空亦属此列）。
    if template_body is None or not template_body.nodes:
        raise ApiError(
            409,
            rest.ErrorCode.TEMPLATE_CANVAS_NOT_READY,
            "画布无正式 task 节点，无法存为模板",
            rule="B§11.1",
            details={"reason": "no_task_nodes"},
        )

    templates_service.validate_template_body(template_body)
    ws = require_workspace(tx.conn)
    row = templates_service.insert_template(
        tx.conn,
        workspace_id=ws["id"],
        name=body.name,
        description=body.description,
        body=template_body,
        created_by=me["id"],
        builtin_flag=False,
    )
    return template_public(row)


# ---------------------------------------------------------------- 实例化（H6；B §11.2）


def _instantiate_result(batch: Any, task_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """InstantiateResult 组装：LandingBatchPublic(≡Row) + TaskPublic 列表（形状零偏差）。"""
    return {
        "batch": batch.model_dump(mode="json"),
        "tasks": [task_public(t) for t in task_rows],
    }


def _reconstruct_from_ledger(tx: Tx, payload: dict[str, Any]) -> dict[str, Any]:
    """幂等命中 → 凭账本重建原 InstantiateResult。

    REST op_id 采 reserve-before 语义（建任何节点前登记，见 instantiate_template），故其 payload
    只携 batch_id；task_rows 从该批已落库的逐节点 `create_node` 账本行按落库顺序派生
    （batch_node_task_ids 按 seq 排序 → 与首次 201 的 tasks 顺序一致），而非一份只有落库后才算
    得出的 task_ids 列表。
    """
    batch = service._fetch_batch(tx.conn, payload["batch_id"])
    assert batch is not None
    task_ids = service.batch_node_task_ids(tx.conn, payload["batch_id"])
    task_rows = [tasks_service.fetch_task(tx.conn, tid) for tid in task_ids]
    return _instantiate_result(batch, task_rows)


@router.post(
    "/templates/{template_id}/instantiate",
    response_model=rest.InstantiateResult,
    status_code=201,
)
def instantiate_template(
    template_id: str,
    body: rest.TemplateInstantiate,
    request: Request,
    tx: Tx = Depends(get_tx),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Any:
    """实例化模板到目标频道画布（B §11.2）：单事务落地批 → 逐节点 create_node 全链 → 连边 →
    briefing @角色 → mark_done。

    role_mapping 须覆盖 body.roles 全部占位（缺失 → 422 VALIDATION_FAILED，details.missing）；值
    null = 该角色节点无 owner「待认领」。Idempotency-Key（照 messages.py OPID_REST_IDEMPOTENCY
    先例，lookup/record 双段式）：同键同体重放回同一批（不重复建节点），同键异体 → 409。零新增
    WS 事件（复用 message/task/canvas 事件）。
    """
    me = acting_member(request, tx.conn)
    template = templates_service.fetch_template(tx.conn, template_id)
    if template is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "模板不存在")

    tbody = TemplateBody.model_validate(template["body"])
    role_mapping = dict(body.role_mapping)
    missing = templates_service.missing_role_mappings(tbody, role_mapping)
    if missing:
        raise ApiError(
            422,
            rest.ErrorCode.VALIDATION_FAILED,
            "role_mapping 未覆盖全部角色占位",
            rule="B§11.2",
            details={"missing": missing},
        )
    # role_mapping 值须指向在册活动成员：格式合法但不存在的 member id 会在建任务/mention 时触发
    # FK IntegrityError（未捕获 → 500 + 回滚）；落库前显式拒为 422（details.unknown 列之）。
    unknown = templates_service.unknown_role_members(tx.conn, role_mapping)
    if unknown:
        raise ApiError(
            422,
            rest.ErrorCode.VALIDATION_FAILED,
            "role_mapping 含未知成员",
            rule="B§11.2",
            details={"unknown": unknown},
        )
    # 无画布 404 前移到 reserve 之前（见下 reserve 说明）。
    if not templates_service.channel_has_canvas(tx.conn, body.channel_id):
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "目标频道无画布，无法实例化")

    batch_id = service.new_ulid()

    # 幂等 reserve-before（照 messages.py:430-448：record 先于副作用）：并发对手先落库同键时，本
    # 请求在建任何节点前即 409/回其结果，绝不产生重复落地批。关键不变式：**所有可失败校验（模板
    # 404 / 角色 422 / 未知成员 422 / 无画布 404）全部前置于 reserve 之上**，故 reserve 后
    # instantiate 不再抛可失败错误（模板存前已校验无环、实例化子图与既有画布节点不相交故连边不
    # 成环）——这样无论事务回滚语义如何，都不会残留悬挂 op_id 指向未建的批（reserve 的 record 走
    # SAVEPOINT，其写入未必随外层回滚而撤销，故此不变式是安全性所系，而非依赖回滚行为）。
    # req_hash 折入 template_id：否则同键 + 同 {channel,role_mapping} 跨两个模板会误判 hit、回放错
    # 模板。op_id payload 只记 batch_id，task_ids 由逐节点 create_node 账本行派生（按 seq 保序）。
    if idempotency_key is not None:
        op_id = OPID_REST_IDEMPOTENCY.format(key=idempotency_key)
        req_hash = fingerprint(
            {"template_id": template_id, "body": body.model_dump(mode="json")}
        )
        res = service.record(
            tx.conn, op_id, "rest_instantiate", {"batch_id": batch_id}, request_hash=req_hash
        )
        if res["status"] == "hit":  # 并发对手已抢先登记同键同体 → 回其结果，不建节点
            return _reconstruct_from_ledger(tx, res["entry"].payload)
        if res["status"] == "mismatch":
            raise ApiError(
                409, rest.ErrorCode.IDEMPOTENCY_MISMATCH, "同 Idempotency-Key 不同请求体"
            )
        batch_id = res["entry"].payload["batch_id"]  # new：复用账本登记的 batch_id

    batch, task_rows = templates_service.instantiate_template(
        tx,
        template_row=template,
        channel_id=body.channel_id,
        role_mapping=role_mapping,
        owner_id=me["id"],
        batch_id=batch_id,
    )
    return _instantiate_result(batch, task_rows)
