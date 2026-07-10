"""数据目录布局（契约 D §9.1/§9.3）：~/.coagentia/ 下 daemon/ 与 agents/ 子树。

- 支持测试注入临时根目录（root 参数）；
- daemon/buffer/（离线遥测缓冲）、daemon/state/<member_id>.json（会话簿记位，A7 用）、
  daemon.log（daemon 自身进程日志，≠ 诊断事件）；
- agents/<member_id>/（Agent Home，daemon 只在创建时建目录、reset_full 清空、查询帧只读遍历）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class DataPaths:
    """~/.coagentia/ 目录布局解析器（root 可注入，默认 %USERPROFILE%\\.coagentia）。"""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else Path.home() / ".coagentia"

    # ---- 目录 ----
    @property
    def daemon_dir(self) -> Path:
        return self.root / "daemon"

    @property
    def buffer_dir(self) -> Path:
        return self.daemon_dir / "buffer"

    @property
    def state_dir(self) -> Path:
        return self.daemon_dir / "state"

    @property
    def agents_dir(self) -> Path:
        return self.root / "agents"

    @property
    def log_path(self) -> Path:
        return self.daemon_dir / "daemon.log"

    def ensure_dirs(self) -> None:
        for d in (self.daemon_dir, self.buffer_dir, self.state_dir, self.agents_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ---- Agent Home（契约 D §9.3：member_id 命名，非名字）----
    def agent_home(self, member_id: str) -> Path:
        return self.agents_dir / member_id

    def ensure_agent_home(self, member_id: str) -> Path:
        home = self.agent_home(member_id)
        home.mkdir(parents=True, exist_ok=True)
        return home

    def clear_agent_home(self, member_id: str) -> None:
        """reset_full：清空 Home 目录内容，目录本身保留（契约 D §5.1/§9.3）。"""
        home = self.agent_home(member_id)
        if not home.exists():
            home.mkdir(parents=True, exist_ok=True)
            return
        for child in home.iterdir():
            if child.is_dir():
                _rmtree(child)
            else:
                child.unlink()

    # ---- 会话簿记（daemon/state/<member_id>.json，契约 D §9.1；A7 --resume 用）----
    def session_file(self, member_id: str) -> Path:
        return self.state_dir / f"{member_id}.json"

    def read_session(self, member_id: str) -> dict[str, Any]:
        f = self.session_file(member_id)
        if not f.exists():
            return {}
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def write_session(self, member_id: str, data: dict[str, Any]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.session_file(member_id).write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )

    def clear_session(self, member_id: str) -> None:
        f = self.session_file(member_id)
        if f.exists():
            f.unlink()


def _rmtree(path: Path) -> None:
    for child in path.iterdir():
        if child.is_dir():
            _rmtree(child)
        else:
            child.unlink()
    path.rmdir()
