# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""
TRPC Agent Toolset System Core Abstractions.

This module defines the fundamental building blocks for creating and managing tool
collections (toolsets) in the TRPC Agent framework. Key components include:

1. Core Abstractions:
   - ToolPredicate: Protocol for dynamic tool filtering
   - BaseToolSet: Abstract base class for all toolsets

2. Key Features:
   - Context-aware tool selection
   - Runtime protocol checking
   - Resource lifecycle management
   - Thread-safe tool access

3. Implementation Patterns:
   - Filter tools based on invocation context
   - Clean up resources in close()
   - Support for both static and dynamic tool collections

Example Usage:
    class MyToolset(BaseToolSet):
        async def get_tools(self, context=None):
            return [tool1, tool2] if context else []

        async def close(self):
            # Cleanup resources
"""
from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from typing import Callable
from typing import List
from typing import Optional
from typing import Protocol
from typing import TYPE_CHECKING
from typing import Union
from typing import runtime_checkable

from ._tool import ToolABC

if TYPE_CHECKING:
    from trpc_agent_sdk.context import InvocationContext


@runtime_checkable
class ToolPredicate(Protocol):
    """Base class for a predicate that defines the interface to decide whether a

    tool should be exposed to LLM. Toolset implementer could consider whether to
    accept such instance in the toolset's constructor and apply the predicate in
    get_tools method.
    """

    def __call__(self, tool: ToolABC, invocation_context: Optional[InvocationContext] = None) -> bool:
        """Decide whether the passed-in tool should be exposed to LLM based on the

        current context. True if the tool is usable by the LLM.

        It's used to filter tools in the toolset.
        """


class ToolSetABC(ABC):
    """Base class for toolset.

    A toolset is a collection of tools that can be used by an agent.
    """
    """The base class for all tools."""

    def __init__(self,
                 *,
                 tool_filter: Optional[Union[ToolPredicate, List[str]]] = None,
                 is_include_all_tools: bool = True,
                 name: str = ''):
        self.name = name
        self._tool_filter: Optional[Union[ToolPredicate, List[str]]] = tool_filter
        self._is_include_all_tools: bool = is_include_all_tools

    def initialize(self) -> None:
        """Initialize the toolset."""
        return

    def add_tools(self, tools: list[Union[ToolABC, Callable]]):
        """Add tools to the toolset."""
        pass

    def _is_tool_selected(self, tool: ToolABC, invocation_context: InvocationContext | None) -> bool:
        """
      Args:
        tool: The tool to check.
        invocation_context: The invocation context.

      Returns:
        True if the tool should be selected, False otherwise.
      """
        if not self._tool_filter or self._is_include_all_tools:
            return True

        if isinstance(self._tool_filter, ToolPredicate):
            return self._tool_filter(tool, invocation_context)

        if isinstance(self._tool_filter, list):
            return tool.name in self._tool_filter

        return False

    @abstractmethod
    async def get_tools(self, invocation_context: Optional[InvocationContext] = None) -> list[ToolABC]:
        """Return all tools in the toolset based on the provided context.

        Args:
          readonly_context (AgentContext, optional): Context used to filter tools
            available to the agent. If None, all tools in the toolset are returned.

        Returns:
          list[ToolABC]: A list of tools available under the specified context.
        """

    async def close(self) -> None:
        """Performs cleanup and releases resources held by the toolset.

        NOTE: This method is invoked, for example, at the end of an agent server's
        lifecycle or when the toolset is no longer needed. Implementations
        should ensure that any open connections, files, or other managed
        resources are properly released to prevent leaks.
        """
