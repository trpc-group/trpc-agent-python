# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""TRPC Agent Tools Utilities Package.

This package provides utility functions for automatic function calling and parameter parsing
in the TRPC Agent system.

Exported Functions:
    - build_function_declaration: Creates function declarations from callables
    - from_function_with_options: Generates function declarations with variant support
    - get_required_fields: Extracts required fields from schemas
    - parse_schema_from_parameter: Converts parameters to schema definitions
    - register_checker: Registers schema validation checkers
"""

from ._automatic_function_calling import build_function_declaration
from ._automatic_function_calling import from_function_with_options
from ._function_parameter_parse import SCHEMA_FIELDS
from ._function_parameter_parse import get_required_fields
from ._function_parameter_parse import parse_schema_from_parameter
from ._function_parameter_parse import register_checker
from ._tool_utils import convert_pydantic_args
from ._tool_utils import extract_text
from ._tool_utils import get_mandatory_args
from ._tool_utils import retry_on_closed_resource

__all__ = [
    "build_function_declaration",
    "from_function_with_options",
    "SCHEMA_FIELDS",
    "get_required_fields",
    "parse_schema_from_parameter",
    "register_checker",
    "convert_pydantic_args",
    "extract_text",
    "get_mandatory_args",
    "retry_on_closed_resource",
]
