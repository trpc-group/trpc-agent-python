# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for RequestProcessor helper methods."""

from __future__ import annotations

import asyncio
import copy
from typing import List
from unittest.mock import AsyncMock, Mock, patch

import pytest

from trpc_agent_sdk.agents._llm_agent import LlmAgent
from trpc_agent_sdk.agents.core._request_processor import (
    RequestProcessor,
    default_request_processor,
)
from trpc_agent_sdk.context import InvocationContext, create_agent_context
from trpc_agent_sdk.events import Event, EventActions
from trpc_agent_sdk.models import LLMModel, LlmRequest, LlmResponse, ModelRegistry
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content, FunctionCall, FunctionResponse, Part


class MockLLMModel(LLMModel):
    @classmethod
    def supported_models(cls) -> List[str]:
        return [r"test-reqproc-.*"]

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


@pytest.fixture
def processor():
    return RequestProcessor()


@pytest.fixture
def invocation_context():
    service = InMemorySessionService()
    session = asyncio.run(
        service.create_session(app_name="test", user_id="u1", session_id="s1")
    )
    agent = LlmAgent(name="test_agent", model="test-reqproc-model", instruction="Be helpful")
    ctx = InvocationContext(
        session_service=service,
        invocation_id="inv-1",
        agent=agent,
        agent_context=create_agent_context(),
        session=session,
        branch="test_branch",
    )
    return ctx


# ---------------------------------------------------------------------------
# _create_error_event
# ---------------------------------------------------------------------------


class TestCreateErrorEvent:
    def test_creates_proper_error_event(self, processor, invocation_context):
        event = processor._create_error_event(invocation_context, "test_err", "Something failed")
        assert event.error_code == "test_err"
        assert event.error_message == "Something failed"
        assert event.author == "test_agent"
        assert event.invocation_id == "inv-1"


# ---------------------------------------------------------------------------
# _apply_template_substitution
# ---------------------------------------------------------------------------


class TestApplyTemplateSubstitution:
    def test_no_placeholders(self, processor, invocation_context):
        result = processor._apply_template_substitution("Hello world", invocation_context)
        assert result == "Hello world"

    def test_empty_string(self, processor, invocation_context):
        result = processor._apply_template_substitution("", invocation_context)
        assert result == ""

    def test_replaces_state_value(self, processor, invocation_context):
        invocation_context.session.state["user_name"] = "Alice"
        result = processor._apply_template_substitution("Hello {user_name}", invocation_context)
        assert result == "Hello Alice"

    def test_missing_required_placeholder_unchanged(self, processor, invocation_context):
        result = processor._apply_template_substitution("Hello {missing}", invocation_context)
        assert result == "Hello {missing}"

    def test_optional_placeholder_missing_becomes_empty(self, processor, invocation_context):
        result = processor._apply_template_substitution("Hello {optional?}", invocation_context)
        assert result == "Hello "

    def test_optional_placeholder_present(self, processor, invocation_context):
        invocation_context.session.state["greeting"] = "Hi"
        result = processor._apply_template_substitution("{greeting?} there", invocation_context)
        assert result == "Hi there"


# ---------------------------------------------------------------------------
# _get_effective_content_role
# ---------------------------------------------------------------------------


class TestGetEffectiveContentRole:
    def test_none_content(self, processor):
        event = Event(invocation_id="inv-1", author="agent", content=None)
        assert processor._get_effective_content_role(event) is None

    def test_explicit_role_used(self, processor):
        event = Event(
            invocation_id="inv-1",
            author="agent",
            content=Content(role="model", parts=[Part(text="hi")]),
        )
        assert processor._get_effective_content_role(event) == "model"

    def test_user_author_inferred(self, processor):
        event = Event(
            invocation_id="inv-1",
            author="user",
            content=Content(parts=[Part(text="hi")]),
        )
        assert processor._get_effective_content_role(event) == "user"

    def test_agent_text_becomes_model(self, processor):
        event = Event(
            invocation_id="inv-1",
            author="agent",
            content=Content(parts=[Part(text="hi")]),
        )
        assert processor._get_effective_content_role(event) == "model"

    def test_function_response_becomes_user(self, processor):
        fr = FunctionResponse(name="tool", response={"result": "ok"})
        event = Event(
            invocation_id="inv-1",
            author="agent",
            content=Content(parts=[Part(function_response=fr)]),
        )
        assert processor._get_effective_content_role(event) == "user"


# ---------------------------------------------------------------------------
# _is_other_agent_reply
# ---------------------------------------------------------------------------


