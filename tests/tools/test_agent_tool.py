# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from trpc_agent_sdk.abc import AgentABC
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.tools._agent_tool import AGENT_TOOL_APP_NAME_SUFFIX, AgentTool
from trpc_agent_sdk.types import Content, FunctionDeclaration, Part, Schema, Type


class InputSchema(BaseModel):
    query: str
    limit: int = 10


class OutputSchema(BaseModel):
    answer: str
    confidence: float


class TestAgentToolInit:

    def test_basic_init(self):
        mock_agent = MagicMock(spec=AgentABC)
        mock_agent.name = "test_agent"
        mock_agent.description = "A test agent"

        tool = AgentTool(agent=mock_agent)
        assert tool.name == "test_agent"
        assert tool.description == "A test agent"
        assert tool.agent is mock_agent
        assert tool.skip_summarization is False

    def test_init_with_skip_summarization(self):
        mock_agent = MagicMock(spec=AgentABC)
        mock_agent.name = "agent"
        mock_agent.description = "desc"

        tool = AgentTool(agent=mock_agent, skip_summarization=True)
        assert tool.skip_summarization is True


class TestAgentToolGetDeclaration:

    def test_declaration_without_input_schema(self):
        mock_agent = MagicMock(spec=AgentABC)
        mock_agent.name = "basic_agent"
        mock_agent.description = "Basic agent"
        # Not an LlmAgent
        mock_agent.__class__ = AgentABC

        tool = AgentTool(agent=mock_agent)
        decl = tool._get_declaration()

        assert isinstance(decl, FunctionDeclaration)
        assert decl.name == "basic_agent"
        assert decl.parameters.properties["request"].type == Type.STRING
        assert decl.response.type == Type.STRING

    def test_declaration_with_input_schema(self):
        from trpc_agent_sdk.agents import LlmAgent

        mock_agent = MagicMock(spec=LlmAgent)
        mock_agent.name = "schema_agent"
        mock_agent.description = "Schema agent"
        mock_agent.input_schema = InputSchema
        mock_agent.output_schema = None

        tool = AgentTool(agent=mock_agent)
        decl = tool._get_declaration()
        assert decl.name == "schema_agent"
        assert decl.parameters is not None

    def test_declaration_with_output_schema(self):
        from trpc_agent_sdk.agents import LlmAgent

        mock_agent = MagicMock(spec=LlmAgent)
        mock_agent.name = "agent_out"
        mock_agent.description = "desc"
        mock_agent.input_schema = None
        mock_agent.output_schema = OutputSchema

        tool = AgentTool(agent=mock_agent)
        decl = tool._get_declaration()
        assert decl.response.type == Type.OBJECT

    def test_declaration_without_output_schema(self):
        mock_agent = MagicMock(spec=AgentABC)
        mock_agent.name = "agent"
        mock_agent.description = "desc"

        tool = AgentTool(agent=mock_agent)
        decl = tool._get_declaration()
        assert decl.response.type == Type.STRING


