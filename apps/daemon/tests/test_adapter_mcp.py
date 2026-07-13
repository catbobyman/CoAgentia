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


def test_build_request_m2_task_tools() -> None:
    """M2 六工具 → REST 端点映射（纯透传，method/path/query/json_body 一一断言）。"""
    lt = mcp.build_request(
        "list_tasks",
        {"channel_id": "C1", "status": "todo", "owner": "M1", "limit": 5},
    )
    assert lt.method == "GET"
    assert lt.path == "/api/tasks"
    assert lt.query == {"channel_id": "C1", "status": "todo", "owner": "M1", "limit": 5}
    assert lt.json_body is None

    # 全空过滤 → query 收敛为 None（不发空 querystring）
    assert mcp.build_request("list_tasks", {}).query is None

    gt = mcp.build_request("get_task", {"task_id": "T1"})
    assert gt.method == "GET"
    assert gt.path == "/api/tasks/T1"

    cl = mcp.build_request("claim_task", {"task_id": "T1"})
    assert cl.method == "POST"
    assert cl.path == "/api/tasks/T1/claim"
    assert cl.json_body is None

    un = mcp.build_request("unclaim_task", {"task_id": "T1"})
    assert un.method == "POST"
    assert un.path == "/api/tasks/T1/unclaim"
    assert un.json_body is None

    ss = mcp.build_request("set_task_status", {"task_id": "T1", "to": "in_progress"})
    assert ss.method == "POST"
    assert ss.path == "/api/tasks/T1/status"
    assert ss.json_body == {"to": "in_progress"}

    se = mcp.build_request(
        "search", {"q": "hello", "kind": "message", "from_member": "M1", "in_channel": "C1"}
    )
    assert se.method == "GET"
    assert se.path == "/api/search"
    assert se.query == {"q": "hello", "kind": "message", "from_member": "M1", "in_channel": "C1"}


def test_set_task_status_body_matches_contract() -> None:
    """set_task_status 的 body 必须过 TaskStatusChange 契约校验。"""
    from coagentia_contracts.rest import TaskStatusChange

    req = mcp.build_request("set_task_status", {"task_id": "T1", "to": "done"})
    TaskStatusChange.model_validate(req.json_body)  # 字段名 / 值域对齐


def test_send_message_as_task_passthrough() -> None:
    """send_message 的 as_task 原样透传，且构造 body 过 MessageCreate 契约。"""
    from coagentia_contracts.rest import MessageCreate

    req = mcp.build_request(
        "send_message",
        {"channel_id": "C1", "body": "做这个", "as_task": {"title": "标题"}},
    )
    assert req.json_body["as_task"] == {"title": "标题"}
    MessageCreate.model_validate(req.json_body)

    # 空 {} 也透传（server 用缺省 title）
    empty = mcp.build_request("send_message", {"channel_id": "C1", "body": "x", "as_task": {}})
    assert empty.json_body["as_task"] == {}
    MessageCreate.model_validate(empty.json_body)

    # 未给 as_task → body 不含该键
    none = mcp.build_request("send_message", {"channel_id": "C1", "body": "x"})
    assert "as_task" not in none.json_body


def test_claim_task_race_passthrough() -> None:
    """claim_task 收 409 CLAIM_RACE → isError=True 且 data 原样带 code/details。"""
    data = {"code": "CLAIM_RACE", "details": {"current_owner": "01K5MEMB00000000000000000A"}}
    http = StubHttp(status=409, data=data)
    out = mcp.call_tool("claim_task", {"task_id": "T1"}, http)
    assert out["isError"] is True
    payload = json.loads(out["content"][0]["text"])
    assert payload["status"] == 409
    assert payload["data"]["code"] == "CLAIM_RACE"
    assert payload["data"]["details"]["current_owner"] == "01K5MEMB00000000000000000A"


def test_set_task_status_transition_invalid_passthrough() -> None:
    """set_task_status 收 422 TASK_TRANSITION_INVALID → isError=True 原样透传。"""
    http = StubHttp(status=422, data={"code": "TASK_TRANSITION_INVALID"})
    out = mcp.call_tool("set_task_status", {"task_id": "T1", "to": "done"}, http)
    assert out["isError"] is True
    payload = json.loads(out["content"][0]["text"])
    assert payload["status"] == 422
    assert payload["data"]["code"] == "TASK_TRANSITION_INVALID"


