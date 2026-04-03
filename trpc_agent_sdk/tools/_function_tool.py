# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""
Function Tool Adapter Implementation.

This module implements the FunctionTool class which serves as an adapter between
Python functions and the TRPC Agent tooling system. Key capabilities include:

1. Function Wrapping:
   - Converts regular functions into tool-compatible interfaces
   - Handles both sync and async functions
   - Supports callable objects with __call__ method

2. Parameter Handling:
   - Automatic signature inspection
   - Mandatory parameter validation
   - Context injection support

3. Declaration Generation:
   - Automatic OpenAPI schema generation
   - Parameter filtering (e.g. tool_context)
   - API variant support

Key Features:
- Seamless integration of existing functions
- Type-safe execution
- Comprehensive error handling

Example Usage:
    def my_function(param1: str, tool_context: InvocationContext):
        return {"result": f"Processed {param1}"}

    tool = FunctionTool(my_function)
    result = await tool.run_async(
        args={"param1": "value"},
        tool_context=context
    )
"""
import asyncio
import inspect
from typing import Any
from typing import Callable
from typing import Optional
from typing_extensions import override

from pydantic import BaseModel

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.types import FunctionDeclaration

from ._base_tool import BaseTool
from ._constants import INPUT_STREAM
from ._constants import TOOL_CONTEXT
from .utils import build_function_declaration
from .utils import convert_pydantic_args
from .utils import get_mandatory_args


class FunctionTool(BaseTool):
    """A tool that wraps a user-defined Python function.

    Attributes:
    func: The function to wrap.
    """

    def __init__(self,
                 func: Callable[..., Any],
                 filters_name: Optional[list[str]] = None,
                 filters: Optional[list[BaseFilter]] = None):
        """Extract metadata from a callable object."""
        if inspect.isfunction(func) or inspect.ismethod(func):
            # Handle regular functions and methods
            name = func.__name__
            doc = func.__doc__ or ''
        else:
            # Handle objects with __call__ method
            call_method = func.__call__
            name = func.__class__.__name__
            doc = call_method.__doc__ or func.__doc__ or ''
        super().__init__(name=name, description=doc, filters_name=filters_name, filters=filters)
        self.func = func

    @override
    def _get_declaration(self) -> Optional[FunctionDeclaration]:
        """Generate the function declaration schema for this tool.

        Returns:
            FunctionDeclaration: The OpenAPI-compatible function schema
            None: If the function cannot be properly declared

        Notes:
            - Automatically filters out internal params (tool_context, input_stream)
            - Supports API variant configurations
            - Validates the generated schema against the FunctionDeclaration model
        """
        function_decl = FunctionDeclaration.model_validate(
            build_function_declaration(
                func=self.func,
                # The model doesn't understand the function context.
                # input_stream is for streaming tool
                ignore_params=[TOOL_CONTEXT, INPUT_STREAM],
                variant=self.api_variant,
            ))

        return function_decl

    @override
    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        """Execute the wrapped function asynchronously with proper context handling.

        Args:
            tool_context: The execution context containing auth, artifacts etc.
            args: Dictionary of arguments to pass to the function

        Returns:
            Any: The function's return value or error dict if validation fails

        Raises:
            ValueError: If mandatory arguments are missing

        Behavior:
            1. Validates all mandatory arguments are provided
            2. Injects tool_context if required by function signature
            3. Converts dict arguments to Pydantic models when needed
            4. Handles both sync and async functions transparently
            5. Returns empty dict for None returns
        """
        args_to_call = args.copy()
        signature = inspect.signature(self.func)

        # Inject tool_context if required by function signature
        if TOOL_CONTEXT in signature.parameters:
            args_to_call[TOOL_CONTEXT] = tool_context

        # Convert dict arguments to Pydantic models when needed
        args_to_call = convert_pydantic_args(args_to_call, signature)

        # Before invoking the function, we check for if the list of args passed in
        # has all the mandatory arguments or not.
        # If the check fails, then we don't invoke the tool and let the Agent know
        # that there was a missing a input parameter. This will basically help
        # the underlying model fix the issue and retry.
        mandatory_args = get_mandatory_args(self.func)
        missing_mandatory_args = [arg for arg in mandatory_args if arg not in args_to_call]

        if missing_mandatory_args:
            missing_mandatory_args_str = '\n'.join(missing_mandatory_args)
            error_str = f"""Invoking `{self.name}()` failed as the following mandatory input parameters are not present:
{missing_mandatory_args_str}
You could retry calling this tool, but it is IMPORTANT for you to provide all the mandatory parameters."""
            return {'error': error_str}

        # Functions are callable objects, but not all callable objects are functions
        # checking coroutine function is not enough. We also need to check whether
        # Callable's __call__ function is a coroutine function
        if (inspect.iscoroutinefunction(self.func)
                or hasattr(self.func, '__call__') and inspect.iscoroutinefunction(self.func.__call__)):
            res = await self.func(**args_to_call) or {}
        else:
            parallel_tool_calls: bool = getattr(tool_context.agent, 'parallel_tool_calls', False)
            if parallel_tool_calls:
                res = await asyncio.to_thread(self.func, **args_to_call) or {}
            else:
                res = self.func(**args_to_call) or {}

        if isinstance(res, BaseModel):
            return res.model_dump_json()
        return res
