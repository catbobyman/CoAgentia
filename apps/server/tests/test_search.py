"""C4 真 server：GET /search 三分组（jumps / messages-FTS / tasks）+ 中文 FTS 分词实测。

中文实测结论（2026-07-09，契约 A §10.4）：见 test_chinese_fts_unicode61_limitation——
unicode61 把连续 CJK 串当单一 token，子串检索不命中；需 M3 换 trigram 分词器（见 search.py 头注）。
"""

from __future__ import annotations

from coagentia_contracts import rest
from fastapi.testclient import TestClient

BUILD = "build"
RESEARCH = "research"


def _channel(client: TestClient, name: str) -> dict:
    return next(c for c in client.get("/api/channels").json()["items"] if c["name"] == name)


def _member(client: TestClient, name: str) -> dict:
    return next(m for m in client.get("/api/members").json() if m["name"] == name)


def _post(client: TestClient, channel_id: str, body: str) -> dict:
    r = client.post(f"/api/channels/{channel_id}/messages", json={"body": body})
    assert r.status_code == 201
    return r.json()["message"]


def _task(client: TestClient, channel_id: str, body: str, title: str) -> dict:
    r = client.post(
        f"/api/channels/{channel_id}/messages",
        json={"body": body, "as_task": {"title": title}},
    )
    assert r.status_code == 201
    return r.json()


def test_jumps_channel_and_member_substring_nocase(server_client: TestClient) -> None:
    res = rest.SearchResponse.model_validate(
        server_client.get("/api/search", params={"q": "buil"}).json()
    )
    assert any(c.name == "build" for c in res.jumps.channels)
    # 成员名子串 NOCASE：q="pAt" 命中 "Pat"。
    res2 = rest.SearchResponse.model_validate(
        server_client.get("/api/search", params={"q": "pAt"}).json()
    )
    assert any(m.name == "Pat" for m in res2.jumps.members)


def test_messages_fts_hit_with_snippet_markers(server_client: TestClient) -> None:
    build = _channel(server_client, BUILD)["id"]
    _post(server_client, build, "prelude zqxwvu keyword follows here")
    res = rest.SearchResponse.model_validate(
        server_client.get("/api/search", params={"q": "zqxwvu"}).json()
    )
    assert len(res.messages) == 1
    hit = res.messages[0]
    assert "zqxwvu" in hit.message.body
    assert "«" in hit.snippet and "»" in hit.snippet  # snippet() 高亮标记


def test_tasks_title_substring_and_anchor_fts_dedup(server_client: TestClient) -> None:
    build = _channel(server_client, BUILD)["id"]
    # (a) title 子串命中。
    t_title = _task(server_client, build, "普通正文", "flibbertigibbet plan")["task"]
    res = rest.SearchResponse.model_validate(
        server_client.get("/api/search", params={"q": "flibbertigibbet"}).json()
    )
    assert [t.id for t in res.tasks] == [t_title["id"]]
    # (b) 锚点消息 FTS 命中（title 不含该词，正文含）——task 经 root_message FTS 进入结果。
    t_anchor = _task(server_client, build, "正文含 wobblethon 关键词", "无关标题")["task"]
    res2 = rest.SearchResponse.model_validate(
        server_client.get("/api/search", params={"q": "wobblethon"}).json()
    )
    assert [t.id for t in res2.tasks] == [t_anchor["id"]]
    # (c) 去重：title 与锚点同时命中同一 task → 只出现一次。
    t_both = _task(server_client, build, "正文含 zonktastic", "zonktastic 也在标题")["task"]
    res3 = rest.SearchResponse.model_validate(
        server_client.get("/api/search", params={"q": "zonktastic"}).json()
    )
    assert [t.id for t in res3.tasks] == [t_both["id"]]


def test_kind_filter_narrows_groups(server_client: TestClient) -> None:
    build = _channel(server_client, BUILD)["id"]
    _task(server_client, build, "正文 kindfiltertoken here", "kindfiltertoken title")
    only_msg = rest.SearchResponse.model_validate(
        server_client.get(
            "/api/search", params={"q": "kindfiltertoken", "kind": "message"}
        ).json()
    )
    assert only_msg.tasks == [] and len(only_msg.messages) >= 1
    only_task = rest.SearchResponse.model_validate(
        server_client.get(
            "/api/search", params={"q": "kindfiltertoken", "kind": "task"}
        ).json()
    )
    assert only_task.messages == [] and len(only_task.tasks) >= 1


def test_from_member_and_in_channel_filters(server_client: TestClient) -> None:
    build = _channel(server_client, BUILD)["id"]
    research = _channel(server_client, RESEARCH)["id"]
    owner = _member(server_client, "Memcyo")["id"]
    _post(server_client, build, "crossfilter marker in build")
    _post(server_client, research, "crossfilter marker in research")
    # in_channel 收窄到 build。
    scoped = rest.SearchResponse.model_validate(
        server_client.get(
            "/api/search", params={"q": "crossfilter", "in_channel": build}
        ).json()
    )
    assert len(scoped.messages) == 1
    assert scoped.messages[0].message.channel_id == build
    # from_member=owner 命中（owner 发的都算）；不存在成员 → 空。
    by_owner = rest.SearchResponse.model_validate(
        server_client.get(
            "/api/search", params={"q": "crossfilter", "from_member": owner}
        ).json()
    )
    assert len(by_owner.messages) == 2
    by_ghost = rest.SearchResponse.model_validate(
        server_client.get(
            "/api/search",
            params={"q": "crossfilter", "from_member": "01K0GHOST00000000000000000"},
        ).json()
    )
    assert by_ghost.messages == []


def test_chinese_fts_unicode61_limitation(server_client: TestClient) -> None:
    """中文分词实测（契约 A §10.4 收口）：unicode61 把连续 CJK 串当单一 token。

    - body="修复登录页面的崩溃"（无分隔）→ 整串是一个 token；子串 "登录"/"崩溃" MATCH **不命中**。
    - 整串完整查询命中；空白分隔的 CJK 词（"上线"）作为独立 token 可命中。
    结论：unicode61 对中文子串检索不够用，M3 需换 trigram 分词器（或分词预切）。
    """
    build = _channel(server_client, BUILD)["id"]
    _post(server_client, build, "修复登录页面的崩溃")
    _post(server_client, build, "部署 上线 完成")

    # 子串不命中（连续 CJK 单 token 的固有局限）。
    for sub in ("登录", "崩溃"):
        res = rest.SearchResponse.model_validate(
            server_client.get("/api/search", params={"q": sub}).json()
        )
        assert res.messages == [], f"unicode61 子串 {sub} 预期不命中"
    # 整串命中。
    whole = rest.SearchResponse.model_validate(
        server_client.get("/api/search", params={"q": "修复登录页面的崩溃"}).json()
    )
    assert len(whole.messages) == 1
    # 空白分隔的独立 CJK token 命中。
    delimited = rest.SearchResponse.model_validate(
        server_client.get("/api/search", params={"q": "上线"}).json()
    )
    assert len(delimited.messages) == 1


def test_empty_query_returns_empty_groups(server_client: TestClient) -> None:
    res = rest.SearchResponse.model_validate(
        server_client.get("/api/search", params={"q": "   "}).json()
    )
    assert res.jumps.channels == [] and res.jumps.members == []
    assert res.messages == [] and res.tasks == []
