"""coagentia stdio MCP server（契约 E §3）：Agent 一切主动行为的唯一出口。

`coagentia-daemon mcp --agent-member <id> --server-url <url> --api-key <key>`
由 claude 子进程经 --mcp-config 拉起。M1 最小工具集 → 契约 B REST 端点的**纯代理**
（Bearer + X-Acting-Member）；权限门 / 护栏 / 留痕全部在 server 单点执法，MCP 层零业务规则。

- send_message 命中 freshness → 202 held，本层**原样结构化透传**（M4 前不触发，形状先对）。
- 协议：newline-delimited JSON-RPC 2.0（initialize / tools/list / tools/call / ping）。
- HTTP 层可注入（测试用桩），真跑用 urllib（daemon 无 httpx 依赖）。
"""

from __future__ import annotations

import json
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TextIO
from urllib.parse import urlencode

from coagentia_contracts.enums import ContractKind, TaskStatus

MCP_PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "coagentia"
SERVER_VERSION = "1.0.0"

# 状态值域从契约枚举派生（单一事实源）——手写字面量会在 M3 状态机演进时漂移。
_TASK_STATUS_VALUES = [s.value for s in TaskStatus]
# 任务契约 kind 值域（submit_task_contract）：loop_contract 属 Reminder 域、端点会 422 拒，
# 故此处只列 POST /tasks/{id}/contracts 受理的两 kind（值从枚举派生、序确定）。
_CONTRACT_KIND_VALUES = [ContractKind.TASK_PLAN.value, ContractKind.TASK_HANDOFF.value]


@dataclass
class ToolRequest:
    """工具 → REST 请求的中间表示（可脱离 HTTP 单测映射正确性）。"""

    method: str
    path: str  # 含 /api 前缀
    query: dict[str, Any] | None = None
    json_body: dict[str, Any] | None = None
    upload_path: str | None = None  # upload_file：multipart 文件源
    download: bool = False  # get_file：返回二进制元信息


@dataclass
class ToolResult:
    status: int
    data: Any
    is_error: bool = False


# ------------------------------------------------------------ 工具目录（M1 最小集，E §3）

