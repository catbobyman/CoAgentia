"""R2 人类 nudge：以 Owner 身份 @Orch-Main 催验收 #14 + 闭环 #15 + 总报告。"""

from __future__ import annotations

import sys

import httpx

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

API = "http://127.0.0.1:8787/api"
RT = "01KXHXW27P1P1D5G94H1V5HA04"

BODY = (
    "@Orch-Main ③ 长休息机制（#14）已交付进入 in_review，请验收并指挥合并；"
    "冲突任务 #15 的修复已随 #13 合入主干，请确认后将其闭环；"
    "三件全部入主干后发总交付报告。"
)


def main() -> None:
    hc = httpx.Client(timeout=15.0)
    r = hc.post(f"{API}/channels/{RT}/messages", json={"body": BODY})
    print(r.status_code)
    data = r.json()
    msg = data.get("message", data)
    print("id=", msg.get("id"), "mentions=", msg.get("mentions"))


if __name__ == "__main__":
    main()
