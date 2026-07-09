"""生成设计稿同款 fixtures（packages/fixtures/seed.json + timeline.json）。

事实源 = CLAUDE-DESIGN-CONTEXT.md「示例数据」节 + P1-channel-chat.html 消息流原文：
Memcyo(人类 owner)/Pat/Hank/Rin/Orchestrator、Catmem's PC、#build 番茄钟 MVP、任务 #1–#7、
07-08 04:12–04:58 的六条消息与未读线。勿换名字。

确定性：无随机无时钟，重跑输出逐字节一致（git diff 守门）。
运行：uv run python scripts/gen_fixtures.py
"""

import hashlib
import json
from pathlib import Path
from typing import Any

from coagentia_contracts import entities, ws
from coagentia_contracts.kernel import fingerprint

OUT_DIR = Path(__file__).parents[1] / "packages" / "fixtures"


def mkid(kind: str, n: int) -> str:
    """合法 Crockford ULID 形状的确定性 id：01K0 + 4 字符类别码 + 18 位序号。"""
    assert len(kind) == 4
    return f"01K0{kind}{n:018d}"


WS_ID = mkid("WKSP", 1)
PC_ID = mkid("CMPT", 1)

M_MEMCYO = mkid("MMBR", 1)
M_PAT = mkid("MMBR", 2)
M_HANK = mkid("MMBR", 3)
M_RIN = mkid("MMBR", 4)
M_ORCH = mkid("MMBR", 5)

CH_ALL = mkid("CHAN", 1)
CH_BUILD = mkid("CHAN", 2)
CH_RESEARCH = mkid("CHAN", 3)
CH_OPS = mkid("CHAN", 4)
DM_PAT = mkid("CHAN", 5)
DM_HANK = mkid("CHAN", 6)
DM_RIN = mkid("CHAN", 7)


def ts(day: int, hh: int, mm: int, ss: int = 0) -> str:
    return f"2026-07-{day:02d}T{hh:02d}:{mm:02d}:{ss:02d}.000Z"


EMPTY_BASELINE = fingerprint({"edges": [], "nodes": []})


