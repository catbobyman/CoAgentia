"""M8c（L13）实机 verify —— M8c 新面全链：真 uvicorn + 真 daemon-sim（FakeAdapter）+ 隔离临时库。

覆盖 M8c 引入的新用户可见面（端到端真机）：
- **B-M8-3 外壳 / L10**：POST /channels 新建频道、POST /agents 建 Agent（零新端点，真服务端）。
- **L11 入职问候**：默认关→零问候 / 开→上线 daemon 收到 InjectKind.SYSTEM 问候一条 / 重启不重复
  / diagnostic 幂等标记落一条 / 关→零动作。经 FakeAdapter.injects 于线级观测。
- **L1 原子建边（加固批）**：带上游 merge 节点同事务建节点+入边（不空成功）、悬空上游 → 422。

O8 协调护栏（8 轮/stall/replan/阻断/恢复）与 W9 双档 satisfied 的**正确性由单元套证**
（test_summary/test_delta/test_gating，全量 1122 passing 跑在真 ORM/DB 上）——按 M8-HANDOFF 防返工
锚点 7「O8 红例用 daemon-sim/单测，真 CLI 只进演示位」，本机不烧真 Orchestrator 空转多轮。

用法：uv run python scratchpad/m8_verify.py [--keep]
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import m6a_harness as H
from coagentia_server.db import models
from coagentia_server.db.engine import make_engine
from sqlalchemy import select

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

RESULTS: list[tuple[str, bool, str]] = []


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


PORT = _free_port()
SERVER_URL = f"http://127.0.0.1:{PORT}"
API = f"{SERVER_URL}/api"
_DIAG = models.tbl(models.DiagnosticEvent)
_GREET_DIAG = "agent.onboarding_greeting"


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""),
          flush=True)


async def poll(fn, timeout: float = 20.0, interval: float = 0.3):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = await fn()
        if last:
            return last
        await asyncio.sleep(interval)
    return last


class Rest:
    def __init__(self, hc: httpx.AsyncClient) -> None:
        self.hc = hc

    def _h(self, agent: str | None) -> dict:
        if agent is None:
            return {}
        return {"X-Acting-Member": agent, "Authorization": f"Bearer {H.API_KEY}"}

    async def req(self, method, path, body=None, expect=None, agent=None):
        r = None
        for _ in range(6):
            r = await self.hc.request(method, f"{API}{path}", json=body, headers=self._h(agent))
            if r.status_code != 500:
                break
            await asyncio.sleep(0.5)
        if expect is not None and r.status_code != expect:
            raise AssertionError(f"{method} {path} → {r.status_code} (want {expect}): {r.text}")
        return r

    async def post(self, path, body=None, expect=None, agent=None):
        return await self.req("POST", path, body, expect, agent)

    async def patch(self, path, body=None, expect=None, agent=None):
        return await self.req("PATCH", path, body, expect, agent)

    async def get(self, path, expect=None, agent=None):
        return await self.req("GET", path, None, expect, agent)


def _channel_names(snap) -> list[str]:
    rows = snap.get("channels") if isinstance(snap, dict) else snap
    if rows is None and isinstance(snap, dict):
        rows = snap.get("items", [])
    return [c["name"] for c in rows]


def _marker_count(engine, agent_id: str) -> int:
    with engine.connect() as c:
        return len(c.execute(
            select(_DIAG.c.seq).where(
                _DIAG.c.agent_member_id == agent_id, _DIAG.c.type == _GREET_DIAG
            )
        ).all())


async def run(ids: dict, keep: bool, db_url: str) -> None:
    daemon_root = Path(os.environ["M6_DAEMON_ROOT"])
    client, _ = H.build_daemon(SERVER_URL, daemon_root)
    daemon_task = asyncio.create_task(client.run())
    engine = H.probe_engine(db_url)
    adapter = client.adapter  # FakeAdapter：injects/starts 线级可观测
    try:
        await asyncio.wait_for(client.connected.wait(), timeout=15.0)
        check("P0 daemon-sim 真 websockets 连上真 server", True)

        async with httpx.AsyncClient(timeout=30.0) as hc:
            rest = Rest(hc)

            # ---------------- B-M8-3 外壳 / L10（零新端点） ----------------
            await rest.post("/channels", {"name": "m8-shell", "member_ids": []}, expect=201)
            snap = (await rest.get("/channels", expect=200)).json()
            check("S1 POST /channels 新建频道进快照",
                  "m8-shell" in _channel_names(snap), "侧栏『新建频道』真实端点")

            r = await rest.post("/agents", {
                "computer_id": H.COMP_ID, "name": "ShellBot",
                "runtime": "claude_code", "model": "m",
            }, expect=201)
            shell_agent = r.json()["member_id"]
            members = (await rest.get("/members", expect=200)).json()
            mrows = members.get("items") if isinstance(members, dict) else members
            check("S2 POST /agents 建 Agent 进成员表",
                  any(m["id"] == shell_agent for m in mrows), "Members 页『创建 Agent』真实端点")

            # ---------------- L11 入职问候 ----------------
            a_off = ids["agents"][0]
            a_on = ids["agents"][1]

            ws = (await rest.get("/workspace", expect=200)).json()
            check("L11-0 seed 工作区默认关（裁决 #9）", ws.get("onboarding_greeting") is False,
                  f"onboarding_greeting={ws.get('onboarding_greeting')}")

            # G1 默认关 → 上线零问候、不落标记
            base = len(adapter.injects)
            await rest.post(f"/agents/{a_off}/lifecycle", {"action": "start"}, expect=200)
            await asyncio.sleep(0.8)
            off_injects = [b for (aid, b) in adapter.injects[base:] if aid == a_off]
            check("L11-1 默认关→上线零问候", not off_injects and _marker_count(engine, a_off) == 0)

            # 开开关
            await rest.patch("/workspace", {"onboarding_greeting": True}, expect=200)

            # G2 开→上线问候一条（daemon 线级收到 SYSTEM inject）
            base = len(adapter.injects)
            await rest.post(f"/agents/{a_on}/lifecycle", {"action": "start"}, expect=200)

            async def _greeted():
                got = [b for (aid, b) in adapter.injects if aid == a_on]
                return got or None
            greet = await poll(_greeted, timeout=10.0)
            body = greet[0] if greet else ""
            check("L11-2 开→daemon 收到入职问候一条",
                  bool(greet) and "#all" in body and "欢迎" in body,
                  f"body 摘要：{body[:34]}…" if body else "无 inject")
            check("L11-3 幂等标记落一条", _marker_count(engine, a_on) == 1)

            # G3 重启（再 START）→ 不重复问候
            before = len([b for (aid, b) in adapter.injects if aid == a_on])
            await rest.post(f"/agents/{a_on}/lifecycle", {"action": "start"}, expect=200)
            await asyncio.sleep(0.8)
            after = len([b for (aid, b) in adapter.injects if aid == a_on])
            check("L11-4 重启不重复问候（标记 airtight）",
                  after == before and _marker_count(engine, a_on) == 1,
                  f"inject {before}→{after}")

            # ---------------- L1 原子建边（加固批 K1） ----------------
            canvas_id = ids["channels"][0]["canvas_id"]
            up = await rest.post(f"/canvases/{canvas_id}/nodes",
                                 {"title": "上游工作", "kind": "agent"}, expect=201)
            up_node = up.json()["node"]["id"]
            mg = await rest.post(f"/canvases/{canvas_id}/nodes", {
                "title": "汇合", "kind": "system", "system_action": "merge",
                "upstream_node_ids": [up_node],
            }, expect=201)
            merge_node = mg.json()["node"]["id"]
            cv = (await rest.get(f"/channels/{ids['channels'][0]['channel_id']}/canvas",
                                 expect=200)).json()
            has_edge = any(e["from_node_id"] == up_node and e["to_node_id"] == merge_node
                           for e in cv["edges"])
            mnode = next((n for n in cv["nodes"] if n["id"] == merge_node), None)
            check("L1-1 带上游 merge 节点：节点+入边同事务原子落地（非空成功）",
                  has_edge and mnode is not None and mnode.get("system_status") == "idle",
                  f"edge={has_edge} status={mnode and mnode.get('system_status')}")

            r = await rest.post(f"/canvases/{canvas_id}/nodes", {
                "title": "悬空", "kind": "system", "system_action": "merge",
                "upstream_node_ids": ["01K0NONEXISTENT00000000000"],
            })
            check("L1-2 悬空上游 → 422（全量收集，不留悬挂节点）", r.status_code == 422,
                  f"status={r.status_code}")

            if keep:
                print(f"\n[KEEP] SERVER_URL={SERVER_URL}", flush=True)
                print(f"[KEEP] shell_channel=m8-shell agent_on={a_on}", flush=True)
                print("[KEEP] 浏览 SPA；Ctrl+C 结束。", flush=True)
                await asyncio.sleep(3600)
    finally:
        engine.dispose()
        if not keep:
            with __import__("contextlib").suppress(BaseException):
                client.stop()
                await client.shutdown()
            daemon_task.cancel()
            with __import__("contextlib").suppress(BaseException):
                await daemon_task


def main() -> int:
    keep = "--keep" in sys.argv
    base = Path(tempfile.mkdtemp(prefix="m8_verify_"))
    db_url = f"sqlite:///{base / 'coagentia.db'}"
    data_root = str(base / "server-data")
    daemon_root = base / "daemon"
    print(f"临时根：{base}", flush=True)

    H.migrate(db_url)
    engine = make_engine(url=db_url)
    ids = H.seed(engine)
    engine.dispose()

    os.environ["M6A_DB_URL"] = db_url
    os.environ["M6A_DATA_ROOT"] = data_root
    os.environ["M6_DAEMON_ROOT"] = str(daemon_root)

    proc = subprocess.Popen(
        ["uv", "run", "uvicorn", "m6a_appfactory:make_probe_app", "--factory",
         "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
        cwd=str(Path(__file__).resolve().parent),
        env={**os.environ}, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        if not H.wait_port(f"{SERVER_URL}/api/workspace", timeout=40.0):
            print("server 未起", flush=True)
            return 1
        asyncio.run(run(ids, keep, db_url))
    finally:
        if not keep:
            proc.terminate()
            with __import__("contextlib").suppress(Exception):
                proc.wait(timeout=10)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n=== M8c verify: {passed}/{total} "
          f"{'ALL PASS' if passed == total else 'HAS FAILURES'} ===", flush=True)
    import json
    out = Path(__file__).resolve().parents[1] / "docs" / "verify" / "M8-VERIFY-results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {"passed": passed, "total": total,
         "results": [{"probe": n, "ok": ok, "detail": d} for n, ok, d in RESULTS]},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
