"""契约 B/C/D 目录对照：错误码、WS 事件、daemon 帧类型逐名一致。"""

from coagentia_contracts import daemon, rest, ws

# 契约 B §3 全集（转录自 02-REST-API契约.md v1.0；实际 20 个，头表"19"为统计笔误）
ERROR_CODES = {
    "VALIDATION_FAILED", "TASK_IN_DM", "NOT_TOP_LEVEL_MESSAGE", "CLAIM_RACE",
    "HANDOFF_INCOMPLETE", "GRAPH_CYCLE", "STALE_CONFIRM", "DELTA_BASE_MISMATCH",
    "NODE_ACTIVE", "NO_ORCHESTRATOR", "IDEMPOTENCY_MISMATCH", "NAME_TAKEN",
    "CHANNEL_ARCHIVED", "COMPUTER_HAS_AGENTS", "WORKSPACE_EXISTS", "DEPLOY_IN_PROGRESS",
    "DAEMON_OFFLINE", "FILE_TOO_LARGE", "PERMISSION_DENIED", "NOT_FOUND",
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
    "runtime.rescan",
}
QUERY_TYPES = {"home.tree", "home.file", "git.diff"}
REPORT_TYPES = {
    "hello", "agent.status_changed", "agent.activity", "runtimes.detected",
    "diagnostics.batch", "usage.batch", "deploy.log", "deploy.finished", "preview.status",
    "worktree.status",
}


def test_error_codes_exact() -> None:
    assert {c.value for c in rest.ErrorCode} == ERROR_CODES
    assert len(ERROR_CODES) == 20


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
