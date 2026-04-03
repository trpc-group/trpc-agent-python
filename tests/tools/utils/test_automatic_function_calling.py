# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from __future__ import annotations

import inspect
from typing import Optional

import pytest
from pydantic import BaseModel

from trpc_agent_sdk.tools._constants import DEFAULT_API_VARIANT
from trpc_agent_sdk.tools.utils._automatic_function_calling import (
    build_function_declaration,
    from_function_with_options,
)


# --- Test helpers ---

def simple_func(name: str, age: int) -> str:
    """A simple function."""
    return f"{name} is {age}"


def no_param_func() -> str:
    """No params."""
    return "hello"


def func_with_defaults(name: str, count: int = 5) -> str:
    """Func with defaults."""
    return f"{name}-{count}"


def func_with_tool_context(query: str, tool_context) -> str:
    """Has tool_context."""
    return query


def func_no_return(x: str):
    """No return annotation."""
    pass


class InputModel(BaseModel):
    query: str
    limit: int = 10


# --- Tests for from_function_with_options ---

class TestFromFunctionWithOptions:

    def test_basic_function(self):
        decl = from_function_with_options(simple_func)
        assert decl.name == "simple_func"
        assert decl.description == "A simple function."
        assert decl.parameters is not None
        assert "name" in decl.parameters.properties
        assert "age" in decl.parameters.properties

    def test_no_params(self):
        decl = from_function_with_options(no_param_func)
        assert decl.name == "no_param_func"
        assert decl.parameters is None

    def test_unsupported_variant_raises(self):
        with pytest.raises(ValueError, match="Unsupported variant"):
            from_function_with_options(simple_func, variant="bad_variant")

    def test_supported_variants(self):
        decl = from_function_with_options(
            simple_func,
            variant=DEFAULT_API_VARIANT,
            supported_variants=[DEFAULT_API_VARIANT],
        )
        assert decl is not None

    def test_with_required_variant(self):
        decl = from_function_with_options(
            simple_func,
            variant=DEFAULT_API_VARIANT,
            required=DEFAULT_API_VARIANT,
        )
        assert decl.parameters is not None
        assert decl.parameters.required is not None

    def test_return_annotation_with_required(self):
        def typed_func(x: str) -> str:
            """Returns string."""
            return x

        decl = from_function_with_options(
            typed_func,
            variant=DEFAULT_API_VARIANT,
            required=DEFAULT_API_VARIANT,
        )
        assert decl.response is not None

    def test_no_return_annotation_with_required(self):
        decl = from_function_with_options(
            func_no_return,
            variant=DEFAULT_API_VARIANT,
            required=DEFAULT_API_VARIANT,
        )
        assert decl.response is None

    def test_non_required_variant_skips_response(self):
        def typed_func(x: str) -> str:
            """Returns string."""
            return x

        decl = from_function_with_options(typed_func, variant=DEFAULT_API_VARIANT)
        assert decl.response is None


# --- Tests for build_function_declaration ---

class TestBuildFunctionDeclaration:

    def test_basic_function(self):
        decl = build_function_declaration(simple_func)
        assert decl.name == "simple_func"
        assert "name" in decl.parameters.properties
        assert "age" in decl.parameters.properties

    def test_ignore_params(self):
        decl = build_function_declaration(
            func_with_tool_context,
            ignore_params=["tool_context"],
        )
        assert decl.parameters.properties is not None
        assert "tool_context" not in decl.parameters.properties
        assert "query" in decl.parameters.properties

    def test_ignore_empty_list(self):
        decl = build_function_declaration(simple_func, ignore_params=[])
        assert "name" in decl.parameters.properties

    def test_ignore_none(self):
        decl = build_function_declaration(simple_func, ignore_params=None)
        assert "name" in decl.parameters.properties

    def test_with_base_model(self):
        decl = build_function_declaration(InputModel)
        assert decl is not None

    def test_ignore_param_from_base_model(self):
        decl = build_function_declaration(InputModel, ignore_params=["limit"])
        assert decl.parameters is not None
        assert "limit" not in decl.parameters.properties

    def test_no_params_to_ignore(self):
        decl = build_function_declaration(no_param_func)
        assert decl.name == "no_param_func"

    def test_with_variant(self):
        decl = build_function_declaration(simple_func, variant=DEFAULT_API_VARIANT)
        assert decl is not None