TOOLS: list[dict[str, Any]] = [
    {
        "name": "send_message",
        "description": "在频道发消息（唯一发言出口）。命中 freshness 时返回 202 held 结构，"
        "此时停止重发、等待反馈。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string"},
                "body": {"type": "string"},
                "thread_root_id": {"type": "string"},
                "file_ids": {"type": "array", "items": {"type": "string"}},
                "as_task": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                },
            },
            "required": ["channel_id", "body"],
        },
    },
    {
        "name": "get_messages",
        "description": "回看频道历史消息（投递批之外的按需拉取）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string"},
                "limit": {"type": "integer"},
                "before": {"type": "string"},
                "after": {"type": "string"},
            },
            "required": ["channel_id"],
        },
    },
    {
        "name": "get_thread",
        "description": "拉取某消息所在线程的全部消息。",
        "inputSchema": {
            "type": "object",
            "properties": {"message_id": {"type": "string"}},
            "required": ["message_id"],
        },
    },
    {
        "name": "upload_file",
        "description": "上传本地（Home 内）文件到 staging，返回 file_id。",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "get_file",
        "description": "按 file_id 拉取文件内容元信息。",
        "inputSchema": {
            "type": "object",
            "properties": {"file_id": {"type": "string"}},
            "required": ["file_id"],
        },
    },
    {
        "name": "create_reminder",
        "description": "创建提醒（recurring 缺 loop_contract → 422 原样透传）。"
        "cadence：once = ISO 时刻；recurring = interval（ISO-8601 duration，如 PT1H）"
        "或 cron 五段式（分 时 日 月 周，服务器本地时区）。"
        "recurring 须内联 loop_contract 且其 cadence 与本 cadence 一致。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["once", "recurring"]},
                "cadence": {"type": "string"},
                "anchor_channel_id": {"type": "string"},
                "anchor_message_id": {"type": "string"},
                "anchor_task_id": {"type": "string"},
                "loop_contract": {
                    "type": "object",
                    "description": "recurring 必填 LoopContract（PRD §4.3；随建即生效）。",
                    "properties": {
                        "version": {"type": "string"},
                        # cadence 须与 reminder cadence 一致（interval 如 PT1H，或 cron 五段式）
                        "cadence": {"type": "string"},
                        "verification": {"type": "array", "items": {"type": "string"}},
                        "budget": {"type": "object"},
                        "tools": {"type": "array", "items": {"type": "string"}},
                        "escalation": {"type": "string"},
                    },
                },
            },
            "required": ["kind", "cadence", "anchor_channel_id"],
        },
    },
    {
        "name": "cancel_reminder",
        "description": "取消一个提醒。",
        "inputSchema": {
            "type": "object",
            "properties": {"reminder_id": {"type": "string"}},
            "required": ["reminder_id"],
        },
    },
    {
        "name": "list_channels",
        "description": "列出工作区频道（自我融入所需读面）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_members",
        "description": "列出工作区成员（自我融入所需读面）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    # M2（契约 E v1.1）任务域 + 搜索
    {
        "name": "list_tasks",
        "description": "列出任务（可按频道 / 状态 / owner / 创建者过滤，游标分页）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string"},
                "status": {"type": "string", "enum": _TASK_STATUS_VALUES},
                "owner": {"type": "string"},
                "creator": {"type": "string"},
                "after": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "get_task",
        "description": "拉取单个任务详情（含成本聚合 usage）。",
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "claim_task",
        "description": "认领无主任务（并发抢占失败 → 409 CLAIM_RACE 结构化透传）。",
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "unclaim_task",
        "description": "释放自己认领的任务（仅本人为 owner 时有效）。",
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "set_task_status",
        "description": "推进任务状态（非法边 → 422 TASK_TRANSITION_INVALID 透传）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "to": {"type": "string", "enum": _TASK_STATUS_VALUES},
            },
            "required": ["task_id", "to"],
        },
    },
    {
        "name": "search",
        "description": "跨工作区搜索（频道 / 成员跳转 + 消息 FTS + 任务，三分组）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "q": {"type": "string"},
                "kind": {"type": "string", "enum": ["message", "task"]},
                "from_member": {"type": "string"},
                "in_channel": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["q"],
        },
    },
    # M7（契约 E v1.5）部署——R8「部署全员含 Agent」的通道兑现
    {
        "name": "trigger_deploy",
        "description": "触发一次部署（全员含 Agent，R8）。请求体空——"
        "分支/commit 由 server 触发时解析主干 HEAD。"
        "进行中→409 DEPLOY_IN_PROGRESS / 无 deploy_command→422 / daemon 离线→503，均结构化透传。"
        "部署结果经绑定频道的结果卡消息被动触达（无需轮询）。",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": {"type": "string"}},
            "required": ["project_id"],
        },
    },
    # M8-B5（契约 E v1.6）契约提交——置任务 in_review/done 的前置通道
    {
        "name": "submit_task_contract",
        "description": "提交/修订任务契约（置任务 in_review/done 的前置——T7 门要求活动 "
        "TaskHandoff 的 deliverables/evidence 非空，缺则 set_task_status 以 422 "
        "HANDOFF_INCOMPLETE 退回）。\n"
        "kind=task_handoff（完成实现/评审后的跨 Agent 交接）字段："
        "version='coagentia.task-handoff.v1'、from_member（你的 member_id）、"
        "to_member（接收方 member_id：评审人/人类/下游）、"
        "deliverables=[{path,kind}]（置 in_review 前须≥1）、"
        "evidence=[{type,ref,conclusion}]、verify_plan（接收方如何独立复核）、"
        "open_risks=[]（可空）、review_verdict（可空）。\n"
        "kind=task_plan（立项/升格计划）字段：version='coagentia.task-plan.v1'、goal、"
        "acceptance_criteria=[{id,statement,verify_by,verify_ref}]（≥1）、"
        "defaults_decided=[]、out_of_scope=[]。\n"
        "字段不符 → 422 VALIDATION_FAILED 携逐字段 loc/msg，按清单补齐重投即可（同 kind "
        "重复提交自动 supersede 成修订链，不新建重复行）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "kind": {"type": "string", "enum": _CONTRACT_KIND_VALUES},
                "body": {
                    "type": "object",
                    "description": "契约内容，按 kind 对应 TaskHandoffBody / TaskPlanBody"
                    "（server 二次 model_validate，字段见上）。",
                },
            },
            "required": ["task_id", "kind", "body"],
        },
    },
]

_TOOL_NAMES = frozenset(t["name"] for t in TOOLS)


