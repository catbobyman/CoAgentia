"""契约驱动 mock server：fixtures over REST（契约 B M1 端点）+ WS 事件（契约 C）。

- 响应形状 = contracts 的 *Public 模型（response_model 强制，吐出去的就是契约形状）；
- OpenAPI 导出即 TS 生成管线的 REST 源（00 §4.4）；
- `/__mock/*` 前缀 = mock 专用控制面（时间线回放），不属契约。
"""

import hashlib
from typing import Any

import uvicorn
from coagentia_contracts import daemon, entities, rest
from coagentia_contracts.enums import ActivityFilter, SearchKind, UsageLevel
from coagentia_contracts.ws import EventType
from fastapi import FastAPI, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from coagentia_mock.events import Hub
from coagentia_mock.state import Store, new_id, now_ts

MOCK_PORT = 8642

store = Store()
hub = Hub(store)

app = FastAPI(title="CoAgentia mock server", version="0.1.0")
# mock 专用：Vite dev server 跨端口访问（真实 server 同源伺服 UI，无此需求）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ApiError(Exception):
    def __init__(self, status: int, code: rest.ErrorCode, message: str,
                 rule: str | None = None, details: Any = None) -> None:
        self.status = status
        self.body = rest.ErrorBody(code=code, message=message, rule=rule, details=details)


@app.exception_handler(ApiError)
async def api_error_handler(_req: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content=rest.ErrorResponse(error=exc.body).model_dump(),
    )


def public_computer(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k != "api_key_hash"}


def require_channel(channel_id: str) -> dict[str, Any]:
    ch = store.channel(channel_id)
    if ch is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "channel not found")
    return ch


# ---------------------------------------------------------------- 4.1 工作区


@app.get("/api/workspace", response_model=entities.WorkspacePublic)
async def get_workspace() -> Any:
    return store.workspace


@app.post("/api/workspace", response_model=entities.WorkspacePublic, status_code=201)
async def create_workspace(body: rest.WorkspaceCreate) -> Any:
    raise ApiError(409, rest.ErrorCode.WORKSPACE_EXISTS, "MVP 单工作区,已存在")


@app.patch("/api/workspace", response_model=entities.WorkspacePublic)
async def patch_workspace(body: rest.WorkspacePatch) -> Any:
    store.workspace.update({k: v for k, v in body.model_dump().items() if v is not None})
    await hub.broadcast(EventType.WORKSPACE_UPDATED, None, {"workspace": store.workspace})
    return store.workspace


# ---------------------------------------------------------------- 4.2 机器


@app.get("/api/computers", response_model=list[entities.ComputerPublic])
async def list_computers() -> Any:
    return [public_computer(c) for c in store.computers]


@app.post("/api/computers", response_model=rest.ComputerCreated, status_code=201)
async def add_computer(body: rest.ComputerCreate) -> Any:
    api_key = f"cak_{new_id().lower()}"
    row = {
        "id": new_id(), "workspace_id": store.workspace["id"], "name": body.name,
        "os": None, "arch": None, "daemon_version": None,
        "api_key_hash": hashlib.sha256(api_key.encode()).hexdigest(),
        "detected_runtimes": [], "status": "offline", "last_seen_at": None,
        "created_at": now_ts(),
    }
    store.computers.append(row)
    return {
        "computer": public_computer(row),
        "api_key": api_key,  # 明文仅此一次（契约 B §4.2）
        "command_line": f"uvx coagentia-daemon --server-url http://127.0.0.1:{MOCK_PORT}"
                        f" --api-key {api_key}",
    }


@app.patch("/api/computers/{computer_id}", response_model=entities.ComputerPublic)
async def rename_computer(computer_id: str, body: rest.ComputerPatch) -> Any:
    row = next((c for c in store.computers if c["id"] == computer_id), None)
    if row is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "computer not found")
    row["name"] = body.name
    await hub.broadcast(EventType.COMPUTER_UPDATED, None, {"computer": public_computer(row)})
    return public_computer(row)


@app.delete("/api/computers/{computer_id}", status_code=204)
async def remove_computer(computer_id: str) -> Response:
    if any(a["computer_id"] == computer_id for a in store.agents):
        raise ApiError(409, rest.ErrorCode.COMPUTER_HAS_AGENTS,
                       "该机器上仍有 Agent,先删除全部 Agent", rule="FR-2.7")
    store.computers = [c for c in store.computers if c["id"] != computer_id]
    return Response(status_code=204)


# ---------------------------------------------------------------- 4.3 成员与 Agent


@app.get("/api/members", response_model=list[entities.MemberPublic])
async def list_members(include_removed: bool = False) -> Any:
    return [m for m in store.members if include_removed or m.get("removed_at") is None]


@app.patch("/api/members/{member_id}", response_model=entities.MemberPublic)
async def patch_member(member_id: str, body: rest.MemberPatch) -> Any:
    m = store.member(member_id)
    if m is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "member not found")
    if m["kind"] == "agent" and body.role == "owner":
        raise ApiError(403, rest.ErrorCode.PERMISSION_DENIED, "Agent 永不 Owner", rule="R1")
    m["role"] = body.role
    await hub.broadcast(EventType.MEMBER_UPDATED, None, {"member": m})
    return m


@app.get("/api/presence", response_model=rest.PresenceSnapshot)
async def get_presence() -> Any:
    return {"items": store.presence}


@app.post("/api/agents", response_model=entities.AgentPublic, status_code=201)
async def create_agent(body: rest.AgentCreate) -> Any:
    if any(m["name"].lower() == body.name.lower() for m in store.members):
        raise ApiError(409, rest.ErrorCode.NAME_TAKEN, f"成员名 {body.name} 已被占用")
    member = {"id": new_id(), "workspace_id": store.workspace["id"], "kind": "agent",
              "name": body.name, "role": "member", "removed_at": None, "created_at": now_ts()}
    store.members.append(member)
    agent = {"member_id": member["id"], "computer_id": body.computer_id,
             "runtime": body.runtime, "model": body.model, "description": body.description,
             "home_path": f"~/.coagentia/agents/{member['id']}", "status": "offline",
             "created_by_member_id": store.members[0]["id"]}
    store.agents.append(agent)
    store.skills[member["id"]] = []
    store.presence.append({"member_id": member["id"], "kind": "agent",
                           "status": "offline", "busy_detail": None})
    await hub.broadcast(EventType.MEMBER_CREATED, None, {"member": member})
    return agent


