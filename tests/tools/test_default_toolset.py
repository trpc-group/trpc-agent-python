# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typing_extensions import override

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools._base_tool import BaseTool
from trpc_agent_sdk.tools._default_toolset import DefaultToolSet
from trpc_agent_sdk.tools._function_tool import FunctionTool


class DummyTool(BaseTool):

    def __init__(self, name="dummy", description="dummy"):
        super().__init__(name=name, description=description)

    @override
    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        return {}


def sample_func(x: str) -> str:
    """Sample function."""
    return x


class TestDefaultToolSetInit:

    def test_init(self):
        ts = DefaultToolSet()
        ts.initialize()
        assert ts._tool_filter == []


class TestDefaultToolSetAddTools:

    def setup_method(self):
        self.toolset = DefaultToolSet()
        self.toolset.initialize()

    def test_add_base_tool(self):
        tool = DummyTool(name="t1")
        self.toolset.add_tools([tool])
        assert len(self.toolset._tool_filter) == 1
        assert self.toolset._tool_filter[0] is tool

    def test_add_callable(self):
        self.toolset.add_tools([sample_func])
        assert len(self.toolset._tool_filter) == 1
        assert isinstance(self.toolset._tool_filter[0], FunctionTool)

    def test_add_string_tool_found(self):
        with patch("trpc_agent_sdk.tools._default_toolset.get_tool") as mock_get:
            mock_tool = DummyTool(name="from_registry")
            mock_get.return_value = mock_tool
            self.toolset.add_tools(["from_registry"])
            assert len(self.toolset._tool_filter) == 1
            assert self.toolset._tool_filter[0] is mock_tool

    def test_add_string_tool_not_found_logs_warning(self):
        with patch("trpc_agent_sdk.tools._default_toolset.get_tool", return_value=None), \
             patch("trpc_agent_sdk.tools._default_toolset.logger") as mock_logger:
            self.toolset.add_tools(["missing_tool"])
            mock_logger.warning.assert_called_once()
            assert len(self.toolset._tool_filter) == 0

    def test_add_unsupported_type_raises(self):
        with pytest.raises(ValueError, match="Unsupported tool type"):
            self.toolset.add_tools([12345])

    def test_add_multiple_tools(self):
        tool = DummyTool(name="t1")
        self.toolset.add_tools([tool, sample_func])
        assert len(self.toolset._tool_filter) == 2


class TestDefaultToolSetGetTools:

    def setup_method(self):
        self.toolset = DefaultToolSet()
        self.toolset.initialize()

    @pytest.mark.asyncio
    async def test_get_tools_empty(self):
        tools = await self.toolset.get_tools()
        assert tools == []

    @pytest.mark.asyncio
    async def test_get_tools_returns_all(self):
        t1 = DummyTool(name="t1")
        t2 = DummyTool(name="t2")
        self.toolset.add_tools([t1, t2])

        with patch.object(self.toolset, "_is_tool_selected", return_value=True):
            tools = await self.toolset.get_tools()
            assert len(tools) == 2

    @pytest.mark.asyncio
    async def test_get_tools_filters_by_selection(self):
        t1 = DummyTool(name="selected")
        t2 = DummyTool(name="not_selected")
        self.toolset.add_tools([t1, t2])

        def select_only_selected(tool, ctx):
            return tool.name == "selected"

        with patch.object(self.toolset, "_is_tool_selected", side_effect=select_only_selected):
            tools = await self.toolset.get_tools()
            assert len(tools) == 1
            assert tools[0].name == "selected"


class TestDefaultToolSetClose:

    @pytest.mark.asyncio
    async def test_close_is_noop(self):
        ts = DefaultToolSet()
        ts.initialize()
        await ts.close()  # Should not raise
