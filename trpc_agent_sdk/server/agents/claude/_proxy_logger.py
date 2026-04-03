# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
#
"""Claude proxy logger for TRPC Agent framework."""

import logging
import os
from typing import Optional

from trpc_agent_sdk.log import BaseLogger
from trpc_agent_sdk.log import DefaultLogger
from trpc_agent_sdk.log import LogLevel
from trpc_agent_sdk.log import register_logger
from trpc_agent_sdk.log import set_default_logger


class ProxyLogger(DefaultLogger):
    """File-only logger for the Anthropic proxy server."""

    def __init__(
        self,
        name: str = "anthropic_proxy",
        log_file: str = "anthropic_proxy.log",
        min_level: LogLevel = LogLevel.INFO,
    ):
        super().__init__(name=name, min_level=min_level)
        self._log_file = log_file

        self._remove_console_handlers()
        self._add_file_handler()

    def _remove_console_handlers(self) -> None:
        for handler in list(self.logger.handlers):
            if isinstance(handler, logging.StreamHandler):
                self.logger.removeHandler(handler)
                handler.close()

    def _add_file_handler(self) -> None:
        absolute_path = os.path.abspath(self._log_file)
        for handler in self.logger.handlers:
            if isinstance(handler, logging.FileHandler) and os.path.abspath(getattr(handler, "baseFilename",
                                                                                    "")) == absolute_path:
                return

        file_handler = logging.FileHandler(self._log_file)
        file_handler.setLevel(self._convert_log_level(self.min_level))
        formatter = logging.Formatter(
            "[%(asctime)s][%(levelname)s][%(name)s][%(filename)s:%(lineno)d][%(process)d] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)

    def debug(self, format_str: str, *args, **kwargs):
        if self.min_level.value <= LogLevel.DEBUG.value:
            kwargs.setdefault("stacklevel", 3)
            super().debug(format_str, *args, **kwargs)

    def info(self, format_str: str, *args, **kwargs):
        if self.min_level.value <= LogLevel.INFO.value:
            kwargs.setdefault("stacklevel", 3)
            super().info(format_str, *args, **kwargs)

    def warning(self, format_str: str, *args, **kwargs):
        if self.min_level.value <= LogLevel.WARNING.value:
            kwargs.setdefault("stacklevel", 3)
            super().warning(format_str, *args, **kwargs)

    def error(self, format_str: str, *args, **kwargs):
        if self.min_level.value <= LogLevel.ERROR.value:
            kwargs.setdefault("stacklevel", 3)
            super().error(format_str, *args, **kwargs)

    def fatal(self, format_str: str, *args, **kwargs):
        if self.min_level.value <= LogLevel.FATAL.value:
            kwargs.setdefault("stacklevel", 3)
            super().fatal(format_str, *args, **kwargs)

    def with_fields(self, **kwargs) -> "ProxyLogger":
        new_logger = ProxyLogger(name=self.name, log_file=self._log_file, min_level=self.min_level)
        new_logger.extra_fields.update(self.extra_fields)
        new_logger.extra_fields.update(kwargs)
        return new_logger


_PROXY_LOGGER: Optional[ProxyLogger] = None


def get_proxy_logger(*, set_as_default: bool = False) -> BaseLogger:
    global _PROXY_LOGGER
    if _PROXY_LOGGER is None:
        _PROXY_LOGGER = ProxyLogger()
        register_logger(_PROXY_LOGGER.name, _PROXY_LOGGER)
    if set_as_default:
        set_default_logger(_PROXY_LOGGER)
    return _PROXY_LOGGER