@app.get("/api/agents/{member_id}", response_model=entities.AgentPublic)
async def get_agent(member_id: str) -> Any:
    a = store.agent(member_id)
    if a is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "agent not found")
    return a


@app.patch("/api/agents/{member_id}", response_model=entities.AgentPublic)
async def patch_agent(member_id: str, body: rest.AgentPatch) -> Any:
    a = store.agent(member_id)
    if a is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "agent not found")
    a.update({k: v for k, v in body.model_dump().items() if v is not None})
    await hub.broadcast(EventType.AGENT_UPDATED, None, {"agent": a})  # 下次启动生效
    return a


@app.delete("/api/agents/{member_id}", status_code=204)
async def delete_agent(member_id: str) -> Response:
    m = store.member(member_id)
    if m is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "agent not found")
    m["removed_at"] = now_ts()  # 软删（消息归属保留身份）
    store.agents = [a for a in store.agents if a["member_id"] != member_id]
    await hub.broadcast(EventType.MEMBER_REMOVED, None, {"member": m})
    return Response(status_code=204)


@app.post("/api/agents/{member_id}/lifecycle", response_model=entities.AgentPublic,
          status_code=202)
async def agent_lifecycle(member_id: str, body: rest.LifecycleRequest) -> Any:
    a = store.agent(member_id)
    if a is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "agent not found")
    target = {"start": "starting", "restart": "starting", "reset_session": "starting",
              "reset_full": "starting", "stop": "offline"}[body.action.value]
    a["status"] = target
    for p in store.presence:
        if p["member_id"] == member_id:
            p["status"] = target
    await hub.broadcast(EventType.PRESENCE_CHANGED, None,
                        {"member_id": member_id, "kind": "agent", "status": target})
    return a


@app.get("/api/agents/{member_id}/home/tree")
async def home_tree(member_id: str, path: str = "/") -> Any:
    """daemon 查询帧代理（契约 D §6）；mock 回一棵固定小树（Raft 磁盘侧同构）。"""
    return {"entries": [
        {"name": "MEMORY.md", "kind": "file", "size_bytes": 2048, "mtime": now_ts()},
        {"name": "notes", "kind": "dir", "size_bytes": 0, "mtime": now_ts()},
        {"name": "deliverables", "kind": "dir", "size_bytes": 0, "mtime": now_ts()},
    ]}


@app.get("/api/agents/{member_id}/home/file")
async def home_file(member_id: str, path: str) -> Any:
    return {"kind": "text", "content": f"# MEMORY\n\n(mock) {path}\n", "truncated": False}


@app.get("/api/agents/{member_id}/skills", response_model=list[entities.AgentSkillPublic])
async def get_skills(member_id: str) -> Any:
    return store.skills.get(member_id, [])


@app.put("/api/agents/{member_id}/skills", response_model=list[entities.AgentSkillPublic])
async def put_skills(member_id: str, body: rest.SkillsPut) -> Any:
    granted = [{"agent_member_id": member_id, "skill": s,
                "granted_by_member_id": store.members[0]["id"], "granted_at": now_ts()}
               for s in body.skills]
    store.skills[member_id] = granted  # 全量替换制（R6）
    return granted


@app.get("/api/agents/{member_id}/diagnostics",
         response_model=rest.Page[entities.DiagnosticEventPublic])
async def get_diagnostics(member_id: str, after_seq: int = 0, type: str | None = None,
                          limit: int = 50) -> Any:
    return {"items": [], "next_cursor": None}


@app.get("/api/agents/{member_id}/diagnostics/export")
async def export_diagnostics(member_id: str) -> Response:
    return Response(content="(mock) no diagnostics\n", media_type="text/plain")


# ---------------------------------------------------------------- 4.4 提醒


@app.post("/api/reminders", response_model=entities.ReminderPublic, status_code=201)
async def create_reminder(body: rest.ReminderCreate) -> Any:
    # recurring 必须内联 loop_contract（D1-L2）；mock 不建 task_contracts，仅回填合成 id 对齐形状。
    if body.kind == "recurring" and body.loop_contract is None:
        raise ApiError(422, rest.ErrorCode.VALIDATION_FAILED,
                       "循环 reminder 必须内联 LoopContract", rule="D1-L2",
                       details={"missing": ["loop_contract"]})
    loop_contract_id = new_id() if body.loop_contract is not None else None
    row = {"id": new_id(), "workspace_id": store.workspace["id"],
           "agent_member_id": store.agents[0]["member_id"], "kind": body.kind,
           "cadence": body.cadence, "anchor_channel_id": body.anchor_channel_id,
           "anchor_message_id": body.anchor_message_id, "anchor_task_id": body.anchor_task_id,
           "loop_contract_id": loop_contract_id, "next_fire_at": now_ts(),
           "status": "active", "cancelled_by_member_id": None, "created_at": now_ts()}
    store.reminders.append(row)
    await hub.broadcast(EventType.REMINDER_CREATED, None, {"reminder": row})
    return row


@app.get("/api/agents/{member_id}/reminders", response_model=list[entities.ReminderPublic])
async def list_reminders(member_id: str) -> Any:
    return [r for r in store.reminders if r["agent_member_id"] == member_id]


@app.delete("/api/reminders/{reminder_id}", status_code=204)
async def cancel_reminder(reminder_id: str) -> Response:
    store.reminders = [r for r in store.reminders if r["id"] != reminder_id]
    return Response(status_code=204)


# ---------------------------------------------------------------- 4.5 频道与 DM


@app.get("/api/channels", response_model=rest.ChannelsSnapshot)
async def list_channels() -> Any:
    me = store.members[0]["id"]  # 浏览器 = Owner 人类（契约 B §2）
    # notification_settings = 本人非默认行（§11.4 #5）；mock 无设置存储，形状源回空列表。
    return {"items": store.channels,
            "read_positions": [r for r in store.read_positions if r["member_id"] == me],
            "notification_settings": []}


