"""M7a 实机 verify（= PRD M7a 预览链出口）：真 uvicorn + 真 websockets daemon-sim（真 git.py +
**真 PreviewRunner 起真 dev server 子进程**）+ 真 scratch 仓库。

场景（§9a #7）：
  P1 交付 → 预览面板打开 → 健康检查 → iframe 真实 HTTP 200（真 http.server 起在 worktree）。
  P2 并排第二任务预览（端口互异，注册表唯一性）。
  P3 ensure+touch 幂等（二次 POST=touch 同会话）。
  P4 idle 超时自动回收（backdate → 回收扫描 → recycled + dev server 被杀端口不可达）。
  P5 坏 dev_command → failed 携 fail_log_tail。

用法：uv run python scratchpad/m7a_verify.py [--keep]
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m6a_harness as H  # noqa: E402
from coagentia_server.db import models  # noqa: E402
from coagentia_server.db.engine import make_engine  # noqa: E402
from coagentia_server.ledger.service import format_iso  # noqa: E402
from sqlalchemy import update  # noqa: E402

PY = sys.executable
GOOD_DEV_CMD = f'"{PY}" -m http.server %PORT% --bind 127.0.0.1'
BAD_DEV_CMD = f'"{PY}" -m coagentia_no_such_module_xyz %PORT%'


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

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f"  — {detail}" if detail else ""), flush=True)


async def poll(fn, timeout: float = 30.0, interval: float = 0.4):
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

    async def post(self, path, body=None, expect=None):
        r = None
        for _ in range(6):
            r = await self.hc.post(f"{API}{path}", json=body)
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


def iframe_ok(port: int) -> bool:
    """真 dev server 可达性 = iframe 数据源 http://127.0.0.1:{port}/ 返回 HTTP 200。"""
    try:
        r = httpx.get(f"http://127.0.0.1:{port}/", timeout=2.0)
        return r.status_code == 200
    except Exception:
        return False


def port_unreachable(port: int) -> bool:
    import socket

    s = socket.socket()
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", port))
        return False
    except OSError:
        return True
    finally:
        s.close()


async def wait_worktree(rest: Rest, task_id: str, timeout: float = 40.0) -> dict | None:
    """轮询 server 侧 worktree 行（TaskDetail.worktree）——预览端点实际依据（daemon worktree.status
    上报后落库；物理目录先于行存在，故轮询行比轮询磁盘更稳）。"""
    async def _w():
        # TaskDetail.worktree 是根层派生字段（与 task/usage 平级，非 task 子字段）。
        return (await rest.get(f"/tasks/{task_id}")).json().get("worktree")

    return await poll(_w, timeout=timeout)


async def preview_row(rest: Rest, task_id: str) -> dict | None:
    r = await rest.get(f"/tasks/{task_id}/preview")
    if r.status_code != 200:
        return None
    return r.json()


async def wait_preview_status(rest: Rest, task_id: str, want: str, timeout: float = 30.0):
    async def _s():
        row = await preview_row(rest, task_id)
        return row if (row and row.get("status") == want) else None

    return await poll(_s, timeout=timeout)


