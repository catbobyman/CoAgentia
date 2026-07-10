"""真 CLI 冒烟（契约 E §10；锚定 claude 2.1.205 真实帧）。

默认跳过（不烧 token / 不依赖登录）；开启：`COAGENTIA_SMOKE=1 uv run pytest -m slow`。
覆盖用例 1（启动就绪）/ 2（一次完整对话 + usage 恰一条 ULID）/ 3（Restart --resume 保上下文）/
7（帧防腐：真契约外帧被计数 + 桩帧不崩）。已在本机（win32, claude 2.1.205）真跑通过。

结论（写入 open_issues）：
- §11.2 --verbose 必需已确认；且 --verbose 灌 stderr → 适配器必须持续排空 stderr（否则死锁）。
- §11.3 实测：stream-json 输入模式下 init 帧在**首个 stdin 输入后**才到；就绪 idle 解耦于 init，
  会话确认由 result/init 记（router.confirmed）。busy 期继续写 stdin：CLI 按序排队消化。
- §11.4 DISALLOWED_TOOLS 初值 EnterPlanMode/ExitPlanMode 生效，未见副作用。
"""

from __future__ import annotations

import io
import json
import os
import shutil

import pytest
from adapter_helpers import RecordingSink
from coagentia_contracts.daemon import AgentBoot
from coagentia_contracts.enums import AgentStatus
from coagentia_daemon.adapters import mcp
from coagentia_daemon.adapters.claude_code import ClaudeCodeAdapter
from coagentia_daemon.paths import DataPaths
from helpers import until

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        os.environ.get("COAGENTIA_SMOKE") != "1" or shutil.which("claude") is None,
        reason="真 CLI 冒烟：设 COAGENTIA_SMOKE=1 且 claude 已登录",
    ),
]

AID = "01K5CMPT00000000000000000A"
CHAN = "01K5CHAN00000000000000000A"


def _adapter(tmp_path):
    paths = DataPaths(tmp_path / "root")
    paths.ensure_dirs()
    adapter = ClaudeCodeAdapter(paths, server_url="http://127.0.0.1:1", api_key="cak_smoke")
    sink = RecordingSink()
    adapter.bind(sink)
    boot = AgentBoot(
        agent_member_id=AID, name="Pat", runtime="claude_code",
        model="claude-opus-4-8", home_path=str(tmp_path / "home"), skills=[],
    )
    return adapter, sink, paths, boot


def _previews(sink: RecordingSink) -> list[str]:
    return [d.payload.get("preview", "") for d in sink.diagnostics if d.type == "agent.turn_output"]


async def _run_turn(adapter, sink, body: str, timeout: float = 150.0) -> None:
    n = len(sink.usage)
    msg = {
        "id": f"01K5MSG1000000000000000{chr(65 + n % 26)}0",
        "channel_id": CHAN,
        "author_member_id": "01K5AUTH00000000000000000A",
        "created_at": "2026-07-09T00:00:00.000Z",
        "body": body,
    }
    await adapter.deliver(AID, CHAN, [msg], None)
    await until(lambda: len(sink.usage) > n and sink.statuses()[-1] == AgentStatus.IDLE, timeout)


async def test_smoke_case1_2_7_start_turn_usage_anticorruption(tmp_path) -> None:
    adapter, sink, paths, boot = _adapter(tmp_path)
    try:
        # 用例 1：启动就绪 → idle
        assert await adapter.start(boot) is True
        await until(lambda: AgentStatus.IDLE in sink.statuses(), 30)

        # 用例 2：一次完整对话 → busy + activity → result → idle + usage 恰一条
        await _run_turn(adapter, sink, "Reply in plain text with exactly: PONG. No tools.")
        assert AgentStatus.BUSY in sink.statuses()
        assert sink.statuses()[-1] == AgentStatus.IDLE
        assert len(sink.usage) == 1  # result 帧唯一提取点，ULID 去重
        ev = sink.usage[0]
        assert len(ev.id) == 26  # 适配器 ULID
        assert ev.input_tokens > 0 and ev.output_tokens > 0
        assert ev.source_session and "-" in ev.source_session  # session_id 是 UUID
        assert paths.read_session(AID).get("session_id") == ev.source_session
        # activity 相位来自真流（相位切换粒度）
        assert sink.activity, "应有 activity 相位上报"
        assert all(d for _, d in sink.activity)

        # 用例 7：帧防腐——真契约外帧已被计数（无崩溃）
        router = adapter._agents[AID].process.router
        assert router.unknown_counts, "真流应出现契约外帧（system/status、rate_limit_event 等）"
        # 再注入契约外桩帧 → 不崩、计数 +1
        before = router.unknown_counts.get("rate_limit_event", 0)
        await router.process({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}})
        await router.process({"type": "system", "subtype": "notification"})
        assert router.unknown_counts["rate_limit_event"] == before + 1
        assert "agent.unknown_frame" in sink.diag_types()
    finally:
        await adapter.stop(AID)