def test_tools_list_includes_m2_tools() -> None:
    """tools/list 往返：6 个 M2 工具全部出现，且 send_message 声明 as_task 属性。"""
    state = mcp._RpcState(http=StubHttp())
    listed = mcp.handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, state)
    names = {t["name"] for t in listed["result"]["tools"]}
    assert {
        "list_tasks",
        "get_task",
        "claim_task",
        "unclaim_task",
        "set_task_status",
        "search",
    } <= names
    send = next(t for t in listed["result"]["tools"] if t["name"] == "send_message")
    assert "as_task" in send["inputSchema"]["properties"]


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
    out = mcp.call_tool(
        "create_reminder",
        {
            "kind": "recurring",
            "cadence": "0 9 * * *",
            "anchor_channel_id": "01K5CHAN00000000000000000A",
        },
        http,
    )
    assert out["isError"] is True
    assert json.loads(out["content"][0]["text"])["status"] == 422


def test_create_reminder_body_matches_contract() -> None:
    """回归 #1：create_reminder 构造的 body 必须过 ReminderCreate 契约校验（曾字段全错→422）。"""
    from coagentia_contracts.rest import ReminderCreate

    ulid = "01K5CHAN00000000000000000A"
    # once：时刻写入 cadence，最小必填集
    once = mcp.build_request(
        "create_reminder",
        {"kind": "once", "cadence": "2026-07-10T09:00:00Z", "anchor_channel_id": ulid},
    )
    assert once.path == "/api/reminders"
    assert once.method == "POST"
    assert once.json_body == {
        "kind": "once",
        "cadence": "2026-07-10T09:00:00Z",
        "anchor_channel_id": ulid,
    }
    ReminderCreate.model_validate(once.json_body)  # 不抛 = 字段名/必填/extra 全对齐

    # recurring + 全部可选锚点 + 内联 loop_contract（M4：cadence = interval，须过 ReminderCreate）
    loop_contract = {
        "version": "coagentia.loop-contract.v1",
        "cadence": "PT1H",
        "verification": ["每次输出附校验命令"],
        "budget": {"max_retries": 1, "max_runtime_min": 10},
        "tools": [],
        "escalation": "连续两次失败拉创建者",
    }
    rec = mcp.build_request(
        "create_reminder",
        {
            "kind": "recurring",
            "cadence": "PT1H",
            "anchor_channel_id": ulid,
            "anchor_message_id": "01K5MSG100000000000000000A",
            "anchor_task_id": "01K5TASK00000000000000000A",
            "loop_contract": loop_contract,
        },
    )
    assert rec.json_body["loop_contract"] == loop_contract  # 对象原样透传
    ReminderCreate.model_validate(rec.json_body)


def test_tool_catalog_matches_contract() -> None:
    """TOOLS 名集恰等于契约正目录 COAGENTIA_MCP_TOOLS（含 M7 trigger_deploy，无遗漏/多发明）。"""
    from coagentia_contracts.constants import COAGENTIA_MCP_TOOLS

    names = [t["name"] for t in mcp.TOOLS]
    assert set(names) == set(COAGENTIA_MCP_TOOLS)
    assert len(names) == len(set(names)) == len(COAGENTIA_MCP_TOOLS)  # 无重复
    assert "trigger_deploy" in names


def test_build_request_trigger_deploy() -> None:
    """trigger_deploy → POST /api/projects/{id}/deployments，空请求体（分支由 server 侧解析）。"""
    r = mcp.build_request("trigger_deploy", {"project_id": "P1"})
    assert r.method == "POST"
    assert r.path == "/api/projects/P1/deployments"
    assert r.json_body is None
    assert r.query is None


def test_trigger_deploy_in_progress_passthrough() -> None:
    """trigger_deploy 收 409 DEPLOY_IN_PROGRESS → isError=True 且 status/data 原样透传。"""
    http = StubHttp(status=409, data={"code": "DEPLOY_IN_PROGRESS"})
    out = mcp.call_tool("trigger_deploy", {"project_id": "P1"}, http)
    assert out["isError"] is True
    payload = json.loads(out["content"][0]["text"])
    assert payload["status"] == 409
    assert payload["data"]["code"] == "DEPLOY_IN_PROGRESS"
    # 请求确实发到 deployments 端点
    assert http.calls[0].path == "/api/projects/P1/deployments"
    assert http.calls[0].method == "POST"


