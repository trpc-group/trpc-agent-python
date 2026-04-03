# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for ClientProxyTool."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any
from unittest.mock import Mock

import pytest

from trpc_agent_sdk import types
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.server.ag_ui._core._client_proxy_tool import ClientProxyTool
from trpc_agent_sdk.tools import LongRunningFunctionTool


def _make_ag_ui_tool(
    name: str = "test_tool",
    description: str = "A test tool",
    parameters: Any = None,
) -> Mock:
    """Create a mock AG-UI tool with the given attributes."""
    tool = Mock()
    tool.name = name
    tool.description = description
    tool.parameters = parameters if parameters is not None else {
        "type": "object",
        "properties": {
            "arg1": {"type": "string"},
            "arg2": {"type": "integer"},
        },
    }
    return tool


# ---------------------------------------------------------------------------
# __init__ tests
# ---------------------------------------------------------------------------


class TestClientProxyToolInit:
    def test_stores_ag_ui_tool_and_event_queue(self):
        ag_tool = _make_ag_ui_tool()
        queue = asyncio.Queue()
        proxy = ClientProxyTool(ag_ui_tool=ag_tool, event_queue=queue)

        assert proxy.ag_ui_tool is ag_tool
        assert proxy.event_queue is queue

    def test_name_matches_ag_ui_tool(self):
        ag_tool = _make_ag_ui_tool(name="my_special_tool")
        proxy = ClientProxyTool(ag_ui_tool=ag_tool, event_queue=asyncio.Queue())
        assert proxy.name == "my_special_tool"

    def test_description_matches_ag_ui_tool(self):
        ag_tool = _make_ag_ui_tool(description="Does something special")
        proxy = ClientProxyTool(ag_ui_tool=ag_tool, event_queue=asyncio.Queue())
        assert proxy.description is not None
        assert "Does something special" in proxy.description

    def test_creates_signature_from_properties(self):
        ag_tool = _make_ag_ui_tool(parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
        })
        proxy = ClientProxyTool(ag_ui_tool=ag_tool, event_queue=asyncio.Queue())

        sig = inspect.signature(proxy.func)
        param_names = list(sig.parameters.keys())
        assert "query" in param_names
        assert "limit" in param_names

    def test_signature_params_are_keyword_only(self):
        ag_tool = _make_ag_ui_tool(parameters={
            "type": "object",
            "properties": {"x": {"type": "string"}},
        })
        proxy = ClientProxyTool(ag_ui_tool=ag_tool, event_queue=asyncio.Queue())

        sig = inspect.signature(proxy.func)
        for param in sig.parameters.values():
            assert param.kind == inspect.Parameter.KEYWORD_ONLY

    def test_signature_params_default_to_none(self):
        ag_tool = _make_ag_ui_tool(parameters={
            "type": "object",
            "properties": {"x": {"type": "string"}},
        })
        proxy = ClientProxyTool(ag_ui_tool=ag_tool, event_queue=asyncio.Queue())

        sig = inspect.signature(proxy.func)
        for param in sig.parameters.values():
            assert param.default is None

    def test_signature_params_annotated_as_any(self):
        ag_tool = _make_ag_ui_tool(parameters={
            "type": "object",
            "properties": {"x": {"type": "string"}},
        })
        proxy = ClientProxyTool(ag_ui_tool=ag_tool, event_queue=asyncio.Queue())

        sig = inspect.signature(proxy.func)
        for param in sig.parameters.values():
            assert param.annotation is Any

    def test_no_properties_key_in_parameters(self):
        ag_tool = _make_ag_ui_tool(parameters={"type": "object"})
        proxy = ClientProxyTool(ag_ui_tool=ag_tool, event_queue=asyncio.Queue())

        sig = inspect.signature(proxy.func)
        keyword_only = [p for p in sig.parameters.values() if p.kind == inspect.Parameter.KEYWORD_ONLY]
        assert len(keyword_only) == 0

    def test_parameters_is_none(self):
        tool = Mock()
        tool.name = "null_params_tool"
        tool.description = "Tool with None parameters"
        tool.parameters = None
        proxy = ClientProxyTool(ag_ui_tool=tool, event_queue=asyncio.Queue())

        sig = inspect.signature(proxy.func)
        keyword_only = [p for p in sig.parameters.values() if p.kind == inspect.Parameter.KEYWORD_ONLY]
        assert len(keyword_only) == 0

    def test_parameters_is_non_dict(self):
        tool = Mock()
        tool.name = "str_params_tool"
        tool.description = "Tool with string parameters"
        tool.parameters = "not_a_dict"
        proxy = ClientProxyTool(ag_ui_tool=tool, event_queue=asyncio.Queue())

        sig = inspect.signature(proxy.func)
        keyword_only = [p for p in sig.parameters.values() if p.kind == inspect.Parameter.KEYWORD_ONLY]
        assert len(keyword_only) == 0

    def test_empty_properties_dict(self):
        ag_tool = _make_ag_ui_tool(parameters={
            "type": "object",
            "properties": {},
        })
        proxy = ClientProxyTool(ag_ui_tool=ag_tool, event_queue=asyncio.Queue())

        sig = inspect.signature(proxy.func)
        keyword_only = [p for p in sig.parameters.values() if p.kind == inspect.Parameter.KEYWORD_ONLY]
        assert len(keyword_only) == 0

    def test_is_long_running(self):
        ag_tool = _make_ag_ui_tool()
        proxy = ClientProxyTool(ag_ui_tool=ag_tool, event_queue=asyncio.Queue())
        assert proxy.is_long_running is True

    def test_inherits_from_long_running_function_tool(self):
        ag_tool = _make_ag_ui_tool()
        proxy = ClientProxyTool(ag_ui_tool=ag_tool, event_queue=asyncio.Queue())
        assert isinstance(proxy, LongRunningFunctionTool)


