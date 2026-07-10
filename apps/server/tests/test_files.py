"""C4 真 server：频道文件页签 GET /channels/{id}/files——倒序、游标分页、跨频道隔离、形状。

文件经 POST /files 预上传 + 发消息携 file_ids 绑定后才落 files 表（channel_id 随绑定写入）。
"""

from __future__ import annotations

from coagentia_contracts import entities, rest
from fastapi.testclient import TestClient

BUILD = "build"
RESEARCH = "research"


def _channel(client: TestClient, name: str) -> dict:
    return next(c for c in client.get("/api/channels").json()["items"] if c["name"] == name)


def _upload_and_bind(client: TestClient, channel_id: str, name: str, data: bytes) -> str:
    up = client.post("/api/files", files={"file": (name, data, "text/plain")})
    assert up.status_code == 201
    upload_id = up.json()["id"]
    r = client.post(
        f"/api/channels/{channel_id}/messages",
        json={"body": f"附件 {name}", "file_ids": [upload_id]},
    )
    assert r.status_code == 201
    return upload_id


def test_channel_files_desc_order_and_shape(server_client: TestClient) -> None:
    build = _channel(server_client, BUILD)["id"]
    ids = [
        _upload_and_bind(server_client, build, f"f{i}.txt", f"body{i}".encode())
        for i in range(3)
    ]
    page = rest.Page[entities.FilePublic].model_validate(
        server_client.get(f"/api/channels/{build}/files").json()
    )
    got = [f.id for f in page.items]
    assert got == list(reversed(ids)), "最新文件在前（created_at desc, id desc）"
    for f in page.items:
        assert f.channel_id == build
        assert f.message_id is not None  # 已绑定态


def test_channel_files_cursor_pagination(server_client: TestClient) -> None:
    build = _channel(server_client, BUILD)["id"]
    ids = [_upload_and_bind(server_client, build, f"p{i}.txt", f"x{i}".encode()) for i in range(5)]
    expected = list(reversed(ids))
    first = server_client.get(f"/api/channels/{build}/files", params={"limit": 2}).json()
    assert [f["id"] for f in first["items"]] == expected[:2]
    assert first["next_cursor"] == expected[1]
    second = server_client.get(
        f"/api/channels/{build}/files", params={"limit": 2, "after": first["next_cursor"]}
    ).json()
    assert [f["id"] for f in second["items"]] == expected[2:4]
    assert second["next_cursor"] == expected[3]
    last = server_client.get(
        f"/api/channels/{build}/files", params={"limit": 2, "after": second["next_cursor"]}
    ).json()
    assert [f["id"] for f in last["items"]] == expected[4:]
    assert last["next_cursor"] is None


def test_channel_files_cross_channel_isolation(server_client: TestClient) -> None:
    build = _channel(server_client, BUILD)["id"]
    research = _channel(server_client, RESEARCH)["id"]
    _upload_and_bind(server_client, build, "b.txt", b"b")
    rid = _upload_and_bind(server_client, research, "r.txt", b"r")
    research_files = server_client.get(f"/api/channels/{research}/files").json()["items"]
    assert [f["id"] for f in research_files] == [rid]
    assert all(f["channel_id"] == research for f in research_files)


def test_channel_files_empty_and_missing_channel(server_client: TestClient) -> None:
    build = _channel(server_client, BUILD)["id"]
    empty = rest.Page[entities.FilePublic].model_validate(
        server_client.get(f"/api/channels/{build}/files").json()
    )
    assert empty.items == [] and empty.next_cursor is None
    missing = server_client.get("/api/channels/01K0MISSING000000000000000/files")
    assert missing.status_code == 404
    err = rest.ErrorResponse.model_validate(missing.json())
    assert err.error.code is rest.ErrorCode.NOT_FOUND
