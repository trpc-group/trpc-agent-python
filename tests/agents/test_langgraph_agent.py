# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for LangGraphAgent helper methods and edge cases."""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from google.genai import types
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.tool import ToolCall
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from trpc_agent_sdk.agents._base_agent import BaseAgent
from trpc_agent_sdk.agents._langgraph_agent import LangGraphAgent, _INTERRUPT_KEY, _TRPC_LONG_RUNNING_PREFIX
from trpc_agent_sdk.agents.utils import LANGGRAPH_KEY, STREAM_MODE_KEY, CHUNK_KEY
from trpc_agent_sdk.configs import RunConfig
from trpc_agent_sdk.context import InvocationContext, create_agent_context
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.models import LLMModel, LlmRequest, LlmResponse, ModelRegistry
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content, FunctionCall, FunctionResponse, Part


# ---------------------------------------------------------------------------
# Stubs / helpers
# ---------------------------------------------------------------------------


class MockLLMModel(LLMModel):
    @classmethod
    def supported_models(cls) -> List[str]:
        return [r"test-lg-.*"]

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


def _mock_graph(checkpointer=None):
    graph = MagicMock(spec=CompiledStateGraph)
    graph.checkpointer = checkpointer
    return graph


def _make_agent(graph=None, instruction="", output_key=None, name="lg_agent"):
    g = graph or _mock_graph()
    return LangGraphAgent(name=name, graph=g, instruction=instruction, output_key=output_key)


@pytest.fixture
def invocation_context():
    service = InMemorySessionService()
    session = asyncio.run(
        service.create_session(app_name="test", user_id="u1", session_id="s1")
    )
    graph = _mock_graph()
    agent = _make_agent(graph=graph)
    ctx = InvocationContext(
        session_service=service,
        invocation_id="inv-1",
        agent=agent,
        agent_context=create_agent_context(),
        session=session,
        run_config=RunConfig(),
    )
    return ctx


# ---------------------------------------------------------------------------
# _apply_template_substitution
# ---------------------------------------------------------------------------


class TestApplyTemplateSubstitution:
    def test_no_placeholders(self, invocation_context):
        """Instruction with no placeholders returned unchanged."""
        agent = _make_agent(instruction="Hello world")
        result = agent._apply_template_substitution("Hello world", invocation_context)
        assert result == "Hello world"

    def test_empty_instruction(self, invocation_context):
        """Empty instruction returned unchanged."""
        agent = _make_agent()
        result = agent._apply_template_substitution("", invocation_context)
        assert result == ""

    def test_placeholder_replaced_from_state(self, invocation_context):
        """Placeholder replaced with state value."""
        invocation_context.session.state["user_name"] = "Alice"
        agent = _make_agent()
        result = agent._apply_template_substitution("Hello {user_name}", invocation_context)
        assert result == "Hello Alice"

    def test_optional_placeholder_missing(self, invocation_context):
        """Optional placeholder replaced with empty string when missing."""
        agent = _make_agent()
        result = agent._apply_template_substitution("Hello {missing_key?}", invocation_context)
        assert result == "Hello "

    def test_required_placeholder_missing_unchanged(self, invocation_context):
        """Required placeholder left unchanged when missing."""
        agent = _make_agent()
        result = agent._apply_template_substitution("Hello {no_such_key}", invocation_context)
        assert result == "Hello {no_such_key}"


# ---------------------------------------------------------------------------
# _save_output_to_state
# ---------------------------------------------------------------------------


class TestSaveOutputToStateLG:
    def test_saves_when_output_key_set(self, invocation_context):
        """Output saved to state when output_key is configured."""
        agent = _make_agent(output_key="result")
        event = Event(
            invocation_id="inv-1",
            author="lg_agent",
            content=types.Content(role="model", parts=[types.Part.from_text(text="answer")]),
        )
        agent._save_output_to_state(invocation_context, event)
        assert invocation_context.session.state.get("result") == "answer"

    def test_skips_when_no_output_key(self, invocation_context):
        """Nothing saved when output_key is None."""
        agent = _make_agent(output_key=None)
        event = Event(
            invocation_id="inv-1",
            author="lg_agent",
            content=types.Content(role="model", parts=[types.Part.from_text(text="answer")]),
        )
        agent._save_output_to_state(invocation_context, event)
        assert "result" not in invocation_context.session.state

    def test_skips_when_content_is_none(self, invocation_context):
        """Nothing saved when event.content is None."""
        agent = _make_agent(output_key="result")
        event = Event(invocation_id="inv-1", author="lg_agent")
        agent._save_output_to_state(invocation_context, event)
        assert "result" not in invocation_context.session.state


