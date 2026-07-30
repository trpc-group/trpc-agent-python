# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from __future__ import annotations

from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typing_extensions import override

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.tools._base_tool import BaseTool
from trpc_agent_sdk.tools._constants import DEFAULT_API_VARIANT
from trpc_agent_sdk.types import FunctionDeclaration, GenerateContentConfig, Schema, Tool, Type


class ConcreteTool(BaseTool):
    """Minimal concrete implementation for testing."""

    def __init__(self, name="test_tool", description="A test tool", **kwargs):
        super().__init__(name=name, description=description, **kwargs)
        self._run_result = "default_result"

    @override
    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        return self._run_result


class DeclarableTool(BaseTool):
    """Tool that provides a function declaration."""

    def __init__(self, name="declarable_tool", description="A declarable tool"):
        super().__init__(name=name, description=description)

    @override
    def _get_declaration(self) -> Optional[FunctionDeclaration]:
        return FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters=Schema(type=Type.OBJECT, properties={"query": Schema(type=Type.STRING)}),
        )

    @override
    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        return {}


class TestBaseToolInit:

    def test_basic_init(self):
        tool = ConcreteTool(name="my_tool", description="desc")
        assert tool.name == "my_tool"
        assert tool.description == "desc"

    def test_name_property(self):
        tool = ConcreteTool(name="test")
        assert tool.name == "test"

    def test_is_streaming_default_false(self):
        tool = ConcreteTool()
        assert tool.is_streaming is False

    def test_api_variant_default(self):
        tool = ConcreteTool()
        assert tool.api_variant == DEFAULT_API_VARIANT

    def test_get_declaration_returns_none(self):
        tool = ConcreteTool()
        assert tool._get_declaration() is None


