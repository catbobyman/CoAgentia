"""TS daemon 实机 verify —— 真 uvicorn + 真 node daemon（apps/daemon-ts）+ 真 git。

TS 迁移批 TS-W5：对等 dedag_verify.py 的组件链，把进程内 py daemon 换成
`node apps/daemon-ts/src/cli.ts` 真子进程（零掩盖性 env）。探针面 =
hello 握手/query 代理/worktree 派生+merge+冲突/deploy 全链（instr+缓冲重传 wire 面）/
断连 offline 503/进程重启重连（真重启新 boot_nonce）。

跑法：cd coagentia && uv run python scratchpad/tsdaemon_verify.py [--keep]
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
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
from coagentia_server.db.engine import make_engine  # noqa: E402

PORT = 8931
SERVER_URL = f"http://127.0.0.1:{PORT}"
API = f"{SERVER_URL}/api"
REPO_ROOT = Path(__file__).resolve().parents[1]
NODE_CLI = REPO_ROOT / "apps" / "daemon-ts" / "src" / "cli.ts"

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""), flush=True)


async def poll(fn, timeout: float = 30.0, interval: float = 0.4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        got = fn() if not inspect.iscoroutinefunction(fn) else await fn()
        if asyncio.iscoroutine(got):
            got = await got
        if got:
            return got
        await asyncio.sleep(interval)
    return None


class Rest:
    def __init__(self, hc: httpx.AsyncClient) -> None:
        self.hc = hc

    async def req(self, method, path, body=None, headers=None, expect=None):
        r = await self.hc.request(method, API + path, json=body, headers=headers or {})
        if expect is not None:
            assert r.status_code == expect, f"{method} {path} -> {r.status_code}: {r.text[:300]}"
        return r

    async def post(self, path, body=None, expect=None, headers=None):
        return await self.req("POST", path, body, headers, expect)

    async def get(self, path, expect=None):
        return await self.req("GET", path, None, None, expect)


def _wt_row(engine, task_id: str) -> dict | None:
    from sqlalchemy import text

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM worktrees WHERE task_id = :t"), {"t": task_id}
        ).mappings().first()
        return dict(row) if row else None


def spawn_daemon(daemon_root: Path, log_path: Path) -> subprocess.Popen:
    """真 node daemon 子进程；stdout/stderr 落日志文件供诊断（探针不注任何编码 env）。"""
    logf = open(log_path, "ab")  # noqa: SIM115 — 随子进程生命周期持有
    return subprocess.Popen(
        ["node", str(NODE_CLI), "--server-url", SERVER_URL, "--api-key", H.API_KEY,
         "--data-root", str(daemon_root)],
        cwd=str(REPO_ROOT), stdout=logf, stderr=subprocess.STDOUT)


def kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                   capture_output=True, check=False)


async def _make_code_task(rest: Rest, engine, channel: str, project: str, *,
                          title: str, seed_file: str, branch_body: str) -> str:
    r = await rest.post(f"/channels/{channel}/messages", {
        "body": f"{title}（TS 探针任务）",
        "as_task": {"title": title, "writes_code": True, "project_id": project},
    }, expect=201)
    task_id = r.json()["task"]["id"]

    async def _active():
        row = _wt_row(engine, task_id)
        if row and row["status"] == "active" and row["path"] and Path(row["path"]).exists():
            return row
        return None
    row = await poll(_active)
    assert row, f"worktree 未派生/未落盘 task={task_id}"
    wt = Path(row["path"])
    (wt / seed_file).write_text(branch_body, encoding="utf-8")
    H.git(wt, "add", "--", seed_file)
    H.git(wt, "commit", "-m", f"probe: {title}")
    for to in ("in_progress", "in_review", "done"):
        await rest.post(f"/tasks/{task_id}/status", {"to": to}, expect=200)
    return task_id


async def run(ids: dict, keep: bool, db_url: str, repo: Path, daemon_root: Path,
              dlog: Path) -> None:
    engine = H.probe_engine(db_url)
    dproc = spawn_daemon(daemon_root, dlog)
    try:
        async with httpx.AsyncClient(timeout=30.0) as hc:
            rest = Rest(hc)
            channel = ids["channels"][0]["channel_id"]

            # ---------------- T0 hello 握手 → computer online ----------------
            async def _online():
                rows = (await rest.get("/computers", expect=200)).json()
                rows = rows.get("items") if isinstance(rows, dict) else rows
                me = next((c for c in rows if c["id"] == H.COMP_ID), None)
                return me if me and me["status"] == "connected" else None
            online = await poll(_online, timeout=60.0)  # 首连含 runtime 深探（codex 15s 上限）
            check("T0 node daemon hello 握手 → computer online", bool(online))
            if not online:
                return

            # ---------------- T1 query 代理：fs.tree ----------------
            r = await rest.get(f"/computers/{H.COMP_ID}/fs", expect=200)
            entries = r.json().get("entries", [])
            check("T1 fs.tree 根视图（win32 盘符经 node daemon 回流）",
                  any(e.get("name", "").endswith(":\\") for e in entries),
                  f"entries={len(entries)}")

            # ---------------- Project + 部署命令 ----------------
            (repo / "deploy.mjs").write_text(
                'console.log("ts-daemon deploy step 1");\n'
                'console.log("ts-daemon deploy step 2");\n'
                'console.log("done https://tsdemo.example.com/app");\n',
                encoding="utf-8")
            H.git(repo, "add", "--", "deploy.mjs")
            H.git(repo, "commit", "-m", "probe: add deploy script")
            r = await rest.post("/projects", {
                "name": "TsDemo", "repo_path": str(repo), "computer_id": H.COMP_ID,
                "deploy_command": "node deploy.mjs",
            }, expect=201)
            project = r.json()["id"]
            if not (r.json().get("deploy_command") or "").strip():
                await rest.req("PATCH", f"/projects/{project}",
                               {"deploy_command": "node deploy.mjs"}, expect=200)
            await rest.post(f"/channels/{channel}/projects", {"project_id": project},
                            expect=201)

            # ---------------- T2 worktree 派生 + 真 git 落盘 ----------------
            t1 = await _make_code_task(rest, engine, channel, project,
                                       title="TS改欢迎语A", seed_file="app.txt",
                                       branch_body="hello from ts-task-1\n")
            row = _wt_row(engine, t1)
            check("T2 worktree 派生落盘（node daemon 真 git ensure）",
                  bool(row and Path(row["path"]).exists()))
            t2 = await _make_code_task(rest, engine, channel, project,
                                       title="TS改欢迎语B", seed_file="app.txt",
                                       branch_body="hello from ts-task-2\n")

            # ---------------- T3 merge 全链（真 git --no-ff） ----------------
            r = await rest.post(f"/tasks/{t1}/merge", expect=202)
            check("T3a merge → 202 accepted", r.json().get("status") == "accepted")

            async def _merged():
                row = _wt_row(engine, t1)
                return row if row and row["status"] == "merged" and row["merge_commit"] else None
            row = await poll(_merged)
            main_body = H.git(repo, "show", "main:app.txt").stdout if row else ""
            merges = H.git(repo, "log", "--merges", "--oneline").stdout.strip()
            check("T3b merged + merge_commit + main 含内容 + --no-ff",
                  bool(row) and "ts-task-1" in main_body and bool(merges),
                  f"commit={row and row['merge_commit'][:8]}")

            # ---------------- T4 幂等 ----------------
            r = await rest.post(f"/tasks/{t1}/merge", expect=202)
            check("T4 已 merged 再触发 → 幂等 202 status=merged",
                  r.json().get("status") == "merged", r.text[:80])

            # ---------------- T5 真冲突 → abort + 冲突任务派回 ----------------
            before_ids = {t["id"] for t in (await rest.get(
                "/tasks?limit=200", expect=200)).json()["items"]}
            await rest.post(f"/tasks/{t2}/merge", expect=202)

            async def _conflicted():
                row = _wt_row(engine, t2)
                return row if row and row["status"] == "conflicted" else None
            row = await poll(_conflicted)
            clean = H.git(repo, "status", "--porcelain").stdout.strip()

            async def _conflict_task():
                items = (await rest.get("/tasks?limit=200", expect=200)).json()["items"]
                new = [t for t in items if t["id"] not in before_ids]
                return new or None
            ct = await poll(_conflict_task)
            ct0 = (ct or [None])[0]
            check("T5 真冲突 → conflicted + abort 主干净 + 冲突任务派回",
                  bool(row) and clean == "" and bool(ct0 and ct0["writes_code"]),
                  f"conflict_task={ct0 and ct0['id']}")

            # ------- T6 deploy 全链（instr + deploy.log/finished 缓冲重传 wire 面） -------
            r = await rest.post(f"/projects/{project}/deployments", {},
                                headers={"Idempotency-Key": "ts-probe-deploy-1"})
            check("T6a 触发部署受理", r.status_code in (200, 201, 202), f"status={r.status_code}")
            dep_id = r.json().get("id")

            async def _dep_done():
                rr = await rest.get(f"/deployments/{dep_id}", expect=200)
                j = rr.json()
                return j if j.get("status") in ("success", "failed") else None
            dep = await poll(_dep_done, timeout=60.0)
            check("T6b 部署 succeeded + URL 提取（deploy.run→log→finished 全 wire）",
                  bool(dep) and dep["status"] == "success"
                  and "tsdemo.example.com" in (dep.get("url") or ""),
                  f"status={dep and dep['status']} url={dep and dep.get('url')}")

            # ---------------- T7 杀 daemon → offline → merge 503 ----------------
            kill_tree(dproc.pid)

            async def _offline():
                rows = (await rest.get("/computers", expect=200)).json()
                rows = rows.get("items") if isinstance(rows, dict) else rows
                me = next((c for c in rows if c["id"] == H.COMP_ID), None)
                return me if me and me["status"] == "offline" else None
            check("T7a taskkill daemon → computer offline",
                  bool(await poll(_offline, timeout=90.0)))
            r = await rest.post(f"/tasks/{t2}/merge")
            check("T7b daemon 离线 merge → 503 DAEMON_OFFLINE",
                  r.status_code == 503 and r.json()["error"]["code"] == "DAEMON_OFFLINE",
                  f"status={r.status_code}")

            # ---------------- T8 重启 daemon → 重连 online（真重启新 boot_nonce） ----------------
            dproc2 = spawn_daemon(daemon_root, dlog)
            try:
                back = await poll(_online, timeout=60.0)
                check("T8 daemon 重启 → 重连 online（真重启对账面）", bool(back))
                if keep:
                    print(f"\n[KEEP] SERVER_URL={SERVER_URL} daemon_pid={dproc2.pid}", flush=True)
                    await asyncio.sleep(3600)
            finally:
                if not keep:
                    kill_tree(dproc2.pid)
    finally:
        engine.dispose()
        if not keep:
            kill_tree(dproc.pid)


def main() -> int:
    keep = "--keep" in sys.argv
    base = Path(tempfile.mkdtemp(prefix="tsdaemon_verify_"))
    db_url = f"sqlite:///{base / 'coagentia.db'}"
    data_root = str(base / "server-data")
    daemon_root = base / "daemon-data"
    dlog = base / "node-daemon.log"
    print(f"临时根：{base}", flush=True)

    H.migrate(db_url)
    engine = make_engine(url=db_url)
    ids = H.seed(engine)
    engine.dispose()
    repo = H.scratch_repo(base, "ts-demo-repo", seed_file="app.txt", seed_body="hello base\n")

    os.environ["M6A_DB_URL"] = db_url
    os.environ["M6A_DATA_ROOT"] = data_root

    proc = subprocess.Popen(
        ["uv", "run", "uvicorn", "m6a_appfactory:make_probe_app", "--factory",
         "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
        cwd=str(Path(__file__).resolve().parent),
        env={**os.environ}, stdout=open(base / "server.log", "ab"),
        stderr=subprocess.STDOUT)
    try:
        if not H.wait_port(f"{SERVER_URL}/api/workspace", timeout=40.0):
            print("server 未起", flush=True)
            return 1
        asyncio.run(run(ids, keep, db_url, repo, daemon_root, dlog))
    finally:
        if not keep:
            kill_tree(proc.pid)  # uv run 包装进程 terminate 杀不到 uvicorn 孙（cal3 孤儿教训）
            with contextlib.suppress(Exception):
                proc.wait(timeout=10)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n=== TS-daemon verify: {passed}/{total} "
          f"{'ALL PASS' if passed == total else 'HAS FAILURES'} ===", flush=True)
    if passed != total:
        print(f"daemon 日志：{dlog}", flush=True)
    out = REPO_ROOT / "docs" / "verify" / "TSDAEMON-VERIFY-results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {"passed": passed, "total": total,
         "results": [{"probe": n, "ok": ok, "detail": d} for n, ok, d in RESULTS]},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
