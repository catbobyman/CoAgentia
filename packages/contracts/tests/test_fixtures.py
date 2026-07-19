"""fixtures 校验（M3 验证）：全量过契约模型 + 引用一致性 + 指纹一致性。"""

import json
from pathlib import Path

import pytest
from coagentia_contracts import entities, ws

FIXTURES = Path(__file__).parents[2] / "fixtures"


@pytest.fixture(scope="module")
def seed() -> dict:
    return json.loads((FIXTURES / "seed.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def timeline() -> list:
    return json.loads((FIXTURES / "timeline.json").read_text(encoding="utf-8"))


def test_seed_validates_against_contracts(seed: dict) -> None:
    entities.WorkspaceRow.model_validate(seed["workspace"])
    plans: list[tuple[str, type]] = [
        ("computers", entities.ComputerRow), ("members", entities.MemberRow),
        ("agents", entities.AgentRow), ("channels", entities.ChannelRow),
        ("channel_members", entities.ChannelMemberRow), ("messages", entities.MessageRow),
        ("message_mentions", entities.MessageMentionRow), ("tasks", entities.TaskRow),
        ("read_positions", entities.ReadPositionRow),
        ("token_usage_events", entities.TokenUsageEventRow),
    ]  # canvases 键随 DEDAG 退役自 seed 移除（冻结表仅存 DDL，fixtures 不再灌种）
    for key, model in plans:
        for row in seed[key]:
            model.model_validate(row)


def test_design_sample_names(seed: dict) -> None:
    """设计稿统一样例(勿换名字):Memcyo/Pat/Hank/Rin/Orchestrator、Catmem's PC、#build。"""
    names = {m["name"] for m in seed["members"]}
    assert {"Memcyo", "Pat", "Hank", "Rin", "Orchestrator"} <= names
    assert seed["computers"][0]["name"] == "Catmem's PC"
    build = next(c for c in seed["channels"] if c["name"] == "build")
    assert build["description"].startswith("番茄钟 MVP")
    assert {t["number"] for t in seed["tasks"]} == {1, 2, 3, 4, 5, 6, 7}


def test_referential_integrity(seed: dict) -> None:
    member_ids = {m["id"] for m in seed["members"]}
    channel_ids = {c["id"] for c in seed["channels"]}
    message_ids = {m["id"] for m in seed["messages"]}
    task_ids = {t["id"] for t in seed["tasks"]}

    for a in seed["agents"]:
        assert a["member_id"] in member_ids
        assert a["computer_id"] == seed["computers"][0]["id"]
    for cm in seed["channel_members"]:
        assert cm["channel_id"] in channel_ids and cm["member_id"] in member_ids
    for m in seed["messages"]:
        assert m["channel_id"] in channel_ids
        assert m["author_member_id"] is None or m["author_member_id"] in member_ids
        # 消息作者必须是频道成员（投递语义前提）
        if m["author_member_id"]:
            crew = {cm["member_id"] for cm in seed["channel_members"]
                    if cm["channel_id"] == m["channel_id"]}
            assert m["author_member_id"] in crew, m["id"]
    for mm in seed["message_mentions"]:
        assert mm["message_id"] in message_ids and mm["member_id"] in member_ids
    for t in seed["tasks"]:
        assert t["root_message_id"] in message_ids
        assert t["owner_member_id"] is None or t["owner_member_id"] in member_ids
    for rp in seed["read_positions"]:
        assert rp["member_id"] in member_ids and rp["channel_id"] in channel_ids
        assert rp["last_read_message_id"] in message_ids
    for tu in seed["token_usage_events"]:
        assert tu["agent_member_id"] in member_ids
        assert tu["task_id"] is None or tu["task_id"] in task_ids


def test_task_numbers_contiguous_and_counter(seed: dict) -> None:
    """编号频道内自增(契约 A):连续无空洞,next_task_number = max+1。"""
    build = next(c for c in seed["channels"] if c["name"] == "build")
    numbers = sorted(t["number"] for t in seed["tasks"])
    assert numbers == list(range(1, len(numbers) + 1))
    assert build["next_task_number"] == numbers[-1] + 1


def test_dm_invariants(seed: dict) -> None:
    """DM:恰两成员、is_private、dm_key = 排序后成员对(契约 A)。"""
    for ch in seed["channels"]:
        if ch["kind"] != "dm":
            continue
        crew = sorted(cm["member_id"] for cm in seed["channel_members"]
                      if cm["channel_id"] == ch["id"])
        assert len(crew) == 2
        assert ch["is_private"] is True
        assert ch["dm_key"] == ":".join(crew)


def test_seed_has_no_retired_domain_keys(seed: dict) -> None:
    """DEDAG：seed 不再灌种退役域（画布/提案/模板/落地批）——冻结表仅存 DDL。"""
    assert {"canvases", "canvas_nodes", "canvas_edges", "proposals", "templates",
            "landing_batches"}.isdisjoint(seed.keys())


def test_p1_screen_shapes(seed: dict) -> None:
    """P1 会话屏的关键形状:未读线位置与 #research 未读数(设计稿 cnt=3)。"""
    memcyo = next(m for m in seed["members"] if m["name"] == "Memcyo")
    research = next(c for c in seed["channels"] if c["name"] == "research")
    rp = next(r for r in seed["read_positions"]
              if r["member_id"] == memcyo["id"] and r["channel_id"] == research["id"])
    research_msgs = sorted((m for m in seed["messages"] if m["channel_id"] == research["id"]),
                           key=lambda m: m["id"])
    read_index = [m["id"] for m in research_msgs].index(rp["last_read_message_id"])
    assert len(research_msgs) - read_index - 1 == 3


def test_timeline_payloads_validate(timeline: list) -> None:
    for entry in timeline:
        model = ws.EVENT_PAYLOADS[ws.EventType(entry["type"])]
        model.model_validate(entry["data"])
        assert entry["delay_ms"] >= 0
