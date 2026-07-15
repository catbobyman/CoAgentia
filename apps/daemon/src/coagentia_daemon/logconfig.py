"""daemon 进程级文件日志装配（B-4 可观测性）。

诊断事件（agent.*）走 AdapterSink→server；本文件日志是 **daemon 自身进程**的现场可观测面
——JSON-RPC 帧收发、握手、子进程生命周期、stderr——供 codex 挂死等真机排查用：server 侧
诊断为空（挂死时正是如此）时，daemon.log 是唯一现场。

- 落盘 = `~/.coagentia/daemon/daemon.log`（`DataPaths.log_path`），RotatingFileHandler + UTF-8。
- 级别由 env `COAGENTIA_DAEMON_LOG_LEVEL`（默认 INFO；帧原文在 DEBUG）；排查 codex 挂死时设 DEBUG。
- **只 daemon 主进程装配**（cli 非 mcp 路径）：mcp 子进程可并发多个，同写一文件会交错——故不装配。
- 幂等：重复调用只调级别、不重复挂 handler（`coagentia_daemon` 命名空间单 handler）。
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

from coagentia_daemon.paths import DataPaths

_ROOT_LOGGER = "coagentia_daemon"
_DEFAULT_LEVEL = "INFO"
_MAX_BYTES = 8 * 1024 * 1024  # 8MB × 3 备份，帧原文 DEBUG 也不至无界
_BACKUPS = 3
_LOG_FORMAT = "%(asctime)s %(levelname)-5s %(name)s: %(message)s"


def _resolve_level(level: int | str | None) -> int:
    if isinstance(level, int):
        return level
    name = (level or os.environ.get("COAGENTIA_DAEMON_LOG_LEVEL", _DEFAULT_LEVEL)).upper()
    resolved = logging.getLevelName(name)
    return resolved if isinstance(resolved, int) else logging.INFO


def setup_file_logging(paths: DataPaths, *, level: int | str | None = None) -> logging.Logger:
    """幂等装配 `coagentia_daemon` 命名空间的文件日志，返回其 logger。

    子模块 `logging.getLogger(__name__)`（如 coagentia_daemon.adapters.codex）继承此 handler。
    幂等以「已挂 RotatingFileHandler」判定（非全局标志——便于单测隔离）：重复调用只更新级别、
    不重复挂 handler（避免同一行写多遍）。
    """
    logger = logging.getLogger(_ROOT_LOGGER)
    logger.setLevel(_resolve_level(level))
    logger.propagate = False  # 不冒泡到 root（避免 mcp 子进程/宿主意外把帧原文打到 stdout）
    if any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
        return logger  # 已装配 → 幂等返回
    paths.daemon_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        paths.log_path, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    logger.addHandler(handler)
    return logger
