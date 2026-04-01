# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Default logger implementation for TRPC Agent framework.

This module provides a default logger implementation that uses Python's
standard logging module as the backend. It serves as the reference
implementation and fallback logger for the trpc_agent framework.
"""

import logging
import os
import site
import sys
from typing import Any
from typing import Dict
from typing import Optional

from ._base_logger import BaseLogger
from ._base_logger import LogLevel


class RelativePathFormatter(logging.Formatter):
    """Custom formatter that converts absolute paths to relative paths.

    This formatter detects the base path (venv root or project root) and
    converts absolute file paths to relative paths for cleaner log output.
    """

    def __init__(self, fmt=None, datefmt=None, base_path=None):
        """Initialize the formatter.

        Args:
            fmt: Log format string
            datefmt: Date format string
            base_path: Base path for relative path calculation. If None, auto-detected.
        """
        super().__init__(fmt, datefmt)
        self.base_path = base_path or self._detect_base_path()

    def _detect_base_path(self) -> str:
        """Detect the base path (project root containing venv).

        Returns:
            str: The detected base path
        """
        # Try to find project root from site-packages
        # e.g., /project/venv/lib/python3.x/site-packages -> /project
        try:
            site_packages = site.getsitepackages()
            if site_packages:
                # Go up to venv root, then one more level to project root
                # site-packages -> lib -> python3.x -> venv -> project
                venv_root = os.path.dirname(os.path.dirname(os.path.dirname(site_packages[0])))
                project_root = os.path.dirname(venv_root)
                if os.path.isdir(project_root):
                    return project_root
        except Exception:  # pylint: disable=broad-except
            pass

        # Fallback to current working directory
        return os.getcwd()

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record with relative path.

        Args:
            record: The log record to format

        Returns:
            str: The formatted log message
        """
        # Convert absolute pathname to relative only if file is inside base path
        if record.pathname and self.base_path:
            try:
                abs_pathname = os.path.abspath(record.pathname)
                abs_base = os.path.abspath(self.base_path)
                # Only use relative path if file is inside base path
                if abs_pathname.startswith(abs_base + os.sep):
                    record.pathname = os.path.relpath(abs_pathname, abs_base)
            except ValueError:
                # On Windows, relpath fails across different drives
                pass
        return super().format(record)


class DefaultLogger(BaseLogger):
    """Default logger implementation using Python's standard logging.

    This logger uses Python's built-in logging module as the backend and
    provides a simple, reliable logging solution for trpc_agent.

    Attributes:
        logger: The underlying Python logger instance
        extra_fields: Additional fields to include in all log messages
    """

    def __init__(self,
                 name: str = "trpc_agent",
                 min_level: LogLevel = LogLevel.INFO,
                 extra_fields: Optional[Dict[str, Any]] = None):
        """Initialize the default logger.

        Args:
            name: Logger name identifier
            min_level: Minimum log level to process
            extra_fields: Additional fields to include in all log messages
        """
        super().__init__(name, min_level)
        self.logger = logging.getLogger(name)
        self.extra_fields = extra_fields or {}

        # Set up the logger if it hasn't been configured
        if not self.logger.handlers:
            self._setup_logger()

    def _setup_logger(self):
        """Set up the Python logger with default configuration."""
        # Create console handler
        console_handler = logging.StreamHandler(sys.stdout)

        # Create formatter with relative path support
        # Use standard logging format specifiers with stacklevel for correct caller location
        formatter = RelativePathFormatter(
            '[%(asctime)s][%(levelname)s][%(name)s][%(pathname)s:%(lineno)d][%(process)d] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S')
        console_handler.setFormatter(formatter)

        # Add handler to logger
        self.logger.addHandler(console_handler)

        # Set level
        self.logger.setLevel(self._convert_log_level(self.min_level))

        # Prevent propagation to avoid duplicate logs
        self.logger.propagate = False

    def _convert_log_level(self, level: LogLevel) -> int:
        """Convert LogLevel enum to Python logging level.

        Args:
            level: LogLevel enum value

        Returns:
            int: Python logging level constant
        """
        mapping = {
            LogLevel.TRACE: logging.DEBUG,
            LogLevel.DEBUG: logging.DEBUG,
            LogLevel.INFO: logging.INFO,
            LogLevel.WARNING: logging.WARNING,
            LogLevel.ERROR: logging.ERROR,
            LogLevel.FATAL: logging.CRITICAL,
        }
        return mapping.get(level, logging.INFO)

    def _ensure_extra_fields(self, **kwargs) -> Dict[str, Any]:
        """Ensure extra fields are properly formatted.

        Args:
            **kwargs: Keyword arguments that may contain 'extra' dict

        Returns:
            Dict[str, Any]: Updated kwargs with properly formatted extra fields
        """
        if 'extra' not in kwargs:
            kwargs['extra'] = {}

        # Add any additional fields
        kwargs['extra'].update(self.extra_fields)

        return kwargs

    def debug(self, format_str: str, *args, **kwargs):
        """Log a debug message."""
        if self.min_level.value <= LogLevel.DEBUG.value:
            kwargs = self._ensure_extra_fields(**kwargs)
            stacklevel = kwargs.pop('stacklevel', 1)
            self.logger.debug(format_str, *args, stacklevel=stacklevel, **kwargs)

    def info(self, format_str: str, *args, **kwargs):
        """Log an info message."""
        if self.min_level.value <= LogLevel.INFO.value:
            kwargs = self._ensure_extra_fields(**kwargs)
            stacklevel = kwargs.pop('stacklevel', 1)
            self.logger.info(format_str, *args, stacklevel=stacklevel, **kwargs)

    def warning(self, format_str: str, *args, **kwargs):
        """Log a warning message."""
        if self.min_level.value <= LogLevel.WARNING.value:
            kwargs = self._ensure_extra_fields(**kwargs)
            stacklevel = kwargs.pop('stacklevel', 1)
            self.logger.warning(format_str, *args, stacklevel=stacklevel, **kwargs)

    def error(self, format_str: str, *args, **kwargs):
        """Log an error message."""
        if self.min_level.value <= LogLevel.ERROR.value:
            kwargs = self._ensure_extra_fields(**kwargs)
            stacklevel = kwargs.pop('stacklevel', 1)
            self.logger.error(format_str, *args, stacklevel=stacklevel, **kwargs)

    def fatal(self, format_str: str, *args, **kwargs):
        """Log a fatal message."""
        if self.min_level.value <= LogLevel.FATAL.value:
            kwargs = self._ensure_extra_fields(**kwargs)
            stacklevel = kwargs.pop('stacklevel', 1)
            self.logger.critical(format_str, *args, stacklevel=stacklevel, **kwargs)

    def set_level(self, level: LogLevel):
        """Set the minimum log level."""
        super().set_level(level)
        self.logger.setLevel(self._convert_log_level(level))

    def with_fields(self, **kwargs) -> 'DefaultLogger':
        """Create a logger with additional fields.

        Args:
            **kwargs: Additional fields to include in logs

        Returns:
            DefaultLogger: New logger instance with additional fields
        """
        new_extra_fields = self.extra_fields.copy()
        new_extra_fields.update(kwargs)

        return DefaultLogger(name=self.name, min_level=self.min_level, extra_fields=new_extra_fields)
