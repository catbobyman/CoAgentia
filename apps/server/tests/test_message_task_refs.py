"""C3a：发消息时服务端解析 body 里的 `task #<n>` → message_task_refs（契约 B §9.5）。

编号频道内自增，故只解析当前频道；未命中保纯文本不报错；同一 task 只落一行（派生持久化，
与 message_mentions 同构）。真 server 断言（解析是业务逻辑，mock 无此行为）。
"""

from __future__ import annotations

from coagentia_server.db import models
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine

BUILD = "build"
RESEARCH = "research"

_MTR = models.MessageTaskRef.__table__


def _channel(client: TestClient, name: str) -> dict:
    return next(c for c in client.get("/api/channels").json()["items"] if c["name"] == name)


def _new_task(client: TestClient, channel_id: str, title: str = "t") -> dict:
    r = client.post(
        f"/api/channels/{channel_id}/messages", json={"body": "b", "as_task": {"title": title}}
    )
    assert r.status_code == 201, r.text
    return r.json()["task"]


def _post(client: TestClient, channel_id: str, body: str) -> str:
    r = client.post(f"/api/channels/{channel_id}/messages", json={"body": body})
    assert r.status_code == 201, r.text
    return r.json()["message"]["id"]


def _refs(engine: Engine, message_id: str) -> list[str]:
    with engine.connect() as conn:
        return [
            r[0]
            for r in conn.execute(
                select(_MTR.c.task_id).where(_MTR.c.message_id == message_id)
            ).fetchall()
        ]


def test_task_ref_hit_same_channel(server_client: TestClient, seeded_engine: Engine) -> None:
    build = _channel(server_client, BUILD)
    task = _new_task(server_client, build["id"])
    mid = _post(server_client, build["id"], f"见 task #{task['number']} 的进展")
    assert _refs(seeded_engine, mid) == [task["id"]]


def test_task_ref_cross_channel_no_match(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """编号频道内自增，跨频道无定义：build 的 task #n 在 research 引用不落 ref。"""
    build = _channel(server_client, BUILD)
    research = _channel(server_client, RESEARCH)
    task = _new_task(server_client, build["id"])  # 只在 build 建号
    mid = _post(server_client, research["id"], f"另频道提 task #{task['number']}")
    assert _refs(seeded_engine, mid) == []


def test_task_ref_unknown_number_plain_text(
    server_client: TestClient, seeded_engine: Engine
) -> None:
    """未命中的号：不报错、无 ref、消息正常落库。"""
    build = _channel(server_client, BUILD)
    mid = _post(server_client, build["id"], "占位 task #999 不存在")
    assert _refs(seeded_engine, mid) == []
    # 消息本体仍在
    msgs = server_client.get(f"/api/channels/{build['id']}/messages").json()["items"]
    assert any(m["id"] == mid for m in msgs)


def test_task_ref_dedup_single_row(server_client: TestClient, seeded_engine: Engine) -> None:
    build = _channel(server_client, BUILD)
    task = _new_task(server_client, build["id"])
    n = task["number"]
    mid = _post(server_client, build["id"], f"task #{n} 前文 ...... 又 task #{n} 后文")
    assert _refs(seeded_engine, mid) == [task["id"]]  # 只一行


def test_task_ref_case_and_spacing(server_client: TestClient, seeded_engine: Engine) -> None:
    """大小写不敏感、task 与 # 间允许空白（B §9.5 正则口径）。"""
    build = _channel(server_client, BUILD)
    task = _new_task(server_client, build["id"])
    mid = _post(server_client, build["id"], f"参考 TASK #{task['number']}")
    assert _refs(seeded_engine, mid) == [task["id"]]
