"""契约驱动 mock server：fixtures over REST（契约 B M1 端点）+ WS 事件（契约 C）。

- 响应形状 = contracts 的 *Public 模型（response_model 强制，吐出去的就是契约形状）；
- OpenAPI 导出即 TS 生成管线的 REST 源（00 §4.4）；
- `/__mock/*` 前缀 = mock 专用控制面（时间线回放），不属契约。
"""

import hashlib
from typing import Any

import uvicorn
from coagentia_contracts import entities, rest
from coagentia_contracts.enums import ActivityFilter, SearchKind
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
    if body.kind == "recurring" and body.loop_contract_id is None:
        raise ApiError(422, rest.ErrorCode.VALIDATION_FAILED,
                       "循环 reminder 必须先提交 LoopContract", rule="D1-L2",
                       details={"missing": ["loop_contract_id"]})
    row = {"id": new_id(), "workspace_id": store.workspace["id"],
           "agent_member_id": store.agents[0]["member_id"], "kind": body.kind,
           "cadence": body.cadence, "anchor_channel_id": body.anchor_channel_id,
           "anchor_message_id": body.anchor_message_id, "anchor_task_id": body.anchor_task_id,
           "loop_contract_id": body.loop_contract_id, "next_fire_at": now_ts(),
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
    return {"items": store.channels,
            "read_positions": [r for r in store.read_positions if r["member_id"] == me]}


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
    return {"task": task, "contracts": [], "usage": usage}


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
                 limit: int = rest.PAGE_DEFAULT_LIMIT) -> Any:
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