@app.post("/api/channels", response_model=entities.ChannelPublic, status_code=201)
async def create_channel(body: rest.ChannelCreate) -> Any:
    if any(c["name"] == body.name for c in store.channels if c["kind"] == "channel"):
        raise ApiError(409, rest.ErrorCode.NAME_TAKEN, f"频道名 {body.name} 已存在")
    row = {"id": new_id(), "workspace_id": store.workspace["id"], "kind": "channel",
           "name": body.name, "description": body.description,
           "is_private": body.is_private, "created_at": now_ts()}
    row = entities.ChannelRow.model_validate(row).model_dump()
    store.channels.append(row)
    for mid in body.member_ids:
        store.channel_members.append(
            {"channel_id": row["id"], "member_id": mid, "joined_at": now_ts()})
    await hub.broadcast(EventType.CHANNEL_CREATED, row["id"], {"channel": row})
    return row


@app.patch("/api/channels/{channel_id}", response_model=entities.ChannelPublic)
async def patch_channel(channel_id: str, body: rest.ChannelPatch) -> Any:
    ch = require_channel(channel_id)
    ch.update({k: v for k, v in body.model_dump().items() if v is not None})
    await hub.broadcast(EventType.CHANNEL_UPDATED, channel_id, {"channel": ch})
    return ch


@app.post("/api/channels/{channel_id}/archive", response_model=entities.ChannelPublic)
async def archive_channel(channel_id: str) -> Any:
    ch = require_channel(channel_id)
    ch["archived_at"] = now_ts()
    await hub.broadcast(EventType.CHANNEL_UPDATED, channel_id, {"channel": ch})
    return ch


@app.post("/api/channels/{channel_id}/unarchive", response_model=entities.ChannelPublic)
async def unarchive_channel(channel_id: str) -> Any:
    ch = require_channel(channel_id)
    ch["archived_at"] = None
    await hub.broadcast(EventType.CHANNEL_UPDATED, channel_id, {"channel": ch})
    return ch


@app.delete("/api/channels/{channel_id}", status_code=204)
async def delete_channel(channel_id: str) -> Response:
    ch = require_channel(channel_id)
    store.channels.remove(ch)
    await hub.broadcast(EventType.CHANNEL_DELETED, channel_id, {"channel": ch})
    return Response(status_code=204)


@app.post("/api/channels/{channel_id}/members", status_code=201)
async def add_channel_member(channel_id: str, body: rest.ChannelMemberAdd) -> Any:
    require_channel(channel_id)
    store.channel_members.append(
        {"channel_id": channel_id, "member_id": body.member_id, "joined_at": now_ts()})
    await hub.broadcast(EventType.CHANNEL_MEMBER_ADDED, channel_id,
                        {"channel_id": channel_id, "member_id": body.member_id})
    return {"channel_id": channel_id, "member_id": body.member_id}


@app.delete("/api/channels/{channel_id}/members/{member_id}", status_code=204)
async def remove_channel_member(channel_id: str, member_id: str) -> Response:
    store.channel_members = [cm for cm in store.channel_members
                             if not (cm["channel_id"] == channel_id
                                     and cm["member_id"] == member_id)]
    await hub.broadcast(EventType.CHANNEL_MEMBER_REMOVED, channel_id,
                        {"channel_id": channel_id, "member_id": member_id})
    return Response(status_code=204)


@app.post("/api/dms", response_model=entities.ChannelPublic)
async def open_dm(body: rest.DmCreate) -> Any:
    me = store.members[0]["id"]  # 浏览器 = Owner 人类（契约 B §2）
    key = ":".join(sorted([me, body.member_id]))
    existing = next((c for c in store.channels if c.get("dm_key") == key), None)
    if existing:
        return existing
    row = entities.ChannelRow.model_validate({
        "id": new_id(), "workspace_id": store.workspace["id"], "kind": "dm", "name": None,
        "is_private": True, "dm_key": key, "created_at": now_ts()}).model_dump()
    store.channels.append(row)
    for mid in (me, body.member_id):
        store.channel_members.append(
            {"channel_id": row["id"], "member_id": mid, "joined_at": now_ts()})
    await hub.broadcast(EventType.CHANNEL_CREATED, row["id"], {"channel": row})
    return row


# ---------------------------------------------------------------- 4.6 消息、文件与已读


@app.get("/api/channels/{channel_id}/messages",
         response_model=rest.Page[entities.MessagePublic])
async def get_messages(channel_id: str, after: str | None = None, before: str | None = None,
                       limit: int = rest.PAGE_DEFAULT_LIMIT) -> Any:
    require_channel(channel_id)
    msgs = store.channel_messages(channel_id)
    if after:
        ids = [m["id"] for m in msgs]
        msgs = msgs[ids.index(after) + 1:] if after in ids else msgs
    if before:
        ids = [m["id"] for m in msgs]
        msgs = msgs[: ids.index(before)] if before in ids else msgs
    limit = min(limit, rest.PAGE_MAX_LIMIT)
    page, rest_items = msgs[:limit], msgs[limit:]
    return {"items": page, "next_cursor": page[-1]["id"] if rest_items else None}


@app.get("/api/messages/{message_id}/thread",
         response_model=rest.Page[entities.MessagePublic])
async def get_thread(message_id: str, after: str | None = None) -> Any:
    root = next((m for m in store.messages if m["id"] == message_id), None)
    if root is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "message not found")
    replies = sorted((m for m in store.messages if m["thread_root_id"] == message_id),
                     key=lambda m: (m["created_at"], m["id"]))
    return {"items": [root, *replies], "next_cursor": None}


@app.post("/api/channels/{channel_id}/messages", response_model=rest.MessageCreated,
          status_code=201)
async def post_message(channel_id: str, body: rest.MessageCreate) -> Any:
    ch = require_channel(channel_id)
    if ch.get("archived_at"):
        raise ApiError(409, rest.ErrorCode.CHANNEL_ARCHIVED, "归档频道拒收新消息",
                       rule="FR-1.3")
    if body.as_task is not None and ch["kind"] == "dm":
        raise ApiError(422, rest.ErrorCode.TASK_IN_DM, "DM 不承载任务", rule="FR-5.1")
    if body.as_task is not None and body.thread_root_id is not None:
        raise ApiError(422, rest.ErrorCode.NOT_TOP_LEVEL_MESSAGE,
                       "仅顶级消息可转任务", rule="T3")
    me = store.members[0]["id"]
    msg = store.append_message(channel_id, me, body.body, body.thread_root_id)
    task = None
    if body.as_task is not None:
        title = body.as_task.title or body.body[:40]
        task = store.create_task(channel_id, msg["id"], title, me)
    await hub.broadcast(EventType.MESSAGE_CREATED, channel_id, {"message": msg})
    if task:
        await hub.broadcast(EventType.TASK_CREATED, channel_id, {"task": task})
    return {"message": msg, "task": task}


