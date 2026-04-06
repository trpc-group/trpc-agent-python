# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""This file is used to forward nanobot/loguru logs to trpc_agent_sdk logger."""

import logging

from loguru import logger as _loguru_logger
from trpc_agent_sdk.log import DefaultLogger
from trpc_agent_sdk.log import LogLevel
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.log import set_logger

from .config import LoggerConfig

_LOGURU_BRIDGE_ENABLED = False


def setup_loguru_bridge() -> None:
    """Forward nanobot/loguru logs to trpc_agent_sdk logger."""
    global _LOGURU_BRIDGE_ENABLED
    if _LOGURU_BRIDGE_ENABLED:
        return

    def _sink(message) -> None:
        record = message.record
        level = str(record.get("level").name).upper()
        rendered = str(record.get("message", "")).rstrip()
        if not rendered:
            return
        source_file = record.get("file").path if record.get("file") else "unknown"
        source_line = record.get("line", 0)
        text = f"[nanobot:{source_file}:{source_line}] {rendered}"

        if level in {"TRACE", "DEBUG"}:
            logger.debug("%s", text)
        elif level == "INFO":
            logger.info("%s", text)
        elif level == "WARNING":
            logger.warning("%s", text)
        else:
            logger.error("%s", text)

    # Replace default loguru sinks to avoid duplicate output.
    _loguru_logger.remove()
    _loguru_logger.add(_sink, level="DEBUG", enqueue=True)
    _LOGURU_BRIDGE_ENABLED = True


def default_logger() -> DefaultLogger:
    lg = DefaultLogger(name="trpc_claw", min_level=LogLevel.INFO)
    file_handler = logging.FileHandler("trpc_claw.log", encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter(
            "[%(asctime)s][%(levelname)s][%(name)s][%(pathname)s:%(lineno)d][%(process)d] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
    lg.logger.addHandler(file_handler)
    return lg


def init_claw_logger(config: LoggerConfig) -> None:
    log_level = LogLevel[config.log_level.upper()]
    lg = DefaultLogger(name=config.name, min_level=log_level)
    if config.log_file:
        file_handler = logging.FileHandler(config.log_file, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(
            config.log_format,
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        lg.logger.addHandler(file_handler)
    set_logger(lg)
    setup_loguru_bridge()
    return lg
