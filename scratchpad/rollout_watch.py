"""DEDAG 实机铺开观察器：轮询频道消息/任务/worktree/diagnostic（显式 UTF-8，避 GBK 管道）。

用法：uv run python scratchpad/rollout_watch.py [--since N] [--tasks] [--wt] [--diag]
"""

from __future__ import annotations

import sys

import httpx

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

API = "http://127.0.0.1:8787/api"
RT = "01KXHXW27P1P1D5G94H1V5HA04"  # realtest 频道
NAMES = {
    "01KXFC8QTTQ003YWPVVV3XQZE3": "Owner",
    "01KXFGFW21BVB5E9MJ5N41DYH9": "Orchestrator",
    "01KXFQ89EJT4YD0SYMDPXJZ90F": "Dev",
    "01KXHXV9969ZX6NW52AKTP77K2": "Orch-Main",
    "01KXHXV99DEJB6YTW12JFN5JR0": "Dev-Claude-A",
    "01KXHXV99HQMNQRR5FKPFRSPTY": "Dev-Codex-A",
}


def main() -> None:
    n = 12
    for i, a in enumerate(sys.argv):
        if a == "--since" and i + 1 < len(sys.argv):
            n = int(sys.argv[i + 1])
    hc = httpx.Client(timeout=15.0)

    msgs = hc.get(f"{API}/channels/{RT}/messages", params={"limit": 100}).json()["items"]
    print(f"== 频道消息（近 {n} 条 / 共 {len(msgs)}）==")
    for m in msgs[-n:]:
        who = NAMES.get(m.get("author_member_id") or "", m.get("author_member_id") or "SYS")
        body = (m.get("body") or "").replace("\n", " ")[:150]
        thread = " [thread]" if m.get("thread_root_id") else ""
        card = f" [card={m['card_kind']}]" if m.get("card_kind") else ""
        print(f"  {m['id'][-6:]} {who}{thread}{card}: {body}")

    if "--tasks" in sys.argv or True:
        tasks = hc.get(f"{API}/tasks", params={"limit": 200}).json()["items"]
        live = [t for t in tasks if t["channel_id"] == RT]
        print(f"== realtest 任务（{len(live)}）==")
        for t in live[-10:]:
            own = NAMES.get(t.get("owner_member_id") or "", "-")
            head = f"#{t['number']} [{t['status']}] wc={t['writes_code']} owner={own}"
            print(f"  {head} :: {t['title'][:60]}")

    if "--wt" in sys.argv:
        wts = hc.get(f"{API}/worktrees").json()
        rows = wts.get("items", wts)
        print(f"== worktrees（{len(rows)}）==")
        for w in rows:
            commit = (w.get("merge_commit") or "")[:8]
            print(f"  task={w['task_id'][-6:]} {w['status']} commit={commit}")


if __name__ == "__main__":
    main()
