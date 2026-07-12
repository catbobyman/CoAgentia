"""M6a 实机 verify 共享装置：临时库迁移+seed、scratch git 仓库、真 uvicorn 子进程、真 daemon-sim。

daemon-sim = 真 DaemonClient（真 websockets + 真心跳 + 真 git.py）+ FakeAdapter（只桩掉 Agent turn）。
worktree ensure/merge/cleanup/check 全走生产 git.py，对真 scratch 仓库执行真命令。
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from coagentia_daemon.adapter import FakeAdapter
from coagentia_daemon.buffer import TelemetryBuffer
from coagentia_daemon.client import DaemonClient
from coagentia_daemon.paths import DataPaths
from coagentia_server.db import models
from coagentia_server.db.engine import make_engine, sqlite_url
from coagentia_server.ledger.service import new_ulid, now_iso
from sqlalchemy import insert
from sqlalchemy.engine import Engine

API_KEY = "cak_m6a_probe_key"
KEY_HASH = hashlib.sha256(API_KEY.encode()).hexdigest()

WS_ID = "01K6WKSP00000000000000000A"
COMP_ID = "01K6CMPT00000000000000000A"
OWNER_ID = "01K6HMAN00000000000000000A"

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "apps" / "server" / "alembic.ini"


def _nid() -> str:
    time.sleep(0.002)  # +2ms 保证毫秒单调 ULID = 插入序
    return new_ulid()


def migrate(db_url: str) -> None:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(cfg, "head")


def seed(engine: Engine) -> dict:
    """最小 seed：workspace + computer(api_key) + owner + 2 agents + 2 channels(+canvas)。"""
    ids: dict = {"agents": [], "channels": []}
    with engine.begin() as c:
        c.execute(insert(models.Workspace.__table__).values(
            id=WS_ID, name="M6A", slug="m6a", created_at=now_iso()))
        c.execute(insert(models.Computer.__table__).values(
            id=COMP_ID, workspace_id=WS_ID, name="ProbeRig",
            api_key_hash=KEY_HASH, status="offline", created_at=now_iso()))
        c.execute(insert(models.Member.__table__).values(
            id=OWNER_ID, workspace_id=WS_ID, kind="human", name="Owner",
            role="owner", created_at=now_iso()))
        for name in ("Ada", "Ben", "Cid", "Dot"):
            mid = _nid()
            c.execute(insert(models.Member.__table__).values(
                id=mid, workspace_id=WS_ID, kind="agent", name=name,
                role="member", created_at=now_iso()))
            c.execute(insert(models.Agent.__table__).values(
                member_id=mid, computer_id=COMP_ID, runtime="claude_code",
                model="m", description="", home_path=f"~/.coagentia/agents/{mid}",
                status="offline", created_by_member_id=OWNER_ID))
            ids["agents"].append(mid)
        for cname in ("delivery", "conflict"):
            cid = _nid()
            c.execute(insert(models.Channel.__table__).values(
                id=cid, workspace_id=WS_ID, kind="channel", name=cname,
                dm_key=None, created_at=now_iso()))
            c.execute(insert(models.ChannelMember.__table__).values(
                channel_id=cid, member_id=OWNER_ID, joined_at=now_iso()))
            for mid in ids["agents"]:
                c.execute(insert(models.ChannelMember.__table__).values(
                    channel_id=cid, member_id=mid, joined_at=now_iso()))
            canvas_id = _nid()
            c.execute(insert(models.Canvas.__table__).values(
                id=canvas_id, workspace_id=WS_ID, channel_id=cid,
                baseline_hash="", updated_at=now_iso()))
            ids["channels"].append({"channel_id": cid, "canvas_id": canvas_id})
    return ids


def probe_engine(db_url: str) -> Engine:
    """probe 侧只读/拓扑写 engine：busy_timeout 拉高到 30s，避免与 uvicorn 并发写伪锁。"""
    from sqlalchemy import event
    engine = make_engine(url=db_url)

    @event.listens_for(engine, "connect")
    def _bump(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()
    return engine


def insert_system_topology(engine: Engine, canvas_id: str, upstream_node_ids: list[str], *,
                           add_check: bool = False, check_command: str = "git --version") -> dict:
    """原子插入 merge/check 系统节点 + 边（不经 REST → 无 CANVAS_NODE_ADDED 空扫触发的伪成功）。

    真实产品里系统节点随 landing 批一次性带边落地；per-node REST 会在建节点(无边)时被系统节点扫描
    判为非 blocked 而空成功——verify 用直插拓扑复刻"节点+边同事务"，交付执行仍全走真 daemon+真 git。
    """
    merge_id = _nid()
    check_id = _nid() if add_check else None
    with engine.begin() as c:
        c.execute(insert(models.CanvasNode.__table__).values(
            id=merge_id, canvas_id=canvas_id, kind="system",
            system_action="merge", system_status="idle", created_at=now_iso()))
        if add_check:
            c.execute(insert(models.CanvasNode.__table__).values(
                id=check_id, canvas_id=canvas_id, kind="system", system_action="check",
                command=check_command, system_status="idle", created_at=now_iso()))
        for up in upstream_node_ids:
            c.execute(insert(models.CanvasEdge.__table__).values(
                id=_nid(), canvas_id=canvas_id, from_node_id=up, to_node_id=merge_id))
        if add_check:
            c.execute(insert(models.CanvasEdge.__table__).values(
                id=_nid(), canvas_id=canvas_id, from_node_id=merge_id, to_node_id=check_id))
    return {"merge": merge_id, "check": check_id}


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    r = subprocess.run(["git", "-c", "core.quotepath=false", "-C", str(repo), *args],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", check=False)
    if check and r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed ({r.returncode}): {r.stdout}\n{r.stderr}")
    return r


def scratch_repo(root: Path, name: str, *, seed_file: str, seed_body: str) -> Path:
    repo = root / name
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main", str(repo)], capture_output=True,
                   text=True, encoding="utf-8", check=True)
    git(repo, "config", "user.name", "CoAgentia Probe")
    git(repo, "config", "user.email", "probe@coagentia.local")
    git(repo, "config", "core.autocrlf", "false")
    (repo / seed_file).write_text(seed_body, encoding="utf-8")
    git(repo, "add", "--", seed_file)
    git(repo, "commit", "-m", "seed")
    return repo


async def _stub_runner(argv):  # runtime 探测桩：一律"未安装"，跳过真 CLI + codex 深探
    return (1, "", "")


def build_daemon(server_url: str, daemon_root: Path) -> tuple[DaemonClient, DataPaths]:
    paths = DataPaths(daemon_root)
    paths.ensure_dirs()
    client = DaemonClient(
        server_url=server_url, api_key=API_KEY, adapter=FakeAdapter(),
        buffer=TelemetryBuffer(paths), paths=paths,
        os_name="Windows probe", arch="x64", runner=_stub_runner,
        heartbeat_sec=25.0, pong_timeout=10.0,
    )
    return client, paths


def wait_port(url: str, timeout: float = 30.0) -> bool:
    import httpx
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = httpx.get(url, timeout=2.0)
            if r.status_code < 500:
                return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