# ---------------------------------------------------------------------------
# _get_declaration tests
# ---------------------------------------------------------------------------


class TestClientProxyToolGetDeclaration:
    def test_returns_function_declaration(self):
        ag_tool = _make_ag_ui_tool(
            name="search",
            description="Search for items",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                },
            },
        )
        proxy = ClientProxyTool(ag_ui_tool=ag_tool, event_queue=asyncio.Queue())
        decl = proxy._get_declaration()

        assert decl is not None
        assert isinstance(decl, types.FunctionDeclaration)

    def test_declaration_name_matches(self):
        ag_tool = _make_ag_ui_tool(name="find_user")
        proxy = ClientProxyTool(ag_ui_tool=ag_tool, event_queue=asyncio.Queue())
        decl = proxy._get_declaration()

        assert decl.name == "find_user"

    def test_declaration_description_present(self):
        ag_tool = _make_ag_ui_tool(description="Finds users by criteria")
        proxy = ClientProxyTool(ag_ui_tool=ag_tool, event_queue=asyncio.Queue())
        decl = proxy._get_declaration()

        assert decl.description is not None
        assert "Finds users by criteria" in decl.description

    def test_declaration_parameters_schema(self):
        params = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
        }
        ag_tool = _make_ag_ui_tool(parameters=params)
        proxy = ClientProxyTool(ag_ui_tool=ag_tool, event_queue=asyncio.Queue())
        decl = proxy._get_declaration()

        assert decl.parameters is not None
        assert isinstance(decl.parameters, types.Schema)

    def test_non_dict_parameters_uses_empty_schema(self):
        ag_tool = _make_ag_ui_tool(parameters="invalid")
        proxy = ClientProxyTool(ag_ui_tool=ag_tool, event_queue=asyncio.Queue())
        decl = proxy._get_declaration()

        assert decl is not None
        assert isinstance(decl, types.FunctionDeclaration)
        assert decl.parameters is not None

    def test_none_parameters_uses_empty_schema(self):
        ag_tool = _make_ag_ui_tool(parameters=None)
        proxy = ClientProxyTool(ag_ui_tool=ag_tool, event_queue=asyncio.Queue())
        decl = proxy._get_declaration()

        assert decl is not None
        assert isinstance(decl, types.FunctionDeclaration)

    def test_list_parameters_uses_empty_schema(self):
        ag_tool = _make_ag_ui_tool(parameters=[1, 2, 3])
        proxy = ClientProxyTool(ag_ui_tool=ag_tool, event_queue=asyncio.Queue())
        decl = proxy._get_declaration()

        assert decl is not None
        assert isinstance(decl, types.FunctionDeclaration)


# ---------------------------------------------------------------------------
# _execute_proxy_tool tests
# ---------------------------------------------------------------------------


