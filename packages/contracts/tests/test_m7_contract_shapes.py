"""M7 契约登记的聚焦形状测试（K0）：预览/部署实体、新账 token_summary、usage 三层、D 帧。"""

from coagentia_contracts import daemon, entities, rest
from coagentia_contracts.enums import DeploymentStatus, PreviewStatus, UsageLevel

U1 = "01KX0000000000000000000001"
U2 = "01KX0000000000000000000002"
U3 = "01KX0000000000000000000003"
U4 = "01KX0000000000000000000004"
TS = "2026-07-13T12:00:00.000Z"


def test_preview_session_public_carries_fail_log_tail() -> None:
    """PreviewSessionPublic 含 fail_log_tail（A v1.0.11）；starting 期 port/fail_log_tail 均空。"""
    starting = entities.PreviewSessionPublic.model_validate({
        "id": U1, "workspace_id": U2, "task_id": U3, "worktree_id": U4,
        "status": "starting", "started_at": TS,
    })
    assert starting.status is PreviewStatus.STARTING
    assert starting.port is None and starting.fail_log_tail is None

    failed = entities.PreviewSessionPublic.model_validate({
        "id": U1, "workspace_id": U2, "task_id": U3, "worktree_id": U4, "port": None,
        "status": "failed", "fail_log_tail": "Error: dev server crashed\n  at line 3\n",
        "started_at": TS, "last_active_at": TS,
    })
    assert failed.status is PreviewStatus.FAILED
    assert failed.fail_log_tail is not None and "crashed" in failed.fail_log_tail


def _token_summary_payload() -> dict:
    return {
        "usage": {"input_tokens": 1200, "output_tokens": 450, "cache_read_tokens": 800,
                  "cache_write_tokens": 200, "events": 3},
        "tasks_reporting": {"reporting": 1, "total": 2},
        "task_ids": [U3],
    }


def test_token_summary_roundtrip() -> None:
    """TokenSummary 新账嵌套模型（B §13.4）：usage 四字段+events / tasks_reporting / task_ids。"""
    summary = entities.TokenSummary.model_validate(_token_summary_payload())
    assert summary.usage.input_tokens == 1200 and summary.usage.events == 3
    assert summary.tasks_reporting.reporting == 1 and summary.tasks_reporting.total == 2
    assert summary.task_ids == [U3]


def test_deployment_public_tightened_token_summary_and_drops_log_path() -> None:
    """DeploymentPublic 剔除 log_path（内部列）；token_summary 收紧为 TokenSummary（v1.5）。"""
    assert "log_path" not in entities.DeploymentPublic.model_fields
    deployment = entities.DeploymentPublic.model_validate({
        "id": U1, "workspace_id": U2, "project_id": U3, "triggered_by_member_id": U4,
        "branch": "main", "commit_hash": "0" * 40, "command": "npm run deploy",
        "status": "success", "exit_code": 0, "url": "https://preview.example.com/app",
        "token_summary": _token_summary_payload(), "started_at": TS, "finished_at": TS,
    })
    assert deployment.status is DeploymentStatus.SUCCESS
    assert deployment.token_summary is not None
    assert deployment.token_summary.usage.output_tokens == 450

    # queued 瞬时态：未终结，exit_code/url/token 可空。
    queued = entities.DeploymentPublic.model_validate({
        "id": U1, "workspace_id": U2, "project_id": U3, "triggered_by_member_id": U4,
        "branch": "main", "command": "npm run deploy", "status": "queued",
    })
    assert queued.status is DeploymentStatus.QUEUED
    assert queued.exit_code is None and queued.url is None and queued.finished_at is None


def test_usage_report_three_levels_and_rollup() -> None:
    """UsageReport 响应形状冻结（B §13.4）：level 三值 / 覆盖率 / rollup breakdown。"""
    bucket = {"input_tokens": 10, "output_tokens": 5, "cache_read_tokens": 0,
              "cache_write_tokens": 0, "events": 2}
    task_level = rest.UsageReport.model_validate({
        "level": "task", "ref": U3, "usage": bucket,
        "tasks_reporting": {"reporting": 1, "total": 1},
    })
    assert task_level.level is UsageLevel.TASK
    assert task_level.breakdown is None  # 默认省略

    canvas_level = rest.UsageReport.model_validate({
        "level": "canvas", "ref": U2, "usage": bucket,
        "tasks_reporting": {"reporting": 2, "total": 3},
        "breakdown": [{"ref": U3, "label": "节点任务 #1", "usage": bucket}],
    })
    assert canvas_level.level is UsageLevel.CANVAS
    assert canvas_level.breakdown is not None
    assert canvas_level.breakdown[0].label == "节点任务 #1"
    assert canvas_level.breakdown[0].usage.input_tokens == 10


def test_deployment_log_page_shape() -> None:
    """GET /deployments/{id}/log 响应（B §13.3）：{lines, next_after, truncated}。"""
    empty = rest.DeploymentLogPage.model_validate({})
    assert empty.lines == [] and empty.next_after is None and empty.truncated is False
    page = rest.DeploymentLogPage.model_validate({
        "lines": ["$ npm run deploy", "build ok"], "next_after": 2, "truncated": True,
    })
    assert page.lines[0] == "$ npm run deploy" and page.next_after == 2 and page.truncated


def test_daemon_m7_frames() -> None:
    """D §5.3/§7 v1.0.4 帧：preview.start/stop、deploy.run/log/finished、preview.status。"""
    start = daemon.PreviewStartData.model_validate({
        "preview_session_id": U1, "task_id": U2,
        "worktree_path": r"D:\\worktrees\\01", "dev_command": "npm run dev",
    })
    assert start.dev_command == "npm run dev"
    stop = daemon.PreviewStopData.model_validate({"preview_session_id": U1})
    assert stop.preview_session_id == U1

    run = daemon.DeployRunData.model_validate({
        "deployment_id": U1, "repo_path": r"D:\\repo", "command": "npm run deploy",
        "branch": "main",
    })
    assert run.commit_hash is None  # 可空——仅留痕，daemon 不校验

    log = daemon.DeployLogReportData.model_validate({
        "deployment_id": U1, "chunk_seq": 3, "lines": ["building…", "done"],
    })
    assert log.chunk_seq == 3 and log.lines == ["building…", "done"]

    # 超时 = failed（exit_code=null，v1.0.4）
    finished = daemon.DeployFinishedData.model_validate({
        "deployment_id": U1, "status": "failed", "exit_code": None,
    })
    assert finished.exit_code is None and finished.url is None
    ok = daemon.DeployFinishedData.model_validate({
        "deployment_id": U1, "status": "success", "exit_code": 0,
        "url": "https://preview.example.com/app",
    })
    assert ok.exit_code == 0 and ok.url is not None

    running = daemon.PreviewStatusData.model_validate({
        "preview_session_id": U1, "status": "running", "port": 43117,
    })
    assert running.port == 43117 and running.log_tail is None  # 加字段向后兼容
    failed = daemon.PreviewStatusData.model_validate({
        "preview_session_id": U1, "status": "failed", "log_tail": "boom\n",
    })
    assert failed.port is None and failed.log_tail == "boom\n"
