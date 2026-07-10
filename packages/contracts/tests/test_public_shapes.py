"""Public 形状与 Row 的字段集关系（契约 A §8.2 + 契约 D §9.2 / E 的两处放宽裁决）。"""

from coagentia_contracts import entities


def fields(model: type) -> set[str]:
    return set(model.model_fields)  # type: ignore[attr-defined]


def test_computer_public_drops_api_key_hash() -> None:
    assert fields(entities.ComputerPublic) == fields(entities.ComputerRow) - {"api_key_hash"}


def test_file_public_drops_stored_path_and_relaxes_binding() -> None:
    assert fields(entities.FilePublic) == fields(entities.FileRow) - {"stored_path"}
    # staging 态（契约 D §9.2）：message_id/channel_id 可空
    staging = entities.FilePublic.model_validate({
        "id": "01JZKJ7GG00000000000000001",
        "workspace_id": "01JZKJ7GG00000000000000002",
        "name": "spec.md",
        "mime": "text/markdown",
        "size_bytes": 1024,
        "sha256": "a" * 64,
        "created_at": "2026-07-09T12:00:00.000Z",
    })
    assert staging.message_id is None


def test_deployment_public_drops_log_path() -> None:
    assert fields(entities.DeploymentPublic) == fields(entities.DeploymentRow) - {"log_path"}


def test_activity_item_public_adds_actor() -> None:
    """读面增派生字段 actor_member_id（消息作者，联查得出不落库）——M2 二轮 review 裁决。"""
    assert fields(entities.ActivityItemPublic) == fields(entities.ActivityItemRow) | {
        "actor_member_id"
    }
    # 可选字段：老载荷（无 actor 键）仍验证通过。
    legacy = entities.ActivityItemPublic.model_validate({
        "id": "01JZKJ7GG00000000000000001",
        "workspace_id": "01JZKJ7GG00000000000000002",
        "member_id": "01JZKJ7GG00000000000000003",
        "kind": "mention",
        "created_at": "2026-07-09T12:00:00.000Z",
    })
    assert legacy.actor_member_id is None


def test_all_other_publics_equal_rows() -> None:
    """其余 Public = Row（子类零改动）；有意放宽的四个在上面单测。"""
    pairs = [
        (entities.WorkspaceRow, entities.WorkspacePublic),
        (entities.MemberRow, entities.MemberPublic),
        (entities.AgentRow, entities.AgentPublic),
        (entities.ChannelRow, entities.ChannelPublic),
        (entities.MessageRow, entities.MessagePublic),
        (entities.ReadPositionRow, entities.ReadPositionPublic),
        (entities.TaskRow, entities.TaskPublic),
        (entities.TaskEventRow, entities.TaskEventPublic),
        (entities.TaskContractRow, entities.TaskContractPublic),
        (entities.CanvasRow, entities.CanvasPublic),
        (entities.CanvasNodeRow, entities.CanvasNodePublic),
        (entities.CanvasEdgeRow, entities.CanvasEdgePublic),
        (entities.HeldDraftRow, entities.HeldDraftPublic),
        (entities.ReminderRow, entities.ReminderPublic),
        (entities.DiagnosticEventRow, entities.DiagnosticEventPublic),
        (entities.TokenUsageEventRow, entities.TokenUsageEventPublic),
        (entities.LandingBatchRow, entities.LandingBatchPublic),
        (entities.LedgerEntryRow, entities.LedgerEntryPublic),
        (entities.ProposalRow, entities.ProposalPublic),
        (entities.ProjectRow, entities.ProjectPublic),
        (entities.WorktreeRow, entities.WorktreePublic),
        (entities.PreviewSessionRow, entities.PreviewSessionPublic),
        (entities.DeploymentRow, entities.DeploymentPublic),
        (entities.TemplateRow, entities.TemplatePublic),
        (entities.ActivityItemRow, entities.ActivityItemPublic),
        (entities.ComputerRow, entities.ComputerPublic),
    ]
    for row, public in pairs:
        if public in (
            entities.ComputerPublic,
            entities.FilePublic,
            entities.DeploymentPublic,
            entities.ActivityItemPublic,
        ):
            continue
        assert fields(public) == fields(row), f"{public.__name__} != {row.__name__}"