async def run(ids: dict, repos: dict, keep: bool, db_url: str) -> None:
    daemon_root = Path(os.environ["M6_DAEMON_ROOT"])
    client, _ = H.build_daemon(SERVER_URL, daemon_root)
    daemon_task = asyncio.create_task(client.run())
    try:
        await asyncio.wait_for(client.connected.wait(), timeout=15.0)
        check("P0.1 daemon-sim 真 websockets 连上真 server", True)
        paths = client.git.paths

        async with httpx.AsyncClient(timeout=30.0) as hc:
            rest = Rest(hc)
            # Project：好命令（P1–P4）绑 delivery 频道、坏命令（P5）绑 conflict 频道。
            good = (await rest.post("/projects", {
                "name": "PreviewGood", "repo_path": str(repos["good"]),
                "computer_id": H.COMP_ID, "dev_command": GOOD_DEV_CMD,
                "preview_idle_min": 1}, expect=201)).json()
            await rest.post(f"/channels/{ids['channels'][0]['channel_id']}/projects",
                            {"project_id": good["id"]}, expect=201)
            bad = (await rest.post("/projects", {
                "name": "PreviewBad", "repo_path": str(repos["bad"]),
                "computer_id": H.COMP_ID, "dev_command": BAD_DEV_CMD}, expect=201)).json()
            await rest.post(f"/channels/{ids['channels'][1]['channel_id']}/projects",
                            {"project_id": bad["id"]}, expect=201)
            check("P0.2 两 Project 建立并绑定频道（好/坏 dev_command）", True)

            gcanvas = ids["channels"][0]["canvas_id"]
            bcanvas = ids["channels"][1]["canvas_id"]

            # ---- P1：交付 → 预览面板 → 健康检查 → iframe HTTP 200 ----
            n1 = await make_node(rest, gcanvas, {
                "title": "预览任务一", "kind": "agent", "writes_code": True,
                "project_id": good["id"]})
            t1 = n1["task_id"]
            wt1 = paths.worktree_path(good["id"], t1)
            got = await wait_worktree(rest, t1, timeout=40.0)
            check("P1.1 writes_code 任务激活联动真 git 派生 worktree",
                  bool(got) and wt1.exists(), wt1.name)

            r = await rest.post(f"/tasks/{t1}/preview", expect=201)
            check("P1.2 POST /preview = 201 建 starting 会话", r.json()["status"] == "starting")

            row1 = await wait_preview_status(rest, t1, "running", timeout=40.0)
            port1 = row1["port"] if row1 else None
            check("P1.3 daemon 真起 dev server → 健康检查 → running 携 port", bool(port1),
                  f"port={port1}")

            ok200 = (await poll(lambda: _async_true(iframe_ok(port1)), timeout=15.0)
                     if port1 else False)
            check("P1.4 iframe 数据源真实可达 HTTP 200（真 http.server 在 worktree）", bool(ok200),
                  f"http://127.0.0.1:{port1}/")

            # ---- P2：并排第二任务预览（端口互异）----
            n2 = await make_node(rest, gcanvas, {
                "title": "预览任务二", "kind": "agent", "writes_code": True,
                "project_id": good["id"]})
            t2 = n2["task_id"]
            await wait_worktree(rest, t2, timeout=40.0)
            await rest.post(f"/tasks/{t2}/preview", expect=201)
            row2 = await wait_preview_status(rest, t2, "running", timeout=40.0)
            port2 = row2["port"] if row2 else None
            distinct = bool(port1) and bool(port2) and port1 != port2
            both200 = bool(port2) and iframe_ok(port1) and iframe_ok(port2)  # noqa: E501
            check("P2.1 并排双预览端口互异（注册表唯一性）", distinct, f"{port1} vs {port2}")
            check("P2.2 两预览 iframe 同时 HTTP 200", both200)

            # ---- P3：ensure+touch 幂等（二次 POST=touch 同会话）----
            r_touch = await rest.post(f"/tasks/{t1}/preview", expect=200)
            same = r_touch.json()["id"] == row1["id"]
            check("P3.1 二次 POST = 200 touch 同会话（ensure+touch 幂等）", same,
                  f"200 same_session={same}")

            # ---- P4：idle 超时自动回收（backdate → 回收扫描 → recycled + 进程被杀）----
            pengine = H.probe_engine(db_url)
            stale = format_iso(datetime.now(UTC) - timedelta(minutes=10))
            with pengine.begin() as c:
                c.execute(update(models.PreviewSession.__table__)
                          .where(models.PreviewSession.__table__.c.id == row1["id"])
                          .values(last_active_at=stale))
            pengine.dispose()
            recy = await wait_preview_status(rest, t1, "recycled", timeout=20.0)
            check("P4.1 idle 超时 → 回收扫描下发 stop → recycled", bool(recy))
            killed = port1 and await poll(
                lambda: _async_true(port_unreachable(port1)), timeout=10.0)
            check("P4.2 回收后 dev server 子进程被杀（端口不可达）", bool(killed), f"port={port1}")

            # ---- P5：坏 dev_command → failed 携 fail_log_tail ----
            nb = await make_node(rest, bcanvas, {
                "title": "坏命令任务", "kind": "agent", "writes_code": True,
                "project_id": bad["id"]})
            tb = nb["task_id"]
            wtb = paths.worktree_path(bad["id"], tb)
            wtb_row = await wait_worktree(rest, tb, timeout=40.0)
            check("P5.0 坏命令任务 worktree 就位", bool(wtb_row) and wtb.exists(), wtb.name)
            await rest.post(f"/tasks/{tb}/preview", expect=201)
            frow = await wait_preview_status(rest, tb, "failed", timeout=30.0)
            tail = (frow or {}).get("fail_log_tail") or ""
            check("P5.1 坏 dev_command → failed", bool(frow))
            check("P5.2 failed 携 fail_log_tail（进程输出尾）", "No module named" in tail,
                  tail.strip()[:60])
    finally:
        if not keep:
            client.stop()
            await client.shutdown()  # 逐个杀活跃预览子进程（清洁关闭无孤儿）
            daemon_task.cancel()
            with __import__("contextlib").suppress(BaseException):
                await daemon_task


