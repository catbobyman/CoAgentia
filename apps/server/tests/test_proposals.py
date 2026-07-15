"""J8 提案域测试（契约 B §12.1–§12.3 / 拆解设计 §3–§9）。

分两层：
- 单元层（_Tx 直驱域函数）：状态机逐边 / 解析挂接成败非 / 修复循环两轮穷尽 / rev+1 重置配额 /
  同指纹忽略 / 直落 landing / 注入内容 / 24h 提醒幂等 / 对账 #6 / 单一非终态 DB 兜底。
- HTTP 层（server_client + spy hub）：decompose 三入口 / NO_ORCHESTRATOR / 离线 503 /
  POST /agents role_template_key（未知 422）/ upsert 幂等 / GET /proposals / 提案卡 card_kind。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from typing import Any

import pytest
from coagentia_contracts import entities, rest
from coagentia_contracts.enums import InjectKind, ProposalStatus
from coagentia_contracts.kernel.decomposition import proposal_fingerprint
from coagentia_server.db import models
from coagentia_server.ledger.service import new_ulid, now_iso
from coagentia_server.orchestration import proposal as pd
from coagentia_server.orchestration.role_templates import (
    ORCHESTRATOR_ROLE_KEY,
    upsert_builtin_role_templates,
)
from daemon_helpers import Env
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

_PROPOSAL = models.tbl(models.Proposal)
_MSG = models.tbl(models.Message)
_TASK = models.tbl(models.Task)
_DIAG = models.tbl(models.DiagnosticEvent)


# ---------------------------------------------------------------- _Tx + 场景构造


class _Tx:
    """轻量事务上下文（deps.Tx/GatewayTx 的鸭子替身）：conn + 收集 emit。"""

    def __init__(self, conn: Any) -> None:
        self.conn = conn
        self.events: list[tuple[Any, str | None, dict[str, Any]]] = []

    def emit(self, etype: Any, channel_id: str | None, data: dict[str, Any]) -> None:
        self.events.append((etype, channel_id, data))


@contextlib.contextmanager
def _tx(engine: Engine) -> Any:
    conn = engine.connect()
    trans = conn.begin()
    tx = _Tx(conn)
    try:
        yield tx
        trans.commit()
    finally:
        conn.close()


def _seed(engine: Engine, *, decomp_mode: str = "draft") -> dict[str, str]:
    """建 channel + orchestrator + human + source 任务 + 绑定 Project；返回 id 字典。"""
    upsert_builtin_role_templates(engine)
    env = Env(engine)
    channel = env.add_channel(name="build")
    orch = env.add_agent("Orch", "idle")
    human = env.owner_id
    with engine.begin() as c:
        c.execute(
            update(models.Agent.__table__)
            .where(models.Agent.__table__.c.member_id == orch)
            .values(role_template_key=ORCHESTRATOR_ROLE_KEY)
        )
        c.execute(
            update(models.Channel.__table__)
            .where(models.Channel.__table__.c.id == channel)
            .values(decomp_mode=decomp_mode)
        )
    env.join(channel, orch)
    env.join(channel, human)
    root_msg = env.add_message(channel, author=human, body="需要一个登录功能")
    task_id = new_ulid()
    project_id = new_ulid()
    with engine.begin() as c:
        c.execute(
            insert(models.Task.__table__).values(
                id=task_id, workspace_id=env.ws_id, channel_id=channel, number=1,
                root_message_id=root_msg, title="登录功能", status="todo", level="l1",
                created_by_member_id=human, status_changed_at=now_iso(), created_at=now_iso(),
            )
        )
        c.execute(
            insert(models.Project.__table__).values(
                id=project_id, workspace_id=env.ws_id, computer_id=env.comp_id,
                name="Repo", repo_path="/tmp/repo", dev_command="pytest", created_at=now_iso(),
            )
        )
        c.execute(
            insert(models.ChannelProject.__table__).values(
                channel_id=channel, project_id=project_id
            )
        )
    return {
        "ws": env.ws_id, "channel": channel, "orch": orch, "human": human,
        "task": task_id, "root_msg": root_msg, "project": project_id,
    }


def _valid_single_task_body(source_task_id: str) -> dict[str, Any]:
    return {
        "version": "coagentia.decomposition.v1",
        "source": source_task_id,
        "mode": "single_task",
        "summary": "单节点实现登录",
        "nodes": [
            {
                "temp_id": "N1", "title": "实现登录", "kind": "agent",
                "task_plan": {
                    "goal": "实现登录",
                    "acceptance_criteria": [
                        {"id": "AC1", "statement": "登录成功",
                         "verify_by": "command", "verify_ref": "pytest"},
                    ],
                },
            }
        ],
    }


def _control_msg(body: dict[str, Any]) -> str:
    control = "<control>" + json.dumps(body, ensure_ascii=False) + "</control>"
    return "这是我的拆解提案（散文说明）。\n\n" + control


def _invalid_decompose_body(source_task_id: str) -> dict[str, Any]:
    """校验失败提案：decompose 模式仅 1 节点（NODE_COUNT）。"""
    return {
        "version": "coagentia.decomposition.v1", "source": source_task_id,
        "mode": "decompose", "summary": "x",
        "nodes": [{
            "temp_id": "N1", "title": "只有一个", "kind": "agent",
            "task_plan": {"goal": "g", "acceptance_criteria": [
                {"id": "AC1", "statement": "s", "verify_by": "manual", "verify_ref": "r"}]},
        }],
    }


def _make_drafting(engine: Engine, ids: dict[str, str]) -> str:
    with _tx(engine) as tx:
        proposal = pd.create_drafting_proposal(
            tx, workspace_id=ids["ws"], channel_id=ids["channel"],
            source_task_id=ids["task"], proposed_by=ids["orch"],
        )
    return proposal["id"]


def _channel_row(engine: Engine, channel_id: str) -> dict[str, Any]:
    with engine.connect() as c:
        return dict(
            c.execute(
                select(models.Channel.__table__).where(models.Channel.__table__.c.id == channel_id)
            ).mappings().one()
        )


def _proposal_row(engine: Engine, pid: str) -> dict[str, Any]:
    with engine.connect() as c:
        return dict(c.execute(select(_PROPOSAL).where(_PROPOSAL.c.id == pid)).mappings().one())


# ---------------------------------------------------------------- 状态机逐边


def test_transition_table_covers_j8_edges() -> None:
    T = pd.PROPOSAL_TRANSITIONS
    S = ProposalStatus
    assert S.VALIDATING in T[S.DRAFTING] and S.SUPERSEDED in T[S.DRAFTING]
    assert {S.REPAIRING, S.AWAITING_CONFIRM, S.LANDING, S.FAILED} <= T[S.VALIDATING]
    assert S.VALIDATING in T[S.REPAIRING] and S.FAILED in T[S.REPAIRING]
    # 终态无出边。
    for term in (S.SUPERSEDED, S.REJECTED, S.FAILED, S.LANDED):
        assert T[term] == frozenset() or term == S.LANDED  # landed→awaiting(delta) 归 J10


def test_illegal_transition_raises(migrated_engine: Engine) -> None:
    ids = _seed(migrated_engine)
    pid = _make_drafting(migrated_engine, ids)
    with _tx(migrated_engine) as tx:
        proposal = pd.fetch_proposal(tx.conn, pid)
        assert proposal is not None
        # drafting → landed 非法（须经 validating）。
        with pytest.raises(ValueError):
            pd._transition(tx, proposal, ProposalStatus.LANDED)


def test_single_active_proposal_db_backstop(migrated_engine: Engine) -> None:
    """并发同 source 第二个非终态提案 → 部分唯一索引兜底（IntegrityError）。"""
    ids = _seed(migrated_engine)
    _make_drafting(migrated_engine, ids)
    with pytest.raises(IntegrityError):
        _make_drafting(migrated_engine, ids)


# ---------------------------------------------------------------- 解析挂接：成 / 败 / 非情形


def test_classify_non_proposal_message_returns_none(migrated_engine: Engine) -> None:
    ids = _seed(migrated_engine)
    _make_drafting(migrated_engine, ids)
    channel = _channel_row(migrated_engine, ids["channel"])
    with _tx(migrated_engine) as tx:
        # ① 非作者（human 发的）→ None
        assert pd.classify_submission(
            tx, channel=channel, author_member_id=ids["human"],
            body="讨论 <control>{}</control>", thread_root_id=ids["root_msg"],
        ) is None
        # ② 作者对但无 <control> → None（普通讨论）
        assert pd.classify_submission(
            tx, channel=channel, author_member_id=ids["orch"],
            body="我在想怎么拆", thread_root_id=ids["root_msg"],
        ) is None
        # ③ 顶级消息（thread_root_id None）→ None（提案在 source 线程内提交）
        assert pd.classify_submission(
            tx, channel=channel, author_member_id=ids["orch"],
            body="<control>{}</control>", thread_root_id=None,
        ) is None


def test_classify_success_awaiting_confirm(migrated_engine: Engine) -> None:
    ids = _seed(migrated_engine)
    pid = _make_drafting(migrated_engine, ids)
    channel = _channel_row(migrated_engine, ids["channel"])
    body = _valid_single_task_body(ids["task"])
    with _tx(migrated_engine) as tx:
        decision = pd.classify_submission(
            tx, channel=channel, author_member_id=ids["orch"],
            body=_control_msg(body), thread_root_id=ids["root_msg"],
        )
        assert decision is not None
        assert decision.card_kind == "proposal"
        assert decision.card_ref == pid  # drafting 同 revision 同行
        injects = decision.apply(tx)
        assert injects == []  # 成功不 inject（awaiting 等人类）
    proposal = _proposal_row(migrated_engine, pid)
    assert proposal["status"] == ProposalStatus.AWAITING_CONFIRM.value
    assert proposal["proposal_hash"] == proposal_fingerprint(body)


def test_classify_direct_mode_lands(migrated_engine: Engine) -> None:
    ids = _seed(migrated_engine, decomp_mode="direct")
    pid = _make_drafting(migrated_engine, ids)
    channel = _channel_row(migrated_engine, ids["channel"])
    with _tx(migrated_engine) as tx:
        decision = pd.classify_submission(
            tx, channel=channel, author_member_id=ids["orch"],
            body=_control_msg(_valid_single_task_body(ids["task"])),
            thread_root_id=ids["root_msg"],
        )
        assert decision is not None
        decision.apply(tx)
    assert _proposal_row(migrated_engine, pid)["status"] == ProposalStatus.LANDING.value


# ---------------------------------------------------------------- 修复循环两轮穷尽 → Failed


def test_repair_loop_exhausts_to_failed(migrated_engine: Engine) -> None:
    ids = _seed(migrated_engine)
    pid = _make_drafting(migrated_engine, ids)
    channel = _channel_row(migrated_engine, ids["channel"])
    # 无效提案：decompose 模式仅 1 节点（NODE_COUNT）。
    bad = _invalid_decompose_body(ids["task"])

    def submit() -> list[pd.PendingInject]:
        with _tx(migrated_engine) as tx:
            decision = pd.classify_submission(
                tx, channel=channel, author_member_id=ids["orch"],
                body=_control_msg(bad), thread_root_id=ids["root_msg"],
            )
            assert decision is not None
            assert decision.card_kind is None  # 校验失败非提案卡
            return decision.apply(tx)

    # 第 1 次失败 → repairing，repair_count=1，直投 1 条（第 1/2 次）。
    injects1 = submit()
    assert len(injects1) == 1 and injects1[0].kind is InjectKind.REPAIR
    assert injects1[0].best_effort is True
    p = _proposal_row(migrated_engine, pid)
    assert p["status"] == ProposalStatus.REPAIRING.value and p["repair_count"] == 1
    assert "第 1/2 次" in injects1[0].body

    # 第 2 次失败 → repairing，repair_count=2（第 2/2 次）。
    injects2 = submit()
    assert len(injects2) == 1 and "第 2/2 次" in injects2[0].body
    assert _proposal_row(migrated_engine, pid)["repair_count"] == 2

    # 第 3 次失败 → failed，不再 inject，source 线程系统消息 @人类。
    before = _system_msg_count(migrated_engine, ids["channel"])
    injects3 = submit()
    assert injects3 == []
    assert _proposal_row(migrated_engine, pid)["status"] == ProposalStatus.FAILED.value
    assert _system_msg_count(migrated_engine, ids["channel"]) == before + 1
    # failed_escalated 诊断落库。
    assert _diag_count(migrated_engine, "proposal.failed_escalated") == 1


def test_repair_with_unfingerprintable_body_no_500(migrated_engine: Engine) -> None:
    """F4 回归（M6 review）：修复路径对未通过校验的体取哈希不得炸——float（违反 A §2.1）、
    混型 temp_id（排序键 TypeError）都该走 _fingerprint_lenient 兜底进 repairing，而非 500 回滚。"""
    ids = _seed(migrated_engine)
    pid = _make_drafting(migrated_engine, ids)
    channel = _channel_row(migrated_engine, ids["channel"])
    bad = _invalid_decompose_body(ids["task"])
    bad["nodes"][0]["task_plan"]["estimate"] = 1.5  # UNKNOWN_FIELD + float（指纹域外值）
    bad["nodes"].append({"temp_id": 2, "title": "混型"})  # 混型 temp_id（排序键守卫）
    with _tx(migrated_engine) as tx:
        decision = pd.classify_submission(
            tx, channel=channel, author_member_id=ids["orch"],
            body=_control_msg(bad), thread_root_id=ids["root_msg"],
        )
        assert decision is not None
        injects = decision.apply(tx)  # 修复前此处 ValueError/TypeError → 500
    assert len(injects) == 1 and injects[0].kind is InjectKind.REPAIR
    row = _proposal_row(migrated_engine, pid)
    assert row["status"] == ProposalStatus.REPAIRING.value
    assert isinstance(row["proposal_hash"], str) and len(row["proposal_hash"]) == 64


def test_awaiting_invalid_float_body_revbumps_no_500(migrated_engine: Engine) -> None:
    """F4 回归（M6 review）：awaiting_confirm 期作者重提含 float 的无效体 →
    _apply_revbump_invalid 取哈希不得炸，正常 rev+1 进 repairing。"""
    ids = _seed(migrated_engine)
    pid = _make_drafting(migrated_engine, ids)
    channel = _channel_row(migrated_engine, ids["channel"])
    with _tx(migrated_engine) as tx:
        decision = pd.classify_submission(
            tx, channel=channel, author_member_id=ids["orch"],
            body=_control_msg(_valid_single_task_body(ids["task"])),
            thread_root_id=ids["root_msg"],
        )
        assert decision is not None
        decision.apply(tx)
    assert _proposal_row(migrated_engine, pid)["status"] == ProposalStatus.AWAITING_CONFIRM.value
    bad = _invalid_decompose_body(ids["task"])
    bad["estimate"] = 1.5  # 顶层未知字段 + float
    with _tx(migrated_engine) as tx:
        decision = pd.classify_submission(
            tx, channel=channel, author_member_id=ids["orch"],
            body=_control_msg(bad), thread_root_id=ids["root_msg"],
        )
        assert decision is not None
        injects = decision.apply(tx)  # 修复前此处 ValueError → 500
    assert len(injects) == 1 and injects[0].kind is InjectKind.REPAIR
    old = _proposal_row(migrated_engine, pid)
    assert old["status"] == ProposalStatus.SUPERSEDED.value
    with migrated_engine.connect() as c:
        new_row = dict(c.execute(
            select(_PROPOSAL).where(
                _PROPOSAL.c.source_task_id == ids["task"], _PROPOSAL.c.revision == 2
            )
        ).mappings().one())
    assert new_row["status"] == ProposalStatus.REPAIRING.value
    assert len(new_row["proposal_hash"]) == 64


def _system_msg_count(engine: Engine, channel_id: str) -> int:
    with engine.connect() as c:
        return len(c.execute(
            select(_MSG.c.id).where(_MSG.c.channel_id == channel_id, _MSG.c.kind == "system")
        ).all())


def _diag_count(engine: Engine, diag_type: str) -> int:
    with engine.connect() as c:
        return len(c.execute(select(_DIAG.c.seq).where(_DIAG.c.type == diag_type)).all())


# ---------------------------------------------------------------- rev+1 重置配额 / 同指纹忽略


def test_dialogue_revision_bump_resets_quota(migrated_engine: Engine) -> None:
    ids = _seed(migrated_engine)
    pid = _make_drafting(migrated_engine, ids)
    channel = _channel_row(migrated_engine, ids["channel"])
    # 先提交有效提案 → awaiting_confirm。
    with _tx(migrated_engine) as tx:
        pd.classify_submission(
            tx, channel=channel, author_member_id=ids["orch"],
            body=_control_msg(_valid_single_task_body(ids["task"])),
            thread_root_id=ids["root_msg"],
        ).apply(tx)  # type: ignore[union-attr]
    # 对话修正：不同指纹（改 summary）→ rev+1、旧行 superseded、新行 repair_count=0。
    body2 = _valid_single_task_body(ids["task"])
    body2["summary"] = "改了拆解思路"
    with _tx(migrated_engine) as tx:
        decision = pd.classify_submission(
            tx, channel=channel, author_member_id=ids["orch"],
            body=_control_msg(body2), thread_root_id=ids["root_msg"],
        )
        assert decision is not None and decision.card_ref != pid  # 新行 id
        new_id = decision.card_ref
        decision.apply(tx)
    assert _proposal_row(migrated_engine, pid)["status"] == ProposalStatus.SUPERSEDED.value
    new_row = _proposal_row(migrated_engine, new_id)
    assert new_row["revision"] == 2 and new_row["repair_count"] == 0
    assert new_row["status"] == ProposalStatus.AWAITING_CONFIRM.value


def test_dialogue_same_fingerprint_ignored(migrated_engine: Engine) -> None:
    ids = _seed(migrated_engine)
    pid = _make_drafting(migrated_engine, ids)
    channel = _channel_row(migrated_engine, ids["channel"])
    body = _valid_single_task_body(ids["task"])
    with _tx(migrated_engine) as tx:
        pd.classify_submission(
            tx, channel=channel, author_member_id=ids["orch"],
            body=_control_msg(body), thread_root_id=ids["root_msg"],
        ).apply(tx)  # type: ignore[union-attr]
    rev_before = _proposal_row(migrated_engine, pid)["revision"]
    # 同指纹重提（awaiting_confirm 期）→ 忽略，不动 revision，写 duplicate_ignored 诊断。
    with _tx(migrated_engine) as tx:
        decision = pd.classify_submission(
            tx, channel=channel, author_member_id=ids["orch"],
            body=_control_msg(body), thread_root_id=ids["root_msg"],
        )
        assert decision is not None and decision.card_kind is None
        decision.apply(tx)
    assert _proposal_row(migrated_engine, pid)["revision"] == rev_before
    assert _diag_count(migrated_engine, "proposal.duplicate_ignored") == 1


# ---------------------------------------------------------------- awaiting 期无效重提（rev+1）


def _submit(
    engine: Engine, ids: dict[str, str], channel: dict[str, Any], body_text: str
) -> tuple[Any, list[pd.PendingInject], list[tuple[Any, str | None, dict[str, Any]]]]:
    """提交一条提案消息（unit 直驱）：返回 (decision, injects, events)。"""
    with _tx(engine) as tx:
        decision = pd.classify_submission(
            tx, channel=channel, author_member_id=ids["orch"],
            body=body_text, thread_root_id=ids["root_msg"],
        )
        assert decision is not None
        injects = decision.apply(tx)
        return decision, injects, tx.events


def _goto_awaiting(engine: Engine, ids: dict[str, str], channel: dict[str, Any]) -> str:
    """建 drafting → 提交有效提案 → awaiting_confirm；返回 proposal id。"""
    pid = _make_drafting(engine, ids)
    _submit(engine, ids, channel, _control_msg(_valid_single_task_body(ids["task"])))
    assert _proposal_row(engine, pid)["status"] == ProposalStatus.AWAITING_CONFIRM.value
    return pid


def _active_row(engine: Engine, source_task_id: str) -> dict[str, Any]:
    with engine.connect() as c:
        row = pd.active_proposal_for_source(c, source_task_id)
    assert row is not None
    return row


def test_awaiting_invalid_resubmit_revbumps_to_repairing(migrated_engine: Engine) -> None:
    """awaiting_confirm + 无效新 control（校验失败版）= 对话修正失败版：不 500、旧行 superseded、
    新行 rev+1 状态 repairing（repair_count=1 配额全新）、repair 直投 attempt=1、事件断言。"""
    from coagentia_contracts.ws import EventType

    ids = _seed(migrated_engine)
    channel = _channel_row(migrated_engine, ids["channel"])
    old_pid = _goto_awaiting(migrated_engine, ids, channel)

    decision, injects, events = _submit(
        migrated_engine, ids, channel, _control_msg(_invalid_decompose_body(ids["task"]))
    )
    assert decision.card_kind is None  # 校验未过不落提案卡
    # 旧行 superseded；新行 rev+1、repairing、配额全新（attempt 1 已消耗 → repair_count=1）。
    assert _proposal_row(migrated_engine, old_pid)["status"] == ProposalStatus.SUPERSEDED.value
    new_row = _active_row(migrated_engine, ids["task"])
    assert new_row["id"] != old_pid
    assert new_row["revision"] == 2
    assert new_row["status"] == ProposalStatus.REPAIRING.value
    assert new_row["repair_count"] == 1
    # repair 直投发出（第 1/2 次，新 revision）。
    assert len(injects) == 1 and injects[0].kind is InjectKind.REPAIR
    assert "第 1/2 次" in injects[0].body and "rev.2" in injects[0].body
    # 事件：draft.superseded + proposal.updated 均已登记。
    etypes = [e for e, _, _ in events]
    assert EventType.DRAFT_SUPERSEDED in etypes
    assert EventType.PROPOSAL_UPDATED in etypes


def test_awaiting_invalid_resubmit_control_parse_placeholder_body(
    migrated_engine: Engine,
) -> None:
    """awaiting_confirm + CONTROL_PARSE 失败版：同走 rev+1 失败链；新行 body={} 占位。"""
    ids = _seed(migrated_engine)
    channel = _channel_row(migrated_engine, ids["channel"])
    old_pid = _goto_awaiting(migrated_engine, ids, channel)

    decision, injects, _events = _submit(
        migrated_engine, ids, channel, "修正版：<control>{bad json</control>"
    )
    assert decision.card_kind is None
    assert _proposal_row(migrated_engine, old_pid)["status"] == ProposalStatus.SUPERSEDED.value
    new_row = _active_row(migrated_engine, ids["task"])
    assert new_row["revision"] == 2
    assert new_row["status"] == ProposalStatus.REPAIRING.value
    assert new_row["repair_count"] == 1
    assert new_row["body"] == {}  # CONTROL_PARSE 无 parsed → 占位 {}
    assert len(injects) == 1 and injects[0].kind is InjectKind.REPAIR


def test_awaiting_invalid_revision_quota_independent(migrated_engine: Engine) -> None:
    """rev+1 失败版配额独立：首败已耗 attempt 1，再连败 2 次 → 新行 failed @人类（共 3 次失败）。"""
    ids = _seed(migrated_engine)
    channel = _channel_row(migrated_engine, ids["channel"])
    _goto_awaiting(migrated_engine, ids, channel)
    bad = _control_msg(_invalid_decompose_body(ids["task"]))

    _submit(migrated_engine, ids, channel, bad)  # rev+1 失败版：attempt 1
    new_row = _active_row(migrated_engine, ids["task"])
    _submit(migrated_engine, ids, channel, bad)  # attempt 2（同 revision 同行）
    assert _proposal_row(migrated_engine, new_row["id"])["repair_count"] == 2

    before = _system_msg_count(migrated_engine, ids["channel"])
    _, injects, _ = _submit(migrated_engine, ids, channel, bad)  # 第 3 次 → failed
    assert injects == []
    final = _proposal_row(migrated_engine, new_row["id"])
    assert final["status"] == ProposalStatus.FAILED.value and final["revision"] == 2
    assert _system_msg_count(migrated_engine, ids["channel"]) == before + 1
    assert _diag_count(migrated_engine, "proposal.failed_escalated") == 1


# ---------------------------------------------------------------- landing 期重提一律忽略


def _goto_landing(engine: Engine, ids: dict[str, str], channel: dict[str, Any]) -> str:
    """直落频道：建 drafting → 提交有效提案 → landing；返回 proposal id。"""
    pid = _make_drafting(engine, ids)
    _submit(engine, ids, channel, _control_msg(_valid_single_task_body(ids["task"])))
    assert _proposal_row(engine, pid)["status"] == ProposalStatus.LANDING.value
    return pid


def _proposal_count(engine: Engine) -> int:
    with engine.connect() as c:
        return len(c.execute(select(_PROPOSAL.c.id)).all())


def test_landing_valid_control_ignored(migrated_engine: Engine) -> None:
    """landing + 有效异指纹新 control → 忽略：状态仍 landing、无新行、诊断留痕（reason）。"""
    ids = _seed(migrated_engine, decomp_mode="direct")
    channel = _channel_row(migrated_engine, ids["channel"])
    pid = _goto_landing(migrated_engine, ids, channel)
    count_before = _proposal_count(migrated_engine)

    body2 = _valid_single_task_body(ids["task"])
    body2["summary"] = "落地中途想改主意"
    decision, injects, _ = _submit(migrated_engine, ids, channel, _control_msg(body2))
    assert decision.card_kind is None and injects == []
    assert _proposal_row(migrated_engine, pid)["status"] == ProposalStatus.LANDING.value
    assert _proposal_count(migrated_engine) == count_before  # 无新行、不 supersede
    with migrated_engine.connect() as c:
        diag = c.execute(
            select(_DIAG.c.payload).where(_DIAG.c.type == "proposal.duplicate_ignored")
        ).scalars().all()
    assert any(p.get("reason") == "landing_in_progress" for p in diag)


def test_landing_invalid_control_ignored(migrated_engine: Engine) -> None:
    """landing + 无效新 control → 同样忽略（不进修复循环、不 500）。"""
    ids = _seed(migrated_engine, decomp_mode="direct")
    channel = _channel_row(migrated_engine, ids["channel"])
    pid = _goto_landing(migrated_engine, ids, channel)
    count_before = _proposal_count(migrated_engine)

    decision, injects, _ = _submit(
        migrated_engine, ids, channel, _control_msg(_invalid_decompose_body(ids["task"]))
    )
    assert decision.card_kind is None and injects == []
    row = _proposal_row(migrated_engine, pid)
    assert row["status"] == ProposalStatus.LANDING.value and row["repair_count"] == 0
    assert _proposal_count(migrated_engine) == count_before


# ---------------------------------------------------------------- 上下文注入内容


def test_injection_body_contains_context(migrated_engine: Engine) -> None:
    ids = _seed(migrated_engine)
    channel = _channel_row(migrated_engine, ids["channel"])
    with _tx(migrated_engine) as tx:
        source_task = dict(
            tx.conn.execute(select(_TASK).where(_TASK.c.id == ids["task"])).mappings().one()
        )
        proposal = pd.create_drafting_proposal(
            tx, workspace_id=ids["ws"], channel_id=ids["channel"],
            source_task_id=ids["task"], proposed_by=ids["orch"],
        )
        body = pd.build_injection_body(
            tx.conn, proposal=proposal, source_task=source_task, channel=channel
        )
    # prompt_sections 在场（角色说明第 6 条含 schema 版本串）。
    assert "coagentia.decomposition.v1" in body
    assert "角色说明" in body
    # 成员清单含 member_id（Orchestrator 自身 id）。
    assert f"member_id={ids['orch']}" in body
    # Project 清单含 project_id。
    assert f"project_id={ids['project']}" in body
    # source_task_id + 频道配置在场。
    assert f"source_task_id={ids['task']}" in body
    assert "decomp_mode=draft" in body and "decomp_node_limit=12" in body


# ---------------------------------------------------------------- 对账 #6 + 24h 提醒


def test_reconcile_repairing_injects(migrated_engine: Engine) -> None:
    ids = _seed(migrated_engine)
    pid = _make_drafting(migrated_engine, ids)
    channel = _channel_row(migrated_engine, ids["channel"])
    bad = _invalid_decompose_body(ids["task"])
    with _tx(migrated_engine) as tx:
        pd.classify_submission(
            tx, channel=channel, author_member_id=ids["orch"],
            body=_control_msg(bad), thread_root_id=ids["root_msg"],
        ).apply(tx)  # type: ignore[union-attr]
    assert _proposal_row(migrated_engine, pid)["status"] == ProposalStatus.REPAIRING.value
    # 对账 #6：从 body 重算错误清单重发（本机 orch 名下）。
    with migrated_engine.connect() as c:
        injects = pd.repairing_reconcile_injects(c, agent_member_ids={ids["orch"]})
    assert len(injects) == 1
    assert injects[0].agent_member_id == ids["orch"]
    assert injects[0].kind is InjectKind.REPAIR
    assert injects[0].ref == pid
    # 无本机 Agent → 空。
    with migrated_engine.connect() as c:
        assert pd.repairing_reconcile_injects(c, agent_member_ids=set()) == []


def test_awaiting_confirm_24h_reminder_idempotent(migrated_engine: Engine) -> None:
    ids = _seed(migrated_engine)
    pid = _make_drafting(migrated_engine, ids)
    channel = _channel_row(migrated_engine, ids["channel"])
    with _tx(migrated_engine) as tx:
        pd.classify_submission(
            tx, channel=channel, author_member_id=ids["orch"],
            body=_control_msg(_valid_single_task_body(ids["task"])),
            thread_root_id=ids["root_msg"],
        ).apply(tx)  # type: ignore[union-attr]
    # 把 updated_at 拨到过去（模拟超 24h）。
    with migrated_engine.begin() as c:
        c.execute(
            update(_PROPOSAL).where(_PROPOSAL.c.id == pid)
            .values(updated_at="2000-01-01T00:00:00.000Z")
        )
    cutoff = "2001-01-01T00:00:00.000Z"
    before = _system_msg_count(migrated_engine, ids["channel"])
    with _tx(migrated_engine) as tx:
        sent = pd.awaiting_confirm_reminder_scan(tx, cutoff_iso=cutoff)
    assert sent == 1
    assert _system_msg_count(migrated_engine, ids["channel"]) == before + 1
    # 再扫一次 → 幂等（防重发推导：诊断行已存在，created_at > updated_at）。
    with _tx(migrated_engine) as tx:
        assert pd.awaiting_confirm_reminder_scan(tx, cutoff_iso=cutoff) == 0
    assert _diag_count(migrated_engine, "proposal.awaiting_reminder_sent") == 1


# ---------------------------------------------------------------- HTTP 层（server_client + spy）


class _SpyHub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any, str | None]] = []

    def agent_daemon_online(self, agent_member_id: str) -> bool:
        return True

    def inject_orchestrator(
        self, agent_member_id: str, body: str, *, kind: Any, ref: str | None = None
    ) -> str:
        self.calls.append((agent_member_id, body, kind, ref))
        return "done"


class _OfflineHub:
    def agent_daemon_online(self, agent_member_id: str) -> bool:
        return False

    def inject_orchestrator(self, *a: Any, **k: Any) -> str:
        from coagentia_server.computers import DaemonOffline

        raise DaemonOffline("离线桩")


AGENT_KEY = "cak_orch_test"


def _http_orchestrator(client: TestClient, channel_id: str) -> dict[str, str]:
    """经 REST 建 Orchestrator（role_template_key）+ 加频道，返回 agent + 头。"""
    comp = client.get("/api/computers").json()[0]
    resp = client.post("/api/agents", json={
        "computer_id": comp["id"], "name": "OrchBot", "runtime": "claude_code",
        "model": "m", "role_template_key": "orchestrator",
    })
    assert resp.status_code == 201, resp.text
    agent = resp.json()
    client.post(f"/api/channels/{channel_id}/members", json={"member_id": agent["member_id"]})
    # 注入已知测试 key 到该 computer，供 Agent 主体发消息。
    digest = hashlib.sha256(AGENT_KEY.encode()).hexdigest()
    engine: Engine = client.app.state.engine  # type: ignore[attr-defined]
    with engine.begin() as c:
        c.execute(
            update(models.Computer.__table__).where(models.Computer.__table__.c.id == comp["id"])
            .values(api_key_hash=digest)
        )
    headers = {"Authorization": f"Bearer {AGENT_KEY}", "X-Acting-Member": agent["member_id"]}
    return {"member_id": agent["member_id"], "headers": headers}


def _build_channel(client: TestClient) -> str:
    r = client.post("/api/channels", json={"name": "orch-build", "member_ids": []})
    return r.json()["id"]


def test_post_agent_role_template_key_unknown_422(server_client: TestClient) -> None:
    comp = server_client.get("/api/computers").json()[0]
    r = server_client.post("/api/agents", json={
        "computer_id": comp["id"], "name": "Bad", "runtime": "claude_code",
        "model": "m", "role_template_key": "not_a_real_key",
    })
    assert r.status_code == 422, r.text
    err = rest.ErrorResponse.model_validate(r.json())
    assert err.error.code is rest.ErrorCode.VALIDATION_FAILED
    assert err.error.details == {"role_template_key": "not_a_real_key"}


def test_post_agent_role_template_key_persisted(server_client: TestClient) -> None:
    comp = server_client.get("/api/computers").json()[0]
    r = server_client.post("/api/agents", json={
        "computer_id": comp["id"], "name": "Orch2", "runtime": "claude_code",
        "model": "m", "role_template_key": "orchestrator",
    })
    assert r.status_code == 201, r.text
    agent = entities.AgentPublic.model_validate(r.json())
    assert agent.role_template_key == "orchestrator"


def test_role_template_upsert_idempotent(migrated_engine: Engine) -> None:
    Env(migrated_engine)
    upsert_builtin_role_templates(migrated_engine)
    upsert_builtin_role_templates(migrated_engine)  # 重启幂等
    _ROLE = models.tbl(models.AgentRoleTemplate)
    with migrated_engine.connect() as c:
        rows = c.execute(select(_ROLE).where(_ROLE.c.key == ORCHESTRATOR_ROLE_KEY)).all()
    assert len(rows) == 1


def test_decompose_no_orchestrator_409(server_client: TestClient) -> None:
    channel = _build_channel(server_client)
    r = server_client.post(f"/api/channels/{channel}/decompose", json={"text": "做个登录"})
    assert r.status_code == 409, r.text
    assert rest.ErrorResponse.model_validate(r.json()).error.code is rest.ErrorCode.NO_ORCHESTRATOR


def test_decompose_daemon_offline_503_rolls_back(server_client: TestClient) -> None:
    """CR-M8-1 后离线判定=agent_daemon_online 预检（写前快速失败），「不落库」语义不变。"""
    channel = _build_channel(server_client)
    _http_orchestrator(server_client, channel)
    server_client.app.state.daemon_hub = _OfflineHub()  # type: ignore[attr-defined]
    r = server_client.post(f"/api/channels/{channel}/decompose", json={"text": "做个登录"})
    assert r.status_code == 503, r.text
    assert rest.ErrorResponse.model_validate(r.json()).error.code is rest.ErrorCode.DAEMON_OFFLINE
    # 回滚：无 drafting 提案落库。
    engine: Engine = server_client.app.state.engine  # type: ignore[attr-defined]
    with engine.connect() as c:
        assert c.execute(select(_PROPOSAL.c.id)).first() is None


def test_decompose_text_creates_proposal_and_injects(server_client: TestClient) -> None:
    channel = _build_channel(server_client)
    orch = _http_orchestrator(server_client, channel)
    spy = _SpyHub()
    server_client.app.state.daemon_hub = spy  # type: ignore[attr-defined]
    r = server_client.post(f"/api/channels/{channel}/decompose", json={"text": "做个登录功能"})
    assert r.status_code == 202, r.text
    proposal = entities.ProposalPublic.model_validate(r.json())
    assert proposal.channel_id == channel
    assert proposal.status is ProposalStatus.DRAFTING
    # 注入到 Orchestrator，含上下文与角色 prompt。
    assert len(spy.calls) == 1
    agent_id, body, kind, ref = spy.calls[0]
    assert agent_id == orch["member_id"] and kind is InjectKind.SYSTEM and ref == proposal.id
    assert "coagentia.decomposition.v1" in body and "做个登录功能" in body
    # GET /proposals 回读。
    got = entities.ProposalPublic.model_validate(
        server_client.get(f"/api/proposals/{proposal.id}").json()
    )
    assert got.id == proposal.id


class _CommitProbeHub:
    """CR-M8-1 回归探针：inject 时从**独立连接**回读提案行——只有事务已提交才可见。"""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.committed_at_inject: list[bool] = []

    def agent_daemon_online(self, agent_member_id: str) -> bool:
        return True

    def inject_orchestrator(
        self, agent_member_id: str, body: str, *, kind: Any, ref: str | None = None
    ) -> str:
        with self.engine.connect() as c:
            row = c.execute(select(_PROPOSAL.c.id).where(_PROPOSAL.c.id == ref)).first()
        self.committed_at_inject.append(row is not None)
        return "done"


def test_decompose_inject_fires_after_commit(server_client: TestClient) -> None:
    """CR-M8-1 自死锁回归：上下文注入必须发生在写事务提交后（等 ack 期间不持 SQLite 写锁，
    daemon 的 agent.status/心跳写入不再被 decompose 事务阻塞）。inject 时独立连接须已能读到
    drafting 提案行。"""
    channel = _build_channel(server_client)
    _http_orchestrator(server_client, channel)
    engine: Engine = server_client.app.state.engine  # type: ignore[attr-defined]
    probe = _CommitProbeHub(engine)
    server_client.app.state.daemon_hub = probe  # type: ignore[attr-defined]
    r = server_client.post(f"/api/channels/{channel}/decompose", json={"text": "做个登录"})
    assert r.status_code == 202, r.text
    assert probe.committed_at_inject == [True]


def test_t1_inject_fires_after_commit(server_client: TestClient) -> None:
    """CR-M8-1 同族（T1 顶级 @Orchestrator 消息）：flush_injects 经 tx.after_commit 提交后
    投递——inject 时独立连接须已能读到 drafting 提案行与需求消息。"""
    channel = _build_channel(server_client)
    _http_orchestrator(server_client, channel)
    engine: Engine = server_client.app.state.engine  # type: ignore[attr-defined]
    probe = _CommitProbeHub(engine)
    server_client.app.state.daemon_hub = probe  # type: ignore[attr-defined]
    r = server_client.post(
        f"/api/channels/{channel}/messages", json={"body": "@OrchBot 帮我拆解登录功能"}
    )
    assert r.status_code == 201, r.text
    assert probe.committed_at_inject == [True]


def test_decompose_task_id_source(server_client: TestClient) -> None:
    channel = _build_channel(server_client)
    _http_orchestrator(server_client, channel)
    spy = _SpyHub()
    server_client.app.state.daemon_hub = spy  # type: ignore[attr-defined]
    # 建一个任务作 source。
    task = server_client.post(
        f"/api/channels/{channel}/messages", json={"body": "登录", "as_task": {"title": "登录"}}
    ).json()["task"]
    r = server_client.post(f"/api/channels/{channel}/decompose", json={"task_id": task["id"]})
    assert r.status_code == 202, r.text
    proposal = entities.ProposalPublic.model_validate(r.json())
    assert proposal.source_task_id == task["id"]


def test_decompose_task_id_wrong_channel_404(server_client: TestClient) -> None:
    channel = _build_channel(server_client)
    other = _build_channel_named(server_client, "orch-other")
    _http_orchestrator(server_client, channel)
    server_client.app.state.daemon_hub = _SpyHub()  # type: ignore[attr-defined]
    task = server_client.post(
        f"/api/channels/{other}/messages", json={"body": "x", "as_task": {"title": "x"}}
    ).json()["task"]
    r = server_client.post(f"/api/channels/{channel}/decompose", json={"task_id": task["id"]})
    assert r.status_code == 404, r.text


def _build_channel_named(client: TestClient, name: str) -> str:
    return client.post("/api/channels", json={"name": name, "member_ids": []}).json()["id"]


def test_t1_top_level_mention_triggers_decompose(server_client: TestClient) -> None:
    """T1：频道顶级 @Orchestrator 消息 → 转任务 + 建提案 + 注入。"""
    channel = _build_channel(server_client)
    orch = _http_orchestrator(server_client, channel)
    spy = _SpyHub()
    server_client.app.state.daemon_hub = spy  # type: ignore[attr-defined]
    r = server_client.post(
        f"/api/channels/{channel}/messages",
        json={"body": "@OrchBot 帮我拆解登录功能"},
    )
    assert r.status_code == 201, r.text
    engine: Engine = server_client.app.state.engine  # type: ignore[attr-defined]
    with engine.connect() as c:
        proposals = c.execute(select(_PROPOSAL)).mappings().all()
        # T1 消息转成的 source 任务存在。
        tasks = c.execute(
            select(_TASK).where(_TASK.c.root_message_id == r.json()["message"]["id"])
        ).all()
    assert len(proposals) == 1 and proposals[0]["status"] == ProposalStatus.DRAFTING.value
    assert len(tasks) == 1
    assert any(a == orch["member_id"] for a, *_ in spy.calls)


def test_parse_hook_sets_card_kind_on_message(server_client: TestClient) -> None:
    """提案提交消息落 card_kind=PROPOSAL/card_ref=proposal_id（HTTP 端到端）。"""
    channel = _build_channel(server_client)
    orch = _http_orchestrator(server_client, channel)
    spy = _SpyHub()
    server_client.app.state.daemon_hub = spy  # type: ignore[attr-defined]
    decompose = server_client.post(
        f"/api/channels/{channel}/decompose", json={"text": "做个登录功能"}
    )
    proposal = entities.ProposalPublic.model_validate(decompose.json())
    source_task_id = proposal.source_task_id
    engine: Engine = server_client.app.state.engine  # type: ignore[attr-defined]
    with engine.connect() as c:
        root_msg = c.execute(
            select(_TASK.c.root_message_id).where(_TASK.c.id == source_task_id)
        ).scalar_one()
        # Orchestrator 先「读」source 线程根消息（避免 freshness 扣草稿）。
        c_root = root_msg
    with engine.begin() as c:
        c.execute(
            insert(models.ReadPosition.__table__).values(
                member_id=orch["member_id"], channel_id=channel,
                last_read_message_id=c_root, last_read_at=now_iso(),
            )
        )
    body = _valid_single_task_body(source_task_id)
    r = server_client.post(
        f"/api/channels/{channel}/messages",
        json={"body": _control_msg(body), "thread_root_id": root_msg},
        headers=orch["headers"],
    )
    assert r.status_code == 201, r.text
    msg = r.json()["message"]
    assert msg["card_kind"] == "proposal" and msg["card_ref"] == proposal.id
    with engine.connect() as c:
        got = c.execute(select(_PROPOSAL).where(_PROPOSAL.c.id == proposal.id)).mappings().one()
    assert got["status"] == ProposalStatus.AWAITING_CONFIRM.value

    # 崩溃路径回归（缺陷 1）：awaiting_confirm 期重提**无效**新 control → 消息 POST 仍 201 落库
    # （非 500 回滚）、旧行 superseded、新行 rev+1 进 repairing。
    r2 = server_client.post(
        f"/api/channels/{channel}/messages",
        json={
            "body": _control_msg(_invalid_decompose_body(source_task_id)),
            "thread_root_id": root_msg,
        },
        headers=orch["headers"],
    )
    assert r2.status_code == 201, r2.text
    assert r2.json()["message"]["card_kind"] is None  # 校验未过不落提案卡
    with engine.connect() as c:
        old = c.execute(
            select(_PROPOSAL).where(_PROPOSAL.c.id == proposal.id)
        ).mappings().one()
        active = pd.active_proposal_for_source(c, source_task_id)
    assert old["status"] == ProposalStatus.SUPERSEDED.value
    assert active is not None and active["revision"] == 2
    assert active["status"] == ProposalStatus.REPAIRING.value
    # 修复直投经 daemon_hub 发给 Orchestrator（best-effort spy 记录）。
    assert any(k is InjectKind.REPAIR for _, _, k, _ in spy.calls)
