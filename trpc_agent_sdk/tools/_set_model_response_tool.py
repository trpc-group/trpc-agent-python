# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Tool for setting model response when using output_schema with other tools.

This module provides the SetModelResponseTool class which allows LLM agents to
set their final structured response when output_schema is configured alongside
other tools.
"""

from __future__ import annotations

import inspect
from typing import Any
from typing import Optional
from typing_extensions import override

from pydantic import BaseModel

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.types import FunctionDeclaration

from ._base_tool import BaseTool
from .utils import build_function_declaration


class SetModelResponseTool(BaseTool):
    """Internal tool used for output schema workaround.

    This tool allows the model to set its final response when output_schema
    is configured alongside other tools. The model should use this tool to
    provide its final structured response instead of outputting text directly.
    """

    def __init__(self,
                 output_schema: type[BaseModel],
                 filters_name: Optional[list[str]] = None,
                 filters: Optional[list[BaseFilter]] = None):
        """Initialize the tool with the expected output schema.

        Args:
            output_schema: The pydantic model class defining the expected output
                structure.
            filters_name: List of filter names
            filters: List of filter instances
        """
        self.output_schema = output_schema

        # Create a LOCAL function inside __init__ - each instance gets its own
        # This avoids the race condition where multiple instances would modify
        # a shared global function's __signature__ concurrently
        def set_model_response() -> str:
            """Set your final response using the required output schema.

            Use this tool to provide your final structured answer instead
            of outputting text directly.
            """
            return "Response set successfully."

        # Add the schema fields as parameters to the function dynamically
        schema_fields = output_schema.model_fields
        params = []
        for field_name, field_info in schema_fields.items():
            param = inspect.Parameter(
                field_name,
                inspect.Parameter.KEYWORD_ONLY,
                annotation=field_info.annotation,
            )
            params.append(param)

        # Create new signature with schema parameters
        # Safe to modify - this is a local function, not shared across instances
        new_sig = inspect.Signature(parameters=params)
        setattr(set_model_response, "__signature__", new_sig)

        self.func = set_model_response

        super().__init__(
            name=self.func.__name__,
            description=self.func.__doc__.strip() if self.func.__doc__ else "",
            filters_name=filters_name,
            filters=filters,
        )

    @override
    def _get_declaration(self) -> Optional[FunctionDeclaration]:
        """Gets the OpenAPI specification of this tool."""
        function_decl = FunctionDeclaration.model_validate(
            build_function_declaration(
                func=self.func,
                ignore_params=[],
                variant=self.api_variant,
            ))
        return function_decl

    @override
    async def _run_async_impl(self, *, args: dict[str, Any], tool_context: InvocationContext) -> dict[str, Any]:
        """Process the model's response and return the validated dict.

        Args:
            args: The structured response data matching the output schema.
            tool_context: Tool execution context.

        Returns:
            The validated response as dict.
        """
        # Validate the input matches the expected schema
        validated_response = self.output_schema.model_validate(args)

        # Return the validated dict directly
        return validated_response.model_dump()
