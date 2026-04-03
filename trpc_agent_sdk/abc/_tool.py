# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""
TRPC Agent Tool System Base Class.

This module defines the fundamental ToolABC class that serves as the foundation for all
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
    class MyTool(ToolABC):
        async def run_async(self, *, tool_context, args):
            # Tool implementation
            return result
"""

# Standard library imports (system modules)
from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from typing import Any
from typing import Optional
from typing import TYPE_CHECKING

from ._request import RequestABC

if TYPE_CHECKING:
    from trpc_agent_sdk.context import InvocationContext


class ToolABC(ABC):
    """Base class for all tools."""

    def _get_declaration(self) -> Optional[str]:
        """Gets the OpenAPI specification of this tool in the form of a FunctionDeclaration.

        NOTE
        - Required if subclass uses the default implementation of
          `process_request` to add function declaration to LLM request.
        - Otherwise, can be skipped

        Returns:
          The OpenAPI specification of this tool, or None if it doesn't need to be
          added to ModelRequest.config.
        """
        return None

    @abstractmethod
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

    @abstractmethod
    async def process_request(self, *, tool_context: InvocationContext, llm_request: RequestABC) -> None:
        """Processes the outgoing LLM request for this tool.

        Use cases:
        - Most common use case is adding this tool to the LLM request.
        - Some tools may just preprocess the LLM request before it's sent out.

        Args:
          tool_context: The context of the tool.
          llm_request: The outgoing LLM request, mutable this method.
        """
