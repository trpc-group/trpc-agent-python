# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""
TRPC Agent Logging Module

This module provides a unified logging system for the trpc_agent framework.
"""

from . import _logger as logger
from ._base_logger import BaseLogger
from ._base_logger import LogLevel
from ._default_logger import DefaultLogger
from ._default_logger import RelativePathFormatter
from ._logger import debug
from ._logger import error
from ._logger import fatal
from ._logger import get_logger
from ._logger import info
from ._logger import register_logger
from ._logger import set_default_logger
from ._logger import set_logger
from ._logger import warning
from ._logger import with_fields

__all__ = [
    "logger",
    "BaseLogger",
    "LogLevel",
    "DefaultLogger",
    "RelativePathFormatter",
    "debug",
    "error",
    "fatal",
    "get_logger",
    "info",
    "register_logger",
    "set_default_logger",
    "set_logger",
    "warning",
    "with_fields",
]
