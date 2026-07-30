# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unified logger module for TRPC Agent framework.

This module provides the main logging interface for the trpc_agent_sdk framework.
It offers global logging functions that can be used throughout the codebase
and provides logger management capabilities.

Usage:
    import trpc_agent_sdk.log.logger as logger

    logger.info("This is an info message")
    logger.debug("Debug message with args: %s", some_value)
    logger.error("Error occurred", extra={"custom_field": "value"})

    # Set custom logger
    from trpc_agent_sdk.abc import BaseLogger
    custom_logger = MyCustomLogger()
    logger.set_logger(custom_logger)
"""

import sys
from typing import Dict
from typing import Optional

from ._base_logger import BaseLogger
from ._default_logger import DefaultLogger

# Global logger registry
_loggers: Dict[str, BaseLogger] = {}  # pylint: disable=invalid-name
_default_logger_name: str = "default"  # pylint: disable=invalid-name
_current_logger: Optional[BaseLogger] = None  # pylint: disable=invalid-name

# Initialize with default logger
_default_logger: BaseLogger = DefaultLogger(name="trpc_agent_sdk")  # pylint: disable=invalid-name
_loggers[_default_logger_name] = _default_logger  # pylint: disable=invalid-name
_current_logger: BaseLogger = _default_logger  # pylint: disable=invalid-name


def get_logger(name: Optional[str] = None) -> BaseLogger:
    """Get a logger by name.

    Args:
        name: Logger name. If None, returns the current default logger.

    Returns:
        BaseLogger: The requested logger instance
    """
    if name is None:
        return _current_logger

    if name not in _loggers:
        warning("Logger '%s' not found, using default logger", name)
        return _current_logger

    return _loggers[name]


def set_logger(logger: BaseLogger, name: Optional[str] = None):
    """Set a logger instance.

    Args:
        logger: The logger instance to set
        name: Logger name. If None, sets as the default logger.
    """
    global _current_logger  # pylint: disable=invalid-name

    if name is None:
        name = _default_logger_name
        _current_logger = logger

    _loggers[name] = logger


def register_logger(name: str, logger: BaseLogger):
    """Register a named logger.

    Args:
        name: Logger name
        logger: The logger instance to register
    """
    _loggers[name] = logger


# Stack level for logging to show correct caller location
# The call stack is: user_code -> logger.info() -> _current_logger.info() -> self.logger.info()
# So we need stacklevel=3 to skip the wrapper functions
_STACK_LEVEL = 3


def debug(format_str: str, *args, **kwargs):
    """Log a debug message.

    Args:
        format_str: Format string for the message
        *args: Arguments for format string
        **kwargs: Additional keyword arguments (may include 'extra' dict)
    """
    try:
        kwargs['stacklevel'] = _STACK_LEVEL
        _current_logger.debug(format_str, *args, **kwargs)
    except Exception as err:  # pylint: disable=broad-except
        error("log err! format_str: %s; args: %s; err: %s", format_str, str(args), repr(err))


def info(format_str: str, *args, **kwargs):
    """Log an info message.

    Args:
        format_str: Format string for the message
        *args: Arguments for format string
        **kwargs: Additional keyword arguments (may include 'extra' dict)
    """
    try:
        kwargs['stacklevel'] = _STACK_LEVEL
        _current_logger.info(format_str, *args, **kwargs)
    except Exception as err:  # pylint: disable=broad-except
        error("log err! format_str: %s; args: %s; err: %s", format_str, str(args), repr(err))


def warning(format_str: str, *args, **kwargs):
    """Log a warning message.

    Args:
        format_str: Format string for the message
        *args: Arguments for format string
        **kwargs: Additional keyword arguments (may include 'extra' dict)
    """
    try:
        kwargs['stacklevel'] = _STACK_LEVEL
        _current_logger.warning(format_str, *args, **kwargs)
    except Exception as err:  # pylint: disable=broad-except
        error("log err! format_str: %s; args: %s; err: %s", format_str, str(args), repr(err))


def error(format_str: str, *args, **kwargs):
    """Log an error message.

    Args:
        format_str: Format string for the message
        *args: Arguments for format string
        **kwargs: Additional keyword arguments (may include 'extra' dict)
    """
    try:
        kwargs['stacklevel'] = _STACK_LEVEL
        _current_logger.error(format_str, *args, **kwargs)
    except Exception as err:  # pylint: disable=broad-except
        # Fallback to print if logger fails
        print(f"log err! format_str: {format_str}; args: {args}; err: {repr(err)}", file=sys.stderr)


def fatal(format_str: str, *args, **kwargs):
    """Log a fatal message.

    Args:
        format_str: Format string for the message
        *args: Arguments for format string
        **kwargs: Additional keyword arguments (may include 'extra' dict)
    """
    try:
        kwargs['stacklevel'] = _STACK_LEVEL
        _current_logger.fatal(format_str, *args, **kwargs)
    except Exception as err:  # pylint: disable=broad-except
        error("log err! format_str: %s; args: %s; err: %s", format_str, str(args), repr(err))


def with_fields(**kwargs) -> BaseLogger:
    """Create a logger with additional fields.

    Args:
        **kwargs: Additional fields to include in logs

    Returns:
        BaseLogger: Logger instance with additional fields
    """
    return _current_logger.with_fields(**kwargs)


# Convenience function for backward compatibility
def set_default_logger(logger: BaseLogger):
    """Set the default logger.

    Args:
        logger: The logger instance to set as default
    """
    set_logger(logger)
