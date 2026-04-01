# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Default toolset implementation for TRPC Agent framework.

This module provides the core implementation of BaseToolSet that serves as
the foundation for tool management in agent system, including:
- Tool registration and management
- Tool lifecycle handling
- Tool lookup and retrieval
"""

# System modules
from typing import Callable
from typing import List
from typing import Optional
from typing import Union
from typing_extensions import override

from trpc_agent_sdk.abc import ToolSetABC as BaseToolSet
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.log import logger

from ._base_tool import BaseTool
from ._constants import DEFAULT_TOOLSET_NAME
from ._function_tool import FunctionTool
from ._registry import get_tool
from ._registry import register_tool_set


@register_tool_set(name=DEFAULT_TOOLSET_NAME)
class DefaultToolSet(BaseToolSet):
    """Default implementation of BaseToolSet for managing agent tools.

    This class provides core functionality for:
    - Tool registration and management
    - Tool lifecycle handling
    - Tool lookup and retrieval

    Attributes:
        _base_tools: Dictionary mapping tool names to tool instances
    """

    def __init__(self):
        """Initialize the DefaultToolSet with empty tool collection."""
        super().__init__()

    @override
    def initialize(self) -> None:
        """Initialize the toolset and validate required parameters.

        Raises:
            ValueError: If required parameters are not properly set
        """
        super().initialize()
        self._tool_filter = []
        self.__checker_required_params()

    def __checker_required_params(self):
        """Validate that all required parameters are properly initialized.

        Raises:
            ValueError: If _base_tools is None
        """
        if self._tool_filter is None:
            raise ValueError("_base_tools is None.")

    @override
    def add_tools(self, tools: list[Union[BaseTool, Callable, str]]):
        """Add multiple tools to the toolset.

        Args:
            tools: List of tools to add, which can be:
                - BaseTool instances
                - Callable functions (will be wrapped as FunctionTool)
                - Tool names (will be looked up in registry)

        Raises:
            ValueError: If unsupported tool type is provided
        """
        for t in tools:
            if isinstance(t, BaseTool):
                self._tool_filter.append(t)
            elif isinstance(t, Callable):
                tool = FunctionTool(func=t)
                self._tool_filter.append(tool)
            elif isinstance(t, str):
                tool = get_tool(t)
                if tool:
                    self._tool_filter.append(tool)
                else:
                    logger.warning("Tool %s not found.", t)
            else:
                raise ValueError(f"Unsupported tool type: {type(t)}")

    @override
    async def close(self):
        """Clean up resources and close all tools in the toolset.

        Note:
            Currently this is a no-op but should be overridden by subclasses
            that require cleanup logic.
        """
        pass

    @override
    async def get_tools(self, invocation_context: Optional[InvocationContext] = None) -> List[BaseTool]:
        """Retrieve all tools registered in this toolset.

        Args:
            invocation_context: Optional context for tool retrieval

        Returns:
            List of all BaseTool instances in this toolset
        """
        tools = []
        for tool in self._tool_filter or []:
            if self._is_tool_selected(tool, invocation_context):
                tools.append(tool)

        return tools