def build_request(tool: str, args: dict[str, Any]) -> ToolRequest:
    """工具调用参数 → REST 请求（契约 B 端点，每工具一一对应，不发明无端点工具）。"""
    a = args or {}
    if tool == "send_message":
        body: dict[str, Any] = {"body": a.get("body", "")}
        if a.get("thread_root_id"):
            body["thread_root_id"] = a["thread_root_id"]
        if a.get("file_ids"):
            body["file_ids"] = a["file_ids"]
        if a.get("as_task") is not None:  # 空 {} 也透传 → server 用缺省 title（契约 AsTask）
            body["as_task"] = a["as_task"]
        return ToolRequest("POST", f"/api/channels/{a['channel_id']}/messages", json_body=body)
    if tool == "get_messages":
        query = {k: a[k] for k in ("limit", "before", "after") if a.get(k) is not None}
        return ToolRequest("GET", f"/api/channels/{a['channel_id']}/messages", query=query or None)
    if tool == "get_thread":
        return ToolRequest("GET", f"/api/messages/{a['message_id']}/thread")
    if tool == "upload_file":
        return ToolRequest("POST", "/api/files", upload_path=a["path"])
    if tool == "get_file":
        return ToolRequest("GET", f"/api/files/{a['file_id']}/content", download=True)
    if tool == "create_reminder":
        fields = (
            "kind",
            "cadence",
            "anchor_channel_id",
            "anchor_message_id",
            "anchor_task_id",
            "loop_contract",
        )
        body = {k: a[k] for k in fields if a.get(k) is not None}
        return ToolRequest("POST", "/api/reminders", json_body=body)
    if tool == "cancel_reminder":
        return ToolRequest("DELETE", f"/api/reminders/{a['reminder_id']}")
    if tool == "list_channels":
        return ToolRequest("GET", "/api/channels")
    if tool == "list_members":
        return ToolRequest("GET", "/api/members")
    if tool == "list_tasks":
        query = {
            k: a[k]
            for k in ("channel_id", "status", "owner", "creator", "after", "limit")
            if a.get(k) is not None
        }
        return ToolRequest("GET", "/api/tasks", query=query or None)
    if tool == "get_task":
        return ToolRequest("GET", f"/api/tasks/{a['task_id']}")
    if tool == "claim_task":
        return ToolRequest("POST", f"/api/tasks/{a['task_id']}/claim")
    if tool == "unclaim_task":
        return ToolRequest("POST", f"/api/tasks/{a['task_id']}/unclaim")
    if tool == "set_task_status":
        return ToolRequest("POST", f"/api/tasks/{a['task_id']}/status", json_body={"to": a["to"]})
    if tool == "search":
        query = {
            k: a[k]
            for k in ("q", "kind", "from_member", "in_channel", "limit")
            if a.get(k) is not None
        }
        return ToolRequest("GET", "/api/search", query=query)
    if tool == "trigger_deploy":  # 空请求体：分支/commit 由 server 解析主干 HEAD
        return ToolRequest("POST", f"/api/projects/{a['project_id']}/deployments")
    if tool == "submit_task_contract":  # body free-form 透传，server 按 kind 二次校验
        return ToolRequest(
            "POST",
            f"/api/tasks/{a['task_id']}/contracts",
            json_body={"kind": a["kind"], "body": a["body"]},
        )
    raise KeyError(tool)


# HTTP 执行口（(ToolRequest) -> ToolResult），真跑用 urllib，测试注入桩。
HttpFn = Callable[[ToolRequest], ToolResult]


def call_tool(tool: str, args: dict[str, Any], http: HttpFn) -> dict[str, Any]:
    """执行工具 → MCP tools/call result（content[0].text = JSON 字符串）。

    202 held / 4xx / 422 一律**原样结构化透传**（含 status），Agent 据此感知被扣/失败。
    """
    if tool not in _TOOL_NAMES:
        return _text_result({"error": "unknown_tool", "tool": tool}, is_error=True)
    try:
        req = build_request(tool, args)
    except KeyError as exc:
        return _text_result({"error": "missing_argument", "detail": str(exc)}, is_error=True)
    res = http(req)
    payload = {"status": res.status, "data": res.data}
    if res.status == 202:
        payload["held"] = True  # freshness 命中：Agent 停止重发、等待反馈直投（D §5.2）
    is_error = res.is_error or res.status >= 400
    return _text_result(payload, is_error=is_error)


def _text_result(obj: Any, *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(obj, ensure_ascii=False)}],
        "isError": is_error,
    }


# ------------------------------------------------------------ 真 HTTP（urllib）


