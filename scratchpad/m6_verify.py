"""J12 实机 verify（= PRD M6 出口）：真 uvicorn + 真 websockets daemon-sim（真 git.py）+ 真 scratch
仓库 + REST 扮演 Orchestrator/工人（FakeAdapter 桩掉 LLM turn，全链走生产代码）。

场景（M6-HANDOFF §9b #21 + 拆解设计 §16 A1–A8）：
  S1 拆解全链（A1/A3/A4）：一句话需求 decompose → Orch 发 <control> 提案（含显式 merge+check 系统
     节点）→ 提案卡+awaiting → 人工调整（删节点+改标题）确认 → 落地（含汇总节点）→ 两 writes_code
     任务并行 worktree 交付 → merge --no-ff 成功 → check 绿 → 汇总解锁；全程 WS 事件采集（无刷新）。
  S2 冲突派回：两任务同行冲突 → conflicted → 冲突任务派回 → 解决 → retry → 合并成功。
  S3 修复循环（A2）：无效提案 → repairing → 改好通过；连续三败 → failed + @人类升级。
  S4 A5 崩溃重放：确认后落地中 taskkill server → 重启 → 启动扫描补齐，节点无重复无缺失、
     「已落地」恰一条。
  S5 delta + O9（A6 + §9b #18）：Agent 直接建节点 403 O9 → delta 提案 → 部分接受（removed_ops）→
     落地；base 过期 → confirm 409 DELTA_BASE_MISMATCH + 提案 failed。
  S6 single_task（A7）：单节点无汇总无 merge。
  S7 直落（A8）：direct 频道无确认停顿，confirmed_by=auto(channel-policy)。

用法：uv run python scratchpad/m6_verify.py [--keep]
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m6a_harness as H  # noqa: E402
from coagentia_contracts.kernel.fingerprint import fingerprint  # noqa: E402
from coagentia_server.canvas.service import snapshot  # noqa: E402
from coagentia_server.db import models  # noqa: E402
from coagentia_server.db.engine import sqlite_url  # noqa: E402
from coagentia_server.ledger.service import now_iso  # noqa: E402
from sqlalchemy import func, insert, select  # noqa: E402

# 空画布规范快照指纹（契约 A §6：空画布 = 空快照指纹，非空串——CanvasPublic 校验 64-hex）。
EMPTY_BASELINE_HASH = fingerprint(snapshot([], []))


def _free_port() -> int:
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


PORT = _free_port()
SERVER_URL = f"http://127.0.0.1:{PORT}"
API = f"{SERVER_URL}/api"
WS_URL = f"ws://127.0.0.1:{PORT}/api/ws"

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f"  — {detail}" if detail else ""), flush=True)


async def poll(fn, timeout: float = 40.0, interval: float = 0.4):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = await fn()
        if last:
            return last
        await asyncio.sleep(interval)
    return last


class Rest:
    """REST 驱动器：默认 Owner 身份；as_agent(mid) 携 X-Acting-Member + Bearer（契约 B §2）。"""

    def __init__(self, hc: httpx.AsyncClient) -> None:
        self.hc = hc

    def _headers(self, agent: str | None) -> dict[str, str]:
        if agent is None:
            return {}
        return {"X-Acting-Member": agent, "Authorization": f"Bearer {H.API_KEY}"}

    async def post(self, path: str, body: dict | None = None, expect: int | None = None,
                   agent: str | None = None):
        r = None
        for _ in range(6):  # 瞬时 500（残留 DB 锁）退避重试
            r = await self.hc.post(f"{API}{path}", json=body, headers=self._headers(agent))
            if r.status_code != 500:
                break
            await asyncio.sleep(0.5)
        if expect is not None and r.status_code != expect:
            raise AssertionError(f"POST {path} → {r.status_code} (want {expect}): {r.text}")
        return r

    async def get(self, path: str, expect: int | None = None):
        r = None
        for _ in range(6):
            r = await self.hc.get(f"{API}{path}")
            if r.status_code != 500:
                break
            await asyncio.sleep(0.5)
        if expect is not None and r.status_code != expect:
            raise AssertionError(f"GET {path} → {r.status_code} (want {expect}): {r.text}")
        return r


# ---------------------------------------------------------------- seed（M6b 形态）


def seed_m6b(engine) -> dict:
    """workspace + computer + owner + Orch(role_template_key)/Ada/Ben + 4 频道（orchestrated/
    conflict/direct/repair 各带画布；direct 频道 decomp_mode='direct'）。"""
    ids: dict = {"agents": {}, "channels": {}}
    with engine.begin() as c:
        c.execute(insert(models.Workspace.__table__).values(
            id=H.WS_ID, name="M6", slug="m6", created_at=now_iso()))
        c.execute(insert(models.Computer.__table__).values(
            id=H.COMP_ID, workspace_id=H.WS_ID, name="ProbeRig",
            api_key_hash=H.KEY_HASH, status="offline", created_at=now_iso()))
        c.execute(insert(models.Member.__table__).values(
            id=H.OWNER_ID, workspace_id=H.WS_ID, kind="human", name="Owner",
            role="owner", created_at=now_iso()))
        for name, role_key in (("Orch", "orchestrator"), ("Ada", None), ("Ben", None)):
            mid = H._nid()
            c.execute(insert(models.Member.__table__).values(
                id=mid, workspace_id=H.WS_ID, kind="agent", name=name,
                role="member", created_at=now_iso()))
            c.execute(insert(models.Agent.__table__).values(
                member_id=mid, computer_id=H.COMP_ID, runtime="claude_code",
                model="m", description="", home_path=f"~/.coagentia/agents/{mid}",
                status="offline", created_by_member_id=H.OWNER_ID,
                role_template_key=role_key))
            ids["agents"][name] = mid
        for cname, mode in (("orchestrated", "draft"), ("conflict", "draft"),
                            ("direct", "direct"), ("repair", "draft")):
            cid = H._nid()
            c.execute(insert(models.Channel.__table__).values(
                id=cid, workspace_id=H.WS_ID, kind="channel", name=cname,
                dm_key=None, decomp_mode=mode, decomp_node_limit=12, created_at=now_iso()))
            c.execute(insert(models.ChannelMember.__table__).values(
                channel_id=cid, member_id=H.OWNER_ID, joined_at=now_iso()))
            for mid in ids["agents"].values():
                c.execute(insert(models.ChannelMember.__table__).values(
                    channel_id=cid, member_id=mid, joined_at=now_iso()))
            canvas_id = H._nid()
            c.execute(insert(models.Canvas.__table__).values(
                id=canvas_id, workspace_id=H.WS_ID, channel_id=cid,
                baseline_hash=EMPTY_BASELINE_HASH, updated_at=now_iso()))
            ids["channels"][cname] = {"channel_id": cid, "canvas_id": canvas_id}
    return ids


# ---------------------------------------------------------------- 提案体构造


def plan(goal: str) -> dict:
    return {
        "version": "coagentia.task-plan.v1",
        "goal": goal,
        "acceptance_criteria": [
            {"id": "AC1", "statement": f"{goal}——文件落盘且提交可见",
             "verify_by": "inspect", "verify_ref": "git log"},
        ],
    }


def code_node(temp_id: str, title: str, project: str, owner: str | None) -> dict:
    n = {"temp_id": temp_id, "title": title, "kind": "agent", "writes_code": True,
         "project": project, "task_plan": plan(title)}
    if owner:
        n["suggested_owner"] = owner
    return n


def control_msg(body: dict, prose: str = "拆解说明见控制块。") -> str:
    return f"{prose}\n<control>{json.dumps(body, ensure_ascii=False)}</control>"


def full_proposal(source_task_id: str, project_id: str, ada: str, ben: str) -> dict:
    """S1 提案：N1/N2 writes_code 并行 → M(merge) → C(check)；N3 文档节点留给人工删除。"""
    return {
        "version": "coagentia.decomposition.v1",
        "source": source_task_id,
        "mode": "decompose",
        "summary": "贪吃蛇小游戏：核心逻辑与界面并行实现，合并后跑检查。",
        "merge_plan": "两分支各自提交后由 merge 系统节点按 DAG 序 --no-ff 合并。",
        "nodes": [
            code_node("N1", "实现贪吃蛇核心逻辑", project_id, ada),
            code_node("N2", "实现贪吃蛇界面层", project_id, ben),
            {"temp_id": "N3", "title": "编写玩法说明文档", "kind": "agent",
             "task_plan": plan("玩法说明文档")},
            {"temp_id": "M", "title": "合并分支", "kind": "system", "system_action": "merge"},
            {"temp_id": "C", "title": "自检命令", "kind": "system", "system_action": "check",
             "command": "git --version"},
        ],
        "edges": [
            {"from": "N1", "to": "M"}, {"from": "N2", "to": "M"}, {"from": "M", "to": "C"},
            {"from": "N1", "to": "N3"},
        ],
    }


# ---------------------------------------------------------------- WS 事件采集（无刷新证据）


class WsProbe:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        import websockets

        async def _run() -> None:
            async with websockets.connect(WS_URL, ping_interval=20) as ws:
                async for raw in ws:
                    try:
                        self.events.append(json.loads(raw))
                    except ValueError:
                        pass

        self._task = asyncio.create_task(_run())
        await asyncio.sleep(0.5)

    def count(self, event_type: str) -> int:
        return sum(1 for e in self.events if e.get("type") == event_type)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with __import__("contextlib").suppress(BaseException):
                await self._task


# ---------------------------------------------------------------- 通用小步


async def decompose(rest: Rest, channel_id: str, text: str) -> dict:
    r = await rest.post(f"/channels/{channel_id}/decompose", {"text": text}, expect=202)
    return r.json()


async def proposal_status(rest: Rest, pid: str) -> str:
    return (await rest.get(f"/proposals/{pid}", expect=200)).json()["status"]


async def wait_status(rest: Rest, pid: str, want: str, timeout: float = 30.0) -> bool:
    async def _s():
        return (await proposal_status(rest, pid)) == want
    return bool(await poll(_s, timeout=timeout))


async def source_thread_root(rest: Rest, pid: str) -> tuple[str, str]:
    p = (await rest.get(f"/proposals/{pid}", expect=200)).json()
    t = (await rest.get(f"/tasks/{p['source_task_id']}", expect=200)).json()["task"]
    return p["source_task_id"], t["root_message_id"]


def _mark_agent_read(channel_id: str, agent_mid: str) -> None:
    """推进 agent 在本频道的 read_position 至频道最新消息 id（含线程内）——复刻真实语义：
    Orchestrator 收到上下文注入即「读」了 source 线程摘要（注入体含 [首条]/[讨论]），故其回复的
    提案消息过 freshness 门；harness REST 直发不经适配器读，故显式对齐（裁决 #12 held 门本身在
    S5 的「人类插入节点后 delta」等路径仍受检——此处只对提案作者补其已读的注入线程）。"""
    _READ = models.tbl(models.ReadPosition)
    _MSG_T = models.tbl(models.Message)
    engine = H.probe_engine(os.environ["M6A_DB_URL"])
    try:
        with engine.begin() as c:
            latest = c.execute(
                select(func.max(_MSG_T.c.id)).where(_MSG_T.c.channel_id == channel_id)
            ).scalar()
            if latest is None:
                return
            existing = c.execute(
                select(_READ.c.member_id).where(
                    _READ.c.member_id == agent_mid, _READ.c.channel_id == channel_id)
            ).first()
            if existing is None:
                c.execute(insert(_READ).values(
                    member_id=agent_mid, channel_id=channel_id,
                    last_read_message_id=latest, last_read_at=now_iso()))
            else:
                c.execute(models.tbl(models.ReadPosition).update().where(
                    _READ.c.member_id == agent_mid, _READ.c.channel_id == channel_id
                ).values(last_read_message_id=latest, last_read_at=now_iso()))
    finally:
        engine.dispose()


async def post_control(rest: Rest, channel_id: str, thread_root: str, agent_mid: str,
                       body: dict, prose: str = "提案如下。") -> dict:
    _mark_agent_read(channel_id, agent_mid)  # 提案作者已读注入线程（freshness 门语义对齐）
    r = await rest.post(f"/channels/{channel_id}/messages",
                        {"body": control_msg(body, prose), "thread_root_id": thread_root},
                        expect=201, agent=agent_mid)
    return r.json()["message"]


async def canvas_state(rest: Rest, channel_id: str) -> dict:
    """CanvasDetail = {canvas: CanvasPublic, nodes, edges}——扁平化 baseline 到顶层便于调用。"""
    d = (await rest.get(f"/channels/{channel_id}/canvas", expect=200)).json()
    return {
        "baseline_version": d["canvas"]["baseline_version"],
        "baseline_hash": d["canvas"]["baseline_hash"],
        "nodes": d["nodes"], "edges": d["edges"],
    }


async def confirm(rest: Rest, pid: str, canvas: dict, proposal: dict,
                  adjustments: list | None = None, removed_ops: list[int] | None = None):
    return await rest.post(f"/proposals/{pid}/confirm", {
        "expected": {
            "proposal_hash": proposal["proposal_hash"],
            "baseline_version": canvas["baseline_version"],
            "baseline_hash": canvas["baseline_hash"],
        },
        "adjustments": adjustments or [],
        "removed_ops": removed_ops or [],
    })


async def fresh_confirm(rest: Rest, channel_id: str, pid: str,
                        adjustments: list | None = None,
                        removed_ops: list[int] | None = None):
    cv = await canvas_state(rest, channel_id)
    p = (await rest.get(f"/proposals/{pid}", expect=200)).json()
    return await confirm(rest, pid, cv, p, adjustments, removed_ops)


async def drive_done(rest: Rest, task_id: str, agent_mid: str) -> None:
    """claim→in_progress→handoff→in_review→done（承 M6a 体例；agent 身份驱动）。"""
    r = await rest.post(f"/tasks/{task_id}/claim", agent=agent_mid)
    if r.status_code not in (200, 409):
        raise AssertionError(f"claim {task_id} → {r.status_code}: {r.text}")
    t = (await rest.get(f"/tasks/{task_id}")).json()["task"]
    if t["status"] == "todo":
        await rest.post(f"/tasks/{task_id}/status", {"to": "in_progress"}, expect=200,
                        agent=agent_mid)
    handoff = {
        "kind": "task_handoff",
        "body": {
            "version": "coagentia.task-handoff.v1",
            "from_member": agent_mid, "to_member": H.OWNER_ID,
            "deliverables": [{"path": "D:/x", "kind": "file"}],
            "evidence": [{"type": "test", "ref": "probe", "conclusion": "green"}],
            "open_risks": [], "verify_plan": "re-run", "review_verdict": "pass",
        },
    }
    await rest.post(f"/tasks/{task_id}/contracts", handoff, expect=201, agent=agent_mid)
    await rest.post(f"/tasks/{task_id}/status", {"to": "in_review"}, expect=200, agent=agent_mid)
    await rest.post(f"/tasks/{task_id}/status", {"to": "done"}, expect=200)


def worktree_commit(wt: Path, fname: str, content: str, msg: str) -> None:
    (wt / fname).write_text(content, encoding="utf-8")
    H.git(wt, "add", "--", fname)
    H.git(wt, "commit", "-m", msg)


async def nodes_by_kind(rest: Rest, channel_id: str) -> dict:
    cv = await canvas_state(rest, channel_id)
    out = {"agent": [], "merge": [], "check": [], "summary": []}
    for n in cv["nodes"]:
        if n.get("kind") == "system":
            out[n["system_action"]].append(n)
        elif n.get("is_summary"):
            out["summary"].append(n)
        else:
            out["agent"].append(n)
    return out


async def task_of_node(rest: Rest, node: dict) -> dict:
    return (await rest.get(f"/tasks/{node['task_id']}", expect=200)).json()["task"]


async def wait_worktree(rest: Rest, task_id: str, timeout: float = 40.0) -> dict | None:
    async def _w():
        t = (await rest.get(f"/tasks/{task_id}", expect=200)).json()
        wt = t.get("worktree")  # TaskDetail.worktree 顶层派生字段（A v1.0.7 ④）
        return wt if wt and wt.get("status") == "active" and wt.get("path") else None
    return await poll(_w, timeout=timeout)


async def wait_node_status(rest: Rest, channel_id: str, node_id: str, want: str,
                           timeout: float = 60.0) -> bool:
    async def _s():
        cv = await canvas_state(rest, channel_id)
        for n in cv["nodes"]:
            if n["id"] == node_id:
                return n.get("system_status") == want
        return False
    return bool(await poll(_s, timeout=timeout))


async def thread_bodies(rest: Rest, channel_id: str, root_id: str) -> list[str]:
    r = await rest.get(f"/channels/{channel_id}/messages?limit=200", expect=200)
    items = r.json()["items"]
    return [m["body"] for m in items
            if m.get("thread_root_id") == root_id or m.get("id") == root_id]


# ---------------------------------------------------------------- S1 拆解全链


async def s1_full_chain(rest: Rest, ids: dict, project_id: str, repo: Path,
                        ws_probe: WsProbe) -> dict:
    print("\n== S1 拆解全链（A1/A3/A4）==", flush=True)
    ch = ids["channels"]["orchestrated"]["channel_id"]
    orch, ada, ben = (ids["agents"][k] for k in ("Orch", "Ada", "Ben"))

    p = await decompose(rest, ch, "做一个贪吃蛇小游戏：核心逻辑与界面并行，合并后自检。")
    check("S1.1 decompose 202 + drafting 提案（A1 触发）", p["status"] == "drafting", p["id"])
    src_task, root = await source_thread_root(rest, p["id"])

    body = full_proposal(src_task, project_id, ada, ben)
    msg = await post_control(rest, ch, root, orch, body)
    check("S1.2 Orch <control> 消息即提案卡（card_kind=proposal）",
          msg.get("card_kind") == "proposal" and msg.get("card_ref") == p["id"])
    ok = await wait_status(rest, p["id"], "awaiting_confirm")
    check("S1.3 校验通过 → awaiting_confirm（draft.presented）", ok)

    # A3 人工调整：删 N3 文档节点 + 改 N1 标题。
    adjustments = [
        {"op": "remove_node", "temp_id": "N3"},
        {"op": "edit_node", "temp_id": "N1",
         "changes": {"title": "实现贪吃蛇核心逻辑（含移动与碰撞）"}},
    ]
    r = await fresh_confirm(rest, ch, p["id"], adjustments=adjustments)
    check("S1.4 调整确认 202（remove_node+edit_node 服务端重验通过）", r.status_code == 202,
          f"{r.status_code}")
    ok = await wait_status(rest, p["id"], "landed")
    check("S1.5 异步落地完成 → landed", ok)

    pp = (await rest.get(f"/proposals/{p['id']}", expect=200)).json()
    check("S1.6 账本落账：landed_hash 存在且 ≠ proposal_hash（有调整）",
          bool(pp.get("landed_hash")) and pp["landed_hash"] != pp["proposal_hash"])

    kinds = await nodes_by_kind(rest, ch)
    check("S1.7 落地结构与调整一致：2 agent + merge + check + 汇总（N3 已删）",
          len(kinds["agent"]) == 2 and len(kinds["merge"]) == 1
          and len(kinds["check"]) == 1 and len(kinds["summary"]) == 1,
          f"agent={len(kinds['agent'])} merge={len(kinds['merge'])} "
          f"check={len(kinds['check'])} summary={len(kinds['summary'])}")

    titles = []
    for n in kinds["agent"]:
        titles.append((await task_of_node(rest, n))["title"])
    check("S1.8 edit_node 标题调整落地", any("含移动与碰撞" in t for t in titles), str(titles))

    landed_msgs = [b for b in await thread_bodies(rest, ch, root) if "拆解已落地" in b]
    check("S1.9 「已落地」消息恰一条（:done 后发）", len(landed_msgs) == 1, f"{len(landed_msgs)}")

    # A4：无上游 writes_code 节点 worktree 就位；下游 blocked。
    t1 = await task_of_node(rest, kinds["agent"][0])
    t2 = await task_of_node(rest, kinds["agent"][1])
    wt1 = await wait_worktree(rest, t1["id"])
    wt2 = await wait_worktree(rest, t2["id"])
    check("S1.10 两 writes_code 任务 worktree 各自就位（A4）",
          wt1 is not None and wt2 is not None)
    check("S1.11 worktree 目录真实存在于磁盘",
          wt1 and Path(wt1["path"]).is_dir() and wt2 and Path(wt2["path"]).is_dir())

    # 交付：两 worktree 各写不同文件 → done → merge --no-ff → check 绿 → 汇总解锁。
    worktree_commit(Path(wt1["path"]), "core.py", "SNAKE=1\n", "core")
    worktree_commit(Path(wt2["path"]), "ui.py", "UI=1\n", "ui")
    await drive_done(rest, t1["id"], t1.get("owner_member_id") or ada)
    await drive_done(rest, t2["id"], t2.get("owner_member_id") or ben)

    merge_node = kinds["merge"][0]
    check_node = kinds["check"][0]
    ok = await wait_node_status(rest, ch, merge_node["id"], "success", timeout=90.0)
    check("S1.12 merge 系统节点自动执行 → success（合并成功终态）", ok)
    log = H.git(repo, "log", "--oneline", "--merges").stdout
    check("S1.13 主仓 --no-ff merge 提交 ≥2（两分支各一）", len(log.strip().splitlines()) >= 2,
          log.strip().replace("\n", " | "))
    ok = await wait_node_status(rest, ch, check_node["id"], "success", timeout=90.0)
    check("S1.14 check 节点绿（command 在主工作区跑）", ok)

    st = await task_of_node(rest, kinds["summary"][0])
    check("S1.15 汇总任务存在且 owner=Orchestrator", st.get("owner_member_id") == orch)

    check("S1.16 WS 无刷新证据：draft.presented/landing.completed/node_added/worktree.updated",
          ws_probe.count("draft.presented") >= 1 and ws_probe.count("landing.completed") >= 1
          and ws_probe.count("canvas.node_added") >= 5
          and ws_probe.count("worktree.updated") >= 2,
          f"presented={ws_probe.count('draft.presented')} "
          f"completed={ws_probe.count('landing.completed')} "
          f"node_added={ws_probe.count('canvas.node_added')} "
          f"wt={ws_probe.count('worktree.updated')}")
    return {"channel_id": ch, "proposal": pp, "root": root, "src_task": src_task,
            "kinds": kinds}


# ---------------------------------------------------------------- S2 冲突派回


async def s2_conflict(rest: Rest, ids: dict, project_id: str, repo: Path) -> None:
    print("\n== S2 冲突派回 ==", flush=True)
    ch = ids["channels"]["conflict"]["channel_id"]
    orch, ada, ben = (ids["agents"][k] for k in ("Orch", "Ada", "Ben"))

    p = await decompose(rest, ch, "两位工程师并行修改同一配置文件后合并。")
    src_task, root = await source_thread_root(rest, p["id"])
    body = {
        "version": "coagentia.decomposition.v1",
        "source": src_task, "mode": "decompose",
        "summary": "两分支同文件修改，合并系统节点收口（预期一次冲突派回）。",
        "merge_plan": "DAG 序 --no-ff；冲突自动建任务派回。",
        "nodes": [
            code_node("A", "分支甲改 conflict.txt", project_id, ada),
            code_node("B", "分支乙改 conflict.txt", project_id, ben),
            {"temp_id": "M", "title": "合并分支", "kind": "system", "system_action": "merge"},
        ],
        "edges": [{"from": "A", "to": "M"}, {"from": "B", "to": "M"}],
    }
    await post_control(rest, ch, root, orch, body)
    await wait_status(rest, p["id"], "awaiting_confirm")
    r = await fresh_confirm(rest, ch, p["id"])
    check("S2.1 无调整确认 202", r.status_code == 202, f"{r.status_code}")
    await wait_status(rest, p["id"], "landed")

    kinds = await nodes_by_kind(rest, ch)
    known = {n["id"] for n in kinds["agent"]}
    ta = await task_of_node(rest, kinds["agent"][0])
    tb = await task_of_node(rest, kinds["agent"][1])
    wta = await wait_worktree(rest, ta["id"])
    wtb = await wait_worktree(rest, tb["id"])
    check("S2.2 两 worktree 就位", wta is not None and wtb is not None)

    worktree_commit(Path(wta["path"]), "conflict.txt", "from-A\n", "A edit")
    worktree_commit(Path(wtb["path"]), "conflict.txt", "from-B\n", "B edit")
    await drive_done(rest, ta["id"], ada)
    await drive_done(rest, tb["id"], ben)

    merge_node = kinds["merge"][0]
    ok = await wait_node_status(rest, ch, merge_node["id"], "failed", timeout=90.0)
    check("S2.3 第二分支合并冲突 → failed（冲突态,仅 failed 可 retry）", ok)

    async def _new_node():
        cv = await canvas_state(rest, ch)
        for n in cv["nodes"]:
            if n.get("kind") == "agent" and n["id"] not in known and not n.get("is_summary"):
                return n
        return None
    fix_node = await poll(_new_node, timeout=40.0)
    check("S2.4 冲突任务自动建卡派回（task.created + 画布节点）", fix_node is not None)

    fix_task = await task_of_node(rest, fix_node)
    # 解决：冲突 worktree merge main → 修文件 → commit。
    loser = None
    for wt in (wta, wtb):
        r2 = H.git(Path(wt["path"]), "merge", "main", check=False)
        if r2.returncode != 0:
            loser = Path(wt["path"])
            (loser / "conflict.txt").write_text("from-A\nfrom-B\n", encoding="utf-8")
            H.git(loser, "add", "--", "conflict.txt")
            H.git(loser, "commit", "-m", "resolve conflict")
            break
        H.git(Path(wt["path"]), "merge", "--abort", check=False)
    check("S2.5 冲突分支手工解决并提交", loser is not None)

    await drive_done(rest, fix_task["id"], fix_task.get("owner_member_id") or ada)
    await rest.post(f"/canvas-nodes/{merge_node['id']}/retry", expect=202)
    ok = await wait_node_status(rest, ch, merge_node["id"], "success", timeout=90.0)
    check("S2.6 retry 后合并成功 → success（merge_commit 持久）", ok)


# ---------------------------------------------------------------- S3 修复循环


async def s3_repair(rest: Rest, ids: dict) -> None:
    print("\n== S3 修复循环（A2）==", flush=True)
    ch = ids["channels"]["repair"]["channel_id"]
    orch = ids["agents"]["Orch"]

    # 修好路径：无效（边引用未知节点）→ repairing → 改好 → awaiting。
    p = await decompose(rest, ch, "写一份部署手册。")
    src_task, root = await source_thread_root(rest, p["id"])
    bad = {
        "version": "coagentia.decomposition.v1", "source": src_task, "mode": "decompose",
        "summary": "部署手册两步：初稿与校对。",
        "nodes": [
            {"temp_id": "W1", "title": "写初稿", "kind": "agent", "task_plan": plan("初稿")},
            {"temp_id": "W2", "title": "校对", "kind": "agent", "task_plan": plan("校对")},
        ],
        "edges": [{"from": "W1", "to": "W9"}],  # V8：未知节点
    }
    await post_control(rest, ch, root, orch, bad)
    ok = await wait_status(rest, p["id"], "repairing")
    check("S3.1 无效提案 → repairing（S1 直投修复提示，不进频道流）", ok)

    good = dict(bad, edges=[{"from": "W1", "to": "W2"}])
    await post_control(rest, ch, root, orch, good, prose="按错误清单修复重提。")
    ok = await wait_status(rest, p["id"], "awaiting_confirm")
    check("S3.2 修复重提 → awaiting_confirm（修复循环自动改好）", ok)
    await rest.post(f"/proposals/{p['id']}/reject", {"reason": "probe 清场"}, expect=200)

    # 穷尽路径：初提失败 →1/2→2/2→ 第三败 failed + @人类。
    p2 = await decompose(rest, ch, "再写一份运维手册。")
    src2, root2 = await source_thread_root(rest, p2["id"])
    bad2 = dict(bad, source=src2)
    await post_control(rest, ch, root2, orch, bad2)
    await wait_status(rest, p2["id"], "repairing")
    await post_control(rest, ch, root2, orch, bad2, prose="重提 1")
    await asyncio.sleep(1.0)
    await post_control(rest, ch, root2, orch, bad2, prose="重提 2")
    ok = await wait_status(rest, p2["id"], "failed")
    check("S3.3 连续三败 → failed（配额 2 轮穷尽）", ok)
    bodies = await thread_bodies(rest, ch, root2)
    esc = [b for b in bodies if "升级人类" in b]
    check("S3.4 升级消息进线程 @人类（附错误清单）", len(esc) >= 1,
          f"threads={len(bodies)}")


# ---------------------------------------------------------------- S4 A5 崩溃重放


async def s4_crash_replay(rest: Rest, ids: dict, server_proc, restart_server) -> None:
    print("\n== S4 A5 崩溃重放 ==", flush=True)
    ch = ids["channels"]["repair"]["channel_id"]
    orch = ids["agents"]["Orch"]

    p = await decompose(rest, ch, "十步文档流水线（崩溃重放专用）。")
    src_task, root = await source_thread_root(rest, p["id"])
    nodes = [{"temp_id": f"K{i}", "title": f"步骤 {i}", "kind": "agent",
              "task_plan": plan(f"步骤 {i}")} for i in range(1, 11)]
    edges = [{"from": f"K{i}", "to": f"K{i+1}"} for i in range(1, 10)]
    body = {"version": "coagentia.decomposition.v1", "source": src_task,
            "mode": "decompose", "summary": "十节点链条，用于落地中途 kill 的恢复验证。",
            "nodes": nodes, "edges": edges}
    await post_control(rest, ch, root, orch, body)
    await wait_status(rest, p["id"], "awaiting_confirm")
    r = await fresh_confirm(rest, ch, p["id"])
    assert r.status_code == 202, r.text
    batch_id = r.json()["batch"]["id"]

    # 等第一个 op 落账即 kill（落地进行中）。
    pengine = H.probe_engine(os.environ["M6A_DB_URL"])
    ledger = models.LedgerEntry.__table__
    def _op_count() -> int:
        with pengine.connect() as c:
            return c.execute(
                select(func.count()).select_from(ledger).where(
                    ledger.c.batch_id == batch_id)
            ).scalar() or 0
    deadline = time.monotonic() + 30
    n0 = 0
    while time.monotonic() < deadline:
        n0 = _op_count()
        if n0 >= 1:
            break
        time.sleep(0.05)
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(server_proc.pid)],
                   capture_output=True)
    print(f"  · kill 时已落账 op 数 = {n0}", flush=True)
    killed_mid_landing = 1 <= n0
    check("S4.1 落地进行中 kill server（已落账 op ≥1）", killed_mid_landing, f"n0={n0}")
    pengine.dispose()

    proc2 = restart_server()
    ok = H.wait_port(f"{API}/projects", timeout=40.0)
    check("S4.2 server 重启就绪", ok)

    ok = await wait_status(rest, p["id"], "landed", timeout=60.0)
    check("S4.3 启动扫描续跑 → landed（前缀 hit 尾段补齐）", ok)
    kinds = await nodes_by_kind(rest, ch)
    k_titles: list[str] = []
    for n in kinds["agent"]:
        k_titles.append((await task_of_node(rest, n))["title"])
    step_titles = [t for t in k_titles if t.startswith("步骤 ")]
    check("S4.4 十节点无重复无缺失", len(step_titles) == 10 and len(set(step_titles)) == 10,
          f"{len(step_titles)}/{len(set(step_titles))}")
    landed_msgs = [b for b in await thread_bodies(rest, ch, root) if "拆解已落地" in b]
    check("S4.5 「已落地」消息恰一条", len(landed_msgs) == 1, f"{len(landed_msgs)}")
    return proc2


# ---------------------------------------------------------------- S5 delta + O9


async def s5_delta_o9(rest: Rest, ids: dict, s1: dict) -> None:
    print("\n== S5 delta + O9（A6 + 部分接受）==", flush=True)
    ch = s1["channel_id"]
    canvas_id = ids["channels"]["orchestrated"]["canvas_id"]
    orch = ids["agents"]["Orch"]
    root = s1["root"]

    # O9：Agent 直接建节点 → 403 rule=O9。
    r = await rest.post(f"/canvases/{canvas_id}/nodes",
                        {"kind": "agent", "title": "越权节点"}, agent=orch)
    check("S5.1 Agent 直接建节点 403（O9）", r.status_code == 403
          and r.json().get("error", {}).get("rule") == "O9", f"{r.status_code}")
    # 连边用真实节点 id（合法 ULID 才抵达 O9 门；随意串会被 FastAPI 层 422 拦在门前）。
    real_nodes = [n["id"] for n in s1["kinds"]["agent"][:2]]
    r = await rest.post(f"/canvases/{canvas_id}/edges",
                        {"from_node_id": real_nodes[0], "to_node_id": real_nodes[1]},
                        agent=orch)
    check("S5.2 Agent 直接连边 403（O9）", r.status_code == 403
          and r.json().get("error", {}).get("rule") == "O9", f"{r.status_code}")

    # delta 提案：base=当前基线；4 op，部分接受剔除后两 op 落地。
    cv = await canvas_state(rest, ch)
    check_node = s1["kinds"]["check"][0]
    delta = {
        "version": "coagentia.decomposition-delta.v1",
        "base": cv["baseline_hash"],
        "reason": "补充发布说明与回滚预案两个跟进任务。",
        "operations": [
            {"op": "add_node", "node": {"temp_id": "D1", "title": "撰写发布说明",
                                        "kind": "agent", "task_plan": plan("发布说明")}},
            {"op": "add_edge", "from": check_node["id"], "to": "D1"},
            {"op": "add_node", "node": {"temp_id": "D2", "title": "回滚预案",
                                        "kind": "agent", "task_plan": plan("回滚预案")}},
            {"op": "add_edge", "from": "D1", "to": "D2"},
        ],
    }
    msg = await post_control(rest, ch, root, orch, delta, prose="增量提案。")
    check("S5.3 delta <control> 即提案卡", msg.get("card_kind") == "proposal")
    pid = msg["card_ref"]
    ok = await wait_status(rest, pid, "awaiting_confirm")
    check("S5.4 delta 校验通过 → awaiting_confirm（delta.proposed）", ok)

    before = len((await canvas_state(rest, ch))["nodes"])
    r = await fresh_confirm(rest, ch, pid, removed_ops=[2, 3])
    check("S5.5 部分接受确认 202（removed_ops=[2,3]）", r.status_code == 202, f"{r.status_code}")
    ok = await wait_status(rest, pid, "landed")
    check("S5.6 delta 落地 → landed", ok)
    pp = (await rest.get(f"/proposals/{pid}", expect=200)).json()
    check("S5.7 delta_landed_hash ≠ delta_hash 且 adjustments=removed_ops",
          pp["landed_hash"] != pp["proposal_hash"] and pp.get("adjustments") == [2, 3])
    cv2 = await canvas_state(rest, ch)
    titles = []
    for n in cv2["nodes"]:
        if n.get("kind") == "agent" and n.get("task_id"):
            t = (await rest.get(f"/tasks/{n['task_id']}", expect=200)).json()["task"]
            titles.append(t["title"])
    check("S5.8 剔除生效：D1 落地、D2 未落地",
          any("发布说明" in t for t in titles) and not any("回滚预案" in t for t in titles),
          f"nodes {before}→{len(cv2['nodes'])}")
    bodies = await thread_bodies(rest, ch, root)
    check("S5.9 剔除清单消息进线程（Orchestrator 可读）",
          any("已剔除" in b for b in bodies))
    check("S5.10 增量已落地消息恰一条",
          len([b for b in bodies if "增量已落地" in b]) == 1)

    # base 过期（F9）：新 delta → 人类改画布推进基线 → confirm 409 + 提案 failed。
    cv3 = await canvas_state(rest, ch)
    delta2 = {
        "version": "coagentia.decomposition-delta.v1", "base": cv3["baseline_hash"],
        "reason": "再补一个任务（用于 F9 探针）。",
        "operations": [{"op": "add_node", "node": {
            "temp_id": "D9", "title": "F9 探针任务", "kind": "agent",
            "task_plan": plan("F9 探针")}}],
    }
    msg2 = await post_control(rest, ch, root, orch, delta2, prose="第二个增量。")
    pid2 = msg2["card_ref"]
    await wait_status(rest, pid2, "awaiting_confirm")
    # 人类直接建节点推进基线（人类不受 O9 限制，C5）。
    await rest.post(f"/canvases/{canvas_id}/nodes",
                    {"kind": "agent", "title": "人类插入节点（推进基线）"}, expect=201)
    r = await fresh_confirm(rest, ch, pid2)
    check("S5.11 base 过期 confirm → 409 DELTA_BASE_MISMATCH（F9）",
          r.status_code == 409
          and r.json().get("error", {}).get("code") == "DELTA_BASE_MISMATCH",
          f"{r.status_code}")
    ok = await wait_status(rest, pid2, "failed", timeout=10.0)
    check("S5.12 F9 处置：提案 → failed + 线程要求重出",
          ok and any("基线已过期" in b for b in await thread_bodies(rest, ch, root)))


# ---------------------------------------------------------------- S6/S7


async def s6_single_task(rest: Rest, ids: dict) -> None:
    print("\n== S6 single_task（A7）==", flush=True)
    ch = ids["channels"]["repair"]["channel_id"]
    orch = ids["agents"]["Orch"]
    p = await decompose(rest, ch, "修一个 README 错别字。")
    src_task, root = await source_thread_root(rest, p["id"])
    body = {"version": "coagentia.decomposition.v1", "source": src_task,
            "mode": "single_task", "summary": "单任务：README 错别字修正。",
            "nodes": [{"temp_id": "S1", "title": "修正 README 错别字", "kind": "agent",
                       "task_plan": plan("README 修正")}]}
    await post_control(rest, ch, root, orch, body)
    await wait_status(rest, p["id"], "awaiting_confirm")
    kinds_before = await nodes_by_kind(rest, ch)
    r = await fresh_confirm(rest, ch, p["id"])
    assert r.status_code == 202, r.text
    ok = await wait_status(rest, p["id"], "landed")
    kinds = await nodes_by_kind(rest, ch)
    added_agents = len(kinds["agent"]) - len(kinds_before["agent"])
    added_summary = len(kinds["summary"]) - len(kinds_before["summary"])
    added_merge = len(kinds["merge"]) - len(kinds_before["merge"])
    check("S6.1 single_task 落地：+1 agent、无汇总、无自动 merge（A7）",
          ok and added_agents == 1 and added_summary == 0 and added_merge == 0,
          f"+agent={added_agents} +summary={added_summary} +merge={added_merge}")


async def s7_direct(rest: Rest, ids: dict, db_url: str) -> None:
    print("\n== S7 直落（A8）==", flush=True)
    ch = ids["channels"]["direct"]["channel_id"]
    orch = ids["agents"]["Orch"]
    p = await decompose(rest, ch, "整理会议纪要并归档。")
    src_task, root = await source_thread_root(rest, p["id"])
    body = {"version": "coagentia.decomposition.v1", "source": src_task,
            "mode": "decompose", "summary": "会议纪要两步：整理与归档。",
            "nodes": [
                {"temp_id": "R1", "title": "整理纪要", "kind": "agent",
                 "task_plan": plan("整理纪要")},
                {"temp_id": "R2", "title": "归档发布", "kind": "agent",
                 "task_plan": plan("归档发布")},
            ],
            "edges": [{"from": "R1", "to": "R2"}]}
    await post_control(rest, ch, root, orch, body)
    ok = await wait_status(rest, p["id"], "landed", timeout=40.0)
    check("S7.1 直落频道无确认停顿 → landed（A8）", ok)
    pengine = H.probe_engine(db_url)
    with pengine.connect() as c:
        row = c.execute(
            select(models.LandingBatch.__table__.c.confirmed_by).where(
                models.LandingBatch.__table__.c.source_ref == p["id"])
        ).scalar()
    pengine.dispose()
    check("S7.2 账本 confirmed_by=auto(channel-policy)", row == "auto(channel-policy)",
          str(row))


# ---------------------------------------------------------------- main


async def run(ids: dict, repos: dict, keep: bool, server_proc, restart_server,
              db_url: str):
    daemon_root = Path(os.environ["M6_DAEMON_ROOT"])
    client, _ = H.build_daemon(SERVER_URL, daemon_root)
    daemon_task = asyncio.create_task(client.run())
    proc_holder = {"proc": server_proc}
    try:
        await asyncio.wait_for(client.connected.wait(), timeout=15.0)
        check("S0.1 daemon-sim 真 websockets 连上真 server", True)
        ws_probe = WsProbe()
        await ws_probe.start()
        async with httpx.AsyncClient(timeout=30.0) as hc:
            rest = Rest(hc)
            pa = (await rest.post("/projects", {
                "name": "SnakeRepo", "repo_path": str(repos["snake"]),
                "computer_id": H.COMP_ID}, expect=201)).json()
            await rest.post(
                f"/channels/{ids['channels']['orchestrated']['channel_id']}/projects",
                {"project_id": pa["id"]}, expect=201)
            pb = (await rest.post("/projects", {
                "name": "ConflictRepo", "repo_path": str(repos["conflict"]),
                "computer_id": H.COMP_ID}, expect=201)).json()
            await rest.post(
                f"/channels/{ids['channels']['conflict']['channel_id']}/projects",
                {"project_id": pb["id"]}, expect=201)
            check("S0.2 Project 建立并绑定频道", True)

            s1 = await s1_full_chain(rest, ids, pa["id"], repos["snake"], ws_probe)
            await s2_conflict(rest, ids, pb["id"], repos["conflict"])
            await s3_repair(rest, ids)
            # S4 会 kill/重启 server：daemon 断线重连由 client.run 自恢复。
            proc2 = await s4_crash_replay(rest, ids, proc_holder["proc"], restart_server)
            if proc2 is not None:
                proc_holder["proc"] = proc2
            await asyncio.sleep(2.0)  # daemon 重连窗口
            await s5_delta_o9(rest, ids, s1)
            await s6_single_task(rest, ids)
            await s7_direct(rest, ids, db_url)
        await ws_probe.stop()
    finally:
        if not keep:
            client.stop()
            await client.shutdown()
            daemon_task.cancel()
            with __import__("contextlib").suppress(BaseException):
                await daemon_task
    return proc_holder["proc"]


def main() -> int:
    keep = "--keep" in sys.argv
    base = Path(tempfile.mkdtemp(prefix="m6_verify_"))
    db_path = base / "coagentia.db"
    db_url = sqlite_url(db_path)
    data_root = base / "server-data"
    daemon_root = base / "daemon"
    repos_root = base / "repos"

    print(f"临时根：{base}", flush=True)
    H.migrate(db_url)
    engine = H.make_engine(url=db_url)
    ids = seed_m6b(engine)
    engine.dispose()

    repos = {
        "snake": H.scratch_repo(repos_root, "snake-repo",
                                seed_file="README.md", seed_body="seed\n"),
        "conflict": H.scratch_repo(repos_root, "conflict-repo",
                                   seed_file="conflict.txt", seed_body="base\n"),
    }

    web_dist = Path(__file__).resolve().parents[1] / "apps" / "web" / "dist"
    env = dict(os.environ, M6A_DB_URL=db_url, M6A_DATA_ROOT=str(data_root),
               M6_DAEMON_ROOT=str(daemon_root),
               COAGENTIA_WEB_DIST=str(web_dist),  # --keep 后浏览器截图同源 SPA
               PYTHONPATH=str(Path(__file__).resolve().parent))
    os.environ["M6A_DB_URL"] = db_url
    os.environ["M6_DAEMON_ROOT"] = str(daemon_root)

    def start_server():
        return subprocess.Popen(
            ["uv", "run", "uvicorn", "m6a_appfactory:make_probe_app", "--factory",
             "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
            cwd=str(Path(__file__).resolve().parents[1]), env=env)

    proc = start_server()
    final_proc = proc
    try:
        if not H.wait_port(f"{API}/projects", timeout=40.0):
            print("!! server 未就绪", flush=True)
            return 2
        final_proc = asyncio.run(run(ids, repos, keep, proc, start_server, db_url))
    finally:
        passed = sum(1 for _, ok, _ in RESULTS if ok)
        total = len(RESULTS)
        print(f"\n=== M6 J12 实机 verify：{passed}/{total} "
              f"{'ALL PASS' if passed == total else 'HAS FAILURES'} ===", flush=True)
        (base / "results.json").write_text(
            json.dumps([{"name": n, "pass": ok, "detail": d} for n, ok, d in RESULTS],
                       ensure_ascii=False, indent=2), encoding="utf-8")
        if keep:
            print(f"\n[--keep] server pid={final_proc.pid} port={PORT} 保留；db={db_url}\n"
                  f"临时根={base}\n结束后手动 taskkill。", flush=True)
        else:
            with __import__("contextlib").suppress(Exception):
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(final_proc.pid)],
                               capture_output=True)
            with __import__("contextlib").suppress(Exception):
                final_proc.wait(timeout=10)
    return 0 if all(ok for _, ok, _ in RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
