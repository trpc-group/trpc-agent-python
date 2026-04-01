# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Automatic Function Calling Utilities Module.

This module provides utilities for automatically generating function declarations
and handling function call configurations in the TRPC Agent system.

Key Features:
    - Converts Python functions/callables to FunctionDeclarations
    - Handles parameter schema generation
    - Supports variant-specific function configurations
"""

import inspect
from types import FunctionType
from typing import Callable
from typing import Optional
from typing import Union

from google.genai.types import FunctionDeclaration
from google.genai.types import Schema
from pydantic import BaseModel
from pydantic import create_model

from . import _function_parameter_parse
from .._constants import DEFAULT_API_VARIANT


def from_function_with_options(
    func: Callable,
    variant: str = DEFAULT_API_VARIANT,
    supported_variants: Optional[list[str]] = None,
    required: str = '',
) -> FunctionDeclaration:
    """Generates a FunctionDeclaration from a callable with variant support.

    Args:
        func: The callable to convert to FunctionDeclaration
        variant: The variant type for parameter parsing
        supported_variants: List of supported variant types
        required: Variant that requires parameter validation

    Returns:
        A FunctionDeclaration representing the callable

    Raises:
        ValueError: If variant is not in supported_variants
    """
    supported_variants: list[str] = supported_variants or [DEFAULT_API_VARIANT]
    if variant not in supported_variants:
        raise ValueError(f'Unsupported variant: {variant}. Supported variants are:'
                         f' {", ".join(supported_variants)}')

    # Parse function parameters into schema properties
    parameters_properties = {}
    func_globals = getattr(func, '__globals__', {})
    for name, param in inspect.signature(func).parameters.items():
        if param.kind in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
                inspect.Parameter.POSITIONAL_ONLY,
        ):
            schema = _function_parameter_parse.parse_schema_from_parameter(variant, param, func.__name__, func_globals)
            parameters_properties[name] = schema

    # Create base function declaration
    declaration: FunctionDeclaration = FunctionDeclaration(
        name=func.__name__,
        description=func.__doc__,
    )

    # Add parameters schema if any parameters exist
    if parameters_properties:
        declaration.parameters = Schema(
            type='OBJECT',
            properties=parameters_properties,
        )
        if variant == required:
            declaration.parameters.required = (_function_parameter_parse.get_required_fields(declaration.parameters))

    # Return early if not required variant
    if variant != required:
        return declaration

    # Handle return type annotation if present
    return_annotation = inspect.signature(func).return_annotation
    if return_annotation is inspect._empty:
        return declaration

    declaration.response = (_function_parameter_parse.parse_schema_from_parameter(
        variant,
        inspect.Parameter(
            'return_value',
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=return_annotation,
        ),
        func.__name__,
        func_globals,
    ))
    return declaration


def build_function_declaration(
    func: Union[Callable, BaseModel],
    ignore_params: Optional[list[str]] = None,
    variant: str = DEFAULT_API_VARIANT,
) -> FunctionDeclaration:
    """Builds a FunctionDeclaration while optionally ignoring specified parameters.

    Args:
        func: The callable or BaseModel to convert
        ignore_params: List of parameter names to exclude
        variant: The variant type for parameter parsing

    Returns:
        A FunctionDeclaration with specified parameters ignored
    """
    signature = inspect.signature(func)
    should_update_signature = False
    new_func = None

    # Initialize ignore_params if not provided
    if not ignore_params:
        ignore_params = []

    # Check if any parameters need to be ignored
    for name in signature.parameters.keys():
        if name in ignore_params:
            should_update_signature = True
            break

    if should_update_signature:
        # Create new parameters list excluding ignored ones
        new_params = [param for name, param in signature.parameters.items() if name not in ignore_params]

        if isinstance(func, type):
            # Handle BaseModel case
            fields = {
                name: (param.annotation, param.default)
                for name, param in signature.parameters.items() if name not in ignore_params
            }
            new_func = create_model(func.__name__, **fields)
        else:
            # Handle callable case
            new_sig = signature.replace(parameters=new_params)
            new_func = FunctionType(
                func.__code__,
                func.__globals__,
                func.__name__,
                func.__defaults__,
                func.__closure__,
            )
            new_func.__signature__ = new_sig

    if should_update_signature:
        func = new_func

    return from_function_with_options(func, variant)