def build_seed() -> dict[str, Any]:
    workspace = {
        "id": WS_ID, "name": "Memcyo", "slug": "memcyo",
        "setup_state": {"add_computer": True, "create_agent": True, "first_task": True},
        "created_at": ts(7, 2, 0),
    }
    computers = [{
        "id": PC_ID, "workspace_id": WS_ID, "name": "Catmem's PC",
        "os": "Windows 11", "arch": "x64", "daemon_version": "0.1.0",
        "api_key_hash": hashlib.sha256(b"coagentia-mock-api-key").hexdigest(),
        "detected_runtimes": [
            {"runtime": "claude_code", "installed": True, "models": ["opus", "sonnet"]},
            {"runtime": "codex", "installed": True, "models": ["gpt-5-codex"]},
        ],
        "status": "connected", "last_seen_at": ts(8, 5, 0), "created_at": ts(7, 2, 5),
    }]

    def member(mid: str, kind: str, name: str, role: str = "member") -> dict[str, Any]:
        return {"id": mid, "workspace_id": WS_ID, "kind": kind, "name": name,
                "role": role, "created_at": ts(7, 2, 10)}

    members = [
        member(M_MEMCYO, "human", "Memcyo", "owner"),
        member(M_PAT, "agent", "Pat"),
        member(M_HANK, "agent", "Hank"),
        member(M_RIN, "agent", "Rin"),
        member(M_ORCH, "agent", "Orchestrator"),
    ]

    def agent(mid: str, runtime: str, model: str, desc: str) -> dict[str, Any]:
        return {"member_id": mid, "computer_id": PC_ID, "runtime": runtime, "model": model,
                "description": desc, "home_path": f"~/.coagentia/agents/{mid}",
                "status": "idle", "created_by_member_id": M_MEMCYO}

    agents = [
        agent(M_PAT, "claude_code", "opus", "PM:框定需求、起草契约、验收标准。"),
        agent(M_HANK, "codex", "gpt-5-codex", "Engineer:TDD 实现,证据先行。"),
        agent(M_RIN, "claude_code", "sonnet", "Reviewer:独立验收,逐条 PASS/FAIL。"),
        agent(M_ORCH, "claude_code", "opus", "Orchestrator:任务拆解与编排(M6)。"),
    ]
    agents[1]["status"] = "busy"
    agents[3]["status"] = "offline"

    def channel(cid: str, kind: str, name: str | None, desc: str = "",
                private: bool = False, dm_key: str | None = None) -> dict[str, Any]:
        return {"id": cid, "workspace_id": WS_ID, "kind": kind, "name": name,
                "description": desc, "is_private": private, "dm_key": dm_key,
                "created_at": ts(7, 2, 15)}

    def dmkey(a: str, b: str) -> str:
        return ":".join(sorted([a, b]))

    channels = [
        channel(CH_ALL, "channel", "all", "工作区全员频道"),
        channel(CH_BUILD, "channel", "build", "番茄钟 MVP——契约、实现与评审"),
        channel(CH_RESEARCH, "channel", "research", "竞品与技术调研"),
        channel(CH_OPS, "channel", "ops-private", "运维私有频道", private=True),
        channel(DM_PAT, "dm", None, private=True, dm_key=dmkey(M_MEMCYO, M_PAT)),
        channel(DM_HANK, "dm", None, private=True, dm_key=dmkey(M_MEMCYO, M_HANK)),
        channel(DM_RIN, "dm", None, private=True, dm_key=dmkey(M_MEMCYO, M_RIN)),
    ]
    channels[1]["next_task_number"] = 8  # 任务 #1–#7 已用

    everyone = [M_MEMCYO, M_PAT, M_HANK, M_RIN, M_ORCH]
    build_crew = [M_MEMCYO, M_PAT, M_HANK, M_RIN]
    channel_members = (
        [{"channel_id": CH_ALL, "member_id": m, "joined_at": ts(7, 2, 20)} for m in everyone]
        + [{"channel_id": CH_BUILD, "member_id": m, "joined_at": ts(7, 3, 0)}
           for m in build_crew]
        + [{"channel_id": CH_RESEARCH, "member_id": m, "joined_at": ts(7, 3, 0)}
           for m in [M_MEMCYO, M_PAT, M_RIN]]
        + [{"channel_id": CH_OPS, "member_id": M_MEMCYO, "joined_at": ts(7, 3, 0)}]
        + [{"channel_id": DM_PAT, "member_id": m, "joined_at": ts(7, 3, 0)}
           for m in [M_MEMCYO, M_PAT]]
        + [{"channel_id": DM_HANK, "member_id": m, "joined_at": ts(7, 3, 0)}
           for m in [M_MEMCYO, M_HANK]]
        + [{"channel_id": DM_RIN, "member_id": m, "joined_at": ts(7, 3, 0)}
           for m in [M_MEMCYO, M_RIN]]
    )

    msgs: list[dict[str, Any]] = []
    mentions: list[dict[str, Any]] = []
    _n = [0]

    def msg(cid: str, author: str | None, body: str, at: str,
            kind: str = "user", thread_root: str | None = None) -> str:
        _n[0] += 1
        mid = mkid("MESG", _n[0])
        msgs.append({"id": mid, "workspace_id": WS_ID, "channel_id": cid,
                     "thread_root_id": thread_root, "author_member_id": author,
                     "kind": kind, "body": body, "created_at": at})
        return mid

    # ---- #all：onboarding 打招呼（FR-1.4）
    msg(CH_ALL, None, "工作区 Memcyo 已创建。", ts(7, 2, 20), kind="system")
    msg(CH_ALL, M_PAT, "大家好,我是 Pat,负责需求框定与契约起草。看了频道历史,随时叫我。",
        ts(7, 2, 30))
    msg(CH_ALL, M_HANK, "Hank 报到,工程实现,TDD 优先。", ts(7, 2, 32))
    msg(CH_ALL, M_RIN, "我是 Rin,独立验收。评审结论只认证据。", ts(7, 2, 34))

    # ---- #build 07-07:任务 #2–#7 的锚点消息(As Task 转换源)
    root2 = msg(CH_BUILD, M_PAT, "评审门:AC 全过才允许进 Done,验收人 @Rin。", ts(7, 6, 10))
    mentions.append({"message_id": root2, "member_id": M_RIN})
    root3 = msg(CH_BUILD, M_MEMCYO, "把契约骨架抽成模板,后续任务直接复用。", ts(7, 6, 20))
    root4 = msg(CH_BUILD, M_HANK, "CI 骨架:lint + test 两条流水线先立起来。", ts(7, 6, 30))
    root5 = msg(CH_BUILD, M_RIN, "部署脚本等评审门过了再动。", ts(7, 6, 40))
    root6 = msg(CH_BUILD, M_HANK, "冒烟测试脚本:构建产物起服务打一枪。", ts(7, 6, 50))
    root7 = msg(CH_BUILD, M_PAT, "语料清洗那批数据我来收尾。", ts(7, 7, 0))

    # ---- #build 07-08:P1 会话屏消息流原文
    msg(CH_BUILD, M_PAT, "契约草案写好了:goal 与 AC 一共 11 条,已发到任务线程等 review。",
        ts(8, 4, 12))
    msg(CH_BUILD, M_PAT, "补充:相位切换的边界我按 25:00 → 05:00 写进 AC-07 了。", ts(8, 4, 13))
    root1 = msg(
        CH_BUILD, M_MEMCYO,
        "@Hank 番茄钟先做单文件版本,计时循环用 `requestAnimationFrame` 而不是"
        " setInterval——这条我转成任务了。",
        ts(8, 4, 15),
    )
    mentions.append({"message_id": root1, "member_id": M_HANK})
    msg(
        CH_BUILD, M_HANK,
        "契约已锁定,开始 TDD——先给相位切换写红灯测试:\n```js\nimport { tick, PHASE } from"
        " './timer.js';\n\ntest('focus 归零后切换到 break', () => {\n  const s = tick({ phase:"
        " PHASE.FOCUS, remaining: 1 });\n  expect(s.phase).toBe(PHASE.BREAK);  // 25:00 →"
        " 05:00\n  expect(s.remaining).toBe(300);\n});\n```",
        ts(8, 4, 29),
    )
    sys1 = msg(CH_BUILD, None,
               "task #1 已 In Progress 12h 无更新——@Memcyo 检查、指派,或继续等待?",
               ts(8, 4, 45), kind="system")
    mentions.append({"message_id": sys1, "member_id": M_MEMCYO})
    rin_last = msg(CH_BUILD, M_RIN, "验收 subagent 已启动,G1–G12 逐条跑,约 5 分钟出结论。",
                   ts(8, 4, 58))

    # ---- #research:1 已读 + 3 未读(P1 侧栏 cnt=3)
    r0 = msg(CH_RESEARCH, M_RIN, "Raft 的 hands-on 报告读完了,结论放这里。", ts(8, 3, 50))
    msg(CH_RESEARCH, M_PAT, "竞品的 held draft 对用户不可见,是黑洞——我们的 G2 是差异点。",
        ts(8, 4, 40))
    msg(CH_RESEARCH, M_PAT, "freshness check 方向已被商业产品验证,照抄思路、补可见性。",
        ts(8, 4, 42))
    msg(CH_RESEARCH, M_RIN, "同意。已把两条结论写进调研笔记。", ts(8, 4, 50))

    def task(n: int, root: str, title: str, status: str, owner: str | None,
             creator: str, changed: str, created: str) -> dict[str, Any]:
        return {"id": mkid("TASK", n), "workspace_id": WS_ID, "channel_id": CH_BUILD,
                "number": n, "root_message_id": root, "title": title, "status": status,
                "owner_member_id": owner, "level": "l2" if n in (1, 2) else "l1",
                "created_by_member_id": creator, "status_changed_at": changed,
                "created_at": created}

    tasks = [
        task(1, root1, "单文件番茄钟", "in_progress", M_HANK, M_MEMCYO, ts(8, 4, 20), ts(8, 4, 15)),
        task(2, root2, "评审门", "in_review", M_RIN, M_PAT, ts(8, 4, 30), ts(7, 6, 10)),
        task(3, root3, "契约模板抽取", "todo", M_PAT, M_MEMCYO, ts(7, 6, 20), ts(7, 6, 20)),
        task(4, root4, "CI 骨架", "done", M_HANK, M_HANK, ts(7, 9, 0), ts(7, 6, 30)),
        task(5, root5, "部署脚本", "todo", M_RIN, M_RIN, ts(7, 6, 40), ts(7, 6, 40)),
        task(6, root6, "冒烟测试脚本", "done", M_HANK, M_HANK, ts(7, 10, 0), ts(7, 6, 50)),
        task(7, root7, "语料清洗", "closed", M_PAT, M_PAT, ts(7, 11, 0), ts(7, 7, 0)),
    ]

    canvases = [
        {"id": mkid("CNVS", i + 1), "workspace_id": WS_ID, "channel_id": cid,
         "baseline_version": 0, "baseline_hash": EMPTY_BASELINE, "updated_at": ts(7, 2, 15)}
        for i, cid in enumerate([CH_ALL, CH_BUILD, CH_RESEARCH, CH_OPS])
    ]

    def rp(member: str, cid: str, last: str, at: str) -> dict[str, Any]:
        return {"member_id": member, "channel_id": cid,
                "last_read_message_id": last, "last_read_at": at}

    read_positions = [
        rp(M_MEMCYO, CH_BUILD, sys1, ts(8, 4, 50)),  # 未读线在 Rin 04:58 之前
        rp(M_MEMCYO, CH_RESEARCH, r0, ts(8, 4, 0)),  # 3 条未读
        rp(M_MEMCYO, CH_ALL, msgs[3]["id"], ts(8, 4, 0)),
        rp(M_PAT, CH_BUILD, rin_last, ts(8, 4, 59)),
        rp(M_HANK, CH_BUILD, sys1, ts(8, 4, 46)),
        rp(M_RIN, CH_BUILD, rin_last, ts(8, 4, 58)),
    ]

    token_usage_events = [
        {"id": mkid("TKNE", 1), "workspace_id": WS_ID, "agent_member_id": M_HANK,
         "task_id": tasks[0]["id"], "channel_id": CH_BUILD, "input_tokens": 1800,
         "output_tokens": 500, "cache_read_tokens": 350, "cache_write_tokens": 80,
         "source_session": "sess-hank-001", "reported_at": ts(8, 4, 30)},
        {"id": mkid("TKNE", 2), "workspace_id": WS_ID, "agent_member_id": M_HANK,
         "task_id": tasks[0]["id"], "channel_id": CH_BUILD, "input_tokens": 700,
         "output_tokens": 200, "cache_read_tokens": 120, "cache_write_tokens": 30,
         "source_session": "sess-hank-001", "reported_at": ts(8, 4, 44)},
    ]

    presence = [
        {"member_id": M_MEMCYO, "kind": "human", "status": "online", "busy_detail": None},
        {"member_id": M_PAT, "kind": "agent", "status": "idle", "busy_detail": None},
        {"member_id": M_HANK, "kind": "agent", "status": "busy",
         "busy_detail": "Running tests…"},
        {"member_id": M_RIN, "kind": "agent", "status": "idle", "busy_detail": None},
        {"member_id": M_ORCH, "kind": "agent", "status": "offline", "busy_detail": None},
    ]

    return {
        "workspace": workspace, "computers": computers, "members": members,
        "agents": agents, "channels": channels, "channel_members": channel_members,
        "messages": msgs, "message_mentions": mentions, "tasks": tasks,
        "canvases": canvases, "read_positions": read_positions,
        "token_usage_events": token_usage_events, "presence": presence,
    }


