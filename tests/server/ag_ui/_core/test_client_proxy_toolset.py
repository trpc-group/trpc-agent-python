# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for ClientProxyToolset."""

from __future__ import annotations

import asyncio
from unittest.mock import Mock, patch

import pytest

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.server.ag_ui._core._client_proxy_toolset import ClientProxyToolset
from trpc_agent_sdk.server.ag_ui._core._client_proxy_tool import ClientProxyTool
from trpc_agent_sdk.tools import BaseTool, BaseToolSet


def _make_ag_ui_tool(
    name: str = "test_tool",
    description: str = "A test tool",
    parameters: dict | None = None,
) -> Mock:
    """Create a mock AG-UI tool with the given attributes."""
    tool = Mock()
    tool.name = name
    tool.description = description
    tool.parameters = parameters if parameters is not None else {
        "type": "object",
        "properties": {
            "arg1": {"type": "string"},
        },
    }
    return tool


# ---------------------------------------------------------------------------
# __init__ tests
# ---------------------------------------------------------------------------


class TestClientProxyToolsetInit:
    def test_stores_ag_ui_tools(self):
        tools = [_make_ag_ui_tool(name="t1"), _make_ag_ui_tool(name="t2")]
        queue = asyncio.Queue()
        toolset = ClientProxyToolset(ag_ui_tools=tools, event_queue=queue)

        assert toolset.ag_ui_tools is tools
        assert len(toolset.ag_ui_tools) == 2

    def test_stores_event_queue(self):
        queue = asyncio.Queue()
        toolset = ClientProxyToolset(ag_ui_tools=[], event_queue=queue)

        assert toolset.event_queue is queue

    def test_empty_tools_list(self):
        toolset = ClientProxyToolset(ag_ui_tools=[], event_queue=asyncio.Queue())
        assert toolset.ag_ui_tools == []

    def test_inherits_from_base_toolset(self):
        toolset = ClientProxyToolset(ag_ui_tools=[], event_queue=asyncio.Queue())
        assert isinstance(toolset, BaseToolSet)


# ---------------------------------------------------------------------------
# get_tools tests
# ---------------------------------------------------------------------------


