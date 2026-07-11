"""模板 REST 端点（契约 B §4.12/§11.1；M5b H5）：GET 列表 + POST 存为模板。

instantiate（POST /templates/{id}/instantiate）归 H6，本模块只承载保存与列表两端点。序列化 / 校验 /
409 约束集中在 templates/service.py + contracts.kernel（纪律 7 单一事实源）。模板本体 CRUD **零新增
WS 事件**（B §11.2 #4 裁决——列表走 REST 拉取，PUT 后前端本地更新，契约 C 保持 v1.0）。
"""

from __future__ import annotations

from typing import Any

from coagentia_contracts import entities, rest
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from coagentia_server.api import ApiError
from coagentia_server.db import models
from coagentia_server.deps import Tx, acting_member, get_tx, require_workspace
from coagentia_server.routes.serialize import template_public
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