def build_timeline(seed: dict[str, Any]) -> list[dict[str, Any]]:
    """脚本化 WS 事件时间线(mock 回放,驱动 P1 无刷新更新——NFR1 实证)。"""
    task1 = next(t for t in seed["tasks"] if t["number"] == 1)
    task1_review = {**task1, "status": "in_review", "status_changed_at": ts(8, 5, 3)}
    rin_msg = {
        "id": mkid("MESG", 900), "workspace_id": WS_ID, "channel_id": CH_BUILD,
        "thread_root_id": None, "author_member_id": M_RIN, "kind": "user",
        "body": "验收完成:G1–G12 = 18/18 全 PASS。建议 task #1 置 In Review,证据在任务线程。",
        "created_at": ts(8, 5, 3),
    }
    return [
        {"delay_ms": 900, "type": "agent.activity", "channel_id": None,
         "data": {"member_id": M_RIN, "detail": "Subagent started"}},
        {"delay_ms": 2000, "type": "message.created", "channel_id": CH_BUILD,
         "data": {"message": rin_msg}},
        {"delay_ms": 2600, "type": "task.updated", "channel_id": CH_BUILD,
         "data": {"task": task1_review,
                  "change": {"kind": "status_change", "from_status": "in_progress",
                             "to_status": "in_review", "actor_member_id": M_RIN}}},
        {"delay_ms": 3200, "type": "presence.changed", "channel_id": None,
         "data": {"member_id": M_HANK, "kind": "agent", "status": "idle"}},
        {"delay_ms": 3600, "type": "token_usage.reported", "channel_id": CH_BUILD,
         "data": {"agent_member_id": M_HANK, "task_id": task1["id"],
                  "totals": {"input_tokens": 2500, "output_tokens": 700,
                             "cache_read_tokens": 470, "cache_write_tokens": 110}}},
    ]


