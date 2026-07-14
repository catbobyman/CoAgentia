"""死壳修复（DEADSHELL-FIX-PLAN F1–F13）浏览器实证用：真 server + seed 数据，跑在 8787。

用法：uv run python scratchpad/deadshell_verify.py
然后另开 web dev（pnpm --filter @coagentia/web dev，代理 /api→8787），浏览器开 http://localhost:5173。

seed = workspace + computer + owner + 2 agents + #all/#build 两频道 + #build 内若干消息（供 F1 未读→已读实证）。
daemon 离线（未接 daemon-sim）→ F2 生命周期走 503 toast 路径（计划接受的验证路径）；F3/F4/F5… 走真端点。
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import m6a_harness as H  # noqa: E402
from coagentia_server.db import models  # noqa: E402
from coagentia_server.db.engine import make_engine  # noqa: E402
from coagentia_server.ledger.service import new_ulid, now_iso  # noqa: E402
from sqlalchemy import insert  # noqa: E402

PORT = 8787


def _nid() -> str:
    time.sleep(0.002)
    return new_ulid()


def seed_messages(engine, channel_id: str, owner_id: str, agent_id: str) -> None:
    """#build 里塞几条消息（owner + agent 交替），供 F1 未读→已读、F5 hover 动作实证。"""
    lines = [
        (owner_id, "帮我把登录页做出来"),
        (agent_id, "收到，开始拆解交付。"),
        (owner_id, "顺便加上深色模式切换"),
        (agent_id, "已完成，请查看预览。"),
        (owner_id, "看着不错，合并吧"),
    ]
    with engine.begin() as c:
        for author, body in lines:
            c.execute(insert(models.Message.__table__).values(
                id=_nid(), workspace_id=H.WS_ID, channel_id=channel_id,
                thread_root_id=None, author_member_id=author, kind="user",
                card_kind=None, card_ref=None, body=body, created_at=now_iso(),
            ))


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="deadshell_verify_"))
    db_url = f"sqlite:///{(tmp / 'app.db').as_posix()}"
    data_root = str(tmp / "data")
    print(f"[seed] db={db_url}")
    H.migrate(db_url)
    engine = make_engine(url=db_url)
    ids = H.seed(engine)
    # seed 的频道叫 delivery/conflict；补一个 #build（默认活跃频道）+ #all，塞消息。
    owner = H.OWNER_ID
    agent0 = ids["agents"][0]
    with engine.begin() as c:
        for cname in ("all", "build"):
            cid = _nid()
            c.execute(insert(models.Channel.__table__).values(
                id=cid, workspace_id=H.WS_ID, kind="channel", name=cname,
                dm_key=None, created_at=now_iso()))
            c.execute(insert(models.ChannelMember.__table__).values(
                channel_id=cid, member_id=owner, joined_at=now_iso()))
            for mid in ids["agents"]:
                c.execute(insert(models.ChannelMember.__table__).values(
                    channel_id=cid, member_id=mid, joined_at=now_iso()))
            c.execute(insert(models.Canvas.__table__).values(
                id=_nid(), workspace_id=H.WS_ID, channel_id=cid,
                baseline_hash="", updated_at=now_iso()))
            if cname == "build":
                build_id = cid
    seed_messages(engine, build_id, owner, agent0)
    print(f"[seed] owner={owner} agent0={agent0} #build={build_id}")
    print(f"[seed] agents={ids['agents']}")

    import os
    os.environ["M6A_DB_URL"] = db_url
    os.environ["M6A_DATA_ROOT"] = data_root

    import uvicorn
    from coagentia_server.app import create_app
    app = create_app(engine=make_engine(url=db_url), data_root=data_root)
    print(f"[server] http://127.0.0.1:{PORT}  (Ctrl+C 停)")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
