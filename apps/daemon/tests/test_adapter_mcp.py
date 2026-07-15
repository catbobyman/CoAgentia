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


def _handoff_body() -> dict:
    """最小合法 TaskHandoffBody（置 in_review 门要 deliverables≥1；此处即满足）。

    member_id 用合法 ULID（Crockford base32，无 I/L/O/U）——过 TaskHandoffBody 的 Ulid 模式校验。
    """
    return {
        "version": "coagentia.task-handoff.v1",
        "from_member": "01AGENTMEMBER0000000000000",
        "to_member": "01K5MEMB00000000000000000A",
        "deliverables": [{"path": "/repo/x.py", "kind": "file"}],
        "evidence": [{"type": "command", "ref": "pytest -q → 48 passed", "conclusion": "全绿"}],
        "verify_plan": "复跑 pytest -q 复核",
    }


def test_build_request_submit_task_contract() -> None:
    """submit_task_contract → POST /api/tasks/{id}/contracts，{kind, body} 原样透传（两 kind）。"""
    body = _handoff_body()
    r = mcp.build_request(
        "submit_task_contract", {"task_id": "T1", "kind": "task_handoff", "body": body}
    )
    assert r.method == "POST"
    assert r.path == "/api/tasks/T1/contracts"
    assert r.json_body == {"kind": "task_handoff", "body": body}
    assert r.query is None

    plan_body = {
        "version": "coagentia.task-plan.v1",
        "goal": "做个东西",
        "acceptance_criteria": [
            {
                "id": "ac1",
                "statement": "命令退 0",
                "verify_by": "command",
                "verify_ref": "make test",
            }
        ],
    }
    p = mcp.build_request(
        "submit_task_contract", {"task_id": "T2", "kind": "task_plan", "body": plan_body}
    )
    assert p.path == "/api/tasks/T2/contracts"
    assert p.json_body == {"kind": "task_plan", "body": plan_body}


def test_submit_task_contract_body_matches_contract() -> None:
    """构造的 {kind, body} 过 ContractCreate；body 过对应 kind 模型（字段名/值域对齐端点）。"""
    from coagentia_contracts.enums import ContractKind
    from coagentia_contracts.rest import CONTRACT_BODY_MODELS, ContractCreate, TaskHandoffBody

    req = mcp.build_request(
        "submit_task_contract", {"task_id": "T1", "kind": "task_handoff", "body": _handoff_body()}
    )
    ContractCreate.model_validate(req.json_body)  # {kind, body} 形状对齐 POST 端点
    TaskHandoffBody.model_validate(req.json_body["body"])  # body 过 kind 模型 = 首投即可通过 T7
    assert CONTRACT_BODY_MODELS[ContractKind.TASK_HANDOFF] is TaskHandoffBody


def test_submit_task_contract_missing_body_arg() -> None:
    """必填 body 缺失 → 不触 HTTP，收敛为 missing_argument（同其它必填参防御）。"""
    http = StubHttp()
    out = mcp.call_tool("submit_task_contract", {"task_id": "T1", "kind": "task_plan"}, http)
    assert out["isError"] is True
    assert http.calls == []
    assert json.loads(out["content"][0]["text"])["error"] == "missing_argument"


def test_submit_task_contract_validation_failed_passthrough() -> None:
    """字段不符 → 422 VALIDATION_FAILED 携逐字段 errors 原样透传（Agent 据此按清单修复自愈）。"""
    data = {
        "code": "VALIDATION_FAILED",
        "details": {
            "kind": "task_handoff",
            "errors": [{"loc": ["verify_plan"], "msg": "Field required", "type": "missing"}],
        },
    }
    http = StubHttp(status=422, data=data)
    out = mcp.call_tool(
        "submit_task_contract",
        {"task_id": "T1", "kind": "task_handoff", "body": {"version": "coagentia.task-handoff.v1"}},
        http,
    )
    assert out["isError"] is True
    payload = json.loads(out["content"][0]["text"])
    assert payload["status"] == 422
    assert payload["data"]["code"] == "VALIDATION_FAILED"
    assert payload["data"]["details"]["errors"][0]["loc"] == ["verify_plan"]
    assert http.calls[0].path == "/api/tasks/T1/contracts"
    assert http.calls[0].method == "POST"


