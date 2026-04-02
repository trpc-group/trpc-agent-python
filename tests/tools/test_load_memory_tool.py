# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.tools._load_memory_tool import (
    LoadMemoryResponse,
    LoadMemoryTool,
    load_memory,
    load_memory_tool,
)
from trpc_agent_sdk.types import FunctionDeclaration, MemoryEntry, Schema, Type


class TestLoadMemoryResponse:

    def test_default_empty(self):
        rsp = LoadMemoryResponse()
        assert rsp.memories == []

    def test_with_memories(self):
        entry = MagicMock(spec=MemoryEntry)
        rsp = LoadMemoryResponse(memories=[entry])
        assert len(rsp.memories) == 1


class TestLoadMemoryFunction:

    @pytest.mark.asyncio
    async def test_load_memory_returns_json(self):
        mock_ctx = MagicMock(spec=InvocationContext)
        mock_response = MagicMock()
        mock_response.memories = []
        mock_ctx.search_memory = AsyncMock(return_value=mock_response)

        result = await load_memory("test query", mock_ctx)
        parsed = json.loads(result)
        assert "memories" in parsed

    @pytest.mark.asyncio
    async def test_load_memory_with_results(self):
        mock_ctx = MagicMock(spec=InvocationContext)
        mock_entry = MagicMock(spec=MemoryEntry)
        mock_response = MagicMock()
        mock_response.memories = [mock_entry]
        mock_ctx.search_memory = AsyncMock(return_value=mock_response)

        result = await load_memory("query", mock_ctx)
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_load_memory_calls_search_memory(self):
        mock_ctx = MagicMock(spec=InvocationContext)
        mock_response = MagicMock()
        mock_response.memories = []
        mock_ctx.search_memory = AsyncMock(return_value=mock_response)

        await load_memory("my_query", mock_ctx)
        mock_ctx.search_memory.assert_awaited_once_with("my_query")


class TestLoadMemoryTool:

    def test_init(self):
        tool = LoadMemoryTool()
        assert tool.name == "load_memory"

    def test_get_declaration(self):
        tool = LoadMemoryTool()
        decl = tool._get_declaration()
        assert isinstance(decl, FunctionDeclaration)
        assert decl.name == "load_memory"
        assert decl.parameters.type == Type.OBJECT
        assert "query" in decl.parameters.properties

    @pytest.mark.asyncio
    async def test_process_request_adds_instructions(self):
        tool = LoadMemoryTool()
        ctx = MagicMock(spec=InvocationContext)
        llm_request = LlmRequest()

        await tool.process_request(tool_context=ctx, llm_request=llm_request)

        assert tool.name in llm_request.tools_dict
        assert llm_request.config is not None
        assert llm_request.config.system_instruction is not None
        assert "memory" in llm_request.config.system_instruction.lower()


class TestLoadMemoryToolSingleton:

    def test_module_level_instance(self):
        assert isinstance(load_memory_tool, LoadMemoryTool)
