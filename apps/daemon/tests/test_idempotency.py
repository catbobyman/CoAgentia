"""指令幂等消费（契约 D §5：自然键幂等 + frame_id 短窗去重加速器）。

驱动方式：内存传输 + 直接调用 client.handle_instr（免 server）。断言 ack 结果与假适配器副作用次数。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers import (
    RecordingTransport,
    boot_data,
    instr,
    make_client,
    message_public,
)


@pytest.mark.asyncio
async def test_agent_start_done_then_noop_same_frame(tmp_path: Path) -> None:
    tr = RecordingTransport()
    client, adapter, _ = make_client(tmp_path, transport=tr)
    data = boot_data(tmp_path)
    frame = instr("agent.start", {"agent": data})

    await client.handle_instr(frame)
    assert adapter.starts == [data["agent_member_id"]]
    assert tr.last_ack()["result"] == "done"

    # 同 frame_id 重发（ack 丢失场景）→ noop，假适配器不产生第二次副作用。
    await client.handle_instr(frame)
    assert adapter.starts == [data["agent_member_id"]]  # 仍只 1 次
    assert tr.last_ack()["result"] == "noop"


@pytest.mark.asyncio
async def test_agent_start_noop_by_natural_key_diff_frame(tmp_path: Path) -> None:
    """正确性押在自然键：不同 frame_id、同 agent 已在跑 → noop（不依赖 frame 去重）。"""
    tr = RecordingTransport()
    client, adapter, _ = make_client(tmp_path, transport=tr)
    data = boot_data(tmp_path)
    await client.handle_instr(instr("agent.start", {"agent": data}))
    await client.handle_instr(instr("agent.start", {"agent": data}))  # 新 frame_id
    assert adapter.starts == [data["agent_member_id"]]
    assert tr.last_ack()["result"] == "noop"


@pytest.mark.asyncio
async def test_agent_start_emits_status_reports(tmp_path: Path) -> None:
    tr = RecordingTransport()
    client, _adapter, _ = make_client(tmp_path, transport=tr)
    data = boot_data(tmp_path)
    await client.handle_instr(instr("agent.start", {"agent": data}))
    statuses = [
        r["data"]["status"] for r in tr.reports("agent.status_changed")
    ]
    assert statuses == ["starting", "idle"]  # 契约 D §7：starting→idle 上报


@pytest.mark.asyncio
async def test_agent_stop_done_then_noop(tmp_path: Path) -> None:
    tr = RecordingTransport()
    client, adapter, _ = make_client(tmp_path, transport=tr)
    data = boot_data(tmp_path)
    aid = data["agent_member_id"]
    await client.handle_instr(instr("agent.start", {"agent": data}))
    await client.handle_instr(instr("agent.stop", {"agent_member_id": aid}))
    assert tr.last_ack()["result"] == "done"
    await client.handle_instr(instr("agent.stop", {"agent_member_id": aid}))  # 已停
    assert tr.last_ack()["result"] == "noop"


@pytest.mark.asyncio
async def test_message_deliver_dedup_by_max_id(tmp_path: Path) -> None:
    tr = RecordingTransport()
    client, adapter, _ = make_client(tmp_path, transport=tr)
    data = boot_data(tmp_path)
    aid = data["agent_member_id"]
    ch = "01K5CHAN00000000000000000A"
    await client.handle_instr(instr("agent.start", {"agent": data}))
    msg = message_public(ch)
    deliver = instr(
        "message.deliver",
        {"agent_member_id": aid, "channel_id": ch, "messages": [msg], "thread_root_id": None},
    )
    await client.handle_instr(deliver)
    assert tr.last_ack()["result"] == "done"
    assert adapter.delivers == [(aid, msg["id"])]
    # 重复投递同批（不同 frame_id）→ 已喂过的最大 message_id → noop 去重。
    deliver2 = instr(
        "message.deliver",
        {"agent_member_id": aid, "channel_id": ch, "messages": [msg], "thread_root_id": None},
    )
    await client.handle_instr(deliver2)
    assert tr.last_ack()["result"] == "noop"
    assert len(adapter.delivers) == 1


@pytest.mark.asyncio
async def test_wake_noop_when_already_awake(tmp_path: Path) -> None:
    tr = RecordingTransport()
    client, adapter, _ = make_client(tmp_path, transport=tr)
    data = boot_data(tmp_path)
    aid = data["agent_member_id"]
    await client.handle_instr(instr("agent.start", {"agent": data}))
    wake = instr(
        "agent.wake",
        {
            "agent_member_id": aid,
            "reason": "mention",
            "refs": {"message_ids": [message_public("c")["id"]]},
        },
    )
    await client.handle_instr(wake)
    assert tr.last_ack()["result"] == "done"
    await client.handle_instr(
        instr("agent.wake", {"agent_member_id": aid, "reason": "mention", "refs": {}})
    )
    assert tr.last_ack()["result"] == "noop"


@pytest.mark.asyncio
async def test_runtime_rescan_reports_detected(tmp_path: Path) -> None:
    tr = RecordingTransport()

    async def runner(argv: list[str]) -> tuple[int, str, str]:
        return 0, "2.1.205 (Claude Code)", ""

    client, _adapter, _ = make_client(tmp_path, transport=tr, runner=runner)
    await client.handle_instr(instr("runtime.rescan", {}))
    assert tr.last_ack()["result"] == "done"
    assert tr.reports("runtimes.detected"), "rescan 应上报 runtimes.detected"


@pytest.mark.asyncio
async def test_deploy_run_now_supported_acks_done(tmp_path: Path) -> None:
    # preview.start/stop 自 K2、deploy.run 自 M7b K4 起均已落地——M7 指令目录再无 _unsupported。
    from coagentia_daemon.deploy import DeployProcessResult, DeployRunner
    from coagentia_daemon.util import new_ulid

    async def fake(data: object, *, on_log: object, timeout_sec: float) -> object:
        return DeployProcessResult(0, "https://demo.example.com")

    tr = RecordingTransport()
    client, _adapter, _ = make_client(tmp_path, transport=tr)
    client.deploys = DeployRunner(runner=fake)  # type: ignore[arg-type]
    await client.handle_instr(
        instr(
            "deploy.run",
            {
                "deployment_id": new_ulid(),
                "repo_path": "/r",
                "command": "run",
                "branch": "main",
            },
        )
    )
    assert tr.last_ack()["result"] == "done"  # 起后台 task 即 ack DONE（不再 UNSUPPORTED）


@pytest.mark.asyncio
async def test_reset_full_clears_home_and_session(tmp_path: Path) -> None:
    tr = RecordingTransport()
    client, adapter, _ = make_client(tmp_path, transport=tr)
    data = boot_data(tmp_path)
    aid = data["agent_member_id"]
    await client.handle_instr(instr("agent.start", {"agent": data}))
    # 在 daemon 管理的 agent home 落点写一个文件 + 会话簿记。
    home = client.paths.ensure_agent_home(aid)
    (home / "junk.txt").write_text("x", encoding="utf-8")
    client.paths.write_session(aid, {"source_session": "s"})
    await client.handle_instr(instr("agent.reset_full", {"agent": data}))
    assert tr.last_ack()["result"] == "done"
    assert list(home.iterdir()) == []  # Home 内容清空、目录保留
    assert client.paths.read_session(aid) == {}
    assert aid in adapter.reset_fulls
