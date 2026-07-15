"""PS-WT 实机 verify —— 目录浏览 + 工作树管理台全链：真 uvicorn + 真 daemon(真 git.py) + 真 git worktree。

覆盖 PS-WT 新用户可见面（端到端真机，非 mock/桩）：
- fs 代理：真盘符枚举(V1) / 子目录 has_git(V2) / Agent 403(V3) / daemon 离线 503(V4)。
- 管理台读面：live=0 骨架(V5) / live=1 合账 ok+live/missing/orphan 三态(V6)。
- 清理门：active→409 not_terminal(V7) / 预览活跃→409 preview_active(V12) / Agent 403(V11)。
- 清理执行：merged 树真删盘+CAS 收敛 cleaned(V8) / 孤儿真删盘 removed(V9) / 非孤儿 409(V10)。

真 daemon.git 扫真 worktrees_dir、真 git worktree add/remove；护栏 _assert_managed_target 的
worktrees_dir 边界正确性由单元套证（本机只跑正常路径不构造越界删）。

用法：uv run python scratchpad/pswt_verify.py [--keep]
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

import httpx
import m6a_harness as H
from coagentia_server.db import models
from coagentia_server.ledger.service import new_ulid, now_iso
from sqlalchemy import insert, select

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
_WT = models.tbl(models.Worktree)


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""),
          flush=True)


class Rest:
    def __init__(self, hc: httpx.AsyncClient) -> None:
        self.hc = hc

    def _h(self, agent: str | None) -> dict:
        if agent is None:
            return {}
        return {"X-Acting-Member": agent, "Authorization": f"Bearer {H.API_KEY}"}

    async def req(self, method, path, body=None, expect=None, agent=None, params=None):
        r = None
        for _ in range(6):
            r = await self.hc.request(method, f"{API}{path}", json=body,
                                      headers=self._h(agent), params=params)
            if r.status_code != 500:
                break
            await asyncio.sleep(0.5)
        if expect is not None and r.status_code != expect:
            raise AssertionError(f"{method} {path} → {r.status_code} (want {expect}): {r.text}")
        return r

    async def post(self, path, body=None, expect=None, agent=None):
        return await self.req("POST", path, body, expect, agent)

    async def get(self, path, expect=None, agent=None, params=None):
        return await self.req("GET", path, None, expect, agent, params)


def _insert_worktree(engine, *, wt_id, project_id, task_id, status, path, branch,
                     merged_at=None, cleaned_at=None) -> None:
    with engine.begin() as c:
        c.execute(insert(_WT).values(
            id=wt_id, workspace_id=H.WS_ID, project_id=project_id, task_id=task_id,
            branch=branch, path=str(path), status=status,
            merge_commit=None, created_at=now_iso(), merged_at=merged_at, cleaned_at=cleaned_at))


async def run(ids: dict, keep: bool, db_url: str, repo: Path, comp2_id: str) -> None:
    daemon_root = Path(os.environ["M6_DAEMON_ROOT"])
    client, paths = H.build_daemon(SERVER_URL, daemon_root)
    daemon_task = asyncio.create_task(client.run())
    engine = H.probe_engine(db_url)
    wt_root = paths.worktrees_dir
    cid = ids["channels"][0]["channel_id"]
    agent0 = ids["agents"][0]
    try:
        await asyncio.wait_for(client.connected.wait(), timeout=15.0)
        check("P0 真 daemon(真 git.py) websockets 连上真 server", True)

        async with httpx.AsyncClient(timeout=30.0) as hc:
            rest = Rest(hc)

            # ---- 建 Project（真 REST，指向真 scratch 仓库）----
            pr = await rest.post("/projects", {
                "name": "pswt-verify", "repo_path": str(repo), "computer_id": H.COMP_ID,
            }, expect=201)
            proj_id = pr.json()["id"]

            # ---- 建 4 个真任务（真 as_task）：A 活跃有盘/脏, C 活跃无盘=丢失, M merged 待清, P merged+预览 ----
            async def _mktask(title: str) -> str:
                r = await rest.post(f"/channels/{cid}/messages",
                                    {"body": title, "as_task": {"title": title}}, expect=201)
                return r.json()["task"]["id"]
            task_a = await _mktask("活跃有盘")
            task_c = await _mktask("活跃丢失")
            task_m = await _mktask("已合待清")
            task_p = await _mktask("预览占用")
            orphan_task = new_ulid()  # 无 DB 任务 → 孤儿

            # ---- 真 git worktree add 造盘上工作树（A/M/P/orphan 有盘，C 无盘）----
            def _addtree(task_id: str) -> Path:
                target = wt_root / proj_id / task_id
                target.parent.mkdir(parents=True, exist_ok=True)
                H.git(repo, "worktree", "add", str(target), "-b", f"coagentia/task-{task_id}")
                return target
            path_a = _addtree(task_a)
            (path_a / "dirty.txt").write_text("uncommitted", encoding="utf-8")  # A 脏
            path_m = _addtree(task_m)
            path_p = _addtree(task_p)
            path_orphan = _addtree(orphan_task)
            # 主干推进一提交 → 各树落后 1（behind 尽力可见）
            (repo / "advance.txt").write_text("x", encoding="utf-8")
            H.git(repo, "add", "--", "advance.txt")
            H.git(repo, "commit", "-m", "advance main")

            # ---- 登记 Worktree 行 ----
            wt_a, wt_c, wt_m, wt_p = (new_ulid() for _ in range(4))
            _insert_worktree(engine, wt_id=wt_a, project_id=proj_id, task_id=task_a,
                             status="active", path=path_a, branch=f"coagentia/task-{task_a}")
            _insert_worktree(engine, wt_id=wt_c, project_id=proj_id, task_id=task_c,
                             status="active", path=wt_root / proj_id / task_c,
                             branch=f"coagentia/task-{task_c}")
            _insert_worktree(engine, wt_id=wt_m, project_id=proj_id, task_id=task_m,
                             status="merged", path=path_m, branch=f"coagentia/task-{task_m}",
                             merged_at=now_iso())
            _insert_worktree(engine, wt_id=wt_p, project_id=proj_id, task_id=task_p,
                             status="merged", path=path_p, branch=f"coagentia/task-{task_p}",
                             merged_at=now_iso())
            # P 挂活跃预览
            with engine.begin() as c:
                c.execute(insert(models.tbl(models.PreviewSession)).values(
                    id=new_ulid(), workspace_id=H.WS_ID, task_id=task_p, worktree_id=wt_p,
                    port=44100, status="running", started_at=now_iso(), last_active_at=now_iso()))

            # ================= V1–V4 fs 代理 =================
            r = await rest.get(f"/computers/{H.COMP_ID}/fs", expect=200)
            drives = [e["name"] for e in r.json()["entries"]]
            check("V1 fs 根视图真盘符枚举", any(n.rstrip("\\").endswith(":") for n in drives),
                  f"drives={drives}")

            r = await rest.get(f"/computers/{H.COMP_ID}/fs", expect=200,
                               params={"path": str(repo.parent)})
            names = {e["name"]: e for e in r.json()["entries"]}
            check("V2 fs 子目录列 + has_git 命中真仓库",
                  repo.name in names and names[repo.name]["has_git"] is True,
                  f"repo={repo.name} has_git={names.get(repo.name, {}).get('has_git')}")

            r = await rest.get(f"/computers/{H.COMP_ID}/fs", agent=agent0)
            check("V3 fs Agent 主体 → 403（O9 同门）", r.status_code == 403, f"status={r.status_code}")

            r = await rest.get(f"/computers/{comp2_id}/fs")
            check("V4 fs daemon 离线 → 503 DAEMON_OFFLINE", r.status_code == 503,
                  f"status={r.status_code}")

            # ================= V5 管理台 live=0 骨架 =================
            r = await rest.get("/worktrees", expect=200, params={"live": 0})
            body = r.json()
            by_task = {it["task_id"]: it for it in body["items"]}
            check("V5 live=0 纯 DB 骨架（4 行 derived=ok，scans 空）",
                  body["scans"] == [] and all(by_task[t]["derived"] == "ok"
                                              for t in (task_a, task_c, task_m, task_p))
                  and all(by_task[t]["live"] is None for t in (task_a, task_c)),
                  f"items={len(body['items'])}")

            # ================= V6 管理台 live=1 合账三态 =================
            r = await rest.get("/worktrees", expect=200, params={"live": 1})
            body = r.json()
            by_task = {it["task_id"]: it for it in body["items"]}
            orphans = [it for it in body["items"] if it["derived"] == "orphan"]
            a_live = by_task.get(task_a, {}).get("live") or {}
            scan_ok = any(s["computer_id"] == H.COMP_ID and s["status"] == "ok"
                          for s in body["scans"])
            check("V6a live=1 有盘活跃树 derived=ok + live.dirty=True",
                  by_task[task_a]["derived"] == "ok" and a_live.get("dirty") is True,
                  f"derived={by_task[task_a]['derived']} dirty={a_live.get('dirty')}")
            check("V6b live=1 活跃无盘树 derived=missing（丢失）",
                  by_task[task_c]["derived"] == "missing", f"derived={by_task[task_c]['derived']}")
            check("V6c live=1 盘上无登记树浮出 orphan 行（id=None，目录名解析 task）",
                  any(o["task_id"] == orphan_task and o["id"] is None
                      and o["project_id"] == proj_id for o in orphans),
                  f"orphans={[o['task_id'][:8] for o in orphans]}")
            check("V6d scans 报本机 ok", scan_ok, f"scans={body['scans']}")

            # ================= V7/V11/V12 清理门 =================
            r = await rest.post(f"/worktrees/{wt_a}/cleanup")
            check("V7 active 树清理 → 409 WORKTREE_NOT_TERMINAL（裁决 #10）",
                  r.status_code == 409
                  and r.json()["error"]["code"] == "WORKTREE_NOT_TERMINAL",
                  f"status={r.status_code}")

            r = await rest.post(f"/worktrees/{wt_m}/cleanup", agent=agent0)
            check("V11 清理 Agent 主体 → 403（O9 同门）", r.status_code == 403,
                  f"status={r.status_code}")

            r = await rest.post(f"/worktrees/{wt_p}/cleanup")
            check("V12 预览活跃树清理 → 409 WORKTREE_PREVIEW_ACTIVE",
                  r.status_code == 409
                  and r.json()["error"]["code"] == "WORKTREE_PREVIEW_ACTIVE",
                  f"status={r.status_code}")

            # ================= V8 merged 树真清理：删盘 + CAS 收敛 cleaned =================
            r = await rest.post(f"/worktrees/{wt_m}/cleanup", expect=200)
            cleaned = r.json()
            await asyncio.sleep(0.4)
            with engine.connect() as c:
                db_status = c.execute(
                    select(_WT.c.status).where(_WT.c.id == wt_m)).scalar()
            check("V8 merged 清理：真删盘 + 登记 CAS 收敛 cleaned",
                  cleaned["status"] == "cleaned" and db_status == "cleaned"
                  and not path_m.exists(),
                  f"resp={cleaned['status']} db={db_status} disk_gone={not path_m.exists()}")

            # ================= V9 孤儿真清理：ids-only + 删盘 =================
            r = await rest.post(f"/computers/{H.COMP_ID}/worktrees/cleanup-orphan",
                                {"project_id": proj_id, "task_id": orphan_task}, expect=200)
            await asyncio.sleep(0.4)
            check("V9 孤儿清理：ids-only 真删盘 removed=True",
                  r.json()["removed"] is True and not path_orphan.exists(),
                  f"removed={r.json()['removed']} disk_gone={not path_orphan.exists()}")

            # ================= V10 非孤儿护栏 =================
            r = await rest.post(f"/computers/{H.COMP_ID}/worktrees/cleanup-orphan",
                                {"project_id": proj_id, "task_id": task_a})
            check("V10 存在非 cleaned 登记行 → 409 WORKTREE_NOT_ORPHAN（防误删登记树）",
                  r.status_code == 409
                  and r.json()["error"]["code"] == "WORKTREE_NOT_ORPHAN",
                  f"status={r.status_code}")

            if keep:
                print(f"\n[KEEP] SERVER_URL={SERVER_URL}", flush=True)
                print("[KEEP] 浏览 SPA；Ctrl+C 结束。", flush=True)
                await asyncio.sleep(3600)
    finally:
        engine.dispose()
        if not keep:
            with contextlib.suppress(BaseException):
                client.stop()
                await client.shutdown()
            daemon_task.cancel()
            with contextlib.suppress(BaseException):
                await daemon_task


def main() -> int:
    keep = "--keep" in sys.argv
    base = Path(tempfile.mkdtemp(prefix="pswt_verify_"))
    db_url = f"sqlite:///{base / 'coagentia.db'}"
    data_root = str(base / "server-data")
    daemon_root = base / "daemon"
    print(f"临时根：{base}", flush=True)

    H.migrate(db_url)
    from coagentia_server.db.engine import make_engine
    engine = make_engine(url=db_url)
    ids = H.seed(engine)
    # 第二台 computer（无 daemon 连接）→ fs 503 用
    comp2_id = new_ulid()
    with engine.begin() as c:
        c.execute(insert(models.tbl(models.Computer)).values(
            id=comp2_id, workspace_id=H.WS_ID, name="Offline", api_key_hash="x",
            status="offline", created_at=now_iso()))
    engine.dispose()

    repo = H.scratch_repo(base, "app-repo", seed_file="README.md", seed_body="# app\n")

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
        asyncio.run(run(ids, keep, db_url, repo, comp2_id))
    finally:
        if not keep:
            proc.terminate()
            with contextlib.suppress(Exception):
                proc.wait(timeout=10)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n=== PS-WT verify: {passed}/{total} "
          f"{'ALL PASS' if passed == total else 'HAS FAILURES'} ===", flush=True)
    out = Path(__file__).resolve().parents[1] / "docs" / "verify" / "PSWT-VERIFY-results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {"passed": passed, "total": total,
         "results": [{"probe": n, "ok": ok, "detail": d} for n, ok, d in RESULTS]},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
