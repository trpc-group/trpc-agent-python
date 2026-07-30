# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
#
# Below code are copy and modified from https://github.com/ag-ui-protocol/ag-ui.git
#
# MIT License
#
# Copyright (c) 2025
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
"""Client-side proxy tool implementation for AG-UI protocol tools."""

import asyncio
import inspect
from typing import Any
from typing import Dict
from typing import Optional

from ag_ui.core import Tool as AGUITool
from trpc_agent_sdk import types
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.tools import LongRunningFunctionTool


class ClientProxyTool(LongRunningFunctionTool):
    """A proxy tool that bridges AG-UI protocol tools to TRPC Agent.

    This tool appears as a normal TRPC tool to the agent, but when executed,
    it emits AG-UI protocol events and waits for the client to execute
    the actual tool and return results.

    Inherits from LongRunningFunctionTool for proper TRPC long-running behavior.
    """

    def __init__(self, ag_ui_tool: AGUITool, event_queue: asyncio.Queue):
        """Initialize the client proxy tool.

        Args:
            ag_ui_tool: The AG-UI tool definition
            event_queue: Queue to emit AG-UI events
        """
        self.ag_ui_tool = ag_ui_tool
        self.event_queue = event_queue

        # Create dynamic function with proper parameter signatures for TRPC inspection
        # This allows TRPC to extract parameters from user requests correctly
        sig_params = []

        # Extract parameters from AG-UI tool schema
        parameters = ag_ui_tool.parameters
        if isinstance(parameters, dict) and "properties" in parameters:
            for param_name in parameters["properties"].keys():
                # Create parameter with proper type annotation
                sig_params.append(
                    inspect.Parameter(param_name, inspect.Parameter.KEYWORD_ONLY, default=None, annotation=Any))

        # Create the async function that will be wrapped by LongRunningFunctionTool
        async def proxy_tool_func(**kwargs) -> Any:
            # Access the original args and tool_context that were stored in run_async
            original_args = getattr(self, "_current_args", kwargs)
            original_tool_context = getattr(self, "_current_tool_context", None)
            return await self._execute_proxy_tool(original_args, original_tool_context)

        # Set the function name, docstring, and signature to match the AG-UI tool
        proxy_tool_func.__name__ = ag_ui_tool.name
        proxy_tool_func.__doc__ = ag_ui_tool.description

        # Create new signature with extracted parameters
        if sig_params:
            proxy_tool_func.__signature__ = inspect.Signature(sig_params)

        # Initialize LongRunningFunctionTool with the proxy function
        super().__init__(proxy_tool_func)

    def _get_declaration(self) -> Optional[types.FunctionDeclaration]:
        """Create FunctionDeclaration from AG-UI tool parameters.

        We override this instead of delegating to the wrapped tool because
        the TRPC's automatic function calling has difficulty parsing our
        dynamically created function signature without proper type annotations.
        """
        logger.debug("_get_declaration called for %s", self.name)
        logger.debug("AG-UI tool parameters: %s", self.ag_ui_tool.parameters)

        # Convert AG-UI parameters (JSON Schema) to TRPC format
        parameters = self.ag_ui_tool.parameters

        # Ensure it's a proper object schema
        if not isinstance(parameters, dict):
            parameters = {"type": "object", "properties": {}}
            logger.warning("Tool %s had non-dict parameters, using empty schema", self.name)

        # Create FunctionDeclaration
        function_declaration = types.FunctionDeclaration(name=self.name,
                                                         description=self.description,
                                                         parameters=types.Schema.model_validate(parameters))
        logger.debug("Created FunctionDeclaration for %s: %s", self.name, function_declaration)
        return function_declaration

    async def _run_async_impl(self, *, args: dict[str, Any], tool_context: InvocationContext) -> Any:
        """Execute the tool by storing args/context and calling parent implementation.

        Args:
            args: The arguments for the tool call
            tool_context: The TRPC tool context

        Returns:
            None for long-running tools (client handles execution)
        """
        # Store args and context for proxy function access
        self._current_args = args
        self._current_tool_context = tool_context

        # Call parent LongRunningFunctionTool implementation
        return await super()._run_async_impl(args=args, tool_context=tool_context)

    async def _execute_proxy_tool(self, args: Dict[str, Any], tool_context: InvocationContext) -> Any:
        """Execute the proxy tool logic and return None.

        Note: Tool call events (TOOL_CALL_START, TOOL_CALL_ARGS, TOOL_CALL_END) are NOT emitted here.
        They are emitted by EventTranslator.translate_lro_function_calls() when processing
        the LongRunningEvent that is generated by the parent LongRunningFunctionTool class.
        This avoids duplicate event emission.

        Args:
            args: Tool arguments from TRPC
            tool_context: TRPC tool context

        Returns:
            None for long-running tools (client handles the actual execution)
        """
        logger.debug("Proxy tool execution: %s", self.ag_ui_tool.name)
        logger.debug("Arguments received: %s", args)
        logger.debug("Tool context type: %s", type(tool_context))

        # Return None for long-running tools - client handles the actual execution
        # The LongRunningEvent generated by parent class will be translated to
        # AG-UI tool call events by EventTranslator.translate_lro_function_calls()
        return None

    def __repr__(self) -> str:
        """String representation of the proxy tool."""
        return f"ClientProxyTool(name='{self.name}', ag_ui_tool='{self.ag_ui_tool.name}')"