def make_urllib_http(server_url: str, api_key: str, acting_member: str) -> HttpFn:
    import urllib.error
    import urllib.request

    base = server_url.rstrip("/")

    def http(req: ToolRequest) -> ToolResult:
        url = base + req.path
        if req.query:
            url += "?" + urlencode(req.query)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "X-Acting-Member": acting_member,
        }
        data: bytes | None = None
        if req.upload_path is not None:
            data, ctype = _multipart_file(req.upload_path)
            headers["Content-Type"] = ctype
        elif req.json_body is not None:
            data = json.dumps(req.json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, method=req.method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as resp:  # noqa: S310
                status = resp.status
                raw = resp.read()
                ctype = resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw = exc.read()
            ctype = exc.headers.get("Content-Type", "") if exc.headers else ""
        except Exception as exc:  # noqa: BLE001 — 网络异常收敛为 isError（不崩 MCP 进程）
            return ToolResult(0, {"error": "http_error", "detail": repr(exc)}, is_error=True)
        if req.download:
            return ToolResult(status, {"size_bytes": len(raw), "mime": ctype or None})
        parsed = _parse_body(raw, ctype)
        return ToolResult(status, parsed, is_error=status >= 400)

    return http


def _parse_body(raw: bytes, ctype: str) -> Any:
    if not raw:
        return None
    if "json" in ctype:
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"bytes": len(raw)}


def _multipart_file(path: str) -> tuple[bytes, str]:
    import mimetypes
    import os

    boundary = f"----coagentia{uuid.uuid4().hex}"
    name = os.path.basename(path)
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        content = f.read()
    pre = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode()
    post = f"\r\n--{boundary}--\r\n".encode()
    return pre + content + post, f"multipart/form-data; boundary={boundary}"


# ------------------------------------------------------------ JSON-RPC stdio 循环


@dataclass
class _RpcState:
    http: HttpFn
    initialized: bool = False
    extra_log: list[str] = field(default_factory=list)


def handle_rpc(msg: dict[str, Any], state: _RpcState) -> dict[str, Any] | None:
    """处理一条 JSON-RPC 请求 → 响应对象（notification 返回 None）。"""
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        return _ok(
            mid,
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )
    if method in ("notifications/initialized", "initialized"):
        state.initialized = True
        return None
    if method == "ping":
        return _ok(mid, {})
    if method == "tools/list":
        return _ok(mid, {"tools": TOOLS})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name", "")
        args = params.get("arguments") or {}
        return _ok(mid, call_tool(name, args, state.http))
    if mid is None:
        return None  # 未知 notification → 忽略
    return _err(mid, -32601, f"method not found: {method}")


def _ok(mid: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _err(mid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def serve_stdio(http: HttpFn, stdin: Any = None, stdout: TextIO | None = None) -> None:
    """同步 newline-delimited JSON-RPC 循环（stdin EOF → 退出）。

    解析失败必须回 JSON-RPC parse error（id=null）而非静默丢弃（CR-M8-2）：请求丢了不回声，
    claude 侧对应 tools/call 会**无限等待**——wedge 教训「状态怎么出去」同族。
    """
    rin = stdin if stdin is not None else sys.stdin
    rout = stdout if stdout is not None else sys.stdout
    state = _RpcState(http=http)

    def reply(obj: dict[str, Any]) -> None:
        rout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        rout.flush()

    for line in rin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            reply(_err(None, -32700, f"parse error: {exc}"))
            continue
        response = handle_rpc(msg, state)
        if response is not None:
            reply(response)


def _reconfigure_stdio_utf8() -> None:
    """win32 stdio 编码校准（CR-M8-2；GIT-CALIBRATION「git stdout 显式 UTF-8」同族）。

    claude 子进程写给 MCP 的管道恒为 UTF-8，但 win32 Python(<3.15) 对管道 stdio 默认
    locale 编码（中文系统 = GBK）：中文载荷必 mojibake；GBK 非法序列 UnicodeDecodeError
    崩掉读循环（claude 报工具超时）；GBK 前导字节吞掉 JSON 结构引号 → JSONDecodeError
    （修复前被静默丢弃 → claude 无限挂起）。双向 reconfigure 为 UTF-8 根治。
    """
    for stream in (sys.stdin, sys.stdout):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


def run(agent_member_id: str, server_url: str, api_key: str) -> int:
    """`coagentia-daemon mcp` 入口。"""
    _reconfigure_stdio_utf8()
    http = make_urllib_http(server_url, api_key, agent_member_id)
    serve_stdio(http)
    return 0
