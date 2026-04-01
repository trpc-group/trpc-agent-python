# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Base logger interface for TRPC Agent framework.

This module defines the abstract base class that all loggers in the trpc_agent
framework must implement. It provides a unified interface for logging operations.
"""

from abc import ABC
from abc import abstractmethod
from enum import Enum
from enum import unique


@unique
class LogLevel(Enum):
    """Log level enumeration."""
    TRACE = 1
    DEBUG = 2
    INFO = 3
    WARNING = 4
    ERROR = 5
    FATAL = 6


class BaseLogger(ABC):
    """Abstract base class for all loggers in TRPC Agent framework.

    This class defines the interface that all logger implementations must follow.
    It provides methods for different log levels and allows for custom metadata.

    Attributes:
        name: The logger name identifier
        min_level: Minimum log level to process
    """

    def __init__(self, name: str = "default", min_level: LogLevel = LogLevel.DEBUG):
        """Initialize the base logger.

        Args:
            name: Logger name identifier
            min_level: Minimum log level to process
        """
        self.name = name
        self.min_level = min_level

    @abstractmethod
    def debug(self, format_str: str, *args, **kwargs):
        """Log a debug message.

        Args:
            format_str: Format string for the message
            *args: Arguments for format string
            **kwargs: Additional keyword arguments (may include 'extra' dict)
        """
        pass

    @abstractmethod
    def info(self, format_str: str, *args, **kwargs):
        """Log an info message.

        Args:
            format_str: Format string for the message
            *args: Arguments for format string
            **kwargs: Additional keyword arguments (may include 'extra' dict)
        """
        pass

    @abstractmethod
    def warning(self, format_str: str, *args, **kwargs):
        """Log a warning message.

        Args:
            format_str: Format string for the message
            *args: Arguments for format string
            **kwargs: Additional keyword arguments (may include 'extra' dict)
        """
        pass

    @abstractmethod
    def error(self, format_str: str, *args, **kwargs):
        """Log an error message.

        Args:
            format_str: Format string for the message
            *args: Arguments for format string
            **kwargs: Additional keyword arguments (may include 'extra' dict)
        """
        pass

    @abstractmethod
    def fatal(self, format_str: str, *args, **kwargs):
        """Log a fatal message.

        Args:
            format_str: Format string for the message
            *args: Arguments for format string
            **kwargs: Additional keyword arguments (may include 'extra' dict)
        """
        pass

    def set_level(self, level: LogLevel):
        """Set the minimum log level.

        Args:
            level: Minimum log level to process
        """
        self.min_level = level

    def with_fields(self, **kwargs) -> 'BaseLogger':
        """Create a logger with additional fields.

        Args:
            **kwargs: Additional fields to include in logs

        Returns:
            BaseLogger: Logger instance with additional fields
        """
        # Default implementation returns self
        # Subclasses can override to provide field support
        return self
