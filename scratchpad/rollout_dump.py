"""R3 归档：全量频道日志 + 任务 + worktree 落 docs/verify/DEDAG-ROLLOUT-CHANNEL-LOG.txt。"""

from __future__ import annotations

import io
import sys

import httpx

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

API = "http://127.0.0.1:8787/api"
RT = "01KXHXW27P1P1D5G94H1V5HA04"
NAMES = {
    "01KXFC8QTTQ003YWPVVV3XQZE3": "Owner",
    "01KXHXV9969ZX6NW52AKTP77K2": "Orch-Main",
    "01KXHXV99DEJB6YTW12JFN5JR0": "Dev-Claude-A",
    "01KXHXV99HQMNQRR5FKPFRSPTY": "Dev-Codex-A",
}
OUT = "docs/verify/DEDAG-ROLLOUT-CHANNEL-LOG.txt"


def main() -> None:
    hc = httpx.Client(timeout=15.0)
    buf = io.StringIO()
    msgs = hc.get(f"{API}/channels/{RT}/messages", params={"limit": 200}).json()["items"]
    buf.write(f"== realtest 频道全量消息（{len(msgs)} 条，抓取于 R3 收口）==\n")
    for m in msgs:
        who = NAMES.get(m.get("author_member_id") or "", m.get("author_member_id") or "SYS")
        body = (m.get("body") or "").replace("\n", " ")
        thread = " [thread]" if m.get("thread_root_id") else ""
        card = f" [card={m['card_kind']}]" if m.get("card_kind") else ""
        buf.write(f"{m['created_at']} {m['id'][-6:]} {who}{thread}{card}: {body}\n")
    tasks = hc.get(f"{API}/tasks", params={"limit": 200}).json()["items"]
    live = [t for t in tasks if t["channel_id"] == RT]
    buf.write(f"\n== realtest 任务终态（{len(live)}）==\n")
    for t in live:
        own = NAMES.get(t.get("owner_member_id") or "", "-")
        head = f"#{t['number']} [{t['status']}] wc={t['writes_code']} owner={own}"
        buf.write(f"{head} :: {t['title']}\n")
    wts = hc.get(f"{API}/worktrees").json()
    rows = wts.get("items", wts)
    buf.write(f"\n== worktrees 终态（{len(rows)}）==\n")
    for w in rows:
        commit = (w.get("merge_commit") or "")[:12]
        buf.write(f"task={w['task_id']} {w['status']} commit={commit}\n")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(buf.getvalue())
    print("written", OUT, "bytes=", len(buf.getvalue().encode("utf-8")))


if __name__ == "__main__":
    main()
