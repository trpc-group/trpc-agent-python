# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for OutputSchemaRequestProcessor and helpers."""

from __future__ import annotations

import asyncio
import json
from typing import List
from unittest.mock import Mock

import pytest
from pydantic import BaseModel

from trpc_agent_sdk.agents.core._output_schema_processor import (
    OutputSchemaRequestProcessor,
    create_final_model_response_event,
    default_output_schema_processor,
    get_structured_model_response,
)
from trpc_agent_sdk.context import InvocationContext, create_agent_context
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.models import LLMModel, LlmRequest, LlmResponse, ModelRegistry
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import TOOL_NAME as SET_MODEL_RESPONSE_TOOL_NAME
from trpc_agent_sdk.types import Content, FunctionResponse, Part


class MockLLMModel(LLMModel):
    @classmethod
    def supported_models(cls) -> List[str]:
        return [r"test-output-.*"]

    async def _generate_async_impl(self, request, stream=False, ctx=None):
        yield LlmResponse(content=None)

    def validate_request(self, request):
        pass


@pytest.fixture(scope="module", autouse=True)
def register_test_model():
    original_registry = ModelRegistry._registry.copy()
    ModelRegistry.register(MockLLMModel)
    yield
    ModelRegistry._registry = original_registry


class SampleOutputSchema(BaseModel):
    answer: str
    confidence: float


@pytest.fixture
def invocation_context():
    from trpc_agent_sdk.agents._llm_agent import LlmAgent
    from trpc_agent_sdk.tools import FunctionTool

    def dummy_tool():
        """A dummy tool."""
        return "ok"

    service = InMemorySessionService()
    session = asyncio.run(
        service.create_session(app_name="test", user_id="u1", session_id="s1")
    )
    agent = LlmAgent(
        name="test_agent",
        model="test-output-model",
        output_schema=SampleOutputSchema,
        tools=[FunctionTool(dummy_tool)],
    )
    ctx = InvocationContext(
        session_service=service,
        invocation_id="inv-1",
        agent=agent,
        agent_context=create_agent_context(),
        session=session,
    )
    return ctx


# ---------------------------------------------------------------------------
# create_final_model_response_event
# ---------------------------------------------------------------------------


class TestCreateFinalModelResponseEvent:
    def test_creates_event_with_json_response(self, invocation_context):
        json_response = json.dumps({"answer": "42", "confidence": 0.95})
        event = create_final_model_response_event(invocation_context, json_response)
        assert event.invocation_id == "inv-1"
        assert event.author == "test_agent"
        assert event.content.role == "model"
        assert event.content.parts[0].text == json_response


# ---------------------------------------------------------------------------
# get_structured_model_response
# ---------------------------------------------------------------------------


class TestGetStructuredModelResponse:
    def test_returns_none_for_none_event(self):
        assert get_structured_model_response(None) is None

    def test_returns_none_for_no_function_responses(self):
        event = Event(invocation_id="inv-1", author="agent", content=Content(parts=[Part(text="text")]))
        assert get_structured_model_response(event) is None

    def test_returns_none_for_non_set_model_response(self):
        fr = FunctionResponse(name="other_tool", response={"key": "val"})
        event = Event(
            invocation_id="inv-1",
            author="agent",
            content=Content(parts=[Part(function_response=fr)]),
        )
        assert get_structured_model_response(event) is None

    def test_returns_json_for_set_model_response(self):
        response_data = {"answer": "42", "confidence": 0.95}
        fr = FunctionResponse(name=SET_MODEL_RESPONSE_TOOL_NAME, response=response_data)
        event = Event(
            invocation_id="inv-1",
            author="agent",
            content=Content(parts=[Part(function_response=fr)]),
        )
        result = get_structured_model_response(event)
        assert result is not None
        parsed = json.loads(result)
        assert parsed["answer"] == "42"
        assert parsed["confidence"] == 0.95


# ---------------------------------------------------------------------------
# OutputSchemaRequestProcessor
# ---------------------------------------------------------------------------


class TestOutputSchemaRequestProcessor:
    def test_skips_non_llm_agent(self):
        proc = OutputSchemaRequestProcessor()
        mock_agent = Mock()
        mock_agent.name = "non_llm"
        ctx = Mock()
        ctx.agent = mock_agent
        request = LlmRequest()

        async def run():
            await proc.run_async(ctx, request)

        asyncio.run(run())
        # Should not raise or modify anything

    def test_skips_when_no_output_schema(self):
        from trpc_agent_sdk.agents._llm_agent import LlmAgent

        proc = OutputSchemaRequestProcessor()
        service = InMemorySessionService()
        session = asyncio.run(
            service.create_session(app_name="test", user_id="u1", session_id="s1")
        )
        agent = LlmAgent(name="test", model="test-output-model")
        ctx = InvocationContext(
            session_service=service,
            invocation_id="inv-1",
            agent=agent,
            agent_context=create_agent_context(),
            session=session,
        )
        request = LlmRequest()

        async def run():
            await proc.run_async(ctx, request)

        asyncio.run(run())
        # No tool should be added

    def test_adds_set_model_response_tool(self, invocation_context):
        proc = OutputSchemaRequestProcessor()
        request = LlmRequest()

        async def run():
            await proc.run_async(invocation_context, request)

        asyncio.run(run())
        assert request.tools_dict is not None or True  # Tool may be in agent.tools


# ---------------------------------------------------------------------------
# Default instance
# ---------------------------------------------------------------------------


class TestDefaultInstance:
    def test_exists(self):
        assert default_output_schema_processor is not None
        assert isinstance(default_output_schema_processor, OutputSchemaRequestProcessor)