@app.post("/api/files", response_model=entities.FilePublic, status_code=201)
async def upload_file(file: UploadFile) -> Any:
    content = await file.read()
    max_bytes = store.workspace["attachment_max_mb"] * 1024 * 1024
    if len(content) > max_bytes:
        raise ApiError(413, rest.ErrorCode.FILE_TOO_LARGE,
                       f"超过 {store.workspace['attachment_max_mb']}MB 上限")
    meta = {"id": new_id(), "workspace_id": store.workspace["id"], "message_id": None,
            "channel_id": None, "name": file.filename or "unnamed", "mime":
            file.content_type or "application/octet-stream", "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(), "created_at": now_ts()}
    store.files[meta["id"]] = {"meta": meta, "bytes": content}
    return meta  # staging 态：message_id = null（契约 D §9.2）


@app.get("/api/files/{file_id}/content")
async def file_content(file_id: str) -> Response:
    f = store.files.get(file_id)
    if f is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "file not found")
    return Response(content=f["bytes"], media_type=f["meta"]["mime"])


@app.put("/api/channels/{channel_id}/read-position",
         response_model=entities.ReadPositionPublic)
async def put_read_position(channel_id: str, body: rest.ReadPositionPut) -> Any:
    require_channel(channel_id)
    me = store.members[0]["id"]
    row = store.set_read_position(me, channel_id, body.last_read_message_id)
    await hub.broadcast(EventType.READ_UPDATED, channel_id, {
        "channel_id": channel_id, "member_id": me,
        "last_read_message_id": body.last_read_message_id,
    })
    return row


# ---------------------------------------------------------------- M2 任务/搜索/Activity（纯形状）
#
# C0 登记：mock 只验形状不做业务（无状态机/竞态/FTS，纪律 4）——喂 OpenAPI→rest.ts。
# 状态机（TASK_TRANSITION_INVALID）、CLAIM_RACE、FTS 命中都活在真 server（C1/C2）。


def require_task(task_id: str) -> dict[str, Any]:
    task = next((t for t in store.tasks if t["id"] == task_id), None)
    if task is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "task not found")
    return task


@app.get("/api/tasks", response_model=rest.Page[entities.TaskPublic])
async def list_tasks(channel_id: str | None = None, status: str | None = None,
                     owner: str | None = None, creator: str | None = None,
                     after: str | None = None, limit: int = rest.PAGE_DEFAULT_LIMIT) -> Any:
    items = [t for t in store.tasks
             if (channel_id is None or t["channel_id"] == channel_id)
             and (status is None or t["status"] == status)
             and (owner is None or t.get("owner_member_id") == owner)
             and (creator is None or t.get("created_by_member_id") == creator)]
    return {"items": sorted(items, key=lambda t: t["number"]), "next_cursor": None}


@app.get("/api/tasks/{task_id}", response_model=rest.TaskDetail)
async def get_task_detail(task_id: str) -> Any:
    task = require_task(task_id)
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
             "cache_write_tokens": 0, "events": 0}
    for e in store.token_usage_events:
        if e.get("task_id") == task_id:
            for k in ("input_tokens", "output_tokens", "cache_read_tokens",
                      "cache_write_tokens"):
                usage[k] += e.get(k, 0)
            usage["events"] += 1
    return {"task": task, "contracts": [], "usage": usage, "worktree": None}


@app.get("/api/tasks/{task_id}/contracts", response_model=list[entities.TaskContractPublic])
async def get_task_contracts(task_id: str) -> Any:
    """M3 契约读取（形状源非逻辑源，纪律 4）：mock 恒空，修订链/T7 只活真 server。"""
    require_task(task_id)
    return []


@app.get("/api/channels/{channel_id}/canvas", response_model=rest.CanvasDetail)
async def get_canvas(channel_id: str) -> Any:
    """M3b 画布读形状（形状源非逻辑源，纪律 4）：mock 只回画布头 + 空节点/边。

    环校验/gating/baseline 推进只活真 server（E4/E5）。每频道恰一画布，缺行 → 404。
    """
    require_channel(channel_id)
    canvas = store.canvas(channel_id)
    if canvas is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "canvas not found")
    return {"canvas": canvas, "nodes": [], "edges": []}


@app.post("/api/messages/{message_id}/task", response_model=entities.TaskPublic,
          status_code=201)
async def convert_message_to_task(message_id: str, body: rest.ConvertToTask) -> Any:
    msg = next((m for m in store.messages if m["id"] == message_id), None)
    if msg is None:
        raise ApiError(404, rest.ErrorCode.NOT_FOUND, "message not found")
    title = body.title or msg["body"][:80]
    task = store.create_task(msg["channel_id"], message_id, title, store.members[0]["id"])
    await hub.broadcast(EventType.TASK_CREATED, msg["channel_id"], {"task": task})
    return task


@app.post("/api/tasks/{task_id}/claim", response_model=entities.TaskPublic)
async def claim_task(task_id: str) -> Any:
    return require_task(task_id)


@app.post("/api/tasks/{task_id}/unclaim", response_model=entities.TaskPublic)
async def unclaim_task(task_id: str) -> Any:
    return require_task(task_id)


@app.post("/api/tasks/{task_id}/assign", response_model=entities.TaskPublic)
async def assign_task(task_id: str, body: rest.AssignRequest) -> Any:
    return require_task(task_id)


@app.post("/api/tasks/{task_id}/status", response_model=entities.TaskPublic)
async def set_task_status(task_id: str, body: rest.TaskStatusChange) -> Any:
    return require_task(task_id)


@app.patch("/api/tasks/{task_id}", response_model=entities.TaskPublic)
async def patch_task(task_id: str, body: rest.TaskPatch) -> Any:
    return require_task(task_id)


