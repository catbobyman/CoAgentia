"""keyset 分页统一整改回归（挂账批2，2026-07-10）：游标行离开过滤结果集不再从头翻、
messages before 紧邻回翻、未知游标宽容、LIMIT 语义不变（next_cursor 链）。

旧范式（游标按"id 在结果集内位置"定位）在 after 行被过滤掉时静默重发首页——
list_tasks 因 status/owner 可变过滤最易触发（M2 挂账 3）。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

BUILD = "build"


def _channel(client: TestClient, name: str) -> dict:
    return next(c for c in client.get("/api/channels").json()["items"] if c["name"] == name)


def _new_task(client: TestClient, channel_id: str, title: str) -> dict:
    r = client.post(
        f"/api/channels/{channel_id}/messages", json={"body": title, "as_task": {"title": title}}
    )
    assert r.status_code == 201, r.text
    return r.json()["task"]


# ---------------------------------------------------------------- list_tasks：过滤态游标


def test_tasks_cursor_survives_filter_departure(server_client: TestClient) -> None:
    """after 行流转出过滤集（todo→in_progress）后，翻页从锚点继续、不重发首页。"""
    build = _channel(server_client, BUILD)
    t1 = _new_task(server_client, build["id"], "kp-t1")
    _new_task(server_client, build["id"], "kp-t2")
    _new_task(server_client, build["id"], "kp-t3")

    page1 = server_client.get(
        "/api/tasks", params={"channel_id": build["id"], "status": "todo", "limit": 1}
    ).json()
    assert [t["title"] for t in page1["items"]] == ["kp-t1"]
    assert page1["next_cursor"] == t1["id"]

    # t1 离开 todo 结果集（claim 联动 in_progress；浏览器无双头默认 Owner 主体）
    r = server_client.post(f"/api/tasks/{t1['id']}/claim")
    assert r.status_code == 200, r.text

    # 旧范式此处 after 不在结果集 → 从头翻重发 kp-t2 所在首页且丢页界；keyset 锚点照常续翻
    page2 = server_client.get(
        "/api/tasks",
        params={"channel_id": build["id"], "status": "todo", "limit": 1, "after": t1["id"]},
    ).json()
    assert [t["title"] for t in page2["items"]] == ["kp-t2"]
    page3 = server_client.get(
        "/api/tasks",
        params={
            "channel_id": build["id"], "status": "todo", "limit": 1,
            "after": page2["next_cursor"],
        },
    ).json()
    assert [t["title"] for t in page3["items"]] == ["kp-t3"]
    assert page3["next_cursor"] is None


def test_tasks_unknown_cursor_is_lenient(server_client: TestClient) -> None:
    """未知游标沿旧行为宽容忽略（从头翻），不 404/500。"""
    build = _channel(server_client, BUILD)
    _new_task(server_client, build["id"], "kp-lenient")
    r = server_client.get(
        "/api/tasks",
        params={"channel_id": build["id"], "after": "01K0MMBR0000000000000000ZZ"},
    )
    assert r.status_code == 200
    assert r.json()["items"], "未知游标 → 从头翻，仍有结果"


# ---------------------------------------------------------------- messages：before 紧邻回翻


def test_messages_before_returns_adjacent_window(server_client: TestClient) -> None:
    """before 单独出现 = 取 before 前最近 limit 条（升序返回）——真「倒序回翻」；
    旧实现错误返回频道最头部窗口。next_cursor = 窗口最旧 id 供继续回翻。"""
    build = _channel(server_client, BUILD)
    ids = []
    for i in range(6):
        r = server_client.post(
            f"/api/channels/{build['id']}/messages", json={"body": f"kpm-{i}"}
        )
        ids.append(r.json()["message"]["id"])

    page = server_client.get(
        f"/api/channels/{build['id']}/messages",
        params={"before": ids[5], "limit": 2},
    ).json()
    bodies = [m["body"] for m in page["items"]]
    assert bodies == ["kpm-3", "kpm-4"], bodies  # 紧邻窗口，非频道头部
    assert page["next_cursor"] == ids[3]  # 最旧 id，作下一次 before 续翻

    older = server_client.get(
        f"/api/channels/{build['id']}/messages",
        params={"before": page["next_cursor"], "limit": 2},
    ).json()
    assert [m["body"] for m in older["items"]] == ["kpm-1", "kpm-2"]


def test_messages_after_keyset_chain(server_client: TestClient) -> None:
    build = _channel(server_client, BUILD)
    ids = []
    for i in range(3):
        r = server_client.post(
            f"/api/channels/{build['id']}/messages", json={"body": f"kpa-{i}"}
        )
        ids.append(r.json()["message"]["id"])
    page = server_client.get(
        f"/api/channels/{build['id']}/messages", params={"after": ids[0], "limit": 2}
    ).json()
    assert [m["body"] for m in page["items"][-2:]] == ["kpa-1", "kpa-2"]