class TestClientProxyToolsetGetTools:
    async def test_returns_list_of_proxy_tools(self):
        tools = [
            _make_ag_ui_tool(name="tool_a", description="Tool A"),
            _make_ag_ui_tool(name="tool_b", description="Tool B"),
        ]
        queue = asyncio.Queue()
        toolset = ClientProxyToolset(ag_ui_tools=tools, event_queue=queue)

        result = await toolset.get_tools()
        assert len(result) == 2
        assert all(isinstance(t, ClientProxyTool) for t in result)

    async def test_proxy_tools_have_correct_names(self):
        tools = [
            _make_ag_ui_tool(name="search"),
            _make_ag_ui_tool(name="calculate"),
        ]
        toolset = ClientProxyToolset(ag_ui_tools=tools, event_queue=asyncio.Queue())

        result = await toolset.get_tools()
        names = [t.name for t in result]
        assert "search" in names
        assert "calculate" in names

    async def test_returns_base_tool_instances(self):
        tools = [_make_ag_ui_tool()]
        toolset = ClientProxyToolset(ag_ui_tools=tools, event_queue=asyncio.Queue())

        result = await toolset.get_tools()
        assert all(isinstance(t, BaseTool) for t in result)

    async def test_empty_tools_returns_empty_list(self):
        toolset = ClientProxyToolset(ag_ui_tools=[], event_queue=asyncio.Queue())

        result = await toolset.get_tools()
        assert result == []

    async def test_accepts_optional_context(self):
        tools = [_make_ag_ui_tool()]
        toolset = ClientProxyToolset(ag_ui_tools=tools, event_queue=asyncio.Queue())
        ctx = Mock(spec=InvocationContext)

        result = await toolset.get_tools(context=ctx)
        assert len(result) == 1

    async def test_accepts_none_context(self):
        tools = [_make_ag_ui_tool()]
        toolset = ClientProxyToolset(ag_ui_tools=tools, event_queue=asyncio.Queue())

        result = await toolset.get_tools(context=None)
        assert len(result) == 1

    async def test_handles_tool_creation_failure_gracefully(self):
        good_tool = _make_ag_ui_tool(name="good_tool")
        bad_tool = _make_ag_ui_tool(name="bad_tool")

        tools = [good_tool, bad_tool, _make_ag_ui_tool(name="another_good")]
        toolset = ClientProxyToolset(ag_ui_tools=tools, event_queue=asyncio.Queue())

        with patch(
            "trpc_agent_sdk.server.ag_ui._core._client_proxy_toolset.ClientProxyTool",
            side_effect=[
                ClientProxyTool(ag_ui_tool=good_tool, event_queue=asyncio.Queue()),
                RuntimeError("creation failed"),
                ClientProxyTool(ag_ui_tool=_make_ag_ui_tool(name="another_good"), event_queue=asyncio.Queue()),
            ],
        ):
            result = await toolset.get_tools()

        assert len(result) == 2

    async def test_all_tools_fail_returns_empty_list(self):
        tools = [_make_ag_ui_tool(name="bad1"), _make_ag_ui_tool(name="bad2")]
        toolset = ClientProxyToolset(ag_ui_tools=tools, event_queue=asyncio.Queue())

        with patch(
            "trpc_agent_sdk.server.ag_ui._core._client_proxy_toolset.ClientProxyTool",
            side_effect=RuntimeError("fail"),
        ):
            result = await toolset.get_tools()

        assert result == []

    async def test_creates_fresh_instances_each_call(self):
        tools = [_make_ag_ui_tool(name="tool_x")]
        toolset = ClientProxyToolset(ag_ui_tools=tools, event_queue=asyncio.Queue())

        result1 = await toolset.get_tools()
        result2 = await toolset.get_tools()

        assert result1[0] is not result2[0]

    async def test_proxy_tools_share_event_queue(self):
        queue = asyncio.Queue()
        tools = [
            _make_ag_ui_tool(name="t1"),
            _make_ag_ui_tool(name="t2"),
        ]
        toolset = ClientProxyToolset(ag_ui_tools=tools, event_queue=queue)

        result = await toolset.get_tools()
        for t in result:
            assert t.event_queue is queue


# ---------------------------------------------------------------------------
# close tests
# ---------------------------------------------------------------------------


class TestClientProxyToolsetClose:
    async def test_close_completes_without_error(self):
        toolset = ClientProxyToolset(ag_ui_tools=[], event_queue=asyncio.Queue())
        await toolset.close()

    async def test_close_returns_none(self):
        toolset = ClientProxyToolset(ag_ui_tools=[], event_queue=asyncio.Queue())
        result = await toolset.close()
        assert result is None

    async def test_close_with_tools_loaded(self):
        tools = [_make_ag_ui_tool(name="t1")]
        toolset = ClientProxyToolset(ag_ui_tools=tools, event_queue=asyncio.Queue())
        await toolset.get_tools()
        await toolset.close()


# ---------------------------------------------------------------------------
# __repr__ tests
# ---------------------------------------------------------------------------


class TestClientProxyToolsetRepr:
    def test_repr_with_tools(self):
        tools = [_make_ag_ui_tool(name="search"), _make_ag_ui_tool(name="calculate")]
        toolset = ClientProxyToolset(ag_ui_tools=tools, event_queue=asyncio.Queue())

        result = repr(toolset)
        assert result == "ClientProxyToolset(tools=['search', 'calculate'], all_long_running=True)"

    def test_repr_empty_tools(self):
        toolset = ClientProxyToolset(ag_ui_tools=[], event_queue=asyncio.Queue())

        result = repr(toolset)
        assert result == "ClientProxyToolset(tools=[], all_long_running=True)"

    def test_repr_single_tool(self):
        tools = [_make_ag_ui_tool(name="only_tool")]
        toolset = ClientProxyToolset(ag_ui_tools=tools, event_queue=asyncio.Queue())

        result = repr(toolset)
        assert "only_tool" in result
        assert "all_long_running=True" in result

    def test_repr_contains_class_name(self):
        toolset = ClientProxyToolset(ag_ui_tools=[], event_queue=asyncio.Queue())
        assert "ClientProxyToolset" in repr(toolset)
