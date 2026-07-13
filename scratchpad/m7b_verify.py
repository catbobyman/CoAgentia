"""M7b（K9）实机 verify —— PRD M7 出口全链：真 uvicorn + 真 daemon-sim（真 PreviewRunner/真
DeployRunner 起真子进程）+ 真 scratch git 仓库。

出口句逐环节：需求 → 拆解/交付（writes_code 任务真 worktree）→ 预览验收（真 dev server iframe 200）
→ 合并（merge --no-ff 真 git）→ 一键部署（人类 + Agent 双通道，二次触发 409）→ 日志实时流 + URL
+ 新账 token 小结 → 对账 #9/#10 崩溃探针。deploy_command 用本地脚本输出伪 URL 行（裁决 #14，不真
部署外网）。

用法：uv run python scratchpad/m7b_verify.py [--keep]
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
from coagentia_server.ledger.service import new_ulid, now_iso
from sqlalchemy import insert

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

PY = sys.executable
GOOD_DEV_CMD = f'"{PY}" -m http.server %PORT% --bind 127.0.0.1'
# 部署命令：打印若干行含 URL（DeployRunner 取末 URL），exit 0 → success。
DEPLOY_OK_URL = "https://demo.coagentia.test/build-42"
DEPLOY_OK_CMD = (
    f'"{PY}" -c "import sys;'
    f'print(\'== deploy start ==\');'
    f'print(\'building bundle...\');'
    f'print(\'uploading...\');'
    f'print(\'live at {DEPLOY_OK_URL}\');'
    f'print(\'== done ==\');sys.stdout.flush()"'
)
# 慢部署：先打印一行（promote running），再长睡（409 / 对账 #10 期间保持 running）。
DEPLOY_SLOW_CMD = (
    f'"{PY}" -c "import sys,time;'
    f'print(\'deploying slow...\');sys.stdout.flush();time.sleep(90)"'
)

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


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""),
          flush=True)


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
    def __init__(self, hc: httpx.AsyncClient) -> None:
        self.hc = hc

    def _h(self, agent: str | None) -> dict:
        if agent is None:
            return {}
        return {"X-Acting-Member": agent, "Authorization": f"Bearer {H.API_KEY}"}

    async def post(self, path, body=None, expect=None, agent=None, headers=None):
        r = None
        h = self._h(agent)
        if headers:
            h.update(headers)
        for _ in range(6):
            r = await self.hc.post(f"{API}{path}", json=body, headers=h)
            if r.status_code != 500:
                break
            await asyncio.sleep(0.5)
        if expect is not None and r.status_code != expect:
            raise AssertionError(f"POST {path} → {r.status_code} (want {expect}): {r.text}")
        return r

    async def get(self, path, expect=None):
        r = None
        for _ in range(6):
            r = await self.hc.get(f"{API}{path}")
            if r.status_code != 500:
                break
            await asyncio.sleep(0.5)
        if expect is not None and r.status_code != expect:
            raise AssertionError(f"GET {path} → {r.status_code} (want {expect}): {r.text}")
        return r


async def make_node(rest: Rest, canvas_id: str, body: dict) -> dict:
    r = await rest.post(f"/canvases/{canvas_id}/nodes", body, expect=201)
    return r.json()["node"]


async def wait_worktree(rest: Rest, task_id: str, timeout: float = 45.0) -> dict | None:
    async def _w():
        r = await rest.get(f"/tasks/{task_id}")
        wt = r.json().get("worktree")
        return wt if wt and wt.get("status") == "active" else None
    return await poll(_w, timeout=timeout)


async def wait_preview(rest: Rest, task_id: str, want: str, timeout: float = 45.0):
    async def _s():
        r = await rest.get(f"/tasks/{task_id}/preview")
        if r.status_code != 200:
            return None
        row = r.json()
        return row if row.get("status") == want else None
    return await poll(_s, timeout=timeout)


async def wait_node_status(rest: Rest, channel_id: str, node_id: str, want: str,
                           timeout: float = 90.0):
    async def _n():
        cv = (await rest.get(f"/channels/{channel_id}/canvas")).json()
        for n in cv["nodes"]:
            if n["id"] == node_id and n.get("system_status") == want:
                return n
        return None
    return await poll(_n, timeout=timeout)


async def wait_deployment(rest: Rest, dep_id: str, want: str, timeout: float = 60.0):
    async def _d():
        r = await rest.get(f"/deployments/{dep_id}")
        if r.status_code != 200:
            return None
        row = r.json()
        return row if row.get("status") == want else None
    return await poll(_d, timeout=timeout)


def worktree_commit(wt: Path, fname: str, content: str, msg: str) -> None:
    (wt / fname).write_text(content, encoding="utf-8")
    H.git(wt, "add", "--", fname)
    H.git(wt, "commit", "-m", msg)


async def drive_done(rest: Rest, task_id: str, agent_mid: str) -> None:
    r = await rest.post(f"/tasks/{task_id}/claim", agent=agent_mid)
    if r.status_code not in (200, 409):
        raise AssertionError(f"claim → {r.status_code}: {r.text}")
    t = (await rest.get(f"/tasks/{task_id}")).json()["task"]
    if t["status"] == "todo":
        await rest.post(f"/tasks/{task_id}/status", {"to": "in_progress"}, expect=200,
                        agent=agent_mid)
    handoff = {"kind": "task_handoff", "body": {
        "version": "coagentia.task-handoff.v1", "from_member": agent_mid,
        "to_member": H.OWNER_ID, "deliverables": [{"path": "feature.txt", "kind": "file"}],
        "evidence": [{"type": "test", "ref": "probe", "conclusion": "green"}],
        "open_risks": [], "verify_plan": "re-run", "review_verdict": "pass"}}
    await rest.post(f"/tasks/{task_id}/contracts", handoff, expect=201, agent=agent_mid)
    await rest.post(f"/tasks/{task_id}/status", {"to": "in_review"}, expect=200, agent=agent_mid)
    await rest.post(f"/tasks/{task_id}/status", {"to": "done"}, expect=200)


def seed_usage(engine, *, task_id: str, agent_mid: str, ws_id: str) -> None:
    with engine.begin() as c:
        c.execute(insert(models.TokenUsageEvent.__table__).values(
            id=new_ulid(), workspace_id=ws_id, agent_member_id=agent_mid, task_id=task_id,
            input_tokens=1200, output_tokens=340, cache_read_tokens=80, cache_write_tokens=20,
            source_session="probe", reported_at=now_iso()))


async def deployment_cards(rest: Rest, channel_id: str, dep_id: str) -> list:
    r = await rest.get(f"/channels/{channel_id}/messages")
    msgs = r.json()["items"]
    return [m for m in msgs if m.get("card_kind") == "deployment" and m.get("card_ref") == dep_id]


async def run(ids: dict, repos: dict, keep: bool, db_url: str, data_root: str) -> None:
    daemon_root = Path(os.environ["M6_DAEMON_ROOT"])
    client, _ = H.build_daemon(SERVER_URL, daemon_root)
    daemon_task = asyncio.create_task(client.run())
    engine = H.probe_engine(db_url)
    try:
        await asyncio.wait_for(client.connected.wait(), timeout=15.0)
        check("P0.1 daemon-sim 真 websockets 连上真 server", True)
        paths = client.git.paths
        agent0 = ids["agents"][0]
        ch_a = ids["channels"][0]  # delivery：好项目（预览/合并/部署 happy）
        ch_b = ids["channels"][1]  # conflict：慢项目（409/对账 #10）

        async with httpx.AsyncClient(timeout=30.0) as hc:
            rest = Rest(hc)
            good = (await rest.post("/projects", {
                "name": "DeployGood", "repo_path": str(repos["good"]),
                "computer_id": H.COMP_ID, "dev_command": GOOD_DEV_CMD,
                "deploy_command": DEPLOY_OK_CMD, "preview_idle_min": 30}, expect=201)).json()
            await rest.post(f"/channels/{ch_a['channel_id']}/projects",
                            {"project_id": good["id"]}, expect=201)
            slow = (await rest.post("/projects", {
                "name": "DeploySlow", "repo_path": str(repos["slow"]),
                "computer_id": H.COMP_ID, "deploy_command": DEPLOY_SLOW_CMD}, expect=201)).json()
            await rest.post(f"/channels/{ch_b['channel_id']}/projects",
                            {"project_id": slow["id"]}, expect=201)
            check("P0.2 两 Project 建立并绑定频道（好+慢 deploy_command）", True)

            # ---- P1 交付 + 预览验收（真 dev server iframe 200）----
            n1 = await make_node(rest, ch_a["canvas_id"], {
                "title": "M7 交付任务", "kind": "agent", "writes_code": True,
                "project_id": good["id"]})
            t1 = n1["task_id"]
            wt1 = paths.worktree_path(good["id"], t1)
            got = await wait_worktree(rest, t1)
            check("P1.1 writes_code 任务真 git 派生 worktree", bool(got) and wt1.exists(), wt1.name)
            await rest.post(f"/tasks/{t1}/preview", expect=201)
            prow = await wait_preview(rest, t1, "running", timeout=45.0)
            port = prow["port"] if prow else None
            check("P1.2 daemon 真起 dev server → running 携 port", bool(port), f"port={port}")
            ok200 = False
            if port:
                for _ in range(30):
                    try:
                        if httpx.get(f"http://127.0.0.1:{port}/", timeout=2.0).status_code == 200:
                            ok200 = True
                            break
                    except Exception:
                        pass
                    await asyncio.sleep(0.4)
            check("P1.3 预览验收：iframe 数据源真实 HTTP 200", ok200, f"http://127.0.0.1:{port}/")
            await rest.hc.request("DELETE", f"{API}/tasks/{t1}/preview")  # 回收，腾出后续

            # ---- P2 合并（真 merge --no-ff）----
            worktree_commit(wt1, "feature.txt", "m7 feature\n", "add m7 feature")
            topo = H.insert_system_topology(engine, ch_a["canvas_id"], [n1["id"]])
            await drive_done(rest, t1, agent0)
            merged = await wait_node_status(rest, ch_a["channel_id"], topo["merge"], "success",
                                            timeout=90.0)
            check("P2.1 merge 系统节点自动执行 → success（真 --no-ff 合并）", bool(merged))
            log = H.git(repos["good"], "log", "--oneline", "--merges").stdout
            check("P2.2 主仓出现 --no-ff merge 提交", bool(log.strip()), log.strip()[:50])
            wt_row = (await rest.get(f"/tasks/{t1}")).json().get("worktree")
            check("P2.3 worktree 行终态 merged", (wt_row or {}).get("status") == "merged")

            # ---- P3 seed usage（Agent 花费，新账小结数据源）----
            seed_usage(engine, task_id=t1, agent_mid=agent0, ws_id=H.WS_ID)
            check("P3.1 任务 usage 事件就位（新账小结数据源）", True)

            # ---- P4 部署（人类通道）→ 日志流 → URL → 结果卡 → 新账小结 ----
            dep = (await rest.post(f"/projects/{good['id']}/deployments", None, expect=201)).json()
            check("P4.1 POST 部署 = 201 queued（人类触发）", dep["status"] == "queued", dep["id"])
            fin = await wait_deployment(rest, dep["id"], "success", timeout=60.0)
            check("P4.2 deploy.run 真跑 → deploy.finished success", bool(fin))
            check("P4.3 结果携伪 URL（末 URL 提取）", (fin or {}).get("url") == DEPLOY_OK_URL,
                  (fin or {}).get("url", ""))
            check("P4.4 exit_code=0", (fin or {}).get("exit_code") == 0)
            logpage = (await rest.get(f"/deployments/{dep['id']}/log")).json()
            has_url_line = any(DEPLOY_OK_URL in ln for ln in logpage.get("lines", []))
            check("P4.5 GET /log server 直读落盘含日志行", len(logpage.get("lines", [])) >= 3
                  and has_url_line, f"{len(logpage.get('lines', []))} 行")
            ts = (fin or {}).get("token_summary") or {}
            usage = ts.get("usage") or {}
            tr = ts.get("tasks_reporting") or {}
            check("P4.6 新账 token 小结含合并任务花费（input=1200）",
                  usage.get("input_tokens") == 1200 and t1 in (ts.get("task_ids") or []),
                  f"in={usage.get('input_tokens')} tasks={len(ts.get('task_ids') or [])}")
            check("P4.7 覆盖率诚实标注 reporting/total（无货币字段）",
                  tr.get("total", 0) >= 1 and "currency" not in usage and "cost" not in ts)
            cards = await deployment_cards(rest, ch_a["channel_id"], dep["id"])
            check("P4.8 结果卡进绑定频道（card_kind=deployment/card_ref）", len(cards) == 1,
                  f"{len(cards)} 卡")

            # ---- P5 Agent 触发通道（trigger_deploy REST 代理，X-Acting-Member=agent）----
            dep2 = (await rest.post(f"/projects/{good['id']}/deployments", None, expect=201,
                                    agent=agent0)).json()
            fin2 = await wait_deployment(rest, dep2["id"], "success", timeout=60.0)
            check("P5.1 Agent 通道触发部署（R8 无角色门）→ success", bool(fin2))
            check("P5.2 triggered_by=Agent 留痕",
                  (fin2 or {}).get("triggered_by_member_id") == agent0)

            # ---- P6 成本三层读面 GET /usage ----
            u_task = (await rest.get(f"/usage?level=task&ref={t1}")).json()
            check("P6.1 level=task 聚合 + 恒 {reporting,total=1}",
                  u_task["usage"]["input_tokens"] == 1200
                  and u_task["tasks_reporting"]["total"] == 1)
            u_agent = (await rest.get(f"/usage?level=agent&ref={agent0}&rollup=true")).json()
            check("P6.2 level=agent 聚合 + rollup breakdown",
                  u_agent["usage"]["input_tokens"] >= 1200 and u_agent.get("breakdown") is not None)
            u_canvas = (await rest.get(f"/usage?level=canvas&ref={ch_a['channel_id']}")).json()
            check("P6.3 level=canvas 频道任务集聚合（永无货币）",
                  "currency" not in u_canvas["usage"] and "cost" not in u_canvas)

            # ---- P7 409 不排队（慢部署进行中二次触发）----
            deps = (await rest.post(f"/projects/{slow['id']}/deployments", None, expect=201)).json()
            running = await wait_deployment(rest, deps["id"], "running", timeout=30.0)
            check("P7.1 慢部署 promote running", bool(running))
            r409 = await rest.post(f"/projects/{slow['id']}/deployments", None)
            code = r409.json().get("error", {}).get("code")
            check("P7.2 进行中二次触发 → 409 DEPLOY_IN_PROGRESS 不排队",
                  r409.status_code == 409 and code == "DEPLOY_IN_PROGRESS",
                  f"{r409.status_code} {code}")

            # ---- P8 对账 #10：daemon 真重启（新 boot_nonce）→ running 部署 fail-closed ----
            client.stop()
            await client.shutdown()  # 杀活跃子进程（慢部署树），行留 running
            daemon_task.cancel()
            import contextlib
            with contextlib.suppress(BaseException):
                await daemon_task
            client2, _ = H.build_daemon(SERVER_URL, daemon_root)
            daemon_task = asyncio.create_task(client2.run())
            await asyncio.wait_for(client2.connected.wait(), timeout=15.0)
            failed = await wait_deployment(rest, deps["id"], "failed", timeout=30.0)
            check("P8.1 对账 #10：真重启 running 部署 → fail-closed（不重跑）", bool(failed))
            check("P8.2 fail-closed exit_code=NULL（结果未知）",
                  (failed or {}).get("exit_code") is None)
            fcards = await deployment_cards(rest, ch_b["channel_id"], deps["id"])
            check("P8.3 fail-closed 结果卡 @触发者进频道", len(fcards) == 1, f"{len(fcards)} 卡")

            # ---- P9 对账 #9：真重启 → 活跃预览 fail-close（复验 M7a jitter-survive 反面）----
            n9 = await make_node(rest, ch_a["canvas_id"], {
                "title": "对账9 预览", "kind": "agent", "writes_code": True,
                "project_id": good["id"]})
            t9 = n9["task_id"]
            await wait_worktree(rest, t9)
            await rest.post(f"/tasks/{t9}/preview", expect=201)
            p9run = await wait_preview(rest, t9, "running", timeout=45.0)
            check("P9.0 预览 running（对账 #9 前置）", bool(p9run))
            client2.stop()
            await client2.shutdown()
            daemon_task.cancel()
            with contextlib.suppress(BaseException):
                await daemon_task
            client3, _ = H.build_daemon(SERVER_URL, daemon_root)
            daemon_task = asyncio.create_task(client3.run())
            await asyncio.wait_for(client3.connected.wait(), timeout=15.0)
            p9fail = await wait_preview(rest, t9, "failed", timeout=30.0)
            check("P9.1 对账 #9：真重启后活跃预览 fail-close（子进程已死）", bool(p9fail))
            client = client3

            if keep:
                print(f"\n[KEEP] SERVER_URL={SERVER_URL}", flush=True)
                print(f"[KEEP] delivery_channel={ch_a['channel_id']}", flush=True)
                print(f"[KEEP] deployment_id={dep['id']}", flush=True)
                print("[KEEP] 浏览 SPA 看部署卡；Ctrl+C 结束。", flush=True)
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
    base = Path(tempfile.mkdtemp(prefix="m7b_verify_"))
    db_path = base / "coagentia.db"
    db_url = f"sqlite:///{db_path}"
    data_root = str(base / "server-data")
    daemon_root = base / "daemon"
    repos_root = base / "repos"
    print(f"临时根：{base}", flush=True)

    H.migrate(db_url)
    engine = make_engine(url=db_url)
    ids = H.seed(engine)
    engine.dispose()

    repos = {
        "good": H.scratch_repo(repos_root, "good-repo", seed_file="index.html",
                               seed_body="<h1>m7 deploy ok</h1>\n"),
        "slow": H.scratch_repo(repos_root, "slow-repo", seed_file="app.txt", seed_body="app\n"),
    }

    os.environ["M6A_DB_URL"] = db_url
    os.environ["M6A_DATA_ROOT"] = data_root
    os.environ["M6_DAEMON_ROOT"] = str(daemon_root)

    proc = subprocess.Popen(
        ["uv", "run", "uvicorn", "m7a_appfactory:make_probe_app", "--factory",
         "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
        cwd=str(Path(__file__).resolve().parent),
        env={**os.environ}, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        if not H.wait_port(f"{SERVER_URL}/api/workspace", timeout=40.0):
            print("server 未起", flush=True)
            return 1
        asyncio.run(run(ids, repos, keep, db_url, data_root))
    finally:
        if not keep:
            proc.terminate()
            with __import__("contextlib").suppress(Exception):
                proc.wait(timeout=10)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n=== M7b verify: {passed}/{total} "
          f"{'ALL PASS' if passed == total else 'HAS FAILURES'} ===", flush=True)
    import json
    out = Path(__file__).resolve().parents[1] / "docs" / "verify" / "M7B-VERIFY-results.json"
    out.write_text(json.dumps(
        {"passed": passed, "total": total,
         "results": [{"probe": n, "ok": ok, "detail": d} for n, ok, d in RESULTS]},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