@app.get("/api/channels/{channel_id}/files", response_model=rest.Page[entities.FilePublic])
async def list_channel_files(channel_id: str, after: str | None = None,
                             limit: int = rest.PAGE_DEFAULT_LIMIT) -> Any:
    require_channel(channel_id)
    files = [f["meta"] for f in store.files.values()
             if f["meta"].get("channel_id") == channel_id]
    return {"items": files, "next_cursor": None}


@app.get("/api/search", response_model=rest.SearchResponse)
async def search(q: str = "", kind: SearchKind | None = None,
                 from_member: str | None = None, in_channel: str | None = None,
                 limit: int = rest.PAGE_DEFAULT_LIMIT) -> Any:
    # 形状源须声明真 server 全部 query 参（B §9.6），否则生成 TS 类型漏 from_member/in_channel。
    return {"jumps": {"channels": [], "members": []}, "messages": [], "tasks": []}


@app.get("/api/activity", response_model=rest.Page[entities.ActivityItemPublic])
async def list_activity(filter: ActivityFilter = ActivityFilter.ALL, after: str | None = None,
                        limit: int = rest.PAGE_DEFAULT_LIMIT) -> Any:
    return {"items": [], "next_cursor": None}


@app.post("/api/activity/{activity_id}/done", response_model=entities.ActivityItemPublic)
async def mark_activity_done(activity_id: str) -> Any:
    return {"id": activity_id, "workspace_id": store.workspace["id"],
            "member_id": store.members[0]["id"], "kind": "mention", "channel_id": None,
            "message_id": None, "task_id": None, "created_at": now_ts(), "done_at": now_ts()}


# ---------------------------------------------------------------- M4 护栏 HeldDraft（纯形状）
#
# C0 登记：mock 只验形状不做业务（纪律 4）——freshness 门/三键干预/G4 重评估活真 server。
# held 行只由 freshness 门创建（无 POST 创建端点，B §4.14）；此处仅回列表形状喂 OpenAPI→rest.ts。


@app.get("/api/held-drafts", response_model=rest.Page[entities.HeldDraftPublic])
async def list_held_drafts(status: str | None = None, channel_id: str | None = None,
                           after: str | None = None,
                           limit: int = rest.PAGE_DEFAULT_LIMIT) -> Any:
    """被扣草稿清单（§6 重同步清单成员，status=held = 现行被扣）；mock 恒空，形状源非逻辑源。"""
    return {"items": [], "next_cursor": None}


# ---------------------------------------------------------------- M5 模板与通知设置（纯形状）
#
# C0 登记：mock 只验形状不做业务（纪律 4）——快照序列化/实例化事务/409 约束/mode 门/dm 422 全活
# 真 server（H3/H5/H6）。此处仅喂 OpenAPI→rest.ts：GET /templates 含 builtin 工程三角形状占位。


def _builtin_triangle_template() -> dict[str, Any]:
    """工程三角 builtin 的形状占位（真值 = contracts 常量 + server 启动 upsert，H5）。"""
    return {
        "id": new_id(), "workspace_id": store.workspace["id"], "name": "工程三角",
        "description": "PM 框定→评审门→实现契约→TDD 实现→独立验收→人类终审（builtin 形状占位）",
        "builtin": True, "created_by_member_id": store.members[0]["id"], "created_at": now_ts(),
        "body": {
            "nodes": [
                {"key": "impl", "title": "实现", "role": "实现工程师", "plan_skeleton": None,
                 "writes_code": False, "project_id": None},
                {"key": "review", "title": "独立验收", "role": "评审工程师",
                 "plan_skeleton": None, "writes_code": False, "project_id": None},
            ],
            "edges": [{"from_key": "impl", "to_key": "review"}],
            "roles": [
                {"placeholder": "实现工程师", "description": "落地实现（doer）"},
                {"placeholder": "评审工程师", "description": "独立评审（checker ≠ doer）"},
            ],
            "briefing": "本频道由工程三角模板实例化：实现方交付、评审方独立复核、人类终审。",
        },
    }


@app.get("/api/templates", response_model=list[entities.TemplatePublic])
async def list_templates() -> Any:
    """工作区级列表（builtin 置前，body 全量携带——向导预览用）；用户模板 mock 恒空。"""
    return [_builtin_triangle_template()]


@app.post("/api/templates", response_model=entities.TemplatePublic, status_code=201)
async def create_template(body: rest.TemplateCreate) -> Any:
    """存为模板（B §4.12）：mock 回形状占位（不读画布、不校验 409，纪律 4）。"""
    return {
        "id": new_id(), "workspace_id": store.workspace["id"], "name": body.name,
        "description": body.description, "builtin": False,
        "created_by_member_id": store.members[0]["id"], "created_at": now_ts(),
        "body": {"nodes": [], "edges": [], "roles": [], "briefing": ""},
    }


@app.post("/api/templates/{template_id}/instantiate", response_model=rest.InstantiateResult,
          status_code=201)
async def instantiate_template(template_id: str, body: rest.TemplateInstantiate) -> Any:
    """实例化（B §4.12/§11.2）：mock 回落地批 + 空任务形状（单事务/幂等/briefing 活真 server）。"""
    batch = {
        "id": new_id(), "workspace_id": store.workspace["id"], "channel_id": body.channel_id,
        "kind": "tmpl", "content_hash": hashlib.sha256(template_id.encode()).hexdigest(),
        "source_ref": template_id, "confirmed_by": store.members[0]["id"], "status": "done",
        "created_at": now_ts(), "done_at": now_ts(),
    }
    return {"batch": batch, "tasks": []}


@app.get("/api/channels/{channel_id}/notification-setting",
         response_model=entities.ChannelNotificationSettingPublic)
async def get_notification_setting(channel_id: str) -> Any:
    """GET 无行回默认 {mode: all}（B §4.5）；dm 422 / Agent 403 活真 server（纪律 4）。"""
    require_channel(channel_id)
    return {"channel_id": channel_id, "member_id": store.members[0]["id"], "mode": "all"}


@app.put("/api/channels/{channel_id}/notification-setting",
         response_model=entities.ChannelNotificationSettingPublic)