def validate(seed: dict[str, Any], timeline: list[dict[str, Any]]) -> None:
    """写盘前全量过契约模型——不合形状的 fixtures 根本生成不出来。"""
    entities.WorkspaceRow.model_validate(seed["workspace"])
    plans: list[tuple[str, type]] = [
        ("computers", entities.ComputerRow), ("members", entities.MemberRow),
        ("agents", entities.AgentRow), ("channels", entities.ChannelRow),
        ("channel_members", entities.ChannelMemberRow), ("messages", entities.MessageRow),
        ("message_mentions", entities.MessageMentionRow), ("tasks", entities.TaskRow),
        ("canvases", entities.CanvasRow), ("read_positions", entities.ReadPositionRow),
        ("token_usage_events", entities.TokenUsageEventRow),
    ]
    for key, model in plans:
        for row in seed[key]:
            model.model_validate(row)
    for entry in timeline:
        payload_model = ws.EVENT_PAYLOADS[ws.EventType(entry["type"])]
        payload_model.model_validate(entry["data"])


def main() -> None:
    seed = build_seed()
    timeline = build_timeline(seed)
    validate(seed, timeline)
    (OUT_DIR / "seed.json").write_text(
        json.dumps(seed, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (OUT_DIR / "timeline.json").write_text(
        json.dumps(timeline, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"seed: {sum(len(v) if isinstance(v, list) else 1 for v in seed.values())} rows, "
          f"timeline: {len(timeline)} events -> {OUT_DIR}")


if __name__ == "__main__":
    main()