class TestClientProxyToolExecuteProxyTool:
    async def test_returns_none(self):
        ag_tool = _make_ag_ui_tool()
        proxy = ClientProxyTool(ag_ui_tool=ag_tool, event_queue=asyncio.Queue())
        ctx = Mock(spec=InvocationContext)

        result = await proxy._execute_proxy_tool(args={"arg1": "val"}, tool_context=ctx)
        assert result is None

    async def test_returns_none_with_empty_args(self):
        ag_tool = _make_ag_ui_tool()
        proxy = ClientProxyTool(ag_ui_tool=ag_tool, event_queue=asyncio.Queue())
        ctx = Mock(spec=InvocationContext)

        result = await proxy._execute_proxy_tool(args={}, tool_context=ctx)
        assert result is None

    async def test_returns_none_with_none_context(self):
        ag_tool = _make_ag_ui_tool()
        proxy = ClientProxyTool(ag_ui_tool=ag_tool, event_queue=asyncio.Queue())

        result = await proxy._execute_proxy_tool(args={"key": "value"}, tool_context=None)
        assert result is None


# ---------------------------------------------------------------------------
# _run_async_impl stores args and context
# ---------------------------------------------------------------------------


class TestClientProxyToolRunAsyncImpl:
    async def test_stores_current_args(self):
        ag_tool = _make_ag_ui_tool()
        proxy = ClientProxyTool(ag_ui_tool=ag_tool, event_queue=asyncio.Queue())
        ctx = Mock(spec=InvocationContext)
        test_args = {"arg1": "hello", "arg2": 42}

        # _run_async_impl calls super() which invokes the proxy function,
        # which calls _execute_proxy_tool. We verify args are stored.
        try:
            await proxy._run_async_impl(args=test_args, tool_context=ctx)
        except Exception:
            pass

        assert proxy._current_args == test_args

    async def test_stores_current_tool_context(self):
        ag_tool = _make_ag_ui_tool()
        proxy = ClientProxyTool(ag_ui_tool=ag_tool, event_queue=asyncio.Queue())
        ctx = Mock(spec=InvocationContext)

        try:
            await proxy._run_async_impl(args={"arg1": "x"}, tool_context=ctx)
        except Exception:
            pass

        assert proxy._current_tool_context is ctx


# ---------------------------------------------------------------------------
# __repr__ tests
# ---------------------------------------------------------------------------


class TestClientProxyToolRepr:
    def test_repr_format(self):
        ag_tool = _make_ag_ui_tool(name="my_tool")
        proxy = ClientProxyTool(ag_ui_tool=ag_tool, event_queue=asyncio.Queue())

        result = repr(proxy)
        assert result == "ClientProxyTool(name='my_tool', ag_ui_tool='my_tool')"

    def test_repr_with_different_names(self):
        ag_tool = _make_ag_ui_tool(name="calculator")
        proxy = ClientProxyTool(ag_ui_tool=ag_tool, event_queue=asyncio.Queue())

        result = repr(proxy)
        assert "calculator" in result
        assert "ClientProxyTool" in result

    def test_repr_contains_name_and_ag_ui_tool(self):
        ag_tool = _make_ag_ui_tool(name="search_web")
        proxy = ClientProxyTool(ag_ui_tool=ag_tool, event_queue=asyncio.Queue())

        result = repr(proxy)
        assert "name='search_web'" in result
        assert "ag_ui_tool='search_web'" in result


# ---------------------------------------------------------------------------
# Multiple parameters ordering
# ---------------------------------------------------------------------------


class TestClientProxyToolParameterOrdering:
    def test_preserves_parameter_order(self):
        ag_tool = _make_ag_ui_tool(parameters={
            "type": "object",
            "properties": {
                "alpha": {"type": "string"},
                "beta": {"type": "integer"},
                "gamma": {"type": "boolean"},
            },
        })
        proxy = ClientProxyTool(ag_ui_tool=ag_tool, event_queue=asyncio.Queue())

        sig = inspect.signature(proxy.func)
        param_names = list(sig.parameters.keys())
        assert param_names == ["alpha", "beta", "gamma"]

    def test_many_parameters(self):
        props = {f"param_{i}": {"type": "string"} for i in range(10)}
        ag_tool = _make_ag_ui_tool(parameters={
            "type": "object",
            "properties": props,
        })
        proxy = ClientProxyTool(ag_ui_tool=ag_tool, event_queue=asyncio.Queue())

        sig = inspect.signature(proxy.func)
        assert len(sig.parameters) == 10
