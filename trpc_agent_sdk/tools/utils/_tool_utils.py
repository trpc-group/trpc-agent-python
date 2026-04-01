# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Tool utilities for TRPC Agent framework."""

import functools
import inspect
from typing import Any
from typing import Callable

import anyio
import pydantic

from trpc_agent_sdk.log import logger
from trpc_agent_sdk.types import MemoryEntry


def retry_on_closed_resource(func):
    """Decorator to automatically retry action when MCP session is closed.

    When MCP session was closed, the decorator will automatically retry the
    action once. The create_session method will handle creating a new session
    if the old one was disconnected.

    Args:
        func: The function to decorate.

    Returns:
        The decorated function.
    """

    @functools.wraps(func)  # Preserves original function metadata
    async def wrapper(self, *args, **kwargs):
        try:
            return await func(self, *args, **kwargs)
        except anyio.ClosedResourceError:
            # Simply retry the function - create_session will handle
            # detecting and replacing disconnected sessions
            logger.info('Retrying %s due to closed resource', func.__name__)
            return await func(self, *args, **kwargs)

    return wrapper


def extract_text(memory: MemoryEntry, splitter: str = ' ') -> str:
    """Extracts the text from the memory entry."""
    if not memory.content.parts:
        return ''
    return splitter.join([part.text for part in memory.content.parts if part.text])


def convert_pydantic_args(args: dict[str, Any], signature: inspect.Signature) -> dict[str, Any]:
    """Convert dictionary arguments to Pydantic model instances when needed.

    Args:
        args: Dictionary of arguments to convert
        signature: Function signature for parameter type information

    Returns:
        Dictionary with converted Pydantic model instances

    Notes:
        - Only converts parameters that are Pydantic BaseModel subclasses
        - Preserves non-Pydantic arguments as-is
        - Handles validation errors gracefully with detailed logging
    """
    converted_args = {}

    for param_name, param_value in args.items():
        if param_name not in signature.parameters:
            # Keep unknown parameters as-is
            converted_args[param_name] = param_value
            continue

        param = signature.parameters[param_name]
        param_annotation = param.annotation

        # Skip if no annotation or annotation is not a class
        if param_annotation == inspect.Parameter.empty or not inspect.isclass(param_annotation):
            converted_args[param_name] = param_value
            continue

        # Check if the parameter is a Pydantic BaseModel subclass
        try:
            if issubclass(param_annotation, pydantic.BaseModel):
                # Convert dict to Pydantic model instance
                if isinstance(param_value, dict):
                    try:
                        converted_args[param_name] = param_annotation(**param_value)
                        logger.debug("Successfully converted %s: %s -> %s instance", param_name, param_value,
                                     param_annotation.__name__)
                    except pydantic.ValidationError as ex:
                        logger.error("Pydantic validation failed for %s: %s", param_name, ex)
                        logger.error("   Input value: %s", param_value)
                        logger.error("   Expected model: %s", param_annotation.__name__)
                        # Try to provide helpful error information
                        error_details = []
                        for error in ex.errors():
                            field_name = error.get('loc', ['unknown'])[0]
                            error_type = error.get('type', 'unknown')
                            error_msg = error.get('msg', 'validation failed')
                            error_details.append(f"Field '{field_name}': {error_msg} (type: {error_type})")

                        logger.error("   Validation errors: %s", '; '.join(error_details))
                        # Keep original value and let function handle the error
                        converted_args[param_name] = param_value
                elif isinstance(param_value, param_annotation):
                    # Already the correct type
                    converted_args[param_name] = param_value
                    logger.debug("Parameter %s is already a %s instance", param_name, param_annotation.__name__)
                else:
                    # Try to create from the value directly
                    try:
                        converted_args[param_name] = param_annotation(param_value)
                        logger.debug("Successfully converted %s: %s -> %s instance", param_name, param_value,
                                     param_annotation.__name__)
                    except (pydantic.ValidationError, TypeError) as ex:
                        logger.error("Failed to convert %s from %s: %s", param_name, type(param_value).__name__, ex)
                        # If conversion fails, keep original value
                        converted_args[param_name] = param_value
            else:
                # Not a Pydantic model, keep as-is
                converted_args[param_name] = param_value
        except TypeError:
            # issubclass failed, not a class type
            converted_args[param_name] = param_value

    return converted_args


def get_mandatory_args(func: Callable) -> list[str]:
    """Identify all mandatory arguments from the function signature.

    Returns:
        list[str]: Names of parameters that are required

    Rules:
        - Parameters without default values are mandatory
        - Excludes *args and **kwargs parameters
        - Considers parameter kind (POSITIONAL_ONLY, KEYWORD_ONLY etc.)
    """
    signature = inspect.signature(func)
    mandatory_params = []

    for name, param in signature.parameters.items():
        # A parameter is mandatory if:
        # 1. It has no default value (param.default is inspect.Parameter.empty)
        # 2. It's not a variable positional (*args) or variable keyword (**kwargs) parameter
        #
        # For more refer to: https://docs.python.org/3/library/inspect.html#inspect.Parameter.kind
        if param.default == inspect.Parameter.empty and param.kind not in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
        ):
            mandatory_params.append(name)

    return mandatory_params
