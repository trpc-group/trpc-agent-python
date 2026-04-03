# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Long running tool for TRPC Agent framework."""

from __future__ import annotations

from typing import Callable
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.types import FunctionDeclaration

from ._function_tool import FunctionTool


class LongRunningFunctionTool(FunctionTool):
    """A function tool that returns the result asynchronously.

    This tool is used for long-running operations that may take a significant
    amount of time to complete. The framework will call the function. Once the
    function returns, the response will be returned asynchronously to the
    framework which is identified by the function_call_id.

    Example:
    ```python
    tool = LongRunningFunctionTool(a_long_running_function)
    ```

    Attributes:
        is_long_running: Whether the tool is a long running operation.
    """

    def __init__(self,
                 func: Callable,
                 filters_name: Optional[list[str]] = None,
                 filters: Optional[list[BaseFilter]] = None):
        """Initialize the long running function tool.

        Args:
            func: The function to wrap
            filters_name: List of filter names
            filters: List of filter instances
        """
        super().__init__(func, filters_name, filters=filters)
        self.is_long_running = True

    @override
    def _get_declaration(self) -> Optional[FunctionDeclaration]:
        """Generate the function declaration schema for this long running tool.

        Returns:
            FunctionDeclaration: The OpenAPI-compatible function schema with
                                long-running operation instructions
        """
        declaration = super()._get_declaration()
        if declaration:
            instruction = ("\n\nNOTE: This is a long-running operation. Do not call this tool"
                           " again if it has already returned some intermediate or pending"
                           " status.")
            if declaration.description:
                declaration.description += instruction
            else:
                declaration.description = instruction.lstrip()
        return declaration
