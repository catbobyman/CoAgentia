"""M6a 出口 #11 实机 verify：真 uvicorn + 真 websockets daemon-sim（真 git.py）+ 真 scratch 仓库。

场景 A（交付链）：两并行 writes_code 任务 → 各自 worktree 交付 → merge 节点 --no-ff 合并成功
                （merge_commit 持久）→ check 节点绿；顺带 Diff 端点。
场景 B（冲突派回）：两任务改同一行 → 第二个 merge 冲突 → 自动建"解决冲突"任务派回 →
                解决 → retry → 合并成功。

用法：uv run python scratchpad/m6a_verify.py [--keep]
  --keep：verify 后保留 server/repos/库不清理（供浏览器截图），打印句柄信息。
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
    RESULTS.append((name, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f"  — {detail}" if detail else ""), flush=True)


async def poll(fn, timeout: float = 30.0, interval: float = 0.4):
    """轮询 async predicate 直至真值或超时；返回最后一次结果。"""
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

    async def post(self, path: str, body: dict | None = None, expect: int | None = None):
        r = None
        for _ in range(6):  # 瞬时 500（残留 DB 锁）退避重试
            r = await self.hc.post(f"{API}{path}", json=body)
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


async def make_node(rest: Rest, canvas_id: str, body: dict) -> dict:
    r = await rest.post(f"/canvases/{canvas_id}/nodes", body, expect=201)
    return r.json()["node"]


async def add_edge(rest: Rest, canvas_id: str, frm: str, to: str) -> None:
    await rest.post(f"/canvases/{canvas_id}/edges",
                    {"from_node_id": frm, "to_node_id": to}, expect=201)


async def drive_done(rest: Rest, task_id: str, agent_mid: str) -> None:
    """把 L2 writes_code 任务经 claim→handoff→in_review→done 推到终态。

    冲突派回任务已带 owner（承原任务 owner）→ claim 会 409 CLAIM_RACE；此时改直推
    todo→in_progress（合法边），兼容已认领态。
    """
    r = await rest.post(f"/tasks/{task_id}/claim")
    if r.status_code not in (200, 409):
        raise AssertionError(f"claim {task_id} → {r.status_code}: {r.text}")
    t = (await rest.get(f"/tasks/{task_id}")).json()["task"]
    if t["status"] == "todo":
        await rest.post(f"/tasks/{task_id}/status", {"to": "in_progress"}, expect=200)
    handoff = {
        "kind": "task_handoff",
        "body": {
            "version": "coagentia.task-handoff.v1",
            "from_member": agent_mid,
            "to_member": H.OWNER_ID,
            "deliverables": [{"path": "D:/x", "kind": "file"}],
            "evidence": [{"type": "test", "ref": "probe", "conclusion": "green"}],
            "open_risks": [],
            "verify_plan": "re-run",
            "review_verdict": "pass",
        },
    }
    await rest.post(f"/tasks/{task_id}/contracts", handoff, expect=201)
    await rest.post(f"/tasks/{task_id}/status", {"to": "in_review"}, expect=200)
    await rest.post(f"/tasks/{task_id}/status", {"to": "done"}, expect=200)


async def node_status(rest: Rest, channel_id: str, node_id: str) -> str | None:
    r = await rest.get(f"/channels/{channel_id}/canvas")
    for n in r.json()["nodes"]:
        if n["id"] == node_id:
            return n.get("system_status")
    return None


async def find_new_agent_node(rest: Rest, channel_id: str, known_ids: set[str]) -> dict | None:
    """找画布上新出现的 agent 节点（= 自动派回的解决冲突任务，节点行无 title 字段）。"""
    r = await rest.get(f"/channels/{channel_id}/canvas")
    for n in r.json()["nodes"]:
        if n.get("kind") == "agent" and n["id"] not in known_ids:
            return n
    return None


async def scenario_delivery(rest: Rest, paths, ch: dict, project_id: str, repo: Path,
                            agents: list[str], pengine) -> None:
    print("\n=== 场景 A：交付链（双并行 → worktree 交付 → merge --no-ff → check 绿）===", flush=True)
    cid, canvas = ch["channel_id"], ch["canvas_id"]
    na = await make_node(rest, canvas, {"title": "实现 A", "kind": "agent",
                                        "writes_code": True, "project_id": project_id})
    nb = await make_node(rest, canvas, {"title": "实现 B", "kind": "agent",
                                        "writes_code": True, "project_id": project_id})
    topo = H.insert_system_topology(pengine, canvas, [na["id"], nb["id"]], add_check=True)
    nm = {"id": topo["merge"]}
    nc = {"id": topo["check"]}
    check("A1 建两 writes_code 任务 + merge/check 系统节点 + 3 边（拓扑原子落地）", True,
          f"TA={na['task_id'][:8]} TB={nb['task_id'][:8]}")

    ta, tb = na["task_id"], nb["task_id"]
    wta = paths.worktree_path(project_id, ta)
    wtb = paths.worktree_path(project_id, tb)
    got = await poll(lambda: _both_exist(wta, wtb), timeout=30.0)
    check("A2 daemon 真 git 派生两 worktree（激活联动 ensure）", bool(got),
          f"{wta.name} / {wtb.name}")

    # 模拟 Agent 在各自 worktree 交付（真 commit，改不同文件 → 无冲突）。
    for wt, fn in ((wta, "fileA.txt"), (wtb, "fileB.txt")):
        (wt / fn).write_text(f"{fn} delivered\n", encoding="utf-8")
        H.git(wt, "add", "--", fn)
        H.git(wt, "commit", "-m", f"deliver {fn}")
    check("A3 两 worktree 真交付提交（分支 coagentia/task-*）", True, "fileA/fileB")

    await drive_done(rest, ta, agents[0])
    await drive_done(rest, tb, agents[1])
    check("A4 两任务 claim→handoff→in_review→done（T7 门放行）", True)

    nm_ok = await poll(lambda: _is_status(rest, cid, nm["id"], "success"), timeout=45.0)
    check("A5 merge 系统节点自动触发并 success（DAG 序 --no-ff）", bool(nm_ok))

    # 主干应有两个 --no-ff merge commit（各带 2 parent）。
    merges = H.git(repo, "rev-list", "--merges", "--count", "HEAD").stdout.strip()
    check("A6 主干产生 2 个真 merge commit", merges == "2", f"count={merges}")

    # merge_commit 持久（TaskDetail.worktree 读面）。
    da = (await rest.get(f"/tasks/{ta}")).json()
    mc = (da.get("worktree") or {}).get("merge_commit")
    check("A7 worktrees.merge_commit 持久到 TaskDetail", bool(mc), f"{(mc or '')[:12]}")

    nc_ok = await poll(lambda: _is_status(rest, cid, nc["id"], "success"), timeout=45.0)
    check("A8 check 系统节点在主工作区跑并 success（git --version）", bool(nc_ok))

    # Diff 端点经 daemon 真 git.diff 代理：base=main（合并后）与 TA 分支的逐文件 unified patch。
    r = await rest.get(f"/tasks/{ta}/diff?base=main")
    ok = False
    detail = f"status={r.status_code}"
    if r.status_code == 200:
        payload = r.json()
        files = [f["path"] for f in payload.get("files", [])]
        has_patch = any(f.get("patch") for f in payload.get("files", []))
        ok = len(files) >= 1 and has_patch
        detail = f"files={files} has_patch={has_patch}"
    check("A9 GET /tasks/{id}/diff 经 daemon 真 git.diff 返回逐文件 unified patch", ok, detail)


async def scenario_conflict(rest: Rest, paths, ch: dict, project_id: str, repo: Path,
                            agents: list[str], pengine) -> None:
    print("\n=== 场景 B：冲突派回（同行冲突 → 派回解决冲突任务 → retry 合并成功）===", flush=True)
    cid, canvas = ch["channel_id"], ch["canvas_id"]
    nc = await make_node(rest, canvas, {"title": "改 C", "kind": "agent",
                                        "writes_code": True, "project_id": project_id})
    nd = await make_node(rest, canvas, {"title": "改 D", "kind": "agent",
                                        "writes_code": True, "project_id": project_id})
    topo = H.insert_system_topology(pengine, canvas, [nc["id"], nd["id"]], add_check=False)
    nm = {"id": topo["merge"]}
    tc, td = nc["task_id"], nd["task_id"]
    wtc = paths.worktree_path(project_id, tc)
    wtd = paths.worktree_path(project_id, td)
    got = await poll(lambda: _both_exist(wtc, wtd), timeout=30.0)
    check("B1 派生两 worktree", bool(got))

    # 两任务改同一行 → 制造冲突。
    for wt, val in ((wtc, "from-C\n"), (wtd, "from-D\n")):
        (wt / "conflict.txt").write_text(val, encoding="utf-8")
        H.git(wt, "add", "--", "conflict.txt")
        H.git(wt, "commit", "-m", "edit conflict.txt")
    check("B2 两 worktree 改同一行 conflict.txt（真提交）", True)

    await drive_done(rest, tc, agents[2])
    await drive_done(rest, td, agents[3])
    check("B3 两任务推到 done", True)

    # merge 节点应 failed（第二个 merge 冲突），并自动建"解决冲突"任务。
    nm_failed = await poll(lambda: _is_status(rest, cid, nm["id"], "failed"), timeout=45.0)
    check("B4 冲突致 merge 节点 failed", bool(nm_failed))

    known_ids = {nc["id"], nd["id"]}
    conflict_node = await poll(lambda: find_new_agent_node(rest, cid, known_ids), timeout=15.0)
    check("B5 自动建「解决冲突」任务派回（新 agent 节点 + 连边→merge）", bool(conflict_node),
          f"task={(conflict_node or {}).get('task_id','?')[:8]}")

    if not conflict_node:
        return
    # 解决：在 D 的 worktree 里 merge main 并解决冲突（使其可被主干 --no-ff 合并）。
    H.git(wtd, "merge", "main", check=False)  # 冲突
    (wtd / "conflict.txt").write_text("from-C + from-D (resolved)\n", encoding="utf-8")
    H.git(wtd, "add", "--", "conflict.txt")
    H.git(wtd, "commit", "--no-edit")
    check("B6 在冲突 worktree 解决并提交（merge main + resolve）", True)

    # 冲突任务推到 done → merge 节点解除 blocked → retry。
    ct = conflict_node["task_id"]
    await drive_done(rest, ct, agents[3])
    check("B7 解决冲突任务推到 done", True)

    r = await rest.post(f"/canvas-nodes/{nm['id']}/retry")
    check("B8 POST /canvas-nodes/{id}/retry 接受（仅 failed 可 retry）",
          r.status_code == 202, f"status={r.status_code}")

    nm_ok = await poll(lambda: _is_status(rest, cid, nm["id"], "success"), timeout=45.0)
    check("B9 retry 后 merge 节点 success（冲突解决 → 合并成功）", bool(nm_ok))


async def _both_exist(a: Path, b: Path):
    return a.is_dir() and b.is_dir()


async def _is_status(rest: Rest, cid: str, node_id: str, want: str):
    return (await node_status(rest, cid, node_id)) == want


async def run(paths, ids: dict, repos: dict, keep: bool, pengine) -> None:
    client, _ = H.build_daemon(SERVER_URL, paths.root)
    daemon_task = asyncio.create_task(client.run())
    try:
        await asyncio.wait_for(client.connected.wait(), timeout=15.0)
        check("D0 daemon-sim 真 websockets 连上真 server（hello/ack）", True)
        async with httpx.AsyncClient(timeout=20.0) as hc:
            rest = Rest(hc)
            # 建 project 并绑定频道（repo_path 真 git 仓库校验）。
            pa = (await rest.post("/projects", {
                "name": "DeliveryRepo", "repo_path": str(repos["delivery"]),
                "computer_id": H.COMP_ID}, expect=201)).json()
            await rest.post(f"/channels/{ids['channels'][0]['channel_id']}/projects",
                            {"project_id": pa["id"]}, expect=201)
            pb = (await rest.post("/projects", {
                "name": "ConflictRepo", "repo_path": str(repos["conflict"]),
                "computer_id": H.COMP_ID}, expect=201)).json()
            await rest.post(f"/channels/{ids['channels'][1]['channel_id']}/projects",
                            {"project_id": pb["id"]}, expect=201)
            check("D1 建 Project + 绑定频道（repo_path 是 git 仓库校验）", True)

            await scenario_delivery(rest, client.git.paths, ids["channels"][0], pa["id"],
                                    repos["delivery"], ids["agents"], pengine)
            await scenario_conflict(rest, client.git.paths, ids["channels"][1], pb["id"],
                                    repos["conflict"], ids["agents"], pengine)
    finally:
        if not keep:
            client.stop()
            await client.shutdown()
            daemon_task.cancel()
            # CancelledError 是 BaseException，suppress(Exception) 接不住 → 收尾假 traceback。
            with __import__("contextlib").suppress(BaseException):
                await daemon_task


def main() -> int:
    keep = "--keep" in sys.argv
    base = Path(tempfile.mkdtemp(prefix="m6a_verify_"))
    db_path = base / "coagentia.db"
    db_url = H.sqlite_url(db_path)
    data_root = base / "server-data"
    daemon_root = base / "daemon"
    repos_root = base / "repos"

    print(f"临时根：{base}", flush=True)
    H.migrate(db_url)
    engine = H.make_engine(url=db_url)
    ids = H.seed(engine)
    engine.dispose()

    repos = {
        "delivery": H.scratch_repo(repos_root, "delivery-repo",
                                   seed_file="README.md", seed_body="seed\n"),
        "conflict": H.scratch_repo(repos_root, "conflict-repo",
                                   seed_file="conflict.txt", seed_body="base\n"),
    }

    env = dict(os.environ, M6A_DB_URL=db_url, M6A_DATA_ROOT=str(data_root),
               PYTHONPATH=str(Path(__file__).resolve().parent))
    proc = subprocess.Popen(
        ["uv", "run", "uvicorn", "m6a_appfactory:make_probe_app", "--factory",
         "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
        cwd=str(Path(__file__).resolve().parents[1]), env=env)
    try:
        if not H.wait_port(f"{SERVER_URL}/api/projects", timeout=40.0):
            print("!! server 未就绪", flush=True)
            return 2
        from coagentia_daemon.paths import DataPaths
        paths = DataPaths(daemon_root)
        paths.ensure_dirs()
        pengine = H.probe_engine(db_url)
        try:
            asyncio.run(run(paths, ids, repos, keep, pengine))
        finally:
            pengine.dispose()
    finally:
        passed = sum(1 for _, ok, _ in RESULTS if ok)
        total = len(RESULTS)
        print(f"\n=== M6a 实机 verify：{passed}/{total} "
              f"{'ALL PASS' if passed == total else 'HAS FAILURES'} ===", flush=True)
        (base / "results.json").write_text(
            json.dumps([{"name": n, "pass": ok, "detail": d} for n, ok, d in RESULTS],
                       ensure_ascii=False, indent=2), encoding="utf-8")
        if keep:
            print(f"\n[--keep] server pid={proc.pid} port={PORT} 保留中；"
                  f"db={db_url}\n临时根={base}\n结束后请手动 kill。", flush=True)
        else:
            # uv run → uvicorn 是子进程树；terminate 只杀 uv 包装器，须 taskkill /T 杀整树。
            with __import__("contextlib").suppress(Exception):
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True)
            with __import__("contextlib").suppress(Exception):
                proc.wait(timeout=10)
    return 0 if all(ok for _, ok, _ in RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
