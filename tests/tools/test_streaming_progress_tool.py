# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from __future__ import annotations

from typing import AsyncIterator
from unittest.mock import MagicMock

import pytest

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools._function_tool import FunctionTool
from trpc_agent_sdk.tools._streaming_progress_tool import StreamingProgressTool


async def streaming_func(query: str) -> AsyncIterator[dict]:
    """Stream a few progress updates."""
    yield {"status": "started", "query": query}
    yield {"status": "step", "step": 1}
    yield {"status": "step", "step": 2}
    yield {"status": "done", "query": query, "steps": 2}


async def streaming_func_no_yield() -> AsyncIterator[dict]:
    """Generator that never yields."""
    if False:  # pragma: no cover
        yield {}


async def streaming_func_str() -> AsyncIterator[str]:
    """Stream a few string progress updates."""
    yield "starting"
    yield "halfway"
    yield "completed"


def regular_sync_func(x: int) -> int:
    """Not a generator."""
    return x


async def regular_async_func(x: int) -> int:
    """Not a generator."""
    return x


class TestStreamingProgressToolInit:

    def test_init_with_async_generator(self):
        tool = StreamingProgressTool(streaming_func)
        assert tool.name == "streaming_func"
        assert tool.is_progress_streaming is True
        assert tool.func is streaming_func

    def test_init_rejects_sync_function(self):
        with pytest.raises(TypeError, match="async def.*generator"):
            StreamingProgressTool(regular_sync_func)  # type: ignore[arg-type]

    def test_init_rejects_plain_async_function(self):
        with pytest.raises(TypeError, match="async def.*generator"):
            StreamingProgressTool(regular_async_func)  # type: ignore[arg-type]

    def test_is_progress_streaming_property(self):
        tool = StreamingProgressTool(streaming_func)
        assert tool.is_progress_streaming is True

    def test_base_function_tool_is_not_progress_streaming(self):
        # Plain FunctionTool must not silently inherit the streaming flag.
        ft = FunctionTool(regular_sync_func)
        assert getattr(ft, "is_progress_streaming", False) is False


class TestStreamingProgressToolExecution:

    @pytest.mark.asyncio
    async def test_run_streaming_yields_all_values(self):
        tool = StreamingProgressTool(streaming_func)
        ctx = MagicMock(spec=InvocationContext)
        ctx.agent = MagicMock()

        out = []
        async for value in tool.run_streaming(tool_context=ctx, args={"query": "hi"}):
            out.append(value)
        assert out == [
            {"status": "started", "query": "hi"},
            {"status": "step", "step": 1},
            {"status": "step", "step": 2},
            {"status": "done", "query": "hi", "steps": 2},
        ]

    @pytest.mark.asyncio
    async def test_run_streaming_with_string_payloads(self):
        tool = StreamingProgressTool(streaming_func_str)
        ctx = MagicMock(spec=InvocationContext)
        ctx.agent = MagicMock()

        out = []
        async for value in tool.run_streaming(tool_context=ctx, args={}):
            out.append(value)
        assert out == ["starting", "halfway", "completed"]

    @pytest.mark.asyncio
    async def test_run_streaming_missing_mandatory_arg(self):
        tool = StreamingProgressTool(streaming_func)
        ctx = MagicMock(spec=InvocationContext)
        ctx.agent = MagicMock()

        out = []
        async for value in tool.run_streaming(tool_context=ctx, args={}):
            out.append(value)
        # Exactly one error payload, no exception bubbled up.
        assert len(out) == 1
        assert "error" in out[0]
        assert "missing" in out[0]["error"].lower()

    @pytest.mark.asyncio
    async def test_run_async_impl_refuses_direct_invocation(self):
        # Single-responsibility: streaming tools must NOT be drainable via
        # the synchronous tool path. The only entry point is run_streaming(),
        # which ToolsProcessor.execute_tools_async calls.
        tool = StreamingProgressTool(streaming_func)
        ctx = MagicMock(spec=InvocationContext)
        ctx.agent = MagicMock()

        with pytest.raises(RuntimeError, match="does not support direct"):
            await tool._run_async_impl(tool_context=ctx, args={"query": "hi"})


class TestStreamingProgressToolDeclaration:

    def test_get_declaration_includes_function_name(self):
        tool = StreamingProgressTool(streaming_func)
        decl = tool._get_declaration()
        assert decl is not None
        assert decl.name == "streaming_func"
