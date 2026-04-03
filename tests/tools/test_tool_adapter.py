# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typing_extensions import override

from trpc_agent_sdk.abc import ToolSetABC as BaseToolSet
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools._base_tool import BaseTool
from trpc_agent_sdk.tools._function_tool import FunctionTool
from trpc_agent_sdk.tools._tool_adapter import (
    convert_toolunion_to_tool_list,
    create_tool,
    create_toolset,
)


class DummyTool(BaseTool):

    def __init__(self, name="dummy", description="dummy"):
        super().__init__(name=name, description=description)

    @override
    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        return {}


def sample_callable(x: str) -> str:
    """Sample."""
    return x


class TestCreateTool:

    def test_create_from_callable(self):
        tool = create_tool(sample_callable)
        assert isinstance(tool, FunctionTool)
        assert tool.name == "sample_callable"

    def test_create_from_base_tool(self):
        t = DummyTool(name="my_tool")
        result = create_tool(t)
        assert result is t

    def test_create_from_string_found(self):
        with patch("trpc_agent_sdk.tools._tool_adapter.get_tool") as mock_get:
            mock_tool = DummyTool(name="from_reg")
            mock_get.return_value = mock_tool
            result = create_tool("from_reg")
            assert result is mock_tool

    def test_create_from_string_not_found_raises(self):
        with patch("trpc_agent_sdk.tools._tool_adapter.get_tool", return_value=None):
            with pytest.raises(ValueError, match="Cannot find tool"):
                create_tool("missing")

    def test_create_from_unsupported_type_raises(self):
        with pytest.raises(TypeError, match="Unsupported tool type"):
            create_tool(12345)

    def test_create_from_list(self):
        t = DummyTool(name="t1")
        result = create_tool([sample_callable, t])
        assert isinstance(result, list)
        assert len(result) == 2
        assert isinstance(result[0], FunctionTool)
        assert result[1] is t

    def test_create_single_returns_single(self):
        result = create_tool(sample_callable)
        assert isinstance(result, BaseTool)
        assert not isinstance(result, list)

    def test_create_with_filters_name(self):
        with patch.object(FunctionTool, "add_filters") as mock_add:
            create_tool(sample_callable, filters_name=["f1"])
            mock_add.assert_called_once_with(["f1"], True)

    def test_create_with_need_cache(self):
        with patch("trpc_agent_sdk.tools._tool_adapter.ToolRegistry") as MockReg:
            mock_registry = MagicMock()
            MockReg.return_value = mock_registry
            create_tool(sample_callable, need_cache=True)
            mock_registry.add.assert_called_once()


class TestCreateToolset:

    def test_create_from_base_toolset(self):
        mock_ts = MagicMock(spec=BaseToolSet)
        mock_ts.name = "ts"
        result = create_toolset(mock_ts)
        assert result is mock_ts

    def test_create_from_string_found(self):
        mock_ts = MagicMock(spec=BaseToolSet)
        mock_ts.name = "found_ts"
        with patch("trpc_agent_sdk.tools._tool_adapter.get_tool_set", return_value=mock_ts):
            result = create_toolset("found_ts")
            assert result is mock_ts

    def test_create_from_string_not_found_raises(self):
        with patch("trpc_agent_sdk.tools._tool_adapter.get_tool_set", return_value=None):
            with pytest.raises(ValueError, match="Cannot find toolset"):
                create_toolset("missing_ts")

    def test_create_from_callable_wraps_in_default_toolset(self):
        result = create_toolset(sample_callable)
        assert isinstance(result, BaseToolSet)

    def test_create_from_list(self):
        mock_ts = MagicMock(spec=BaseToolSet)
        mock_ts.name = "ts1"
        result = create_toolset([mock_ts, sample_callable])
        assert isinstance(result, list)
        assert len(result) == 2

    def test_create_from_list_with_need_cache(self):
        mock_ts = MagicMock(spec=BaseToolSet)
        mock_ts.name = f"ts_cache_{id(self)}"
        with patch("trpc_agent_sdk.tools._tool_adapter.ToolSetRegistry") as MockReg:
            mock_registry = MagicMock()
            MockReg.return_value = mock_registry
            create_toolset([mock_ts], need_cache=True)
            mock_registry.add.assert_called_once()


class TestConvertToolunionToToolList:

    @pytest.mark.asyncio
    async def test_with_base_tools(self):
        t1 = DummyTool(name="t1")
        t2 = DummyTool(name="t2")
        ctx = MagicMock(spec=InvocationContext)

        result = await convert_toolunion_to_tool_list([t1, t2], ctx)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_with_toolset(self):
        mock_ts = AsyncMock(spec=BaseToolSet)
        t1 = DummyTool(name="ts_tool")
        mock_ts.get_tools = AsyncMock(return_value=[t1])
        ctx = MagicMock(spec=InvocationContext)

        result = await convert_toolunion_to_tool_list([mock_ts], ctx)
        assert len(result) == 1
        assert result[0] is t1

    @pytest.mark.asyncio
    async def test_mixed_tools_and_toolsets(self):
        t1 = DummyTool(name="direct")
        mock_ts = AsyncMock(spec=BaseToolSet)
        t2 = DummyTool(name="from_ts")
        mock_ts.get_tools = AsyncMock(return_value=[t2])
        ctx = MagicMock(spec=InvocationContext)

        result = await convert_toolunion_to_tool_list([t1, mock_ts], ctx)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_unsupported_type_raises(self):
        ctx = MagicMock(spec=InvocationContext)
        with pytest.raises(TypeError, match="Unsupported tool type"):
            await convert_toolunion_to_tool_list(["not_a_tool"], ctx)

    @pytest.mark.asyncio
    async def test_empty_list(self):
        ctx = MagicMock(spec=InvocationContext)
        result = await convert_toolunion_to_tool_list([], ctx)
        assert result == []
