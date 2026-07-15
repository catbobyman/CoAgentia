"""契约 B/C/D 目录对照：错误码、WS 事件、daemon 帧类型逐名一致。"""

from coagentia_contracts import constants, daemon, rest, ws
from coagentia_contracts.enums import TaskStatus

# 契约 B §3 全集（转录自 02-REST-API契约.md；v1.4.2 起 29 个）
ERROR_CODES = {
    "VALIDATION_FAILED", "TASK_IN_DM", "NOT_TOP_LEVEL_MESSAGE", "CLAIM_RACE",
    "HANDOFF_INCOMPLETE", "TASK_TRANSITION_INVALID", "GRAPH_CYCLE", "STALE_CONFIRM",
    "DELTA_BASE_MISMATCH", "NODE_ACTIVE", "NO_ORCHESTRATOR", "IDEMPOTENCY_MISMATCH",
    "NAME_TAKEN", "CHANNEL_NOT_EMPTY", "CHANNEL_ARCHIVED", "COMPUTER_HAS_AGENTS",
    "COMPUTER_HAS_PROJECTS",
    "WORKSPACE_EXISTS", "DEPLOY_IN_PROGRESS", "DAEMON_OFFLINE", "FILE_TOO_LARGE",
    "HELD_DRAFT_RESOLVED", "NOTIF_IN_DM", "TEMPLATE_CANVAS_NOT_READY",
    "SYSTEM_NODE_NOT_RETRYABLE", "TEMPLATE_BUILTIN_IMMUTABLE", "PROJECT_IN_USE",
    "PERMISSION_DENIED", "NOT_FOUND",
}

# 契约 C §6/§7/§8 全集（转录自 03-WS事件协议.md v1.0）
WS_EVENTS = {
    # 6.1
    "sys.hello", "sys.pong", "workspace.updated",
    # 6.2
    "presence.changed", "agent.activity", "member.created", "member.updated",
    "member.removed", "agent.updated", "computer.connected", "computer.disconnected",
    "computer.updated",
    # 6.3
    "channel.created", "channel.updated", "channel.deleted", "channel.member_added",
    "channel.member_removed", "message.created", "read.updated",
    # 6.4
    "task.created", "task.updated", "task_contract.created", "task_contract.updated",
    "activity.created", "activity.done", "token_usage.reported",
    # 6.5
    "canvas.node_added", "canvas.node_updated", "canvas.node_removed", "canvas.edge_added",
    "canvas.edge_removed", "canvas.layout_updated", "canvas.baseline_advanced",
    # 6.6
    "held_draft.created", "held_draft.updated", "reminder.created", "reminder.updated",
    # 6.7
    "worktree.updated", "preview.updated", "deployment.created", "deployment.updated",
    "deployment.log",
    # §7 M6 预留（与拆解设计 §15 一名两用）
    "draft.presented", "draft.adjusted", "draft.confirmed", "draft.rejected",
    "draft.superseded", "delta.proposed", "delta.adjusted", "delta.confirmed",
    "delta.rejected", "landing.started", "landing.completed", "landing.fail_closed",
    "proposal.updated",
    # §8 订阅制诊断流
    "diagnostic.appended",
}

# 契约 D §5/§6/§7 全集（转录自 04-daemon-server协议.md v1.0）
INSTR_TYPES = {
    "agent.start", "agent.stop", "agent.restart", "agent.reset_session", "agent.reset_full",
    "agent.wake", "agent.sleep", "message.deliver", "message.inject", "worktree.ensure",
    "worktree.merge", "worktree.cleanup", "preview.start", "preview.stop", "deploy.run",
    "check.run", "runtime.rescan",
}
QUERY_TYPES = {"home.tree", "home.file", "git.diff"}
REPORT_TYPES = {
    "hello", "agent.status_changed", "agent.activity", "runtimes.detected",
    "diagnostics.batch", "usage.batch", "deploy.log", "deploy.finished", "preview.status",
    "worktree.status",
    "check.finished",
}


def test_error_codes_exact() -> None:
    assert {c.value for c in rest.ErrorCode} == ERROR_CODES
    assert len(ERROR_CODES) == 29


def test_ws_event_catalog_exact() -> None:
    assert {e.value for e in ws.EventType} == WS_EVENTS


def test_every_ws_event_has_payload_model() -> None:
    assert set(ws.EVENT_PAYLOADS) == set(ws.EventType)


def test_daemon_frame_catalogs_exact() -> None:
    assert {t.value for t in daemon.InstrType} == INSTR_TYPES
    assert {t.value for t in daemon.QueryType} == QUERY_TYPES
    assert {t.value for t in daemon.ReportType} == REPORT_TYPES