async def put_notification_setting(channel_id: str, body: rest.NotificationSettingPut) -> Any:
    """PUT upsert 懒建（B §4.5）：mock 回请求 mode 的形状（自治/dm 422 活真 server）。"""
    require_channel(channel_id)
    return {"channel_id": channel_id, "member_id": store.members[0]["id"], "mode": body.mode}


# ---------------------------------------------------------------- M6 编排与交付链（纯形状）
#
# J0 只让 mock 为 OpenAPI/前端提供稳定形状。Project 权限与仓库校验、提案状态机、CAS、git
# diff、系统节点 retry 约束都只活在真 server/daemon（纪律 4）。


def _mock_project(project_id: str | None = None, **updates: Any) -> dict[str, Any]:
    build = next(c for c in store.channels if c.get("name") == "build")
    row = {
        "id": project_id or new_id(),
        "workspace_id": store.workspace["id"],
        "computer_id": store.computers[0]["id"],
        "name": "CoAgentia mock",
        "repo_path": r"C:\coagentia\mock-project",
        "dev_command": None,
        "deploy_command": None,
        "preview_idle_min": 30,
        "worktree_keep_days": 7,
        "created_at": now_ts(),
        "channel_ids": [build["id"]],
    }
    row.update({key: value for key, value in updates.items() if value is not None})
    return row


def _mock_proposal(
    proposal_id: str | None = None,
    *,
    channel_id: str | None = None,
    status: str = "awaiting_confirm",
) -> dict[str, Any]:
    body = {"version": "coagentia.decomposition.v1", "nodes": [], "edges": []}
    digest = hashlib.sha256(repr(body).encode("utf-8")).hexdigest()
    return {
        "id": proposal_id or new_id(),
        "workspace_id": store.workspace["id"],
        "channel_id": channel_id or store.tasks[0]["channel_id"],
        "source_task_id": store.tasks[0]["id"],
        "kind": "full",
        "revision": 1,
        "status": status,
        "body": body,
        "proposal_hash": digest,
        "base_hash": None,
        "landed_hash": digest if status == "landed" else None,
        "adjustments": [],
        "repair_count": 0,
        "proposed_by_member_id": store.members[0]["id"],
        "created_at": now_ts(),
        "updated_at": now_ts(),
    }


@app.post("/api/channels/{channel_id}/decompose", response_model=entities.ProposalPublic,
          status_code=202)
async def decompose(channel_id: str, body: rest.DecomposeRequest) -> Any:
    # 形状源非逻辑源（纪律 4）：sentinel 文本触发 409/503 错误变体的**形状**——真判定
    # （find_orchestrator / daemon 在线）只活在真 server；前端引导链（交互 §6.8）据 code 分派。
    if body.text == "__no_orchestrator__":
        raise ApiError(409, rest.ErrorCode.NO_ORCHESTRATOR, "本频道还没有协调 Agent")
    if body.text == "__daemon_offline__":
        raise ApiError(503, rest.ErrorCode.DAEMON_OFFLINE, "@Orchestrator 当前离线（机器断连）")
    return _mock_proposal(channel_id=channel_id)


@app.get("/api/proposals/{proposal_id}", response_model=entities.ProposalPublic)
async def get_proposal(proposal_id: str) -> Any:
    return _mock_proposal(proposal_id)


@app.post("/api/proposals/{proposal_id}/confirm", response_model=rest.ProposalConfirmResult,
          status_code=202)
async def confirm_proposal(proposal_id: str, body: rest.ProposalConfirm) -> Any:
    proposal = _mock_proposal(proposal_id, status="landed")
    batch = {
        "id": new_id(),
        "workspace_id": store.workspace["id"],
        "channel_id": proposal["channel_id"],
        "kind": "decomp",
        "content_hash": proposal["proposal_hash"],
        "source_ref": proposal_id,
        "confirmed_by": store.members[0]["id"],
        "status": "done",
        "created_at": now_ts(),
        "done_at": now_ts(),
    }
    return {"batch": batch, "proposal": proposal}


@app.post("/api/proposals/{proposal_id}/reject", response_model=entities.ProposalPublic)
async def reject_proposal(proposal_id: str, body: rest.ProposalReject) -> Any:
    return _mock_proposal(proposal_id, status="rejected")


@app.get("/api/projects", response_model=list[entities.ProjectPublic])
async def list_projects() -> Any:
    return [_mock_project()]


@app.post("/api/projects", response_model=entities.ProjectPublic, status_code=201)
async def create_project(body: rest.ProjectCreate) -> Any:
    return _mock_project(**body.model_dump(exclude_unset=True))


@app.patch("/api/projects/{project_id}", response_model=entities.ProjectPublic)
async def patch_project(project_id: str, body: rest.ProjectPatch) -> Any:
    return _mock_project(project_id, **body.model_dump(exclude_unset=True))


@app.delete("/api/projects/{project_id}", status_code=204)
async def delete_project(project_id: str) -> Response:
    return Response(status_code=204)


@app.post("/api/channels/{channel_id}/projects",
          response_model=entities.ChannelProjectPublic, status_code=201)
async def bind_project(channel_id: str, body: rest.ProjectBind) -> Any:
    return {"channel_id": channel_id, "project_id": body.project_id}


@app.delete("/api/channels/{channel_id}/projects/{project_id}", status_code=204)
async def unbind_project(channel_id: str, project_id: str) -> Response:
    return Response(status_code=204)


@app.get("/api/tasks/{task_id}/diff", response_model=daemon.DiffPayload)
async def get_task_diff(task_id: str) -> Any:
    return {
        "base_ref": "main",
        "head_ref": f"coagentia/task-{task_id}",
        "files": [{
            "path": "README.md",
            "status": "modified",
            "old_path": None,
            "additions": 1,
            "deletions": 0,
            "patch": "@@ -1 +1,2 @@\n # CoAgentia\n+mock diff\n",
            "patch_truncated": False,
        }],
        "total_additions": 1,
        "total_deletions": 0,
        "files_truncated": False,
    }


@app.post("/api/canvas-nodes/{node_id}/retry", response_model=entities.CanvasNodePublic,
          status_code=202)