# ---------------------------------------------------------------------------
# _build_custom_metadata
# ---------------------------------------------------------------------------


class TestBuildCustomMetadata:
    def test_basic_structure(self):
        """Metadata has correct nested structure."""
        agent = _make_agent()
        metadata = agent._build_custom_metadata("updates", {"node": {"messages": []}})
        assert LANGGRAPH_KEY in metadata
        assert metadata[LANGGRAPH_KEY][STREAM_MODE_KEY] == "updates"
        assert metadata[LANGGRAPH_KEY][CHUNK_KEY] == {"node": {"messages": []}}

    def test_updates_mode_serialises_messages(self):
        """Messages in updates mode are serialised to JSON."""
        agent = _make_agent()
        msg = AIMessage(content="hi")
        chunk = {"node": {"messages": [msg]}}
        metadata = agent._build_custom_metadata("updates", chunk)
        serialised = metadata[LANGGRAPH_KEY][CHUNK_KEY]["node"]["messages"]
        assert len(serialised) == 1
        assert isinstance(serialised[0], str)

    def test_custom_mode_passes_through(self):
        """Non-updates modes pass chunk data through unchanged."""
        agent = _make_agent()
        metadata = agent._build_custom_metadata("custom", {"key": "val"})
        assert metadata[LANGGRAPH_KEY][CHUNK_KEY] == {"key": "val"}


# ---------------------------------------------------------------------------
# _check_for_interrupt_in_chunk
# ---------------------------------------------------------------------------


class TestCheckForInterruptInChunk:
    def test_no_interrupt(self):
        """Returns False for a normal chunk."""
        agent = _make_agent()
        assert agent._check_for_interrupt_in_chunk({"node": {}}) is False

    def test_interrupt_present(self):
        """Returns True when __interrupt__ key is present with tuple data."""
        agent = _make_agent()
        chunk = {_INTERRUPT_KEY: (MagicMock(),)}
        assert agent._check_for_interrupt_in_chunk(chunk) is True

    def test_interrupt_empty_tuple(self):
        """Returns False when __interrupt__ key has empty tuple."""
        agent = _make_agent()
        chunk = {_INTERRUPT_KEY: ()}
        assert agent._check_for_interrupt_in_chunk(chunk) is False

    def test_non_dict_chunk(self):
        """Returns False for non-dict chunk."""
        agent = _make_agent()
        assert agent._check_for_interrupt_in_chunk("not a dict") is False


# ---------------------------------------------------------------------------
# _extract_resume_command
# ---------------------------------------------------------------------------


class TestExtractResumeCommand:
    def test_no_checkpointer(self):
        """Returns None when graph has no checkpointer."""
        agent = _make_agent(graph=_mock_graph(checkpointer=None))
        events = [MagicMock()]
        assert agent._extract_resume_command(events) is None

    def test_empty_events(self):
        """Returns None for empty events list."""
        agent = _make_agent(graph=_mock_graph(checkpointer=MagicMock()))
        assert agent._extract_resume_command([]) is None

    def test_extracts_resume_command(self):
        """Extracts Command(resume=...) from matching function response."""
        agent = _make_agent(graph=_mock_graph(checkpointer=MagicMock()))
        fc_rsp = FunctionResponse(
            id=f"{_TRPC_LONG_RUNNING_PREFIX}node:123",
            name="node",
            response={"action": "continue"},
        )
        event = Event(
            invocation_id="inv-1",
            author="user",
            content=types.Content(role="user", parts=[types.Part(function_response=fc_rsp)]),
        )
        result = agent._extract_resume_command([event])
        assert isinstance(result, Command)
        assert result.resume == {"action": "continue"}

    def test_non_matching_event_returns_none(self):
        """Returns None when last event doesn't match pattern."""
        agent = _make_agent(graph=_mock_graph(checkpointer=MagicMock()))
        event = Event(
            invocation_id="inv-1",
            author="user",
            content=types.Content(role="user", parts=[types.Part.from_text(text="hello")]),
        )
        assert agent._extract_resume_command([event]) is None


# ---------------------------------------------------------------------------
# _parse_agent_config
# ---------------------------------------------------------------------------