def test_envelope_example_parses() -> None:
    """契约 C §3 的信封示例（ID 替换为合法 ULID）。"""
    env = ws.Envelope.model_validate({
        "v": 1,
        "seq": 8123,
        "type": "task.updated",
        "workspace_id": "01JZKJ7GG0000000000000000W",
        "channel_id": "01JZKJ7GG0000000000000000C",
        "key": "task:01JZKJ7GG0000000000000000T:2026-07-09T12:34:56.789Z",
        "at": "2026-07-09T12:34:56.790Z",
        "data": {},
    })
    assert env.type is ws.EventType.TASK_UPDATED
    assert env.channel_id is not None


def test_instr_frame_roundtrip() -> None:
    frame = daemon.InstrFrame.model_validate({
        "v": 1,
        "kind": "instr",
        "frame_id": "01JZKJ7GG0000000000000000F",
        "type": "agent.wake",
        "at": "2026-07-09T12:00:00.000Z",
        "data": {
            "agent_member_id": "01JZKJ7GG0000000000000000A",
            "reason": "mention",
            "refs": {"message_ids": ["01JZKJ7GG0000000000000000M"]},
        },
    })
    wake = daemon.AgentWakeData.model_validate(frame.data)
    assert wake.reason == "mention"
    ack = daemon.AckFrame.model_validate({
        "v": 1, "kind": "ack", "ref": frame.frame_id, "result": "noop",
    })
    assert ack.result is daemon.AckResult.NOOP


def test_m1_endpoint_catalog_size() -> None:
    """M1 端点清单：39 条（契约 B §4.1–4.6 的 M1 面，mock 一致性测试的基准）。"""
    assert len(rest.ENDPOINTS_M1) == 39
    assert len(set(rest.ENDPOINTS_M1)) == 39


def test_m2_endpoint_catalog_size() -> None:
    """M2 端点清单：12 条（契约 B §4.7/§4.8 M2 集 + files 页签），与 M1 不相交。"""
    assert len(rest.ENDPOINTS_M2) == 12
    assert len(set(rest.ENDPOINTS_M2)) == 12
    assert set(rest.ENDPOINTS_M1).isdisjoint(rest.ENDPOINTS_M2)


# 契约 B §9.1 状态机合法边（转录；纪律 7 单一事实源钉死）
EXPECTED_TRANSITIONS = {
    TaskStatus.TODO: {TaskStatus.IN_PROGRESS, TaskStatus.CLOSED},
    TaskStatus.IN_PROGRESS: {TaskStatus.TODO, TaskStatus.IN_REVIEW, TaskStatus.CLOSED},
    TaskStatus.IN_REVIEW: {TaskStatus.IN_PROGRESS, TaskStatus.DONE, TaskStatus.CLOSED},
    TaskStatus.DONE: set(),
    TaskStatus.CLOSED: {TaskStatus.TODO},
}


def test_task_transitions_match_contract() -> None:
    assert set(constants.TASK_TRANSITIONS) == set(TaskStatus)  # 全态有键
    assert {s: set(v) for s, v in constants.TASK_TRANSITIONS.items()} == EXPECTED_TRANSITIONS
    assert constants.TASK_TRANSITIONS[TaskStatus.DONE] == frozenset()  # done 终态
    for src, dsts in constants.TASK_TRANSITIONS.items():
        assert src not in dsts  # 无自环（幂等另处理）
        for dst in dsts:
            assert isinstance(dst, TaskStatus)  # 每条边目标都是合法 TaskStatus


def test_mcp_tool_catalog() -> None:
    """MCP 正目录含 M2 组，且与 DISALLOWED_TOOLS 负目录不相交（契约 E §3/§2）。"""
    assert set(constants.COAGENTIA_MCP_TOOLS) >= {
        "list_tasks", "get_task", "claim_task", "unclaim_task", "set_task_status", "search",
    }
    assert set(constants.COAGENTIA_MCP_TOOLS).isdisjoint(constants.DISALLOWED_TOOLS)


def test_m3_endpoint_catalog_size() -> None:
    """M3 端点清单：11 条；系统节点 retry 归 M6 执行面。"""
    assert len(rest.ENDPOINTS_M3) == 11
    assert len(set(rest.ENDPOINTS_M3)) == 11
    assert set(rest.ENDPOINTS_M1).isdisjoint(rest.ENDPOINTS_M3)
    assert set(rest.ENDPOINTS_M2).isdisjoint(rest.ENDPOINTS_M3)


# M1(9)+M2(6)=15 冻结至 M6；M7 起 +trigger_deploy（契约 E v1.5）——排除后核对"该里程碑零新增"。
def _tools_through_m6() -> int:
    return len(set(constants.COAGENTIA_MCP_TOOLS) - {"trigger_deploy"})