async def retry_canvas_node(node_id: str) -> Any:
    canvas = store.canvases[0]
    return {
        "id": node_id,
        "canvas_id": canvas["id"],
        "kind": "system",
        "task_id": None,
        "is_summary": False,
        "system_action": "check",
        "command": "pnpm test",
        "system_status": "running",
        "pos_x": 0,
        "pos_y": 0,
        "created_at": now_ts(),
    }


@app.patch("/api/templates/{template_id}", response_model=entities.TemplatePublic)
async def patch_template(template_id: str, body: rest.TemplatePatch) -> Any:
    row = _builtin_triangle_template()
    row["id"] = template_id
    row.update({
        key: value for key, value in body.model_dump(exclude_unset=True).items()
        if value is not None
    })
    return row


@app.delete("/api/templates/{template_id}", status_code=204)
async def delete_template(template_id: str) -> Response:
    return Response(status_code=204)


# ---------------------------------------------------------------- M7 预览/部署/成本（纯形状）
#
# K0 登记：mock 只验形状不做业务（纪律 4）——健康检查/端口分配/回收调度/409 不排队/聚合 SQL/
# 新账推导只活真 server/daemon。此处仅喂 OpenAPI→rest.ts：预览会话 / 部署 / 日志翻页 / usage
# 三层形状（PreviewSessionPublic / DeploymentPublic / DeploymentLogPage / UsageReport）。


def _mock_usage_bucket() -> dict[str, Any]:
    return {"input_tokens": 1200, "output_tokens": 450, "cache_read_tokens": 800,
            "cache_write_tokens": 200, "events": 3}


def _mock_token_summary() -> dict[str, Any]:
    """新账口径快照占位（B §13.4）；真值 = server 触发时纯 SQL 推导落列。"""
    return {"usage": _mock_usage_bucket(),
            "tasks_reporting": {"reporting": 1, "total": 2},
            "task_ids": [t["id"] for t in store.tasks[:1]]}


def _mock_preview_session(task_id: str, *, status: str = "running") -> dict[str, Any]:
    return {
        "id": new_id(), "workspace_id": store.workspace["id"], "task_id": task_id,
        "worktree_id": new_id(),
        "port": 43117 if status == "running" else None,
        "status": status, "fail_log_tail": None,
        "started_at": now_ts(), "last_active_at": now_ts(),
        "recycled_at": now_ts() if status == "recycled" else None,
    }


def _mock_deployment(deployment_id: str | None = None, *, project_id: str | None = None,
                     status: str = "success") -> dict[str, Any]:
    terminal = status in ("success", "failed")
    return {
        "id": deployment_id or new_id(), "workspace_id": store.workspace["id"],
        "project_id": project_id or new_id(),
        "triggered_by_member_id": store.members[0]["id"],
        "branch": "main", "commit_hash": "0" * 40, "command": "npm run deploy",
        "status": status,
        "exit_code": 0 if status == "success" else (1 if status == "failed" else None),
        "url": "https://preview.example.com/app" if status == "success" else None,
        "token_summary": _mock_token_summary(),
        "started_at": now_ts() if status != "queued" else None,
        "finished_at": now_ts() if terminal else None,
    }


@app.post("/api/tasks/{task_id}/preview", response_model=entities.PreviewSessionPublic)
async def start_preview(task_id: str) -> Any:
    """ensure+touch 幂等（B §13.1）：mock 回 running 会话形状；健康检查/端口分配活真 daemon。"""
    require_task(task_id)
    session = _mock_preview_session(task_id)
    await hub.broadcast(EventType.PREVIEW_UPDATED, None, {"preview": session})
    return session


@app.get("/api/tasks/{task_id}/preview", response_model=entities.PreviewSessionPublic)
async def get_preview(task_id: str) -> Any:
    """纯读（B §13.1）：mock 回现状形状；无活跃会话 404 活真 server。"""
    require_task(task_id)
    return _mock_preview_session(task_id)


@app.delete("/api/tasks/{task_id}/preview", response_model=entities.PreviewSessionPublic)
async def stop_preview(task_id: str) -> Any:
    """下发 preview.stop（B §13.1）：mock 回 recycled 形状；回收判定活真 server。"""
    require_task(task_id)
    session = _mock_preview_session(task_id, status="recycled")
    await hub.broadcast(EventType.PREVIEW_UPDATED, None, {"preview": session})
    return session


@app.post("/api/projects/{project_id}/deployments",
          response_model=entities.DeploymentPublic, status_code=202)
async def create_deployment(project_id: str) -> Any:
    """触发部署（B §13.2；R8 全员含 Agent 无角色校验，请求体空）：mock 回 queued 形状；
    409 不排队/branch·commit 直查主干 HEAD/deploy.run 下发活真 server。"""
    deployment = _mock_deployment(project_id=project_id, status="queued")
    await hub.broadcast(EventType.DEPLOYMENT_CREATED, None, {"deployment": deployment})
    return deployment


@app.get("/api/deployments/{deployment_id}", response_model=entities.DeploymentPublic)
async def get_deployment(deployment_id: str) -> Any:
    return _mock_deployment(deployment_id, status="success")


@app.get("/api/deployments/{deployment_id}/log", response_model=rest.DeploymentLogPage)
async def get_deployment_log(deployment_id: str, after: int = 0) -> Any:
    """server 直读落盘日志（B §13.3，不依赖 daemon 在线）：mock 回固定尾巴形状（无翻页）。"""
    return {
        "lines": ["$ npm run deploy", "build ok",
                  "deployed https://preview.example.com/app"],
        "next_after": None, "truncated": False,
    }


@app.get("/api/usage", response_model=rest.UsageReport)
async def get_usage(level: UsageLevel = UsageLevel.TASK, ref: str | None = None,
                    rollup: bool = False) -> Any:
    """三层成本聚合（B §13.4；永不折算货币）：mock 回形状源；聚合 SQL/覆盖率/新账活真 server。"""
    default_ref = store.tasks[0]["id"] if store.tasks else new_id()
    report: dict[str, Any] = {
        "level": level.value, "ref": ref or default_ref, "usage": _mock_usage_bucket(),
        "tasks_reporting": ({"reporting": 1, "total": 1} if level == UsageLevel.TASK
                            else {"reporting": 1, "total": 2}),
        "breakdown": None,
    }
    if rollup and level != UsageLevel.TASK:
        report["breakdown"] = [
            {"ref": default_ref, "label": "任务 #1", "usage": _mock_usage_bucket()},
        ]
    return report