async def _async_true(v: bool) -> bool:
    return v


def main() -> int:
    keep = "--keep" in sys.argv
    base = Path(tempfile.mkdtemp(prefix="m7a_verify_"))
    db_path = base / "coagentia.db"
    db_url = f"sqlite:///{db_path}"
    data_root = base / "server-data"
    daemon_root = base / "daemon"
    repos_root = base / "repos"

    print(f"临时根：{base}", flush=True)
    H.migrate(db_url)
    engine = make_engine(url=db_url)
    ids = H.seed(engine)
    engine.dispose()

    repos = {
        "good": H.scratch_repo(repos_root, "good-repo",
                               seed_file="index.html", seed_body="<h1>preview ok</h1>\n"),
        "bad": H.scratch_repo(repos_root, "bad-repo",
                              seed_file="README.md", seed_body="seed\n"),
    }

    env = dict(os.environ, M6A_DB_URL=db_url, M6A_DATA_ROOT=str(data_root),
               M6_DAEMON_ROOT=str(daemon_root),
               PYTHONPATH=str(Path(__file__).resolve().parent))
    os.environ["M6_DAEMON_ROOT"] = str(daemon_root)

    proc = subprocess.Popen(
        ["uv", "run", "uvicorn", "m7a_appfactory:make_probe_app", "--factory",
         "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
        cwd=str(Path(__file__).resolve().parents[1]), env=env)
    try:
        if not H.wait_port(f"{API}/projects", timeout=40.0):
            print("!! server 未就绪", flush=True)
            return 2
        asyncio.run(run(ids, repos, keep, db_url))
    finally:
        passed = sum(1 for _, ok, _ in RESULTS if ok)
        total = len(RESULTS)
        print(f"\n=== M7a 实机 verify：{passed}/{total} "
              f"{'ALL PASS' if passed == total else 'HAS FAILURES'} ===", flush=True)
        (base / "results.json").write_text(
            json.dumps([{"name": n, "pass": ok, "detail": d} for n, ok, d in RESULTS],
                       ensure_ascii=False, indent=2), encoding="utf-8")
        if keep:
            print(f"\n[--keep] server pid={proc.pid} port={PORT} 保留；临时根={base}", flush=True)
        else:
            with __import__("contextlib").suppress(Exception):
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True)
            with __import__("contextlib").suppress(Exception):
                proc.wait(timeout=10)
    return 0 if all(ok for _, ok, _ in RESULTS) else 1


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    raise SystemExit(main())
