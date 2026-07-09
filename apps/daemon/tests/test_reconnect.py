"""重连 / 握手 / 缓冲重传（契约 D §2 退避、§4.1 hello 进程表、§7/§11.5 重传不虚增）。"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

import pytest
from coagentia_contracts.daemon import TokenUsageEventIn
from coagentia_daemon.client import BACKOFF_CAP, next_backoff
from helpers import (
    AutoAckTransport,
    RecordingTransport,
    boot_data,
    fake_runner,
    instr,
    make_client,
    until,
    usage_event,
)


def test_backoff_schedule() -> None:
    b = 1.0
    seq = [b]
    for _ in range(6):
        b = next_backoff(b)
        seq.append(b)
    assert seq == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0]
    assert next_backoff(30.0) == BACKOFF_CAP


@pytest.mark.asyncio
async def test_build_hello_reflects_process_table(tmp_path: Path) -> None:
    tr = RecordingTransport()
    client, _adapter, _ = make_client(tmp_path, transport=tr)
    data = boot_data(tmp_path)
    aid = data["agent_member_id"]
    await client.handle_instr(instr("agent.start", {"agent": data}))
    hello = client.build_hello()
    assert [a.agent_member_id for a in hello.agents] == [aid]
    assert hello.agents[0].status.value in {"starting", "idle", "busy"}
    assert hello.buffered.usage == 0
    # 停掉后进程表清空。
    await client.handle_instr(instr("agent.stop", {"agent_member_id": aid}))
    assert client.build_hello().agents == []


@pytest.mark.asyncio
async def test_run_retries_connect_until_success(tmp_path: Path) -> None:
    calls = {"n": 0}
    tr = AutoAckTransport()

    async def connect_fn(url: str, key: str) -> AutoAckTransport:
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError("connection refused")
        return tr

    client, _adapter, _ = make_client(
        tmp_path, connect_fn=connect_fn, runner=fake_runner, backoff_start=0.01, backoff_cap=0.02
    )
    task = asyncio.create_task(client.run())
    try:
        await asyncio.wait_for(client.connected.wait(), timeout=5)
        assert calls["n"] == 3  # 两次失败退避后第三次连上
        assert client.hello_ack is not None
        assert client.hello_ack.heartbeat_sec == 25
    finally:
        client.stop()
        await tr.close()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)


@pytest.mark.asyncio
async def test_reconnect_keeps_agents_and_rehellos(tmp_path: Path) -> None:
    """断连≠Agent 死亡：重连后 hello 进程表仍含存活 Agent（契约 D §4.2）。"""
    transports: list[AutoAckTransport] = []

    async def connect_fn(url: str, key: str) -> AutoAckTransport:
        t = AutoAckTransport()
        transports.append(t)
        return t

    client, adapter, _ = make_client(
        tmp_path, connect_fn=connect_fn, runner=fake_runner, backoff_start=0.01, backoff_cap=0.02
    )
    task = asyncio.create_task(client.run())
    try:
        await asyncio.wait_for(client.connected.wait(), timeout=5)
        t1 = transports[-1]
        data = boot_data(tmp_path)
        aid = data["agent_member_id"]
        t1.feed(instr("agent.start", {"agent": data}))
        await until(lambda: aid in [a.agent_member_id for a in adapter.process_table()])
        # 杀连接 → 重连。
        await t1.close()
        await until(lambda: len(transports) >= 2 and client.connected.is_set())
        t2 = transports[-1]
        hellos = [f for f in t2.sent if f.get("type") == "hello"]
        assert hellos, "重连应重发 hello"
        table = [a["agent_member_id"] for a in hellos[-1]["data"]["agents"]]
        assert aid in table  # 存活进程仍在进程表
    finally:
        client.stop()
        if transports:
            await transports[-1].close()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)


@pytest.mark.asyncio
async def test_usage_retransmit_no_inflation(tmp_path: Path) -> None:
    """§11.5 daemon 侧半边：未 ack → 同 ULID 批重传；ack 后清空（server 按 ULID 去重）。"""
    tr = RecordingTransport()
    client, _adapter, _ = make_client(tmp_path, transport=tr, ack_timeout=0.05)
    aid = "01K5AGENT0000000000000000A"
    for _ in range(10):
        client.on_usage(TokenUsageEventIn.model_validate(usage_event(aid)))
    ids = [e.id for e in client.buffer.peek_usage(500)]
    assert len(ids) == 10

    # 第一次 flush：无 ack → 超时 → 全量保留。
    await client._flush_usage()
    assert client.buffer.counts().usage == 10
    rep1 = tr.reports("usage.batch")[-1]
    assert [e["id"] for e in rep1["data"]["events"]] == ids

    # 第二次 flush：并发解析 ack → 落库确认 → 缓冲清空；ULID 与首发一致（不虚增）。
    task = asyncio.create_task(client._flush_usage())
    await asyncio.sleep(0.01)
    rep2 = tr.reports("usage.batch")[-1]
    client._resolve_report_ack({"kind": "ack", "ref": rep2["frame_id"], "result": "done"})
    await asyncio.wait_for(task, timeout=2)
    assert client.buffer.counts().usage == 0
    assert [e["id"] for e in rep2["data"]["events"]] == ids
