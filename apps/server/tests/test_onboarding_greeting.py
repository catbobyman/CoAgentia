"""L11 新 Agent 入职问候（PRD FR-1.4；任务书裁决 #9「默认关」）。

双门：① 工作区 onboarding_greeting 开关（seed 默认 false）；② diagnostic 幂等标记未落。
断言：默认关→零动作、开→上线问候一条、重启不重复、预检离线→不落标记（下次仍可问候）。
"""

from __future__ import annotations

from typing import Any

from coagentia_server.db import models
from fastapi.testclient import TestClient
from sqlalchemy import select

_DIAG = models.tbl(models.DiagnosticEvent)
_DIAG_TYPE = "agent.onboarding_greeting"


class _LifecycleSpyHub:
    """记录 send_lifecycle / inject_onboarding_greeting；模拟在线 daemon（online 可关）。"""

    def __init__(self, *, online: bool = True, start_result: str = "done") -> None:
        self.online = online
        self.start_result = start_result
        self.lifecycles: list[tuple[str, Any]] = []
        self.greetings: list[str] = []

    def agent_daemon_online(self, agent_member_id: str) -> bool:
        return self.online

    def send_lifecycle(self, agent_id: str, action: Any) -> str:
        self.lifecycles.append((agent_id, action))
        return self.start_result

    def inject_onboarding_greeting(self, agent_member_id: str, *, ref: str | None = None) -> str:
        self.greetings.append(agent_member_id)
        return "done"


def _create_agent(client: TestClient, name: str = "GreetBot") -> str:
    comp = client.get("/api/computers").json()[0]
    r = client.post(
        "/api/agents",
        json={"computer_id": comp["id"], "name": name, "runtime": "claude_code", "model": "m"},
    )
    assert r.status_code == 201, r.text
    return r.json()["member_id"]


def _set_greeting(client: TestClient, on: bool) -> None:
    r = client.patch("/api/workspace", json={"onboarding_greeting": on})
    assert r.status_code == 200, r.text


def _start(client: TestClient, member_id: str) -> Any:
    return client.post(f"/api/agents/{member_id}/lifecycle", json={"action": "start"})


def _marker_count(client: TestClient, member_id: str) -> int:
    engine = client.app.state.engine  # type: ignore[attr-defined]
    with engine.connect() as c:
        rows = c.execute(
            select(_DIAG.c.seq).where(
                _DIAG.c.agent_member_id == member_id, _DIAG.c.type == _DIAG_TYPE
            )
        ).all()
    return len(rows)


def test_greeting_off_by_default_no_action(server_client: TestClient) -> None:
    """seed 默认 onboarding_greeting=false（裁决 #9）——上线零问候、不落标记。"""
    member = _create_agent(server_client)
    spy = _LifecycleSpyHub()
    server_client.app.state.daemon_hub = spy  # type: ignore[attr-defined]
    r = _start(server_client, member)
    assert r.status_code == 200, r.text
    assert spy.greetings == []
    assert _marker_count(server_client, member) == 0


def test_greeting_on_fires_once(server_client: TestClient) -> None:
    member = _create_agent(server_client)
    _set_greeting(server_client, True)
    spy = _LifecycleSpyHub()
    server_client.app.state.daemon_hub = spy  # type: ignore[attr-defined]
    r = _start(server_client, member)
    assert r.status_code == 200, r.text
    assert spy.greetings == [member]  # tx.after_commit 已在请求返回前跑完
    assert _marker_count(server_client, member) == 1  # 幂等标记落一条


def test_greeting_no_repeat_on_restart(server_client: TestClient) -> None:
    """重启（再次 START）不重复问候——标记 airtight（PRD FR-1.4 一次性）。"""
    member = _create_agent(server_client)
    _set_greeting(server_client, True)
    spy = _LifecycleSpyHub()
    server_client.app.state.daemon_hub = spy  # type: ignore[attr-defined]
    _start(server_client, member)  # 首次问候
    _start(server_client, member)  # 二次上线：命中标记，跳过
    assert spy.greetings == [member]
    assert _marker_count(server_client, member) == 1


def test_greeting_offline_precheck_no_marker(server_client: TestClient) -> None:
    """预检离线 → 不落标记（下次上线仍可问候）。send_lifecycle 成功但 online=False 的构造探针。"""
    member = _create_agent(server_client)
    _set_greeting(server_client, True)
    spy = _LifecycleSpyHub(online=False)
    server_client.app.state.daemon_hub = spy  # type: ignore[attr-defined]
    r = _start(server_client, member)
    assert r.status_code == 200, r.text
    assert spy.greetings == []
    assert _marker_count(server_client, member) == 0


def test_greeting_failed_start_no_action(server_client: TestClient) -> None:
    """START ack=failed → 不问候（未真正上线）。"""
    member = _create_agent(server_client)
    _set_greeting(server_client, True)
    spy = _LifecycleSpyHub(start_result="failed")
    server_client.app.state.daemon_hub = spy  # type: ignore[attr-defined]
    _start(server_client, member)
    assert spy.greetings == []
    assert _marker_count(server_client, member) == 0
