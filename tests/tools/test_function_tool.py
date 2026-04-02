# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools._function_tool import FunctionTool


# --- Test functions ---

def sync_func(param1: str, param2: int = 10) -> str:
    """A sync test function."""
    return f"{param1}-{param2}"


async def async_func(param1: str) -> str:
    """An async test function."""
    return f"async-{param1}"


def func_with_context(query: str, tool_context: InvocationContext) -> str:
    """Function requiring tool context."""
    return f"query={query}"


def func_no_doc(x: int):
    pass


class CallableObj:
    """A callable object."""

    def __call__(self, value: str) -> str:
        """Call doc."""
        return value


class CallableObjNoCallDoc:
    """Object doc."""

    def __call__(self, value: str) -> str:
        return value


class OutputModel(BaseModel):
    name: str
    value: int


def func_returns_model(name: str, value: int) -> OutputModel:
    """Returns a pydantic model."""
    return OutputModel(name=name, value=value)


class TestFunctionToolInit:

    def test_init_with_function(self):
        tool = FunctionTool(sync_func)
        assert tool.name == "sync_func"
        assert tool.description == "A sync test function."
        assert tool.func is sync_func

    def test_init_with_async_function(self):
        tool = FunctionTool(async_func)
        assert tool.name == "async_func"
        assert tool.description == "An async test function."

    def test_init_with_callable_object(self):
        obj = CallableObj()
        tool = FunctionTool(obj)
        assert tool.name == "CallableObj"
        assert tool.description == "Call doc."

    def test_init_with_callable_object_no_call_doc(self):
        obj = CallableObjNoCallDoc()
        tool = FunctionTool(obj)
        assert tool.name == "CallableObjNoCallDoc"
        assert tool.description == "Object doc."

    def test_init_with_no_doc(self):
        tool = FunctionTool(func_no_doc)
        assert tool.name == "func_no_doc"
        assert tool.description == ""

    def test_init_with_filters_name_invalid_raises(self):
        with pytest.raises(ValueError, match="not found"):
            FunctionTool(sync_func, filters_name=["nonexistent_filter"])


class TestFunctionToolGetDeclaration:

    def test_get_declaration_basic(self):
        tool = FunctionTool(sync_func)
        decl = tool._get_declaration()
        assert decl is not None
        assert decl.name == "sync_func"

    def test_get_declaration_ignores_tool_context(self):
        tool = FunctionTool(func_with_context)
        decl = tool._get_declaration()
        assert decl is not None
        if decl.parameters and decl.parameters.properties:
            assert "tool_context" not in decl.parameters.properties


class TestFunctionToolRunAsync:

    @pytest.fixture
    def mock_context(self):
        ctx = MagicMock(spec=InvocationContext)
        ctx.agent = MagicMock()
        ctx.agent.parallel_tool_calls = False
        return ctx

    @pytest.mark.asyncio
    async def test_run_sync_function(self, mock_context):
        tool = FunctionTool(sync_func)
        result = await tool._run_async_impl(
            tool_context=mock_context,
            args={"param1": "hello", "param2": 5},
        )
        assert result == "hello-5"

    @pytest.mark.asyncio
    async def test_run_async_function(self, mock_context):
        tool = FunctionTool(async_func)
        result = await tool._run_async_impl(
            tool_context=mock_context,
            args={"param1": "world"},
        )
        assert result == "async-world"

    @pytest.mark.asyncio
    async def test_injects_tool_context(self, mock_context):
        tool = FunctionTool(func_with_context)
        result = await tool._run_async_impl(
            tool_context=mock_context,
            args={"query": "test"},
        )
        assert result == "query=test"

    @pytest.mark.asyncio
    async def test_missing_mandatory_args(self, mock_context):
        tool = FunctionTool(sync_func)
        result = await tool._run_async_impl(
            tool_context=mock_context,
            args={},
        )
        assert isinstance(result, dict)
        assert "error" in result
        assert "param1" in result["error"]

    @pytest.mark.asyncio
    async def test_returns_empty_dict_for_none(self, mock_context):
        def returns_none():
            return None

        tool = FunctionTool(returns_none)
        result = await tool._run_async_impl(
            tool_context=mock_context,
            args={},
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_pydantic_model_return(self, mock_context):
        tool = FunctionTool(func_returns_model)
        result = await tool._run_async_impl(
            tool_context=mock_context,
            args={"name": "test", "value": 42},
        )
        assert isinstance(result, str)
        assert "test" in result
        assert "42" in result

    @pytest.mark.asyncio
    async def test_parallel_tool_calls_uses_thread(self, mock_context):
        mock_context.agent.parallel_tool_calls = True

        def slow_func(x: str) -> str:
            return f"done-{x}"

        tool = FunctionTool(slow_func)
        result = await tool._run_async_impl(
            tool_context=mock_context,
            args={"x": "val"},
        )
        assert result == "done-val"

    @pytest.mark.asyncio
    async def test_async_callable_object(self, mock_context):
        class AsyncCallable:
            async def __call__(self, value: str) -> str:
                """Async callable."""
                return f"async-{value}"

        obj = AsyncCallable()
        tool = FunctionTool(obj)
        result = await tool._run_async_impl(
            tool_context=mock_context,
            args={"value": "test"},
        )
        assert result == "async-test"

    @pytest.mark.asyncio
    async def test_default_param_not_required(self, mock_context):
        tool = FunctionTool(sync_func)
        result = await tool._run_async_impl(
            tool_context=mock_context,
            args={"param1": "hello"},
        )
        assert result == "hello-10"
