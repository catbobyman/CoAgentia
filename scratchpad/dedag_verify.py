"""DEDAG 实机 verify —— 委派模式 + 任务级 merge 全链：真 uvicorn + 真 daemon(FakeAdapter) + 真 git。

覆盖 DEDAG 批引入/改动的用户可见面（端到端真机）：
- **as_task 交付字段**（create_task 工具的 REST 同道）：writes_code+project_id 落任务行 →
  纯任务驱动派生（无画布门）→ daemon 真 git worktree 落盘。
- **任务级 merge**（B v1.6.1 §14）：202 受理 → daemon 真 git merge --no-ff → merged+merge_commit
  持久 + 频道系统消息；已 merged → 幂等 202；未 done → 422；真冲突 → abort 恢复主干 + 自动建
  冲突任务派回；daemon 离线 → 503。
- merge 域拒绝路径与 hub pending 语义的单元正确性由 test_task_merge.py（14 用例）证，
  本探针只跑真组件链（真 uvicorn/真 daemon/真 git，探针不带掩盖性 env——纪律 8）。

用法：uv run python scratchpad/dedag_verify.py [--keep]
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
_WT = models.tbl(models.Worktree)
_DIAG = models.tbl(models.DiagnosticEvent)


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""),
          flush=True)


async def poll(fn, timeout: float = 25.0, interval: float = 0.3):
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

    async def req(self, method, path, body=None, expect=None):
        r = None
        for _ in range(6):
            r = await self.hc.request(method, f"{API}{path}", json=body)
            if r.status_code != 500:
                break
            await asyncio.sleep(0.5)
        if expect is not None and r.status_code != expect:
            raise AssertionError(f"{method} {path} → {r.status_code} (want {expect}): {r.text}")
        return r

    async def post(self, path, body=None, expect=None):
        return await self.req("POST", path, body, expect)

    async def get(self, path, expect=None):
        return await self.req("GET", path, None, expect)


def _wt_row(engine, task_id: str) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(_WT).where(_WT.c.task_id == task_id)).mappings().first()
    return dict(row) if row else None


def _merge_diag_actions(engine) -> list[str]:
    with engine.connect() as c:
        rows = c.execute(
            select(_DIAG.c.payload).where(_DIAG.c.type == "agent.command")
        ).scalars().all()
    out = []
    for p in rows:
        if isinstance(p, dict) and p.get("action") == "task.merge":
            out.append(p.get("result") or p.get("status") or "?")
    return out


async def _make_code_task(rest: Rest, engine, channel: str, project: str, *,
                          title: str, seed_file: str, branch_body: str) -> tuple[str, Path]:
    """建 writes_code 任务 → 等真 worktree 落盘 → 在树里做一笔真提交 → 推到 done。"""
    r = await rest.post(f"/channels/{channel}/messages", {
        "body": f"{title}（探针任务）",
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
    return task_id, wt


async def run(ids: dict, keep: bool, db_url: str, repo: Path) -> None:
    daemon_root = Path(os.environ["M6_DAEMON_ROOT"])
    client, _ = H.build_daemon(SERVER_URL, daemon_root)
    daemon_task = asyncio.create_task(client.run())
    engine = H.probe_engine(db_url)
    try:
        await asyncio.wait_for(client.connected.wait(), timeout=15.0)
        check("P0 真 daemon 连上真 server", True)

        async with httpx.AsyncClient(timeout=30.0) as hc:
            rest = Rest(hc)
            channel = ids["channels"][0]["channel_id"]

            # ---------------- Project 建立与频道绑定（既有 M6 端点） ----------------
            r = await rest.post("/projects", {
                "name": "DedagDemo", "repo_path": str(repo), "computer_id": H.COMP_ID,
            }, expect=201)
            project = r.json()["id"]
            await rest.post(f"/channels/{channel}/projects", {"project_id": project},
                            expect=201)

            # ---------------- D1/D2 as_task 交付字段（create_task 同道） ----------------
            r = await rest.post(f"/channels/{channel}/messages", {
                "body": "缺 project 的代码任务",
                "as_task": {"writes_code": True},
            })
            check("D2 as_task writes_code 缺 project → 422",
                  r.status_code == 422
                  and r.json()["error"]["code"] == "VALIDATION_FAILED",
                  f"status={r.status_code}")

            # 两个冲突候选任务先后建齐（都基于初始 main，同文件不同内容 → 后合者真冲突）
            t1, _wt1 = await _make_code_task(
                rest, engine, channel, project,
                title="改欢迎语A", seed_file="app.txt", branch_body="hello from task-1\n")
            check("D1 as_task→任务→真 git worktree 落盘（纯任务驱动派生）",
                  _wt_row(engine, t1) is not None, f"task={t1}")
            t2, _wt2 = await _make_code_task(
                rest, engine, channel, project,
                title="改欢迎语B", seed_file="app.txt", branch_body="hello from task-2\n")

            # ---------------- D3 未 done 拒绝 ----------------
            r = await rest.post(f"/channels/{channel}/messages", {
                "body": "未完工任务", "as_task": {"writes_code": True, "project_id": project},
            }, expect=201)
            todo_task = r.json()["task"]["id"]
            r = await rest.post(f"/tasks/{todo_task}/merge")
            check("D3 未 done → 422 TASK_TRANSITION_INVALID",
                  r.status_code == 422
                  and r.json()["error"]["code"] == "TASK_TRANSITION_INVALID",
                  f"status={r.status_code}")

            # ---------------- D4/D5/D6 merge 全链（真 git --no-ff） ----------------
            r = await rest.post(f"/tasks/{t1}/merge", expect=202)
            check("D4 done 任务 merge → 202 accepted",
                  r.json().get("status") == "accepted", r.text)

            async def _merged():
                row = _wt_row(engine, t1)
                return row if row and row["status"] == "merged" and row["merge_commit"] else None
            row = await poll(_merged)
            check("D5a worktree merged + merge_commit 持久", bool(row),
                  f"commit={row and row['merge_commit'][:8]}")
            main_body = H.git(repo, "show", "main:app.txt").stdout
            merges = H.git(repo, "log", "--merges", "--oneline").stdout.strip()
            check("D5b 真 git：main 含任务内容 + --no-ff merge commit",
                  "task-1" in main_body and bool(merges), merges.splitlines()[0] if merges else "")

            msgs = (await rest.get(f"/channels/{channel}/messages?limit=100",
                                   expect=200)).json()["items"]
            check("D6 频道系统消息「已合并主干」",
                  any("已合并主干" in (m.get("body") or "") for m in msgs))

            # ---------------- D7 幂等 ----------------
            r = await rest.post(f"/tasks/{t1}/merge", expect=202)
            check("D7 已 merged 再触发 → 幂等 202 status=merged",
                  r.json().get("status") == "merged", r.text)

            # ---------------- D8/D9 真冲突 → abort + 冲突任务派回 ----------------
            before_ids = {t["id"] for t in (await rest.get(
                "/tasks?limit=200", expect=200)).json()["items"]}
            r = await rest.post(f"/tasks/{t2}/merge", expect=202)

            async def _conflicted():
                row = _wt_row(engine, t2)
                return row if row and row["status"] == "conflicted" else None
            row = await poll(_conflicted)
            check("D8a 同文件真冲突 → worktree conflicted", bool(row))
            clean = H.git(repo, "status", "--porcelain").stdout.strip()
            check("D8b abort 恢复：主干工作区干净", clean == "", clean[:60])

            async def _conflict_task():
                items = (await rest.get("/tasks?limit=200", expect=200)).json()["items"]
                new = [t for t in items if t["id"] not in before_ids]
                return new or None
            new_tasks = await poll(_conflict_task)
            ct = (new_tasks or [None])[0]
            check("D9 自动建冲突解决任务派回（writes_code 同 project）",
                  bool(ct) and ct["writes_code"] and ct["project_id"] == project,
                  f"task={ct and ct['id']}")

            # ---------------- D12 退役端点全部消失（V4） ----------------
            gone = []
            for method, path in (
                ("GET", f"/channels/{channel}/canvas"),
                ("POST", f"/channels/{channel}/decompose"),
                ("GET", "/templates"),
                ("POST", "/proposals/01K0X0X0X0X0X0X0X0X0X0X0X0/confirm"),
                ("POST", "/canvas-nodes/01K0X0X0X0X0X0X0X0X0X0X0X0/retry"),
            ):
                r = await rest.req(method, path, {} if method == "POST" else None)
                if method == "GET":
                    # 未知 GET 路径落 SPA catch-all 回 index.html（既有兜底）：
                    # 非 JSON = 无 API 处理器，即端点已移除。
                    ct = r.headers.get("content-type", "")
                    gone.append(r.status_code in (404, 405) or ct.startswith("text/html"))
                else:
                    gone.append(r.status_code in (404, 405))
            check("D12 退役端点（画布/拆解/模板/提案/retry）全部移除", all(gone),
                  f"gone={gone}")

            # ---------------- D10 diagnostic 留痕链 ----------------
            actions = _merge_diag_actions(engine)
            check("D10 diagnostic task.merge 留痕（running/merged/conflicted）",
                  len(actions) >= 4, f"entries={len(actions)}")

            # ---------------- D11 daemon 离线 → 503 ----------------
            # stop() 只置停机旗标不关 WS；须 shutdown 断连并等 server 侧感知（computers.status
            # → offline）后再打 merge，否则连接表仍在线 → 202（首跑实测）。
            client.stop()
            with __import__("contextlib").suppress(BaseException):
                await client.shutdown()
            daemon_task.cancel()
            with __import__("contextlib").suppress(BaseException):
                await daemon_task

            async def _offline():
                rows = (await rest.get("/computers", expect=200)).json()
                rows = rows.get("items") if isinstance(rows, dict) else rows
                me = next((c for c in rows if c["id"] == H.COMP_ID), None)
                return me if me and me["status"] == "offline" else None
            check("D11a daemon 断连 → computer offline", bool(await poll(_offline)))
            r = await rest.post(f"/tasks/{t2}/merge")
            check("D11 daemon 离线 → 503 DAEMON_OFFLINE",
                  r.status_code == 503
                  and r.json()["error"]["code"] == "DAEMON_OFFLINE",
                  f"status={r.status_code}")

            if keep:
                print(f"\n[KEEP] SERVER_URL={SERVER_URL}", flush=True)
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
    base = Path(tempfile.mkdtemp(prefix="dedag_verify_"))
    db_url = f"sqlite:///{base / 'coagentia.db'}"
    data_root = str(base / "server-data")
    daemon_root = base / "daemon"
    print(f"临时根：{base}", flush=True)

    H.migrate(db_url)
    engine = make_engine(url=db_url)
    ids = H.seed(engine)
    engine.dispose()
    repo = H.scratch_repo(base, "demo-repo", seed_file="app.txt", seed_body="hello base\n")

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
        asyncio.run(run(ids, keep, db_url, repo))
    finally:
        if not keep:
            proc.terminate()
            with __import__("contextlib").suppress(Exception):
                proc.wait(timeout=10)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n=== DEDAG verify: {passed}/{total} "
          f"{'ALL PASS' if passed == total else 'HAS FAILURES'} ===", flush=True)
    import json
    out = Path(__file__).resolve().parents[1] / "docs" / "verify" / "DEDAG-VERIFY-results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {"passed": passed, "total": total,
         "results": [{"probe": n, "ok": ok, "detail": d} for n, ok, d in RESULTS]},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