class TestAgentToolRunAsyncImpl:

    @pytest.fixture
    def mock_context(self):
        ctx = MagicMock(spec=InvocationContext)
        ctx.artifact_service = MagicMock()
        ctx.state = MagicMock()
        ctx.state.to_dict.return_value = {}
        ctx.event_actions = MagicMock()
        ctx.save_artifact = AsyncMock()
        return ctx

    @pytest.mark.asyncio
    async def test_run_basic_agent(self, mock_context):
        mock_agent = MagicMock(spec=AgentABC)
        mock_agent.name = "test_agent"
        mock_agent.description = "test"

        tool = AgentTool(agent=mock_agent)

        last_event = MagicMock(spec=Event)
        last_event.content = Content(parts=[Part.from_text(text="Hello world")])
        last_event.actions = MagicMock()
        last_event.actions.state_delta = {}

        with patch("trpc_agent_sdk.runners.Runner") as MockRunner:
            mock_runner = AsyncMock()
            MockRunner.return_value = mock_runner
            mock_runner.session_service = AsyncMock()
            mock_runner.session_service.create_session = AsyncMock(return_value=MagicMock(
                id="session_id", user_id="tmp_user", app_name=f"test_agent{AGENT_TOOL_APP_NAME_SUFFIX}"
            ))
            mock_runner.artifact_service = None

            async def mock_run_async(**kwargs):
                yield last_event

            mock_runner.run_async = mock_run_async
            mock_runner.close = AsyncMock()

            result = await tool._run_async_impl(
                args={"request": "Hello"},
                tool_context=mock_context,
            )
            assert result == "Hello world"
            mock_runner.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_returns_empty_on_no_event(self, mock_context):
        mock_agent = MagicMock(spec=AgentABC)
        mock_agent.name = "agent"
        mock_agent.description = "desc"

        tool = AgentTool(agent=mock_agent)

        with patch("trpc_agent_sdk.runners.Runner") as MockRunner:
            mock_runner = AsyncMock()
            MockRunner.return_value = mock_runner
            mock_runner.session_service = AsyncMock()
            mock_runner.session_service.create_session = AsyncMock(return_value=MagicMock(
                id="sid", user_id="u", app_name="app"
            ))
            mock_runner.artifact_service = None

            async def mock_run_async(**kwargs):
                return
                yield  # make it an async generator

            mock_runner.run_async = mock_run_async
            mock_runner.close = AsyncMock()

            result = await tool._run_async_impl(
                args={"request": "Hello"},
                tool_context=mock_context,
            )
            assert result == ""

    @pytest.mark.asyncio
    async def test_run_with_skip_summarization(self, mock_context):
        mock_agent = MagicMock(spec=AgentABC)
        mock_agent.name = "agent"
        mock_agent.description = "desc"

        tool = AgentTool(agent=mock_agent, skip_summarization=True)

        last_event = MagicMock(spec=Event)
        last_event.content = Content(parts=[Part.from_text(text="result")])
        last_event.actions = MagicMock()
        last_event.actions.state_delta = {}

        with patch("trpc_agent_sdk.runners.Runner") as MockRunner:
            mock_runner = AsyncMock()
            MockRunner.return_value = mock_runner
            mock_runner.session_service = AsyncMock()
            mock_runner.session_service.create_session = AsyncMock(return_value=MagicMock(
                id="sid", user_id="u", app_name="app"
            ))
            mock_runner.artifact_service = None

            async def mock_run_async(**kwargs):
                yield last_event

            mock_runner.run_async = mock_run_async
            mock_runner.close = AsyncMock()

            await tool._run_async_impl(args={"request": "Hi"}, tool_context=mock_context)
            assert mock_context.event_actions.skip_summarization is True

    @pytest.mark.asyncio
    async def test_run_forwards_state_delta(self, mock_context):
        mock_agent = MagicMock(spec=AgentABC)
        mock_agent.name = "agent"
        mock_agent.description = "desc"

        tool = AgentTool(agent=mock_agent)

        event1 = MagicMock(spec=Event)
        event1.content = Content(parts=[Part.from_text(text="r")])
        event1.actions = MagicMock()
        event1.actions.state_delta = {"key": "value"}

        with patch("trpc_agent_sdk.runners.Runner") as MockRunner:
            mock_runner = AsyncMock()
            MockRunner.return_value = mock_runner
            mock_runner.session_service = AsyncMock()
            mock_runner.session_service.create_session = AsyncMock(return_value=MagicMock(
                id="sid", user_id="u", app_name="app"
            ))
            mock_runner.artifact_service = None

            async def mock_run_async(**kwargs):
                yield event1

            mock_runner.run_async = mock_run_async
            mock_runner.close = AsyncMock()

            await tool._run_async_impl(args={"request": "test"}, tool_context=mock_context)
            mock_context.state.update.assert_called_with({"key": "value"})

    @pytest.mark.asyncio
    async def test_run_raises_on_error(self, mock_context):
        mock_agent = MagicMock(spec=AgentABC)
        mock_agent.name = "agent"
        mock_agent.description = "desc"

        tool = AgentTool(agent=mock_agent)

        with patch("trpc_agent_sdk.runners.Runner") as MockRunner:
            MockRunner.side_effect = RuntimeError("runner error")

            with pytest.raises(RuntimeError, match="runner error"):
                await tool._run_async_impl(args={"request": "x"}, tool_context=mock_context)


