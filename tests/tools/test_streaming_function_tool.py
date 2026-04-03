# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.tools._function_tool import FunctionTool
from trpc_agent_sdk.tools._streaming_function_tool import StreamingFunctionTool


def sync_write(path: str, content: str) -> dict:
    """Write content to a file."""
    return {"success": True}


async def async_write(path: str, content: str) -> dict:
    """Async write."""
    return {"success": True}


class TestStreamingFunctionToolInit:

    def test_from_sync_function(self):
        tool = StreamingFunctionTool(sync_write)
        assert tool.name == "sync_write"
        assert tool.is_streaming is True
        assert tool.func is sync_write

    def test_from_async_function(self):
        tool = StreamingFunctionTool(async_write)
        assert tool.name == "async_write"
        assert tool.is_streaming is True

    def test_from_function_tool(self):
        ft = FunctionTool(sync_write)
        tool = StreamingFunctionTool(ft)
        assert tool.name == "sync_write"
        assert tool.is_streaming is True
        assert tool.func is sync_write

    def test_from_function_tool_copies_filters(self):
        mock_filter = MagicMock(spec=BaseFilter)
        ft = FunctionTool(sync_write)
        ft._filters = [mock_filter]
        tool = StreamingFunctionTool(ft)
        assert mock_filter in tool.filters

    def test_from_function_tool_explicit_filters_override(self):
        mock_filter = MagicMock(spec=BaseFilter)
        ft = FunctionTool(sync_write)
        ft._filters = [MagicMock(spec=BaseFilter)]
        tool = StreamingFunctionTool(ft, filters=[mock_filter])
        assert mock_filter in tool.filters

    def test_with_invalid_filters_name_raises(self):
        with pytest.raises(ValueError, match="not found"):
            StreamingFunctionTool(sync_write, filters_name=["nonexistent"])


class TestStreamingFunctionToolIsStreaming:

    def test_is_streaming_always_true(self):
        tool = StreamingFunctionTool(sync_write)
        assert tool.is_streaming is True

    def test_base_function_tool_is_not_streaming(self):
        ft = FunctionTool(sync_write)
        assert ft.is_streaming is False

    def test_streaming_overrides_base(self):
        ft = FunctionTool(sync_write)
        st = StreamingFunctionTool(ft)
        assert ft.is_streaming is False
        assert st.is_streaming is True


class TestStreamingFunctionToolExecution:

    @pytest.mark.asyncio
    async def test_run_sync_function(self):
        tool = StreamingFunctionTool(sync_write)
        ctx = MagicMock(spec=InvocationContext)
        ctx.agent = MagicMock()
        ctx.agent.parallel_tool_calls = False

        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"path": "/tmp/test", "content": "hello"},
        )
        assert result == {"success": True}

    @pytest.mark.asyncio
    async def test_run_async_function(self):
        tool = StreamingFunctionTool(async_write)
        ctx = MagicMock(spec=InvocationContext)
        ctx.agent = MagicMock()

        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"path": "/tmp/test", "content": "hello"},
        )
        assert result == {"success": True}

    def test_get_declaration(self):
        tool = StreamingFunctionTool(sync_write)
        decl = tool._get_declaration()
        assert decl is not None
        assert decl.name == "sync_write"

    def test_from_function_tool_no_extra_filters(self):
        ft = FunctionTool(sync_write)
        tool = StreamingFunctionTool(ft)
        assert tool.filters is not None