class TestBaseToolRunAsync:

    @pytest.fixture
    def mock_tool_context(self):
        ctx = MagicMock(spec=InvocationContext)
        ctx.agent_context = MagicMock()
        ctx.agent = MagicMock()
        ctx.agent.before_tool_callback = None
        ctx.agent.after_tool_callback = None
        return ctx

    @pytest.mark.asyncio
    async def test_run_async_basic(self, mock_tool_context):
        tool = ConcreteTool()
        tool._run_result = "hello"
        with patch("trpc_agent_sdk.tools._base_tool.FilterRunner._run_filters", new_callable=AsyncMock) as mock_filters:
            mock_filters.return_value = "hello"
            result = await tool.run_async(tool_context=mock_tool_context, args={"key": "val"})
            assert result == "hello"

    @pytest.mark.asyncio
    async def test_run_async_creates_agent_context_if_none(self):
        ctx = MagicMock(spec=InvocationContext)
        ctx.agent_context = None
        ctx.agent = MagicMock()
        ctx.agent.before_tool_callback = None
        ctx.agent.after_tool_callback = None

        tool = ConcreteTool()
        with patch("trpc_agent_sdk.tools._base_tool.FilterRunner._run_filters", new_callable=AsyncMock) as mock_filters:
            mock_filters.return_value = "result"
            with patch("trpc_agent_sdk.tools._base_tool.create_agent_context") as mock_create:
                mock_create.return_value = MagicMock()
                await tool.run_async(tool_context=ctx, args={})
                mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_async_sets_and_resets_tool_var(self, mock_tool_context):
        tool = ConcreteTool()
        with patch("trpc_agent_sdk.tools._base_tool.set_tool_var") as mock_set, \
             patch("trpc_agent_sdk.tools._base_tool.reset_tool_var") as mock_reset, \
             patch("trpc_agent_sdk.tools._base_tool.FilterRunner._run_filters", new_callable=AsyncMock):
            mock_set.return_value = "token"
            await tool.run_async(tool_context=mock_tool_context, args={})
            mock_set.assert_called_once_with(tool)
            mock_reset.assert_called_once_with("token")

    @pytest.mark.asyncio
    async def test_run_async_resets_tool_var_on_exception(self, mock_tool_context):
        tool = ConcreteTool()
        with patch("trpc_agent_sdk.tools._base_tool.set_tool_var") as mock_set, \
             patch("trpc_agent_sdk.tools._base_tool.reset_tool_var") as mock_reset, \
             patch("trpc_agent_sdk.tools._base_tool.FilterRunner._run_filters",
                   new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            mock_set.return_value = "token"
            with pytest.raises(RuntimeError, match="boom"):
                await tool.run_async(tool_context=mock_tool_context, args={})
            mock_reset.assert_called_once_with("token")


class TestFindToolWithFunctionDeclarations:

    def test_returns_none_when_no_config(self):
        llm_request = MagicMock(spec=LlmRequest)
        llm_request.config = None
        assert BaseTool._find_tool_with_function_declarations(llm_request) is None

    def test_returns_none_when_no_tools(self):
        llm_request = MagicMock(spec=LlmRequest)
        llm_request.config = MagicMock()
        llm_request.config.tools = None
        assert BaseTool._find_tool_with_function_declarations(llm_request) is None

    def test_returns_none_when_empty_tools(self):
        llm_request = MagicMock(spec=LlmRequest)
        llm_request.config = MagicMock()
        llm_request.config.tools = []
        assert BaseTool._find_tool_with_function_declarations(llm_request) is None

    def test_returns_tool_with_function_declarations(self):
        tool = Tool(function_declarations=[FunctionDeclaration(name="fn")])
        llm_request = MagicMock(spec=LlmRequest)
        llm_request.config = MagicMock()
        llm_request.config.tools = [tool]
        result = BaseTool._find_tool_with_function_declarations(llm_request)
        assert result is tool

    def test_skips_tools_without_declarations(self):
        tool_no_decl = Tool(function_declarations=None)
        tool_with_decl = Tool(function_declarations=[FunctionDeclaration(name="fn")])
        llm_request = MagicMock(spec=LlmRequest)
        llm_request.config = MagicMock()
        llm_request.config.tools = [tool_no_decl, tool_with_decl]
        result = BaseTool._find_tool_with_function_declarations(llm_request)
        assert result is tool_with_decl


class TestProcessRequest:

    @pytest.mark.asyncio
    async def test_no_declaration_does_nothing(self):
        tool = ConcreteTool()
        ctx = MagicMock(spec=InvocationContext)
        llm_request = LlmRequest()
        await tool.process_request(tool_context=ctx, llm_request=llm_request)
        assert not llm_request.tools_dict

    @pytest.mark.asyncio
    async def test_adds_declaration_to_empty_request(self):
        tool = DeclarableTool()
        ctx = MagicMock(spec=InvocationContext)
        llm_request = LlmRequest()
        await tool.process_request(tool_context=ctx, llm_request=llm_request)
        assert tool.name in llm_request.tools_dict
        assert llm_request.config is not None
        assert llm_request.config.tools is not None
        assert len(llm_request.config.tools) == 1
        assert llm_request.config.tools[0].function_declarations[0].name == "declarable_tool"

    @pytest.mark.asyncio
    async def test_appends_to_existing_tool_declarations(self):
        tool = DeclarableTool()
        ctx = MagicMock(spec=InvocationContext)
        existing_decl = FunctionDeclaration(name="existing_fn")
        existing_tool = Tool(function_declarations=[existing_decl])
        llm_request = LlmRequest(config=GenerateContentConfig(tools=[existing_tool]))

        await tool.process_request(tool_context=ctx, llm_request=llm_request)

        assert len(llm_request.config.tools) == 1
        assert len(llm_request.config.tools[0].function_declarations) == 2

    @pytest.mark.asyncio
    async def test_creates_config_if_none(self):
        tool = DeclarableTool()
        ctx = MagicMock(spec=InvocationContext)
        llm_request = LlmRequest()
        llm_request.config = None

        await tool.process_request(tool_context=ctx, llm_request=llm_request)

        assert llm_request.config is not None
        assert llm_request.config.tools is not None

    @pytest.mark.asyncio
    async def test_creates_tools_list_if_none(self):
        tool = DeclarableTool()
        ctx = MagicMock(spec=InvocationContext)
        llm_request = LlmRequest(config=GenerateContentConfig())
        llm_request.config.tools = None

        await tool.process_request(tool_context=ctx, llm_request=llm_request)

        assert llm_request.config.tools is not None
        assert len(llm_request.config.tools) == 1

    @pytest.mark.asyncio
    async def test_appends_to_tool_with_empty_declarations(self):
        tool = DeclarableTool()
        ctx = MagicMock(spec=InvocationContext)
        existing_tool = Tool(function_declarations=None)
        llm_request = LlmRequest(config=GenerateContentConfig(tools=[existing_tool]))

        await tool.process_request(tool_context=ctx, llm_request=llm_request)

        # Existing tool has no declarations, so a new Tool should be appended
        assert len(llm_request.config.tools) == 2