class TestAgentToolRunAsyncWithInputSchema:

    @pytest.fixture
    def mock_context(self):
        ctx = MagicMock(spec=InvocationContext)
        ctx.artifact_service = MagicMock()
        ctx.state = MagicMock()
        ctx.state.to_dict.return_value = {}
        ctx.event_actions = MagicMock()
        ctx.save_artifact = AsyncMock()
        return ctx

    @pytest.mark.asyncio
    async def test_run_with_input_schema(self, mock_context):
        from trpc_agent_sdk.agents import LlmAgent

        mock_agent = MagicMock(spec=LlmAgent)
        mock_agent.name = "schema_agent"
        mock_agent.description = "desc"
        mock_agent.input_schema = InputSchema
        mock_agent.output_schema = None

        tool = AgentTool(agent=mock_agent)

        last_event = MagicMock(spec=Event)
        last_event.content = Content(parts=[Part.from_text(text="result")])
        last_event.actions = MagicMock()
        last_event.actions.state_delta = {}

        with patch("trpc_agent_sdk.runners.Runner") as MockRunner:
            mock_runner = AsyncMock()
            MockRunner.return_value = mock_runner
            mock_runner.session_service = AsyncMock()
            mock_runner.session_service.create_session = AsyncMock(return_value=MagicMock(
                id="sid", user_id="u", app_name="app"
            ))
            mock_runner.artifact_service = None

            async def mock_run_async(**kwargs):
                yield last_event

            mock_runner.run_async = mock_run_async
            mock_runner.close = AsyncMock()

            result = await tool._run_async_impl(
                args={"query": "hello", "limit": 5},
                tool_context=mock_context,
            )
            assert result == "result"

    @pytest.mark.asyncio
    async def test_run_with_output_schema(self, mock_context):
        from trpc_agent_sdk.agents import LlmAgent

        mock_agent = MagicMock(spec=LlmAgent)
        mock_agent.name = "schema_agent"
        mock_agent.description = "desc"
        mock_agent.input_schema = None
        mock_agent.output_schema = OutputSchema

        tool = AgentTool(agent=mock_agent)

        last_event = MagicMock(spec=Event)
        last_event.content = Content(parts=[
            Part.from_text(text='{"answer": "hi", "confidence": 0.9}')
        ])
        last_event.actions = MagicMock()
        last_event.actions.state_delta = {}

        with patch("trpc_agent_sdk.runners.Runner") as MockRunner:
            mock_runner = AsyncMock()
            MockRunner.return_value = mock_runner
            mock_runner.session_service = AsyncMock()
            mock_runner.session_service.create_session = AsyncMock(return_value=MagicMock(
                id="sid", user_id="u", app_name="app"
            ))
            mock_runner.artifact_service = None

            async def mock_run_async(**kwargs):
                yield last_event

            mock_runner.run_async = mock_run_async
            mock_runner.close = AsyncMock()

            result = await tool._run_async_impl(
                args={"request": "test"},
                tool_context=mock_context,
            )
            assert isinstance(result, dict)
            assert result["answer"] == "hi"

    @pytest.mark.asyncio
    async def test_run_with_artifact_forwarding(self, mock_context):
        mock_agent = MagicMock(spec=AgentABC)
        mock_agent.name = "agent"
        mock_agent.description = "desc"

        tool = AgentTool(agent=mock_agent)

        last_event = MagicMock(spec=Event)
        last_event.content = Content(parts=[Part.from_text(text="done")])
        last_event.actions = MagicMock()
        last_event.actions.state_delta = {}

        with patch("trpc_agent_sdk.runners.Runner") as MockRunner:
            mock_runner = AsyncMock()
            MockRunner.return_value = mock_runner
            mock_runner.session_service = AsyncMock()
            mock_runner.session_service.create_session = AsyncMock(return_value=MagicMock(
                id="sid", user_id="u", app_name="app"
            ))

            mock_artifact_service = AsyncMock()
            mock_artifact_service.list_artifact_keys = AsyncMock(return_value=["file.txt"])
            mock_artifact_service.load_artifact = AsyncMock(return_value=b"data")
            mock_runner.artifact_service = mock_artifact_service

            async def mock_run_async(**kwargs):
                yield last_event

            mock_runner.run_async = mock_run_async
            mock_runner.close = AsyncMock()

            await tool._run_async_impl(
                args={"request": "test"},
                tool_context=mock_context,
            )
            mock_context.save_artifact.assert_awaited_once()


class TestAgentToolAppNameSuffix:

    def test_suffix_value(self):
        assert AGENT_TOOL_APP_NAME_SUFFIX == "_trpc_agent_tool_"