# ---------------------------------------------------- PS-WT 目录浏览/工作树管理台（纯形状）
#
# 登记：mock 只验形状不做业务（纪律 4）——真盘符枚举/worktrees_dir 扫描/合账矩阵/CAS 清理/
# 护栏（worktrees_dir 边界、ULID 命名过滤）全活真 server/daemon。此处仅喂 OpenAPI→rest.ts。


def _mock_worktree(worktree_id: str | None = None, *, status: str = "cleaned") -> dict[str, Any]:
    return {
        "id": worktree_id or new_id(), "workspace_id": store.workspace["id"],
        "project_id": new_id(), "task_id": new_id(),
        "branch": "coagentia/task-mock", "path": r"C:\coagentia\worktrees\p\t",
        "status": status, "merge_commit": "0" * 40 if status == "merged" else None,
        "created_at": now_ts(), "merged_at": now_ts() if status == "merged" else None,
        "cleaned_at": now_ts() if status == "cleaned" else None,
    }


@app.get("/api/computers/{computer_id}/fs", response_model=daemon.FsTreeReply)
async def browse_fs(computer_id: str, path: str | None = None) -> Any:
    """computer 级只读目录浏览（选择仓库路径）：mock 回固定两层形状；真盘符枚举活真 daemon。"""
    if path is None:
        return {"entries": [
            {"name": "C:\\", "path": "C:\\", "has_git": False, "denied": False},
            {"name": "D:\\", "path": "D:\\", "has_git": False, "denied": False},
        ], "truncated": False}
    return {"entries": [
        {"name": "coagentia", "path": path.rstrip("\\/") + "\\coagentia",
         "has_git": True, "denied": False},
        {"name": "node_modules", "path": path.rstrip("\\/") + "\\node_modules",
         "has_git": False, "denied": False},
        {"name": "System Volume Information", "path": path.rstrip("\\/") + "\\svi",
         "has_git": False, "denied": True},
    ], "truncated": False}


@app.get("/api/worktrees", response_model=rest.WorktreeConsoleReply)
async def list_worktrees(live: int = 0) -> Any:
    """工作树管理台读面（B §4.11 扩）：mock 回骨架 + 孤儿行形状；合账/live 字段活真 server。"""
    computer_id = store.computers[0]["id"]
    project_id = new_id()
    items: list[dict[str, Any]] = [
        {"id": new_id(), "project_id": project_id, "project_name": "CoAgentia mock",
         "computer_id": computer_id, "task_id": new_id(), "task_title": "重构画布",
         "channel_id": store.tasks[0]["channel_id"] if store.tasks else None,
         "branch": "coagentia/task-a", "path": r"C:\wt\p\a", "status": "active",
         "derived": "ok", "merge_commit": None, "created_at": now_ts(),
         "merged_at": None, "cleaned_at": None,
         "live": ({"dirty": True, "ahead": 0, "behind": 3, "head_commit": "abc1234"}
                  if live else None)},
        {"id": new_id(), "project_id": project_id, "project_name": "CoAgentia mock",
         "computer_id": computer_id, "task_id": new_id(), "task_title": "修复登录",
         "channel_id": None, "branch": "coagentia/task-b", "path": r"C:\wt\p\b",
         "status": "merged", "derived": "ok", "merge_commit": "0" * 40,
         "created_at": now_ts(), "merged_at": now_ts(), "cleaned_at": None, "live": None},
    ]
    if live:
        items.append(
            {"id": None, "project_id": project_id, "project_name": "CoAgentia mock",
             "computer_id": computer_id, "task_id": new_id(), "task_title": None,
             "channel_id": None, "branch": None, "path": r"C:\wt\p\orphan",
             "status": None, "derived": "orphan", "merge_commit": None,
             "created_at": None, "merged_at": None, "cleaned_at": None,
             "live": {"dirty": False, "ahead": None, "behind": None, "head_commit": None}})
    scans = ([{"computer_id": computer_id, "status": "ok"}] if live else [])
    return {"items": items, "scans": scans}


@app.post("/api/worktrees/{worktree_id}/cleanup", response_model=entities.WorktreePublic)
async def cleanup_worktree(worktree_id: str) -> Any:
    """清理登记的终态树（B §4.11 扩）：mock 回 cleaned 形状 + 广播；CAS/预览门/护栏活真 server。"""
    worktree = _mock_worktree(worktree_id, status="cleaned")
    await hub.broadcast(EventType.WORKTREE_UPDATED, None, {"worktree": worktree})
    return worktree


@app.post("/api/computers/{computer_id}/worktrees/cleanup-orphan",
          response_model=rest.OrphanCleanupResult)
async def cleanup_orphan(computer_id: str, body: rest.OrphanCleanup) -> Any:
    """清理磁盘孤儿树（B §4.11 扩）：无 DB 行、不广播；mock 回 removed 形状，判定活真 server。"""
    return {"project_id": body.project_id, "task_id": body.task_id, "removed": True}


# ---------------------------------------------------------------- WS 与 mock 控制面


@app.websocket("/api/ws")
async def ws_endpoint(sock: WebSocket) -> None:
    await hub.attach(sock)
    try:
        while True:
            msg = await sock.receive_json()
            if msg.get("type") == "ping":
                await hub.pong(sock)
            # sub/unsub：mock 无高吞吐流，接受并忽略（幂等）
    except WebSocketDisconnect:
        hub.detach(sock)


@app.post("/__mock/play", status_code=202)
async def play_timeline() -> Any:
    """回放 fixtures 时间线（M6 一屏验证的驱动按钮）。

    内联 await：事件在各 delay 间实时广播，响应在回放完成后返回——
    单 loop 下无孤儿任务，TestClient 与 uvicorn 行为一致。
    """
    await hub.play_timeline()
    return {"events": len(store.timeline)}


@app.post("/__mock/reset", status_code=200)
async def reset_state() -> Any:
    global store, hub
    conns = hub.conns
    store = Store()
    hub = Hub(store)
    hub.conns = conns
    return {"ok": True}


def main() -> None:
    uvicorn.run(app, host="127.0.0.1", port=MOCK_PORT)


if __name__ == "__main__":
    main()
