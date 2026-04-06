# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
#
# Directly reuse the types from adk-python
# Below code are copy and modified from https://github.com/google/adk-python.git
#
# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""
TRPC Agent Tool System Base Class.

This module defines the fundamental BaseTool class that serves as the foundation for all
tools in the TRPC Agent framework. Key aspects include:

1. Core Functionality:
   - Standardized tool interface
   - Abstract method contracts
   - Tool declaration system
   - Request processing pipeline

2. Key Features:
   - Async-first design
   - Type-safe tool execution
   - LLM request integration
   - Support for long-running operations
   - Configurable API variants

3. Implementation Requirements:
   - Subclasses must implement run_async()
   - Optional _get_declaration() for schema definition
   - Optional process_request() for LLM integration

Example Usage:
    class MyTool(BaseTool):
        async def run_async(self, *, tool_context, args):
            # Tool implementation
            return result
"""

# Standard library imports (system modules)
from __future__ import annotations

from abc import abstractmethod
from functools import partial
from typing import Any
from typing import Optional
from typing import final
from typing_extensions import override

from trpc_agent_sdk.abc import ToolABC
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.context import create_agent_context
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.filter import FilterRunner
from trpc_agent_sdk.filter import FilterType
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import GenerateContentConfig
from trpc_agent_sdk.types import Tool

from ._constants import DEFAULT_API_VARIANT
from ._context_var import reset_tool_var
from ._context_var import set_tool_var


class BaseTool(ToolABC, FilterRunner):
    """Base class for all tools."""

    def __init__(self,
                 *,
                 name: str,
                 description: str,
                 filters_name: Optional[list[str]] = None,
                 filters: Optional[list[BaseFilter]] = None):
        super().__init__(filters_name, filters)
        self._name = name
        self.description = description
        self._type = FilterType.TOOL
        self._init_filters()

    @property
    def name(self) -> str:
        """Get tool name."""
        return self._name

    @property
    def is_streaming(self) -> bool:
        """Whether this tool supports streaming function call arguments.

        When True, the framework will stream partial arguments for this tool
        during LLM generation, enabling real-time display of tool arguments.

        Subclasses that support streaming should override this property to return True.

        Returns:
            bool: True if the tool supports streaming arguments, False otherwise.
        """
        return False

    @property
    def api_variant(self) -> str:
        """Get API variant."""
        return DEFAULT_API_VARIANT

    @override
    def _get_declaration(self) -> Optional[FunctionDeclaration]:
        """Gets the OpenAPI specification of this tool in the form of a FunctionDeclaration.

        NOTE
        - Required if subclass uses the default implementation of
          `process_request` to add function declaration to LLM request.
        - Otherwise, can be skipped

        Returns:
          The FunctionDeclaration of this tool, or None if it doesn't need to be
          added to ModelRequest.config.
        """
        return None

    @final
    async def run_async(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        """Runs the tool with the given arguments and context.

        NOTE
        - Required if this tool needs to run at the client side.
        - Otherwise, can be skipped

        Args:
          args: The LLM-filled arguments.
          tool_context: The context of the tool.

         Returns:
           The result of running the tool.
        """
        agent_context = tool_context.agent_context
        if agent_context is None:
            agent_context = create_agent_context()
            tool_context.agent_context = agent_context

        before_tool_callback = getattr(tool_context.agent, "before_tool_callback", None)
        after_tool_callback = getattr(tool_context.agent, "after_tool_callback", None)
        # Import here to avoid circular import
        from trpc_agent_sdk.agents import ToolCallbackFilter
        extra_filters = [ToolCallbackFilter(before_tool_callback, after_tool_callback)]

        token = set_tool_var(self)
        handler = partial(self._run_async_impl, tool_context=tool_context, args=args)
        try:
            return await self._run_filters(agent_context, args, handler, extra_filters)  # type: ignore
        finally:
            reset_tool_var(token)

    @abstractmethod
    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        """Runs the tool with the given arguments and context.

         NOTE
         - Required if this tool needs to run at the client side.
         - Otherwise, can be skipped

         Args:
           args: The LLM-filled arguments.
           tool_context: The context of the tool.

         Returns:
           The result of running the tool.
         """

    @staticmethod
    def _find_tool_with_function_declarations(llm_request: LlmRequest) -> Optional[Tool]:
        """Finds the first Tool with function declarations in a ModelRequest."""
        if not llm_request.config or not llm_request.config.tools:
            return None
        return next(
            (tool for tool in llm_request.config.tools if isinstance(tool, Tool) and tool.function_declarations), None)

    async def process_request(self, *, tool_context: InvocationContext, llm_request: LlmRequest) -> None:
        """Processes the outgoing LLM request for this tool.

        Use cases:
        - Most common use case is adding this tool to the LLM request.
        - Some tools may just preprocess the LLM request before it's sent out.

        Args:
          tool_context: The context of the tool.
          llm_request: The outgoing LLM request, mutable this method.
        """
        if (function_declaration := self._get_declaration()) is None:
            return

        llm_request.tools_dict[self.name] = self
        if tool_info := self._find_tool_with_function_declarations(llm_request):
            if tool_info.function_declarations is None:
                tool_info.function_declarations = []
            tool_info.function_declarations.append(function_declaration)
        else:
            if not llm_request.config:
                llm_request.config = GenerateContentConfig()
            if not llm_request.config.tools:
                llm_request.config.tools = []
            llm_request.config.tools.append(Tool(function_declarations=[function_declaration]))