class TestIsOtherAgentReply:
    def test_user_message_not_other(self, processor):
        event = Event(invocation_id="inv-1", author="user", branch="b1")
        assert processor._is_other_agent_reply("b1", event) is False

    def test_same_branch_not_other(self, processor):
        event = Event(invocation_id="inv-1", author="agent", branch="b1")
        assert processor._is_other_agent_reply("b1", event) is False

    def test_different_branch_is_other(self, processor):
        event = Event(invocation_id="inv-1", author="agent", branch="b2")
        assert processor._is_other_agent_reply("b1", event) is True

    def test_no_branch_info_is_other(self, processor):
        event = Event(invocation_id="inv-1", author="agent", branch=None)
        assert processor._is_other_agent_reply(None, event) is True


# ---------------------------------------------------------------------------
# _convert_foreign_event
# ---------------------------------------------------------------------------


class TestConvertForeignEvent:
    def test_text_event_converted(self, processor, invocation_context):
        event = Event(
            invocation_id="inv-1",
            author="other_agent",
            branch="other_branch",
            content=Content(parts=[Part(text="other response")]),
        )
        converted = processor._convert_foreign_event(event, invocation_context.agent)
        assert converted.author == "user"
        assert converted.content.role == "user"
        assert "[other_agent]" in converted.content.parts[0].text

    def test_no_content_returns_as_is(self, processor, invocation_context):
        event = Event(invocation_id="inv-1", author="other", content=None)
        result = processor._convert_foreign_event(event, invocation_context.agent)
        assert result is event

    def test_function_call_converted(self, processor, invocation_context):
        fc = FunctionCall(name="my_tool", args={"k": "v"})
        event = Event(
            invocation_id="inv-1",
            author="other_agent",
            content=Content(parts=[Part(function_call=fc)]),
        )
        converted = processor._convert_foreign_event(event, invocation_context.agent)
        assert "my_tool" in converted.content.parts[0].text


# ---------------------------------------------------------------------------
# _merge_consecutive_same_role_contents
# ---------------------------------------------------------------------------


class TestMergeConsecutiveSameRoleContents:
    def test_empty_list(self, processor):
        assert processor._merge_consecutive_same_role_contents([]) == []

    def test_different_roles_not_merged(self, processor):
        e1 = Event(
            invocation_id="inv-1",
            author="user",
            content=Content(role="user", parts=[Part(text="hi")]),
        )
        e2 = Event(
            invocation_id="inv-1",
            author="agent",
            content=Content(role="model", parts=[Part(text="hello")]),
        )
        result = processor._merge_consecutive_same_role_contents([e1, e2])
        assert len(result) == 2

    def test_same_role_merged(self, processor):
        e1 = Event(
            invocation_id="inv-1",
            author="user",
            content=Content(role="user", parts=[Part(text="hi")]),
        )
        e2 = Event(
            invocation_id="inv-1",
            author="user",
            content=Content(role="user", parts=[Part(text="there")]),
        )
        result = processor._merge_consecutive_same_role_contents([e1, e2])
        assert len(result) == 1
        assert len(result[0].content.parts) == 2


# ---------------------------------------------------------------------------
# _get_function_calls / _get_function_responses
# ---------------------------------------------------------------------------


class TestGetFunctionCallsAndResponses:
    def test_get_function_calls_extracts(self, processor):
        fc = FunctionCall(name="my_tool", args={})
        event = Event(
            invocation_id="inv-1",
            author="agent",
            content=Content(parts=[Part(function_call=fc)]),
        )
        calls = processor._get_function_calls(event)
        assert len(calls) == 1
        assert calls[0].name == "my_tool"

    def test_transfer_function_calls_excluded(self, processor):
        fc = FunctionCall(name="transfer_to_agent", args={"agent_name": "x"})
        event = Event(
            invocation_id="inv-1",
            author="agent",
            content=Content(parts=[Part(function_call=fc)]),
        )
        calls = processor._get_function_calls(event)
        assert len(calls) == 0

    def test_get_function_responses_extracts(self, processor):
        fr = FunctionResponse(name="my_tool", response={"r": 1})
        event = Event(
            invocation_id="inv-1",
            author="agent",
            content=Content(parts=[Part(function_response=fr)]),
        )
        responses = processor._get_function_responses(event)
        assert len(responses) == 1

    def test_transfer_function_responses_excluded(self, processor):
        fr = FunctionResponse(name="transfer_to_agent", response={})
        event = Event(
            invocation_id="inv-1",
            author="agent",
            content=Content(parts=[Part(function_response=fr)]),
        )
        responses = processor._get_function_responses(event)
        assert len(responses) == 0


# ---------------------------------------------------------------------------
# Default instance
# ---------------------------------------------------------------------------


class TestDefaultInstance:
    def test_exists(self):
        assert default_request_processor is not None
        assert isinstance(default_request_processor, RequestProcessor)