def test_submit_task_contract_success_passthrough() -> None:
    """201 创建 → isError=False，status/data（含 revision）原样透传。"""
    http = StubHttp(
        status=201, data={"id": "01K5CONTRACT0000000000000A", "kind": "task_handoff", "revision": 1}
    )
    out = mcp.call_tool(
        "submit_task_contract",
        {"task_id": "T1", "kind": "task_handoff", "body": _handoff_body()},
        http,
    )
    assert out["isError"] is False
    payload = json.loads(out["content"][0]["text"])
    assert payload["status"] == 201
    assert payload["data"]["revision"] == 1


def test_tools_list_includes_submit_task_contract() -> None:
    """tools/list 往返：submit_task_contract 出现，required=task_id/kind/body，kind 枚举恰两值。"""
    state = mcp._RpcState(http=StubHttp())
    listed = mcp.handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, state)
    tool = next(t for t in listed["result"]["tools"] if t["name"] == "submit_task_contract")
    assert set(tool["inputSchema"]["required"]) == {"task_id", "kind", "body"}
    assert set(tool["inputSchema"]["properties"]["kind"]["enum"]) == {"task_plan", "task_handoff"}


def test_codex_reuses_same_mcp_catalog() -> None:
    """E2 Codex 零改动：config.toml 拉起同一 `mcp` 子命令 server（工具目录 runtime 无关，
    trigger_deploy 经同一 TOOLS 目录对 codex 亦生效，无需 codex 侧改动）。"""
    from coagentia_daemon.adapters import cmdline, codex_cmdline

    cmd, base_args = cmdline.mcp_command()
    toml = codex_cmdline.build_config_toml(agent_member_id="M1", server_url="http://x", api_key="k")
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


def test_serve_stdio_parse_error_replies_not_silent() -> None:
    """CR-M8-2：不可解析行必须回 JSON-RPC parse error（id=null）——修复前静默丢弃，
    claude 侧对应 tools/call 无限等待（win32 GBK 吞结构引号即触发此路径）。"""
    stdin = io.StringIO('{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params\n')
    stdout = io.StringIO()
    mcp.serve_stdio(StubHttp(), stdin=stdin, stdout=stdout)
    responses = [json.loads(x) for x in stdout.getvalue().splitlines() if x.strip()]
    assert len(responses) == 1
    assert responses[0]["id"] is None
    assert responses[0]["error"]["code"] == -32700


def test_mcp_subprocess_utf8_roundtrip_without_ioencoding() -> None:
    """CR-M8-2 实机回归：以「claude 拉起」的真实形态（无 PYTHONIOENCODING/PYTHONUTF8 的
    子进程管道，win32 中文系统默认 GBK）spawn 真 MCP 进程，UTF-8 中文载荷双向 roundtrip。

    修复前：win32 上 tools/call 的中文 body 经 GBK 误码（mojibake 422 / 崩循环 / 静默丢弃
    无限挂起）；修复后 _reconfigure_stdio_utf8 双向 UTF-8，响应含中文工具描述逐字节可读。
    """
    import os
    import subprocess
    import sys as _sys

    env = dict(os.environ)
    env.pop("PYTHONIOENCODING", None)
    env.pop("PYTHONUTF8", None)
    proc = subprocess.Popen(
        [
            _sys.executable,
            "-m",
            "coagentia_daemon",
            "mcp",
            "--agent-member",
            "01K5AGENT0000000000000000A",
            "--server-url",
            "http://127.0.0.1:1",  # 不连——只验 stdio 编码面
            "--api-key",
            "cak_test",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        reqs = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "回归探针", "version": "0"},
                },
            },
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in reqs)
        out, _err = proc.communicate(payload.encode("utf-8"), timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
    lines = [ln for ln in out.decode("utf-8").splitlines() if ln.strip()]  # 修复前此处 GBK 字节即抛
    responses = [json.loads(ln) for ln in lines]
    assert [r["id"] for r in responses] == [1, 2]
    tools = responses[1]["result"]["tools"]
    send = next(t for t in tools if t["name"] == "send_message")
    assert "唯一发言出口" in send["description"]  # 中文逐字节存活 = 双向 UTF-8 生效
