# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.tools._preload_memory_tool import PreloadMemoryTool, preload_memory_tool
from trpc_agent_sdk.types import Content, MemoryEntry, Part


class TestPreloadMemoryToolInit:

    def test_init(self):
        tool = PreloadMemoryTool()
        assert tool.name == "preload_memory"
        assert tool.description == "preload_memory"


class TestPreloadMemoryToolRunAsync:

    @pytest.mark.asyncio
    async def test_run_returns_empty_dict(self):
        tool = PreloadMemoryTool()
        ctx = MagicMock(spec=InvocationContext)
        result = await tool._run_async_impl(tool_context=ctx, args={})
        assert result == {}


class TestPreloadMemoryToolProcessRequest:

    def _has_system_instruction(self, llm_request):
        return (llm_request.config is not None
                and llm_request.config.system_instruction is not None
                and len(str(llm_request.config.system_instruction)) > 0)

    @pytest.mark.asyncio
    async def test_no_user_content(self):
        tool = PreloadMemoryTool()
        ctx = MagicMock(spec=InvocationContext)
        ctx.user_content = None
        llm_request = LlmRequest()

        await tool.process_request(tool_context=ctx, llm_request=llm_request)
        assert not self._has_system_instruction(llm_request)

    @pytest.mark.asyncio
    async def test_user_content_no_parts(self):
        tool = PreloadMemoryTool()
        ctx = MagicMock(spec=InvocationContext)
        ctx.user_content = Content(parts=[])
        llm_request = LlmRequest()

        await tool.process_request(tool_context=ctx, llm_request=llm_request)
        assert not self._has_system_instruction(llm_request)

    @pytest.mark.asyncio
    async def test_user_content_no_text(self):
        tool = PreloadMemoryTool()
        ctx = MagicMock(spec=InvocationContext)
        part = MagicMock(spec=Part)
        part.text = None
        ctx.user_content = Content(parts=[part])
        llm_request = LlmRequest()

        await tool.process_request(tool_context=ctx, llm_request=llm_request)
        assert not self._has_system_instruction(llm_request)

    @pytest.mark.asyncio
    async def test_no_memories_found(self):
        tool = PreloadMemoryTool()
        ctx = MagicMock(spec=InvocationContext)
        ctx.user_content = Content(parts=[Part.from_text(text="hello")])
        mock_response = MagicMock()
        mock_response.memories = []
        ctx.search_memory = AsyncMock(return_value=mock_response)
        llm_request = LlmRequest()

        await tool.process_request(tool_context=ctx, llm_request=llm_request)
        assert not self._has_system_instruction(llm_request)

    @pytest.mark.asyncio
    async def test_memories_found_adds_instructions(self):
        tool = PreloadMemoryTool()
        ctx = MagicMock(spec=InvocationContext)
        ctx.user_content = Content(parts=[Part.from_text(text="hello")])

        memory = MagicMock(spec=MemoryEntry)
        memory.timestamp = "2026-01-01"
        memory.author = "user"
        memory.content = Content(parts=[Part.from_text(text="past memory")])

        mock_response = MagicMock()
        mock_response.memories = [memory]
        ctx.search_memory = AsyncMock(return_value=mock_response)
        llm_request = LlmRequest()

        await tool.process_request(tool_context=ctx, llm_request=llm_request)
        assert self._has_system_instruction(llm_request)
        assert "PAST_CONVERSATIONS" in str(llm_request.config.system_instruction)

    @pytest.mark.asyncio
    async def test_memory_without_timestamp(self):
        tool = PreloadMemoryTool()
        ctx = MagicMock(spec=InvocationContext)
        ctx.user_content = Content(parts=[Part.from_text(text="hello")])

        memory = MagicMock(spec=MemoryEntry)
        memory.timestamp = None
        memory.author = None
        memory.content = Content(parts=[Part.from_text(text="mem text")])

        mock_response = MagicMock()
        mock_response.memories = [memory]
        ctx.search_memory = AsyncMock(return_value=mock_response)
        llm_request = LlmRequest()

        await tool.process_request(tool_context=ctx, llm_request=llm_request)
        assert self._has_system_instruction(llm_request)
        assert "mem text" in str(llm_request.config.system_instruction)

    @pytest.mark.asyncio
    async def test_memory_with_empty_parts(self):
        tool = PreloadMemoryTool()
        ctx = MagicMock(spec=InvocationContext)
        ctx.user_content = Content(parts=[Part.from_text(text="hello")])

        memory = MagicMock(spec=MemoryEntry)
        memory.timestamp = None
        memory.author = None
        memory.content = Content(parts=[])

        mock_response = MagicMock()
        mock_response.memories = [memory]
        ctx.search_memory = AsyncMock(return_value=mock_response)
        llm_request = LlmRequest()

        await tool.process_request(tool_context=ctx, llm_request=llm_request)
        assert not self._has_system_instruction(llm_request)


class TestPreloadMemoryToolSingleton:

    def test_module_level_instance(self):
        assert isinstance(preload_memory_tool, PreloadMemoryTool)
