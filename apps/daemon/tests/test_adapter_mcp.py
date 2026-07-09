"""coagentia MCP server 单测（E §3）：工具→REST 映射、JSON-RPC、held 透传。"""

from __future__ import annotations

import io
import json

from coagentia_daemon.adapters import mcp


class StubHttp:
    """注入 HTTP：记录请求、返回预置响应。"""

    def __init__(self, status: int = 201, data=None) -> None:
        self.status = status
        self.data = data if data is not None else {"ok": True}
        self.calls: list[mcp.ToolRequest] = []

    def __call__(self, req: mcp.ToolRequest) -> mcp.ToolResult:
        self.calls.append(req)
        return mcp.ToolResult(self.status, self.data, is_error=self.status >= 400)


def test_build_request_mapping() -> None:
    args = {"channel_id": "C1", "body": "hi", "thread_root_id": "T1"}
    r = mcp.build_request("send_message", args)
    assert r.method == "POST"
    assert r.path == "/api/channels/C1/messages"
    assert r.json_body == {"body": "hi", "thread_root_id": "T1"}

    assert mcp.build_request("get_thread", {"message_id": "M9"}).path == "/api/messages/M9/thread"
    assert mcp.build_request("cancel_reminder", {"reminder_id": "R1"}).method == "DELETE"
    assert mcp.build_request("list_channels", {}).path == "/api/channels"
    gm = mcp.build_request("get_messages", {"channel_id": "C1", "limit": 5})
    assert gm.query == {"limit": 5}


def test_call_tool_success() -> None:
    http = StubHttp(status=201, data={"message": {"id": "01K5MSG100000000000000000A"}})
    out = mcp.call_tool("send_message", {"channel_id": "C1", "body": "hi"}, http)
    assert out["isError"] is False
    payload = json.loads(out["content"][0]["text"])
    assert payload["status"] == 201
    assert payload["data"]["message"]["id"] == "01K5MSG100000000000000000A"


def test_call_tool_held_passthrough() -> None:
    """freshness 命中 → 202 held 原样结构化透传（M4 前不触发，形状先对）。"""
    data = {"held_draft": {"id": "01K5HELD0000000000000000A"}, "reasons": ["stale"]}
    http = StubHttp(status=202, data=data)
    out = mcp.call_tool("send_message", {"channel_id": "C1", "body": "hi"}, http)
    payload = json.loads(out["content"][0]["text"])
    assert payload["status"] == 202
    assert payload["held"] is True
    assert payload["data"]["reasons"] == ["stale"]


def test_call_tool_unknown_tool() -> None:
    http = StubHttp()
    out = mcp.call_tool("nonexistent_tool", {}, http)
    assert out["isError"] is True
    assert http.calls == []  # 未知工具不触 HTTP


def test_call_tool_error_status() -> None:
    http = StubHttp(status=422, data={"code": "LOOP_CONTRACT_REQUIRED"})
    out = mcp.call_tool("create_reminder", {"kind": "recurring", "body": "x"}, http)
    assert out["isError"] is True
    assert json.loads(out["content"][0]["text"])["status"] == 422


def test_jsonrpc_initialize_and_tools_list() -> None:
    state = mcp._RpcState(http=StubHttp())
    init = mcp.handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, state)
    assert init["result"]["serverInfo"]["name"] == "coagentia"
    assert "tools" in init["result"]["capabilities"]
    listed = mcp.handle_rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, state)
    names = {t["name"] for t in listed["result"]["tools"]}
    assert {"send_message", "get_messages", "list_channels", "upload_file"} <= names


def test_jsonrpc_notification_returns_none() -> None:
    state = mcp._RpcState(http=StubHttp())
    assert mcp.handle_rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}, state) is None
    assert state.initialized is True


def test_serve_stdio_full_roundtrip() -> None:
    http = StubHttp(status=201, data={"message": {"id": "01K5MSG100000000000000000A"}})
    lines = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "send_message", "arguments": {"channel_id": "C1", "body": "hi"}},
            }
        ),
    ]
    stdin = io.StringIO("\n".join(lines) + "\n")
    stdout = io.StringIO()
    mcp.serve_stdio(http, stdin=stdin, stdout=stdout)
    responses = [json.loads(x) for x in stdout.getvalue().splitlines() if x.strip()]
    # initialize + tools/call 两条响应（notification 无响应）
    assert len(responses) == 2
    assert responses[0]["id"] == 1
    assert responses[1]["id"] == 2
    assert responses[1]["result"]["isError"] is False
    assert len(http.calls) == 1
