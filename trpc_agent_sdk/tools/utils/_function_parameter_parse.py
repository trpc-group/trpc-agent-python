# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Function Parameter Schema Parser Module.

This module provides utilities for parsing Python function parameters into
structured schema definitions, supporting automatic function calling in TRPC Agent.

Key Features:
    - Converts Python type annotations to standardized schema definitions
    - Handles built-in types, generics, unions and Pydantic models
    - Supports variant-specific schema validation
    - Validates default value compatibility with type annotations
"""

import inspect
import types as typing_types
from typing import Any
from typing import Callable
from typing import Literal
from typing import Union
from typing import _GenericAlias
from typing import get_args
from typing import get_origin

import pydantic
from google.genai.types import Schema
from google.genai.types import Type
from pydantic.fields import FieldInfo

from trpc_agent_sdk.utils import singleton


def _resolve_annotation(annotation: Any, func_globals: dict = None) -> Any:
    """Resolve string annotations to actual type objects.

    Args:
        annotation: The annotation to resolve (could be string or type object)
        func_globals: Function globals for resolving string annotations

    Returns:
        The resolved type object
    """
    if isinstance(annotation, str):
        # Handle string annotations by trying to resolve them
        if func_globals is None:
            func_globals = {}

        # Add built-in types to globals for resolution
        builtins = {
            'str': str,
            'int': int,
            'float': float,
            'bool': bool,
            'list': list,
            'dict': dict,
            'Any': Any,
        }
        func_globals.update(builtins)

        try:
            # Try to evaluate the string annotation
            return eval(annotation, func_globals)
        except (NameError, SyntaxError, TypeError):
            # If resolution fails, check for special cases
            if annotation == 'InvocationContext':
                # For InvocationContext, treat it as 'Any' for schema generation
                # since it's typically injected by the framework
                return Any
            # For other unresolvable types, return string as-is
            # This will be handled later in the parsing logic
            return annotation

    return annotation


_py_builtin_type_to_schema_type: dict = {
    str: Type.STRING,
    int: Type.INTEGER,
    float: Type.NUMBER,
    bool: Type.BOOLEAN,
    list: Type.ARRAY,
    dict: Type.OBJECT,
    Any: Type.OBJECT,  # Treat Any as object for schema generation
}


def _is_builtin_primitive_or_compound(annotation: inspect.Parameter.annotation, ) -> bool:
    """Check if the annotation is a built-in primitive or compound type."""
    return annotation in _py_builtin_type_to_schema_type


@singleton
class _SchemaChecker:
    """Schema checker."""

    def __init__(self):
        self._checkers: dict[str, list[Callable[[Schema], bool]]] = {}

    def check(self, variant: str, schema: Schema) -> bool:
        """Check if the schema is supported by the backend."""
        if variant not in self._checkers:
            return True
        for call in self._checkers[variant]:
            if not call(schema):
                return False
        return True

    def register(self, variant: str, checker: Callable[[Schema], bool]) -> None:
        """Register a checker."""
        if variant not in self._checkers:
            self._checkers[variant] = []
        self._checkers[variant].append(checker)


def _raise_if_schema_unsupported(variant: str, schema: Schema, supported: list[str] = None):
    if supported is None or variant in supported:
        return
    _SchemaChecker().check(variant, schema)


def _is_default_value_compatible(default_value: Any, annotation: inspect.Parameter.annotation) -> bool:
    # None type is expected to be handled external to this function
    if _is_builtin_primitive_or_compound(annotation):
        return isinstance(default_value, annotation)

    if (isinstance(annotation, _GenericAlias) or isinstance(annotation, typing_types.GenericAlias)
            or isinstance(annotation, typing_types.UnionType)):
        origin = get_origin(annotation)
        if origin in (Union, typing_types.UnionType):
            return any(_is_default_value_compatible(default_value, arg) for arg in get_args(annotation))

        if origin is dict:
            return isinstance(default_value, dict)

        if origin is list:
            if not isinstance(default_value, list):
                return False
            # most tricky case, element in list is union type
            # need to apply any logic within all
            # see test case test_generic_alias_complex_array_with_default_value
            # a: typing.List[int | str | float | bool]
            # default_value: [1, 'a', 1.1, True]
            return all(
                any(_is_default_value_compatible(item, arg) for arg in get_args(annotation)) for item in default_value)

        if origin is Literal:
            return default_value in get_args(annotation)

    # return False for any other unrecognized annotation
    # let caller handle the raise
    return False


def parse_schema_from_parameter(variant: str,
                                param: inspect.Parameter,
                                func_name: str,
                                func_globals: dict = None) -> Schema:
    """parse schema from parameter.

  from the simplest case to the most complex case.
  """
    schema = Schema()

    # Resolve string annotations to actual type objects
    resolved_annotation = _resolve_annotation(param.annotation, func_globals)

    # Create a new parameter with resolved annotation
    resolved_param = inspect.Parameter(param.name, param.kind, default=param.default, annotation=resolved_annotation)

    default_value_error_msg = (f'Default value {param.default} of parameter {param} of function'
                               f' {func_name} is not compatible with the parameter annotation'
                               f' {param.annotation}.')
    if _is_builtin_primitive_or_compound(resolved_param.annotation):
        if resolved_param.default is not inspect.Parameter.empty:
            if not _is_default_value_compatible(resolved_param.default, resolved_param.annotation):
                raise ValueError(default_value_error_msg)
            schema.default = resolved_param.default
        schema.type = _py_builtin_type_to_schema_type[resolved_param.annotation]
        _raise_if_schema_unsupported(variant, schema)
        return schema
    if (get_origin(resolved_param.annotation) is Union
            # only parse simple UnionType, example int | str | float | bool
            # complex types.UnionType will be invoked in raise branch
            and all((_is_builtin_primitive_or_compound(arg) or arg is type(None))
                    for arg in get_args(resolved_param.annotation))):
        schema.type = Type.OBJECT
        schema.any_of = []
        unique_types = set()
        for arg in get_args(resolved_param.annotation):
            if arg.__name__ == 'NoneType':  # Optional type
                schema.nullable = True
                continue
            schema_in_any_of = parse_schema_from_parameter(
                variant,
                inspect.Parameter('item', inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=arg),
                func_name,
                func_globals,
            )
            if (schema_in_any_of.model_dump_json(exclude_none=True) not in unique_types):
                schema.any_of.append(schema_in_any_of)
                unique_types.add(schema_in_any_of.model_dump_json(exclude_none=True))
        if len(schema.any_of) == 1:  # param: list | None -> Array
            schema.type = schema.any_of[0].type
            schema.any_of = None
        if (resolved_param.default is not inspect.Parameter.empty and resolved_param.default is not None):
            if not _is_default_value_compatible(resolved_param.default, resolved_param.annotation):
                raise ValueError(default_value_error_msg)
            schema.default = resolved_param.default
        _raise_if_schema_unsupported(variant, schema)
        return schema
    if isinstance(resolved_param.annotation, _GenericAlias) or isinstance(resolved_param.annotation,
                                                                          typing_types.GenericAlias):
        origin = get_origin(resolved_param.annotation)
        args = get_args(resolved_param.annotation)
        if origin is dict:
            schema.type = Type.OBJECT
            if resolved_param.default is not inspect.Parameter.empty:
                if not _is_default_value_compatible(resolved_param.default, resolved_param.annotation):
                    raise ValueError(default_value_error_msg)
                schema.default = resolved_param.default
            _raise_if_schema_unsupported(variant, schema)
            return schema
        if origin is Literal:
            if not all(isinstance(arg, str) for arg in args):
                raise ValueError(f'Literal type {resolved_param.annotation} must be a list of strings.')
            schema.type = Type.STRING
            schema.enum = list(args)
            if resolved_param.default is not inspect.Parameter.empty:
                if not _is_default_value_compatible(resolved_param.default, resolved_param.annotation):
                    raise ValueError(default_value_error_msg)
                schema.default = resolved_param.default
            _raise_if_schema_unsupported(variant, schema)
            return schema
        if origin is list:
            schema.type = Type.ARRAY
            schema.items = parse_schema_from_parameter(
                variant,
                inspect.Parameter(
                    'item',
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    annotation=args[0],
                ),
                func_name,
                func_globals,
            )
            if resolved_param.default is not inspect.Parameter.empty:
                if not _is_default_value_compatible(resolved_param.default, resolved_param.annotation):
                    raise ValueError(default_value_error_msg)
                schema.default = resolved_param.default
            _raise_if_schema_unsupported(variant, schema)
            return schema
        if origin is Union:
            schema.any_of = []
            schema.type = Type.OBJECT
            unique_types = set()
            for arg in args:
                if arg.__name__ == 'NoneType':  # Optional type
                    schema.nullable = True
                    continue
                schema_in_any_of = parse_schema_from_parameter(
                    variant,
                    inspect.Parameter(
                        'item',
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                        annotation=arg,
                    ),
                    func_name,
                    func_globals,
                )
                if (len(resolved_param.annotation.__args__) == 2
                        and type(None) in resolved_param.annotation.__args__):  # Optional type
                    for optional_arg in resolved_param.annotation.__args__:
                        if (hasattr(optional_arg, '__origin__') and optional_arg.__origin__ is list):
                            # Optional type with list, for example Optional[list[str]]
                            schema.items = schema_in_any_of.items
                if (schema_in_any_of.model_dump_json(exclude_none=True) not in unique_types):
                    schema.any_of.append(schema_in_any_of)
                    unique_types.add(schema_in_any_of.model_dump_json(exclude_none=True))
            if len(schema.any_of) == 1:  # param: Union[List, None] -> Array
                schema.type = schema.any_of[0].type
                schema.any_of = None
            if (resolved_param.default is not None and resolved_param.default is not inspect.Parameter.empty):
                if not _is_default_value_compatible(resolved_param.default, resolved_param.annotation):
                    raise ValueError(default_value_error_msg)
                schema.default = resolved_param.default
            _raise_if_schema_unsupported(variant, schema)
            return schema
            # all other generic alias will be invoked in raise branch
    if (inspect.isclass(resolved_param.annotation)
            # for user defined class, we only support pydantic model
            and issubclass(resolved_param.annotation, pydantic.BaseModel)):
        if (resolved_param.default is not inspect.Parameter.empty and resolved_param.default is not None):
            schema.default = resolved_param.default
        schema.type = Type.OBJECT
        schema.properties = {}
        for field_name, field_info in resolved_param.annotation.model_fields.items():
            schema.properties[field_name] = parse_schema_from_parameter(
                variant,
                inspect.Parameter(
                    field_name,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    annotation=field_info.annotation,
                ),
                func_name,
                func_globals,
            )
        _raise_if_schema_unsupported(variant, schema)
        return schema
    raise ValueError(f'Failed to parse the parameter {resolved_param} of function {func_name} for'
                     ' automatic function calling. Automatic function calling works best with'
                     ' simpler function signature schema, consider manually parsing your'
                     f' function declaration for function {func_name}.')


def get_required_fields(schema: Schema) -> list[str]:
    if not schema.properties:
        return
    return [
        field_name for field_name, field_schema in schema.properties.items()
        if not field_schema.nullable and field_schema.default is None
    ]


def register_checker(variant: str) -> Callable:
    """Register a checker."""

    def decorator(checker: Callable[[Schema], bool]):
        _SchemaChecker().register(variant, checker)
        return checker

    return decorator


def _get_schema_fields() -> set[str]:
    valid_fields = set(Schema.model_fields.keys())
    for val in Schema.model_fields.values():
        if isinstance(val, FieldInfo):
            valid_fields.add(val.alias)
    return valid_fields


SCHEMA_FIELDS = _get_schema_fields()