def test_m3_adds_no_mcp_tools() -> None:
    """E 契约 v1.2 裁决：M3 契约面零新 Agent 工具（提交/force-start 人确认/C3 门，
    读走 get_task、起草走 request-draft 直投 + send_message）。COAGENTIA_MCP_TOOLS 不增补。"""
    assert _tools_through_m6() == 15  # M1(9)+M2(6)，M3 无增（M7 的 trigger_deploy 已排除）


def test_m4_endpoint_catalog_size() -> None:
    """M4 端点清单：4 条（§4.14 护栏三键干预 + held-drafts 列表），与 M1/M2/M3 不相交。"""
    assert len(rest.ENDPOINTS_M4) == 4
    assert len(set(rest.ENDPOINTS_M4)) == 4
    assert set(rest.ENDPOINTS_M1).isdisjoint(rest.ENDPOINTS_M4)
    assert set(rest.ENDPOINTS_M2).isdisjoint(rest.ENDPOINTS_M4)
    assert set(rest.ENDPOINTS_M3).isdisjoint(rest.ENDPOINTS_M4)


def test_m4_adds_no_mcp_tools() -> None:
    """E 契约 v1.3 裁决：M4 零新 Agent 工具（护栏干预是人类面 rule=G3；create_reminder 参数
    扩展属 daemon mcp.py 非工具目录）。COAGENTIA_MCP_TOOLS 不增补。"""
    assert _tools_through_m6() == 15  # M1(9)+M2(6)，M3/M4 无增


def test_m5_endpoint_catalog_size() -> None:
    """M5 端点清单：5 条（§4.12 模板三 + §4.5 通知设置二），与 M1/M2/M3/M4 不相交。"""
    assert len(rest.ENDPOINTS_M5) == 5
    assert len(set(rest.ENDPOINTS_M5)) == 5
    for prior in (rest.ENDPOINTS_M1, rest.ENDPOINTS_M2, rest.ENDPOINTS_M3, rest.ENDPOINTS_M4):
        assert set(prior).isdisjoint(rest.ENDPOINTS_M5)


def test_m5_adds_no_mcp_tools() -> None:
    """E 契约 v1.4 裁决（§7 #12）：M5 工具组为空——连续第三个里程碑零新增 Agent 工具（唯一变化 =
    create_reminder cadence 值域扩 cron，属描述文案非工具目录）。COAGENTIA_MCP_TOOLS 不增补。"""
    assert _tools_through_m6() == 15  # M1(9)+M2(6)，M3/M4/M5 无增


def test_m6_endpoint_catalog_size() -> None:
    """M6 端点清单：编排 4 + Project 7 + retry 1 + 模板治理 2。"""
    assert len(rest.ENDPOINTS_M6) == 14
    assert len(set(rest.ENDPOINTS_M6)) == 14
    prior = (
        rest.ENDPOINTS_M1,
        rest.ENDPOINTS_M2,
        rest.ENDPOINTS_M3,
        rest.ENDPOINTS_M4,
        rest.ENDPOINTS_M5,
    )
    for endpoints in prior:
        assert set(endpoints).isdisjoint(rest.ENDPOINTS_M6)


def test_m6_adds_no_mcp_tools() -> None:
    """M6 零新增 Agent 工具（契约裁决 #13）；零工具连胜止于 M6——M7 起 +trigger_deploy。"""
    assert _tools_through_m6() == 15


def test_m7_endpoint_catalog_size() -> None:
    """M7 端点清单：预览 3 + 部署 3 + 成本 1 = 7（§13），与 M1–M6 全不相交。"""
    assert len(rest.ENDPOINTS_M7) == 7
    assert len(set(rest.ENDPOINTS_M7)) == 7
    prior = (
        rest.ENDPOINTS_M1,
        rest.ENDPOINTS_M2,
        rest.ENDPOINTS_M3,
        rest.ENDPOINTS_M4,
        rest.ENDPOINTS_M5,
        rest.ENDPOINTS_M6,
    )
    for endpoints in prior:
        assert set(endpoints).isdisjoint(rest.ENDPOINTS_M7)


def test_m7_adds_trigger_deploy_tool() -> None:
    """M7 工具组 +1：trigger_deploy（契约 E v1.5；R8 部署全员含 Agent 的通道兑现）——与负目录
    DISALLOWED_TOOLS 不相交，总数 15→16。"""
    assert "trigger_deploy" in constants.COAGENTIA_MCP_TOOLS
    assert len(constants.COAGENTIA_MCP_TOOLS) == 16
    assert len(set(constants.COAGENTIA_MCP_TOOLS)) == 16  # 无重复
    assert set(constants.COAGENTIA_MCP_TOOLS).isdisjoint(constants.DISALLOWED_TOOLS)


def test_codex_disallowed_tools_placeholder() -> None:
    """CODEX_DISALLOWED_TOOLS 占位空 tuple（契约 E2 §2.5；终表 H2 A 级实测校准回填）。"""
    assert constants.CODEX_DISALLOWED_TOOLS == ()