async def test_smoke_case3_restart_resume_keeps_context(tmp_path) -> None:
    adapter, sink, paths, boot = _adapter(tmp_path)
    try:
        await adapter.start(boot)
        await until(lambda: AgentStatus.IDLE in sink.statuses(), 30)
        # 建立上下文
        await _run_turn(
            adapter, sink,
            "Remember codeword BANANA123. No tools; reply plain text: acknowledged.",
        )
        session1 = paths.read_session(AID).get("session_id")
        assert session1

        # Restart（一档）→ --resume 保上下文
        await adapter.restart(boot)
        await until(lambda: AgentStatus.IDLE in sink.statuses(), 30)
        resume_args = adapter._agents[AID].process.reset_session_args()
        assert resume_args == ["--resume", session1]  # 精确续接同一会话

        # 追问 → Agent 记得
        n_before = len(_previews(sink))
        await _run_turn(
            adapter, sink,
            "What codeword did I ask you to remember? No tools; reply just the codeword.",
        )
        recalled = " ".join(_previews(sink)[n_before:])
        assert "BANANA123" in recalled, f"Restart 后应记得上下文，实际预览: {recalled[:200]}"
    finally:
        await adapter.stop(AID)


class _RoutingHttp:
    """按 (method, path) 路由的桩 HTTP：模拟真 server 对任务工具的响应序列。"""

    def __init__(self) -> None:
        self.calls: list[mcp.ToolRequest] = []

    def __call__(self, req: mcp.ToolRequest) -> mcp.ToolResult:
        self.calls.append(req)
        if req.method == "GET" and req.path == "/api/tasks":
            items = [{"id": "T1", "status": "todo"}]
            return mcp.ToolResult(200, {"items": items, "next_cursor": None})
        if req.path == "/api/tasks/T1/claim":
            return mcp.ToolResult(200, {"task": {"id": "T1", "owner_member_id": AID}})
        if req.path == "/api/tasks/T1/status":
            return mcp.ToolResult(200, {"task": {"id": "T1", "status": "in_progress"}})
        return mcp.ToolResult(404, {"code": "NOT_FOUND"}, is_error=True)


def test_smoke_mcp_task_tools_roundtrip() -> None:
    """用例扩展：Agent 经真 MCP JSON-RPC 走 list_tasks → claim → set_status（StubHttp 集成，
    因夹具 server_url 为死地址 127.0.0.1:1，用路由桩替真 server 打通 serve_stdio 全链路）。"""
    http = _RoutingHttp()
    lines = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "list_tasks", "arguments": {"status": "todo"}},
        }),
        json.dumps({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "claim_task", "arguments": {"task_id": "T1"}},
        }),
        json.dumps({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {
                "name": "set_task_status",
                "arguments": {"task_id": "T1", "to": "in_progress"},
            },
        }),
    ]
    stdin = io.StringIO("\n".join(lines) + "\n")
    stdout = io.StringIO()
    mcp.serve_stdio(http, stdin=stdin, stdout=stdout)
    responses = [json.loads(x) for x in stdout.getvalue().splitlines() if x.strip()]
    # initialize + 3 tools/call（notification 无响应）
    assert [r["id"] for r in responses] == [1, 2, 3, 4]
    assert all(r["result"]["isError"] is False for r in responses[1:])
    # 请求序列正确落到三个端点
    assert http.calls[0].path == "/api/tasks" and http.calls[0].query == {"status": "todo"}
    assert http.calls[1].path == "/api/tasks/T1/claim" and http.calls[1].method == "POST"
    assert http.calls[2].path == "/api/tasks/T1/status"
    assert http.calls[2].json_body == {"to": "in_progress"}
