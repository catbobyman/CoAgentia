"""M6 契约登记的聚焦形状测试。"""

import pytest
from coagentia_contracts import daemon, rest
from coagentia_contracts.enums import ReviewVerdict
from pydantic import ValidationError

U1 = "01KX0000000000000000000001"
U2 = "01KX0000000000000000000002"
U3 = "01KX0000000000000000000003"
SHA = "a" * 64


def test_project_requests_and_public_shape() -> None:
    created = rest.ProjectCreate.model_validate({
        "name": "CoAgentia",
        "repo_path": r"D:\\repo",
        "computer_id": U1,
    })
    assert created.computer_id == U1
    with pytest.raises(ValidationError):
        rest.ProjectCreate.model_validate({"name": "x", "repo_path": r"D:\\repo"})

    patch = rest.ProjectPatch.model_validate({"repo_path": r"D:\\new"})
    assert patch.model_fields_set == {"repo_path"}
    assert rest.ProjectBind(project_id=U2).project_id == U2


def test_task_delivery_extensions_are_backward_compatible() -> None:
    handoff = rest.TaskHandoffBody.model_validate({
        "from_member": U1,
        "to_member": U2,
        "verify_plan": "run tests",
        "review_verdict": "needs_human",
    })
    assert handoff.review_verdict is ReviewVerdict.NEEDS_HUMAN
    assert rest.TaskHandoffBody.model_validate({
        "from_member": U1, "to_member": U2, "verify_plan": "inspect",
    }).review_verdict is None
    node = rest.NodeCreate.model_validate({
        "title": "implement", "kind": "agent", "writes_code": True, "project_id": U3,
    })
    assert node.writes_code is True and node.project_id == U3


def test_daemon_m6_frames_and_diff_payload() -> None:
    merge = daemon.WorktreeMergeData.model_validate({
        "task_id": U1,
        "project_id": U2,
        "repo_path": r"D:\\repo",
        "branch": "coagentia/task-01",
        "message": "merge task 1",
    })
    assert merge.message == "merge task 1"
    status = daemon.WorktreeStatusData.model_validate({
        "task_id": U1,
        "status": "conflicted",
        "branch": "coagentia/task-01",
        "path": r"D:\\worktrees\\01",
        "conflict_files": ["src/app.py"],
    })
    assert status.merge_commit is None and status.conflict_files == ["src/app.py"]

    run = daemon.CheckRunData.model_validate({
        "run_id": U1,
        "node_id": U2,
        "project_id": U3,
        "repo_path": r"D:\\repo",
        "command": "uv run pytest -q",
    })
    assert run.project_id == U3

    diff = daemon.DiffPayload.model_validate({
        "base_ref": "main",
        "head_ref": "coagentia/task-01",
        "files": [{
            "path": "src/app.py",
            "status": "modified",
            "additions": 2,
            "deletions": 1,
            "patch": "@@ -1 +1 @@",
            "patch_truncated": False,
        }],
        "total_additions": 2,
        "total_deletions": 1,
        "files_truncated": False,
    })
    assert diff.files[0].status == "modified"

    check = daemon.CheckFinishedData.model_validate({
        "run_id": U1,
        "node_id": U2,
        "status": "failed",
        "exit_code": 1,
        "output_tail": "failed",
    })
    assert check.exit_code == 1


def test_proposal_request_shapes() -> None:
    assert rest.DecomposeRequest(text="build it").task_id is None
    with pytest.raises(ValidationError):
        rest.DecomposeRequest()
    confirm = rest.ProposalConfirm.model_validate({
        "expected": {
            "proposal_hash": SHA,
            "baseline_version": 3,
            "baseline_hash": SHA,
        },
        "removed_ops": [2],
    })
    assert confirm.removed_ops == [2]


def test_review_verdict_catalog_exact() -> None:
    assert {item.value for item in ReviewVerdict} == {
        "pass", "downgrade", "send_back", "needs_human",
    }
