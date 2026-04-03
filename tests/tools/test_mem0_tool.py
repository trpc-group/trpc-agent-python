# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.types import FunctionDeclaration, Type

try:
    import mem0  # noqa: F401
    HAS_MEM0 = True
except ImportError:
    HAS_MEM0 = False

pytestmark = pytest.mark.skipif(not HAS_MEM0, reason="mem0 not installed")


class TestSearchMemoryTool:

    @pytest.fixture
    def mock_client(self):
        client = AsyncMock()
        return client

    @pytest.fixture
    def tool(self, mock_client):
        from trpc_agent_sdk.tools.mem0_tool import SearchMemoryTool
        return SearchMemoryTool(client=mock_client)

    def test_init(self, tool):
        assert tool.name == "search_memory"
        assert tool.description == "Search through past conversations and memories"

    def test_init_with_extra_kwargs(self, mock_client):
        from trpc_agent_sdk.tools.mem0_tool import SearchMemoryTool
        tool = SearchMemoryTool(client=mock_client, top_k=5)
        assert tool.kwargs == {"top_k": 5}

    def test_get_declaration(self, tool):
        decl = tool._get_declaration()
        assert isinstance(decl, FunctionDeclaration)
        assert decl.name == "search_memory"
        assert decl.parameters.type == Type.OBJECT
        assert "query" in decl.parameters.properties
        assert decl.parameters.required == ["query"]

    @pytest.mark.asyncio
    async def test_run_with_results(self, tool, mock_client):
        ctx = MagicMock(spec=InvocationContext)
        ctx.user_id = "user_1"
        mock_client.search = AsyncMock(return_value={
            "results": [
                {"memory": "I like Python"},
                {"memory": "I work at ACME"},
            ]
        })

        result = await tool._run_async_impl(tool_context=ctx, args={"query": "preferences"})
        assert result["status"] == "success"
        assert "I like Python" in result["memories"]
        assert result["user_id"] == "user_1"

    @pytest.mark.asyncio
    async def test_run_no_results(self, tool, mock_client):
        ctx = MagicMock(spec=InvocationContext)
        ctx.user_id = "user_1"
        mock_client.search = AsyncMock(return_value={"results": []})

        result = await tool._run_async_impl(tool_context=ctx, args={"query": "something"})
        assert result["status"] == "no_memories"

    @pytest.mark.asyncio
    async def test_run_empty_results_key(self, tool, mock_client):
        ctx = MagicMock(spec=InvocationContext)
        ctx.user_id = "user_1"
        mock_client.search = AsyncMock(return_value={})

        result = await tool._run_async_impl(tool_context=ctx, args={"query": "q"})
        assert result["status"] == "no_memories"


class TestSaveMemoryTool:

    @pytest.fixture
    def mock_client(self):
        return AsyncMock()

    @pytest.fixture
    def tool(self, mock_client):
        from trpc_agent_sdk.tools.mem0_tool import SaveMemoryTool
        return SaveMemoryTool(client=mock_client)

    def test_init(self, tool):
        assert tool.name == "save_memory"
        assert tool.description == "Save important information to memory"

    def test_init_sets_infer_default(self, tool):
        assert tool.kwargs.get("infer") is True

    def test_init_preserves_custom_infer(self, mock_client):
        from trpc_agent_sdk.tools.mem0_tool import SaveMemoryTool
        tool = SaveMemoryTool(client=mock_client, infer=False)
        assert tool.kwargs["infer"] is False

    def test_get_declaration(self, tool):
        decl = tool._get_declaration()
        assert isinstance(decl, FunctionDeclaration)
        assert decl.name == "save_memory"
        assert decl.parameters.type == Type.OBJECT
        assert "content" in decl.parameters.properties
        assert decl.parameters.required == ["content"]

    @pytest.mark.asyncio
    async def test_run_success(self, tool, mock_client):
        ctx = MagicMock(spec=InvocationContext)
        ctx.user_id = "user_1"
        mock_client.add = AsyncMock(return_value={"id": "mem_1"})

        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"content": "Remember this"},
        )
        assert result["status"] == "success"
        assert result["user_id"] == "user_1"
        mock_client.add.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_error(self, tool, mock_client):
        ctx = MagicMock(spec=InvocationContext)
        ctx.user_id = "user_1"
        mock_client.add = AsyncMock(side_effect=RuntimeError("API error"))

        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"content": "data"},
        )
        assert result["status"] == "error"
        assert "API error" in result["message"]
        assert result["user_id"] == "user_1"

    def test_init_with_invalid_filters_raises(self, mock_client):
        from trpc_agent_sdk.tools.mem0_tool import SaveMemoryTool
        with pytest.raises(ValueError, match="not found"):
            SaveMemoryTool(client=mock_client, filters_name=["nonexistent"])