class TestParseAgentConfig:
    def test_default_stream_modes(self, invocation_context):
        """Default stream modes are updates, custom, messages."""
        agent = _make_agent()
        config = agent._parse_agent_config(invocation_context)
        assert set(config[STREAM_MODE_KEY]) == {"updates", "custom", "messages"}

    def test_custom_stream_modes(self, invocation_context):
        """Custom stream modes override default."""
        invocation_context.run_config.agent_run_config[STREAM_MODE_KEY] = ["values"]
        agent = _make_agent()
        config = agent._parse_agent_config(invocation_context)
        assert config[STREAM_MODE_KEY] == ["values"]

    def test_custom_runnable_config(self, invocation_context):
        """Custom runnable_config is used when provided."""
        custom_rc = {"configurable": {"thread_id": "custom-thread"}}
        invocation_context.run_config.agent_run_config["runnable_config"] = custom_rc
        agent = _make_agent()
        config = agent._parse_agent_config(invocation_context)
        assert config["runnable_config"]["configurable"]["thread_id"] == "custom-thread"

    def test_subgraphs_default_false(self, invocation_context):
        """Subgraphs defaults to False."""
        invocation_context.run_config.agent_run_config.clear()
        agent = _make_agent()
        config = agent._parse_agent_config(invocation_context)
        assert config.get("subgraphs", False) is False


# ---------------------------------------------------------------------------
# _get_last_human_messages
# ---------------------------------------------------------------------------


class TestGetLastHumanMessages:
    def test_extracts_last_user_text(self):
        """Extracts the last user text message from events."""
        agent = _make_agent()
        event = Event(
            invocation_id="inv-1",
            author="user",
            content=types.Content(role="user", parts=[types.Part.from_text(text="question")]),
        )
        msgs = agent._get_last_human_messages([event])
        assert len(msgs) == 1
        assert isinstance(msgs[0], HumanMessage)
        assert msgs[0].content == "question"

    def test_returns_empty_for_no_user_events(self):
        """Returns empty list when no user events."""
        agent = _make_agent()
        event = Event(
            invocation_id="inv-1",
            author="agent",
            content=types.Content(role="model", parts=[types.Part.from_text(text="answer")]),
        )
        msgs = agent._get_last_human_messages([event])
        assert msgs == []

    def test_raises_on_non_text_user_part(self):
        """Raises ValueError for user part without text."""
        agent = _make_agent()
        fc = FunctionCall(id="fc1", name="fn", args={})
        event = Event(
            invocation_id="inv-1",
            author="user",
            content=types.Content(role="user", parts=[types.Part(function_call=fc)]),
        )
        with pytest.raises(ValueError, match="Invalid message part"):
            agent._get_last_human_messages([event])


# ---------------------------------------------------------------------------
# _build_event_from_message
# ---------------------------------------------------------------------------


class TestBuildEventFromMessage:
    def test_ai_message_text(self, invocation_context):
        """AIMessage with text builds correct event."""
        agent = _make_agent()
        msg = AIMessage(content="response text")
        event = agent._build_event_from_message(invocation_context, msg, "updates", {})
        assert event is not None
        assert event.content.parts[0].text == "response text"
        assert event.partial is False

    def test_ai_message_tool_calls(self, invocation_context):
        """AIMessage with tool_calls builds function call event."""
        agent = _make_agent()
        msg = AIMessage(content="", tool_calls=[{"id": "tc1", "name": "fn", "args": {"x": 1}}])
        event = agent._build_event_from_message(invocation_context, msg, "updates", {})
        assert event is not None
        assert event.content.parts[0].function_call.name == "fn"

    def test_tool_message(self, invocation_context):
        """ToolMessage builds function response event."""
        agent = _make_agent()
        msg = ToolMessage(content='{"result": "ok"}', name="fn", tool_call_id="tc1")
        event = agent._build_event_from_message(invocation_context, msg, "updates", {})
        assert event is not None
        assert event.content.parts[0].function_response.name == "fn"

    def test_tool_message_non_json_content(self, invocation_context):
        """ToolMessage with non-JSON content falls back to result wrapper."""
        agent = _make_agent()
        msg = ToolMessage(content="plain text", name="fn", tool_call_id="tc1")
        event = agent._build_event_from_message(invocation_context, msg, "updates", {})
        assert event is not None
        assert event.content.parts[0].function_response.response["result"] == "plain text"

    def test_unsupported_message_returns_none(self, invocation_context):
        """Unsupported message type returns None."""
        agent = _make_agent()
        msg = SystemMessage(content="sys")
        result = agent._build_event_from_message(invocation_context, msg, "updates", {})
        assert result is None

    def test_ai_message_with_usage_metadata(self, invocation_context):
        """AIMessage with usage_metadata includes it in event."""
        agent = _make_agent()
        msg = AIMessage(content="hi")
        msg.usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        event = agent._build_event_from_message(invocation_context, msg, "updates", {})
        assert event is not None
        assert event.usage_metadata is not None


# ---------------------------------------------------------------------------
# _convert_override_messages_to_langchain
# ---------------------------------------------------------------------------


