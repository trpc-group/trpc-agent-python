# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""
TRPC Agent Filter System Core Abstractions.

This module defines the fundamental building blocks for the TRPC Agent filter system,
providing:

1. Base Classes:
   - FilterABC: Abstract base class defining the filter interface
   - FilterResult: Standardized container for filter outputs

2. Type System:
   - FilterType: Enumeration of filter categories
   - Generic type variables for context and request/response types
   - Type aliases for filter handlers and results

3. Core Features:
   - Async-first design with full async generator support
   - Type-safe filter execution pipeline
   - Comprehensive error handling
   - Extensible filter categorization

Example Usage:
    class MyFilter(FilterABC):
        async def _before(self, ctx, req):
            # Pre-processing logic
            yield FilterResult(...)

        async def _after(self, ctx, req):
            # Post-processing logic
            yield FilterResult(...)
"""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
from enum import IntEnum
from enum import unique
from typing import Any
from typing import AsyncGenerator
from typing import Awaitable
from typing import Callable
from typing import Generic
from typing import Optional
from typing import TYPE_CHECKING
from typing import Tuple
from typing import TypeVar

if TYPE_CHECKING:
    from trpc_agent_sdk.context import AgentContext

FilterRspType = TypeVar('FilterRspType')


@dataclass
class FilterResult(Generic[FilterRspType]):
    """Filter result."""
    """Standardized result container for filter operations.

    Attributes:
        rsp: Response data produced by the filter (optional)
        error: Exception encountered during execution (optional)
    """
    rsp: Optional[FilterRspType] = None
    """The response data produced by filter execution."""

    error: Optional[Exception] = None
    """The exception encountered during filter execution."""

    is_continue: bool = True
    """Whether to continue processing."""

    def __iter__(self):
        return iter([self.rsp, self.error])


# Type aliases
FilterReturnType = Tuple[Any, Optional[Exception]]
"""Type alias for filter return value (result, error) tuple."""

FilterHandleType = Callable[[], Awaitable[FilterReturnType]]
"""Type alias for async filter handler function."""

FilterAsyncGenReturnType = AsyncGenerator[FilterResult, None]
"""Type alias for async generator filter return type."""

FilterAsyncGenHandleType = Callable[[], AsyncGenerator[FilterResult, None]]
"""Type alias for async generator filter handler function."""


@unique
class FilterType(IntEnum):
    """Enumeration of filter types used in the filtering system.

    Each type represents a distinct category of filters with specific purposes.
    The @unique decorator ensures all values are distinct.
    """

    # Filter categories
    UNSUPPORTED = -1
    """Unsupported filters."""

    TOOL = 0
    """Filters for tool processing and manipulation."""

    MODEL = 1
    """Filters for model processing and manipulation."""

    AGENT = 2
    """Filters for agent processing and manipulation."""


class FilterABC(ABC):
    """Abstract base class defining the filter interface.

    All concrete filters must implement these methods to be compatible
    with the filter management system.
    """

    def __init__(self) -> None:
        super().__init__()
        self._type = FilterType.UNSUPPORTED
        self._name = ""

    @property
    def full_name(self):
        """Get the full name of the filter."""
        return f"{self._type.name}_{self._name}"

    @property
    def type(self) -> FilterType:
        """Get the filter type."""
        return self._type

    @type.setter
    def type(self, value: FilterType):
        """Set the filter type."""
        self._type = value

    @property
    def name(self) -> str:
        """Get the filter name."""
        return self._name

    @name.setter
    def name(self, value: str):
        """Set the filter name."""
        self._name = value

    @abstractmethod
    async def _before(self, ctx: "AgentContext", req: Any, rsp: FilterResult):
        """Execute before.

        Args:
            ctx: AgentContext
            req: Request data
            rsp: Response data, will be used to store the result of the filter

        Returns:
            None
        """
        return None

    @abstractmethod
    async def _after(self, ctx: "AgentContext", req: Any, rsp: FilterResult):
        """Execute after.

        Args:
            ctx: AgentContext
            req: Request data
            rsp: Response data, will be used to store the result of the filter
        Returns:
            None
        """
        return None

    @abstractmethod
    async def _after_every_stream(self, ctx: "AgentContext", req: Any, rsp: FilterResult) -> None:
        """Execute after every stream.

        Args:
            ctx: AgentContext
            req: Request data
            rsp: Response data, will be used to store the result of the filter
        Returns:
            None
        """
        return None

    def _create_err_str(self, msg: str) -> str:
        """Create an error string with filter information.

        Args:
            msg: Error message

        Returns:
            str: Formatted error string
        """
        return f"filter type: '{self._type.name}', name: '{self._name}': ({msg})"

    @abstractmethod
    async def run_stream(self, ctx: "AgentContext", req: Any,
                         handle: FilterAsyncGenHandleType) -> FilterAsyncGenReturnType:
        """Execute the full filter lifecycle (before -> handle -> after).

        Args:
            ctx: AgentContext
            req: Request data
            handle: Next handler in the chain

        Returns:
            FilterResult: Combined result of all operations
        """

    @abstractmethod
    async def run(self, ctx: "AgentContext", req: Any, handle: FilterHandleType) -> FilterResult:
        """Execute the full filter lifecycle (before -> handle -> after).

        Args:
            ctx: AgentContext
            req: Request data
            handle: Next handler in the chain

        Returns:
            FilterResult: Combined result of all operations
        """
