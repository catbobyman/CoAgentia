"""输入编码（E §6）+ 命令行拼装 / 配置隔离（E §2/§3）单测。"""

from __future__ import annotations

import json
from pathlib import Path

from coagentia_contracts.constants import DISALLOWED_TOOLS
from coagentia_contracts.daemon import AgentBoot
from coagentia_daemon.adapters import cmdline, encoding

AID = "01K5CMPT00000000000000000A"


def _boot(**kw) -> AgentBoot:
    base = dict(
        agent_member_id=AID,
        name="Pat",
        runtime="claude_code",
        model="claude-opus-4-8",
        home_path="/tmp/home/pat",
        skills=["writing-plans"],
    )
    base.update(kw)
    return AgentBoot(**base)


# ---------------- 输入编码 ----------------


def test_encode_deliver_template_and_frame() -> None:
    msgs = [
        {
            "id": "01K5MSG100000000000000000A",
            "channel_id": "01K5CHAN00000000000000000A",
            "author_member_id": "01K5AUTH00000000000000000A",
            "created_at": "2026-07-09T01:02:03.000Z",
            "body": "你好世界",
        }
    ]
    line = encoding.encode_deliver(
        msgs, reason="mention", thread_root_id="01K5THRD00000000000000000A"
    )
    obj = json.loads(line)
    assert obj["type"] == "user"
    text = obj["message"]["content"][0]["text"]
    assert "[投递" in text and "有人 @你" in text  # 批首投递原因
    # 模板 [#频道] @作者 (时间): 正文
    assert "[#01K5CHAN00000000000000000A] @01K5AUTH00000000000000000A " in text
    assert "(2026-07-09T01:02:03.000Z): 你好世界" in text


def test_encode_inject_system_first_line() -> None:
    line = encoding.encode_inject("修复清单如下", {"kind": "repair", "ref": "err-1"})
    text = json.loads(line)["message"]["content"][0]["text"]
    assert text.startswith("[system → 仅你可见] (repair: err-1)\n")
    assert "修复清单如下" in text


def test_user_frame_line_is_single_line() -> None:
    line = encoding.user_frame_line("a\nb")  # 正文含换行也必须是单行 JSON
    assert "\n" not in line
    assert json.loads(line)["message"]["content"][0]["text"] == "a\nb"


# ---------------- 命令行 / 隔离 ----------------


def test_build_argv_core_flags() -> None:
    argv = cmdline.build_argv(_boot(), mcp_config_path="/x/coagentia-mcp.json")
    joined = " ".join(argv)
    assert argv[0] == "claude"
    for flag in (
        "--output-format", "stream-json", "--input-format",
        "--include-partial-messages", "--permission-mode", "bypassPermissions",
        "--verbose", "--mcp-config", "--append-system-prompt",
    ):
        assert flag in argv, flag
    assert "--model" in argv and "claude-opus-4-8" in argv
    # disallowed tools 逐个 argv 元素
    for tool in DISALLOWED_TOOLS:
        assert tool in argv
    assert "--resume" not in argv  # 无 resume 参数
    assert "--strict-mcp-config" in joined


def test_build_argv_resume() -> None:
    argv = cmdline.build_argv(_boot(), resume_session_id="sess-uuid")
    i = argv.index("--resume")
    assert argv[i + 1] == "sess-uuid"


def test_identity_prompt_contains_required_elements() -> None:
    text = cmdline.build_identity_prompt(_boot())
    assert "Pat" in text and AID in text
    assert "coagentia" in text.lower()  # 工具用法
    assert "held" in text.lower()  # 护栏约定


def test_build_env_isolates_config_dir() -> None:
    env = cmdline.build_env("/home/pat", base_env={"PATH": "/usr/bin"})
    assert env["CLAUDE_CONFIG_DIR"] == str(Path("/home/pat") / ".claude")
    assert env["PATH"] == "/usr/bin"


def test_materialize_mcp_config(tmp_path: Path) -> None:
    path = cmdline.materialize_mcp_config(
        tmp_path / ".claude", agent_member_id=AID, server_url="http://s", api_key="cak_x"
    )
    cfg = json.loads(path.read_text(encoding="utf-8"))
    server = cfg["mcpServers"]["coagentia"]
    assert server["type"] == "stdio"
    assert "mcp" in server["args"]
    assert AID in server["args"] and "http://s" in server["args"] and "cak_x" in server["args"]