class TestConvertOverrideMessagesToLangchain:
    def test_text_user_message(self):
        """User text Content converts to HumanMessage."""
        agent = _make_agent()
        content = Content(role="user", parts=[Part.from_text(text="hi")])
        result = agent._convert_override_messages_to_langchain([content])
        assert len(result) == 1
        assert isinstance(result[0], HumanMessage)

    def test_text_model_message(self):
        """Model text Content converts to AIMessage."""
        agent = _make_agent()
        content = Content(role="model", parts=[Part.from_text(text="response")])
        result = agent._convert_override_messages_to_langchain([content])
        assert len(result) == 1
        assert isinstance(result[0], AIMessage)

    def test_function_call_message(self):
        """Function call Content converts to AIMessage with tool_calls."""
        agent = _make_agent()
        fc = FunctionCall(id="fc1", name="fn", args={"a": 1})
        content = Content(role="model", parts=[Part(function_call=fc)])
        result = agent._convert_override_messages_to_langchain([content])
        assert len(result) == 1
        assert isinstance(result[0], AIMessage)
        assert len(result[0].tool_calls) == 1

    def test_function_response_message(self):
        """Function response Content converts to ToolMessage."""
        agent = _make_agent()
        fr = FunctionResponse(id="fc1", name="fn", response={"result": "ok"})
        content = Content(role="user", parts=[Part(function_response=fr)])
        result = agent._convert_override_messages_to_langchain([content])
        assert len(result) == 1
        assert isinstance(result[0], ToolMessage)

    def test_skips_non_content(self):
        """Non-Content items are skipped."""
        agent = _make_agent()
        result = agent._convert_override_messages_to_langchain(["not a content", None])
        assert result == []


# ---------------------------------------------------------------------------
# _convert_parts_to_messages
# ---------------------------------------------------------------------------


class TestConvertPartsToMessages:
    def test_function_call_part(self):
        """Function call part converts to AIMessage with tool call."""
        agent = _make_agent()
        fc = FunctionCall(id="fc1", name="fn", args={"x": 1})
        event = Event(
            invocation_id="inv-1",
            author="lg_agent",
            content=types.Content(role="model", parts=[types.Part(function_call=fc)]),
        )
        msgs = agent._convert_parts_to_messages(event)
        assert len(msgs) == 1
        assert isinstance(msgs[0], AIMessage)

    def test_function_response_part(self):
        """Function response part converts to ToolMessage."""
        agent = _make_agent()
        fr = FunctionResponse(id="fc1", name="fn", response={"result": "ok"})
        event = Event(
            invocation_id="inv-1",
            author="lg_agent",
            content=types.Content(role="model", parts=[types.Part(function_response=fr)]),
        )
        msgs = agent._convert_parts_to_messages(event)
        assert len(msgs) == 1
        assert isinstance(msgs[0], ToolMessage)

    def test_text_part(self):
        """Text part converts to AIMessage."""
        agent = _make_agent()
        event = Event(
            invocation_id="inv-1",
            author="lg_agent",
            content=types.Content(role="model", parts=[types.Part.from_text(text="hi")]),
        )
        msgs = agent._convert_parts_to_messages(event)
        assert len(msgs) == 1
        assert isinstance(msgs[0], AIMessage)
        assert msgs[0].content == "hi"

    def test_function_response_no_id_generates_fallback(self):
        """Function response with no id generates fallback tool_call_id."""
        agent = _make_agent()
        fr = FunctionResponse(name="fn", response={"result": "ok"})
        event = Event(
            invocation_id="inv-1",
            author="lg_agent",
            content=types.Content(role="model", parts=[types.Part(function_response=fr)]),
        )
        msgs = agent._convert_parts_to_messages(event)
        assert len(msgs) == 1
        assert msgs[0].tool_call_id.startswith("unknown_")


# ---------------------------------------------------------------------------
# _create_interrupt_events
# ---------------------------------------------------------------------------


class TestCreateInterruptEvents:
    def test_creates_three_events(self, invocation_context):
        """Creates function_call, function_response, and long_running events."""
        agent = _make_agent()
        interrupt = MagicMock()
        interrupt.ns = ["my_node:abc"]
        interrupt.value = {"prompt": "confirm?"}
        fc_event, fr_event, lr_event = agent._create_interrupt_events(
            invocation_context, interrupt, "updates", {}
        )
        assert fc_event.content.parts[0].function_call.name == "my_node"
        assert fr_event.content.parts[0].function_response.name == "my_node"
        assert lr_event.function_call.id.startswith(_TRPC_LONG_RUNNING_PREFIX)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