def test_trigger_deploy_validation_failed_passthrough() -> None:
    """trigger_deploy 收 422 VALIDATION_FAILED（无 deploy_command）→ isError=True 原样透传。"""
    data = {"code": "VALIDATION_FAILED", "details": {"hint": "先配置 deploy_command"}}
    http = StubHttp(status=422, data=data)
    out = mcp.call_tool("trigger_deploy", {"project_id": "P1"}, http)
    assert out["isError"] is True
    payload = json.loads(out["content"][0]["text"])
    assert payload["status"] == 422
    assert payload["data"]["code"] == "VALIDATION_FAILED"
    assert payload["data"]["details"]["hint"] == "先配置 deploy_command"


def test_trigger_deploy_daemon_offline_passthrough() -> None:
    """trigger_deploy 收 503 DAEMON_OFFLINE → isError=True 原样透传。"""
    http = StubHttp(status=503, data={"code": "DAEMON_OFFLINE"})
    out = mcp.call_tool("trigger_deploy", {"project_id": "P1"}, http)
    assert out["isError"] is True
    payload = json.loads(out["content"][0]["text"])
    assert payload["status"] == 503
    assert payload["data"]["code"] == "DAEMON_OFFLINE"


def test_trigger_deploy_injects_acting_member_header() -> None:
    """R8 留痕真调用链：make_urllib_http 对 trigger_deploy 的出站请求注入 Authorization Bearer +
    X-Acting-Member（触发者身份 = Agent member_id）——server 单点据此落准 triggered_by（E §3）。"""
    import urllib.request
    from contextlib import contextmanager

    captured: dict[str, object] = {}

    class _Resp:
        status = 201
        headers = {"Content-Type": "application/json"}

        def read(self) -> bytes:
            return b'{"id":"D1","status":"queued"}'

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *a: object) -> None:
            return None

    def _fake_urlopen(req: urllib.request.Request, timeout: float = 0) -> _Resp:  # noqa: ARG001
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["headers"] = dict(req.header_items())
        return _Resp()

    @contextmanager
    def _patched():  # noqa: ANN202
        orig = urllib.request.urlopen
        urllib.request.urlopen = _fake_urlopen  # type: ignore[assignment]
        try:
            yield
        finally:
            urllib.request.urlopen = orig  # type: ignore[assignment]

    with _patched():
        http = mcp.make_urllib_http("http://srv", "secret-key", "01AGENTMEMBER0000000000000")
        out = mcp.call_tool("trigger_deploy", {"project_id": "P1"}, http)

    assert out["isError"] is False
    assert captured["method"] == "POST"
    assert captured["url"] == "http://srv/api/projects/P1/deployments"
    headers = {k.lower(): v for k, v in captured["headers"].items()}  # type: ignore[union-attr]
    assert headers["authorization"] == "Bearer secret-key"
    assert headers["x-acting-member"] == "01AGENTMEMBER0000000000000"


def test_tools_list_includes_trigger_deploy() -> None:
    """tools/list 往返：trigger_deploy 出现且声明 project_id 必填。"""
    state = mcp._RpcState(http=StubHttp())
    listed = mcp.handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, state)
    td = next(t for t in listed["result"]["tools"] if t["name"] == "trigger_deploy")
    assert td["inputSchema"]["required"] == ["project_id"]


def test_codex_reuses_same_mcp_catalog() -> None:
    """E2 Codex 零改动：config.toml 拉起同一 `mcp` 子命令 server（工具目录 runtime 无关，
    trigger_deploy 经同一 TOOLS 目录对 codex 亦生效，无需 codex 侧改动）。"""
    from coagentia_daemon.adapters import cmdline, codex_cmdline

    cmd, base_args = cmdline.mcp_command()
    toml = codex_cmdline.build_config_toml(
        agent_member_id="M1", server_url="http://x", api_key="k"
    )
    assert json.dumps(cmd) in toml  # 同一 daemon mcp 入口 → 同一 mcp.TOOLS 目录
    assert "mcp" in base_args
    # 目录 = 单一事实源，两 runtime 共用；trigger_deploy 无需 codex 侧登记即可用
    assert "trigger_deploy" in {t["name"] for t in mcp.TOOLS}


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
