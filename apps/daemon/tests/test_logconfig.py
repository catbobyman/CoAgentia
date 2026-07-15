"""daemon 文件日志装配单测（B-4 可观测性）：装配/幂等/env 级别/写盘/适配器帧日志。"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from coagentia_daemon.logconfig import _ROOT_LOGGER, setup_file_logging
from coagentia_daemon.paths import DataPaths


@contextmanager
def _isolated_root_logger() -> Iterator[logging.Logger]:
    """隔离 coagentia_daemon 命名空间 logger：存档→清空→测后关闭新 handler 并还原。

    RotatingFileHandler 必须 close 才释放文件句柄（win32 tmp 清理否则失败）。
    """
    logger = logging.getLogger(_ROOT_LOGGER)
    saved_handlers = logger.handlers[:]
    saved_level = logger.level
    saved_propagate = logger.propagate
    logger.handlers.clear()
    try:
        yield logger
    finally:
        for h in logger.handlers:
            h.close()
        logger.handlers.clear()
        logger.handlers.extend(saved_handlers)
        logger.setLevel(saved_level)
        logger.propagate = saved_propagate


def test_setup_writes_to_daemon_log(tmp_path: Path) -> None:
    with _isolated_root_logger():
        paths = DataPaths(str(tmp_path))
        logger = setup_file_logging(paths, level="DEBUG")
        logger.debug("marker-debug-1")
        logging.getLogger("coagentia_daemon.adapters.codex").info("codex-child-marker")
        for h in logger.handlers:
            h.flush()
        content = paths.log_path.read_text(encoding="utf-8")
    assert "marker-debug-1" in content
    assert "codex-child-marker" in content  # 子模块 logger 继承同 handler


def test_setup_is_idempotent(tmp_path: Path) -> None:
    with _isolated_root_logger() as logger:
        paths = DataPaths(str(tmp_path))
        setup_file_logging(paths)
        setup_file_logging(paths)
        setup_file_logging(paths)
        from logging.handlers import RotatingFileHandler

        file_handlers = [h for h in logger.handlers if isinstance(h, RotatingFileHandler)]
        assert len(file_handlers) == 1  # 多次调用不重复挂 handler（否则每行写多遍）


def test_env_level_controls_threshold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COAGENTIA_DAEMON_LOG_LEVEL", "WARNING")
    with _isolated_root_logger():
        paths = DataPaths(str(tmp_path))
        logger = setup_file_logging(paths)  # 无显式 level → 读 env
        assert logger.level == logging.WARNING
        logger.info("info-should-be-filtered")
        logger.warning("warning-should-appear")
        for h in logger.handlers:
            h.flush()
        content = paths.log_path.read_text(encoding="utf-8")
    assert "warning-should-appear" in content
    assert "info-should-be-filtered" not in content


def test_explicit_level_overrides_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COAGENTIA_DAEMON_LOG_LEVEL", "ERROR")
    with _isolated_root_logger():
        logger = setup_file_logging(DataPaths(str(tmp_path)), level="DEBUG")
        assert logger.level == logging.DEBUG  # 显式 level 优先于 env


def test_root_does_not_propagate(tmp_path: Path) -> None:
    """propagate=False：帧原文不冒泡到 root（防 mcp 子进程/宿主把帧打到 stdout）。"""
    with _isolated_root_logger() as logger:
        setup_file_logging(DataPaths(str(tmp_path)))
        assert logger.propagate is False
